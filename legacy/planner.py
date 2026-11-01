#!/usr/bin/env python3
"""
Planner — morning / evening / on-demand task planning run.

Python gathers, Claude decides and writes (see PLANNER-SPEC.md):
  1. Canvas API   -> assignments/quizzes/discussions with due dates AND submission state
  2. Forwarded    -> SDSU/Canvas mail in the watcher inbox, filtered by planner-sources.json
     email
  3. Command log  -> email/voice commands applied since the last run (planner-runs.jsonl)
  4. claude -p    -> planner-prompt.txt + the bundle above. Claude reads the Tasks DB and
                     Google Calendar itself via MCP, upserts/dedupes/scores tasks, rewrites
                     the Notion "Today" page, and returns a JSON report with the email brief.
  5. Email        -> the brief goes to NOTIFY_EMAIL with a "+planner" tag, From the watcher
                     account, so a plain reply becomes a command (see plaud_watcher.py).

Usage: planner.py --mode morning|evening|now [--dry-run] [--no-email]
Config comes from .env (systemd EnvironmentFile) plus planner-sources.json.
"""
import argparse
import email
import fnmatch
import html
import json
import os
import re
import smtplib
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from zoneinfo import ZoneInfo

from imapclient import IMAPClient

# ---------- config (from environment) ----------
WORKDIR        = os.environ.get("WORKDIR", os.path.dirname(os.path.abspath(__file__)))
IMAP_HOST      = os.environ.get("IMAP_HOST", "imap.gmail.com")
IMAP_PORT      = int(os.environ.get("IMAP_PORT", "993"))
IMAP_USER      = os.environ["IMAP_USER"]
IMAP_PASSWORD  = os.environ["IMAP_PASSWORD"]
IMAP_FOLDER    = os.environ.get("IMAP_FOLDER", "INBOX")
PLAUD_SENDER   = os.environ.get("PLAUD_SENDER", "plaud.ai")
NOTIFY_EMAIL   = os.environ["NOTIFY_EMAIL"]
SMTP_HOST      = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT      = int(os.environ.get("SMTP_PORT", "465"))
CLAUDE_BIN     = os.environ.get("CLAUDE_BIN", "claude")
MODEL          = os.environ.get("PLANNER_MODEL", os.environ.get("MODEL", "sonnet"))
CLAUDE_TIMEOUT = int(os.environ.get("PLANNER_TIMEOUT", "1200"))   # first bulk run took 519 s; unit allows 1500
CANVAS_BASE    = os.environ.get("CANVAS_BASE_URL", "https://sdsu.instructure.com").rstrip("/")
CANVAS_TOKEN   = os.environ.get("CANVAS_TOKEN", "")
EMAIL_TAG      = os.environ.get("PLANNER_EMAIL_TAG", "planner")
TZ             = ZoneInfo(os.environ.get("PLANNER_TZ", "America/Los_Angeles"))
PROMPT_FILE    = os.path.join(WORKDIR, os.environ.get("PLANNER_PROMPT_FILE", "planner-prompt.txt"))
SOURCES_FILE   = os.path.join(WORKDIR, os.environ.get("PLANNER_SOURCES_FILE", "planner-sources.json"))
STATE_FILE     = os.path.join(WORKDIR, os.environ.get("PLANNER_STATE_FILE", "planner-state.json"))
RUNS_LOG       = os.path.join(WORKDIR, os.environ.get("PLANNER_RUNS_LOG", "planner-runs.jsonl"))
ALLOWED_TOOLS  = os.environ.get("PLANNER_ALLOWED_TOOLS", "mcp__notion__*,mcp__claude_ai_Google_Calendar__*")
BUNDLE_CAP     = int(os.environ.get("PLANNER_BUNDLE_CAP", "40000"))   # chars handed to Claude
CREDS_FILE     = os.environ.get("CLAUDE_CREDENTIALS",
                                os.path.join(os.path.expanduser("~"), ".claude", ".credentials.json"))
AUTH_WARN_DAYS = int(os.environ.get("PLANNER_AUTH_WARN_DAYS", "3"))


# ---------- small helpers ----------
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


