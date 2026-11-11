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
import time
from datetime import date, timedelta
from typing import Any, Callable

from . import batches
from . import config as C
from . import relations as R
from .db import get_task, log_run, now_iso, rows
from .planner import _same_suggestion
from .sync_file import _clean_task_fields

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


# --- the reply (design §8.3, §8.6) ---------------------------------------------------------------

EFFECTS = ("none", "text", "propose")
WHY_CAP = 300
# Bounded patterns: the old text is a task's text and may hold a stranger's words.
URL_RE = re.compile(r"https?://[^\s<>\"')\]]{1,2000}")
TASK_REF_RE = re.compile(r"\bTask-(\d{1,6})\b")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,24}")
SET_FIELDS = ("name", "due", "hard", "effort", "project")     # what a ripple's `set` or `create` card may carry
NEVER_FIELDS = ("counterpart", "waiting_on")                  # a ripple never copies an address or a person (design §8.6)
LEFT_OUT = "[address left out]"
SHAPE = 'the reply is not {"ripples": [...]}'


def _short(v) -> str:
    """A value the model wrote, as a log line names it: a string as itself, anything else as its repr, cut."""
    r = " ".join(v.split()) if isinstance(v, str) else repr(v)
    return r if len(r) <= 40 else r[:37] + "..."


class ReplyError(ValueError):
    """A reply of the wrong shape; the whole run is refused and nothing is applied."""


def parse(data, b: dict) -> list[dict]:
    """The reply `{"ripples": [entry, ...]}` checked whole against the bundle it answers: one entry per
    linked task at most, each `{task_id, effect, context, why, proposals}`. Returns the entries
    normalised (`context` None unless the effect is `text`, `why` one line of at most `WHY_CAP`,
    `proposals` a list). Raises `ReplyError` naming the first thing wrong."""
    if not isinstance(data, dict) or not isinstance(data.get("ripples"), list):
        raise ReplyError(SHAPE)
    out: list[dict] = []
    seen: set[int] = set()
    for i, e in enumerate(data["ripples"]):
        if not isinstance(e, dict):
            raise ReplyError(f"entry {i} is not an object")
        tid = e.get("task_id")
        if type(tid) is not int or tid not in b["texts"]:
            raise ReplyError(f"entry {i}: task_id {_short(tid)} is not one of the linked tasks")
        if tid in seen:
            raise ReplyError(f"Task-{tid} has two entries")
        seen.add(tid)
        effect = e.get("effect")
        if effect not in EFFECTS:
            raise ReplyError(f"Task-{tid}: effect {_short(effect)} is not none, text or propose")
        if effect == "text" and not isinstance(e.get("context"), str):
            raise ReplyError(f"Task-{tid}: effect text without a context string")
        why = e.get("why", "")
        if not isinstance(why, str):
            raise ReplyError(f"Task-{tid}: why is not a string")
        props = e.get("proposals", [])
        if not isinstance(props, list) or not all(isinstance(p, dict) for p in props):
            raise ReplyError(f"Task-{tid}: proposals is not a list of objects")
        out.append({"task_id": tid, "effect": effect, "context": e["context"] if effect == "text" else None,
                    "why": " ".join(why.split())[:WHY_CAP], "proposals": props})
    return out


def _urls(text: str) -> list[str]:
    return list(dict.fromkeys(u.rstrip(".,;:!?") for u in URL_RE.findall(text or "")))


def _emails(text: str) -> list[str]:
    return list(dict.fromkeys(m.lower() for m in EMAIL_RE.findall(text or "")))


def guard(old: str, new: str) -> str | None:
    """Why a rewrite of `old` into `new` is refused, or None: empty, over `REWRITE_CAP`, a web
    address or a `Task-N` of the old text missing, or an email address the old text did not have."""
    old, new = old or "", new or ""
    if not new.strip():
        return "empty"
    if len(new) > REWRITE_CAP:
        return f"longer than {REWRITE_CAP} characters"
    kept = set(_urls(new))
    for u in _urls(old):
        if u not in kept:
            return f"drops {u}"
    refs = {int(n) for n in TASK_REF_RE.findall(new)}
    for n in dict.fromkeys(int(n) for n in TASK_REF_RE.findall(old)):
        if n not in refs:
            return f"drops Task-{n}"
    had = set(_emails(old))
    for m in _emails(new):
        if m not in had:
            return f"adds the address {m}"
    return None


def _no_new_addresses(text: str, old: str) -> str:
    """`text` with every email address `old` lacks replaced by `LEFT_OUT`."""
    had = set(_emails(old))
    return EMAIL_RE.sub(lambda m: m.group(0) if m.group(0).lower() in had else LEFT_OUT, text)


