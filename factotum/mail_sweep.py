"""The mail sweep (SPEC.md §2 "Mailbird's role" item 1, §6): every `sources.mail.sweep_minutes`,
Python (no Claude) reads each monitored account's new Inbox and Sent conversations and:
  - flips a Waiting task when the ADDRESS it waits on writes back (waiting_on cleared, Review shows
    "Thread updated"); a match on the display name alone is only flagged, since names are forgeable,
  - closes a "reply to X" task when a message was sent to X after the task was created
    (closed_by `mail-evidence`), and records any other sent-to-counterpart as a candidate,
  - closes a WATCHED task (`close_on_sent_since` / `close_on_sent_to`, set by the `watch_sent` op) when a
    message to the watched address is sent after the watch began, whatever the task is called, and
    ends the watch; a watched task is closed by its watch only,
  - watches linked threads (links.kind = mail_thread) and flags new messages,
  - collects .ics invitations and confirmation-shaped mail as candidates for the planner.
Everything it finds is a row in `evidence`; the planner judges the candidates, Review shows the
flags. Reads only: bodies are fetched for candidates alone, truncated, and treated as untrusted.

    sweep(conn, cfg, client, now_local) -> summary | None
    review_flags(conn)                  -> {task_id: {"thread_updated": True, "planner": label}}
    sweep_job(conn, cfg, now_local)     -> what the scheduler calls
"""
from __future__ import annotations

import fnmatch
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import batches, config as C
from .db import add_link, get_settings, now_iso, rows, set_setting

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
REPLY_RE = re.compile(r"^\s*(reply|respond|answer|get back)\b", re.I)      # a task that IS a reply; "send", "email", "write" name a deliverable
CONFIRM_RE = re.compile(r"\b(confirm(ed|ation)?|registered|registration|receipt|accepted|approved|your submission|has been received|scheduled|enrolled|appointment)\b", re.I)
ICS_RE = re.compile(r"\b(invitation|invite|updated invitation|accepted:|declined:|tentative:)\b|\.ics\b", re.I)
EXCERPT = 500
SENT_OVERLAP_MIN = 10
BODY_CAP = 1500                     # how much of a new mail the triage run reads (mail_triage.py)
# Senders whose mail is never a candidate for a task, when the config names none (generic noise).
SKIP_SENDERS = ["*noreply*", "*no-reply*", "*no_reply*", "*donotreply*", "*do-not-reply*", "*newsletter*", "*mailer-daemon*", "*notifications@*", "*notification@*", "*marketing@*", "*news@*", "*digest@*"]


_INVISIBLE = re.compile("[\u200b\u200e\u200f\u2060\ufeff\u00ad\u034f\u061c\u180e\u202a-\u202e\u2066-\u2069]")
_JOINERS = "\u200c\u200d"            # ZWNJ / ZWJ: spelling inside a word or an emoji, padding at a word's edge
_JOINER_RUN = re.compile("[\u200c\u200d]{3,}")
_MD_LINK = re.compile(r"!?\[([^\]\n]{0,300})\]\([^()\s]{0,2000}\)")      # bounded: mail is written by strangers, and this runs on all of it
_URL = re.compile(r"https?://[^\s<>\"')\]]{1,4000}")
_HOST = re.compile(r"https?://[^/\s]{1,255}")
READ_CAP = BODY_CAP * 20            # how much of a body is cleaned at all


def clean_body(text) -> str:
    """A message body as the triage run should read it: Mailbird hands over Markdown, and in bulk
    mail most of it is tracking links and invisible padding (a real 1,500-character excerpt held
    four words). Links keep their text, a long bare URL keeps its host, padding goes."""
    def short(m) -> str:
        u = m.group(0)
        if len(u) <= 60:
            return u
        h = _HOST.match(u)
        return (h.group(0) + "/…") if h else u[:60] + "…"
    t = _INVISIBLE.sub("", str(text or "")[:READ_CAP])
    # Bulk mail pads with "ZWNJ space ZWNJ space …". No lookaround here: a pattern that asked "is this run
    # followed by a space" took 30 s on a 29,000-joiner run inside one word. A run of three or more is
    # never spelling; the rest is stripped from each word's edges by plain string work, linear in the text.
    t = _JOINER_RUN.sub("", t)
    t = _MD_LINK.sub(lambda m: m.group(1), _MD_LINK.sub(lambda m: m.group(1), t))      # twice: a linked image is a link around an image
    t = _URL.sub(short, t)
    t = re.sub(r"\s+", " ", t)
    return " ".join(w for w in (x.strip(_JOINERS) for x in t.split(" ")) if w)[:BODY_CAP]