def tagged(addr, tag):
    """user@gmail.com -> user+tag@gmail.com (leave an existing +tag alone)."""
    local, _, domain = addr.partition("@")
    if not domain or "+" in local:
        return addr
    return f"{local}+{tag}@{domain}"


def glob_any(patterns, *candidates):
    cands = [c.lower() for c in candidates if c]
    return any(fnmatch.fnmatchcase(c, p.lower()) for p in patterns for c in cands)


def strip_html(text):
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def local_dt(iso):
    """ISO/RFC3339 (usually UTC 'Z') -> 'YYYY-MM-DD HH:MM (Dow)' in the planner timezone."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M (%a)")


def fix_notion_links(text):
    """Bare https://app.notion.com/<id> 404s; the page form is /p/<id>."""
    return re.sub(r"(app\.notion\.com/)(?!p/)([0-9a-f]{32})", r"\1p/\2", text or "")


def extract_json(text):
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    candidate = m.group(1) if m else None
    if candidate is None:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        candidate = text[start:end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


# ---------- source 1: Canvas ----------
def canvas_get(path, params=None):
    """GET a Canvas API collection, following Link: rel="next" pagination."""
    url = f"{CANVAS_BASE}/api/v1/{path.lstrip('/')}"
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    out = []
    while url:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {CANVAS_TOKEN}"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            link = resp.headers.get("Link", "")
        out.extend(data if isinstance(data, list) else [data])
        m = re.search(r'<([^>]+)>;\s*rel="next"', link)
        url = m.group(1) if m else None
    return out


def canvas_project(context_name, course_map):
    name = (context_name or "").upper().replace(" ", "")
    for code, project in course_map.items():
        if code.upper().replace(" ", "") in name:
            return project
    return None


def canvas_bundle(cfg, today):
    """Canvas planner items (assignments, quizzes, discussions, events) with submission state,
    plus recent announcements. Returns (text, count, error)."""
    if not CANVAS_TOKEN:
        return "Canvas: CANVAS_TOKEN is not set — Canvas skipped this run.", 0, "no token"
    c = cfg.get("canvas", {})
    course_map = c.get("course_map", {})
    skip = tuple(c.get("skip_title_prefixes", []))
    skip_ctx = c.get("skip_context_patterns", [])
    start = today - timedelta(days=int(c.get("days_back", 7)))
    end = today + timedelta(days=int(c.get("days_ahead", 90)))
    try:
        items = canvas_get("planner/items", {
            "start_date": start.isoformat() + "T00:00:00Z",
            "end_date": end.isoformat() + "T23:59:59Z",
            "per_page": 100,
        })
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as e:
        return f"Canvas: unavailable this run ({e}).", 0, str(e)

    lines, n = [], 0
    for it in items:
        ptype = it.get("plannable_type") or "item"
        p = it.get("plannable") or {}
        title = (p.get("title") or p.get("name") or "").strip()
        if not title or title.startswith(skip) or ptype == "announcement":
            continue
        if glob_any(skip_ctx, it.get("context_name")):
            continue
        due = p.get("due_at") or p.get("todo_date") or it.get("plannable_date")
        sub = it.get("submissions") if isinstance(it.get("submissions"), dict) else {}
        over = it.get("planner_override") or {}
        if sub.get("excused"):
            state = "excused"
        elif sub.get("graded"):
            state = "graded"
        elif sub.get("submitted"):
            state = "submitted"
        elif over.get("marked_complete"):
            state = "marked_complete"      # Robert ticked it off in Canvas's own To-Do
        elif sub.get("missing"):
            state = "missing"
        else:
            state = "open"
        if over.get("dismissed"):
            state += ",dismissed"
        url = it.get("html_url") or ""
        if url.startswith("/"):
            url = CANVAS_BASE + url
        project = canvas_project(it.get("context_name"), course_map) or "Unknown"
        key = f"canvas:{ptype}:{p.get('id')}"
        pts = p.get("points_possible")
        lines.append(
            f"- [{key}] {ptype} | due {local_dt(due) or 'undated'} | {project} "
            f"({it.get('context_name') or '?'}) | {title} | state={state}"
            + (f" | points={pts:g}" if isinstance(pts, (int, float)) else "")
            + (f" | {url}" if url else "")
        )
        n += 1

    # Announcements: deadline changes and instructions usually arrive here first.
    ann_lines = []
    try:
        courses = canvas_get("courses", {"enrollment_state": "active", "per_page": 50})
        courses = [c for c in courses if c.get("id")
                   and not glob_any(skip_ctx, c.get("name"), c.get("course_code"))]
        codes = [f"course_{c['id']}" for c in courses]
        if codes:
            anns = canvas_get("announcements", {
                "context_codes[]": codes, "start_date": start.isoformat(),
                "end_date": end.isoformat(), "per_page": 50,
            })
            for a in anns:
                ctx = next((c.get("course_code") or c.get("name") for c in courses
                            if f"course_{c.get('id')}" == a.get("context_code")), a.get("context_code"))
                body = strip_html(a.get("message") or "")[:400]
                ann_lines.append(f"- {local_dt(a.get('posted_at'))} | {canvas_project(ctx, course_map) or ctx} "
                                 f"| {a.get('title')} | {body}"
                                 + (f" | {a.get('html_url')}" if a.get("html_url") else ""))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, KeyError) as e:
        ann_lines.append(f"(announcements unavailable: {e})")

    text = (f"Canvas planner items, {start} → {end} ({n} items; state=submitted/graded/marked_complete "
            f"means DONE, missing means overdue with no submission):\n" + "\n".join(lines))
    if ann_lines:
        text += f"\n\nCanvas announcements since {start}:\n" + "\n".join(ann_lines)
    return text, n, None


