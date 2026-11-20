"""Review's count, defined once (stage 8): the number beside "Review", the number on the app's icon and
the number a push carries all come from here, and GET /api/review builds its queue and its cards through
the same functions. Reads only. GET /api/review also writes (the day's link-suggestion session, the
offered stamp `expire` acts on), so it is never called just for the number."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import config as C, lifecycle, relations
from .dates import parse_date
from .db import get_settings, rows

SUGGESTION_KINDS = ("proposal", "slack_mention")      # evidence with no task that Review offers as a Create / Dismiss card
URGENT_DAYS = 1                                       # a hard date this many days away, or past, puts an item first (design 6.7, tier 0)
CARD_SOON_DAYS = 7                                    # a card due within this many days ranks among the tasks; later or undated, after them
SLACK_LABEL = re.compile(r"^#([^ ·]{1,80}) · ([^:]{1,80}): (.{0,200})$")     # slack_sweep's label: "#<channel> · <poster>: <text>"
MAIL_KINDS = ("thread_updated", "reply_from_waiting", "maybe_reply_from_waiting")
OWNER_SOURCES = ("ui", "review", "command", "chat", "skill", "connector")   # batch sources that are the owner's own hand: a reopen there is not Back


def back_flags(conn, cfg: dict | None = None) -> dict[int, str]:
    """{task id: the day it came back}: the latest change of `status` from faded or lapsed to open in a batch that
    was not undone and is not the owner's own (OWNER_SOURCES): a recording (`plaud`), mail, a ripple. The day is the
    batch's, where the owner is when `cfg` is given (else UTC). `changes` holds JSON text, so the values are quoted."""
    marks = ",".join("?" * len(OWNER_SOURCES))
    got = conn.execute("SELECT c.task_id, MAX(b.applied_at) FROM changes c JOIN batches b ON b.id = c.batch_id"
                       " WHERE c.field = 'status' AND c.old IN ('\"faded\"', '\"lapsed\"') AND c.new = '\"open\"'"
                       f" AND b.undone_at IS NULL AND b.applied_at IS NOT NULL AND b.source NOT IN ({marks}) GROUP BY c.task_id",
                       OWNER_SOURCES).fetchall()
    tz = None
    if cfg is not None:
        try:
            tz = ZoneInfo(str(C.get(cfg, "owner.timezone", "UTC")))
        except (KeyError, ValueError):
            tz = None
    out: dict[int, str] = {}
    for tid, at in got:
        try:
            d = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
        except ValueError:
            continue
        if tz is not None and d.tzinfo is not None:
            d = d.astimezone(tz)
        out[int(tid)] = d.date().isoformat()
    return out


def flags(conn, cfg: dict | None = None) -> dict[int, dict]:
    """What Review says beside the lifecycle's own reasons, by task id: mail_sweep.review_flags (the sweeps' and the
    planner's evidence) with `back` (back_flags) merged in. Reads only."""
    from .mail_sweep import review_flags
    out = review_flags(conn)
    for tid, day in back_flags(conn, cfg).items():
        out.setdefault(tid, {})["back"] = day
    return out


_flags = flags                                        # `queue`'s argument of the same name hides it there


def queue(conn, cfg: dict, today: date, open_tasks: list[dict] | None = None, flags: dict | None = None) -> list[dict]:
    """Review's whole eligible queue, in Review's order. `open_tasks`: the open rows, already annotated
    (relations.annotate, or the API's scored rows); None reads and annotates them. `flags`: flags(conn, cfg)
    when the caller has it; None reads it."""
    if open_tasks is None:
        open_tasks = relations.annotate(conn, rows(conn, "SELECT * FROM tasks WHERE status = 'open'"))
    if flags is None:
        flags = _flags(conn, cfg)
    last = parse_date(get_settings(conn).get("state.last_review_at"))
    return lifecycle.build_review_queue(open_tasks, today, cfg, last, flags_by_id=flags, cap=10**9)