def _unread(c: dict) -> bool:
    return str(c.get("isRead")).strip().lower() in ("false", "0")


def _utc_str(local: datetime, cfg: dict) -> str:
    tz = ZoneInfo(str(C.get(cfg, "owner.timezone", "UTC")))
    return local.replace(tzinfo=tz).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _glob_any(pats, *cands) -> bool:
    return any(fnmatch.fnmatch((c or "").lower(), str(p).lower()) for p in (pats or []) for c in cands if c)


def _person(x) -> tuple[str, str]:
    """(address, display name) of a sender or recipient. Mailbird writes {email, displayName}; the
    sweep read {address, name} until 2026-11-05 and so saw nobody on any real message (no waiting
    flip, no close on a sent mail, no watch ever fired). Both shapes and a bare string are taken."""
    if isinstance(x, str):
        return x.strip().lower(), x.strip()
    if not isinstance(x, dict):
        return "", ""
    addr = str(x.get("email") or x.get("address") or "").strip().lower()
    return addr, str(x.get("displayName") or x.get("name") or addr).strip()


def _addr(m: dict | None) -> tuple[str, str]:
    return _person((m or {}).get("from"))


def _recipients(m: dict, fields=("to", "cc", "bcc")) -> set[str]:
    return {a for k in fields for a in (_person(r)[0] for r in m.get(k) or []) if a}


