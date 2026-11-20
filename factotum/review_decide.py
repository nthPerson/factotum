"""The decision rule of Review (spoken Review, stage 1, design §5.2): what one option on one Review item stages
in the tray. The page's buttons, its keys, a card's conversation and the connector all go through `plan`, so
there is one rule and it is under pytest. `plan` reads only and writes nothing: it returns the tray rows to
put (each with the item key it decides, or None when it stands beside the decision) and the keys to delete.

Every entry is what the page staged for the same press (`reviewAct`, `linkSuggestionAct`, the `[data-rv-accept]`,
`[data-follow]` and `[data-notabout]` closures in `wire()`, `act`, `stageDue`, `stageReminder`), key order
included, so a row staged here and a row staged by the page are the same row. Two things differ on purpose: Pin
and Long-term stand beside under `<id>:top` / `<id>:lt` (never under the bare id, which is the task's decision),
and Later needs a day after Review's day."""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import batches, relations, review_cards, review_queue
from .db import begin_write

OPTIONS = {"t": ("done", "drop", "keep", "later", "accept", "dismiss", "follow", "not_about", "link", "step",
                 "waits", "same", "merge", "not_related", "pin", "longterm", "due", "remind", "undecide"),
           "s": ("create", "dismiss", "undecide")}
LATER_WORDS = {"tomorrow": "tomorrow", "fri": "Friday", "nextweek": "next week"}
OUTCOME_MAX = 300                        # batches cuts an outcome there
NOTE_MAX = 2000

FROM_CHAT_ARGS = ("prop", "also")       # args only from_proposal supplies: `stage` and every route drop them from what a caller sends
WITH_E = ("accept", "follow", "not_about", "link", "step", "waits", "same", "merge", "not_related")   # name evidence: "accept:190"
LINK_ANSWERS = ("link", "step", "waits", "same", "merge", "not_related")
ACCEPT_BESIDE = ("pin_top", "longterm", "add_reminder", "link", "unlink", "merge", "add_note", "set")

# The page's toasts for a press ("" = none). Speech builds its own words (stage 2).
SAID = {
    "long_note": f"The note is longer than an outcome ({OUTCOME_MAX} characters), so it goes to the task's text instead",
    "accepted_decision": "Decision staged · Apply in the tray",
    "accepted_beside": "Suggestion staged · decide on the card, then Apply",
    "follow": "Staged · it follows the thread once you Apply",
    "link": "Link staged · decide on the card, then Apply",
    "due": "Due date staged · press Apply in the tray to save it",
    "due_removed": "Removal of the due date staged · press Apply in the tray to save it",
    "remind": "Reminder staged · press Apply in the tray to save it",
    "create": "New task staged · press Apply in the tray to make it",
}
# The fixed labels a staged row carries (the page's words); the rest are built by the label helpers below.
LABELS = {
    "done": "done", "drop": "dropped", "keep": "kept", "later": "later · back ",
    "pinned": "pinned", "unpinned": "unpinned", "longterm": "long-term", "not_longterm": "not long-term",
    "due_removed": "due date removed", "suggestion_seen": "suggestion seen", "thread_seen": "thread seen",
    "accepted": "suggestion accepted", "dismissed": "suggestion dismissed", "mail_accepted": "mail card accepted",
    "not_about": "not about this mail", "follows": "follows the mail thread", "not_same": "not the same as Task-",
    "not_related": "not related to Task-", "new_task": "new task: ",
}


class Gone(Exception):
    """Not in the queue: the task is not open, the card was answered, the key names nothing. → 404"""


class Refused(ValueError):
    """An option the item does not offer, or bad arguments. → 400"""


class Stale(Exception):
    """The evidence or suggestion was answered since the item was read. → 409"""


@dataclass
class Plan:
    puts: list[tuple[str, dict, str | None]] = field(default_factory=list)   # (tray key, entry, decides) in the order the page staged them
    dels: list[str] = field(default_factory=list)
    said: str = ""                                                            # the page's toast ("" = none)


# --- keys and options ------------------------------------------------------------------------------------

_ITEM_RE = re.compile(r"([ts]):([1-9][0-9]{0,15})")
_E_RE = re.compile(r"[1-9][0-9]{0,15}")


def item_key(item: dict) -> str:
    """`t:<task id>` for a task item, `s:<evidence id>` for a suggestion card."""
    if item.get("key"):
        return str(item["key"])
    return f"t:{item['task']['id']}" if "task" in item else f"s:{item['id']}"


