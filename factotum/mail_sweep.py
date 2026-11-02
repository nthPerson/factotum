"""The mail sweep (SPEC.md §2 "Mailbird's role" item 1, §6): every `sources.mail.sweep_minutes`,
Python (no Claude) reads each monitored account's new Inbox and Sent conversations and:
  - flips a Waiting task when the ADDRESS it waits on writes back (waiting_on cleared, Review shows
    "Thread updated"); a match on the display name alone is only flagged, since names are forgeable,
  - closes a "reply to X" task when a message was sent to X after the task was created
    (closed_by `mail-evidence`), and records any other sent-to-counterpart as a candidate,
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
REPLY_VERB_RE = re.compile(r"^\s*(reply|respond|answer|send|email|e-mail|forward|write|get back)\b", re.I)
CONFIRM_RE = re.compile(r"\b(confirm(ed|ation)?|registered|registration|receipt|accepted|approved|your submission|has been received|scheduled|enrolled|appointment)\b", re.I)
ICS_RE = re.compile(r"\b(invitation|invite|updated invitation|accepted:|declined:|tentative:)\b|\.ics\b", re.I)
EXCERPT = 500


def _utc_str(local: datetime, cfg: dict) -> str:
    tz = ZoneInfo(str(C.get(cfg, "owner.timezone", "UTC")))
    return local.replace(tzinfo=tz).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _glob_any(pats, *cands) -> bool:
    return any(fnmatch.fnmatch((c or "").lower(), str(p).lower()) for p in (pats or []) for c in cands if c)


def _addr(m: dict | None) -> tuple[str, str]:
    f = (m or {}).get("from") or {}
    if isinstance(f, str):
        return f.lower(), f
    return str(f.get("address") or "").lower(), str(f.get("name") or f.get("address") or "")


def _recipients(m: dict) -> set[str]:
    out = set()
    for k in ("to", "cc", "bcc"):
        for r in m.get(k) or []:
            out.add((r.get("address") if isinstance(r, dict) else str(r)).lower())
    return out


def _evidence(conn, task_id, kind, ref, account, label, excerpt, at) -> bool:
    if conn.execute("SELECT 1 FROM evidence WHERE kind = ? AND ref = ? AND task_id IS ?", (kind, ref, task_id)).fetchone():
        return False
    conn.execute("INSERT INTO evidence (task_id, kind, ref, account, label, excerpt, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (task_id, kind, ref, account, (label or "")[:200], (excerpt or "")[:EXCERPT], at or now_iso()))
    return True


def _waiting_match(task: dict, msgs: list[dict]) -> str | None:
    """'address' when a message comes from an address named in waiting_on (proof enough to flip),
    'name' when only the display name matches (spoofable: flagged for Review, never flipped), else None."""
    w = str(task.get("waiting_on") or "")
    emails = {e.lower() for e in EMAIL_RE.findall(w)}
    name = re.split(r"[(\u2014\u2013,;:-]", w, 1)[0].strip()
    tokens = [t for t in re.findall(r"[A-Za-z]{3,}", name) if t.lower() not in ("the", "and", "for", "from")]
    found = None
    for m in msgs:
        addr, disp = _addr(m)
        if addr and addr in emails:
            return "address"
        if tokens and all(t.lower() in (disp + " " + addr).lower() for t in tokens):
            found = "name"
    return found


def sweep(conn: sqlite3.Connection, cfg: dict, client, now_local: datetime) -> str | None:
    st = get_settings(conn)
    lookback = int(C.get(cfg, "sources.mail.lookback_days", 3))
    limit = int(C.get(cfg, "sources.mail.max_per_sweep", 60))
    allow, deny = C.get(cfg, "sources.mail.allow", ["*"]), C.get(cfg, "sources.mail.deny", [])
    monitored = [a for a in rows(conn, "SELECT * FROM accounts WHERE enabled = 1") if "monitor" in json.loads(a.get("roles_json") or "[]")]
    mb_accounts = {str(a.get("email") or "").lower(): a for a in client.call("list_accounts", {})}
    open_tasks = rows(conn, "SELECT * FROM tasks WHERE status = 'open'")
    waiting = [t for t in open_tasks if t.get("waiting_on")]
    owed = [(t, {e.lower() for e in EMAIL_RE.findall(str(t.get("counterpart") or ""))}) for t in open_tasks if t.get("counterpart")]
    owed = [(t, es) for t, es in owed if es]
    links = rows(conn, "SELECT l.*, t.status FROM links l JOIN tasks t ON t.id = l.task_id WHERE l.kind = 'mail_thread' AND t.status = 'open'")
    changes: list[dict] = []
    flipped = closed = threads = candidates = 0
    notes: list[str] = []
    fetched: dict[str, dict] = {}

    def conversation(cid: str) -> dict | None:
        if cid not in fetched:
            try:
                fetched[cid] = client.call("get_conversation", {"conversationId": cid})
            except Exception as e:   # a vanished thread is not a failed sweep
                notes.append(f"{cid[:8]}: {type(e).__name__}")
                fetched[cid] = None
        return fetched[cid]

    for acc in monitored:
        address = str(acc["address"]).lower()
        mb = mb_accounts.get(address)
        if not mb:
            notes.append(f"{address} not in Mailbird")
            continue
        folders = {str(f.get("name") or "").lower(): f["id"] for f in client.call("list_folders", {"accountId": mb["id"]})}
        key = f"state.mail_sweep.last_at.{address}"
        wm = str(st.get(key) or _utc_str(now_local - timedelta(days=lookback), cfg))
        newest = wm
        for folder in ("inbox", "sent"):
            fid = folders.get(folder)
            if fid is None:
                continue
            convs = [c for c in client.call("list_conversations", {"folderId": fid, "limit": limit}) if str(c.get("receivedAtUtc") or "") > wm]
            for c in convs:
                cid, when, subject = c["conversationId"], str(c.get("receivedAtUtc") or ""), str(c.get("subject") or "")
                newest = max(newest, when)
                conv = conversation(cid)
                msgs = (conv or {}).get("messages") or []
                if not msgs:
                    continue
                last = msgs[-1]
                if folder == "sent":
                    # only what this account itself sent counts, each message on its own clock
                    mine = [m for m in msgs if _addr(m)[0] == address]
                    for t, emails in owed:
                        hits = [m for m in mine if emails & _recipients(m) and str(m.get("receivedAtUtc") or "")[:19] > str(t.get("created_at") or "")[:19]]
                        if hits:
                            add_link(conn, t["id"], "mail_thread", cid, label=subject[:120], account=address)
                            conn.execute("UPDATE links SET last_seen_at = ? WHERE task_id = ? AND kind = 'mail_thread' AND ref = ?", (when, t["id"], cid))
                            if _evidence(conn, t["id"], "sent_to_counterpart", cid, address, subject, c.get("lastSender"), when):
                                if REPLY_VERB_RE.match(t["name"] or ""):
                                    changes.append({"task_id": t["id"], "op": "done", "fields": {}}); closed += 1
                                else:
                                    candidates += 1          # sent to the person, but is the deliverable done? the planner judges
                    continue
                addr, disp = _addr(last)
                header = f"{disp} <{addr}>"
                if not _glob_any(allow, addr, header) or _glob_any(deny, addr, header):
                    continue
                matched = False
                for t in waiting:
                    how = _waiting_match(t, msgs)
                    if how:
                        matched = True
                        add_link(conn, t["id"], "mail_thread", cid, label=subject[:120], account=address)
                        conn.execute("UPDATE links SET last_seen_at = ? WHERE task_id = ? AND kind = 'mail_thread' AND ref = ?", (when, t["id"], cid))
                        if how == "address":
                            if _evidence(conn, t["id"], "reply_from_waiting", cid, address, subject, c.get("lastSender"), when):
                                changes.append({"task_id": t["id"], "op": "set", "fields": {"waiting_on": None}}); flipped += 1
                        elif _evidence(conn, t["id"], "maybe_reply_from_waiting", cid, address, subject, c.get("lastSender"), when):
                            candidates += 1
                if matched:
                    continue
                kind = "ics" if ICS_RE.search(subject) else "confirmation" if CONFIRM_RE.search(subject) else None
                if not kind:
                    continue
                try:
                    m = client.call("get_message", {"messageId": int(c.get("lastMessageId") or last.get("id"))})
                except Exception as e:
                    notes.append(f"message for {cid[:8]}: {type(e).__name__}")
                    continue
                if kind == "ics" and not any(str(a.get("name") or "").lower().endswith(".ics") for a in (m.get("attachments") or [])):
                    continue
                body = re.sub(r"\s+", " ", str(m.get("body") or m.get("bodyPreview") or "")).strip()
                if _evidence(conn, None, kind, cid, address, subject, f"from {header}: {body}", when):
                    candidates += 1
        if newest != wm:
            set_setting(conn, key, newest)
    for link in links:
        conv = conversation(link["ref"])
        msgs = (conv or {}).get("messages") or []
        if not msgs:
            continue
        last_at = max(str(m.get("receivedAtUtc") or "") for m in msgs)
        if last_at > str(link.get("last_seen_at") or ""):
            conn.execute("UPDATE links SET last_seen_at = ? WHERE id = ?", (last_at, link["id"]))
            if _evidence(conn, link["task_id"], "thread_updated", link["ref"], link.get("account"), (conv or {}).get("subject") or link.get("label"), (conv or {}).get("lastSender"), last_at):
                threads += 1
    if changes:
        batches.apply(conn, changes, "mail", now_local.date(), closed_by="mail-evidence")
    conn.commit()
    if not (flipped or closed or threads or candidates):
        return None                                           # notes alone (an account Mailbird lacks) are not activity; mail-check shows them
    return f"flipped {flipped}, closed {closed}, threads {threads}, candidates {candidates}" + (f" · {'; '.join(notes[:4])}" if notes else "")


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