def _evidence(conn, task_id, kind, ref, account, label, excerpt, at) -> bool:
    if conn.execute("SELECT 1 FROM evidence WHERE kind = ? AND ref = ? AND task_id IS ?", (kind, ref, task_id)).fetchone():
        return False
    conn.execute("INSERT INTO evidence (task_id, kind, ref, account, label, excerpt, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (task_id, kind, ref, account, (label or "")[:200], (excerpt or "")[:EXCERPT], at or now_iso()))
    return True


def _waiting_match(task: dict, msgs: list[dict]) -> str | None:
    """'address' when one of `msgs` comes from an address named in waiting_on (proof enough to flip),
    'name' when only the display name carries every word of the name (spoofable, and a first name is
    common: a hint for Review, never a flip, a link or a reason to skip the other rules), else None.
    The caller passes only the messages that arrived since the last pass and that the owner did not
    write: an old message from that person in a thread somebody else just answered is no reply."""
    w = str(task.get("waiting_on") or "")
    emails = {e.lower() for e in EMAIL_RE.findall(w)}
    name = re.split(r"[(—–,;:-]", w, 1)[0].strip()
    tokens = [t.lower() for t in re.findall(r"[A-Za-z]{3,}", name) if t.lower() not in ("the", "and", "for", "from")]
    found = None
    for m in msgs:
        addr, disp = _addr(m)
        if addr and addr in emails:
            return "address"
        words = {x.lower() for x in re.findall(r"[A-Za-z]+", disp)}
        if tokens and all(t in words for t in tokens):
            found = "name"
    return found


def sweep(conn: sqlite3.Connection, cfg: dict, client, now_local: datetime) -> str | None:
    st = get_settings(conn)
    lookback = int(C.get(cfg, "sources.mail.lookback_days", 3))
    limit = int(C.get(cfg, "sources.mail.max_per_sweep", 60))
    allow, deny = C.get(cfg, "sources.mail.allow", ["*"]), C.get(cfg, "sources.mail.deny", [])

    def mb(tool: str, args: dict):
        """Every Mailbird call goes through here: what has been written so far is committed first, so
        the sweep never holds SQLite's write lock across the network (a UI Apply would wait five
        seconds behind it and fail, as with the Slack sweep on 2026-11-01). The price: evidence can
        be committed and the sweep die before its batch is applied; the planner then finds that
        evidence unjudged and proposes the change, so nothing is lost for good."""
        if conn.in_transaction:
            conn.commit()
        return client.call(tool, args)

    monitored = [a for a in rows(conn, "SELECT * FROM accounts WHERE enabled = 1") if "monitor" in json.loads(a.get("roles_json") or "[]")]
    mb_accounts = {str(a.get("email") or "").lower(): a for a in mb("list_accounts", {})}
    open_tasks = rows(conn, "SELECT * FROM tasks WHERE status = 'open'")
    waiting = [t for t in open_tasks if t.get("waiting_on")]
    owed = [(t, {e.lower() for e in EMAIL_RE.findall(str(t.get("counterpart") or ""))}) for t in open_tasks if t.get("counterpart")]
    owed = [(t, es) for t, es in owed if es]
    watched = [(t, {e.lower() for e in EMAIL_RE.findall(str(t.get("close_on_sent_to") or ""))}) for t in open_tasks if t.get("close_on_sent_since")]
    fired: set[int] = set()
    links = rows(conn, "SELECT l.*, t.status FROM links l JOIN tasks t ON t.id = l.task_id WHERE l.kind = 'mail_thread' AND t.status = 'open'")
    followed = {(l["task_id"], l["ref"]) for l in links}     # threads a task followed BEFORE this pass
    changes: list[dict] = []
    flipped = closed = threads = candidates = new_mail = 0
    notes: list[str] = []
    fetched: dict[str, dict] = {}
    bodies: dict[int, str] = {}
    # Tasks suggested from mail (SPEC §8.5): new Inbox mail that no rule below claims is kept as
    # `mail_new` evidence for the triage run. Never the owner's own mail (the brief arrives in this
    # inbox), never a thread a task already follows, never a sender the config calls noise.
    suggest = bool(C.get(cfg, "sources.mail.suggest.enabled", False))
    skip = C.get(cfg, "sources.mail.suggest.skip_senders", None) or SKIP_SENDERS
    only_unread = bool(C.get(cfg, "sources.mail.suggest.only_unread", False))
    per_sweep = int(C.get(cfg, "sources.mail.suggest.max_per_sweep", 20))
    from .brief import smtp_settings
    own = ({str(a["address"]).lower() for a in rows(conn, "SELECT address FROM accounts")} | {str(e).lower() for e in C.get(cfg, "owner.emails", []) or []}
           | {str(smtp_settings().get("user") or "").lower()}) - {""}
    tracked = {l["ref"] for l in links}

    def body_of(msg: dict, c: dict | None = None) -> str | None:
        """A message's text, cleaned (None when Mailbird cannot hand it over). The message is named
        by its own id: a conversation row's lastMessageId can point at another one."""
        try:
            mid = int(msg.get("id") or (c or {}).get("lastMessageId"))
        except (TypeError, ValueError):
            return None
        if mid not in bodies:
            try:
                m = mb("get_message", {"messageId": mid})
                bodies[mid] = clean_body(m.get("body") or m.get("bodyPreview"))
            except Exception as e:                           # one unreadable message is a note, never a failed sweep
                notes.append(f"message {mid}: {type(e).__name__}")
                bodies[mid] = None
        return bodies[mid]

    def candidate(c: dict, msgs: list[dict], account: str, backlog: bool = False) -> bool:
        """Keep the conversation's newest message for the triage. False only when the per-sweep cap
        stopped it: the caller then leaves the watermark where it was, so the mail is met again."""
        nonlocal new_mail
        cid, when, last = c["conversationId"], str(c.get("receivedAtUtc") or ""), msgs[-1]
        addr, disp = _addr(last)
        disp = " ".join(disp.split())
        if not addr or addr in own or cid in tracked or _glob_any(skip, addr, f"{disp} <{addr}>"):
            return True
        if (only_unread or backlog) and not _unread(c):
            return True
        if conn.execute("SELECT 1 FROM evidence WHERE kind = 'mail_new' AND ref = ? AND at >= ?", (cid, when)).fetchone():
            return True                                      # this message is already a candidate; a later one in the thread is a new one
        if new_mail >= per_sweep:
            return False
        to = _recipients(last, ("to",))
        whom = ("to you" if to <= own else f"to you and {len(to - own)} other{'s' if len(to - own) != 1 else ''}") if to & own else "you are not in To (cc, bcc or a list)"
        body = body_of(last, c)
        conn.execute("INSERT INTO evidence (task_id, kind, ref, account, label, excerpt, at) VALUES (NULL, 'mail_new', ?, ?, ?, ?, ?)",
                     (cid, account, str(c.get("subject") or "")[:200],
                      f"from {disp} <{addr}> · {whom}{' · unread' if _unread(c) else ''}\n{body if body is not None else '(the text could not be read; judge by the subject and the sender)'}", when or now_iso()))
        new_mail += 1
        return True

    def conversation(cid: str) -> dict | None:
        if cid not in fetched:
            try:
                fetched[cid] = mb("get_conversation", {"conversationId": cid})
            except Exception as e:   # a vanished thread is not a failed sweep
                notes.append(f"{cid[:8]}: {type(e).__name__}")
                fetched[cid] = None
        return fetched[cid]

    for acc in monitored:
        address = str(acc["address"]).lower()
        mba = mb_accounts.get(address)
        if not mba:
            notes.append(f"{address} not in Mailbird")
            continue
        folders = {str(f.get("name") or "").lower(): f["id"] for f in mb("list_folders", {"accountId": mba["id"]})}
        key = f"state.mail_sweep.last_at.{address}"
        first = not (st.get(key) or st.get(f"{key}.inbox"))  # never swept: its mail of the last days counts only while unread
        start = str(st.get(key) or _utc_str(now_local - timedelta(days=lookback), cfg))     # `key` alone is the pre-2026-11-04 shared watermark
        wms = {f: str(st.get(f"{key}.{f}") or start) for f in ("inbox", "sent")}
        newest = dict(wms)
        held = False                                         # the cap left Inbox mail unread this pass
        for folder in ("inbox", "sent"):
            fid = folders.get(folder)
            if fid is None:
                continue
            wm = wms[folder]
            if folder == "sent":           # evidence is deduplicated, so re-reading ten minutes costs a few reads and loses nothing
                try:
                    wm = (datetime.fromisoformat(wm[:19]) - timedelta(minutes=SENT_OVERLAP_MIN)).isoformat()
                except ValueError:
                    pass
            listed = mb("list_conversations", {"folderId": fid, "limit": limit})
            convs = [c for c in listed if str(c.get("receivedAtUtc") or "") > wm]
            for c in convs:
                cid, when, subject = c["conversationId"], str(c.get("receivedAtUtc") or ""), str(c.get("subject") or "")
                newest[folder] = max(newest[folder], when)
                conv = conversation(cid)
                msgs = (conv or {}).get("messages") or []
                if not msgs:
                    continue
                last = msgs[-1]
                if folder == "sent":
                    # only what this account itself sent counts, each message on its own clock
                    mine = [m for m in msgs if _addr(m)[0] == address]
                    # A watch (`watch_sent`): the user said "close this when I have sent the mail", so a message
                    # to the watched address after the watch began closes the row, whatever it is called; the
                    # watch ends with it (a reopened task is not closed again by the next mail).
                    for t, wemails in watched:
                        since = str(t["close_on_sent_since"])[:19]
                        if t["id"] in fired or not any(wemails & _recipients(m) and str(m.get("receivedAtUtc") or "")[:19] > since for m in mine):
                            continue
                        fired.add(t["id"])
                        add_link(conn, t["id"], "mail_thread", cid, label=subject[:120], account=address)
                        conn.execute("UPDATE links SET last_seen_at = ? WHERE task_id = ? AND kind = 'mail_thread' AND ref = ?", (when, t["id"], cid))
                        if _evidence(conn, t["id"], "sent_watched", cid, address, subject, c.get("lastSender"), when):
                            eid = conn.execute("SELECT id FROM evidence WHERE kind = 'sent_watched' AND ref = ? AND task_id = ?", (cid, t["id"])).fetchone()[0]
                            changes += [{"task_id": t["id"], "op": "done", "fields": {}}, {"task_id": t["id"], "op": "unwatch_sent"},
                                        {"task_id": t["id"], "op": "judge_evidence", "fields": {"evidence_id": eid, "verdict": "applied"}}]
                            closed += 1
                    # Mail to the person a task is owed to. It closes the task by itself in one case only:
                    # the task is a reply ("Reply to …", "Answer …") and the message is in the thread the
                    # task already follows. Any other mail to that address ("Coffee Thursday?" to someone
                    # three tasks are owed to) proves nothing: it is evidence with what was written, and the
                    # planner judges it. Until 2026-11-05 any such mail closed every "send/email/write …"
                    # task for that address; nobody noticed because the sweep saw no senders at all.
                    for t, emails in owed:
                        if t.get("close_on_sent_since"):
                            continue                                 # under a watch: only the watch closes it (above)
                        hits = [m for m in mine if emails & _recipients(m) and str(m.get("receivedAtUtc") or "")[:19] > str(t.get("created_at") or "")[:19]]
                        if not hits or conn.execute("SELECT 1 FROM evidence WHERE kind = 'sent_to_counterpart' AND ref = ? AND task_id = ?", (cid, t["id"])).fetchone():
                            continue
                        text = body_of(hits[-1])
                        said = f"to {', '.join(sorted(emails & _recipients(hits[-1])))}: {text if text is not None else '(text not readable)'}"
                        in_thread = (t["id"], cid) in followed
                        _evidence(conn, t["id"], "sent_to_counterpart", cid, address, subject, said, when)
                        if in_thread:
                            conn.execute("UPDATE links SET last_seen_at = ? WHERE task_id = ? AND kind = 'mail_thread' AND ref = ?", (when, t["id"], cid))
                        if in_thread and REPLY_RE.match(t["name"] or ""):
                            changes.append({"task_id": t["id"], "op": "done", "fields": {}}); closed += 1
                        else:
                            candidates += 1
                    continue
                addr, disp = _addr(last)
                header = f"{disp} <{addr}>"
                if not _glob_any(allow, addr, header) or _glob_any(deny, addr, header):
                    continue
                fresh = [m for m in msgs if str(m.get("receivedAtUtc") or "") > wm and _addr(m)[0] not in own]
                matched = False
                for t in waiting:
                    how = _waiting_match(t, fresh)
                    if how == "address":
                        matched = True
                        add_link(conn, t["id"], "mail_thread", cid, label=subject[:120], account=address)
                        conn.execute("UPDATE links SET last_seen_at = ? WHERE task_id = ? AND kind = 'mail_thread' AND ref = ?", (when, t["id"], cid))
                        if _evidence(conn, t["id"], "reply_from_waiting", cid, address, subject, c.get("lastSender"), when):
                            changes.append({"task_id": t["id"], "op": "set", "fields": {"waiting_on": None}}); flipped += 1
                    elif how == "name" and _evidence(conn, t["id"], "maybe_reply_from_waiting", cid, address, subject, c.get("lastSender"), when):
                        candidates += 1                      # a hint on the task's Review card; the mail goes on to the rules below
                if matched:
                    continue
                kind = "ics" if ICS_RE.search(subject) else "confirmation" if CONFIRM_RE.search(subject) else None
                if not kind:
                    if suggest and not candidate(c, msgs, address, backlog=first):
                        held = True
                    continue
                try:
                    m = mb("get_message", {"messageId": int(last.get("id") or c.get("lastMessageId"))})
                except Exception as e:
                    notes.append(f"message for {cid[:8]}: {type(e).__name__}")
                    continue
                if kind == "ics" and not any(str(a.get("fileName") or a.get("name") or "").lower().endswith(".ics") for a in (m.get("attachments") or [])):
                    continue                                 # Mailbird names an attachment `fileName`
                body = re.sub(r"\s+", " ", str(m.get("body") or m.get("bodyPreview") or "")).strip()
                if _evidence(conn, None, kind, cid, address, subject, f"from {header}: {body}", when):
                    candidates += 1
            if folder == "inbox" and suggest and not first and not st.get(f"state.mail_suggest.backfilled.{address}"):
                # Once per mailbox that was swept before suggestions were on: mail from the last days that
                # is still unread and older than the sweep's mark (what arrives from now on comes through above).
                since = _utc_str(now_local - timedelta(days=int(C.get(cfg, "sources.mail.suggest.backfill_days", 3))), cfg)
                full = True
                for c in listed:
                    if since < str(c.get("receivedAtUtc") or "") <= wm and _unread(c):
                        msgs = (conversation(c["conversationId"]) or {}).get("messages") or []
                        addr, disp = _addr(msgs[-1]) if msgs else ("", "")
                        if msgs and _glob_any(allow, addr, f"{disp} <{addr}>") and not _glob_any(deny, addr, f"{disp} <{addr}>"):
                            full = candidate(c, msgs, address, backlog=True) and full
                if full:                                     # the cap cut it short: the rest is taken on the next pass
                    set_setting(conn, f"state.mail_suggest.backfilled.{address}", True)
            elif folder == "inbox" and suggest and first:
                set_setting(conn, f"state.mail_suggest.backfilled.{address}", True)
        if held:
            newest["inbox"] = wms["inbox"]                   # mail past the cap is still unread by the triage: do not move on
            notes.append(f"{address}: more new mail than {per_sweep} a pass, the rest next time")
        for f in ("inbox", "sent"):
            if newest[f] != wms[f] or (first and f == "inbox"):
                set_setting(conn, f"{key}.{f}", newest[f])
    for link in links:
        conv = conversation(link["ref"])
        msgs = (conv or {}).get("messages") or []
        if not msgs:
            continue
        last_at = max(str(m.get("receivedAtUtc") or "") for m in msgs)
        seen = conn.execute("SELECT last_seen_at FROM links WHERE id = ?", (link["id"],)).fetchone()     # as it is now: a rule above may have moved it
        if last_at > str((seen[0] if seen else None) or ""):
            conn.execute("UPDATE links SET last_seen_at = ? WHERE id = ?", (last_at, link["id"]))
            if _evidence(conn, link["task_id"], "thread_updated", link["ref"], link.get("account"), (conv or {}).get("subject") or link.get("label"), (conv or {}).get("lastSender"), last_at):
                threads += 1
    if changes:
        batches.apply(conn, changes, "mail", now_local.date(), closed_by="mail-evidence")
    conn.commit()
    if not (flipped or closed or threads or candidates or new_mail):
        return None                                           # notes alone (an account Mailbird lacks) are not activity; mail-check shows them
    return (f"flipped {flipped}, closed {closed}, threads {threads}, candidates {candidates}" + (f" · {new_mail} for triage" if new_mail else "")
            + (f" · {'; '.join(notes[:4])}" if notes else ""))


def review_flags(conn: sqlite3.Connection) -> dict[int, dict]:
    """What the sweeps and the planner want Review to say, keyed by task id."""
    out: dict[int, dict] = {}
    for e in rows(conn, "SELECT task_id, kind, label FROM evidence WHERE judged_at IS NULL AND task_id IS NOT NULL"):
        if e["kind"] in ("thread_updated", "reply_from_waiting", "maybe_reply_from_waiting"):
            out.setdefault(int(e["task_id"]), {})["thread_updated"] = True
        elif e["kind"] == "slack_mention":
            out.setdefault(int(e["task_id"]), {})["mentioned"] = True
        elif e["kind"] == "proposal":
            out.setdefault(int(e["task_id"]), {})["planner"] = e["label"]
    return out


def sweep_job(conn: sqlite3.Connection, cfg: dict, now_local: datetime) -> str | None:
    if str(C.get(cfg, "sources.mail.provider", "mailbird")) != "mailbird":
        return None
    from .mailbird import Client
    client = Client(cfg)
    try:
        return sweep(conn, cfg, client, now_local)
    finally:
        client.close()