def parse_item_key(s) -> tuple[str, int]:
    """("t", 412) or ("s", 186). A group key (`g:…`) never reaches the rule: Refused, as is anything else."""
    m = _ITEM_RE.fullmatch(str(s or "")) if isinstance(s, str) else None
    if not m:
        raise Refused(f"not an item key: {str(s)[:40]!r}")
    return m.group(1), int(m.group(2))


def parse_option(s) -> tuple[str, int | None]:
    """"accept:190" → ("accept", 190); "done" → ("done", None). An option that names evidence must name it;
    one that does not may not (`dismiss` may: on a task it names the proposal, on a card nothing)."""
    if not isinstance(s, str) or not s:
        raise Refused("no option given")
    name, sep, rest = s.partition(":")
    if name not in {o for opts in OPTIONS.values() for o in opts}:
        raise Refused(f"unknown option {s[:40]!r}")
    if sep:
        if name not in WITH_E and name != "dismiss":
            raise Refused(f"{name} names no evidence")
        if not _E_RE.fullmatch(rest):
            raise Refused(f"not an evidence id: {rest[:40]!r}")
        return name, int(rest)
    if name in WITH_E:
        raise Refused(f"{name} needs the evidence it answers, as {name}:<id>")
    return name, None


# --- the page's words ------------------------------------------------------------------------------------

_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def cut60(s) -> str:
    """The page's cut60: at most 60 UTF-16 units (JavaScript's length), then "…"."""
    s = "" if s is None else str(s)
    u = s.encode("utf-16-le")
    return s if len(u) <= 120 else u[:120].decode("utf-16-le", errors="ignore") + "…"


def day_words(iso) -> str:
    """"Fri Nov 14" (the page's dayWords); what it cannot read comes back as it was."""
    try:
        d = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return str(iso)
    return f"{_DAYS[d.weekday()]} {_MONTHS[d.month - 1]} {d.day}"


def when_words(iso) -> str:
    """"Fri 9:00 AM" (the page's fmtWhen), the time as written."""
    try:
        d = datetime.fromisoformat(str(iso))
    except ValueError:
        return str(iso)
    return f"{_DAYS[d.weekday()]} {d.hour % 12 or 12}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'}"


def link_predicate(kind, other, removed: bool) -> str:
    """Read with the source task in front: "now waits on Task-14" (the page's linkPredicate)."""
    t = f"Task-{other}"
    if kind == "waits_on":
        return f"no longer waits on {t}" if removed else f"now waits on {t}"
    if kind == "part_of":
        return f"is no longer part of {t}" if removed else f"is now part of {t}"
    if kind == "same_as":
        return f"is no longer merged into {t}" if removed else f"merged into {t}"
    return ""


def change_words(c: dict) -> str:
    """A change in words, or "" when the caller's own label says it better (the page's changeWords)."""
    f = c.get("fields") or {}
    op = c.get("op")
    if op == "link":
        return link_predicate(f.get("kind"), f.get("other"), False)
    if op == "unlink":
        return link_predicate(f.get("kind"), f.get("other"), True)
    if op == "merge":
        return link_predicate("same_as", f.get("into"), False)
    if op == "add_note":
        return f"note “{cut60(f.get('text'))}”"
    if op in ("done", "drop") and f.get("outcome"):
        return f"{'done' if op == 'done' else 'dropped'} · “{cut60(f['outcome'])}”"
    return ""


def _js(v) -> str:
    return "true" if v is True else "false" if v is False else str(v)


def set_words(f: dict | None) -> str:
    """A `set` in words: "due Fri Nov 14, project Personal" (the page's setWords)."""
    out = []
    for k, v in (f or {}).items():
        if k == "due":
            out.append(f"due {day_words(v)}" if v else "due date removed")
        elif k == "name":
            out.append(f"name “{cut60(v)}”")
        elif k == "context":
            out.append("text rewritten")
        elif k == "project":
            out.append(f"project {v or 'none'}")
        elif k == "effort":
            out.append(f"effort {v or 'none'}")
        elif k == "hard":
            out.append("hard deadline" if v else "soft date")
        elif k == "waiting_on":
            out.append(f"waiting on {v}" if v else "no longer waiting")
        elif k == "counterpart":
            out.append(f"for {v}" if v else "for nobody named")
        elif k == "snoozed_until":
            out.append(f"later, back {day_words(v)}" if v else "back now")
        elif k == "why_now":
            out.append(f"why now “{cut60(v)}”" if v else "why now cleared")
        elif k != "due_is_datetime":
            out.append(f"{k} {'cleared' if v is None else cut60(_js(v))}")
    return ", ".join(out)


