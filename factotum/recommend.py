"""A recommended answer on a Review item (spoken Review, stage 2c, design §6.8): Python's reading of what the database
already knows (the item as `review_queue.items` ships it), never a Claude run. A recommendation is data on the item:
its steps are option ids of the rule (design §4.2), and only the owner choosing the pass's option `rec` stages them,
through `review_decide.plan`, as the same presses one by one would. It never stages anything by itself.

The rules, in the order tried: none for a merge or for two proposals that are not mail; no close where
`review_queue.risky_close` gives a reason (then `held` says so); a proposal on the task (already paid for, its own
reason as the basis); then by the item's first reason: a suggested link, new, all steps done, free now, a linked change
already written into the text, a card whose day has passed. Everything else (mail and Slack to read, fading, slipped,
last call, back, Later over, any other card) gets none: the owner's answer there is not known.

The basis is the reason in one sentence and never quotes task text; a `Task-N` in a proposal's reason or a suggestion's
why becomes that task's name (`names`). Every fixed sentence is in TEXTS (the forbidden-words test reads them). Pure."""
from __future__ import annotations

import re
from datetime import date

from .dates import parse_date
from .prompts import one_line
from .review_queue import risky_close

LINKABLE = ("part_of", "waits_on")                       # a suggestion of these kinds is answered "as suggested" by link:E
ACCEPTABLE = ("set", "add_reminder", "pin_top", "longterm", "link", "add_note")   # a proposal beside the decision (design rule 4)
CLOSES = ("done", "drop")
BASIS_MAX = 240
SAID_WORDS = 12                                           # basis_said: the basis in at most this many words, for speech
TASK_REF = re.compile(r"\bTask-([0-9]{1,9})\b")

TEXTS = {
    "keep_label": "Keep it", "keep_said": "Keep it?",
    "done_label": "Done", "done_said": "Mark it done?",
    "accept_done_label": "Mark it done", "accept_done_said": "Mark it done?",
    "accept_drop_label": "Drop it", "accept_drop_said": "Drop it?",
    "dismiss_label": "Dismiss it", "dismiss_said": "Dismiss it?",
    "new_basis": "It is new, and nothing on it says it is done or not yours.", "new_short": "It is new, and nothing says otherwise.",
    "freed_basis": "What it waited on is done, so it can go ahead now.", "freed_short": "What it waited on is done.",
    "changed_basis": "A linked task changed, and its text was already brought up to date.", "changed_short": "Its text was already brought up to date.",
    "steps_basis": "Every one of its steps is done.", "steps_short": "Every one of its steps is done.",
    "card_basis": "The day it was for has passed.", "card_short": "The day it was for has passed.",
    "link_basis": "The link was suggested from what the two tasks share.",
    "planner_basis": "The planner suggested it.", "ripple_basis": "A ripple suggested it after a linked task changed.",
    "mail_basis": "A new mail looks to be about this task.",
    "this_task": "this task", "another_task": "another task",
    "held": "Not suggesting {what}: {why}.", "held_done": "Done", "held_drop": "Drop",
    "follow_first": "Follow the thread, ", "follow_first_many": "Follow the threads, ",
    "follow_label": "Follow the thread and keep it", "follow_said": "Follow the thread and keep it?",
    "follows_label": "Follow the threads and keep it", "follows_said": "Follow the threads and keep it?",
    "link_1": "Link it", "link_2": "Link both", "link_n": "Link all {n}",
    "link_1_said": "Link it?", "link_2_said": "Link both?", "link_n_said": "Link all {n}?",
    "and_keep": " and keep it", "and_keep_said": " and keep it?",
    "accept_set": "Make the change and keep it", "accept_set_said": "Make that change and keep it?",
    "accept_add_reminder": "Add the reminder and keep it", "accept_add_reminder_said": "Add the reminder and keep it?",
    "accept_pin_top": "Pin it and keep it", "accept_pin_top_said": "Pin it to Top and keep it?",
    "accept_longterm": "Mark it long-term and keep it", "accept_longterm_said": "Mark it long-term and keep it?",
    "accept_link": "Link it and keep it", "accept_link_said": "Link it and keep it?",
    "accept_add_note": "Add the note and keep it", "accept_add_note_said": "Add the note and keep it?",
}


def _sentence(s: str) -> str:
    s = s.strip()
    if not s:
        return s
    s = s[0].upper() + s[1:]
    return s if s[-1] in ".!?" else s + "."


def _lcfirst(s: str) -> str:
    return s[:1].lower() + s[1:]


