"""Task lifecycle (SPEC.md §5): tiers, fading, last call, resurrection, the review queue, and the
import classifier used by the one-time migration (SPEC.md §12). All functions are pure.
"""
from __future__ import annotations

from datetime import date, timedelta

from . import config as C
from .dates import days_until, parse_date

OPEN = "open"
CLOSED = ("done", "dropped", "faded", "lapsed")

REVIEW_ORDER = ["last_call", "thread_updated", "mentioned", "planner", "ripple", "freed", "blocker_gone", "steps_done", "linked_changed", "link_suggested", "snooze_over", "new", "slipped", "fading", "back"]
REVIEW_LABELS = {
    "last_call": "Last call · hard deadline passed",
    "thread_updated": "Thread updated",
    "mentioned": "Mentioned in Slack",
    "planner": "Planner suggests",
    "ripple": "Ripple suggests",
    "freed": "Free now",
    "blocker_gone": "A task it waited on closed",
    "steps_done": "All steps done",
    "linked_changed": "A linked task changed",
    "link_suggested": "Suggested link",
    "snooze_over": "Back from Later",
    "new": "New · not yet reviewed",
    "slipped": "Soft date slipped",
    "fading": "Fading soon",
    "back": "Back · mentioned again",
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
    rel = task.get("rel") or {}
    if rel.get("waits_on_open"):
        return None   # a task waiting on another open task does not fade; its clock starts when it is freed
    anchors = [parse_date(task.get(k)) for k in ("last_mention_at", "last_reviewed_at")]
    # freed, a step's activity (keeps its goal alive, not the reverse) and a merge also restart the clock
    anchors += [parse_date(rel.get(k)) for k in ("freed_at", "neighbour_activity_at", "merged_at")]
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
    sweeps and the queue compute (thread_updated, mentioned, planner, ripple; `back`: the day a recording or
    another automation reopened the task from faded or lapsed, review_queue.back_flags)."""
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
    if flags.get("mentioned"):
        out.append("mentioned")
    if flags.get("planner"):
        out.append("planner")
    if flags.get("ripple"):
        out.append("ripple")
    rel = task.get("rel") or {}
    freed_at = parse_date(rel.get("freed_at"))
    closed = rel.get("waits_on_closed") or []
    if freed_at and closed:
        latest = max(closed, key=lambda c: (c.get("at") or "", c.get("id") or 0))
        if latest.get("status") == "done" and not (reviewed and reviewed >= freed_at):
            out.append("freed")
    if any(c.get("status") in ("dropped", "faded", "lapsed") and (not reviewed or (parse_date(c.get("at")) or date.min) > reviewed) for c in closed):
        out.append("blocker_gone")
    activity = parse_date(rel.get("neighbour_activity_at"))
    # A decision on or after the steps' last activity answers it; with no activity date on any step
    # (closed rows that carry none), any decision does, or Keep could never clear the card.
    if rel.get("steps_total", 0) > 0 and rel.get("steps_open", 0) == 0 and not (reviewed and (activity is None or reviewed >= activity)):
        out.append("steps_done")
    if rel.get("changed"):
        out.append("linked_changed")
    if rel.get("suggestions"):
        out.append("link_suggested")
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
    back = parse_date(flags.get("back")) if isinstance(flags.get("back"), str) else None
    if back and not (reviewed and reviewed >= back):      # answered by any decision on or after the day it came back
        out.append("back")
    return [r for r in REVIEW_ORDER if r in out]


REASON_TEXTS = {
    "freed": "Free now: Task-{freed_by} is done",
    "blocker_gone": "Task-{id} was {status}. Is this still needed?",
    "steps_done": "All steps are done. Close this, or is there more?",
    "linked_changed": "A linked task changed{by}",
    "link_suggested": "Suggested: {word} Task-{other}?",
}


def reason_texts(task: dict, reasons: list[str]) -> dict[str, str]:
    """The card sentence for each link reason in `reasons` (others are left out)."""
    from .relations import WORDS, side_word
    rel = task.get("rel") or {}
    out: dict[str, str] = {}
    for r in reasons:
        if r not in REASON_TEXTS:
            continue
        if r == "freed":
            out[r] = REASON_TEXTS[r].format(freed_by=rel.get("freed_by"))
        elif r == "blocker_gone":
            gone = [c for c in rel.get("waits_on_closed") or [] if c.get("status") in ("dropped", "faded", "lapsed")]
            if not gone:
                continue
            c = max(gone, key=lambda c: (c.get("at") or "", c.get("id") or 0))
            out[r] = REASON_TEXTS[r].format(id=c["id"], status=c["status"])
        elif r == "linked_changed":
            by = (rel.get("changed") or {}).get("by")
            out[r] = REASON_TEXTS[r].format(by=f" (Task-{by})" if by else "")
        elif r == "link_suggested":
            sug = (rel.get("suggestions") or [None])[0]
            if not sug:
                continue
            # read from this card's end: the goal of a part_of suggestion "has step", the task waited on "frees"
            kind = sug.get("kind")
            word = side_word(kind, sug.get("dir") or "out") if kind in ("part_of", "waits_on") else WORDS.get(kind, "linked to")
            out[r] = REASON_TEXTS[r].format(word=word, other=sug["other"])
        else:
            out[r] = REASON_TEXTS[r]
    return out


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
