"""Batches: the one write path. UI, commands, review, Chat proposals and automations all call
apply(). Every batch records old values and can be undone once (SPEC.md §2, §4)."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime

from . import relations as R
from .db import get_task, now_iso, upsert_task

# Fields a change may set directly. Everything else is derived or owned by an automation.
SETTABLE = {
    "name", "project", "area", "due", "due_is_datetime", "hard", "effort", "context", "counterpart",
    "waiting_on", "manual_priority", "snoozed_until", "top_rank", "longterm", "legacy_status",
    "last_reviewed_at", "why_now", "calendar_event",
}
OPS = {"set", "done", "drop", "reopen", "pin_top", "unpin", "keep", "longterm", "fade", "lapse", "add_reminder", "judge_evidence",
       "create", "watch_sent", "unwatch_sent", "link", "unlink", "add_note"}
# What a `create` may carry; score, priority, status and mentions stay derived.
CREATABLE = {"name", "project", "area", "due", "due_is_datetime", "hard", "effort", "context", "counterpart", "waiting_on", "manual_priority", "longterm", "snoozed_until"}
CREATED_SOURCE = "Claude"          # the Source of a task a connector or skill created
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
REPEATS = {"daily", "weekdays", "weekly", "monthly"}
VERDICTS = ("applied", "dismissed", "reviewed", "staged")
UNKNOWN_UNDO = "an undo entry Factotum does not recognise was left alone"


class NoSuchBatch(KeyError):
    """undo() of a batch id that does not exist (the API answers 404)."""


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


def _norm_text(v) -> str:
    return " ".join(str(v or "").split())


def _same_value(cur_v, want) -> bool:
    """Undo's comparison: strings whitespace-normalised, None equal to "", anything else by ==."""
    if isinstance(cur_v, str) or isinstance(want, str):
        return _norm_text(cur_v) == _norm_text(want)
    return cur_v == want


def apply(conn: sqlite3.Connection, changes: list[dict], source: str, today: date | None = None, closed_by: str | None = None, cause: dict | None = None) -> dict:
    """Apply a list of changes atomically; `cause` (what set the batch off) is stored as cause_json. Returns {"batch_id", "applied": n, "skipped": [...],
    "created": [task ids a `create` made], "refs": {ref: task id}}."""
    today = today or date.today()
    closed_by = closed_by or source
    cur = conn.cursor()
    cur.execute("INSERT INTO batches (created_at, applied_at, source, cause_json) VALUES (?, ?, ?, ?)",
                (now_iso(), now_iso(), source, json.dumps(cause) if cause else None))
    batch_id = cur.lastrowid
    undo: list[dict] = []
    applied = 0
    skipped: list[dict] = []
    created: list[int] = []
    refs: dict[str, int] = {}          # a create's `ref` → its new id, for links later in the same batch
    try:
        for ch in changes:
            op = ch.get("op") if isinstance(ch, dict) else None
            before = cur.execute("SELECT COALESCE(MAX(id), 0) FROM changes").fetchone()[0]
            if op == "create":
                applied += _create(conn, cur, ch, batch_id, source, today, undo, skipped, created)
            elif op in ("link", "unlink"):
                applied += _link_op(conn, cur, ch, batch_id, source, undo, skipped, refs)
            elif _apply_one(conn, cur, ch, batch_id, source, today, closed_by, undo, skipped):
                applied += 1
            note = str(ch.get("note") or "").strip()[:300] if isinstance(ch, dict) else ""
            if note:
                cur.execute("UPDATE changes SET note = ? WHERE batch_id = ? AND id > ?", (note, batch_id, before))
    except Exception:
        conn.rollback()   # one shared connection serves every request; never leave it mid-transaction
        raise
    cur.execute("UPDATE batches SET undo_json = ? WHERE id = ?", (json.dumps(undo), batch_id))
    conn.commit()
    return {"batch_id": batch_id, "applied": applied, "skipped": skipped, "created": created, "refs": refs}


def _link_end(v, refs: dict) -> tuple[int | None, str | None]:
    """One end of a link as a task id: an int (never a bool: True is Task-1), a digit string, or the
    ref of a task created earlier in this batch. Returns (id, None) or (None, why or None)."""
    if type(v) is int:
        return v, None
    if isinstance(v, str) and v.strip():
        s = v.strip()
        if s.isascii() and s.isdigit():
            return int(s), None
        if s in refs:
            return refs[s], None
        return None, f"no task made in this batch has ref {v!r}"
    return None, None