def stage_key(change: dict, suffix: str | None = None) -> str:
    """Where a change sits in the tray (the page's stageKey, as it is once Pin and Long-term stand beside): a
    link by both ends and its kind, a merge as `same_as`, a note under `<id>:<suffix or note>`, a reminder
    `<id>:r`, a verdict `e:<evidence>`, a new task `new:<suffix>`; with no suffix a pin or unpin (or a `set` of
    `top_rank` alone) `<id>:top` and long-term (or a `set` of `longterm` alone) `<id>:lt`; the rest `<id>:<suffix>`
    or the bare id, which is the task's decision."""
    tid, f, op = change.get("task_id"), change.get("fields") or {}, change.get("op")
    if op in ("link", "unlink"):
        return f"rel:{tid}:{f.get('kind')}:{f.get('other')}"
    if op == "merge":
        return f"rel:{tid}:same_as:{f.get('into')}"
    if op == "add_note":
        return f"{tid}:{suffix or 'note'}"
    if op == "add_reminder":
        return f"{tid}:r"
    if op == "judge_evidence":
        return f"e:{f.get('evidence_id')}"
    if op == "create":
        if not suffix:
            raise ValueError("a new task's key needs a suffix")
        return f"new:{suffix}"
    if suffix:
        return f"{tid}:{suffix}"
    if op in ("pin_top", "unpin") or (op == "set" and set(f) == {"top_rank"}):
        return f"{tid}:top"
    if op == "longterm" or (op == "set" and set(f) == {"longterm"}):
        return f"{tid}:lt"
    return str(tid)


def text_expect(task: dict | None, fields: dict | None) -> dict | None:
    """The text a staged edit of `context` or `name` started from (the server's value, "" for none), so Apply
    skips it when the text changed meanwhile (the page's textExpect)."""
    if not task or not fields:
        return None
    out = {k: ("" if task.get(k) is None else task.get(k)) for k in ("context", "name") if k in fields}
    return out or None


def later_choices(today: date) -> list[dict]:
    """Later's three days from Review's day, as the page computes them: tomorrow, the next Friday (a week on,
    from a Friday) and the next Monday."""
    return [{"id": "tomorrow", "until": (today + timedelta(days=1)).isoformat(), "label": LATER_WORDS["tomorrow"]},
            {"id": "fri", "until": (today + timedelta(days=(4 - today.weekday()) % 7 or 7)).isoformat(), "label": LATER_WORDS["fri"]},
            {"id": "nextweek", "until": (today + timedelta(days=7 - today.weekday())).isoformat(), "label": LATER_WORDS["nextweek"]}]


# --- items -----------------------------------------------------------------------------------------------

def load_item(conn: sqlite3.Connection, cfg: dict, today: date, key: str) -> dict:
    """One item by key, built as Review's queue builds it (`review_queue.task_item`, `review_cards.card`). A
    task must be open; it is an item even with no reason to be on Review today. Raises Gone. Reads only."""
    kind, n = parse_item_key(key)
    if kind == "t":
        item = review_queue.task_item(conn, cfg, today, n)
        if item is None:
            raise Gone(f"Task-{n} is not open")
        return item
    card = review_cards.card(conn, cfg, n)
    if card is None:
        raise Gone("that suggestion was answered, or is not a suggestion card")
    return card


def stored_proposal(conn: sqlite3.Connection, task_id: int, message_id, index) -> dict:
    """The proposal a reply on a task's Review card made, as the server stored it (never as the page sends it).
    Refused unless the message is an answer in that task's own thread (origin `review` or `task`, which share one
    thread per task) and the index names a proposal."""
    if type(message_id) is not int or type(index) is not int or index < 0:
        raise Refused("from_chat needs a message id and an index")
    m = conn.execute("SELECT m.proposals_json, m.role, t.origin, t.task_id FROM chat_messages m JOIN chat_threads t ON t.id = m.thread_id "
                     "WHERE m.id = ?", (message_id,)).fetchone()
    if not m or m["role"] != "assistant" or m["origin"] not in ("review", "task") or m["task_id"] != task_id:
        raise Refused(f"that reply is not in the conversation about Task-{task_id}")
    try:
        props = json.loads(m["proposals_json"] or "[]")
    except ValueError:
        props = []
    if not isinstance(props, list) or index >= len(props) or not isinstance(props[index], dict):
        raise Refused("that reply has no such proposal")
    return props[index]