def _named(text, own: int | None, names: dict) -> str:
    """Model or detector text as one line with every `Task-N` replaced by that task's name ("this task" for the item's
    own, "another task" when the name is not known), capped."""
    def name(m):
        n = int(m.group(1))
        if n == own:
            return TEXTS["this_task"]
        v = (names or {}).get(n)
        return str(v).strip()[:60] if v else TEXTS["another_task"]
    return _clip(TASK_REF.sub(name, one_line(text)).strip())


def _clip(s: str) -> str:
    """At most BASIS_MAX characters, cut between words with an ellipsis."""
    if len(s) <= BASIS_MAX:
        return s
    return s[:BASIS_MAX - 1].rsplit(" ", 1)[0].rstrip(",;:—-") + "…"


SMALL = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "which", "with", "that", "but", "is", "are"}


def _short(basis: str) -> str:
    """The basis in at most SAID_WORDS words, for speech: its first sentence when that is short enough, else its first
    words, never ending on a small word, as a sentence."""
    first = re.split(r"(?<=[.!?])\s", basis.strip(), maxsplit=1)[0]
    if len(first.split()) <= SAID_WORDS:
        return first if len(basis.split()) <= SAID_WORDS else _sentence(first)
    words = [w.rstrip(",;:—-") for w in first.split()[:SAID_WORDS]]
    while len(words) > 1 and (words[-1].lower() in SMALL or not words[-1]):
        words.pop()
    return _sentence(" ".join(words).rstrip(",;:—-.!?…"))


def _rec(answer: str, steps: list[str], label: str, said: str, basis: str, source: str, short: str | None = None) -> dict:
    basis = basis or ""
    return {"answer": answer, "steps": steps, "label": label, "basis": basis, "said": said,
            "basis_said": short if short is not None else _short(basis), "source": source}


def _proposals(entry: dict) -> list[dict]:
    return [e for e in entry.get("evidence") or [] if isinstance(e.get("change"), dict)]


def _proposal_basis(e: dict, own, names) -> str:
    r = _named(e.get("reason"), own, names)
    if r:
        return r
    if (e.get("change") or {}).get("op") == "follow_thread":
        return TEXTS["mail_basis"]
    return TEXTS["ripple_basis" if e.get("by") == "ripple" else "planner_basis"]


def _held_close(answer: str, why: str) -> str:
    return TEXTS["held"].format(what=TEXTS[f"held_{answer}"], why=why)


