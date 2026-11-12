"""Reminders (SPEC.md §10): firing due rows to the chosen providers (notify.provider: ntfy, the
app's own notifications, or both), scheduling the next occurrence of a repeating one, and the
three actions that come back through `/r/<token>`. One token serves both providers: their
buttons are the same links.

`fire_at` is naive local wall time in `owner.timezone`; `now_local` passed in here is the same.
Tokens are random, stored on the reminder row, single use for Done and Snooze (the row's status
says whether it was used, so a second tap gets a clear answer), valid 24 h from `sent_at`; Open
only navigates and never consumes the token.
"""
from __future__ import annotations

import calendar
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import batches, config as C, notify, relations
from .dates import parse_date
from .db import get_settings, now_iso, rows, set_setting

SNOOZE = timedelta(hours=1)
SAID = "done_said"              # a reminder whose Done was followed by its outcome: the form is used up (save_outcome)
STATUS_WORDS = {SAID: "done"}
TOKEN_TTL = timedelta(hours=24)


def local_now(cfg: dict) -> datetime:
    tz = ZoneInfo(str(C.get(cfg, "owner.timezone", "UTC")))
    return datetime.now(tz).replace(tzinfo=None, microsecond=0)


def to_local(fire_at: str) -> datetime:
    """Parse a stored fire_at. Naive strings are local already; an offset or Z is converted."""
    d = datetime.fromisoformat(fire_at.replace("Z", "+00:00"))
    if d.tzinfo is not None:
        d = d.astimezone().replace(tzinfo=None)
    return d


