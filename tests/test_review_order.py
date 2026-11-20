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


# --- 2b-2: order_key, evidence_lines; GET /api/review in the new order ------------------------------

import json                                                                        # noqa: E402
from datetime import date                                                          # noqa: E402

from fastapi.testclient import TestClient                                          # noqa: E402

from factotum import config as C                                                   # noqa: E402
from factotum.api import create_app                                                # noqa: E402
from factotum.db import get_settings, now_iso                                      # noqa: E402

DAY = date(2026, 11, 19)                                                           # a Thursday, as the live queue of the design


def _t(id, primary, score=0, due=None, hard=0):
    return {"key": f"t:{id}", "task": {"id": id, "due": due, "hard": hard, "score": score}, "primary": primary, "reasons": [primary]}


def _c(id, kind="proposal", due=None, by=None):
    return {"key": f"s:{id}", "id": id, "kind": kind, "by": by, "fields": {"name": f"Card {id}", **({"due": due} if due else {})}}


def _ordered(entries, today=DAY):
    return [e["key"] for e in sorted(entries, key=lambda e: review_queue.order_key(e, today))]


def _card_row(c, name, due=None, kind="proposal", account=None):
    if kind == "slack_mention":
        row = (None, "slack_mention", "https://x.slack.com/archives/C1/p1", "Lab", f"#general · Max M: {name}", "Max M in #general")
    else:
        row = (None, "proposal", json.dumps({"op": "create", "fields": dict({"name": name}, **({"due": due} if due else {}))}), account, f"create: {name}", "r")
    cur = c.execute("INSERT INTO evidence (task_id, kind, ref, account, label, excerpt, at) VALUES (?, ?, ?, ?, ?, ?, ?)", (*row, now_iso()))
    c.commit()
    return cur.lastrowid


def test_hard_today_and_tomorrow_come_first_cards_included(conn, cfg):
    today = review_queue.today(cfg)
    iso = lambda n: (today + timedelta(days=n)).isoformat()                       # noqa: E731
    mk(conn, 1, name="Undated new")
    mk(conn, 2, name="Hard tomorrow", due=iso(1), hard=1)
    mk(conn, 3, name="Hard today", due=iso(0), hard=1)
    mk(conn, 4, name="Soft tomorrow", due=iso(1))
    mk(conn, 5, name="Hard in a week", due=iso(7), hard=1)
    e_soon = _card_row(conn, "Card due tomorrow", due=iso(1))
    e_none = _card_row(conn, "Card with no date")
    q = review_queue.items(conn, cfg, today)
    assert [i["task"]["id"] for i in q["tasks"]] == [3, 2, 4, 5, 1]               # urgent hard dates first; then new by date
    assert [c["id"] for c in q["cards"]] == [e_soon, e_none]
    both = _ordered(q["tasks"] + q["cards"], today)
    assert both == ["t:3", f"s:{e_soon}", "t:2", "t:4", "t:5", "t:1", f"s:{e_none}"]   # a card due tomorrow among them, at planner rank


def test_a_dated_card_sits_at_planner_rank_and_an_undated_one_last():
    entries = [_t(1, "new"), _t(2, "thread_updated"), _t(3, "fading"), _t(4, "planner"), _t(5, "link_suggested"),
               _c(10, due="2026-11-23"),                                   # in 4 days: tier 1 at planner rank, before a planner task with no date
               _c(11, kind="slack_mention", due="2026-11-22"),            # a Slack card ranks as Mentioned
               _c(12, by="ripple", due="2026-11-21"),                     # a ripple's card as Ripple
               _c(13), _c(14, due="2026-12-30"), _c(15, due="not a date")]
    assert _ordered(entries) == ["t:2", "s:11", "s:10", "t:4", "s:12", "t:5", "t:1", "t:3", "s:14", "s:13", "s:15"]


def test_a_live_like_queue_orders_as_designed():
    """The shapes of the live queue of 2026-11-19 (16 task items, 6 cards), names changed; the order written out by hand from design 6.7."""
    tasks = [_t(477, "new", 40), _t(478, "new", 55), _t(479, "new", 30), _t(480, "new", 30),
             _t(481, "new", 20, "2026-11-20", 1),                         # a recording's task with a hard date tomorrow
             _t(482, "new", 35, "2026-11-24"),
             _t(483, "new", 60, "2026-12-10", 1), _t(484, "new", 60, "2026-12-10", 1),     # one course's final project, two rows
             _t(300, "link_suggested", 50), _t(301, "link_suggested", 70), _t(302, "link_suggested", 10, "2026-11-18", 1),
             _t(250, "thread_updated", 45), _t(251, "planner", 20), _t(252, "linked_changed", 33),
             _t(253, "slipped", 25, "2026-11-10"), _t(254, "fading", 5)]
    cards = [_c(601, due="2026-11-20"), _c(602, kind="slack_mention"), _c(603, due="2026-11-24"),
             _c(604, by="ripple", due="2026-11-21"), _c(605), _c(606, due="2026-12-30")]
    want = ["t:302", "s:601", "t:481",                                    # tier 0: hard and due by tomorrow, cards due by tomorrow; by date
            "t:250", "s:603", "t:251", "s:604", "t:252", "t:301", "t:300",
            "t:482", "t:483", "t:484", "t:478", "t:477", "t:479", "t:480",  # new: dated first, then by score, then by number
            "t:253", "t:254",
            "s:606", "s:602", "s:605"]                                     # tier 2: cards with a later date or none
    assert _ordered(tasks + cards) == want
    assert _ordered(list(reversed(tasks + cards))) == want                 # the key decides, not the order given