def _rule(entry: dict, today: date, names: dict) -> tuple[dict | None, str | None]:
    """(the recommendation or None, the sentence that says a close was held back as risky, or None)."""
    key = str(entry.get("key") or "")
    if key.startswith("s:"):                                       # a suggestion card: Dismiss once its day has passed
        f = entry.get("fields") or {}
        d = parse_date(f.get("due")) if isinstance(f.get("due"), str) else None
        if d is not None and d < today:
            return _rec("dismiss", ["dismiss"], TEXTS["dismiss_label"], TEXTS["dismiss_said"], TEXTS["card_basis"], "rule:card_past",
                        TEXTS["card_short"]), None
        return None, None
    if "task" not in entry:
        return None, None
    t = entry["task"] or {}
    own = t.get("id")
    reasons = list(entry.get("reasons") or [])
    sugg = list(entry.get("link_suggestions") or [])
    if any(s.get("kind") == "same_as" for s in sugg):              # rule 2: a merge is always asked
        return None, None
    props = _proposals(entry)
    follows = [e for e in props if e["change"].get("op") == "follow_thread"]
    others = [e for e in props if e["change"].get("op") != "follow_thread"]
    if len(others) >= 2:                                           # rule 2: proposals that disagree, or at least compete
        return None, None
    risky = risky_close(t, today)

    if props:                                                      # rule 4: what was proposed, already paid for
        if sugg:                                                   # a link question too: Keep would leave it, so the owner says
            return None, None
        steps = [f"follow:{e['id']}" for e in follows]
        lead = TEXTS["follow_first_many" if len(follows) > 1 else "follow_first"] if follows else ""
        if not others:
            many = len(follows) > 1
            return _rec("follow_keep", steps + ["keep"], TEXTS["follows_label" if many else "follow_label"], TEXTS["follows_said" if many else "follow_said"],
                        _proposal_basis(follows[0], own, names), "proposal:follow_thread"), None
        e = others[0]
        op = e["change"].get("op")
        basis = _proposal_basis(e, own, names)
        if op in CLOSES:
            if risky:
                return None, _held_close(op, risky)
            label, said = TEXTS[f"accept_{op}_label"], TEXTS[f"accept_{op}_said"]
            if lead:
                label, said = lead + _lcfirst(label), lead + _lcfirst(said)
            return _rec(op, steps + [f"accept:{e['id']}"], label, said, basis, f"proposal:{op}"), None
        if op not in ACCEPTABLE:                                   # a merge, a reopen, anything the rule would not stage beside
            return None, None
        label, said = TEXTS[f"accept_{op}"], TEXTS[f"accept_{op}_said"]
        if lead:
            label, said = lead + _lcfirst(label), lead + _lcfirst(said)
        return _rec("accept_keep", steps + [f"accept:{e['id']}", "keep"], label, said, basis, f"proposal:{op}"), None

    primary = entry.get("primary") or (reasons[0] if reasons else None)
    if primary == "link_suggested":                                # rule 5: as suggested, plus Keep when another reason needs a decision
        if not sugg or any(s.get("kind") not in LINKABLE for s in sugg):
            return None, None
        n = len(sugg)
        head = TEXTS["link_1"] if n == 1 else TEXTS["link_2"] if n == 2 else TEXTS["link_n"].format(n=n)
        ask = TEXTS["link_1_said"] if n == 1 else TEXTS["link_2_said"] if n == 2 else TEXTS["link_n_said"].format(n=n)
        whys = []
        for s in sugg:
            w = _named(s.get("why"), own, names)
            if w and _sentence(w) not in whys:
                whys.append(_sentence(w))
        basis = " ".join(whys)[:BASIS_MAX] or TEXTS["link_basis"]
        steps = [f"link:{s['evidence_id']}" for s in sugg]
        if any(r != "link_suggested" for r in reasons):
            return _rec("link_keep", steps + ["keep"], head + TEXTS["and_keep"], ask[:-1] + TEXTS["and_keep_said"], basis, "rule:link"), None
        return _rec("link", steps, head, ask, basis, "rule:link"), None
    if primary == "new":
        return _rec("keep", ["keep"], TEXTS["keep_label"], TEXTS["keep_said"], TEXTS["new_basis"], "rule:new", TEXTS["new_short"]), None
    if primary == "freed":
        return _rec("keep", ["keep"], TEXTS["keep_label"], TEXTS["keep_said"], TEXTS["freed_basis"], "rule:freed", TEXTS["freed_short"]), None
    if primary == "linked_changed":
        since = entry.get("since") or {}
        if since.get("text") and not since.get("undone") and not since.get("cause_undone"):
            return _rec("keep", ["keep"], TEXTS["keep_label"], TEXTS["keep_said"], TEXTS["changed_basis"], "rule:linked_changed", TEXTS["changed_short"]), None
        return None, None
    if primary == "steps_done":
        if risky:
            return None, _held_close("done", risky)
        return _rec("done", ["done"], TEXTS["done_label"], TEXTS["done_said"], TEXTS["steps_basis"], "rule:steps_done", TEXTS["steps_short"]), None
    return None, None                                              # rule 6: the owner's answer is not known


def recommend(entry: dict, today: date, names: dict | None = None) -> dict | None:
    """The recommended answer on one Review item (a task item or a suggestion card as `review_queue.items` ships it):
    {answer, steps, label, basis, said, basis_said, source}, or None. `names`: {task id: name} for a `Task-N` a basis
    would name. Whether recommendations are on (`review.recommend`) is the caller's to ask. Pure."""
    return _rule(entry, today, names or {})[0]


def held(entry: dict, today: date) -> str | None:
    """When the rules would have recommended a close (Done, Drop, an accepted done or drop) but `risky_close` gives a
    reason, the sentence that says so ("Not suggesting Done: it is on Top."); else None. Pure."""
    return _rule(entry, today, {})[1]


def names_for(conn, entries: list[dict]) -> dict[int, str]:
    """{task id: name} for every task a basis may name: the queue's own tasks, the other ends of their suggested links,
    and every `Task-N` in a proposal's reason or a suggestion's why. One query for the ones not in hand. Reads only."""
    from .relations import briefs
    out = {e["task"]["id"]: e["task"].get("name") for e in entries if isinstance(e.get("task"), dict) and e["task"].get("id") is not None}
    want = set()
    for e in entries:
        for s in e.get("link_suggestions") or []:
            if s.get("other_name"):
                out.setdefault(s["other"], s["other_name"])
            want.update(int(n) for n in TASK_REF.findall(str(s.get("why") or "")))
        for ev in e.get("evidence") or []:
            want.update(int(n) for n in TASK_REF.findall(str(ev.get("reason") or "")))
    want -= set(out)
    for i, b in briefs(conn, want).items():
        out[i] = b.get("name")
    return {k: v for k, v in out.items() if v}
