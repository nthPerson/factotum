"""Links between tasks (design §5.1, §6): the rules a new link must pass and the row helpers. Every
function takes `db`, the batch's own connection or cursor, so links made earlier in the same batch
count. Links are written through `batches` (the importer and migration 1 aside); a suggested link is
an `evidence` row (§5.4), never a link. Nothing here commits."""
from __future__ import annotations

import json
import sqlite3
from datetime import date

from . import scoring
from .db import now_iso

KINDS = ("part_of", "waits_on", "same_as")
LINK_KINDS = ("part_of", "waits_on")          # what `link` / `unlink` take; `same_as` is made by `merge` only
CLOSED = ("done", "dropped", "faded", "lapsed")
MAX_PER_KIND = 20
WHY_MAX = 160
WORDS = {"part_of": "part of", "waits_on": "waits on", "same_as": "same as"}
CYCLE_DEPTH = 50
SURVIVOR_HOPS = 5
NEEDED_BY_DEPTH = 5                           # `needed_by` follows `frees` edges this many hops (design §7.1)
# Suggested links (design §5.4): `evidence` rows, never `relations` rows. `any` is a pair Python found
# without knowing the kind; `same_as` and `any` name an unordered pair, the others a directed one.
SUGGEST_KINDS = ("same_as", "part_of", "waits_on", "any")
SYMMETRIC = ("same_as", "any")
EVIDENCE_KIND = "link_suggestion"
IMPORT_WHY = "duplicate source key in import"

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


def survivor(db: Db, task_id: int) -> int:
    """Where a merged duplicate went: follows active `same_as` rows from `src` to `dst`, up to
    SURVIVOR_HOPS, stopping on a repeat; `task_id` itself when it was never merged away (design §6
    "From then on…")."""
    seen, cur = {task_id}, task_id
    for _ in range(SURVIVOR_HOPS):
        r = db.execute("SELECT dst FROM relations WHERE src = ? AND kind = 'same_as' AND removed_at IS NULL ORDER BY id DESC LIMIT 1", (cur,)).fetchone()
        if not r or r[0] in seen:
            break
        cur = r[0]
        seen.add(cur)
    return cur


def _suggestion_rows(db: Db, a: int, b: int, judged: bool) -> list[dict]:
    """`link_suggestion` rows on either task (a row lives on its `a`), judged or not, with `ref` parsed."""
    out = []
    for r in db.execute(f"SELECT id, ref FROM evidence WHERE kind = ? AND task_id IN (?, ?) AND judged_at IS {'NOT ' if judged else ''}NULL ORDER BY id",
                        (EVIDENCE_KIND, a, b)).fetchall():
        try:
            ref = json.loads(r["ref"] or "")
        except (TypeError, ValueError):
            continue
        if isinstance(ref, dict):
            out.append(dict(ref, evidence_id=r["id"]))
    return out


def _same_pair(ref: dict, kind: str, a: int, b: int) -> bool:
    """`ref` is about this kind and pair (unordered when the kind is symmetric), or is an `any` row
    for the unordered pair, which stands for every kind."""
    pair = (ref.get("a"), ref.get("b"))
    if ref.get("kind") == "any" and set(pair) == {a, b}:
        return True
    return ref.get("kind") == kind and (pair == (a, b) or (kind in SYMMETRIC and pair == (b, a)))


def pair_judged(db: Db, kind: str, a: int, b: int) -> bool:
    """A judged suggestion for this kind and pair, or a judged `any` for the pair: never suggested again."""
    return any(_same_pair(r, kind, a, b) for r in _suggestion_rows(db, a, b, judged=True))


def suggest(db: Db, kind: str, a: int, b: int, why: str, signal: str) -> int | None:
    """Store a suggested link as `link_suggestion` evidence on `a`; returns its id, or None (nothing
    written) when the kind is unknown, the two are one task or a task is missing, the pair was judged,
    the same suggestion is already waiting, or a link of any kind already joins them. For `same_as`,
    `a` is the row that would go away and `b` the one kept. The caller commits."""
    if kind not in SUGGEST_KINDS or a == b:
        return None
    if db.execute("SELECT COUNT(*) FROM tasks WHERE id IN (?, ?)", (a, b)).fetchone()[0] < 2:
        return None
    if pair_judged(db, kind, a, b) or any(_same_pair(r, kind, a, b) for r in _suggestion_rows(db, a, b, judged=False)):
        return None
    if db.execute("SELECT 1 FROM relations WHERE ((src = ? AND dst = ?) OR (src = ? AND dst = ?)) AND removed_at IS NULL LIMIT 1", (a, b, b, a)).fetchone():
        return None
    why = str(why or "")
    ref = {"kind": kind, "a": a, "b": b, "why": why, "signal": signal}
    cur = db.execute("INSERT INTO evidence (task_id, kind, ref, label, excerpt, at) VALUES (?, ?, ?, ?, ?, ?)",
                     (a, EVIDENCE_KIND, json.dumps(ref), f"{WORDS.get(kind, 'linked to')} Task-{b}?", why[:300], now_iso()))
    return cur.lastrowid


