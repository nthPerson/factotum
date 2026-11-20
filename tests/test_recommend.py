"""Spoken Review, stage 2c: a recommended answer (factotum/recommend.py, design §6.8). A recommendation is Python's, from
what the database already knows (no Claude run), and data on the item: it says why under the button, it never recommends
a close the design calls risky (it says so instead), and it never stages anything by itself. Only the owner choosing the
pass's option `rec` stages its steps, through the rule, exactly as the same presses one by one would."""
import json
import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from stage4_helpers import link, mk
from test_polish import _card as _kind_card

from factotum import batches, config as C, recommend as R, relations, review_decide, review_pass as P, review_queue, tray
from factotum.api import create_app
from factotum.db import now_iso

D = date(2026, 11, 19)                   # Review's day: a Thursday
BY = "page · Desk"


def T(**kw):
    """A task as the queue ships it (decorated, `rel` compacted)."""
    base = {"id": 1, "name": "Write the report", "status": "open", "hard": 0, "due": None, "top_rank": None}
    base.update(kw)
    return base


def E(reasons, task=None, evidence=(), sugg=(), **kw):
    """A task item as review_queue.items ships it."""
    t = task or T()
    return dict({"key": f"t:{t['id']}", "task": t, "reasons": list(reasons), "primary": reasons[0] if reasons else None,
                 "evidence": list(evidence), "link_suggestions": list(sugg), "flags": {}}, **kw)


def prop(eid, op, fields=None, reason="r", by=None, **more):
    return {"id": eid, "kind": "proposal", "label": op, "reason": reason, "by": by,
            "change": dict({"op": op, "task_id": 1, "fields": fields or {}}, **more)}


def sg(eid, kind, other=2, why="both name the numbers", dir="out"):
    return {"evidence_id": eid, "kind": kind, "other": other, "keep": other if kind == "same_as" else None, "why": why,
            "signal": "planner", "dir": dir, "other_name": f"Task {other}"}


def card(due=None, eid=50):
    return {"key": f"s:{eid}", "id": eid, "kind": "proposal", "label": "create: x", "reason": "r", "at": "2026-11-18T10:00:00",
            "fields": {"name": "Book the bench", **({"due": due} if due else {})}, "mail": None, "links": [], "by": None}


# --- one test per rule row (design §6.8) ----------------------------------------------------------------------

def test_new_is_keep():
    r = R.recommend(E(["new", "fading"]), D, {})
    assert r["answer"] == "keep" and r["steps"] == ["keep"] and r["label"] == "Keep it" and r["said"] == "Keep it?"
    assert r["source"] == "rule:new" and r["basis"] and r["basis_said"] and len(r["basis_said"].split()) <= 12


def test_freed_is_keep():
    r = R.recommend(E(["freed"]), D, {})
    assert r["steps"] == ["keep"] and r["source"] == "rule:freed" and "waited on" in r["basis"]


def test_a_linked_change_whose_text_was_brought_up_to_date_is_keep_and_any_other_is_none():
    since = {"batch_id": 9, "undone": False, "by": [], "text": {"before": "a", "after": "b", "note": None}}
    assert R.recommend(E(["linked_changed"], since=since), D, {})["steps"] == ["keep"]
    assert R.recommend(E(["linked_changed"], since=dict(since, text=None)), D, {}) is None
    assert R.recommend(E(["linked_changed"], since=dict(since, undone=True)), D, {}) is None
    assert R.recommend(E(["linked_changed"]), D, {}) is None


def test_all_steps_done_is_done():
    r = R.recommend(E(["steps_done"], task=T(rel={"steps_open": 0, "steps_total": 2, "frees_open": []})), D, {})
    assert r["answer"] == "done" and r["steps"] == ["done"] and r["label"] == "Done" and r["said"] == "Mark it done?"