def _task_ref(v) -> int | None:
    """A task id the model wrote: an int (never a bool), "12" or "Task-12"."""
    if type(v) is int:
        return v
    if isinstance(v, str):
        m = re.fullmatch(r"\s*(?:Task-)?(\d{1,6})\s*", v)
        if m:
            return int(m.group(1))
    return None


def _said(field: str, v) -> str:
    """A cleaned value in a card's label."""
    if field == "hard":
        return "true" if v else "false"
    return "none" if v is None or v == "" else str(v)


def clean_proposal(conn: sqlite3.Connection, cfg: dict, p: dict, task_id: int, b: dict) -> tuple[dict | None, str]:
    """One proposal of the entry for `task_id` → `(change, label)` for a Review card, or
    `(None, why it is refused)`. Never `counterpart`, `waiting_on`, `context` or `merge`; a `set` or
    `create` carries only `SET_FIELDS`, cleaned like a recording's; a link names a task of the bundle."""
    op = p.get("op")
    head = f"Task-{task_id} {_short(op)}"
    given = p.get("task_id")
    if given is not None and given != task_id:
        return None, f"{head}: names Task-{_short(given)}, not the task it is about"
    fields = p.get("fields", {})
    if fields is None:
        fields = {}
    if not isinstance(fields, dict):
        return None, f"{head}: fields is not an object"
    never = [k for k in NEVER_FIELDS if k in fields]
    if never:
        return None, f"{head}: a ripple never sets {' or '.join(never)}"
    if "context" in fields:
        return None, f"{head}: the text is the rewrite's job, not a proposal's"
    if op in ("done", "drop"):
        return {"task_id": task_id, "op": op, "fields": {}}, f"{op} Task-{task_id}"
    if op in ("set", "create"):
        other = [k for k in fields if k not in SET_FIELDS]
        if other:
            return None, f"{head}: a ripple may propose only {', '.join(SET_FIELDS)}, not {_short(', '.join(map(str, other)))}"
        f = _clean_task_fields(cfg, fields)
        dropped = [k for k in fields if k not in f or (k == "project" and f[k] != fields[k])]
        if dropped:
            return None, f"{head}: {', '.join(dropped)} is not a value Factotum takes"
        if op == "create":
            if not f.get("name"):
                return None, f"{head}: a new task needs a name"
            return {"task_id": None, "op": "create", "fields": f}, f"create: {f['name']}"
        if not f:
            return None, f"{head}: nothing to set"
        said = ", ".join(f"{k} {_said(k, f[k])}" for k in fields)
        return {"task_id": task_id, "op": "set", "fields": f}, f"set {said} on Task-{task_id}"
    if op == "add_reminder":
        try:
            rf = batches._reminder_fields({"fields": fields})
        except ValueError as e:
            return None, f"{head}: {e}"
        return {"task_id": task_id, "op": "add_reminder", "fields": rf}, f"a reminder on Task-{task_id} at {rf['fire_at'][:16].replace('T', ' ')}"
    if op in ("link", "unlink"):
        kind, other = fields.get("kind"), _task_ref(fields.get("other"))
        if kind not in R.LINK_KINDS:
            return None, f"{head}: kind must be part_of or waits_on, not {_short(kind)}"
        if other is None or other == task_id or other not in b["ids"]:
            return None, f"{head}: other must be another task listed in the run, not {_short(fields.get('other'))}"
        why = R.link_problem(conn, op, task_id, kind, other)
        if why:
            return None, f"{head}: {why}"
        words = R.WORDS[kind] if op == "link" else f"no longer {R.WORDS[kind]}"
        return {"task_id": task_id, "op": op, "fields": {"kind": kind, "other": other}}, f"Task-{task_id} {words} Task-{other}"
    if op == "merge":
        return None, f"{head}: a ripple never merges"
    return None, f"{head}: not an op a ripple may propose"


