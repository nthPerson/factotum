"""Suggested links (design §5.4, §10.1): every rule about a link Factotum proposes rather than the
owner typing it. It owns one card per unordered pair, ever (`why_not`, `suggest_pair`), whether a
waiting card is still worth showing (`live`), and the detectors (`twins`, `threads`, `text_refs`, all
three in `detect`). It writes `link_suggestion` evidence through `relations.suggest` only and never
writes `relations`: a suggestion becomes a link when the owner taps it, through `batches`. `relations.suggest` stays as it is for the import; every
other writer goes through `suggest_pair`. Nothing here commits except `sweep_job` and `after_create`;
everything else leaves that to the caller. The daily cap (`session`) picks which waiting suggestions
are offered today; `relations.annotate` reads its choice."""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime

from . import config as C, relations
from .db import get_settings, set_setting
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


def _rule_problem(db: Db, kind: str, a: int, b: int) -> str | None:
    """`why_not`'s rule part: two distinct existing tasks, and the kind's own rule."""
    if a == b:
        return "the same task"
    for x in (a, b):
        if _status(db, x) is None:
            return f"no Task-{x}"
    return _kind_problem(db, kind, a, b)


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


# --- a shared mail thread or calendar event (design §10.1) ------------------------------------------

THREAD_GROUP_MAX = 4                            # five or more open tasks on one thread: a mailing list, not a pair
THREAD_WHY = {"mail_thread": "both follow the same mail thread", "calendar": "both are on the same calendar event"}


def threads(db: Db) -> list[dict]:
    """`any` suggestions for every pair of a group of 2 to THREAD_GROUP_MAX open tasks that share one
    mail thread or calendar event, ordered by (a, b). A shared recording (`plaud_note`) or a shared
    person (`counterpart`) is provenance and is never read here. A pair on both a thread and an event
    is listed once, as the thread."""
    groups: dict[tuple[str, str], list[int]] = {}
    for r in db.execute("SELECT l.kind, l.ref, l.task_id FROM links l JOIN tasks t ON t.id = l.task_id "
                        "WHERE t.status = 'open' AND l.kind IN ('mail_thread', 'calendar') ORDER BY l.task_id").fetchall():
        groups.setdefault((r[0], r[1]), []).append(int(r[2]))
    out: dict[tuple[int, int], dict] = {}
    for (kind, _ref), members in sorted(groups.items(), key=lambda g: g[0][0] != "mail_thread"):
        if not 2 <= len(members) <= THREAD_GROUP_MAX:
            continue
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                out.setdefault((a, b), {"kind": "any", "a": a, "b": b, "why": THREAD_WHY[kind], "signal": "thread"})
    return [out[p] for p in sorted(out)]


# --- "Task-N" in a task's text (design §10.1) -------------------------------------------------------

TEXT_CAP = 4000                                 # characters of a task's text read
MAX_REFS = 10                                   # references kept per task
_LINES, _LINE_CAP, _LOOKBACK = 200, 600, 60     # lines read, characters per line, characters before a reference
_REF = re.compile(r"\btask-(\d{1,7})\b", re.I)
_LABEL = re.compile(r"\b(?:sub-?tasks?|steps)\s{0,2}:")
_SEP = r"[\s:,(-]{0,6}$"                        # all that may stand between a cue and the reference
# The words just before a reference, per word refs_in returns. "blocked on / by" is one phrase, so a
# shorter word inside it never wins over it. These patterns are the only place the word appears.
_CUES = {
    "same_as": r"merged\s{0,3}into|merged\s{0,3}with|duplicate\s{0,3}of|dupe\s{0,3}of|same\s{0,3}as",
    "part_of": r"part\s{0,3}of|sub-?task\s{0,3}of|step\s{0,3}\d{0,3}\s{0,3}(?:of|in|for)|under",
    "waits_on": r"after|waits?\s{0,3}on|waiting\s{0,3}(?:on|for)|blocked\s{0,3}(?:on|by)|depends\s{0,3}on|needs|requires|until|once",
    "frees": r"before|unlocks?|frees|unblocks?|enables?",
}
_CUE_RE = {word: re.compile(rf"\b(?:{p})(?={_SEP})") for word, p in _CUES.items()}


