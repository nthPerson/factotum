"""Task lifecycle (SPEC.md §5): tiers, fading, last call, resurrection, the review queue, and the
import classifier used by the one-time migration (SPEC.md §12). All functions are pure.
"""
from __future__ import annotations

from datetime import date, timedelta

from . import config as C
from .dates import days_until, parse_date

OPEN = "open"
CLOSED = ("done", "dropped", "faded", "lapsed")

REVIEW_ORDER = ["last_call", "thread_updated", "snooze_over", "new", "slipped", "fading", "back", "reminder_missed"]
REVIEW_LABELS = {
    "last_call": "Last call · hard deadline passed",
    "thread_updated": "Thread updated",
    "snooze_over": "Back from Later",
    "new": "New · not yet reviewed",
    "slipped": "Soft date slipped",
    "fading": "Fading soon",
    "back": "Back · mentioned again",
    "reminder_missed": "Reminder missed",
}


def tier(task: dict) -> str:
    if task.get("top_rank"):
        return "top"
    if task.get("due"):
        return "dated"
    return "backlog"


def is_long_term(task: dict, cfg: dict) -> bool:
    return bool(task.get("longterm")) or C.project_never_fades(cfg, task.get("project"))


def fade_at(task: dict, cfg: dict) -> date | None:
    """Date on which an undated backlog task fades, or None if it never fades."""
    if tier(task) != "backlog" or is_long_term(task, cfg) or task.get("status") != OPEN:
        return None
    # A Review decision (Keep, Later) counts as attention: it restarts the quiet clock, so a kept
    # item comes back as Fading in fade_days instead of lingering open and unseen.
    anchors = [parse_date(task.get(k)) for k in ("last_mention_at", "last_reviewed_at")]
    anchor = max((a for a in anchors if a), default=None) or parse_date(task.get("edited_at")) or parse_date(task.get("created_at"))
    if anchor is None:
        return None
    return anchor + timedelta(days=int(C.get(cfg, "lifecycle.fade_days", 21)))


def last_call_at(task: dict, cfg: dict) -> date | None:
    """Date after which a hard-dated task lapses unless kept, or None."""
    if not task.get("hard") or not task.get("due") or task.get("status") != OPEN or task.get("top_rank"):
        return None
    d = parse_date(task.get("due"))
    return None if d is None else d + timedelta(days=int(C.get(cfg, "lifecycle.last_call_days", 7)))


def review_reasons(task: dict, today: date, cfg: dict, last_review_at: date | None, flags: dict | None = None) -> list[str]:
    """Which review buckets this task belongs to, in REVIEW_ORDER. `flags` carries signals the
    sweeps compute (thread_updated, back, reminder_missed)."""
    if task.get("status") != OPEN:
        return []
    snoozed = parse_date(task.get("snoozed_until"))
    if snoozed and snoozed > today:
        return []
    reviewed = parse_date(task.get("last_reviewed_at"))
    flags = flags or {}
    out: list[str] = []
    lc = last_call_at(task, cfg)
    if lc and today >= lc and not (reviewed and reviewed >= lc):
        out.append("last_call")
    if flags.get("thread_updated"):
        out.append("thread_updated")
    # Later = "come back on <date>": once the snooze is over the item is asked about again, until
    # a decision made on or after that date suppresses it.
    if snoozed and snoozed <= today and not (reviewed and reviewed >= snoozed):
        out.append("snooze_over")
    # "New" means never decided on, not "created since the last session": an item you did not get
    # to in one session must still be there in the next one. `last_review_at` is kept for the
    # review push (skip when reviewed today) and no longer gates the queue.
    if not reviewed:
        out.append("new")
    d = days_until(task.get("due"), today)
    if d is not None and d < 0 and not task.get("hard") and not task.get("top_rank") and not (reviewed and reviewed >= parse_date(task["due"])):
        out.append("slipped")
    fa = fade_at(task, cfg)
    warn = int(C.get(cfg, "lifecycle.fade_warn_days", 3))
    if fa and (fa - today).days <= warn and not (reviewed and (fa - reviewed).days <= warn):
        out.append("fading")
    if flags.get("back"):
        out.append("back")
    if flags.get("reminder_missed"):
        out.append("reminder_missed")
    return [r for r in REVIEW_ORDER if r in out]