def apply(conn: sqlite3.Connection, cfg: dict, b: dict, entries: list[dict], run_id: int | None, today: date) -> dict:
    """Write what a parsed reply decided. A text rewrite that passes `guard` is a `set` carrying the
    text the run read as `expect` (a hand edit made meanwhile wins); one that fails becomes a dated
    `add_note`; both ride in ONE `ripple` batch with the cause and each entry's `why` as the change's
    note. A neighbour no longer open is left alone. Every proposal is a Review card (`proposal`
    evidence, `account = 'ripple'`), at most `PROPOSAL_CAP`, never one already waiting. Commits."""
    first = b["from"][0] if b["from"] else None
    changes: list[dict] = []
    rewrites: list[int] = []
    noted: list[dict] = []
    staged: list[str] = []
    refused: list[str] = []
    skipped: list[str] = []
    waiting: list[dict] = []
    for r in rows(conn, "SELECT ref FROM evidence WHERE kind = 'proposal' AND judged_at IS NULL"):
        w = _load(r["ref"])
        if isinstance(w, dict):
            waiting.append(w)
    for e in entries:
        tid = e["task_id"]
        if e["effect"] == "none":
            continue
        t = get_task(conn, tid)
        if not t or t["status"] != "open":
            skipped.append(f"Task-{tid} is {t['status']} now" if t else f"Task-{tid} no longer exists")
            continue
        if e["effect"] == "text":
            old = b["texts"][tid]
            why_not = guard(old, e["context"])
            if why_not is None:
                changes.append({"task_id": tid, "op": "set", "fields": {"context": e["context"]}, "expect": {"context": old}, "note": e["why"]})
                rewrites.append(tid)
            else:
                said = f"Ripple from Task-{first}: {e['why']}" if e["why"] else f"Ripple from Task-{first}"
                changes.append({"task_id": tid, "op": "add_note", "fields": {"text": _no_new_addresses(said, old)}, "note": e["why"]})
                noted.append({"task_id": tid, "why": why_not})
        for p in e["proposals"]:
            ch, label = clean_proposal(conn, cfg, p, tid, b)
            if ch is None:
                refused.append(label)
            elif any(_same_suggestion(ch, w) for w in waiting):
                skipped.append(f"{label}: already waiting in Review")
            elif len(staged) >= PROPOSAL_CAP:
                skipped.append(f"{label}: over the {PROPOSAL_CAP} cards a run may stage")
            else:
                reason = " ".join(str(p.get("reason") or "").split())[:WHY_CAP]
                conn.execute("INSERT INTO evidence (task_id, kind, ref, account, label, excerpt, at) VALUES (?, 'proposal', ?, ?, ?, ?, ?)",
                             (ch["task_id"], json.dumps(ch), ACCOUNT, label, reason, now_iso()))
                waiting.append(ch)
                staged.append(label)
    batch_id = None
    rewritten: list[int] = []
    if changes:
        cause = {"kind": RUN_KIND, "from": b["from"], "run": run_id, "batches": b["batches"], "what": b["what"],
                 "noted": [n["task_id"] for n in noted]}
        res = batches.apply(conn, changes, RUN_KIND, today, cause=cause)
        batch_id = res["batch_id"]
        wrote = {r["task_id"] for r in conn.execute("SELECT DISTINCT task_id FROM changes WHERE batch_id = ? AND field = 'context'", (batch_id,))}
        rewritten = [tid for tid in rewrites if tid in wrote]
        for s in res["skipped"]:
            why, tid = str(s.get("why") or ""), s.get("task_id")
            skipped.append(why if f"Task-{tid}" in why else f"Task-{tid}: {why}")
    conn.commit()
    return {"batch_id": batch_id, "rewritten": rewritten, "noted": noted, "staged": staged, "refused": refused, "skipped": skipped}


# --- the run (design §8.1, §8.5) -----------------------------------------------------------------

def _tasks(ids) -> str:
    return ", ".join(f"Task-{i}" for i in ids)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _drop_queued(conn: sqlite3.Connection, b: dict) -> None:
    """Delete the group's queue rows that nothing queued again while the run was in flight."""
    for tid, last in b["queue"].items():
        conn.execute("DELETE FROM ripple_queue WHERE task_id = ? AND last_batch_id = ?", (tid, last))


def _failed(conn: sqlite3.Connection, b: dict, err: str, ms: int, cost: float, dry_run: bool, run_id: int | None = None) -> dict:
    """A run that failed: one more attempt on each of the group's rows still as the run read them, a
    row reaching `MAX_ATTEMPTS` set aside; the run logged with its cost. Commits."""
    if not dry_run:
        aside = []
        for tid, last in b["queue"].items():
            conn.execute("UPDATE ripple_queue SET attempts = attempts + 1 WHERE task_id = ? AND last_batch_id = ?", (tid, last))
            if conn.execute("DELETE FROM ripple_queue WHERE task_id = ? AND last_batch_id = ? AND attempts >= ?", (tid, last, MAX_ATTEMPTS)).rowcount:
                aside.append(tid)
        if aside:
            err += f" · failed twice on {_tasks(aside)}: set aside"
    # an "applying" row a batch already committed is this run's row: it says the failure, not a second row
    if run_id is not None and conn.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)).fetchone():
        conn.execute("UPDATE runs SET ok = 0, summary = 'ripple', error = ? WHERE id = ?", (err[:1000], run_id))
    else:
        log_run(conn, RUN_KIND, ms, ok=False, summary="ripple", error=err[:1000], cost_usd=cost)
    conn.commit()
    return {"ok": False, "error": err, "cost_usd": cost}


