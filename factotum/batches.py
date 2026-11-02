"""Batches: the one write path. UI, commands, review, Chat proposals and automations all call
apply(). Every batch records old values and can be undone once (SPEC.md §2, §4)."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime

from .db import get_task, now_iso

# Fields a change may set directly. Everything else is derived or owned by an automation.
SETTABLE = {
    "name", "project", "area", "due", "due_is_datetime", "hard", "effort", "context", "counterpart",
    "waiting_on", "manual_priority", "snoozed_until", "top_rank", "longterm", "legacy_status",
    "last_reviewed_at", "why_now", "calendar_event",
}
OPS = {"set", "done", "drop", "reopen", "pin_top", "unpin", "keep", "longterm", "fade", "lapse", "add_reminder", "judge_evidence"}
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
    """Apply a list of changes atomically. Returns {"batch_id", "applied": n, "skipped": [...]}."""
    today = today or date.today()
    closed_by = closed_by or source
    cur = conn.cursor()
    cur.execute("INSERT INTO batches (created_at, applied_at, source) VALUES (?, ?, ?)", (now_iso(), now_iso(), source))
    batch_id = cur.lastrowid
    undo: list[dict] = []
    applied = 0
    skipped: list[dict] = []
    try:
        for ch in changes:
            if _apply_one(conn, cur, ch, batch_id, source, today, closed_by, undo, skipped):
                applied += 1
    except Exception:
        conn.rollback()   # one shared connection serves every request; never leave it mid-transaction
        raise
    cur.execute("UPDATE batches SET undo_json = ? WHERE id = ?", (json.dumps(undo), batch_id))
    conn.commit()
    return {"batch_id": batch_id, "applied": applied, "skipped": skipped}


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
        cur.execute("INSERT INTO reminders (task_id, fire_at, label, repeat, status, created_by, created_at) VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                    (task["id"], rf["fire_at"], rf["label"], rf["repeat"], source, now_iso()))
        rid = cur.lastrowid
        cur.execute("INSERT INTO changes (batch_id, task_id, field, old, new) VALUES (?, ?, 'reminder', NULL, ?)", (batch_id, task["id"], json.dumps(dict(rf, id=rid))))
        undo.append({"task_id": task["id"], "reminder_id": rid})
        return True
    try:
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
        else:
            conn.execute(f"UPDATE tasks SET {u['field']} = ?, edited_at = ? WHERE id = ?", (u["old"], now_iso(), u["task_id"]))
        restored += 1
    conn.execute("UPDATE batches SET undone_at = ? WHERE id = ?", (now_iso(), batch_id))
    conn.commit()
    return {"batch_id": batch_id, "restored": restored, "already": False}