def from_proposal(item: dict, proposal: dict, message_id: int, index: int, today: date) -> list[tuple[str, dict]] | None:
    """The options a proposal of a reply on a Review card stands for, when it is a decision about that card's own
    task: `done` / `drop` (its outcome as the note), a `set` whose `snoozed_until` is after Review's day (`later`;
    the set's other fields stand beside under `<id>:c<message>-<index>`), `pin_top` (`pin`), `longterm`. Anything
    else is None: the page stages it as it does any proposal. Every option carries `prop`, so the rows it stages
    remember the proposal."""
    if "task" not in item or not isinstance(proposal, dict) or proposal.get("task_id") != item["task"]["id"]:
        return None
    op, f, prop = proposal.get("op"), proposal.get("fields") or {}, f"{message_id}-{index}"
    if op in ("done", "drop"):
        out = f.get("outcome")
        return [(op, dict({"note": out} if isinstance(out, str) and out.strip() else {}, prop=prop))]
    if op == "pin_top":
        return [("pin", {"on": True, "prop": prop})]
    if op == "longterm":
        return [("longterm", {"on": True, "prop": prop})]
    if op == "set":
        try:
            until = date.fromisoformat(str(f.get("snoozed_until") or "")[:10])
        except ValueError:
            return None
        if until <= today:
            return None
        rest = {k: v for k, v in f.items() if k != "snoozed_until"}
        return [("later", dict({"until": until.isoformat(), "prop": prop}, **({"also": rest} if rest else {})))]
    return None


# --- the rule --------------------------------------------------------------------------------------------

def _tray_rows(tray) -> list[tuple[str, dict, dict]]:
    """`tray.read`'s entries as (key, entry, meta)."""
    out = []
    for r in (tray or {}).get("entries") or []:
        k, e, m = (list(r) + [None, None])[:3]
        out.append((str(k), e if isinstance(e, dict) else {}, m if isinstance(m, dict) else {}))
    return out


def _note(args: dict) -> str:
    n = args.get("note")
    if n is None:
        return ""
    if not isinstance(n, str):
        raise Refused("a note is text")
    n = n.strip()
    if len(n) > NOTE_MAX:
        raise Refused(f"a note is at most {NOTE_MAX} characters")
    return n


def _until(args: dict, today: date) -> tuple[str, str]:
    """Later's day and the label's word: one of LATER_WORDS, or an ISO date after Review's day (its label is the date)."""
    u = args.get("until")
    if isinstance(u, str) and u in LATER_WORDS:
        c = next(x for x in later_choices(today) if x["id"] == u)
        return c["until"], c["label"]
    try:
        d = date.fromisoformat(u) if isinstance(u, str) and len(u) == 10 else None
    except ValueError:
        d = None
    if d is None:
        raise Refused("later needs until: tomorrow, fri, nextweek or a date")
    if d <= today:
        raise Refused("later needs a day after today")
    return d.isoformat(), d.isoformat()


def _on(args: dict, now: bool) -> bool:
    on = args.get("on")
    if on is None:
        return not now
    if not isinstance(on, bool):
        raise Refused("on is true or false")
    return on


