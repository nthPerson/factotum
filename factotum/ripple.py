"""Ripples (design §8): when a linked task is closed, materially changed or merged, one light Claude
run with no tools brings the tasks linked to it up to date. Python decides when: `batches.apply`
queues each such task in `ripple_queue` (never for a ripple's own batch), the scheduler runs a group
after the quiet period or at once for an outcome. A ripple only rewrites a neighbour's text (guarded,
undoable with its cause); everything else it suggests waits on Review as a card a person accepts.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from typing import Callable

from . import batches
from . import config as C
from .db import now_iso

RUN_KIND = "ripple"                 # the runs row's kind, the batch source and the prompt's run kind
ACCOUNT = "ripple"                  # evidence.account of a card a ripple staged
PROMPT = C.ROOT / "prompts" / "ripple.md"
RUNS_DIR = C.ROOT / "data" / "runs" / "ripple"

WHAT_ORDER = ("closed", "merge", "name", "text", "due", "hard", "outcome")
CHANGED_CAP = 5                     # changed tasks per run; the rest stay queued
NEIGHBOUR_CAP = 12                  # linked tasks per run: open only, direct links first, then other steps of the same goal
TEXT_CAP = 1500                     # characters of each text in the prompt
REWRITE_CAP = 2000                  # characters of a rewrite
PROPOSAL_CAP = 6                    # cards per run
MAX_ATTEMPTS = 2                    # a group that fails this often is set aside and logged
MAX_AGE = timedelta(hours=24)       # a row queued longer ago than this is set aside unrun

_WAKER: Callable[[], None] | None = None


def _load(v):
    try:
        return json.loads(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _what(cur, batch_id: int) -> dict[int, set[str]]:
    """What this batch did that ripples, by task id, read from its own `changes` rows."""
    rows = cur.execute("SELECT task_id, field, old, new FROM changes WHERE batch_id = ? ORDER BY id", (batch_id,)).fetchall()
    merged = {r["task_id"] for r in rows if r["field"] == "closed_by" and _load(r["new"]) == "merge"}
    out: dict[int, set[str]] = {}
    for r in rows:
        f, old, new = r["field"], _load(r["old"]), _load(r["new"])
        if f == "status":
            tag = "closed" if old == "open" and new in ("done", "dropped") and r["task_id"] not in merged else None
        elif f in ("name", "due", "hard", "outcome"):
            tag = f
        elif f == "context":
            tag = "text" if batches._norm_text(old) != batches._norm_text(new) else None
        elif f == "link":
            tag = "merge" if old is None and isinstance(new, dict) and new.get("kind") == "same_as" and new.get("dir") == "in" else None
        else:
            tag = None
        if tag:
            out.setdefault(r["task_id"], set()).add(tag)
    return out


def _ordered(what) -> list[str]:
    return [w for w in WHAT_ORDER if w in what]


def enqueue(cur, batch_id: int, source: str) -> dict[int, list[str]]:
    """Queue every task this batch closed, renamed, re-texted, re-dated, gave an outcome or merged
    into, when it has at least one active link after the batch; one row per task, merged with what
    is already waiting. Runs on the batch's own cursor, inside its transaction. Returns task id →
    what for this batch; nothing for a ripple's own batch (a ripple never triggers a ripple)."""
    if source == RUN_KIND:
        return {}
    queued: dict[int, list[str]] = {}
    for tid, what in _what(cur, batch_id).items():
        if not cur.execute("SELECT 1 FROM relations WHERE (src = ? OR dst = ?) AND removed_at IS NULL LIMIT 1", (tid, tid)).fetchone():
            continue
        now = 1 if "outcome" in what else 0
        row = cur.execute("SELECT batches_json, what_json, now FROM ripple_queue WHERE task_id = ?", (tid,)).fetchone()
        if row:
            ids = (_load(row["batches_json"]) or []) + [batch_id]
            merged = _ordered(set(_load(row["what_json"]) or []) | what)
            cur.execute("UPDATE ripple_queue SET queued_at = ?, batches_json = ?, last_batch_id = ?, what_json = ?, now = ?, attempts = 0 WHERE task_id = ?",
                        (now_iso(), json.dumps(ids), batch_id, json.dumps(merged), max(row["now"], now), tid))
        else:
            cur.execute("INSERT INTO ripple_queue (task_id, queued_at, batches_json, last_batch_id, what_json, now) VALUES (?, ?, ?, ?, ?, ?)",
                        (tid, now_iso(), json.dumps([batch_id]), batch_id, json.dumps(_ordered(what)), now))
        queued[tid] = _ordered(what)
    return queued


def pending(conn: sqlite3.Connection) -> list[dict]:
    """The queued rows, `batches` and `what` parsed, oldest `queued_at` first."""
    out = []
    for r in conn.execute("SELECT * FROM ripple_queue ORDER BY queued_at, task_id").fetchall():
        d = dict(r)
        d["batches"] = _load(d.pop("batches_json")) or []
        d["what"] = _load(d.pop("what_json")) or []
        out.append(d)
    return out


def set_waker(fn: Callable[[], None] | None) -> None:
    """The scheduler's wake (set by `Scheduler.start`, cleared by `stop`)."""
    global _WAKER
    _WAKER = fn


def poke() -> None:
    """Wake the scheduler when one runs in this process; never raises (the batch is already written)."""
    fn = _WAKER
    if fn is None:
        return
    try:
        fn()
    except Exception:
        pass