def today(cfg: dict) -> date:
    """Review's day: the date where the owner is (`owner.timezone`), never the machine's. Every Review route,
    the decision rule, the tray's finish write and the connector use it."""
    from . import reminders                  # lazy, like the rest of this module's heavier neighbours
    return reminders.local_now(cfg).date()


def items(conn, cfg: dict, today: date) -> dict:
    """The whole queue as GET /api/review ships it, built once for every reader: `tasks`, each
    {key: "t:<id>", task (decorated, `rel` compacted), reasons, primary, texts, link_suggestions, since
    (only for "linked_changed"), flags, evidence, evidence_lines, recommendation, rec_held}, and `cards`, each with
    `key: "s:<id>"` (review_cards) and the same two, each list in Review's order (order_key; `queue` keeps the lifecycle's own order for the count,
    the nudge and the planner). No cap, no exclusion, nothing left out for being decided. Reads only: the day's link
    suggestions (suggestions.session) are the caller's to write first."""
    from . import review_cards               # lazy: review_cards imports ripple, which imports the planner
    open_tasks = relations.decorate(conn, rows(conn, "SELECT * FROM tasks WHERE status = 'open'"), today, cfg)
    fl = flags(conn, cfg)
    full = queue(conn, cfg, today, open_tasks, fl)
    tasks = sorted(_finish(conn, full, open_tasks, fl, review_cards.evidence_by_task(conn)), key=lambda e: order_key(e, today))
    cards = sorted(review_cards.cards(conn, cfg), key=lambda e: order_key(e, today))
    recommend_all(conn, cfg, today, tasks + cards)
    return {"tasks": tasks, "cards": cards}


def recommend_all(conn, cfg: dict, today: date, entries: list[dict]) -> None:
    """Each entry's `recommendation` (recommend.recommend; None when `review.recommend` is off) and `rec_held` (the
    sentence saying a close was held back as risky, or None). Data only: nothing is staged. Reads only."""
    from . import recommend                  # lazy: recommend imports risky_close from here
    on = bool(C.value(cfg, "review.recommend"))
    names = recommend.names_for(conn, entries) if on else {}
    for e in entries:
        e["recommendation"] = recommend.recommend(e, today, names) if on else None
        e["rec_held"] = recommend.held(e, today) if on else None


def task_item(conn, cfg: dict, today: date, task_id: int) -> dict | None:
    """One open task as `items` ships it, built the same way (its recommendation too), or None when the task is not open. A task with no
    reason to be on Review today is still an item, with `reasons: []` and `primary: None` (a decision from its
    card's conversation must still work after the reason went). Reads only."""
    from . import review_cards
    ts = rows(conn, "SELECT * FROM tasks WHERE id = ? AND status = 'open'", (task_id,))
    if not ts:
        return None
    t = relations.decorate(conn, ts, today, cfg)[0]
    fl = flags(conn, cfg)
    last = parse_date(get_settings(conn).get("state.last_review_at"))
    rs = lifecycle.review_reasons(t, today, cfg, last, fl.get(task_id))
    item = {"task": t, "reasons": rs, "primary": rs[0] if rs else None}
    out = _finish(conn, [item], ts, fl, review_cards.evidence_by_task(conn, {task_id}))[0]
    recommend_all(conn, cfg, today, [out])
    return out


def _finish(conn, full: list[dict], open_tasks: list[dict], flags: dict, by_task: dict) -> list[dict]:
    """The queue's items (`{task, reasons, primary}`, tasks decorated) as Review ships them: the link texts and
    suggestions read from the full `rel`, then `rel` compacted on every row in `open_tasks`, the flags, the
    evidence and the key."""
    from . import ripple                     # lazy: ripple imports the planner, and the scheduler imports this module
    names = {k: v["name"] for k, v in relations.briefs(conn, {s["other"] for i in full for s in i["task"]["rel"]["suggestions"]}).items()}
    for i in full:                                           # from the full rel (a text may name a closed task it waited on)
        i["texts"] = lifecycle.reason_texts(i["task"], i["reasons"])
        i["link_suggestions"] = [dict(s, other_name=names.get(s["other"])) for s in i["task"]["rel"]["suggestions"]]
    for i in full:
        if "linked_changed" in i["reasons"]:
            i["since"] = ripple.since(conn, i["task"]["id"], i["task"]["rel"].get("changed"))
    for t in open_tasks:
        relations.ship(t)
    out = []
    for i in full:
        i["flags"] = flags.get(i["task"]["id"], {})
        i["evidence"] = by_task.get(i["task"]["id"], [])      # judged in the same batch as the decision; a proposal's change can be accepted
        i["evidence_lines"] = evidence_lines(i)
        out.append({"key": f"t:{i['task']['id']}", **i})
    return out


