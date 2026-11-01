"""The deterministic command grammar. Anything this cannot parse goes to Claude (Chat).

    done 389 392
    drop Task-382
    snooze 364            -> snoozed until tomorrow
    push 364 fri          -> due Friday
    defer 388 to next week
    top 23                -> pin to Top
    longterm 38           -> mark long-term

parse(text, today) -> list of change dicts, or None when the text is not a command.
Change dict: {"task_id": int, "op": str, "fields": {...}}
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from .dates import resolve_when

VERBS = {
    "done": "done", "finished": "done", "complete": "done", "completed": "done",
    "drop": "drop", "remove": "drop", "delete": "drop", "cancel": "drop",
    "snooze": "snooze", "defer": "push", "push": "push", "postpone": "push", "move": "push",
    "top": "top", "pin": "top", "unpin": "unpin",
    "longterm": "longterm", "long-term": "longterm", "lt": "longterm",
    "start": "start", "reopen": "reopen",
}

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
    leftover = _ID.sub("", rest).replace(",", " ").strip()
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
            until = resolve_when(when_text or "tomorrow", today) or (today + timedelta(days=1))
            changes.append({"task_id": tid, "op": "set", "fields": {"snoozed_until": until.isoformat()}})
        elif op == "push":
            if when_text:
                d = resolve_when(when_text, today)
                if d is None:
                    return None
                changes.append({"task_id": tid, "op": "set", "fields": {"due": d.isoformat(), "snoozed_until": None}})
            else:
                changes.append({"task_id": tid, "op": "set", "fields": {"snoozed_until": (today + timedelta(days=1)).isoformat()}})
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
