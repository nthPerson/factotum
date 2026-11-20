"""Review's count, defined once (stage 8): the number beside "Review", the number on the app's icon and
the number a push carries all come from here, and GET /api/review builds its queue and its cards through
the same functions. Reads only. GET /api/review also writes (the day's link-suggestion session, the
offered stamp `expire` acts on), so it is never called just for the number."""
from __future__ import annotations

from datetime import date

from . import lifecycle, relations
from .dates import parse_date
from .db import get_settings, rows

SUGGESTION_KINDS = ("proposal", "slack_mention")      # evidence with no task that Review offers as a Create / Dismiss card


def queue(conn, cfg: dict, today: date, open_tasks: list[dict] | None = None, flags: dict | None = None) -> list[dict]:
    """Review's whole eligible queue, in Review's order. `open_tasks`: the open rows, already annotated
    (relations.annotate, or the API's scored rows); None reads and annotates them. `flags`: review_flags(conn)
    when the caller has it; None reads it."""
    if open_tasks is None:
        open_tasks = relations.annotate(conn, rows(conn, "SELECT * FROM tasks WHERE status = 'open'"))
    if flags is None:
        from .mail_sweep import review_flags
        flags = review_flags(conn)
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
    (only for "linked_changed"), flags, evidence}, in Review's order; and `cards`, each with `key: "s:<id>"`
    (review_cards). No cap, no exclusion, nothing left out for being decided. Reads only: the day's link
    suggestions (suggestions.session) are the caller's to write first."""
    from . import review_cards               # lazy: review_cards imports ripple, which imports the planner
    from .mail_sweep import review_flags
    open_tasks = relations.decorate(conn, rows(conn, "SELECT * FROM tasks WHERE status = 'open'"), today, cfg)
    flags = review_flags(conn)
    full = queue(conn, cfg, today, open_tasks, flags)
    return {"tasks": _finish(conn, full, open_tasks, flags, review_cards.evidence_by_task(conn)), "cards": review_cards.cards(conn, cfg)}


def task_item(conn, cfg: dict, today: date, task_id: int) -> dict | None:
    """One open task as `items` ships it, built the same way, or None when the task is not open. A task with no
    reason to be on Review today is still an item, with `reasons: []` and `primary: None` (a decision from its
    card's conversation must still work after the reason went). Reads only."""
    from . import review_cards
    from .mail_sweep import review_flags
    ts = rows(conn, "SELECT * FROM tasks WHERE id = ? AND status = 'open'", (task_id,))
    if not ts:
        return None
    t = relations.decorate(conn, ts, today, cfg)[0]
    flags = review_flags(conn)
    last = parse_date(get_settings(conn).get("state.last_review_at"))
    rs = lifecycle.review_reasons(t, today, cfg, last, flags.get(task_id))
    item = {"task": t, "reasons": rs, "primary": rs[0] if rs else None}
    return _finish(conn, [item], ts, flags, review_cards.evidence_by_task(conn, {task_id}))[0]


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