def test_evidence_lines_for_a_thread_update_a_reply_and_a_slack_mention():
    item = {"evidence": [
        {"id": 1, "kind": "thread_updated", "label": "Re: the abstract", "reason": "Max Mustermann", "at": "2026-11-18T06:57:16", "change": None},
        {"id": 2, "kind": "reply_from_waiting", "label": "Invoice", "reason": "prof@example.edu", "at": "2026-11-18T07:00:00", "change": None},
        {"id": 3, "kind": "maybe_reply_from_waiting", "label": "Hello", "reason": "\"Dana Doe\" <dana@example.org>", "at": "2026-11-18T08:00:00Z", "change": None},
        {"id": 4, "kind": "proposal", "label": "Task-1 → due Fri", "reason": "r", "at": "2026-11-18T08:00:00Z", "change": {"op": "set"}},
        {"id": 5, "kind": "slack_mention", "label": "#general · Max M: can you send the slides?", "reason": "Max M in #general", "at": "2026-11-19T06:14:46Z", "change": None},
        {"id": 6, "kind": "slack_mention", "label": "a label in another shape", "reason": "x", "at": "2026-11-19T06:14:46Z", "change": None},
        {"id": 7, "kind": "thread_updated", "label": "x" * 500, "reason": None, "at": None, "change": None}]}
    lines = review_queue.evidence_lines(item)
    assert lines == [
        {"kind": "mail", "who": "Max Mustermann", "subject": "Re: the abstract", "at": "2026-11-18T06:57:16+00:00"},
        {"kind": "mail", "who": "someone", "subject": "Invoice", "at": "2026-11-18T07:00:00+00:00", "reply": True},       # an address is never shown alone
        {"kind": "mail", "who": "Dana Doe", "subject": "Hello", "at": "2026-11-18T08:00:00+00:00", "maybe": True},
        {"kind": "slack", "who": "Max M", "where": "general", "text": "can you send the slides?", "at": "2026-11-19T06:14:46+00:00"},
        {"kind": "slack", "label": "a label in another shape", "at": "2026-11-19T06:14:46+00:00"},
        {"kind": "mail", "who": "someone", "subject": "x" * 200, "at": None}]
    assert review_queue.evidence_lines({"evidence": []}) == [] and review_queue.evidence_lines({}) == []


def test_get_review_lists_tasks_and_cards_each_in_order_key_order(cfg, tmp_path):
    client = TestClient(create_app(cfg, db_path=str(tmp_path / "a.db")))
    c = client.app.state.conn
    today = review_queue.today(cfg)
    iso = lambda n: (today + timedelta(days=n)).isoformat()                       # noqa: E731
    for i in range(1, 6):
        mk(c, i)
    mk(c, 6, name="Hard today", due=iso(0), hard=1)
    mk(c, 7, name="Max wrote back", last_reviewed_at=iso(-1), longterm=1)
    c.execute("INSERT INTO evidence (task_id, kind, ref, label, excerpt, at) VALUES (7, 'thread_updated', 'conv-1', 'Re: slides', 'Max M', '2026-11-18T06:57:16')")
    e_none = _card_row(c, "Card with no date")
    e_soon = _card_row(c, "Card due tomorrow", due=iso(1))
    ecfg = C.effective(cfg, get_settings(c))
    rv = client.get("/api/review").json()
    keys = [i["key"] for i in rv["items"]]
    assert keys[:2] == ["t:6", "t:7"] and keys == _ordered(rv["items"], today)
    assert [s["key"] for s in rv["suggestions"]] == [f"s:{e_soon}", f"s:{e_none}"]
    assert all("evidence_lines" in i for i in rv["items"])
    assert next(i for i in rv["items"] if i["key"] == "t:7")["evidence_lines"] == [{"kind": "mail", "who": "Max M", "subject": "Re: slides", "at": "2026-11-18T06:57:16+00:00"}]
    one_item = review_queue.task_item(c, ecfg, today, 7)
    assert one_item["evidence_lines"] == review_queue.evidence_lines(one_item)
