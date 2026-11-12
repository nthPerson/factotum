"""The deterministic command grammar. Anything this cannot parse goes to Claude (Chat).

    done 389 392
    drop Task-382
    snooze 364            -> snoozed until tomorrow
    push 364 fri          -> due Friday
    defer 388 to next week
    nodue 364             -> the due date removed (also: undate)
    top 23                -> pin to Top
    drop 12 duplicate of 14 -> not a command (words left over)
    longterm 38           -> mark long-term

parse(text, today) -> list of change dicts, or None when the text is not a command.
Change dict: {"task_id": int, "op": str, "fields": {...}}

An add line is not a command (parse returns None for it); parse_add reads it:

    add Email the lab the dataset fri #hardware ~S after 120
                          -> a new task "Email the lab the dataset", due Friday, project Hardware Lab,
                             effort S, waiting on Task-120 (also: new …, + …)
"""
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, timedelta

from .dates import resolve_when

VERBS = {
    "done": "done", "finished": "done", "complete": "done", "completed": "done",
    "drop": "drop", "remove": "drop", "delete": "drop", "cancel": "drop",
    "snooze": "snooze", "defer": "push", "push": "push", "postpone": "push", "move": "push",
    "nodue": "nodue", "undate": "nodue", "undue": "nodue",
    "top": "top", "pin": "top", "unpin": "unpin",
    "longterm": "longterm", "long-term": "longterm", "lt": "longterm",
    "start": "start", "reopen": "reopen",
}

# Verbs that take no date: any word left after the task numbers makes the text not a command.
DATELESS = {"done", "drop", "top", "unpin", "longterm", "start", "reopen", "nodue"}

_ID = re.compile(r"(?:task[-\s]?|#)?(\d{1,6})\b", re.I)


def parse(text: str, today: date) -> list[dict] | None:
    t = (text or "").strip()
    if not t:
        return None
    m = re.match(r"^([a-z\-]+)\s+(.+)$", t, re.I)
    if not m or m.group(1).lower() not in VERBS:
        return None
    op = VERBS[m.group(1).lower()]
    rest = m.group(2)
    # split ids from an optional "to <when>" / trailing when
    when_text = None
    mw = re.search(r"\b(?:to|until|till)\s+(.+)$", rest, re.I)
    if mw:
        when_text = mw.group(1)
        rest = rest[: mw.start()]
    ids = [int(x) for x in _ID.findall(rest)]
    if not ids:
        return None
    leftover = re.sub(r"\band\b", " ", _ID.sub("", rest), flags=re.I).replace(",", " ").strip()
    if op in DATELESS and (leftover or when_text):
        return None  # words (or a date) this verb cannot use → not a command
    if leftover and when_text is None:
        when_text = leftover
    elif leftover:
        return None  # words we do not understand → not a command
    changes = []
    for tid in ids:
        if op == "done":
            changes.append({"task_id": tid, "op": "done", "fields": {}})
        elif op == "drop":
            changes.append({"task_id": tid, "op": "drop", "fields": {}})
        elif op == "snooze":
            until = resolve_when(when_text, today) if when_text else today + timedelta(days=1)
            if until is None:
                return None
            changes.append({"task_id": tid, "op": "set", "fields": {"snoozed_until": until.isoformat()}})
        elif op == "push":
            if when_text:
                d = resolve_when(when_text, today)
                if d is None:
                    return None
                changes.append({"task_id": tid, "op": "set", "fields": {"due": d.isoformat(), "snoozed_until": None}})
            else:
                changes.append({"task_id": tid, "op": "set", "fields": {"snoozed_until": (today + timedelta(days=1)).isoformat()}})
        elif op == "nodue":
            if when_text:
                return None
            changes.append({"task_id": tid, "op": "set", "fields": {"due": None}})
        elif op == "top":
            changes.append({"task_id": tid, "op": "pin_top", "fields": {}})
        elif op == "unpin":
            changes.append({"task_id": tid, "op": "set", "fields": {"top_rank": None}})
        elif op == "longterm":
            changes.append({"task_id": tid, "op": "set", "fields": {"longterm": 1}})
        elif op == "start":
            changes.append({"task_id": tid, "op": "set", "fields": {"legacy_status": "In progress"}})
        elif op == "reopen":
            changes.append({"task_id": tid, "op": "reopen", "fields": {}})
    return changes


# ---------- the add line (stage 6) ----------
#
# parse_add(text, today, projects, efforts) reads "add <name and parts>". #project, ~effort and a lone
# "!" are read anywhere; a date phrase, "after N" and "under N" only in the trailing run (from the end
# of the line back to the first word that is none of these), so "Plan monday meeting fri" keeps
# "monday" in the name. When a kind appears twice the last one wins. A part that is refused stays in
# the name, and `refused` says why.