def build_review_queue(tasks: list[dict], today: date, cfg: dict, last_review_at: date | None, flags_by_id: dict[int, dict] | None = None,
                       cap: int | None = None) -> list[dict]:
    """Ordered, capped queue of {task, reasons}. `cap=None` reads lifecycle.review_cap; pass a large
    number to see the whole eligible set (the API does, to report how many pages remain)."""
    flags_by_id = flags_by_id or {}
    items = []
    for t in tasks:
        rs = review_reasons(t, today, cfg, last_review_at, flags_by_id.get(t["id"]))
        if rs:
            items.append({"task": t, "reasons": rs, "primary": rs[0]})
    items.sort(key=lambda i: (REVIEW_ORDER.index(i["primary"]), i["task"].get("due") or "9999", -(i["task"].get("score") or 0)))
    cap = int(C.get(cfg, "lifecycle.review_cap", 15)) if cap is None else int(cap)
    return items[:cap]


def expire(tasks: list[dict], today: date, cfg: dict) -> list[tuple[int, str, str]]:
    """Return (task_id, new_status, why) for tasks whose fade or last-call window has passed and
    which were not kept in review. The scheduler applies these as an automation batch.

    Nothing expires unseen: a row must have been on a Review page (`last_offered_at`) on or after
    the day its warning started (fade_at − fade_warn_days, or the last-call date). Until then the
    row stays open and keeps appearing in Review, and the daily push keeps asking."""
    out = []
    warn = timedelta(days=int(C.get(cfg, "lifecycle.fade_warn_days", 3)))
    for t in tasks:
        if t.get("status") != OPEN:
            continue
        reviewed = parse_date(t.get("last_reviewed_at"))
        offered = parse_date(t.get("last_offered_at"))
        fa = fade_at(t, cfg)
        if fa and today > fa and not (reviewed and reviewed >= fa - warn):
            if offered and offered >= fa - warn:
                out.append((t["id"], "faded", f"no mention since {t.get('last_mention_at') or t.get('created_at')}"))
            continue
        lc = last_call_at(t, cfg)
        if lc and today > lc and not (reviewed and reviewed >= lc) and offered and offered >= lc:
            out.append((t["id"], "lapsed", f"hard deadline {t.get('due')} passed, no evidence"))
    return out


# --- one-time migration classifier (SPEC.md §12) -------------------------------------------

IMPORT_BUCKETS = [
    ("canvas_future", "Canvas-sourced, due in the future → keep open; Canvas closes it"),
    ("hard_lapsed", "Hard, past due > last_call_days, few mentions → lapsed"),
    ("hard_last_call", "Hard, past due, ≥3 mentions or calendar-sourced → keep, Last call"),
    ("soft_faded", "Soft, past due > 14 d → faded"),
    ("undated_faded", "Undated, 1 mention, created ≥ fade_days ago → faded"),
    ("undated_keep", "Undated, ≥2 mentions or created recently → keep, New in Review"),
    ("waiting_keep", "Waiting → keep; the mail sweep flips it on reply"),
    ("dated_keep", "Dated, not yet due → keep"),
    ("closed", "Done/Dropped in Notion → imported as history"),
]


def classify_import(task: dict, today: date, cfg: dict) -> tuple[str, str]:
    """Return (bucket, status_after_import) for a row imported from the Notion Tasks DB."""
    st = task.get("status")
    if st in CLOSED:
        return "closed", st
    if task.get("legacy_status") == "Waiting":
        return "waiting_keep", OPEN
    d = days_until(task.get("due"), today)
    mentions = int(task.get("mentions") or 1)
    last_call = int(C.get(cfg, "lifecycle.last_call_days", 7))
    fade_days = int(C.get(cfg, "lifecycle.fade_days", 21))
    if d is not None:
        if task.get("source") == "Canvas" and d >= 0:
            return "canvas_future", OPEN
        if d >= 0:
            return "dated_keep", OPEN
        if task.get("hard"):
            if -d > last_call:
                # A calendar event counts as a user signal only when the task itself came from the
                # calendar: the old sync put every dated extraction on the calendar automatically.
                user_event = bool(task.get("calendar_event")) and task.get("source") == "Calendar"
                if mentions >= 3 or user_event:
                    return "hard_last_call", OPEN
                return "hard_lapsed", "lapsed"
            return "dated_keep", OPEN
        if -d > 14:
            return "soft_faded", "faded"
        return "dated_keep", OPEN
    if is_long_term(task, cfg):
        return "undated_keep", OPEN
    # A row mentioned once was mentioned when it was created, so its quiet clock starts there.
    # Notion's last_edited_time is not a human signal: the old planner rewrote Why now / Score on
    # every open row.
    created = parse_date(task.get("created_at")) or today
    if mentions <= 1 and (today - created).days >= fade_days:
        return "undated_faded", "faded"
    return "undated_keep", OPEN
