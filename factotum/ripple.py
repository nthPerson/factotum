"""Ripples (design §8): when a linked task is closed, materially changed or merged, one light Claude
run with no tools brings the tasks linked to it up to date. Python decides when: `batches.apply`
queues each such task in `ripple_queue` (never for a ripple's own batch), the scheduler runs a group
after the quiet period or at once for an outcome. A ripple only rewrites a neighbour's text (guarded,
undoable with its cause); everything else it suggests waits on Review as a card a person accepts.
"""
from __future__ import annotations

import html
import json
import re
import sqlite3
from datetime import date, timedelta
from typing import Any, Callable

from . import batches
from . import config as C
from .db import get_task, now_iso

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


# --- the run's input (design §8.2) -------------------------------------------------------------

BEFORE_FIELDS = ("name", "context", "due", "hard", "outcome", "status")


def _material_batches(conn: sqlite3.Connection, tid: int) -> tuple[list[int], list[str]]:
    """A task with no queue row (asked for from the CLI): its newest non-ripple, not undone batch
    that changed it by `enqueue`'s rule, as ([batch id], what); ([], ["asked"]) when there is none."""
    for r in conn.execute("SELECT DISTINCT b.id FROM batches b JOIN changes c ON c.batch_id = b.id "
                          "WHERE c.task_id = ? AND b.source != ? AND b.undone_at IS NULL ORDER BY b.id DESC", (tid, RUN_KIND)).fetchall():
        what = _what(conn, r["id"]).get(tid)
        if what:
            return [r["id"]], _ordered(what)
    return [], ["asked"]


def _changed(conn: sqlite3.Connection, t: dict, batch_ids: list[int], what: list[str]) -> dict:
    """One changed task: the current values ("after"), the first `old` of each field in its batches
    ("before"), and the duplicates those batches merged into it."""
    before: dict[str, Any] = {}
    merged: list[int] = []
    if batch_ids:
        marks = ",".join("?" * len(batch_ids))
        for r in conn.execute(f"SELECT field, old, new FROM changes WHERE task_id = ? AND batch_id IN ({marks}) ORDER BY batch_id, id",
                              (t["id"], *batch_ids)).fetchall():
            if r["field"] in BEFORE_FIELDS and r["field"] not in before:
                before[r["field"]] = _load(r["old"])
            elif r["field"] == "link" and r["old"] is None:
                new = _load(r["new"])
                if isinstance(new, dict) and new.get("kind") == "same_as" and new.get("dir") == "in" and new.get("other") not in merged:
                    merged.append(new.get("other"))
    return {"id": t["id"], "name": t["name"], "status": t["status"], "outcome": t.get("outcome"), "due": t["due"], "hard": t["hard"],
            "context": t["context"], "what": what, "before": before, "merged": merged}


def _neighbour_links(conn: sqlite3.Connection, changed: list[int]) -> list[tuple[int, str]]:
    """(task id, how it is linked, from its side) for every task one link away from a changed task,
    in relation order, then the other steps of each changed task's goal."""
    out: list[tuple[int, str]] = []
    goals: list[int] = []
    for c in changed:
        for r in conn.execute("SELECT src, dst, kind FROM relations WHERE (src = ? OR dst = ?) AND removed_at IS NULL ORDER BY id", (c, c)).fetchall():
            if r["kind"] == "same_as":
                out.append((r["dst"] if r["src"] == c else r["src"], f"same task as Task-{c}"))
            elif r["src"] == c:
                out.append((r["dst"], f"Task-{c} waits on it" if r["kind"] == "waits_on" else f"the goal of Task-{c}"))
                if r["kind"] == "part_of":
                    goals.append(r["dst"])
            else:
                out.append((r["src"], f"waits on Task-{c}" if r["kind"] == "waits_on" else f"a step of Task-{c}"))
    for g in goals:
        for r in conn.execute("SELECT src FROM relations WHERE dst = ? AND kind = 'part_of' AND removed_at IS NULL ORDER BY id", (g,)).fetchall():
            out.append((r["src"], f"another step of Task-{g}"))
    return out