def test_a_suggested_link_alone_is_link_and_with_another_reason_link_and_keep():
    r = R.recommend(E(["link_suggested"], sugg=[sg(7, "waits_on")]), D, {})
    assert r["answer"] == "link" and r["steps"] == ["link:7"] and r["label"] == "Link it" and r["source"] == "rule:link"
    assert r["basis"] == "Both name the numbers."
    r = R.recommend(E(["link_suggested", "new"], sugg=[sg(7, "waits_on"), sg(8, "part_of", other=3, why="a chapter step", dir="in")]), D, {})
    assert r["answer"] == "link_keep" and r["steps"] == ["link:7", "link:8", "keep"]
    assert r["label"] == "Link both and keep it" and r["said"] == "Link both and keep it?"


def test_a_suggested_link_of_no_kind_gets_none():
    assert R.recommend(E(["link_suggested", "new"], sugg=[sg(7, "waits_on"), sg(9, "any", other=5)]), D, {}) is None


def test_a_follow_thread_proposal_is_follow_and_keep_with_its_own_reason():
    e = prop(31, "follow_thread", {"conversation_id": "c"}, reason="The booking mail names this pitch.")
    r = R.recommend(E(["thread_updated", "planner"], evidence=[e, {"id": 32, "kind": "thread_updated", "label": "Re", "reason": "Max", "change": None}]), D, {})
    assert r["answer"] == "follow_keep" and r["steps"] == ["follow:31", "keep"] and r["basis"] == "The booking mail names this pitch."
    assert r["source"] == "proposal:follow_thread"


@pytest.mark.parametrize("op, fields", [("set", {"due": "2026-11-20T14:00"}), ("add_reminder", {"fire_at": "2026-11-20T09:00"}), ("pin_top", {}),
                                        ("longterm", {}), ("link", {"kind": "waits_on", "other": 2}), ("add_note", {"text": "Max agreed"})])
def test_a_proposal_beside_the_decision_is_accept_and_keep(op, fields):
    r = R.recommend(E(["planner"], evidence=[prop(40, op, fields, reason="The booking mail gives the time.")]), D, {})
    assert r["answer"] == "accept_keep" and r["steps"] == ["accept:40", "keep"] and r["basis"] == "The booking mail gives the time."
    assert r["label"].endswith("and keep it") and r["said"].endswith("and keep it?") and r["source"] == f"proposal:{op}"


def test_a_follow_and_one_other_proposal_are_both_taken():
    r = R.recommend(E(["planner"], evidence=[prop(31, "follow_thread"), prop(40, "set", {"due": "2026-11-20"}, reason="The mail gives the day.")]), D, {})
    assert r["steps"] == ["follow:31", "accept:40", "keep"] and r["label"].startswith("Follow the thread, ") and r["basis"] == "The mail gives the day."


@pytest.mark.parametrize("op, answer, label", [("done", "done", "Mark it done"), ("drop", "drop", "Drop it")])
def test_a_done_or_drop_proposal_on_a_task_that_is_not_risky_is_accept(op, answer, label):
    r = R.recommend(E(["planner"], evidence=[prop(41, op, {"outcome": "Sent"}, reason="Max says it arrived.")]), D, {})
    assert r["answer"] == answer and r["steps"] == ["accept:41"] and r["label"] == label and r["basis"] == "Max says it arrived."


def test_a_proposal_with_no_reason_says_who_suggested_it():
    assert R.recommend(E(["planner"], evidence=[prop(40, "pin_top", reason="")]), D, {})["basis"] == R.TEXTS["planner_basis"]
    assert R.recommend(E(["ripple"], evidence=[prop(40, "pin_top", reason=None, by="ripple")]), D, {})["basis"] == R.TEXTS["ripple_basis"]


def test_a_card_whose_date_passed_is_dismiss():
    r = R.recommend(card(due="2026-11-18"), D, {})
    assert r["answer"] == "dismiss" and r["steps"] == ["dismiss"] and r["label"] == "Dismiss it" and r["source"] == "rule:card_past"
    assert R.recommend(card(due="2026-11-18T09:00"), D, {})["steps"] == ["dismiss"]


# --- and none where the data does not support one ----------------------------------------------------------------