def _preview(conn: sqlite3.Connection, cfg: dict, b: dict, entries: list[dict]) -> list[dict]:
    """What `apply` would do with each entry and each proposal, writing nothing."""
    out: list[dict] = []
    for e in entries:
        tid = e["task_id"]
        verdict = "nothing"
        if e["effect"] == "text":
            why_not = guard(b["texts"][tid], e["context"])
            verdict = "would rewrite" if why_not is None else f"would add a note: {why_not}"
        out.append({"task_id": tid, "effect": e["effect"], "verdict": verdict})
        for p in e["proposals"]:
            ch, label = clean_proposal(conn, cfg, p, tid, b)
            out.append({"task_id": tid, "proposal": label if ch else None, "verdict": "would stage" if ch else f"refused: {label}"})
    return out


def _line(b: dict, out: dict) -> str:
    """The Log's line for an applied run."""
    k = len(out["rewritten"])
    line = f"Ripple from {_tasks(b['from'])}: {k} task{'' if k == 1 else 's'} updated" + (f" ({_tasks(out['rewritten'])})" if k else "")
    if out["noted"]:
        line += f", {_plural(len(out['noted']), 'note')} (" + "; ".join(f"Task-{n['task_id']}: {n['why']}" for n in out["noted"]) + ")"
    if out["staged"]:
        line += f", {_plural(len(out['staged']), 'card')} for Review"
    if out["refused"]:
        line += f", {len(out['refused'])} refused"
    if out["skipped"]:
        line += f", {len(out['skipped'])} skipped"
    if out["batch_id"]:
        line += f"; batch {out['batch_id']}"
    return line


def run(conn: sqlite3.Connection, cfg: dict, task_ids: list[int], today: date | None = None, *, runner=None,
        dry_run: bool = False, at: str | None = None) -> dict:
    """One ripple run from the changed tasks `task_ids` (a group from the queue, or one task asked
    for from the CLI): the bundle, one `claude -p` with no tools on the Settings model and budget,
    the whole reply checked, then `apply`. A queue row is deleted only when nothing queued it again
    during the run; a failed run counts an attempt, and a group that fails `MAX_ATTEMPTS` times is
    set aside. A dry run makes the real run and says what it would do, writing nothing but the Log row."""
    from . import reminders
    from .claude import runner as _runner
    runner = runner or _runner.run
    if today is None or at is None:
        now = reminders.local_now(cfg)
        today = today or now.date()
        at = at or now.strftime("%Y-%m-%dT%H:%M")
    b = bundle(conn, cfg, task_ids, today)
    if not b["neighbours"]:
        if not dry_run:
            _drop_queued(conn, b)
            conn.commit()
        return {"ok": True, "line": "nothing linked is open", "cost_usd": 0.0}
    started = time.monotonic()
    prompt = build_prompt(cfg, b, today, at[11:16])
    workdir = RUNS_DIR / today.isoformat() / f"{at[11:16].replace(':', '')}-{b['from'][0]}"
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "prompt.md").write_text(prompt, encoding="utf-8")
    # tools="": every task text in this prompt may hold a stranger's words, so the run gets no tool at all
    res = runner(cfg, RUN_KIND, prompt, allowed_tools=[], tools="", workdir=workdir,
                 model=C.value(cfg, "claude.ripple.model"), budget_usd=float(C.value(cfg, "claude.budgets_usd.ripple")))
    (workdir / "result.txt").write_text(res.result_text or "", encoding="utf-8")
    ms = int((time.monotonic() - started) * 1000)
    if not res.ok:
        return _failed(conn, b, res.error or "no JSON", ms, res.cost_usd, dry_run)
    try:
        entries = parse(res.data, b)
    except ReplyError as e:
        return _failed(conn, b, f"{SHAPE}: {'no list under ripples' if str(e) == SHAPE else e}", ms, res.cost_usd, dry_run)
    if dry_run:
        preview = _preview(conn, cfg, b, entries)
        log_run(conn, RUN_KIND, ms, ok=True, summary=f"dry run: {_tasks(b['from'])}, {len(entries)} entries", cost_usd=res.cost_usd)
        conn.commit()
        return {"ok": True, "dry_run": True, "data": res.data, "entries": entries, "preview": preview, "prompt": prompt, "cost_usd": res.cost_usd}
    run_id = log_run(conn, RUN_KIND, ms, ok=True, summary="applying", cost_usd=res.cost_usd)
    try:
        out = apply(conn, cfg, b, entries, run_id, today)
    except Exception as e:                                   # the run is paid for: log it even when applying it fails
        conn.rollback()
        return _failed(conn, b, f"applying the reply failed: {type(e).__name__}: {e}", ms, res.cost_usd, dry_run, run_id)
    line = _line(b, out)
    conn.execute("UPDATE runs SET summary = ? WHERE id = ?", (line[:1000], run_id))
    _drop_queued(conn, b)
    conn.commit()
    return dict(out, ok=True, line=line, cost_usd=res.cost_usd, run_id=run_id)