# ---------- source 2: forwarded email ----------
def get_text(msg):
    plain, htmls = [], []
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        ctype = part.get_content_type()
        if "attachment" in str(part.get("Content-Disposition") or ""):
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            payload = None
        if not payload:
            continue
        text = payload.decode(part.get_content_charset() or "utf-8", "replace")
        (plain if ctype == "text/plain" else htmls if ctype == "text/html" else []).append(text)
    if plain:
        return "\n".join(plain)
    return strip_html("\n".join(htmls))


def email_bundle(cfg, state, today):
    """Scan the watcher inbox (read-only) for allow-listed senders since the lookback window."""
    ecfg = cfg.get("email", {})
    allow, deny = ecfg.get("allow", []), ecfg.get("deny", [])
    lookback = int(ecfg.get("lookback_days", 3))
    max_msgs = int(ecfg.get("max_messages_per_run", 25))
    command_senders = {s.lower() for s in cfg.get("commands", {}).get("senders", [])}
    command_senders.add(NOTIFY_EMAIL.lower())
    seen = set(state.get("seen_email_ids", []))
    since = today - timedelta(days=lookback)

    picked, new_ids, skipped = [], [], 0
    with IMAPClient(IMAP_HOST, port=IMAP_PORT, use_uid=True, ssl=True) as server:
        server.login(IMAP_USER, IMAP_PASSWORD)
        server.select_folder(IMAP_FOLDER, readonly=True)
        uids = server.search(["SINCE", since])
        if not uids:
            return "Forwarded email: nothing in the window.", 0, new_ids
        headers = server.fetch(uids, ["BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)]"])
        wanted = []
        for uid, d in headers.items():
            raw = next((v for k, v in d.items() if k.startswith(b"BODY[")), b"")
            h = email.message_from_bytes(raw)
            name, addr = parseaddr(h.get("From", ""))
            addr = addr.lower()
            header = f"{name} <{addr}>"
            mid = (h.get("Message-ID") or f"uid:{uid}").strip()
            if PLAUD_SENDER and PLAUD_SENDER.lower() in addr:
                continue
            if addr in command_senders or mid in seen:
                continue
            if not glob_any(allow, addr, header) or glob_any(deny, addr, header):
                skipped += 1
                continue
            wanted.append((uid, mid, name, addr, h.get("Subject", ""), h.get("Date", "")))
        wanted = wanted[-max_msgs:]
        if wanted:
            bodies = server.fetch([w[0] for w in wanted], ["BODY.PEEK[]"])
            for uid, mid, name, addr, subject, date_hdr in wanted:
                raw = next((v for k, v in bodies.get(uid, {}).items() if k.startswith(b"BODY[")), b"")
                text = get_text(email.message_from_bytes(raw))
                text = re.sub(r"\n{3,}", "\n\n", text).strip()[:1500]
                try:
                    when = parsedate_to_datetime(date_hdr).astimezone(TZ).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    when = date_hdr
                picked.append(f"### email:{mid}\nFrom: {name} <{addr}>\nDate: {when}\nSubject: {subject}\n{text}")
                new_ids.append(mid)
    if not picked:
        return f"Forwarded email: no new allow-listed mail since {since} ({skipped} filtered out).", 0, new_ids
    return (f"Forwarded email since {since} ({len(picked)} new, {skipped} filtered out). Source Key "
            f"for a task created from one of these is the 'email:<Message-ID>' header shown:\n\n"
            + "\n\n".join(picked)), len(picked), new_ids