@pytest.mark.parametrize("entry", [
    pytest.param(card(due="2026-11-19"), id="a card due today"),
    pytest.param(card(due="2026-11-25"), id="a card due later"),
    pytest.param(card(), id="a card with no date"),
    pytest.param(E(["new"], sugg=[sg(9, "same_as", other=4)]), id="a merge suggestion"),
    pytest.param(E(["link_suggested", "new"], sugg=[sg(7, "waits_on"), sg(9, "same_as", other=4)]), id="a merge among the links"),
    pytest.param(E(["planner"], evidence=[prop(40, "set", {"due": "2026-11-20"}), prop(41, "pin_top")]), id="two proposals"),
    pytest.param(E(["planner"], evidence=[prop(40, "merge", {"into": 4})]), id="a merge proposal"),
    pytest.param(E(["planner"], evidence=[prop(40, "reopen")]), id="a proposal the rule cannot stage"),
    pytest.param(E(["planner", "link_suggested"], evidence=[prop(40, "pin_top")], sugg=[sg(7, "waits_on")]), id="a proposal and a suggested link"),
    pytest.param(E(["thread_updated"], evidence=[{"id": 32, "kind": "thread_updated", "label": "Re", "reason": "Max", "change": None}]), id="thread updated"),
    pytest.param(E(["mentioned", "new"]), id="mentioned in Slack"),
    pytest.param(E(["fading"]), id="fading"),
    pytest.param(E(["slipped", "fading"]), id="slipped"),
    pytest.param(E(["last_call"]), id="last call"),
    pytest.param(E(["back"]), id="back"),
    pytest.param(E(["snooze_over"]), id="snooze over"),
    pytest.param(E(["blocker_gone"]), id="what it waited on was dropped"),
    pytest.param(E([]), id="no reason"),
])
def test_none_where_the_owner_s_answer_is_not_known(entry):
    assert R.recommend(entry, D, {}) is None and R.held(entry, D) is None


# --- a close the design calls risky -------------------------------------------------------------------------------

RISKY = [
    pytest.param({"hard": 1, "due": "2026-11-21"}, "its firm date is within two days", id="hard, due in two days"),
    pytest.param({"hard": 1, "due": "2026-11-17T09:00"}, "its firm date is within two days", id="hard, two days past"),
    pytest.param({"top_rank": 1}, "it is on Top", id="on Top"),
    pytest.param({"rel": {"steps_open": 1, "steps_total": 2, "frees_open": []}}, "it has open steps", id="open steps"),
    pytest.param({"rel": {"steps_open": 0, "steps_total": 0, "frees_open": [9]}}, "an open task waits on it", id="frees an open task"),
]


@pytest.mark.parametrize("fields, why", RISKY)
def test_risky_close_names_its_reason(fields, why):
    assert review_queue.risky_close(T(**fields), D) == why
    assert review_queue.risky_close(T(hard=1, due="2026-11-22"), D) is None          # three days on: not near
    assert review_queue.risky_close(T(due="2026-11-19"), D) is None                  # a soft date is no reason
    assert review_queue.risky_close(T(), D) is None


@pytest.mark.parametrize("fields, why", RISKY)
def test_no_close_recommended_on_a_risky_task(fields, why):
    rel = dict({"steps_open": 0, "steps_total": 2, "frees_open": []}, **(fields.get("rel") or {}))
    t = T(**dict(fields, rel=rel))
    steps_done = E(["steps_done"], task=t)
    assert R.recommend(steps_done, D, {}) is None and R.held(steps_done, D) == f"Not suggesting Done: {why}."
    drop = E(["planner"], task=t, evidence=[prop(41, "drop")])
    assert R.recommend(drop, D, {}) is None and R.held(drop, D) == f"Not suggesting Drop: {why}."
    beside = E(["planner"], task=t, evidence=[prop(40, "pin_top")])                 # not a close: still recommended
    assert R.recommend(beside, D, {})["steps"] == ["accept:40", "keep"] and R.held(beside, D) is None


# --- words -----------------------------------------------------------------------------------------------------

