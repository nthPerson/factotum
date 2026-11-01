#!/usr/bin/env python3
"""
Plaud -> Notion/Calendar sync watcher.

Listens (IMAP IDLE) on a Gmail inbox for Plaud AutoFlow "summary ready" emails.
On each new one, it invokes Claude Code headlessly (`claude -p`) with a task
prompt so Claude fetches the recording via the Plaud MCP and files it into
Notion + creates Google Calendar events.

Config comes from environment variables (see the .env / systemd EnvironmentFile).

Requires:  pip install imapclient
Auth model: the machine must already be logged in with `claude login` (your Max
account) and have the Plaud, Notion, and Google Calendar MCPs added at USER scope.
"""
import os
import re
import sys
import json
import time
import email
import logging
import smtplib
import subprocess
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

from imapclient import IMAPClient

# ---------- config (from environment) ----------
IMAP_HOST     = os.environ.get("IMAP_HOST", "imap.gmail.com")
IMAP_PORT     = int(os.environ.get("IMAP_PORT", "993"))
IMAP_USER     = os.environ["IMAP_USER"]
IMAP_PASSWORD = os.environ["IMAP_PASSWORD"]          # Gmail APP password (not your normal password)
IMAP_FOLDER   = os.environ.get("IMAP_FOLDER", "INBOX")
PLAUD_SENDER  = os.environ.get("PLAUD_SENDER", "")   # substring of the From address, e.g. "plaud.ai"
SUBJECT_MATCH = os.environ.get("SUBJECT_MATCH", "")  # optional extra substring filter on the subject
CLAUDE_BIN    = os.environ.get("CLAUDE_BIN", "claude")
MODEL         = os.environ.get("MODEL", "sonnet")
PROMPT_FILE   = os.environ["PROMPT_FILE"]
WORKDIR       = os.environ.get("WORKDIR", os.getcwd())
LOG_FILE      = os.environ.get("LOG_FILE", "plaud-sync.log")
STATE_FILE    = os.environ.get("STATE_FILE", "processed.json")
RUNS_LOG      = os.environ.get("RUNS_LOG", "runs.jsonl")
CLAUDE_TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT", "600"))
# Server names here must match how you ran `claude mcp add <name> ...`.
ALLOWED_TOOLS = os.environ.get(
    "ALLOWED_TOOLS", "mcp__plaud__*,mcp__notion__*,mcp__google-calendar__*"
)
RETRY_DELAY  = int(os.environ.get("RETRY_DELAY", "120"))  # seconds before the single failure retry
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", "")         # failure alerts go here; empty disables
SMTP_HOST    = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT    = int(os.environ.get("SMTP_PORT", "465"))
IDLE_TIMEOUT = 29 * 60  # re-issue IDLE before the ~30-min server cutoff
# Planner command channel: mail FROM these senders (planner-sources.json "commands.senders" plus
# NOTIFY_EMAIL) whose subject/body starts with a verb — done/defer/drop/add … — is applied to the
# Tasks DB immediately by a small headless Claude run, then answered with a confirmation reply.
SOURCES_FILE         = os.environ.get("PLANNER_SOURCES_FILE", "planner-sources.json")
COMMANDS_PROMPT_FILE = os.environ.get("PLANNER_COMMANDS_PROMPT_FILE", "planner-commands-prompt.txt")
PLANNER_RUNS_LOG     = os.environ.get("PLANNER_RUNS_LOG", "planner-runs.jsonl")
COMMAND_TIMEOUT      = int(os.environ.get("COMMAND_TIMEOUT", "300"))
COMMAND_SENDERS      = set()   # filled in main()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("plaud-sync")

PLAUD_URL_RE = re.compile(r"https?://[^\s\"'>]*plaud[^\s\"'>]*", re.IGNORECASE)


def load_state():
    try:
        with open(STATE_FILE) as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_state(processed):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(sorted(processed), f)
    os.replace(tmp, STATE_FILE)


def decode_str(raw):
    if raw is None:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return str(raw)


def get_body_text(msg):
    parts = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if ctype in ("text/plain", "text/html") and "attachment" not in disp:
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        parts.append(payload.decode(part.get_content_charset() or "utf-8", "replace"))
                except Exception:
                    pass
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                parts.append(payload.decode(msg.get_content_charset() or "utf-8", "replace"))
        except Exception:
            pass
    return "\n".join(parts)