def _cue(line: str, start: int) -> str | None:
    """The word whose cue ends nearest the reference at `start`, within the _LOOKBACK characters before
    it on `line` (only `_SEP` may stand between); the longer cue on a tie. Searched in place, not on a
    slice, so a cut word ("th|under") is not read as a cue."""
    best = None
    for word, rx in _CUE_RE.items():
        for m in rx.finditer(line, max(0, start - _LOOKBACK), start):
            key = (m.end(), -m.start())
            if best is None or key > best[0]:
                best = (key, word)
    return best[1] if best else None


def refs_in(text: str, self_id: int) -> list[tuple[str, int]]:
    """Every "Task-N" in `text` as (word, N), in order of appearance: `same_as` (this task goes into N),
    `part_of` (this is a step of N), `goal_of` (N is a step of this), `waits_on` (this waits on N),
    `frees` (N waits on this) or `any`. The word comes from the cue just before the reference on its
    line, else a "Sub-tasks:" / "Steps:" label earlier on the line makes it `goal_of`. Reads a capped
    slice through bounded patterns (text written by anyone), at most MAX_REFS, never `self_id`, and
    one entry per N (the first wins)."""
    out: list[tuple[str, int]] = []
    seen = {self_id}
    for line in str(text or "")[:TEXT_CAP].split("\n")[:_LINES]:
        line = line[:_LINE_CAP].casefold()
        label = _LABEL.search(line)
        label_end = label.end() if label else None
        for m in _REF.finditer(line):
            n = int(m.group(1))
            if n in seen:
                continue
            seen.add(n)
            word = _cue(line, m.start())
            if word is None:
                word = "goal_of" if label_end is not None and label_end <= m.start() else "any"
            out.append((word, n))
            if len(out) >= MAX_REFS:
                return out
    return out


def _ref_pair(word: str, t: int, n: int) -> tuple[str, int, int]:
    """(kind, a, b) of the suggestion that `word` in Task-t's text, naming Task-n, stands for."""
    if word == "goal_of":
        return "part_of", n, t
    if word == "frees":
        return "waits_on", n, t
    if word == "any":
        return "any", min(t, n), max(t, n)
    return word, t, n


def text_refs(db: Db, ids: set[int] | None = None) -> list[dict]:
    """Suggestions from "Task-N" in the text (name, then context) of the open tasks (only `ids` when
    given). A reference to a missing task, to a closed one where the kind needs it open, or that a
    link rule refuses is dropped, never turned into `any`. The `why` never quotes the text."""
    out = []
    for r in db.execute("SELECT id, name, context FROM tasks WHERE status = 'open' ORDER BY id").fetchall():
        t = int(r[0])
        if ids is not None and t not in ids:
            continue
        for word, n in refs_in(f"{r[1] or ''}\n{r[2] or ''}", t):
            kind, a, b = _ref_pair(word, t, n)
            if _rule_problem(db, kind, a, b) is None:
                out.append({"kind": kind, "a": a, "b": b, "why": f"Task-{t}'s text names Task-{n}", "signal": "text"})
    return out


def detect(db: Db, ids: set[int] | None = None) -> list[dict]:
    """Every detector's suggestions: twins, then "Task-N" in text, then (over the whole list only, not
    for `ids`) a shared thread or event."""
    return twins(db, ids) + text_refs(db, ids) + (threads(db) if ids is None else [])


# --- writing (the caller commits, except sweep_job and after_create) ---------------------------------

SWEEP_MINUTES = 30                              # every half hour: the pass is free, not a matter of taste
_SIGNALS = ("twin", "text", "thread")


def _write(db: Db, found: list[dict]) -> tuple[dict, list[int]]:
    written: dict[str, int] = {}
    ids: list[int] = []
    for c in found:
        e = suggest_pair(db, c["kind"], c["a"], c["b"], c["why"], c["signal"])
        if e is not None:
            written[c["signal"]] = written.get(c["signal"], 0) + 1
            ids.append(e)
    return written, ids