# ---------- source 3: commands applied since the last run ----------
def commands_since(last_run_iso):
    if not last_run_iso or not os.path.exists(RUNS_LOG):
        return "Commands applied since the last run: none recorded."
    lines = []
    with open(RUNS_LOG, encoding="utf-8") as f:
        for raw in f:
            try:
                r = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if r.get("kind") != "command" or (r.get("time") or "") <= last_run_iso:
                continue
            rep = r.get("report") or {}
            for a in rep.get("applied", []):
                lines.append(f"- {a.get('task_id')} {a.get('action')}: {a.get('name')} ({a.get('detail')})")
            for c in rep.get("created", []):
                lines.append(f"- created {c.get('task_id')}: {c.get('name')}")
            for u in rep.get("unresolved", []):
                lines.append(f"- UNRESOLVED: {u}")
    return "Commands applied since the last run (via email/voice):\n" + ("\n".join(lines) or "- none")


# ---------- assembling and running ----------
def build_bundle(mode, cfg, state, now):
    today = now.date()
    canvas_text, n_canvas, canvas_err = canvas_bundle(cfg, today)
    email_text, n_email, new_ids = email_bundle(cfg, state, today)
    cmd_text = commands_since(state.get("last_run"))
    head = (
        f"## Run context\n"
        f"- Mode: {mode}\n"
        f"- Now: {now.strftime('%Y-%m-%d %H:%M %Z')} ({now.strftime('%A')})\n"
        f"- Previous run: {state.get('last_run') or 'never'}\n"
        f"- Command address (for mailto links and replies): {IMAP_USER}\n"
        f"- Brief recipient: {tagged(NOTIFY_EMAIL, EMAIL_TAG)}\n"
    )
    bundle = "\n\n".join([head, "## Canvas\n" + canvas_text, "## Email\n" + email_text, "## " + cmd_text])
    if len(bundle) > BUNDLE_CAP:
        # Trim email first (largest, least structured), keep Canvas and the header intact.
        keep = BUNDLE_CAP - len(head) - len(canvas_text) - len(cmd_text) - 200
        email_text = email_text[:max(keep, 2000)] + "\n[… email section truncated …]"
        bundle = "\n\n".join([head, "## Canvas\n" + canvas_text, "## Email\n" + email_text, "## " + cmd_text])
    meta = {"canvas": n_canvas, "canvas_error": canvas_err, "email": n_email, "new_email_ids": new_ids}
    return bundle, meta