def build_prompt(base_prompt, subject, plaud_link, received):
    ctx = (
        "\n\n---\n## The recording to process (from the triggering email)\n"
        f"- Email subject: {subject}\n"
        f"- Plaud link found in email: {plaud_link or 'none found — locate by title + recency'}\n"
        f"- Email received (UTC): {received}\n"
        "Use the Plaud MCP to locate THIS recording (by the link's ID if present, otherwise the "
        "most recent recording whose title matches the subject), then follow the steps above.\n"
    )
    return base_prompt + ctx


def run_claude(prompt, timeout=None):
    timeout = timeout or CLAUDE_TIMEOUT
    cmd = [
        CLAUDE_BIN, "-p", prompt,
        "--model", MODEL,
        "--allowedTools", ALLOWED_TOOLS,
        "--permission-mode", "acceptEdits",
        "--output-format", "json",
    ]
    log.info("Invoking Claude (model=%s) ...", MODEL)
    try:
        res = subprocess.run(
            cmd, cwd=WORKDIR, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        log.error("Claude run timed out after %ss", timeout)
        return {"ok": False, "stdout": "", "stderr": f"timeout after {timeout}s"}
    if res.returncode != 0:
        log.error("Claude exited %s. stderr tail:\n%s", res.returncode, (res.stderr or "")[-2000:])
    return {"ok": res.returncode == 0, "stdout": res.stdout or "", "stderr": res.stderr or ""}


def extract_report(text):
    """Pull the JSON report object out of the model's final text.

    The prompt demands raw JSON, but tolerate markdown fences or stray prose
    around it so runs.jsonl stays machine-parseable either way.
    """
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        candidate = m.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        candidate = text[start:end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def _page_written(report, summary):
    """Did the run actually file the note into Notion?

    `claude -p` can exit 0 with is_error=False yet accomplish nothing — e.g. a
    write tool blocked pending permission, after which the model gives up with a
    prose apology (the 2026-07-22 incident: a settings.json `ask` gate stalled
    `notion-create-pages`). So exit status alone is not proof of success. Require
    positive evidence a page was created/updated: a notion_page URL in the parsed
    JSON report, or a Notion page link in the raw final text. Idempotency (Step 2
    of the prompt keys create-or-update on Source Link) makes a false negative
    here safe — the retry updates the existing page rather than duplicating it.
    """
    if isinstance(report, dict):
        page = report.get("notion_page")
        if isinstance(page, dict) and page.get("url"):
            return True
    text = summary or ""
    return "notion.so/" in text or "notion.com/" in text


def notify_failure(uid, subject, run):
    """Email a failure alert via the same Gmail account the watcher reads (best-effort)."""
    if not NOTIFY_EMAIL:
        return
    msg = EmailMessage()
    msg["From"] = IMAP_USER
    msg["To"] = NOTIFY_EMAIL
    msg["Subject"] = f"[plaud-sync] sync FAILED: {subject[:120]}"
    try:
        final_text = json.loads(run["stdout"]).get("result") or ""
    except (json.JSONDecodeError, TypeError):
        final_text = run["stdout"] or ""
    msg.set_content(
        f"Both attempts failed for uid={uid}.\n"
        f"Subject: {subject}\n\n"
        f"Claude's final message (tail):\n{(final_text or '(none)')[-1500:]}\n\n"
        f"stderr tail:\n{(run['stderr'] or '(empty)')[-500:]}\n\n"
        f"Details are in {RUNS_LOG}. To replay: mark the email unread, remove the uid from "
        f"{STATE_FILE}, and restart the service — or use /replay-note in Claude Code."
    )
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.login(IMAP_USER, IMAP_PASSWORD)
            smtp.send_message(msg)
        log.info("Failure alert emailed to %s", NOTIFY_EMAIL)
    except Exception as e:
        log.warning("Could not email failure alert: %s", e)


def log_run(uid, subject, plaud_link, run, attempt=1):
    """Append one structured record per run to RUNS_LOG (JSON Lines). Returns overall ok."""
    summary, cost, duration_ms, is_error = None, None, None, None
    try:
        data = json.loads(run["stdout"])       # `claude -p --output-format json` result envelope
        summary = data.get("result")           # the model's final text = the JSON report we asked for
        cost = data.get("total_cost_usd")
        duration_ms = data.get("duration_ms")
        is_error = data.get("is_error")
    except (json.JSONDecodeError, TypeError):
        summary = (run["stdout"] or "")[-2000:] or None    # fallback: raw stdout tail
    report = extract_report(summary)
    # A clean exit is necessary but NOT sufficient: a permission-blocked or
    # abandoned run also exits 0 with is_error=False. Require proof of a write.
    ok = run["ok"] and not is_error and _page_written(report, summary)
    record = {
        "time": datetime.now(timezone.utc).isoformat(),
        "uid": uid,
        "subject": subject,
        "plaud_link": plaud_link,
        "ok": ok,
        "attempt": attempt,
        "cost_usd": cost,
        "duration_ms": duration_ms,
        "report": report,                       # parsed JSON report (None if unparseable)
        "summary": None if report else summary, # raw text kept only when parsing failed
        "error": None if ok else ((run["stderr"] or "")[-1000:] or "see summary"),
    }
    with open(RUNS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return ok


def process_message(server, uid, base_prompt, processed):
    raw = server.fetch([uid], ["RFC822"])[uid][b"RFC822"]
    msg = email.message_from_bytes(raw)
    subject = decode_str(msg.get("Subject"))
    received = datetime.now(timezone.utc).isoformat()
    body = get_body_text(msg)
    m = PLAUD_URL_RE.search(body)
    plaud_link = m.group(0) if m else None
    log.info("New Plaud email uid=%s subject=%r link=%s", uid, subject, plaud_link)

    prompt = build_prompt(base_prompt, subject, plaud_link, received)
    run = run_claude(prompt)
    ok = log_run(uid, subject, plaud_link, run)     # append a structured record to runs.jsonl
    if not ok:
        # One delayed retry: transient API/MCP failures are common enough to be worth a second
        # attempt, and the prompt's create-or-update idempotency makes the retry safe.
        log.warning("Run failed for uid=%s — retrying once in %ss", uid, RETRY_DELAY)
        time.sleep(RETRY_DELAY)
        run = run_claude(prompt)
        ok = log_run(uid, subject, plaud_link, run, attempt=2)
    log.info("Run recorded (ok=%s) -> %s", ok, RUNS_LOG)

    # Mark seen + record UID regardless, so we never reprocess in a loop.
    try:
        server.add_flags([uid], [b"\\Seen"])
    except Exception as e:
        log.warning("Could not mark uid=%s seen: %s", uid, e)
    processed.add(uid)
    save_state(processed)
    if not ok:
        log.error("uid=%s did NOT sync cleanly — see %s and re-run manually if needed.", uid, RUNS_LOG)
        notify_failure(uid, subject, run)


# ---------- planner command channel ----------
COMMAND_VERB_RE = re.compile(
    r"^\s*(?:(?:re|fwd?):\s*)*(?:\[planner\]\s*)?(?:(?:please|pls|hey|ok|okay)[,\s]+)?"
    r"(done|finish(?:ed)?|complete[d]?|mark|defer|push|postpone|snooze|drop|remove|delete|cancel|"
    r"start|reopen|waiting|priority|note|add|schedule|book|set\s+up|create|new|move|reschedule|"
    r"put|task[- ]?\d+)\b", re.I)
FORWARD_RE = re.compile(r"^\s*fwd?:", re.I)
SHORT_COMMAND_MAX = 500   # a short non-forwarded mail from Robert to this inbox is a command
QUOTE_START_RE = re.compile(
    r"^(On .{5,200}wrote:\s*$|-----\s*Original Message\s*-----|_{10,}\s*$|From:\s.+\nSent:\s.+)",
    re.I | re.M)


def load_command_senders():
    try:
        with open(SOURCES_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        cfg = {}
    senders = {s.lower() for s in cfg.get("commands", {}).get("senders", []) if s}
    if NOTIFY_EMAIL:
        senders.add(NOTIFY_EMAIL.lower())
    return senders


def get_plain_text(msg):
    """Prefer the text/plain part; fall back to a crude de-HTML of the text/html part."""
    plain, htmls = [], []
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        if "attachment" in str(part.get("Content-Disposition") or ""):
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            payload = None
        if not payload:
            continue
        text = payload.decode(part.get_content_charset() or "utf-8", "replace")
        ctype = part.get_content_type()
        if ctype == "text/plain":
            plain.append(text)
        elif ctype == "text/html":
            htmls.append(text)
    if plain:
        return "\n".join(plain)
    text = "\n".join(htmls)
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>|</div>|</tr>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    import html as _html
    return _html.unescape(text)


def strip_quoted(text):
    """Keep only what Robert typed: cut at the first quote header, drop '>' lines and the signature."""
    m = QUOTE_START_RE.search(text or "")
    if m:
        text = text[:m.start()]
    kept = []
    for line in text.splitlines():
        if line.strip() == "--":
            break
        if line.lstrip().startswith(">"):
            continue
        kept.append(line.rstrip())
    return "\n".join(kept).strip()


def command_text(subject, body):
    """Return (text for Claude, is_command). Commands arrive three ways: a reply to a planner
    brief (subject contains [planner]), a mailto link (verb in the subject, empty body), or a
    hand-written mail whose first line starts with a verb."""
    subj = re.sub(r"^\s*(?:(?:re|fwd?):\s*)+", "", subject or "", flags=re.I).strip()
    is_reply_to_brief = "[planner]" in subj.lower()
    subj_cmd = re.sub(r"^\[planner\]\s*", "", subj, flags=re.I).strip()
    body = strip_quoted(body)
    parts = []
    if COMMAND_VERB_RE.match(subj_cmd) and not is_reply_to_brief:
        parts.append(subj_cmd)
    if body:
        parts.append(body)
    text = "\n".join(parts).strip()
    if not text:
        return text, False
    if is_reply_to_brief or COMMAND_VERB_RE.match(subj_cmd) or COMMAND_VERB_RE.match(body):
        return text, True
    # Fallback: Robert rarely mails this inbox for any other reason, so a short, non-forwarded
    # message is treated as a free-text command and Claude decides (it reports "no command
    # found" when there is none).
    short = len(text) <= SHORT_COMMAND_MAX and not FORWARD_RE.match(subject or "")
    return text, short


def send_reply(to_addr, subject, body, in_reply_to=None):
    msg = EmailMessage()
    msg["From"] = IMAP_USER
    msg["To"] = to_addr
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.login(IMAP_USER, IMAP_PASSWORD)
            smtp.send_message(msg)
    except Exception as e:
        log.warning("Could not send command reply to %s: %s", to_addr, e)


def process_command(server, uid, processed):
    raw = server.fetch([uid], ["RFC822"])[uid][b"RFC822"]
    msg = email.message_from_bytes(raw)
    subject = decode_str(msg.get("Subject"))
    sender = parseaddr(msg.get("From", ""))[1]
    mid = (msg.get("Message-ID") or "").strip()
    plain = get_plain_text(msg)
    text, is_cmd = command_text(subject, plain)
    if "[planner]" in (subject or "").lower():
        channel = "Email reply"
    elif not strip_quoted(plain):
        channel = "Email subject"       # e.g. a mailto link from the brief
    else:
        channel = "Email free text"
    if not is_cmd:
        log.info("Mail from command sender uid=%s is not a command (subject=%r) — ignored", uid, subject)
        processed.add(uid)
        save_state(processed)
        return
    log.info("Planner command uid=%s from=%s: %r", uid, sender, text[:300])
    with open(COMMANDS_PROMPT_FILE, encoding="utf-8") as f:
        prompt = f.read()
    try:
        when = parsedate_to_datetime(msg.get("Date")).astimezone().strftime("%Y-%m-%d %H:%M %Z (%A)")
    except Exception:
        when = msg.get("Date") or "unknown"
    today = datetime.now().astimezone().strftime("%Y-%m-%d (%A)")
    full = (prompt + f"\n\n---\n## Command email\n- From: {sender}\n- Email date: {when}\n"
            f"- Today: {today}\n- Message-ID: {mid or 'none'}\n- Channel: {channel}\n\n"
            f"### Command text\n{text}\n")
    run = run_claude(full, timeout=COMMAND_TIMEOUT)

    envelope, report, cost, duration_ms = None, None, None, None
    try:
        envelope = json.loads(run["stdout"])
        report = extract_report(envelope.get("result"))
        cost = envelope.get("total_cost_usd")
        duration_ms = envelope.get("duration_ms")
    except (json.JSONDecodeError, TypeError):
        pass
    ok = bool(run["ok"] and not (envelope or {}).get("is_error")
              and isinstance(report, dict) and "applied" in report)
    with open(PLANNER_RUNS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "time": datetime.now(timezone.utc).isoformat(), "kind": "command", "uid": uid,
            "from": sender, "subject": subject, "channel": channel, "command": text[:1000], "ok": ok,
            "cost_usd": cost, "duration_ms": duration_ms, "report": report,
            "error": None if ok else ((run["stderr"] or "")[-800:] or "no report"),
        }, ensure_ascii=False) + "\n")

    reply_subject = f"Re: {subject}" if subject else "[planner] command result"
    if ok:
        lines = []
        for a in report.get("applied") or []:
            lines.append(f"✓ {a.get('task_id')} {a.get('action')}: {a.get('name')}"
                         + (f" — {a.get('detail')}" if a.get("detail") else ""))
        for c in report.get("created") or []:
            lines.append(f"+ {c.get('task_id')} created: {c.get('name')}")
        for ev in report.get("calendar") or []:
            lines.append(f"📅 {ev.get('action')}: {ev.get('title')} — {ev.get('when')}"
                         + (f"\n   {ev.get('link')}" if ev.get("link") else ""))
        for u in report.get("unresolved") or []:
            lines.append(f"? {u}")
        body = ("\n".join(lines) or "Nothing to apply.") + (f"\n\n(planner · ${cost:.2f})" if cost else "")
        log.info("Command applied (ok=True): %d applied, %d created, %d calendar, %d unresolved",
                 len(report.get("applied") or []), len(report.get("created") or []),
                 len(report.get("calendar") or []), len(report.get("unresolved") or []))
    else:
        tail = (envelope or {}).get("result") if isinstance(envelope, dict) else run["stdout"]
        body = (f"Your command could not be applied.\n\nCommand:\n{text}\n\nClaude's final text (tail):\n"
                f"{(tail or '(none)')[-1200:]}\n\nstderr tail:\n{(run['stderr'] or '(empty)')[-500:]}")
        log.error("Command uid=%s FAILED — see %s", uid, PLANNER_RUNS_LOG)
    send_reply(sender, reply_subject, body, in_reply_to=mid or None)

    try:
        server.add_flags([uid], [b"\\Seen"])
    except Exception as e:
        log.warning("Could not mark uid=%s seen: %s", uid, e)
    processed.add(uid)
    save_state(processed)


def search_and_process(server, base_prompt, processed):
    criteria = ["UNSEEN"]
    if PLAUD_SENDER:
        criteria += ["FROM", PLAUD_SENDER]
    for uid in server.search(criteria):
        if uid in processed:
            continue
        if SUBJECT_MATCH:
            hdr = server.fetch([uid], ["BODY.PEEK[HEADER.FIELDS (SUBJECT)]"])
            subj = decode_str(email.message_from_bytes(
                hdr[uid][b"BODY[HEADER.FIELDS (SUBJECT)]"]).get("Subject"))
            if SUBJECT_MATCH.lower() not in subj.lower():
                continue
        process_message(server, uid, base_prompt, processed)
    # Planner commands: unseen mail from Robert's own addresses (see COMMAND_SENDERS).
    for sender in sorted(COMMAND_SENDERS):
        for uid in server.search(["UNSEEN", "FROM", sender]):
            if uid not in processed:
                process_command(server, uid, processed)


def main():
    with open(PROMPT_FILE) as f:
        base_prompt = f.read()
    processed = load_state()
    COMMAND_SENDERS.update(load_command_senders())
    log.info("Starting Plaud sync watcher for %s (folder=%s); command senders: %s",
             IMAP_USER, IMAP_FOLDER, ", ".join(sorted(COMMAND_SENDERS)) or "none")

    while True:
        try:
            with IMAPClient(IMAP_HOST, port=IMAP_PORT, use_uid=True, ssl=True) as server:
                server.login(IMAP_USER, IMAP_PASSWORD)
                server.select_folder(IMAP_FOLDER)
                log.info("Connected. Clearing any backlog, then idling.")
                search_and_process(server, base_prompt, processed)  # handle anything already waiting
                while True:
                    server.idle()
                    responses = server.idle_check(timeout=IDLE_TIMEOUT)
                    server.idle_done()
                    if responses:
                        search_and_process(server, base_prompt, processed)
        except Exception as e:
            log.exception("Connection/loop error (%s) — reconnecting in 15s", e)
            time.sleep(15)


if __name__ == "__main__":
    main()