def next_occurrence(fire_at: str, repeat: str) -> str:
    d = to_local(fire_at)
    if repeat == "daily":
        n = d + timedelta(days=1)
    elif repeat == "weekdays":
        n = d + timedelta(days=1)
        while n.weekday() >= 5:
            n += timedelta(days=1)
    elif repeat == "weekly":
        n = d + timedelta(days=7)
    elif repeat == "monthly":
        y, m = (d.year + (d.month // 12), d.month % 12 + 1)
        n = d.replace(year=y, month=m, day=min(d.day, calendar.monthrange(y, m)[1]))
    else:
        raise ValueError(f"unknown repeat {repeat!r}")
    return n.strftime("%Y-%m-%dT%H:%M")


def due(conn: sqlite3.Connection, now_local: datetime) -> list[dict]:
    out = []
    for r in rows(conn, "SELECT r.*, t.name AS task_name, t.due AS task_due, t.status AS task_status FROM reminders r JOIN tasks t ON t.id = r.task_id WHERE r.status = 'pending' ORDER BY r.fire_at, r.id"):
        try:
            if to_local(r["fire_at"]) <= now_local:
                out.append(r)
        except ValueError:
            continue   # a malformed fire_at never fires; it shows in the task's details for repair
    rels = relations.annotate(conn, [{"id": r["task_id"]} for r in out])
    for r, t in zip(out, rels):
        r["rel"] = t["rel"]      # words only: a reminder fires whether or not its task waits
    return out


def _ids(ids: list[int], cap: int = 3) -> str:
    return "Task-" + ", Task-".join(str(i) for i in ids[:cap]) + (f" +{len(ids) - cap}" if len(ids) > cap else "")


def _body(r: dict) -> str:
    parts = [r.get("label") or "Reminder"]
    d = parse_date(r.get("task_due"))
    if d:
        parts.append(f"due {d.strftime('%b')} {d.day}")
    rel = r.get("rel") or {}
    if rel.get("waits_on_open"):
        parts.append(f"waits on {_ids(rel['waits_on_open'])}")
    if rel.get("frees_open"):
        parts.append(f"frees {_ids(rel['frees_open'])} when done")
    if r.get("task_status") and r["task_status"] != "open":
        parts.append(f"task is {r['task_status']}")
    return " · ".join(parts)


def notice(r: dict, token: str) -> dict:
    """The app's notice for one due reminder (app_push's shape): a tap opens the task; Done and Snooze
    are the same token links ntfy carries, and a task that is no longer open gets no buttons."""
    task_id = r["task_id"]
    actions = [{"action": "done", "title": "Done", "path": f"/r/{token}?a=done"},
               {"action": "snooze", "title": "Snooze 1 h", "path": f"/r/{token}?a=snooze"}] if r["task_status"] == "open" else []
    return {"title": r["task_name"], "body": _body(r), "path": f"/t/{task_id}", "tag": f"task-{task_id}",
            "actions": actions, "urgency": "high", "ttl": 86400, "data": {"task_id": task_id}}


def fire(conn: sqlite3.Connection, cfg: dict, now_local: datetime, push, device_push=None) -> list[dict]:
    """Send every due reminder to the chosen providers, mark it sent, queue the next occurrence of a
    repeating one. `push` is notify.publish or a stand-in with the same signature; `device_push` is
    app_push.send_all or one like it. A reminder is marked sent when at least one provider delivered;
    when none did and one raised, that error propagates and the row stays pending; when none was
    reached (ntfy off, no device) it stays pending and is not returned. Returns what was sent."""
    base = str(C.get(cfg, "server.public_url", "http://127.0.0.1:8750")).rstrip("/")
    chosen = notify.providers(cfg)
    sent = []
    for r in due(conn, now_local):
        token = secrets.token_urlsafe(24)
        actions = [{"label": "Snooze 1 h", "url": f"{base}/r/{token}?a=snooze"},
                   {"label": "Done", "url": f"{base}/r/{token}?a=done"},
                   {"label": "Open", "url": f"{base}/r/{token}?a=open"}]
        # No Click by default: on the phone a Click opens the browser the moment the notification is
        # tapped, so Snooze / Done are only reachable by going back to ntfy. Without it the tap shows
        # the message with its three buttons; "Open" is one of them.
        click = f"{base}/t/{r['task_id']}" if C.get(cfg, "notify.ntfy.tap_opens_app", False) else None
        sends = []
        if "ntfy" in chosen:
            sends.append(("ntfy", lambda: push(cfg, r["task_name"], _body(r), click=click, actions=actions, tags=["alarm_clock"])))
        if "app" in chosen and device_push is not None:
            sends.append(("app", lambda: device_push(conn, cfg, notice(r, token))))
        delivered, results, errors = notify.send_each(sends)
        if not delivered:
            continue                     # nothing reached anyone: leave it pending for when a provider can
        msg_id = results.get("ntfy")
        conn.execute("UPDATE reminders SET status = 'sent', sent_at = ?, token = ?, ntfy_seq = ? WHERE id = ?", (now_iso(), token, msg_id, r["id"]))
        if r.get("repeat"):
            conn.execute("INSERT INTO reminders (task_id, fire_at, label, repeat, status, created_by, created_at) VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                         (r["task_id"], next_occurrence(r["fire_at"], r["repeat"]), r.get("label"), r["repeat"], r.get("created_by") or "repeat", now_iso()))
        conn.commit()   # per reminder: a failure on the next push must not roll this one back into pending (it was delivered)
        sent.append({"reminder_id": r["id"], "task_id": r["task_id"], "msg_id": msg_id, "task_name": r["task_name"],
                     "errors": errors, "app": results.get("app")})
    return sent


def free_now(conn: sqlite3.Connection, cfg: dict, today, push, device_push=None) -> list[dict]:
    """"Task-N is free now": a hard-dated open task whose last open blocker was done today, once per
    (task, blocker, day). The first call on a database only remembers what is already true. A task
    whose blocker was dropped, faded or lapsed is never announced. Marked sent only when a provider
    delivered; returns what was sent."""
    base = str(C.get(cfg, "server.public_url", "http://127.0.0.1:8750")).rstrip("/")
    chosen = notify.providers(cfg)
    day = today.isoformat()
    cands = []
    for t in relations.annotate(conn, rows(conn, "SELECT * FROM tasks WHERE status = 'open' AND hard = 1 AND due IS NOT NULL AND due != '' ORDER BY id")):
        rel = t["rel"]
        if rel["waits_on_open"] or not rel["freed_by"] or str(rel["freed_at"] or "")[:10] != day:
            continue
        if not any(c["id"] == rel["freed_by"] and c["status"] == "done" for c in rel["waits_on_closed"]):
            continue
        cands.append((f"{t['id']}:{rel['freed_by']}:{day}", t, rel["freed_by"]))
    seen = get_settings(conn).get("state.free_now.sent")
    if seen is None:
        set_setting(conn, "state.free_now.sent", [k for k, _, _ in cands])
        conn.commit()
        return []
    seen = list(seen)
    sent = []
    for key, t, by in cands:
        if key in seen:
            continue
        title = f"Task-{t['id']} is free now"
        d = parse_date(t.get("due"))
        body = f"{t['name']} · Task-{by} is done" + (f" · due {d.strftime('%b')} {d.day}" if d else "")
        sends = []
        if "ntfy" in chosen:
            sends.append(("ntfy", lambda: push(cfg, title, body, click=f"{base}/t/{t['id']}", tags=["white_check_mark"])))
        if "app" in chosen and device_push is not None:
            sends.append(("app", lambda: device_push(conn, cfg, {"title": title, "body": body, "path": f"/t/{t['id']}", "tag": f"free-{t['id']}",
                                                               "actions": [], "urgency": "high", "ttl": 86400, "data": {"task_id": t["id"]}})))
        delivered, _results, errors = notify.send_each(sends)
        if not delivered:
            continue
        seen.append(key)
        set_setting(conn, "state.free_now.sent", seen[-200:])
        conn.commit()
        sent.append({"task_id": t["id"], "freed_by": by, "task_name": t["name"], "errors": errors})
    return sent


class TokenError(Exception):
    def __init__(self, status: int, why: str):
        super().__init__(why)
        self.status, self.why = status, why


def act(conn: sqlite3.Connection, cfg: dict, token: str, action: str, now_local: datetime | None = None) -> dict:
    """Apply a reminder action. Raises TokenError(404|410|400)."""
    r = conn.execute("SELECT r.*, t.name AS task_name FROM reminders r JOIN tasks t ON t.id = r.task_id WHERE r.token = ?", (token,)).fetchone()
    if not r:
        raise TokenError(404, "This link is unknown or was already used.")
    r = dict(r)
    if r["status"] != "sent" and action != "open":
        raise TokenError(410, f"This reminder was already acted on (it is {STATUS_WORDS.get(r['status'], r['status'])}).")
    sent_at = datetime.fromisoformat(r["sent_at"]) if r.get("sent_at") else None
    if sent_at is None or datetime.now(timezone.utc) - sent_at > TOKEN_TTL:
        raise TokenError(410, "This link has expired (reminder actions are good for 24 hours).")
    now_local = now_local or local_now(cfg)
    if action == "open":
        return {"action": "open", "task_id": r["task_id"], "task_name": r["task_name"]}
    # Done and Snooze are one batch each (source `ntfy`): the reminder that fired is marked in it, so an Undo of
    # the Done re-arms it, and a Snooze (a new reminder an hour on) is in the Log with its Undo.
    if action == "done":
        res = batches.apply(conn, [{"task_id": r["task_id"], "op": "done", "reminder_status": {"id": r["id"], "status": "done"}}], "ntfy")
        return {"action": "done", "task_id": r["task_id"], "task_name": r["task_name"], "batch_id": res["batch_id"]}
    if action == "snooze":
        at = (now_local + SNOOZE).strftime("%Y-%m-%dT%H:%M")
        res = batches.apply(conn, [{"task_id": r["task_id"], "op": "add_reminder", "fields": {"fire_at": at, "label": r.get("label")},
                                    "reminder_status": {"id": r["id"], "status": "snoozed"}}], "ntfy")
        return {"action": "snooze", "task_id": r["task_id"], "task_name": r["task_name"], "fire_at": at, "batch_id": res["batch_id"]}
    raise TokenError(400, f"unknown action {action!r}")


def hanging_on(conn: sqlite3.Connection, task_id: int) -> int:
    """How many distinct OPEN tasks hang on this one (design §8.5): those that wait on it, and the goal
    it is a step of. A task it only waits on, or a step of its own, does not hang on it; the page's
    `hangsOn` asks on the same two."""
    return conn.execute(
        "SELECT COUNT(DISTINCT t.id) FROM relations r JOIN tasks t ON t.id = CASE WHEN r.kind = 'waits_on' THEN r.src ELSE r.dst END "
        "WHERE r.removed_at IS NULL AND t.status = 'open' "
        "AND ((r.kind = 'waits_on' AND r.dst = ?) OR (r.kind = 'part_of' AND r.src = ?))", (task_id, task_id)).fetchone()[0]


def save_outcome(conn: sqlite3.Connection, cfg: dict, token: str, text: str) -> dict:
    """The one optional line after a reminder's Done: only for a token that closed its task from the link, and
    once: the save marks the reminder `done_said` in its batch, so a later POST with the same token writes
    nothing and queues nothing (`already`), and an Undo of that batch gives the form back. An empty line
    saves nothing and does not use the form up."""
    r = conn.execute("SELECT r.*, t.name AS task_name, t.status AS task_status FROM reminders r JOIN tasks t ON t.id = r.task_id WHERE r.token = ?", (token,)).fetchone()
    if not r:
        raise TokenError(404, "This link is unknown or was already used.")
    r = dict(r)
    if r["status"] == SAID:
        return {"saved": False, "already": True, "task_id": r["task_id"], "task_name": r["task_name"]}
    if r["status"] != "done":
        raise TokenError(409, "Only a task closed from this reminder takes its outcome here; open the task to add one.")
    sent_at = datetime.fromisoformat(r["sent_at"]) if r.get("sent_at") else None
    if sent_at is None or datetime.now(timezone.utc) - sent_at > TOKEN_TTL:
        raise TokenError(410, "This link has expired (reminder actions are good for 24 hours).")
    tid = r["task_id"]
    if r["task_status"] not in ("done", "dropped"):
        raise TokenError(409, f"Task-{tid} is open again; add its outcome when you close it.")
    text = (text or "").strip()[:300]
    if not text:
        return {"saved": False, "task_id": tid, "task_name": r["task_name"]}
    res = batches.apply(conn, [{"task_id": tid, "op": "set", "fields": {"outcome": text}, "reminder_status": {"id": r["id"], "status": SAID}}], "ntfy")
    return {"saved": True, "task_id": tid, "task_name": r["task_name"], "batch_id": res["batch_id"], "outcome": text}