def pending_suggestions(db: Db, ids: set[int] | None = None) -> dict[int, list[dict]]:
    """Unjudged suggestions by task id, listed under both ends (only the ids in `ids`, when given)."""
    out: dict[int, list[dict]] = {}
    for r in db.execute("SELECT id, ref FROM evidence WHERE kind = ? AND judged_at IS NULL ORDER BY id", (EVIDENCE_KIND,)).fetchall():
        try:
            ref = json.loads(r["ref"] or "")
            kind, a, b = ref["kind"], int(ref["a"]), int(ref["b"])
        except (TypeError, ValueError, KeyError):
            continue
        for tid, other in ((a, b), (b, a)):
            if ids is None or tid in ids:
                out.setdefault(tid, []).append({"evidence_id": r["id"], "kind": kind, "other": other, "keep": b if kind == "same_as" else None,
                                                "why": ref.get("why"), "signal": ref.get("signal")})
    return out


def import_duplicate(db: Db, loser: int, owner: int) -> str:
    """A row the importer found sharing another row's source key (design §5.3). A closed loser was a
    duplicate that is over: `same_as` the owner, source `import`. An open one becomes a merge card,
    never a link (a `same_as` between two open rows would send mentions away from an open task).
    Returns "same_as", "suggested" or "skipped" (a row is missing, or the two are one)."""
    status = {r["id"]: r["status"] for r in db.execute("SELECT id, status FROM tasks WHERE id IN (?, ?)", (loser, owner)).fetchall()}
    if loser == owner or len(status) < 2:
        return "skipped"
    if status[loser] != "open":
        if not find(db, loser, owner, "same_as"):
            add(db, loser, owner, "same_as", IMPORT_WHY, "import", None)
        return "same_as"
    keep, goes = default_survivor(db, loser, owner)
    suggest(db, "same_as", goes, keep, IMPORT_WHY, "import")
    return "suggested"


def migration_1(conn: sqlite3.Connection) -> None:
    """The importer's old `links` rows of kind `task` (`task_id` the loser, `ref` the owner's id)
    become `same_as` rows or merge suggestions, then are deleted; a non-numeric ref is just deleted."""
    for ln in conn.execute("SELECT task_id, ref FROM links WHERE kind = 'task' ORDER BY id").fetchall():
        ref = str(ln["ref"] or "").strip()
        if ref.isascii() and ref.isdigit():
            import_duplicate(conn, int(ln["task_id"]), int(ref))
    conn.execute("DELETE FROM links WHERE kind = 'task'")