def check_auth():
    """Pre-flight the Claude login. Headless `claude -p` cannot re-authenticate itself: once the
    refresh token is dead it exits in ~50 ms having done nothing, which is how the 2026-09-15
    morning run was lost. Reads the credential file only — no API call, no cost.

    Returns (ok, message). Anything it cannot prove is treated as fine: a missing or unfamiliar
    credential file means some other auth path (e.g. ANTHROPIC_API_KEY) is in play, and refusing
    to run on that basis would be worse than letting the run try.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True, None
    try:
        with open(CREDS_FILE, encoding="utf-8") as f:
            oauth = json.load(f).get("claudeAiOauth") or {}
    except (OSError, ValueError):
        return True, None
    expires = oauth.get("refreshTokenExpiresAt")
    if not isinstance(expires, (int, float)):
        return True, None
    when = datetime.fromtimestamp(expires / 1000, timezone.utc).astimezone(TZ)
    left = expires / 1000 - datetime.now(timezone.utc).timestamp()
    if left <= 0:
        return False, (f"Claude Code's login expired {when:%Y-%m-%d %H:%M %Z} and a headless run "
                       f"cannot renew it, so this run would fail having written nothing. Fix: log "
                       f"in on this machine as the user the timer runs as — `claude` — then check "
                       f"`claude mcp list`. The next scheduled run picks up on its own.")
    if left < AUTH_WARN_DAYS * 86400:
        return True, (f"Claude Code's login expires {when:%Y-%m-%d %H:%M %Z} "
                      f"({left / 86400:.1f} days) — run `claude` on this machine before then.")
    return True, None


AUTH_FAILURE_MARKERS = ("oauth", "authenticate", "authentication_failed", "invalid api key",
                        "please run /login", "credentials")


def describe_failure(run, envelope, report):
    """Best available explanation for a failed run, most specific first. `claude` reports a fatal
    auth error only in the JSON envelope's `result` with an empty stderr, so stderr alone is not
    enough — trusting it is what logged the useless 'no report/page/email in result' on 09-15.
    """
    for candidate in (run.get("stderr"),
                      (envelope or {}).get("result") if isinstance(envelope, dict) else None,
                      run.get("stdout")):
        if isinstance(candidate, str) and candidate.strip():
            text = candidate.strip()[-1000:]
            break
    else:
        text = "no report/page/email in result"
    subtype = (envelope or {}).get("subtype") if isinstance(envelope, dict) else None
    if subtype and subtype != "success":
        text = f"[{subtype}] {text}"
    if isinstance(report, dict) and not ((report.get("today_page") or {}).get("url")):
        text = f"{text}\n(report present but carried no today_page url)"
    return text


def looks_like_auth_failure(text):
    low = (text or "").lower()
    return any(marker in low for marker in AUTH_FAILURE_MARKERS)


def run_claude(prompt):
    cmd = [CLAUDE_BIN, "-p", prompt, "--model", MODEL, "--allowedTools", ALLOWED_TOOLS,
           "--permission-mode", "acceptEdits", "--output-format", "json"]
    try:
        res = subprocess.run(cmd, cwd=WORKDIR, capture_output=True, text=True, timeout=CLAUDE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": f"timeout after {CLAUDE_TIMEOUT}s"}
    return {"ok": res.returncode == 0, "stdout": res.stdout or "", "stderr": res.stderr or ""}


def send_mail(subject, text, html_body=None, to=None):
    msg = EmailMessage()
    msg["From"] = IMAP_USER
    msg["To"] = to or tagged(NOTIFY_EMAIL, EMAIL_TAG)
    msg["Reply-To"] = IMAP_USER
    msg["Subject"] = subject
    msg.set_content(text)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
        smtp.login(IMAP_USER, IMAP_PASSWORD)
        smtp.send_message(msg)


def log_run(record):
    with open(RUNS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["morning", "evening", "now"], default="now")
    ap.add_argument("--dry-run", action="store_true", help="print the bundle Claude would get and exit")
    ap.add_argument("--no-email", action="store_true", help="run Claude and update Notion but send nothing")
    args = ap.parse_args()

    cfg = load_json(SOURCES_FILE, {})
    state = load_json(STATE_FILE, {})
    now = datetime.now(TZ)
    with open(PROMPT_FILE, encoding="utf-8") as f:
        base_prompt = f.read()

    # Pre-flight the login before spending anything: Canvas calls, tokens, or the run's slot.
    auth_ok, auth_note = (True, None) if args.dry_run else check_auth()
    if not auth_ok:
        log_run({
            "time": datetime.now(timezone.utc).isoformat(), "kind": "plan", "mode": args.mode,
            "ok": False, "cost_usd": None, "duration_ms": None, "stage": "preflight",
            "ingested": None, "report": None, "error": auth_note,
        })
        try:
            send_mail(f"[{EMAIL_TAG}] {args.mode} run BLOCKED — Claude Code needs a login",
                      f"{auth_note}\n\nNothing was read or written. See {RUNS_LOG}.")
        except Exception as e:
            print(f"could not send auth alert: {e}", file=sys.stderr)
        print(f"planner {args.mode}: BLOCKED — {auth_note}", file=sys.stderr)
        return 1
    if auth_note:
        print(f"planner {args.mode}: {auth_note}", file=sys.stderr)

    bundle, meta = build_bundle(args.mode, cfg, state, now)
    if args.dry_run:
        print(bundle)
        print(f"\n[dry-run] bundle chars={len(bundle)} meta={json.dumps({k: v for k, v in meta.items() if k != 'new_email_ids'})}")
        return 0

    started = datetime.now(timezone.utc)
    run = run_claude(base_prompt + "\n\n---\n" + bundle)
    envelope, report, cost, duration_ms = None, None, None, None
    try:
        envelope = json.loads(run["stdout"])
        report = extract_json(envelope.get("result"))
        cost = envelope.get("total_cost_usd")
        duration_ms = envelope.get("duration_ms")
    except (json.JSONDecodeError, TypeError):
        pass
    page_url = ((report or {}).get("today_page") or {}).get("url") if isinstance(report, dict) else None
    mail = (report or {}).get("email") if isinstance(report, dict) else None
    ok = bool(run["ok"] and not (envelope or {}).get("is_error") and page_url and isinstance(mail, dict) and mail.get("body_html"))

    record = {
        "time": started.isoformat(), "kind": "plan", "mode": args.mode, "ok": ok,
        "cost_usd": cost, "duration_ms": duration_ms,
        "ingested": {"canvas": meta["canvas"], "email": meta["email"], "canvas_error": meta["canvas_error"]},
        "report": {k: v for k, v in (report or {}).items() if k != "email"} if isinstance(report, dict) else None,
        "error": None if ok else describe_failure(run, envelope, report),
    }
    log_run(record)

    if not ok:
        tail = (envelope or {}).get("result") if isinstance(envelope, dict) else run["stdout"]
        why = record["error"]
        auth = looks_like_auth_failure(why)
        subject = (f"[{EMAIL_TAG}] {args.mode} run FAILED — Claude Code needs a login" if auth
                   else f"[{EMAIL_TAG}] {args.mode} run FAILED")
        hint = ("\nThis looks like an expired login. Log in on this machine as the user the timer "
                "runs as — `claude` — then check `claude mcp list`.\n" if auth else "")
        try:
            send_mail(subject,
                      f"Why: {why}\n{hint}\nexit_ok={run['ok']} cost={cost} "
                      f"duration_ms={duration_ms}\n\nClaude's final text (tail):\n"
                      f"{(tail or '')[-2000:]}\n\nstderr tail:\n"
                      f"{(run['stderr'] or '(empty)')[-1000:]}\n\nSee {RUNS_LOG}.")
        except Exception as e:
            print(f"could not send failure alert: {e}", file=sys.stderr)
        print(f"planner {args.mode}: FAILED — {why} (see {RUNS_LOG})", file=sys.stderr)
        return 1

    subject = mail.get("subject") or f"[{EMAIL_TAG}] {now.strftime('%a %b %-d')} brief"
    if not subject.startswith(f"[{EMAIL_TAG}]"):
        subject = f"[{EMAIL_TAG}] {subject}"
    footer_txt = f"\n\n--\nplanner {args.mode} run · ${cost:.2f} · Today page: {page_url}" if cost is not None else ""
    footer_html = (f'<p style="color:#888;font-size:12px;margin-top:24px">planner {args.mode} run · '
                   f'${cost:.2f} · <a href="{page_url}">Today page</a></p>') if cost is not None else ""
    if not args.no_email:
        send_mail(subject,
                  fix_notion_links((mail.get("body_text") or "See the HTML version.") + footer_txt),
                  fix_notion_links(mail["body_html"] + footer_html))

    state["last_run"] = started.isoformat()
    state.setdefault("runs", {})[args.mode] = started.isoformat()
    seen = state.get("seen_email_ids", []) + meta["new_email_ids"]
    state["seen_email_ids"] = seen[-1000:]
    save_state(state)
    print(f"planner {args.mode}: ok cost=${cost or 0:.2f} canvas={meta['canvas']} email={meta['email']} "
          f"today={page_url}" + ("" if args.no_email else f" emailed={tagged(NOTIFY_EMAIL, EMAIL_TAG)}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