def is_card(e: dict) -> bool:
    return e["task_id"] is None and e["kind"] in SUGGESTION_KINDS


def card_count(conn) -> int:
    """Unjudged evidence rows that are cards."""
    marks = ",".join("?" * len(SUGGESTION_KINDS))
    return conn.execute(f"SELECT COUNT(*) FROM evidence WHERE judged_at IS NULL AND task_id IS NULL AND kind IN ({marks})",
                        SUGGESTION_KINDS).fetchone()[0]


def undecided_count(conn, cfg: dict, today: date, exclude=frozenset()) -> int:
    """The number beside Review once the tray is on the server: the queue's tasks and cards with no tray row that
    `decides` them (and, for a page loaded before that, no task in its `exclude`). Reads only."""
    decided = {r[0] for r in conn.execute("SELECT DISTINCT decides FROM tray WHERE decides IS NOT NULL")}   # tray.decided's column, read here
    tasks = {int(d[2:]) for d in decided if d.startswith("t:") and d[2:].isdigit()}                          # (tray imports chat: no import here)
    cards = [int(d[2:]) for d in decided if d.startswith("s:") and d[2:].isdigit()]
    marks = ",".join("?" * len(SUGGESTION_KINDS))
    staged_cards = conn.execute(f"SELECT COUNT(*) FROM evidence WHERE judged_at IS NULL AND task_id IS NULL AND kind IN ({marks})"
                                f" AND id IN ({','.join('?' * len(cards))})", (*SUGGESTION_KINDS, *cards)).fetchone()[0] if cards else 0
    return count(conn, cfg, today, exclude=set(exclude) | tasks) - staged_cards


def count(conn, cfg: dict, today: date, exclude=frozenset()) -> int:
    """len(queue(...)) over the open rows not in `exclude` (the page's tray), plus card_count(conn)."""
    open_tasks = relations.annotate(conn, [t for t in rows(conn, "SELECT * FROM tasks WHERE status = 'open'") if t["id"] not in exclude])
    return len(queue(conn, cfg, today, open_tasks)) + card_count(conn)


def _card_reason(entry: dict) -> str:
    """The REVIEW_ORDER reason a suggestion card ranks as: a Slack mention as Mentioned, a ripple's create as Ripple,
    everything else (the planner's, mail's) as Planner."""
    if entry.get("kind") == "slack_mention":
        return "mentioned"
    return "ripple" if entry.get("by") == "ripple" else "planner"


def _rank(reason) -> int:
    return lifecycle.REVIEW_ORDER.index(reason) if reason in lifecycle.REVIEW_ORDER else len(lifecycle.REVIEW_ORDER)