ADD_RE = re.compile(r"^\s*(?:(?:add|new)(?:\s+|$)|\+\s*)", re.I)    # a bare "add" / "new" is a verb with no name, like a bare "+"

_WEEKDAY_WORDS = {"mon", "monday", "tue", "tues", "tuesday", "wed", "weds", "wednesday", "thu", "thur", "thurs",
                  "thursday", "fri", "friday", "sat", "saturday", "sun", "sunday"}
_DAY_WORDS = {"today", "tomorrow", "tmrw"} | _WEEKDAY_WORDS
_MONTH_WORDS = {"jan", "january", "feb", "february", "mar", "march", "apr", "april", "may", "jun", "june",
                "jul", "july", "aug", "august", "sep", "sept", "september", "oct", "october", "nov", "november",
                "dec", "december"}
_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_LINK_N = re.compile(r"(?:task-)?(\d{1,12})", re.I)      # 12 digits: a long number is looked up and refused, never kept silently
_LINK_WORDS = {"after": "waits_on", "under": "part_of"}
_KEPT = "; kept in the name"
_SECOND = {"part_of": "a second under", "waits_on": "a second after", "due": "a second due date",
           "hard": "a second !", "project": "a second project", "effort": "a second effort"}


def link_refusal(kind: str, n: int, task: dict | None, under: int | None = None) -> str | None:
    """Why a new task cannot be linked to Task-n (None when it can). `task` is
    {"id", "name", "status", "goal"} (`goal` = that task's own goal id) or None when there is no Task-n."""
    if task is None:
        return f"no Task-{n}"
    if kind == "waits_on":
        if task["status"] != "open":
            return f"Task-{n} is already {task['status']}"
        if n == under:
            return f"Task-{n} is its goal; a task cannot wait on its own goal"
    elif kind == "part_of" and task.get("goal") is not None:
        return f"Task-{n} is a step of Task-{task['goal']}; a step cannot have steps"
    return None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _project(words: str, projects: list[str]) -> tuple[str | None, str | None]:
    """'#x' → (configured name, None) or (None, why): an exact match after _norm, else a unique prefix of 2+."""
    x = _norm(words[1:])
    exact = [p for p in projects if x and _norm(p) == x]
    if exact:
        return exact[0], None
    starts = [p for p in projects if len(x) >= 2 and _norm(p).startswith(x)]
    if len(starts) == 1:
        return starts[0], None
    if not starts:
        return None, f"no project matches {words}"
    names = starts[:3]
    return None, f"{words} could be {', '.join(names[:-1])} or {names[-1]}"


def _date_at(toks: list[str], j: int) -> dict | None:
    """The date phrase that ends at token j (with a "by"/"due" in front of it), or None."""
    low = [t.lower() for t in toks]
    bang = len(low[j]) > 1 and low[j].endswith("!")     # a trailing "!" is the hard mark; the date is read without it
    last = low[j][:-1] if bang else low[j]
    if j >= 1 and ((low[j - 1] == "next" and (last in _WEEKDAY_WORDS or last == "week"))
                   or (low[j - 1] in _MONTH_WORDS and re.fullmatch(r"\d{1,2}", last))):
        start, when = j - 1, f"{low[j - 1]} {last}"
    else:
        when = last
        if when not in _DAY_WORDS and not _ISO_DAY.fullmatch(when):
            return None
        start = j
    if start >= 1 and low[start - 1] in ("by", "due"):
        start -= 1
    return {"kind": "due", "start": start, "end": j, "when": when, "bang": bang}


