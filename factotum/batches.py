"""Batches: the one write path. UI, commands, review, Chat proposals and automations all call
apply(). Every batch records old values and can be undone once (SPEC.md §2, §4)."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime

from .db import get_task, now_iso, upsert_task

# Fields a change may set directly. Everything else is derived or owned by an automation.
SETTABLE = {
    "name", "project", "area", "due", "due_is_datetime", "hard", "effort", "context", "counterpart",
    "waiting_on", "manual_priority", "snoozed_until", "top_rank", "longterm", "legacy_status",
    "last_reviewed_at", "why_now", "calendar_event",
}
OPS = {"set", "done", "drop", "reopen", "pin_top", "unpin", "keep", "longterm", "fade", "lapse", "add_reminder", "judge_evidence",
       "create", "watch_sent", "unwatch_sent"}
# What a `create` may carry; score, priority, status and mentions stay derived.
CREATABLE = {"name", "project", "area", "due", "due_is_datetime", "hard", "effort", "context", "counterpart", "waiting_on", "manual_priority", "longterm", "snoozed_until"}
CREATED_SOURCE = "Claude"          # the Source of a task a connector or skill created
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
REPEATS = {"daily", "weekdays", "weekly", "monthly"}
VERDICTS = ("applied", "dismissed", "reviewed", "staged")


def _next_top_rank(conn: sqlite3.Connection) -> int:
    r = conn.execute("SELECT COALESCE(MAX(top_rank), 0) FROM tasks WHERE status = 'open'").fetchone()[0]
    return int(r) + 1


def _reminder_fields(change: dict) -> dict:
    """Validate an add_reminder change → {fire_at, label, repeat}. Raises ValueError."""
    f = change.get("fields") or {}
    fire_at = f.get("fire_at")
    if not fire_at:
        raise ValueError("a reminder needs fire_at")
    try:
        datetime.fromisoformat(str(fire_at))
    except ValueError:
        raise ValueError(f"fire_at {fire_at!r} is not an ISO date-time")
    repeat = f.get("repeat") or None
    if repeat is not None and repeat not in REPEATS:
        raise ValueError(f"repeat must be one of {sorted(REPEATS)}, not {repeat!r}")
    return {"fire_at": str(fire_at), "label": (f.get("label") or None), "repeat": repeat}


def watch_fields(task: dict, to) -> dict:
    """`watch_sent`: the task closes when mail to an address is sent from now on. The mail sweep
    reads the two columns this returns; they are set by this op only (not SETTABLE), and the watched
    address never touches `counterpart`, so watching does not make anyone a contact. With `to`, that
    address alone; without, the addresses already in the task's counterpart. Raises ValueError."""
    if to:
        addr = str(to).strip().lower()
        if not EMAIL_RE.fullmatch(addr):
            raise ValueError(f"{to!r} is not an email address")
        return {"close_on_sent_since": now_iso(), "close_on_sent_to": addr}
    found = list(dict.fromkeys(e.lower() for e in EMAIL_RE.findall(str(task.get("counterpart") or ""))))
    if not found:
        raise ValueError("no address to watch: give `to`, or set the task's counterpart to an email address")
    return {"close_on_sent_since": now_iso(), "close_on_sent_to": "; ".join(found)}