def _link_op(conn, cur, ch: dict, batch_id: int, source: str, undo: list, skipped: list, refs: dict) -> bool:
    """`link` / `unlink` one `part_of` or `waits_on` relation, the rules checked on the batch's own
    cursor so links made earlier in the batch count. An unchanged link is a no-op, as an unchanged
    `set` is; anything refused is skipped with a reason."""
    f = ch.get("fields") if isinstance(ch.get("fields"), dict) else {}
    kind, other = f.get("kind"), f.get("other")
    if not kind or other is None:
        skipped.append({"task_id": ch.get("task_id"), "why": "link needs fields.kind and fields.other"})
        return False
    if kind not in R.LINK_KINDS:
        skipped.append({"task_id": ch.get("task_id"), "why": f"kind must be part_of or waits_on, not {kind!r}"})
        return False
    a, why = _link_end(ch.get("task_id"), refs)
    if a is None:
        skipped.append({"task_id": ch.get("task_id"), "why": why or "missing or invalid task_id"})
        return False
    b, why = _link_end(other, refs)
    if b is None:
        skipped.append({"task_id": a, "why": why or "link needs fields.kind and fields.other"})
        return False
    rel = R.find(cur, a, b, kind)
    if ch["op"] == "link":
        if rel:
            return False
        reason = R.check(cur, a, b, kind)
        if reason:
            skipped.append({"task_id": a, "why": reason})
            return False
        rid = R.add(cur, a, b, kind, f.get("why"), source, batch_id)
        rel = dict(cur.execute("SELECT * FROM relations WHERE id = ?", (rid,)).fetchone())
        _link_changes(cur, batch_id, rel, added=True)
        undo.append({"relation_id": rid, "undo": "remove"})
        return True
    if not rel:
        skipped.append({"task_id": a, "why": f"Task-{a} has no {R.WORDS[kind]} link to Task-{b}"})
        return False
    R.remove(cur, rel["id"], batch_id)
    _link_changes(cur, batch_id, rel, added=False)
    undo.append({"relation_id": rel["id"], "undo": "restore"})
    return True


def _link_changes(cur, batch_id: int, rel: dict, added: bool) -> None:
    """Two `link` changes rows, one per task, each naming the other task (History and the Log read
    changes by task id); `new` when the link was added, `old` when it was removed."""
    names = {r["id"]: r["name"] for r in cur.execute("SELECT id, name FROM tasks WHERE id IN (?, ?)", (rel["src"], rel["dst"])).fetchall()}
    for tid, other in ((rel["src"], rel["dst"]), (rel["dst"], rel["src"])):
        v = json.dumps(R.change_value(rel, tid, names.get(other)))
        cur.execute("INSERT INTO changes (batch_id, task_id, field, old, new) VALUES (?, ?, 'link', ?, ?)",
                    (batch_id, tid, None if added else v, v if added else None))
    cur.execute("UPDATE tasks SET edited_at = ? WHERE id IN (?, ?)", (now_iso(), rel["src"], rel["dst"]))


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
        if ch.get("op") == "add_note":                       # appended here, so it lands after whatever the text is by now
            text = str((ch.get("fields") or {}).get("text") or "").strip()[:1000]
            if not text:
                raise ValueError("a note needs fields.text")
            old = task.get("context") or ""
            fields = {"context": (old + "\n" if old else "") + f"{today.isoformat()}: {text}"}
        elif ch.get("op") == "watch_sent":                     # the watch columns are written by these two ops only
            fields = watch_fields(task, (ch.get("fields") or {}).get("to"))
        elif ch.get("op") == "unwatch_sent":
            fields = {"close_on_sent_since": None, "close_on_sent_to": None}
        else:
            fields = _resolve(conn, ch, today, closed_by)
    except ValueError as e:
        skipped.append({"task_id": task["id"], "why": str(e)})
        return False
    if "due" in fields and not fields["due"]:
        # Removing the date: None (never ""), and what only means something beside a date goes with
        # it, so an undated row is not left "hard" or flagged as carrying a time.
        fields["due"] = None
        fields["hard"] = fields["due_is_datetime"] = 0      # even beside an explicit hard: true
    expect = ch.get("expect")
    if isinstance(expect, dict):                             # a staged edit carries the text it started from
        for k, want in expect.items():
            if k in fields and _norm_text(task.get(k)) != _norm_text(want):
                skipped.append({"task_id": task["id"], "why": f"Task-{task['id']} {k} changed since this edit was staged; not applied"})
                return False
    return _write_fields(cur, task, fields, batch_id, undo)