def order_key(entry: dict, today: date) -> tuple:
    """Review's order (design 6.7): (tier, inner, due or "9999", -score, (kind, id)). Tier 0: a task with a hard date
    today, tomorrow or past, or a card whose date is that near; inner = (due, the reason's rank). Tier 1: every other
    task (a group as `new`) and a card due within CARD_SOON_DAYS; inner = the reason's rank. Tier 2: any other card, by
    its date then its id. Dates are ISO text, which compares as dates do; within a tier the types never mix."""
    key = str(entry.get("key") or "")
    if key.startswith("s:"):
        f = entry.get("fields") or {}
        due = f.get("due") if isinstance(f.get("due"), str) and parse_date(f.get("due")) else None
        d = parse_date(due)
        rank = _rank(_card_reason(entry))
        last = (1, int(entry.get("id") or 0))
        if d is not None and d <= today + timedelta(days=URGENT_DAYS):
            return (0, (due, rank), due, 0, last)
        if d is not None and d <= today + timedelta(days=CARD_SOON_DAYS):
            return (1, rank, due, 0, last)
        return (2, 0, due or "9999", 0, last)
    if key.startswith("g:"):                                          # a group of alike new tasks (2d) ranks as new
        return (1, _rank("new"), "9999", 0, (2, 0))
    t = entry.get("task") or {}
    due = t.get("due") if isinstance(t.get("due"), str) and parse_date(t.get("due")) else None
    d = parse_date(due)
    rank, score, last = _rank(entry.get("primary")), -float(t.get("score") or 0), (0, int(t.get("id") or 0))
    if t.get("hard") and d is not None and d <= today + timedelta(days=URGENT_DAYS):
        return (0, (due, rank), due, score, last)
    return (1, rank, due or "9999", score, last)


RISKY_DAYS = 2                                         # a firm date this many days away, either way, makes a close risky
RISKY = {"hard": "its firm date is within two days", "top": "it is on Top", "steps": "it has open steps",
         "frees": "an open task waits on it"}


def risky_close(task: dict, today: date) -> str | None:
    """Why closing this task deserves a question (design §6.8 rule 3), or None: a firm date within RISKY_DAYS either
    way, on Top, open steps, or an open task waits on it. Being a step of an open goal is not a reason. Reads the task as
    the queue ships it (`rel` compacted keeps `steps_open` and `frees_open`). Pure."""
    d = parse_date(task.get("due")) if isinstance(task.get("due"), str) else None
    if task.get("hard") and d is not None and abs((d - today).days) <= RISKY_DAYS:
        return RISKY["hard"]
    if task.get("top_rank"):
        return RISKY["top"]
    rel = task.get("rel") or {}
    if (rel.get("steps_open") or 0) > 0:
        return RISKY["steps"]
    if rel.get("frees_open"):
        return RISKY["frees"]
    return None


def _cap(v, n: int) -> str:
    return str(v or "").strip()[:n]


def _who(v) -> str:
    """A sender as a person's name: "Name <address>" gives the name; an address alone, or nothing, is "someone"."""
    s = _cap(v, 200)
    if "<" in s:
        s = s.split("<", 1)[0]
    s = s.strip().strip('"\'').strip()[:80]
    return "someone" if not s or "@" in s else s


def _at(v):
    """An evidence time as ISO with its offset: Mailbird's times are UTC written without one."""
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=ZoneInfo("UTC"))).isoformat()


def evidence_lines(item: dict) -> list[dict]:
    """Who wrote and about what, behind "Thread updated" and "Mentioned in Slack" (design 6.7): one line per unjudged
    mail or Slack row on the item (`item["evidence"]`). Mail: {kind: "mail", who, subject, at, reply?, maybe?} from the
    subject (the row's label) and the last sender (its excerpt). Slack: {kind: "slack", who, where, text, at} parsed
    from the label with one bounded pattern; a label that does not parse is shown as it is ({kind, label, at}). Every
    value is capped and an address is never shown alone. Pure."""
    out: list[dict] = []
    for e in item.get("evidence") or []:
        k = e.get("kind")
        if k in MAIL_KINDS:
            line = {"kind": "mail", "who": _who(e.get("reason")), "subject": _cap(e.get("label"), 200), "at": _at(e.get("at"))}
            if k == "reply_from_waiting":
                line["reply"] = True
            elif k == "maybe_reply_from_waiting":
                line["maybe"] = True
            out.append(line)
        elif k == "slack_mention":
            label = _cap(e.get("label"), 400)
            m = SLACK_LABEL.match(label)
            if m:
                out.append({"kind": "slack", "who": _who(m.group(2)), "where": m.group(1), "text": m.group(3).strip(), "at": _at(e.get("at"))})
            else:
                out.append({"kind": "slack", "label": label[:200], "at": _at(e.get("at"))})
    return out