def test_the_basis_names_a_task_by_its_name_never_as_task_n():
    e = E(["link_suggested"], sugg=[sg(7, "waits_on", why="Task-1's text names Task-12"), sg(8, "part_of", other=13, why="Task-99 said so")])
    r = R.recommend(e, D, {12: "Order the reagent"})
    assert not re.search(r"Task-\d", r["basis"] + r["basis_said"] + r["label"] + r["said"])
    assert "Order the reagent" in r["basis"] and "this task" in r["basis"].lower() and "another task" in r["basis"].lower()
    r = R.recommend(E(["planner"], evidence=[prop(40, "pin_top", reason="Due with Task-12 tomorrow")]), D, {12: "Order the reagent"})
    assert r["basis"] == "Due with Order the reagent tomorrow"


def test_basis_said_is_at_most_twelve_words():
    long = "One two three four five six seven eight nine ten eleven twelve thirteen fourteen."
    r = R.recommend(E(["planner"], evidence=[prop(40, "pin_top", reason=long)]), D, {})
    assert r["basis"] == long and r["basis_said"] == "One two three four five six seven eight nine ten eleven twelve."
    r = R.recommend(E(["planner"], evidence=[prop(40, "pin_top", reason="It can only go after the screenshots are sent to Sam and the lab.")]), D, {})
    assert r["basis_said"] == "It can only go after the screenshots are sent to Sam."                # never ends on a small word
    r = R.recommend(E(["planner"], evidence=[prop(40, "pin_top", reason="The booking is set. It is at two on Friday in the hall by the lab.")]), D, {})
    assert r["basis_said"] == "The booking is set."                                              # the first sentence, when short
    r = R.recommend(E(["planner"], evidence=[prop(40, "pin_top", reason="word " * 80)]), D, {})
    assert len(r["basis"]) <= R.BASIS_MAX and r["basis"].endswith("word…")


def test_every_fixed_text_is_in_texts():
    for e in (E(["new"]), E(["freed"]), E(["steps_done"]), card(due="2026-11-01")):
        r = R.recommend(e, D, {})
        assert r["said"] in R.TEXTS.values() and r["label"] in R.TEXTS.values()


# --- the database: the View, the option `rec`, the switch ---------------------------------------------------------

def begin(c, cfg, today=D):
    v = P.begin_or_resume(c, cfg, today, by=BY)
    c.commit()
    return v


def dec(c, cfg, key, option, **kw):
    v = P.view(c, cfg, D)
    out = P.decide(c, cfg, D, v["id"], key, option, origin="page", by=BY, **kw)
    c.commit()
    return out, P.view(c, cfg, D)


def entries(c):
    return [(k, e, m["decides"]) for k, e, m in tray.read(c)["entries"]]


def test_the_view_carries_the_recommendation_for_the_item_in_front_and_ahead(conn, cfg):
    mk(conn, 1)
    mk(conn, 2)
    v = begin(conn, cfg)
    cur, ahead = v["current"], v["ahead"]
    assert cur["recommendation"]["steps"] == ["keep"] and ahead["recommendation"]["steps"] == ["keep"]
    assert cur["options"][0] == {"id": "rec", "label": "Keep it", "kind": "rec", "key": "a"}
    assert cur["rec_held"] is None
    items = review_queue.items(conn, cfg, D)
    assert [e["recommendation"]["answer"] for e in items["tasks"]] == ["keep", "keep"]


def test_a_recommendation_never_stages_anything(conn, cfg):
    mk(conn, 1)
    mk(conn, 2, top_rank=1)
    begin(conn, cfg)
    before, rev = conn.total_changes, tray.rev(conn)
    for _ in range(2):
        review_queue.items(conn, cfg, D)
        P.view(conn, cfg, D)
        R.recommend(review_queue.items(conn, cfg, D)["tasks"][0], D, {})
    assert conn.total_changes == before and tray.rev(conn) == rev and tray.read(conn)["entries"] == []


