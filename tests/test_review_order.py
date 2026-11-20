"""Spoken Review, stage 2b: Review's order, the evidence on a card, and the two unused reasons."""
from datetime import timedelta

import pytest

from stage4_helpers import mk
from test_phase3 import REC

from factotum import batches, lifecycle, review_decide, review_queue, sync_file, tray
from factotum.db import one


def _items_by_id(conn, cfg):
    return {i["task"]["id"]: i for i in review_queue.items(conn, cfg, review_queue.today(cfg))["tasks"]}


def _recording_reopens(conn, cfg, tid):
    """A recording names a faded task again: sync_file's update reopens it in the recording's batch (source plaud)."""
    data = {"note": {"properties": {"area": "Personal", "project": "Personal", "type": "Reminder", "tags": []}, "body_md": "x", "followups": []},
            "tasks": [{"op": "update", "id": tid, "context": "Mentioned again"}],
            "instructions": [], "reminders": [], "calendar": [], "unresolved": []}
    sync_file.apply_result(conn, cfg, REC, data, review_queue.today(cfg), calendar=None)
    conn.commit()


# --- 2b-1: back wired, reminder_missed gone ---------------------------------------------------------

@pytest.mark.parametrize("closed", ["faded", "lapsed"])
def test_back_after_a_recording_reopened_a_faded_task(conn, cfg, closed):
    mk(conn, 1, name="Order the cable", status=closed, last_reviewed_at="2026-10-02")
    mk(conn, 2, name="Never closed", last_reviewed_at="2026-10-02")
    _recording_reopens(conn, cfg, 1)
    assert one(conn, "SELECT status FROM tasks WHERE id = 1")["status"] == "open"
    today = review_queue.today(cfg)
    assert review_queue.back_flags(conn, cfg) == {1: today.isoformat()}
    assert review_queue.flags(conn, cfg)[1]["back"] == today.isoformat()
    items = _items_by_id(conn, cfg)
    assert "back" in items[1]["reasons"]
    assert 2 not in items or "back" not in items[2]["reasons"]
    assert lifecycle.REVIEW_LABELS["back"]


@pytest.mark.parametrize("option,args,later", [("keep", {}, 0), ("later", {"until": "tomorrow"}, 1)])
def test_back_is_cleared_by_any_decision(conn, cfg, option, args, later):
    mk(conn, 1, name="Order the cable", status="faded")
    _recording_reopens(conn, cfg, 1)
    today = review_queue.today(cfg)
    assert "back" in _items_by_id(conn, cfg)[1]["reasons"]
    review_decide.stage(conn, cfg, "t:1", option, args, today, origin="page")
    tray.apply(conn, cfg, today)
    t = one(conn, "SELECT * FROM tasks WHERE id = 1")
    assert t["last_reviewed_at"] == today.isoformat()
    flags = review_queue.flags(conn, cfg)
    assert flags[1]["back"] == today.isoformat()                         # the flag stays; the decision answers it
    day = today + timedelta(days=later)                                  # Later: asked about again once the snooze is over, but not as Back
    assert "back" not in lifecycle.review_reasons(dict(t), day, cfg, None, flags.get(1))


@pytest.mark.parametrize("source", review_queue.OWNER_SOURCES)
def test_a_reopen_by_the_owner_is_not_back(conn, cfg, source):
    assert review_queue.OWNER_SOURCES == ("ui", "review", "command", "chat", "skill", "connector")
    mk(conn, 1, name="Order the cable", status="faded")
    batches.apply(conn, [{"task_id": 1, "op": "reopen", "fields": {}}], source, review_queue.today(cfg))
    conn.commit()
    assert review_queue.back_flags(conn, cfg) == {}
    assert "back" not in _items_by_id(conn, cfg)[1]["reasons"]


def test_an_undone_reopen_is_not_back(conn, cfg):
    mk(conn, 1, name="Order the cable", status="faded")
    res = batches.apply(conn, [{"task_id": 1, "op": "reopen", "fields": {}}], "plaud", review_queue.today(cfg))
    conn.commit()
    assert 1 in review_queue.back_flags(conn, cfg)
    batches.undo(conn, res["batch_id"])
    conn.commit()
    assert review_queue.back_flags(conn, cfg) == {}


def test_a_done_task_reopened_is_not_back(conn, cfg):
    """Only a return from faded or lapsed is Back: a done task reopened by a ripple or a recording is not."""
    mk(conn, 1, name="Order the cable", status="done")
    batches.apply(conn, [{"task_id": 1, "op": "reopen", "fields": {}}], "plaud", review_queue.today(cfg))
    conn.commit()
    assert review_queue.back_flags(conn, cfg) == {}


def test_reminder_missed_is_gone(conn, cfg):
    assert "reminder_missed" not in lifecycle.REVIEW_ORDER
    assert "reminder_missed" not in lifecycle.REVIEW_LABELS
    mk(conn, 1, last_reviewed_at="2026-10-30", longterm=1)              # long-term: no fading to hide what is asked
    t = one(conn, "SELECT * FROM tasks WHERE id = 1")
    today = review_queue.today(cfg)
    assert lifecycle.review_reasons(dict(t), today, cfg, None, {"reminder_missed": True}) == []