def _write_fields(cur, task: dict, fields: dict, batch_id: int, undo: list) -> bool:
    """Diff `fields` against the task, write what differs: one changes row and one undo entry (with
    the value written, so undo can tell a later edit) per field, edited_at bumped. False when nothing differs."""
    diffs = {k: v for k, v in fields.items() if task.get(k) != v}
    if not diffs:
        return False
    for k, v in diffs.items():
        cur.execute("INSERT INTO changes (batch_id, task_id, field, old, new) VALUES (?, ?, ?, ?, ?)",
                    (batch_id, task["id"], k, json.dumps(task.get(k)), json.dumps(v)))
        undo.append({"task_id": task["id"], "field": k, "old": task.get(k), "new": v})
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
    """Undo a batch once. An entry that can no longer be undone safely (a link restore that would
    duplicate or loop, an entry it does not know) is skipped and reported, never raised. Any error
    rolls back: the API shares one connection, and an open transaction holds the write lock."""
    b = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
    if not b:
        raise NoSuchBatch(batch_id)
    if b["undone_at"]:
        return {"batch_id": batch_id, "restored": 0, "skipped": [], "already": True}
    restored = 0
    skipped: list[dict] = []
    try:
        # Newest first: a batch may set one field twice (an accepted suggestion, then the decision's
        # note), and each entry's `old` is the value just before it; forwards, the middle value would win.
        for u in reversed(json.loads(b["undo_json"] or "[]")):
            if not isinstance(u, dict):
                skipped.append({"task_id": None, "why": UNKNOWN_UNDO})
                continue
            if "relation_id" in u:
                skip = _undo_relation(conn, u)
                if skip:
                    skipped.append(skip)
                    continue
            elif "reminder_id" in u:
                conn.execute("UPDATE reminders SET status = 'cancelled' WHERE id = ?", (u["reminder_id"],))
            elif "evidence_id" in u:
                conn.execute("UPDATE evidence SET judged_at = NULL, verdict = NULL WHERE id = ?", (u["evidence_id"],))
            elif "created_task_id" in u:                         # a created task is dropped, not deleted — and left alone once a later decision closed it
                t = conn.execute("SELECT status FROM tasks WHERE id = ?", (u["created_task_id"],)).fetchone()
                if t and t["status"] != "open":
                    skipped.append({"task_id": u["created_task_id"], "why": f"Task-{u['created_task_id']} was closed after it was created; left as it is"})
                    continue
                conn.execute("UPDATE tasks SET status = 'dropped', completed_at = ?, closed_by = 'undo', top_rank = NULL, edited_at = ? WHERE id = ? AND status = 'open'",
                             (date.today().isoformat(), now_iso(), u["created_task_id"]))
            elif "field" in u:
                if "new" in u:                                   # entries from before this check carry no `new` and are restored unchecked
                    now_v = conn.execute(f"SELECT {u['field']} FROM tasks WHERE id = ?", (u["task_id"],)).fetchone()
                    if now_v is not None and not _same_value(now_v[0], u["new"]):
                        skipped.append({"task_id": u["task_id"], "why": f"Task-{u['task_id']} {u['field']} was changed after this batch; left as it is"})
                        continue
                conn.execute(f"UPDATE tasks SET {u['field']} = ?, edited_at = ? WHERE id = ?", (u["old"], now_iso(), u["task_id"]))
            else:
                skipped.append({"task_id": u.get("task_id"), "why": UNKNOWN_UNDO})
                continue
            restored += 1
        conn.execute("UPDATE batches SET undone_at = ? WHERE id = ?", (now_iso(), batch_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"batch_id": batch_id, "restored": restored, "skipped": skipped, "already": False}


def _undo_relation(conn: sqlite3.Connection, u: dict) -> dict | None:
    """Undo one link entry: remove what the batch added, or restore what it removed unless that
    would now duplicate an active link or break a rule (a loop, a second goal). Returns the skip
    entry, or None when it was undone."""
    r = conn.execute("SELECT * FROM relations WHERE id = ?", (u["relation_id"],)).fetchone()
    if not r or u.get("undo") not in ("remove", "restore"):
        return {"task_id": u.get("task_id"), "why": UNKNOWN_UNDO}
    rel = dict(r)
    what = f"the link Task-{rel['src']} {R.WORDS.get(rel['kind'], rel['kind'])} Task-{rel['dst']}"
    if u["undo"] == "remove":
        if rel["removed_at"]:
            return {"task_id": rel["src"], "why": f"{what} was already removed"}
        R.remove(conn, rel["id"], None)
    else:
        if R.find(conn, rel["src"], rel["dst"], rel["kind"]):
            return {"task_id": rel["src"], "why": f"{what} was not restored: an identical link exists"}
        reason = R.check(conn, rel["src"], rel["dst"], rel["kind"])
        if reason:
            return {"task_id": rel["src"], "why": f"{what} was not restored: {reason}"}
        R.restore(conn, rel["id"])
    conn.execute("UPDATE tasks SET edited_at = ? WHERE id IN (?, ?)", (now_iso(), rel["src"], rel["dst"]))
    return None