def _linked_world(c):
    mk(c, 1, name="Send the screenshots", hard=1, due="2026-11-19")      # due today, firm: first in Review's order
    mk(c, 2, name="Complete the submission")
    mk(c, 3, name="Apply the scenery")
    a = relations.suggest(c, "part_of", 1, 2, "both were said in the meeting", "planner")
    b = relations.suggest(c, "waits_on", 3, 1, "both were said in the meeting", "planner")
    c.execute("UPDATE tasks SET last_reviewed_at = '2026-11-18', last_mention_at = '2026-11-18' WHERE id IN (2, 3)")
    c.commit()
    return a, b


def _proposal_world(c):
    mk(c, 1, name="Prepare the pitch")
    c.execute("UPDATE tasks SET last_reviewed_at = '2026-11-18', last_mention_at = '2026-11-18' WHERE id = 1")
    for ref, label, reason in (({"op": "follow_thread", "task_id": 1, "fields": {"conversation_id": "conv-9", "account": "me@example.edu", "subject": "Booked", "at": "2026-11-18T10:00:00Z"}},
                                "This mail is about Task-1. Follow its thread?", "The booking mail names the pitch."),
                               ({"op": "set", "task_id": 1, "fields": {"due": "2026-11-20T14:00"}}, "due tomorrow 2 PM", "The booking mail gives the time.")):
        c.execute("INSERT INTO evidence (task_id, kind, ref, label, excerpt, at) VALUES (1, 'proposal', ?, ?, ?, ?)", (json.dumps(ref), label, reason, now_iso()))
    c.execute("INSERT INTO evidence (task_id, kind, ref, label, excerpt, at) VALUES (1, 'thread_updated', 'conv-9', 'Booked', 'Lavin Center', ?)", (now_iso(),))
    c.commit()


@pytest.mark.parametrize("world", [_linked_world, _proposal_world])
def test_rec_stages_the_same_entries_as_its_steps_one_by_one(conn, cfg, world):
    world(conn)
    v = begin(conn, cfg)
    key = v["cursor_key"]
    rec = v["current"]["recommendation"]
    assert rec and len(rec["steps"]) >= 2
    out, v2 = dec(conn, cfg, key, "rec", note="Talked to Max")
    got = entries(conn)
    assert out["advanced"] is True and v2["cursor_key"] != key and set(out["keys"]) == {k for k, _, _ in got}
    tray.write(conn, [{"del": k} for k, _, _ in got], "page")
    conn.commit()
    for s in rec["steps"]:
        review_decide.stage(conn, cfg, key, s, {"note": "Talked to Max"} if s == "keep" else {}, D, origin="page")
        conn.commit()
    assert entries(conn) == got
    assert any(k.endswith(":note") for k, _, _ in got)                       # the note rode on the decision


def test_rec_without_a_recommendation_is_refused_and_stages_nothing(conn, cfg):
    mk(conn, 1)
    mk(conn, 2, name="Goal")
    link(conn, 1, 2, "part_of")
    batches.apply(conn, [{"op": "done", "task_id": 1, "fields": {}}], "ui", D)
    conn.execute("UPDATE tasks SET last_reviewed_at = '2026-11-18', last_mention_at = '2026-11-18', top_rank = 1 WHERE id = 2")
    conn.commit()
    v = begin(conn, cfg)
    assert v["cursor_key"] == "t:2" and v["current"]["item"]["reasons"] == ["steps_done"]
    assert v["current"]["recommendation"] is None and v["current"]["rec_held"] == "Not suggesting Done: it is on Top."
    assert "rec" not in [o["id"] for o in v["current"]["options"]]
    with pytest.raises(review_decide.Refused):
        dec(conn, cfg, "t:2", "rec")
    conn.rollback()
    assert entries(conn) == []


def test_a_note_with_a_recommendation_that_takes_none_is_refused(conn, cfg):
    _linked_world(conn)
    conn.execute("UPDATE tasks SET last_reviewed_at = '2026-11-18' WHERE id = 1")
    conn.commit()
    v = begin(conn, cfg)
    assert v["current"]["recommendation"]["answer"] == "link"
    with pytest.raises(review_decide.Refused):
        dec(conn, cfg, v["cursor_key"], "rec", note="hi")
    conn.rollback()
    out, v2 = dec(conn, cfg, v["cursor_key"], "rec")
    assert out["advanced"] is True and {r["key"]: r["state"] for r in v2["list"]}[v["cursor_key"]] == "answered"


