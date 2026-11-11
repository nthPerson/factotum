"""Links between tasks (design §5.1, §6): the rules a new link must pass and the row helpers. Every
function takes `db`, the batch's own connection or cursor, so links made earlier in the same batch
count. Writes go through `batches` only; nothing here commits."""
from __future__ import annotations

import sqlite3

from .db import now_iso

KINDS = ("part_of", "waits_on", "same_as")
LINK_KINDS = ("part_of", "waits_on")          # what `link` / `unlink` take; `same_as` is made by `merge` only
CLOSED = ("done", "dropped", "faded", "lapsed")
MAX_PER_KIND = 20
WHY_MAX = 160
WORDS = {"part_of": "part of", "waits_on": "waits on", "same_as": "same as"}
CYCLE_DEPTH = 50

Db = sqlite3.Connection | sqlite3.Cursor


def _one(db: Db, sql: str, params: tuple = ()) -> dict | None:
    r = db.execute(sql, params).fetchone()
    return dict(r) if r else None


def find(db: Db, src: int, dst: int, kind: str) -> dict | None:
    """The active row `src kind dst`, or None."""
    return _one(db, "SELECT * FROM relations WHERE src = ? AND dst = ? AND kind = ? AND removed_at IS NULL", (src, dst, kind))


def waits_cycle(db: Db, src: int, dst: int) -> bool:
    """True when `src` is reachable from `dst` over active `waits_on` rows, so `src waits_on dst`
    would close a loop. Bounded in depth, so a loop already in old data cannot run forever."""
    r = db.execute(
        "WITH RECURSIVE r(id, depth) AS (SELECT ?, 0 UNION "
        "SELECT rel.dst, r.depth + 1 FROM relations rel JOIN r ON rel.src = r.id "
        "WHERE rel.kind = 'waits_on' AND rel.removed_at IS NULL AND r.depth < ?) "
        "SELECT 1 FROM r WHERE id = ? LIMIT 1", (dst, CYCLE_DEPTH, src)).fetchone()
    return r is not None


def check(db: Db, src: int, dst: int, kind: str) -> str | None:
    """Why a NEW active link `src kind dst` is refused, or None. An identical active link is not an
    error here: callers `find` it first (that is a no-op, not a refusal)."""
    if kind not in KINDS:
        return f"kind must be one of part_of, waits_on, same_as, not {kind!r}"
    status = {}
    for t in (src, dst):
        row = _one(db, "SELECT status FROM tasks WHERE id = ?", (t,))
        if not row:
            return f"no Task-{t}"
        status[t] = row["status"]
    if src == dst:
        return "a task cannot be linked to itself"
    active = "removed_at IS NULL"
    if kind == "part_of":
        g = _one(db, f"SELECT dst FROM relations WHERE src = ? AND dst <> ? AND kind = 'part_of' AND {active} LIMIT 1", (src, dst))
        if g:
            return f"Task-{src} is already part of Task-{g['dst']}"
        g = _one(db, f"SELECT dst FROM relations WHERE src = ? AND kind = 'part_of' AND {active} LIMIT 1", (dst,))
        if g:
            return f"Task-{dst} is a step of Task-{g['dst']}; a step cannot have steps"
        if _one(db, f"SELECT 1 FROM relations WHERE dst = ? AND kind = 'part_of' AND {active} LIMIT 1", (src,)):
            return f"Task-{src} has steps of its own; a goal cannot be a step"
        if find(db, src, dst, "waits_on"):
            return f"Task-{src} waits on Task-{dst}; a task cannot wait on its own goal"
    elif kind == "waits_on":
        if status[dst] != "open":
            return f"Task-{dst} is already {status[dst]}"
        if find(db, src, dst, "part_of"):
            return f"Task-{dst} is the goal of Task-{src}; a task cannot wait on its own goal"
        if waits_cycle(db, src, dst):
            return f"Task-{dst} already waits on Task-{src}, directly or through other tasks; that would make a loop"
    for x in (src, dst):
        n = db.execute(f"SELECT COUNT(*) FROM relations WHERE kind = ? AND (src = ? OR dst = ?) AND {active}", (kind, x, x)).fetchone()[0]
        if n >= MAX_PER_KIND:
            return f"Task-{x} already has {MAX_PER_KIND} {WORDS[kind]} links"
    return None


def add(db: Db, src: int, dst: int, kind: str, why: str | None, source: str, batch_id: int | None) -> int:
    """Insert an active row; returns its id. `why` is one line, cut to WHY_MAX."""
    if why is not None:
        why = " ".join(str(why).split())[:WHY_MAX] or None
    cur = db.execute("INSERT INTO relations (src, dst, kind, why, source, batch_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (src, dst, kind, why, source, batch_id, now_iso()))
    return cur.lastrowid


def remove(db: Db, rel_id: int, batch_id: int | None) -> None:
    """Soft-remove: History and undo still need the row."""
    db.execute("UPDATE relations SET removed_at = ?, removed_batch_id = ? WHERE id = ?", (now_iso(), batch_id, rel_id))


def restore(db: Db, rel_id: int) -> None:
    db.execute("UPDATE relations SET removed_at = NULL, removed_batch_id = NULL WHERE id = ?", (rel_id,))


def default_survivor(db: Db, a: int, b: int) -> tuple[int, int]:
    """`(keep, goes)` for a merge of two rows: the Canvas row (an outside identity that closes
    itself); both or neither → the older (`created_at`, then the lower id)."""
    rows = {r["id"]: dict(r) for r in db.execute("SELECT id, source, created_at FROM tasks WHERE id IN (?, ?)", (a, b)).fetchall()}
    ra, rb = rows.get(a) or {"id": a}, rows.get(b) or {"id": b}
    ca, cb = ra.get("source") == "Canvas", rb.get("source") == "Canvas"
    if ca != cb:
        return (a, b) if ca else (b, a)
    first = min((ra, rb), key=lambda r: (r.get("created_at") or "", r["id"]))
    return (a, b) if first is ra else (b, a)


def change_value(rel: dict, task_id: int, other_name: str | None) -> dict:
    """What a `link` change row holds for one end of `rel`: it names the OTHER task."""
    out = task_id == rel["src"]
    return {"relation_id": rel["id"], "kind": rel["kind"], "dir": "out" if out else "in",
            "other": rel["dst"] if out else rel["src"], "other_name": other_name, "why": rel["why"]}