def sweep(db: Db, today: date) -> dict:
    """Write every `detect` candidate through `suggest_pair`: {"written": {signal: n}, "ids": [evidence ids]}.
    The caller commits."""
    written, ids = _write(db, detect(db))
    return {"written": written, "ids": ids}


def sweep_job(conn, cfg: dict, now: datetime) -> str | None:
    """`sweep`, today's `session`, commit, and a line like "3 suggested: twin 1, text 2", or None when
    nothing was written."""
    out = sweep(conn, now.date())
    session(conn, now.date(), int(C.value(cfg, "links.suggestions_per_review") or 0))
    conn.commit()
    w = out["written"]
    if not w:
        return None
    return f"{sum(w.values())} suggested: " + ", ".join(f"{s} {w[s]}" for s in _SIGNALS if w.get(s))


def at_create(db: Db, ids: list[int]) -> list[int]:
    """The detectors over the tasks just created, written through `suggest_pair`; returns the evidence
    ids. The caller commits."""
    return _write(db, detect(db, set(ids)))[1]


def pending(db: Db) -> list[dict]:
    """Every unjudged `link_suggestion` as its ref plus `evidence_id`, oldest first (a ref that does
    not parse is left out)."""
    out = []
    for r in db.execute("SELECT id, ref FROM evidence WHERE kind = ? AND judged_at IS NULL ORDER BY id", (relations.EVIDENCE_KIND,)).fetchall():
        try:
            ref = json.loads(r[1] or "")
        except (TypeError, ValueError):
            continue
        if isinstance(ref, dict):
            out.append(dict(ref, evidence_id=int(r[0])))
    return out


# --- the daily cap (design §10.1: at most links.suggestions_per_review a day) ------------------------

SESSION_KEY = relations.SESSION_KEY             # {"day": "YYYY-MM-DD", "ids": [offered that day, judged ones included]}
OFFERED_KEY = "state.links.offered"             # {"<evidence id>": "YYYY-MM-DD"}: the last day each was offered


def session(conn, today: date, n: int) -> list[int]:
    """The ids of the link suggestions to show today, in selection order, at most `n` a day; writes
    SESSION_KEY and OFFERED_KEY, the caller commits. A filing suggestion is never counted (it always
    shows). On the stored session's day: its ids still pending and live, topped up with new ones only
    while fewer than `n` were offered (a judged one is not replaced). On another day: up to `n` afresh.
    Candidates are taken never offered first, then the longest unseen, then the oldest, so a card the
    owner keeps skipping never keeps another out for good; one that is no longer `live` (a task closed)
    is not shown, and from the next day takes no slot."""
    st = get_settings(conn)
    waiting = pending(conn)
    pending_ids = {s["evidence_id"] for s in waiting}
    offered = {k: v for k, v in (st.get(OFFERED_KEY) or {}).items() if str(k).isdigit() and int(k) in pending_ids}
    day = today.isoformat()
    shown: list[int] = []
    stored: list[int] = []
    if n > 0:
        cands = sorted((s["evidence_id"] for s in waiting if s.get("signal") != "filing" and live(conn, s)),
                       key=lambda e: (offered.get(str(e)) or "", e))
        prev = st.get(SESSION_KEY) or {}
        if prev.get("day") == day:
            stored = [int(e) for e in prev.get("ids") or []]
            still = set(cands)
            shown = [e for e in stored if e in still]
        for e in cands:
            if len(stored) >= n:
                break
            if e not in stored:
                stored.append(e)
                shown.append(e)
    for e in shown:
        offered[str(e)] = day
    set_setting(conn, SESSION_KEY, {"day": day, "ids": stored})
    set_setting(conn, OFFERED_KEY, offered)
    return shown


def after_create(conn, created: list[int], source: str) -> None:
    """`at_create`, then commit; never raises (a failure rolls back and leaves one line on stderr)."""
    try:
        at_create(conn, created)
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"factotum: suggestions after create failed: {type(e).__name__}: {e}", file=sys.stderr)