def plan(conn: sqlite3.Connection, cfg: dict, item: dict, option: str, args: dict | None, tray: dict, today: date) -> Plan:
    """What `option` on `item` stages, against the tray as `tray.read` returns it ({"rev", "entries": [[key,
    entry, meta], ...]}). A decision (done, drop, keep, later, an accepted done or drop, a card's create or
    dismiss) puts rows that decide the item, judges the item's pending evidence that nothing staged answers yet,
    and deletes the rows of an earlier decision of the same item that it did not put again; what stands beside
    stays. `undecide` deletes the rows that decide the item. `link` on a same_as suggestion is its merge (as
    suggested). FROM_CHAT_ARGS are honoured as given: only `from_proposal` may supply them, so a caller strips
    them from anything a client sent. Raises Refused and Stale. Reads only."""
    mine = item_key(item)
    kind, n = parse_item_key(mine)
    name, E = parse_option(option)
    if name not in OPTIONS[kind] or (kind == "t" and name == "dismiss" and E is None) or (kind == "s" and E is not None):
        raise Refused(f"{option} is not offered on this item")
    args = dict(args or {})
    rows = _tray_rows(tray)
    whole = {k: e for k, e, _ in rows}
    T = {k: e for k, e, m in rows if m.get("decides") != mine}           # a second decision replaces the first
    old = [k for k, _, m in rows if m.get("decides") == mine]
    p = Plan()

    def put(key, entry, decides=None):
        for i, (k, _, _) in enumerate(p.puts):
            if k == key:                                                    # staged again: it keeps its place
                p.puts[i] = (key, entry, decides)
                return
        p.puts.append((key, entry, decides))

    def judge(eid, tid, verdict, label, decides=None):
        put(f"e:{eid}", {"task_id": tid, "op": "judge_evidence", "fields": {"evidence_id": eid, "verdict": verdict}, "label": label}, decides)

    def decided():                                                          # what the earlier decision put and this one did not
        put_keys = {k for k, _, _ in p.puts}
        p.dels.extend(k for k in old if k not in put_keys)
        return p

    if name == "undecide":
        p.dels.extend(old)
        return p

    if kind == "s":
        if name == "dismiss":
            judge(n, None, "dismissed", LABELS["dismissed"], mine)
            return decided()
        draft, label_name = _draft(conn, cfg, item, args.get("draft"))
        put(f"e:{n}", {"task_id": None, "op": "create_suggestion", "fields": dict({"evidence_id": n}, **({"draft": draft} if draft else {})),
                       "label": LABELS["new_task"] + cut60(label_name)}, mine)
        p.said = SAID["create"]
        return decided()

    task = item["task"]
    tid = task["id"]
    evidence = item.get("evidence") or []
    prop = args.get("prop")

    def with_prop(entry):
        return dict(entry, prop=prop) if prop else entry

    def judges(skip=None):
        for e in evidence:
            if e["id"] != skip and f"e:{e['id']}" not in T:
                judge(e["id"], tid, "reviewed", LABELS["suggestion_seen"] if e["kind"] == "proposal" else LABELS["thread_seen"], mine)

    if name in ("done", "drop", "keep", "later"):
        text = _note(args)
        closes = name in ("done", "drop")
        long = closes and len(text) > OUTCOME_MAX
        if closes and text and not long:
            c = {"task_id": tid, "op": name, "fields": {"outcome": text}}
            put(str(tid), with_prop({**c, "label": change_words(c)}), mine)
        elif closes:
            put(str(tid), with_prop({"task_id": tid, "op": name, "fields": {}, "label": LABELS[name]}), mine)
        elif name == "keep":
            put(str(tid), with_prop({"task_id": tid, "op": "keep", "fields": {}, "label": LABELS["keep"]}), mine)
        else:
            day, word = _until(args, today)
            put(str(tid), with_prop({"task_id": tid, "op": "set", "fields": {"snoozed_until": day, "last_reviewed_at": today.isoformat()},
                                     "label": LABELS["later"] + word}), mine)
            also = args.get("also")
            if also:                                                        # a proposal's other fields, beside its Later
                if not isinstance(also, dict) or not prop:
                    raise Refused("also comes only with a proposal")
                ex = text_expect(task, also)
                put(f"{tid}:c{prop}", dict({"task_id": tid, "op": "set", "fields": also, "label": set_words(also) or "changed"},
                                           **({"expect": ex} if ex else {}), prop=prop))
        if text and (not closes or long):
            c = {"task_id": tid, "op": "add_note", "fields": {"text": text}}
            put(f"{tid}:note", {**c, "label": change_words(c)}, mine)
        if long:
            p.said = SAID["long_note"]
        judges()
        return decided()

    if name in ("accept", "dismiss", "follow", "not_about"):
        e = next((x for x in evidence if x["id"] == E), None)
        if e is None:
            raise Stale(f"that suggestion on Task-{tid} was answered since")
        change = e.get("change") if isinstance(e.get("change"), dict) else None
        if name == "dismiss":
            judge(E, tid, "dismissed", LABELS["dismissed"])
            return p
        if name in ("follow", "not_about"):
            if not change or change.get("op") != "follow_thread":
                raise Refused("that is not a mail about this task")
            if name == "not_about":
                judge(E, tid, "dismissed", LABELS["not_about"])
                return p
            subject = (change.get("mail") or {}).get("subject") or (change.get("fields") or {}).get("subject") or ""
            put(f"{tid}:f{E}", {"task_id": tid, "op": "follow_thread", "fields": change.get("fields") or {},
                                "label": f"follows “{subject}”" if subject else LABELS["follows"], "ev": E})
            judge(E, tid, "applied", LABELS["mail_accepted"])
            p.said = SAID["follow"]
            return p
        if not change:
            raise Refused("that evidence proposes nothing to accept")
        c = {"task_id": tid, **change}
        op, cf = c.get("op"), c.get("fields") or {}
        if op in ("done", "drop"):                                          # this IS the decision: the card's other flags are judged with it
            put(str(tid), {"task_id": tid, "op": op, "fields": cf, "label": change_words(c) or e["label"]}, mine)
            judges(skip=E)
            judge(E, tid, "applied", LABELS["accepted"], mine)
            p.said = SAID["accepted_decision"]
            return decided()
        if op not in ACCEPT_BESIDE:
            raise Refused("this suggestion cannot be staged here")
        if op in ("pin_top", "longterm"):
            put(f"{tid}:a{E}", {"task_id": tid, "op": op, "fields": {}, "label": e["label"]})
        elif op == "add_reminder":
            put(f"{tid}:r", {"task_id": tid, "op": "add_reminder", "fields": cf, "label": e["label"]})
        elif op in ("link", "unlink", "merge", "add_note"):
            put(stage_key(c, f"a{E}"), {"task_id": c.get("task_id"), "op": op, "fields": cf, "label": change_words(c) or e["label"]})
        else:
            ex = c.get("expect") or text_expect(task, c.get("fields"))
            put(f"{tid}:c{E}", dict({"task_id": tid, "op": "set", "fields": cf, "label": e["label"]}, **({"expect": ex} if ex else {})))
        judge(E, tid, "applied", LABELS["accepted"])
        p.said = SAID["accepted_beside"]
        return p

    if name in LINK_ANSWERS:
        s = next((x for x in item.get("link_suggestions") or [] if x["evidence_id"] == E), None)
        if s is None:
            raise Stale(f"that suggested link on Task-{tid} was answered since")
        N = s["other"]
        # A second answer to one suggestion replaces the first (the page hides an answered one; a voice may change its mind).
        p.dels.extend(k for k, e in whole.items() if (e.get("ev") == E or k == f"e:{E}"))
        if name == "not_related":
            judge(E, tid, "dismissed", (LABELS["not_same"] if s["kind"] == "same_as" else LABELS["not_related"]) + str(N))
        else:
            if name == "link" and s["kind"] == "same_as":                     # as suggested: the merge, with the suggestion's survivor
                name = "merge"
            if name == "link":
                if s["kind"] not in relations.LINK_KINDS:
                    raise Refused("this suggestion names no kind of link: say step, waits, same or not related")
                out = s.get("dir") == "out"
                c = {"task_id": tid if out else N, "op": "link", "fields": {"kind": s["kind"], "other": N if out else tid}}
            elif name in ("step", "waits"):
                c = {"task_id": tid, "op": "link", "fields": {"kind": "part_of" if name == "step" else "waits_on", "other": N}}
            else:
                keep = s["keep"] if name == "merge" and s.get("keep") is not None else relations.default_survivor(conn, tid, N)[0]
                c = {"task_id": N if keep == tid else tid, "op": "merge", "fields": {"into": keep}}
            put(stage_key(c), {**c, "label": change_words(c), "ev": E})
            p.said = SAID["link"]
        put_keys = {k for k, _, _ in p.puts}
        p.dels = [k for k in p.dels if k not in put_keys]
        return p

    if name in ("pin", "longterm"):
        key = f"{tid}:top" if name == "pin" else f"{tid}:lt"
        st = whole.get(key)
        if name == "pin":
            row = bool(task.get("top_rank"))
            now = row if st is None else (st.get("op") == "pin_top" or (st.get("op") == "set" and bool((st.get("fields") or {}).get("top_rank"))))
        else:
            row = bool(task.get("longterm"))
            now = row if st is None else (st.get("op") == "longterm" or bool((st.get("fields") or {}).get("longterm")))
        on = _on(args, now)
        if on == row:                                                       # as the task is: nothing to stage
            if st is not None:
                p.dels.append(key)
            return p
        if name == "pin":
            put(key, with_prop({"task_id": tid, "op": "pin_top" if on else "unpin", "fields": {}, "label": LABELS["pinned" if on else "unpinned"]}))
        else:
            put(key, with_prop({"task_id": tid, "op": "set", "fields": {"longterm": 1 if on else 0}, "label": LABELS["longterm" if on else "not_longterm"]}))
        return p

    if name == "due":
        if "due" not in args:
            raise Refused("due needs due: a date or null")
        due = args["due"] or None
        if due is not None:
            try:
                datetime.fromisoformat(str(due))
            except ValueError:
                raise Refused(f"due {str(due)[:40]!r} is not a date") from None
            due = str(due)
        put(f"{tid}:due", {"task_id": tid, "op": "set", "fields": {"due": due}, "label": f"due {day_words(due)}" if due else LABELS["due_removed"]})
        p.said = SAID["due"] if due else SAID["due_removed"]
        return p

    # remind
    label = args.get("label")
    label = label.strip() if isinstance(label, str) else ""
    try:
        f = batches._reminder_fields({"fields": {"fire_at": args.get("fire_at"), "label": label or None, "repeat": args.get("repeat") or None}})
    except ValueError as e:
        raise Refused(str(e)) from None
    put(f"{tid}:r", {"task_id": tid, "op": "add_reminder", "fields": f,
                     "label": f"remind {when_words(f['fire_at'])}{' · ' + f['repeat'] if f['repeat'] else ''}{' · ' + label if label else ''}"})
    p.said = SAID["remind"]
    return p


