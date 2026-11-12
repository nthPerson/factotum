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


def is_card(e: dict) -> bool:
    return e["task_id"] is None and e["kind"] in SUGGESTION_KINDS


def card_count(conn) -> int:
    """Unjudged evidence rows that are cards."""
    marks = ",".join("?" * len(SUGGESTION_KINDS))
    return conn.execute(f"SELECT COUNT(*) FROM evidence WHERE judged_at IS NULL AND task_id IS NULL AND kind IN ({marks})",
                        SUGGESTION_KINDS).fetchone()[0]


def count(conn, cfg: dict, today: date, exclude=frozenset()) -> int:
    """len(queue(...)) over the open rows not in `exclude` (the page's tray), plus card_count(conn)."""
    open_tasks = relations.annotate(conn, [t for t in rows(conn, "SELECT * FROM tasks WHERE status = 'open'") if t["id"] not in exclude])
    return len(queue(conn, cfg, today, open_tasks)) + card_count(conn)