def _add_reminder(cur, task_id: int, rf: dict, batch_id: int, source: str, undo: list) -> None:
    cur.execute("INSERT INTO reminders (task_id, fire_at, label, repeat, status, created_by, created_at) VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                (task_id, rf["fire_at"], rf["label"], rf["repeat"], source, now_iso()))
    rid = cur.lastrowid
    cur.execute("INSERT INTO changes (batch_id, task_id, field, old, new) VALUES (?, ?, 'reminder', NULL, ?)", (batch_id, task_id, json.dumps(dict(rf, id=rid))))
    undo.append({"task_id": task_id, "reminder_id": rid})


def _create(conn, cur, ch: dict, batch_id: int, source: str, today: date, undo: list, skipped: list, created: list) -> int:
    """A new open task (Source "Claude"), with its reminders and an optional sent-mail watch, as
    part of the batch: recorded as a `created` change, and undone by dropping the row (never
    deleting it: ids are forever and the mirror may already have a page). Returns what it wrote."""
    f = ch.get("fields")
    name = str(f.get("name") or "").strip() if isinstance(f, dict) else ""
    if not name:
        skipped.append({"task_id": None, "why": "a create needs fields with a name"})
        return 0
    vals = {k: v for k, v in f.items() if k in CREATABLE}
    vals["name"] = name[:200]
    if vals.get("due"):
        vals.setdefault("due_is_datetime", 1 if "T" in str(vals["due"]) else 0)
    if "hard" in vals:
        vals["hard"] = 1 if vals["hard"] else 0
    if "longterm" in vals:
        vals["longterm"] = 1 if vals["longterm"] else 0
    to = ch.get("watch_sent_to")
    if to:
        try:
            vals.update(watch_fields(vals, to))
        except ValueError as e:
            skipped.append({"task_id": None, "why": f"{name[:40]}: watch not set: {e}"})
    now = now_iso()
    tid = upsert_task(conn, dict(vals, status="open", source=CREATED_SOURCE, source_key=f"batch:{batch_id}:{len(created)}", hard=vals.get("hard", 0),
                                 mentions=1, last_mention_at=today.isoformat(), created_at=now, edited_at=now))
    # `new` keeps who the create named: an address a direct batch introduced is not a contact (mcp_server.known_recipients)
    cur.execute("INSERT INTO changes (batch_id, task_id, field, old, new) VALUES (?, ?, 'created', NULL, ?)",
                (batch_id, tid, json.dumps({"name": vals["name"], "counterpart": vals.get("counterpart"), "waiting_on": vals.get("waiting_on")})))
    undo.append({"created_task_id": tid})
    created.append(tid)
    wrote = 1
    for r in (ch.get("reminders") if isinstance(ch.get("reminders"), list) else []):
        try:
            _add_reminder(cur, tid, _reminder_fields({"fields": r if isinstance(r, dict) else {}}), batch_id, source, undo)
            wrote += 1
        except ValueError as e:
            skipped.append({"task_id": tid, "why": str(e)})
    return wrote


def _resolve(conn: sqlite3.Connection, change: dict, today: date, closed_by: str) -> dict:
    """Turn an op into concrete field values."""
    op = change.get("op", "set")
    fields = dict(change.get("fields") or {})
    if op not in OPS:
        raise ValueError(f"unknown op {op}")
    if op == "done":
        fields.update(status="done", completed_at=today.isoformat(), closed_by=closed_by, top_rank=None)
    elif op == "drop":
        fields.update(status="dropped", completed_at=today.isoformat(), closed_by=closed_by, top_rank=None)
    elif op in ("fade", "lapse"):
        fields.update(status="faded" if op == "fade" else "lapsed", completed_at=today.isoformat(), closed_by=closed_by, top_rank=None)
    elif op == "reopen":
        fields.update(status="open", completed_at=None, closed_by=None)
    elif op == "pin_top":
        fields.setdefault("top_rank", _next_top_rank(conn))
    elif op == "unpin":
        fields["top_rank"] = None
    elif op == "keep":
        fields["last_reviewed_at"] = today.isoformat()
    elif op == "longterm":
        fields["longterm"] = 1
    for k in list(fields):
        if k not in SETTABLE and k not in ("status", "completed_at", "closed_by"):
            raise ValueError(f"field {k} is not settable")
    return fields


def apply(conn: sqlite3.Connection, changes: list[dict], source: str, today: date | None = None, closed_by: str | None = None) -> dict:
    """Apply a list of changes atomically. Returns {"batch_id", "applied": n, "skipped": [...],
    "created": [task ids a `create` made]}."""
    today = today or date.today()
    closed_by = closed_by or source
    cur = conn.cursor()
    cur.execute("INSERT INTO batches (created_at, applied_at, source) VALUES (?, ?, ?)", (now_iso(), now_iso(), source))
    batch_id = cur.lastrowid
    undo: list[dict] = []
    applied = 0
    skipped: list[dict] = []
    created: list[int] = []
    try:
        for ch in changes:
            if isinstance(ch, dict) and ch.get("op") == "create":
                applied += _create(conn, cur, ch, batch_id, source, today, undo, skipped, created)
            elif _apply_one(conn, cur, ch, batch_id, source, today, closed_by, undo, skipped):
                applied += 1
    except Exception:
        conn.rollback()   # one shared connection serves every request; never leave it mid-transaction
        raise
    cur.execute("UPDATE batches SET undo_json = ? WHERE id = ?", (json.dumps(undo), batch_id))
    conn.commit()
    return {"batch_id": batch_id, "applied": applied, "skipped": skipped, "created": created}


def _apply_one(conn, cur, ch: dict, batch_id: int, source: str, today: date, closed_by: str, undo: list, skipped: list) -> bool:
    """Apply one change; returns True when it wrote something. Anything malformed is skipped with a
    reason (a tray saved by an older UI build may lack task_id), never raised."""
    if ch.get("op") == "judge_evidence":
        return _judge_evidence(conn, cur, ch, batch_id, undo, skipped)
    try:
        task_id = int(ch.get("task_id"))
    except (TypeError, ValueError):
        skipped.append({"task_id": ch.get("task_id"), "why": "missing or invalid task_id"})
        return False
    task = get_task(conn, task_id)
    if not task:
        skipped.append({"task_id": task_id, "why": "no such task"})
        return False
    if ch.get("op") == "add_reminder":
        # A reminder is a row of its own, not a task field, so it rides beside any decision on
        # the same task (Keep, Later, even Drop) and is cancelled by the batch's undo.
        try:
            rf = _reminder_fields(ch)
        except ValueError as e:
            skipped.append({"task_id": task["id"], "why": str(e)})
            return False
        _add_reminder(cur, task["id"], rf, batch_id, source, undo)
        return True
    try:
        if ch.get("op") == "watch_sent":                     # the watch columns are written by these two ops only
            fields = watch_fields(task, (ch.get("fields") or {}).get("to"))
        elif ch.get("op") == "unwatch_sent":
            fields = {"close_on_sent_since": None, "close_on_sent_to": None}
        else:
            fields = _resolve(conn, ch, today, closed_by)
    except ValueError as e:
        skipped.append({"task_id": task["id"], "why": str(e)})
        return False
    diffs = {k: v for k, v in fields.items() if task.get(k) != v}
    if not diffs:
        return False
    for k, v in diffs.items():
        cur.execute("INSERT INTO changes (batch_id, task_id, field, old, new) VALUES (?, ?, ?, ?, ?)",
                    (batch_id, task["id"], k, json.dumps(task.get(k)), json.dumps(v)))
        undo.append({"task_id": task["id"], "field": k, "old": task.get(k)})
    sets = ", ".join(f"{k} = ?" for k in diffs) + ", edited_at = ?"
    cur.execute(f"UPDATE tasks SET {sets} WHERE id = ?", tuple(diffs.values()) + (now_iso(), task["id"]))
    return True


def _judge_evidence(conn, cur, ch: dict, batch_id: int, undo: list, skipped: list) -> bool:
    """A verdict on a sweep's finding or a planner proposal (`evidence` row): dismissed, reviewed,
    applied or staged. Recorded as a change (task 0 when the evidence names no task) and reverted by
    the batch's undo, so a dismissal is never a bare judged_at mark. Judged evidence is left alone."""
    f = ch.get("fields") or {}
    try:
        eid = int(f.get("evidence_id"))
    except (TypeError, ValueError):
        skipped.append({"task_id": ch.get("task_id"), "why": "judge_evidence needs evidence_id"})
        return False
    verdict = str(f.get("verdict") or "")
    e = conn.execute("SELECT id, task_id, judged_at FROM evidence WHERE id = ?", (eid,)).fetchone()
    if not e:
        skipped.append({"task_id": ch.get("task_id"), "why": f"no such evidence {eid}"})
        return False
    if verdict not in VERDICTS:
        skipped.append({"task_id": ch.get("task_id"), "why": "verdict must be one of " + ", ".join(VERDICTS)})
        return False
    if e["judged_at"]:
        return False
    tid = int(e["task_id"] or 0)
    cur.execute("INSERT INTO changes (batch_id, task_id, field, old, new) VALUES (?, ?, 'evidence', ?, ?)",
                (batch_id, tid, json.dumps({"evidence_id": eid, "verdict": None}), json.dumps({"evidence_id": eid, "verdict": verdict})))
    cur.execute("UPDATE evidence SET judged_at = ?, verdict = ? WHERE id = ?", (now_iso(), verdict, eid))
    undo.append({"task_id": tid, "evidence_id": eid})
    return True


def undo(conn: sqlite3.Connection, batch_id: int) -> dict:
    b = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
    if not b:
        raise KeyError("no such batch")
    if b["undone_at"]:
        return {"batch_id": batch_id, "restored": 0, "already": True}
    restored = 0
    # Newest first: a batch may set one field twice (an accepted suggestion, then the decision's
    # note), and each entry's `old` is the value just before it; forwards, the middle value would win.
    for u in reversed(json.loads(b["undo_json"] or "[]")):
        if "reminder_id" in u:
            conn.execute("UPDATE reminders SET status = 'cancelled' WHERE id = ?", (u["reminder_id"],))
        elif "evidence_id" in u:
            conn.execute("UPDATE evidence SET judged_at = NULL, verdict = NULL WHERE id = ?", (u["evidence_id"],))
        elif "created_task_id" in u:                         # a created task is dropped, not deleted — and left alone once a later decision closed it
            conn.execute("UPDATE tasks SET status = 'dropped', completed_at = ?, closed_by = 'undo', top_rank = NULL, edited_at = ? WHERE id = ? AND status = 'open'",
                         (date.today().isoformat(), now_iso(), u["created_task_id"]))
        else:
            conn.execute(f"UPDATE tasks SET {u['field']} = ?, edited_at = ? WHERE id = ?", (u["old"], now_iso(), u["task_id"]))
        restored += 1
    conn.execute("UPDATE batches SET undone_at = ? WHERE id = ?", (now_iso(), batch_id))
    conn.commit()
    return {"batch_id": batch_id, "restored": restored, "already": False}