def migration_1_needs_backup(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM links WHERE kind = 'task' LIMIT 1").fetchone() is not None


def change_value(rel: dict, task_id: int, other_name: str | None) -> dict:
    """What a `link` change row holds for one end of `rel`: it names the OTHER task."""
    out = task_id == rel["src"]
    return {"relation_id": rel["id"], "kind": rel["kind"], "dir": "out" if out else "in",
            "other": rel["dst"] if out else rel["src"], "other_name": other_name, "why": rel["why"]}


# --- derived facts (design §7.1): one step in front of scoring and the lifecycle -------------------

def _cause_from(cause_json: str | None) -> int | None:
    """The first id of a batch cause's `from`, or None."""
    try:
        cause = json.loads(cause_json or "")
    except (TypeError, ValueError):
        return None
    src = cause.get("from") if isinstance(cause, dict) else None
    first = src[0] if isinstance(src, list) and src else None
    return first if type(first) is int else None


def annotate(db: Db, tasks: list[dict]) -> list[dict]:
    """Put every neighbour fact under `t["rel"]` (the plan's "task["rel"]" section), in place; returns
    `tasks`. A dict needs only `id`. Reads the active links once, the rows they reach once, the ripple
    batches once and the pending suggestions once; the rest is Python. Never writes (the MCP server's
    connection is query_only)."""
    if not tasks:
        return tasks
    ids = sorted({int(t["id"]) for t in tasks})
    rels = [dict(r) for r in db.execute("SELECT id, src, dst, kind, created_at FROM relations WHERE removed_at IS NULL ORDER BY id")]
    info = {r["id"]: dict(r) for r in db.execute(
        "SELECT id, status, due, hard, completed_at, last_mention_at FROM tasks WHERE id IN "
        "(SELECT src FROM relations WHERE removed_at IS NULL UNION SELECT dst FROM relations WHERE removed_at IS NULL)")}
    touching: dict[int, list[dict]] = {}
    waits: dict[int, list[int]] = {}           # x waits on these
    frees: dict[int, list[int]] = {}           # these wait on x
    steps: dict[int, list[int]] = {}
    goal: dict[int, int] = {}
    merged: dict[int, str] = {}
    for r in rels:
        s, d = r["src"], r["dst"]
        touching.setdefault(s, []).append(r)
        touching.setdefault(d, []).append(r)
        if r["kind"] == "waits_on":
            waits.setdefault(s, []).append(d)
            frees.setdefault(d, []).append(s)
        elif r["kind"] == "part_of":
            steps.setdefault(d, []).append(s)
            goal.setdefault(s, d)
        elif r["kind"] == "same_as":
            merged[d] = max(merged.get(d) or "", r["created_at"] or "")
    is_open = lambda x: (info.get(x) or {}).get("status") == "open"                                 # noqa: E731
    hard_due = lambda x: str(info[x]["due"])[:10] if is_open(x) and info[x].get("hard") and info[x].get("due") else None  # noqa: E731
    activity = lambda x: tuple(str((info.get(x) or {}).get(k) or "")[:10] for k in ("last_mention_at", "completed_at"))  # noqa: E731  (a step's mention, close)

    def needed_by(tid: int) -> tuple | None:
        """(date, id, via): the earliest hard due date among the open tasks `tid` frees, breadth-first
        through open tasks up to NEEDED_BY_DEPTH hops, and its goal's; ties to the lower id."""
        cands, seen, frontier = [], {tid}, [tid]
        for hop in range(1, NEEDED_BY_DEPTH + 1):
            nxt = []
            for x in frontier:
                for y in frees.get(x, ()):
                    if y in seen or not is_open(y):
                        continue
                    seen.add(y)
                    nxt.append(y)
                    if hard_due(y):
                        cands.append((hard_due(y), y, "frees" if hop == 1 else "chain"))
            frontier = nxt
        g = goal.get(tid)
        if g is not None and hard_due(g):
            cands.append((hard_due(g), g, "goal"))
        return min(cands) if cands else None

    changed: dict[int, dict] = {}
    for r in db.execute(
            "SELECT c.task_id, b.id, b.applied_at, b.cause_json FROM changes c JOIN batches b ON b.id = c.batch_id JOIN tasks t ON t.id = c.task_id "
            "WHERE b.cause_json IS NOT NULL AND b.undone_at IS NULL AND c.task_id IN (SELECT value FROM json_each(?)) "
            "AND (t.neighbours_seen_at IS NULL OR b.applied_at > t.neighbours_seen_at) ORDER BY b.id", (json.dumps(ids),)):
        changed[r["task_id"]] = {"by": _cause_from(r["cause_json"]), "at": r["applied_at"], "batch_id": r["id"]}
    sugg = pending_suggestions(db, set(ids))

    for t in tasks:
        tid = int(t["id"])
        closed = [{"id": x, "status": info[x]["status"], "at": info[x]["completed_at"]}
                  for x in sorted(set(waits.get(tid, ()))) if x in info and not is_open(x)]
        rel = {"links": [{"id": r["id"], "kind": r["kind"], "dir": "out" if r["src"] == tid else "in", "other": r["dst"] if r["src"] == tid else r["src"]}
                         for r in touching.get(tid, ())],
               "waits_on_open": sorted({x for x in waits.get(tid, ()) if is_open(x)}), "waits_on_closed": closed,
               "frees_open": sorted({x for x in frees.get(tid, ()) if is_open(x)}),
               "goal_id": goal.get(tid), "steps_open": sum(1 for x in steps.get(tid, ()) if is_open(x)), "steps_total": len(steps.get(tid, ())),
               "needed_by": None, "needed_by_id": None, "needed_by_via": None, "freed_at": None, "freed_by": None,
               "neighbour_activity_at": max((d for x in steps.get(tid, ()) for d in activity(x) if d), default=None),
               "merged_at": merged.get(tid) or None, "changed": changed.get(tid), "suggestions": sugg.get(tid, [])}
        nb = needed_by(tid)
        if nb:
            rel["needed_by"], rel["needed_by_id"], rel["needed_by_via"] = nb
        if closed and not rel["waits_on_open"]:
            last = max(closed, key=lambda c: (c["at"] or "", c["id"]))
            rel["freed_at"], rel["freed_by"] = last["at"], last["id"]
        t["rel"] = rel
    return tasks


def scored(db: Db, tasks: list[dict], today: date, cfg: dict, *, all_rows: bool = False) -> list[dict]:
    """`annotate`, then score every open row (every row with `all_rows`) in place; returns `tasks`.
    Every reader that ranks goes through here, so no surface scores without links (design §7.1)."""
    annotate(db, tasks)
    for t in tasks:
        if all_rows or t.get("status") == "open":
            t["score"], t["priority"], t["score_reasons"] = scoring.score(t, today, cfg)
    return tasks


def score_one(task: dict, today: date, cfg: dict) -> tuple[int, str, list[str]]:
    """`scoring.score` for one row its caller already annotated (the Notion mirror's `properties`)."""
    return scoring.score(task, today, cfg)