def _draft(conn: sqlite3.Connection, cfg: dict, card: dict, draft) -> tuple[dict | None, str]:
    """A card's draft as Create will use it (`chat.clean_draft`, as the Create route cleans it) and the name the
    tray shows. Refused for a draft that is not one, or whose name is given and empty."""
    from . import chat                                      # lazy: chat pulls in the MCP server and the runner
    if draft is None:
        return None, (card.get("fields") or {}).get("name") or ""
    if not isinstance(draft, dict):
        raise Refused("a draft is {fields, links}")
    fields, links = draft.get("fields"), draft.get("links")
    if (fields is not None and not isinstance(fields, dict)) or (links is not None and not isinstance(links, list)):
        raise Refused("a draft is {fields, links}")
    over = chat.clean_draft(conn, cfg, dict(fields or {}, **({"links": links} if links is not None else {})))
    if fields and "name" in fields and not over.get("name"):
        raise Refused("a task needs a name")
    out = {}
    if fields:
        out["fields"] = {k: v for k, v in over.items() if k != "links"}
    if links is not None:
        out["links"] = over.get("links") or []
    name = (out.get("fields") or {}).get("name") or (card.get("fields") or {}).get("name") or ""
    return out or None, name


def stage(conn: sqlite3.Connection, cfg: dict, key: str, option, args: dict | None, today: date, *, origin: str,
          device: str | None = None, from_chat: dict | None = None) -> dict:
    """One decision (or a change beside it) on one Review item into the tray: take the write lock, load the item,
    read the tray, plan, write. With `from_chat` ({message_id, index}) the option is the stored proposal's
    (`stored_proposal`, `from_proposal`): the option and arguments sent with it are ignored, and a proposal that is
    not a decision on this card's own task is Refused (the page stages it as any proposal). FROM_CHAT_ARGS sent by
    a caller are dropped. Answers {"keys": the keys put, "removed": the keys deleted, "said"}. Raises Gone, Refused,
    Stale and tray's ValueError. No commit."""
    from . import tray                                      # tray imports chat lazily; review_decide stays light
    begin_write(conn)
    item = load_item(conn, cfg, today, key)
    if from_chat is not None:
        if "task" not in item or not isinstance(from_chat, dict):
            raise Refused("a reply decides only the task its card is about")
        mid, idx = from_chat.get("message_id"), from_chat.get("index")
        opts = from_proposal(item, stored_proposal(conn, item["task"]["id"], mid, idx), mid, idx, today)
        if not opts:
            raise Refused("that proposal is not a decision on this card")
        (option, args), = opts
    else:
        args = {k: v for k, v in (args or {}).items() if k not in FROM_CHAT_ARGS}
    p = plan(conn, cfg, item, option, args, tray.read(conn), today)
    ops = [{"del": k} for k in p.dels] + [{"put": k, "entry": e, "decides": d} for k, e, d in p.puts]
    tray.write(conn, ops, origin, device)
    return {"keys": [k for k, _, _ in p.puts], "removed": list(p.dels), "said": p.said}
