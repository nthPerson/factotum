"""Batches: the one write path. UI, commands, review, Chat proposals and automations all call
apply(). Every batch records old values and can be undone once (SPEC.md §2, §4)."""
from __future__ import annotations

import json
import sqlite3
from datetime import date

from .db import get_task, now_iso

# Fields a change may set directly. Everything else is derived or owned by an automation.
SETTABLE = {
    "name", "project", "area", "due", "due_is_datetime", "hard", "effort", "context", "counterpart",
    "waiting_on", "manual_priority", "snoozed_until", "top_rank", "longterm", "legacy_status",
    "last_reviewed_at", "why_now", "calendar_event",
}
OPS = {"set", "done", "drop", "reopen", "pin_top", "unpin", "keep", "longterm", "fade", "lapse"}


def _next_top_rank(conn: sqlite3.Connection) -> int:
    r = conn.execute("SELECT COALESCE(MAX(top_rank), 0) FROM tasks WHERE status = 'open'").fetchone()[0]
    return int(r) + 1


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
    for ch in changes:
        task = get_task(conn, int(ch["task_id"]))
        if not task:
            skipped.append({"task_id": ch.get("task_id"), "why": "no such task"})
            continue
        try:
            fields = _resolve(conn, ch, today, closed_by)
        except ValueError as e:
            skipped.append({"task_id": task["id"], "why": str(e)})
            continue
        diffs = {k: v for k, v in fields.items() if task.get(k) != v}
        if not diffs:
            continue
        for k, v in diffs.items():
            cur.execute("INSERT INTO changes (batch_id, task_id, field, old, new) VALUES (?, ?, ?, ?, ?)",
                        (batch_id, task["id"], k, json.dumps(task.get(k)), json.dumps(v)))
            undo.append({"task_id": task["id"], "field": k, "old": task.get(k)})
        sets = ", ".join(f"{k} = ?" for k in diffs) + ", edited_at = ?"
        cur.execute(f"UPDATE tasks SET {sets} WHERE id = ?", tuple(diffs.values()) + (now_iso(), task["id"]))
        applied += 1
    cur.execute("UPDATE batches SET undo_json = ? WHERE id = ?", (json.dumps(undo), batch_id))
    conn.commit()
    return {"batch_id": batch_id, "applied": applied, "skipped": skipped}


def undo(conn: sqlite3.Connection, batch_id: int) -> dict:
    b = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
    if not b:
        raise KeyError("no such batch")
    if b["undone_at"]:
        return {"batch_id": batch_id, "restored": 0, "already": True}
    restored = 0
    for u in json.loads(b["undo_json"] or "[]"):
        conn.execute(f"UPDATE tasks SET {u['field']} = ?, edited_at = ? WHERE id = ?", (u["old"], now_iso(), u["task_id"]))
        restored += 1
    conn.execute("UPDATE batches SET undone_at = ? WHERE id = ?", (now_iso(), batch_id))
    conn.commit()
    return {"batch_id": batch_id, "restored": restored, "already": False}