def test_a_steps_done_goal_is_recommended_done_and_rec_stages_done(conn, cfg):
    mk(conn, 1)
    mk(conn, 2, name="Goal")
    link(conn, 1, 2, "part_of")
    batches.apply(conn, [{"op": "done", "task_id": 1, "fields": {}}], "ui", D)
    conn.execute("UPDATE tasks SET last_reviewed_at = '2026-11-18', last_mention_at = '2026-11-18' WHERE id = 2")
    conn.commit()
    v = begin(conn, cfg)
    assert v["current"]["recommendation"]["steps"] == ["done"]
    dec(conn, cfg, "t:2", "rec")
    assert [(k, e["op"], d) for k, e, d in entries(conn)] == [("2", "done", "t:2")]


def test_recommend_off_in_settings_shows_none(conn, cfg, tmp_path):
    off = dict(cfg, review={"recommend": False})
    mk(conn, 1)
    mk(conn, 2)
    v = begin(conn, off)
    assert v["current"]["recommendation"] is None and v["ahead"]["recommendation"] is None
    assert "rec" not in [o["id"] for o in v["current"]["options"]]
    assert all(e["recommendation"] is None for e in review_queue.items(conn, off, D)["tasks"])
    with pytest.raises(review_decide.Refused):
        dec(conn, off, "t:1", "rec")
    conn.rollback()
    client = TestClient(create_app(cfg, db_path=str(tmp_path / "a.db")))
    c = client.app.state.conn
    mk(c, 1)
    assert client.put("/api/settings", json={"values": {"review.recommend": False}}).status_code == 200
    p = client.post("/api/review/pass", json={}).json()["pass"]
    assert p["current"]["recommendation"] is None
    assert client.get("/api/review").json()["items"][0]["recommendation"] is None
    assert client.post("/api/review/decide", json={"key": "t:1", "option": "rec"}).status_code == 400
    assert client.put("/api/settings", json={"values": {"review.recommend": True}}).status_code == 200
    assert client.get("/api/review/pass").json()["pass"]["current"]["recommendation"]["steps"] == ["keep"]
    r = client.post("/api/review/decide", json={"key": "t:1", "option": "rec"})
    assert r.status_code == 200 and r.json()["keys"] == ["1"] and r.json()["pass"]["state"] == "end"


def test_review_recommend_is_a_settings_key_on_when_absent_and_free():
    from conftest import FIXTURE
    assert "review.recommend" in C.SETTINGS_KEYS and C.DEFAULTS["review.recommend"] is True
    assert C.value({}, "review.recommend") is True
    assert "review.recommend" not in C.PAID_SWITCHES and "review.recommend" not in C.ON_WHEN_NEW
    for path in (C.EXAMPLE_CONFIG, FIXTURE, C.STARTER_CONFIG):
        assert C.get(C.load(path), "review.recommend", "absent") is True, path.name


def test_a_card_whose_date_passed_is_recommended_dismiss_in_the_view(conn, cfg):
    eid = _kind_card(conn, "planner")
    ref = {"op": "create", "fields": {"name": "Go to the dentist", "due": "2026-11-18"}}
    conn.execute("UPDATE evidence SET ref = ? WHERE id = ?", (json.dumps(ref), eid))
    conn.commit()
    v = begin(conn, cfg)
    assert v["current"]["recommendation"]["steps"] == ["dismiss"]
    assert [o["id"] for o in v["current"]["options"]] == ["rec", "create", "dismiss"]
    out, v = dec(conn, cfg, f"s:{eid}", "rec")
    assert out["keys"] == [f"e:{eid}"] and v["state"] == "end"
    assert entries(conn)[0][1]["fields"]["verdict"] == "dismissed"