def bundle(conn: sqlite3.Connection, cfg: dict, task_ids: list[int], today: date) -> dict:
    """What one ripple run reads (design §8.2), capped: each changed task before and after, and each
    open task one link away from one, then the other steps of the same goal (`NEIGHBOUR_CAP` in
    all, each once, never a changed task). `texts` keeps each neighbour's whole text as it was read,
    the `expect` of its rewrite; `queue` the `last_batch_id` the run may delete a queue row for."""
    rows = {r["task_id"]: r for r in pending(conn)}
    changed: list[dict] = []
    queue: dict[int, int] = {}
    causes: set[int] = set()
    for tid in task_ids:
        t = get_task(conn, tid)
        if not t:
            continue
        if tid in rows:
            q = rows[tid]
            queue[tid] = q["last_batch_id"]
            marks = ",".join("?" * len(q["batches"]))
            ids = [r["id"] for r in conn.execute(f"SELECT id FROM batches WHERE id IN ({marks}) AND undone_at IS NULL ORDER BY id",
                                                 tuple(q["batches"])).fetchall()] if q["batches"] else []
            what = q["what"]
        else:
            ids, what = _material_batches(conn, tid)
        causes.update(ids)
        changed.append(_changed(conn, t, ids, what))
    from_ids = [c["id"] for c in changed]
    neighbours: list[dict] = []
    texts: dict[int, str] = {}
    for nid, via in _neighbour_links(conn, from_ids):
        if len(neighbours) >= NEIGHBOUR_CAP:
            break
        if nid in from_ids or nid in texts:
            continue
        n = get_task(conn, nid)
        if not n or n["status"] != "open":
            continue
        waits = [r["dst"] for r in conn.execute("SELECT r.dst FROM relations r JOIN tasks t ON t.id = r.dst WHERE r.src = ? AND r.kind = 'waits_on' "
                                                "AND r.removed_at IS NULL AND t.status = 'open' ORDER BY r.id", (nid,)).fetchall()]
        texts[nid] = n["context"] or ""
        neighbours.append({"id": nid, "name": n["name"], "context": texts[nid][:TEXT_CAP], "due": n["due"], "hard": n["hard"],
                           "waiting_on": n["waiting_on"], "waits_on": waits, "via": via})
    return {"changed": changed, "neighbours": neighbours, "texts": texts, "ids": from_ids + [n["id"] for n in neighbours],
            "from": from_ids, "queue": queue, "batches": sorted(causes), "what": {str(c["id"]): c["what"] for c in changed}}


def _esc(v) -> str:
    return html.escape(str(v or ""), quote=False)


def _shown(field: str, v) -> str:
    """A before or after value in the prompt: texts cut and escaped, an empty value said."""
    if field == "hard":
        return "hard" if v else "not hard"
    if v is None or v == "":
        return "(none)"
    return _esc(str(v)[:TEXT_CAP])


def _changed_text(c: dict) -> str:
    lines = [f"Task-{c['id']} | {_esc(c['status'])} | {_esc(c['name'])}"]
    for f, old in c["before"].items():
        lines.append(f"  {f}: {_shown(f, old)} → {_shown(f, c.get(f))}")
    if c.get("outcome"):
        lines.append(f"  outcome: {_esc(c['outcome'])}")
    lines += [f"  merged into it: Task-{m}" for m in c["merged"]]
    return "\n".join(lines)


def _neighbour_text(n: dict) -> str:
    due = f"due {_esc(n['due'])}{' hard' if n['hard'] else ''}" if n["due"] else "undated"
    waits = "waits on " + (", ".join(f"Task-{w}" for w in n["waits_on"]) or "no task")
    if n["waiting_on"]:
        waits += f"; on {_esc(n['waiting_on'])}"
    return f"Task-{n['id']} | {n['via']} | {due} | {waits} | {_esc(n['name'])}\n  text: {_esc(n['context']) or '(none)'}"


def build_prompt(cfg: dict, b: dict, today: date, clock: str = "") -> str:
    """`prompts/ripple.md` filled from a bundle; everything a task says goes in escaped."""
    fill = {"now": today.strftime("%A %Y-%m-%d") + (f" · it is {clock}" if clock else ""),
            "changed": "\n\n".join(_changed_text(c) for c in b["changed"]) or "(nothing)",
            "neighbours": "\n\n".join(_neighbour_text(n) for n in b["neighbours"]) or "(none)",
            "rewrite_cap": str(REWRITE_CAP)}
    # one pass, so a task's text that says "{{neighbours}}" is never filled in itself
    return re.sub(r"\{\{(\w+)\}\}", lambda m: fill.get(m.group(1), m.group(0)), PROMPT.read_text(encoding="utf-8"))
