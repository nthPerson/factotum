"""Suggested links (design §5.4, §10.1): every rule about a link Factotum proposes rather than the
owner typing it. It owns one card per unordered pair, ever (`why_not`, `suggest_pair`), whether a
waiting card is still worth showing (`live`), and the detectors (`twins`). It writes `link_suggestion`
evidence through `relations.suggest` only and never writes `relations`: a suggestion becomes a link
when the owner taps it, through `batches`. `relations.suggest` stays as it is for the import; every
other writer goes through `suggest_pair`. Nothing here commits except `sweep_job` and `after_create`;
everything else leaves that to the caller."""
from __future__ import annotations

import json
import re

from . import relations
from .relations import Db

KIND_ERROR = "kind must be one of same_as, part_of, waits_on, any"

# Words that say nothing about which item a title is: Canvas's verbs and the filler of a spoken task.
TITLE_STOP = frozenset({"take", "post", "submit", "the", "and", "for", "due", "with", "from", "your",
                        "finish", "complete", "prep", "work", "task", "this", "that"})
_WORD = re.compile(r"[a-z]{3,200}")               # over a 200-character slice: the same as {3,}, bounded


def _status(db: Db, task_id: int) -> str | None:
    r = db.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return r[0] if r else None


def _linked(db: Db, a: int, b: int, removed: bool) -> bool:
    """A `relations` row of any kind joins a and b, either direction: an active one, or a removed one."""
    return db.execute(f"SELECT 1 FROM relations WHERE ((src = ? AND dst = ?) OR (src = ? AND dst = ?)) "
                      f"AND removed_at IS {'NOT ' if removed else ''}NULL LIMIT 1", (a, b, b, a)).fetchone() is not None


def _suggested(db: Db, a: int, b: int) -> bool:
    """Any `link_suggestion` row for the unordered pair, pending or judged, of any kind (a row lives on its `a`)."""
    for r in db.execute("SELECT ref FROM evidence WHERE kind = ? AND task_id IN (?, ?)", (relations.EVIDENCE_KIND, a, b)).fetchall():
        try:
            ref = json.loads(r[0] or "")
            if {int(ref["a"]), int(ref["b"])} == {a, b}:
                return True
        except (TypeError, ValueError, KeyError):
            continue
    return False


def _kind_problem(db: Db, kind: str, a: int, b: int) -> str | None:
    """The kind's own rule, on two existing tasks: why `a kind b` cannot be suggested now, or None."""
    if kind == "same_as":
        return f"Task-{a} is not open" if _status(db, a) != "open" else relations.merge_target_problem(db, b)
    if kind in relations.LINK_KINDS:
        return f"Task-{a} is not open" if _status(db, a) != "open" else relations.check(db, a, b, kind)
    if kind == "any":
        return next((f"Task-{x} is not open" for x in (a, b) if _status(db, x) != "open"), None)
    return KIND_ERROR


def why_not(db: Db, kind: str, a: int, b: int) -> str | None:
    """Why the suggestion `a kind b` may not be written, or None. A pair the owner has decided on in
    any way (linked it, unlinked it, been shown a card for it, whatever its kind or order) is never
    suggested again, whichever signal finds it next (design §5.4). For `same_as`, `a` goes and `b` is kept."""
    if a == b:
        return "the same task"
    for x in (a, b):
        if _status(db, x) is None:
            return f"no Task-{x}"
    if _linked(db, a, b, removed=False):
        return "already linked"
    if _linked(db, a, b, removed=True):
        return "linked before"
    if _suggested(db, a, b):
        return "suggested before"
    return _kind_problem(db, kind, a, b)


def suggest_pair(db: Db, kind: str, a: int, b: int, why: str, signal: str) -> int | None:
    """Write the suggestion `a kind b` (its evidence id) when `why_not` allows it, else None. For
    `same_as`, `a` goes and `b` is kept. The caller commits."""
    if why_not(db, kind, a, b) is not None:
        return None
    return relations.suggest(db, kind, a, b, why, signal)


def live(db: Db, ref: dict) -> bool:
    """A pending suggestion (`ref` with `kind`, `a`, `b`) is still worth showing: both tasks exist, no
    active link joins them, and its kind's rule passes now."""
    try:
        kind, a, b = ref["kind"], int(ref["a"]), int(ref["b"])
    except (TypeError, ValueError, KeyError):
        return False
    if a == b or _status(db, a) is None or _status(db, b) is None or _linked(db, a, b, removed=False):
        return False
    return _kind_problem(db, kind, a, b) is None


# --- twins (design §10.1, tightened: a course series cannot flood Review) ----------------------------

def title_words(t: dict) -> set[str]:
    """The words of a task's name that may tell one item from another: three letters or more, without
    the project's own words and TITLE_STOP."""
    project = set(_WORD.findall(str(t.get("project") or "")[:200].casefold()))
    return set(_WORD.findall(str(t.get("name") or "")[:200].casefold())) - project - TITLE_STOP


def twins(db: Db, ids: set[int] | None = None) -> list[dict]:
    """Twin suggestions over the open rows (`relations.twin_pairs`), only pairs touching `ids` when
    given, ordered by (min id, max id). A row with several candidates keeps the one whose other row
    shares a title word with it; when none or more than one does, that row makes no card."""
    tasks = [dict(r) for r in db.execute("SELECT id, name, project, status, due, source, created_at FROM tasks WHERE status = 'open'").fetchall()]
    words = {int(t["id"]): title_words(t) for t in tasks}
    pairs = relations.twin_pairs(tasks)
    cands: dict[int, list[tuple]] = {}
    for p in pairs:
        for x in p[:2]:
            cands.setdefault(x, []).append(p)

    def chosen(row: int, p: tuple) -> bool:
        mine = cands[row]
        if len(mine) == 1:
            return True
        sharing = [q for q in mine if words[row] & words[q[1] if q[0] == row else q[0]]]
        return sharing == [p]

    return [{"kind": "same_as", "a": goes, "b": keep, "why": why, "signal": "twin"}
            for goes, keep, why in pairs
            if (ids is None or goes in ids or keep in ids) and chosen(goes, (goes, keep, why)) and chosen(keep, (goes, keep, why))]