def parse_add(text: str, today: date, projects: list[str], efforts: list[str], *, verb: bool = True,
              task: Callable[[int], dict | None] | None = None) -> dict | None:
    """Read an add line. None when `verb` is True and the line does not start with add / new / + (that verb is stripped). With
    `verb` False nothing is stripped from a line with words after the first; a bare verb alone is a missing name.
    `task(n)` looks a task up for link_refusal; without it the task numbers are not checked."""
    t = (text or "").strip()
    if verb:                                  # the Today command box: the typed verb is required and stripped
        m = ADD_RE.match(t)
        if not m:
            return None
        t = t[m.end():]
    elif re.fullmatch(r"(?:add|new|\+)", t, re.I):   # the New task box strips nothing, except a bare verb alone
        t = ""
    toks = t.split()

    # anywhere: #project (not all digits), ~effort, a lone "!"
    anywhere: dict[int, dict] = {}
    for i, tok in enumerate(toks):
        if tok.startswith("#") and len(tok) > 1 and not tok[1:].isdigit():
            anywhere[i] = {"kind": "project", "start": i, "end": i}
        elif tok.startswith("~") and len(tok) > 1:
            anywhere[i] = {"kind": "effort", "start": i, "end": i}
        elif tok == "!":
            anywhere[i] = {"kind": "hard", "start": i, "end": i}
    cands = list(anywhere.values())

    # the trailing run: after N / under N, a date phrase, or a token already read above
    j = len(toks) - 1
    while j >= 0:
        n = _LINK_N.fullmatch(toks[j])
        if j >= 1 and n and toks[j - 1].lower() in _LINK_WORDS:
            cands.append({"kind": _LINK_WORDS[toks[j - 1].lower()], "start": j - 1, "end": j, "n": int(n.group(1))})
            j -= 2
            continue
        d = _date_at(toks, j)
        if d:
            cands.append(d)
            j = d["start"] - 1
            continue
        if j in anywhere:
            j -= 1
            continue
        break

    def words(c: dict) -> str:
        return " ".join(toks[c["start"]:c["end"] + 1])

    def of_kind(kind: str) -> list[dict]:
        return sorted((c for c in cands if c["kind"] == kind), key=lambda c: -c["start"])

    parts: list[tuple[int, int, dict]] = []      # (position, order within a token, part)
    refused: list[tuple[int, dict]] = []
    fields: dict = {}
    links: dict[str, int | None] = {"part_of": None, "waits_on": None}

    def refuse(c: dict, why: str) -> None:
        refused.append((c["start"], {"words": words(c), "why": why + _KEPT}))

    def accept(c: dict, part: dict, order: int = 0) -> None:
        c["ok"] = True
        parts.append((c["start"], order, part))

    for kind in ("part_of", "waits_on"):
        for k, c in enumerate(of_kind(kind)):
            if k:
                refuse(c, _SECOND[kind])
                continue
            n = c["n"]
            other = task(n) if task else None
            why = link_refusal(kind, n, other, links["part_of"]) if task else None
            if why:
                refuse(c, why)
                continue
            links[kind] = n
            if kind == "waits_on":
                part = {"kind": kind, "words": words(c), "value": n,
                        "label": f"waits on Task-{n}", "legend": f"makes it wait on Task-{n}"}
            else:
                closed = f" ({other['status']})" if other and other["status"] != "open" else ""
                part = {"kind": kind, "words": words(c), "value": n,
                        "label": f"part of Task-{n}{closed}", "legend": f"makes it a step of Task-{n}"}
            if other:
                part["other"] = {"id": other["id"], "name": other["name"], "status": other["status"]}
            accept(c, part)

    due = None
    for k, c in enumerate(of_kind("due")):
        if k:
            refuse(c, _SECOND["due"])
            continue
        try:
            d = resolve_when(c["when"], today)
        except ValueError:                    # an impossible day: feb 30
            d = None
        if d is None:
            refuse(c, f"{words(c)} is not a date")
            continue
        due = c
        fields["due"] = d.isoformat()
        said = words(c)[:-1] if c["bang"] else words(c)
        accept(c, {"kind": "due", "words": said, "value": d.isoformat(),
                   "label": f"{d:%a %b} {d.day}", "legend": "is the due date"})

    # "fri!" is a date and a "!": that "!" goes with its date and comes first; a lone "!" needs a due date
    bangs = of_kind("hard")
    if due and due["bang"]:
        bangs.insert(0, due)
    for k, c in enumerate(bangs):
        if k:
            refuse(c, _SECOND["hard"])
        elif due is None:
            refuse(c, "! needs a due date")
        else:
            fields["hard"] = 1
            c["ok"] = True
            parts.append((c["start"] if c is not due else c["end"], 1, {
                "kind": "hard", "words": "!", "value": 1, "label": "hard deadline", "legend": "makes it a hard deadline"}))

    for k, c in enumerate(of_kind("project")):
        if k:
            refuse(c, _SECOND["project"])
            continue
        name, why = _project(words(c), projects)
        if why:
            refuse(c, why)
            continue
        fields["project"] = name
        accept(c, {"kind": "project", "words": words(c), "value": name, "label": name, "legend": "is the project"})

    by_effort = {e.lower(): e for e in efforts}
    for k, c in enumerate(of_kind("effort")):
        if k:
            refuse(c, _SECOND["effort"])
            continue
        effort = by_effort.get(words(c)[1:].lower())
        if effort is None:
            refuse(c, f"{words(c)} is not an effort ({', '.join(efforts)})")
            continue
        fields["effort"] = effort
        accept(c, {"kind": "effort", "words": words(c), "value": effort, "label": effort, "legend": "is the effort"})

    used = {i for c in cands if c.get("ok") for i in range(c["start"], c["end"] + 1)}
    name = " ".join(tok for i, tok in enumerate(toks) if i not in used)
    out_refused = [r for _, r in sorted(refused, key=lambda r: r[0])]
    if not name:
        out_refused.append({"words": "", "why": "a task needs a name"})
    return {"name": name, "fields": {"name": name, **fields}, "after": links["waits_on"], "under": links["part_of"],
            "parts": [p for _, _, p in sorted(parts, key=lambda p: (p[0], p[1]))], "refused": out_refused}
