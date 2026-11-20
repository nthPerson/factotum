"""Spoken Review, stage 2d: alike new tasks together (design §6.9). A pass offers the new tasks of one recording, one
Canvas course or one mail thread as one item, a group (`g:rec:<id>`, `g:course:<project>`, `g:thread:<conversation>`),
built when the pass begins and when items arrive. The tray never sees a group key: Keep all stages one Keep per
undecided member, each its own row, undoable and taken back alone; a member can be decided alone, taken out, or the
group broken into its members; a group left with one member is that task; a member that gains a reason leaves by
itself. `review.group_alike` off: no new groups, and a pass begun before the switch changed keeps its own."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from stage4_helpers import mk
from test_review_pass import BY, D, begin, dec, mv, offered, view

from factotum import config as C, relations, review_decide, review_pass as P, review_queue, tray
from factotum.api import create_app
from factotum.db import add_link, now_iso

LABEL = "11-18 Project Meeting: pilots, slides and the billboard"


def new(c, id, rec=None, course=None, thread=None, **kw):
    """A task with no reason on Review but New (made yesterday, never reviewed): from a recording, a course or a thread."""
    kw.setdefault("last_mention_at", "2026-11-18")
    kw.setdefault("created_at", "2026-11-18T20:00:00+00:00")
    kw.setdefault("source", "Manual")
    if rec:
        kw.update(source="Plaud", source_key=f"plaud:{rec}:{id}")
    if course:
        kw.update(source="Canvas", project=course)
    mk(c, id, **kw)
    if rec:
        add_link(c, id, "plaud_note", rec, label=LABEL)
    if thread:
        add_link(c, id, "mail_thread", thread, label="Pilot dates for December")
    c.commit()
    return id


def loose(c, id):
    """A task on Review for a slipped date, reviewed before: it ranks after New, so after a group."""
    return new(c, id, due="2026-11-10", last_reviewed_at="2026-11-01")


def keys(v):
    return [r["key"] for r in v["list"]]


def group_of(v):
    return next(r for r in v["list"] if r["key"].startswith("g:"))


def members(c, gkey):
    r = c.execute("SELECT members_json FROM review_pass_items WHERE key = ?", (gkey,)).fetchone()
    return json.loads(r[0]) if r else None


def entries(c):
    return {k: (e.get("op"), m["decides"]) for k, e, m in tray.read(c)["entries"]}


def off(cfg):
    o = copy.deepcopy(cfg)
    o.setdefault("review", {})["group_alike"] = False
    return o


def first_on_group(c, cfg):
    v = view(c, cfg)
    g = group_of(v)["key"]
    if v["cursor_key"] != g:
        _, v = mv(c, cfg, "goto", key=g, from_key=None)
    return g, v


# --- what is grouped --------------------------------------------------------------------------------------

def test_a_recordings_new_tasks_are_one_group_with_its_title(conn, cfg):
    for i in (1, 2, 3):
        new(conn, i, rec="of_aaa", due=f"2026-11-2{4 - i}")
    new(conn, 4)
    v = begin(conn, cfg)
    assert sorted(keys(v)) == ["g:rec:of_aaa", "t:4"]
    assert members(conn, "g:rec:of_aaa") == [3, 2, 1]                       # by due, then id
    g, v = first_on_group(conn, cfg)
    cur = v["current"]
    assert cur["kind"] == "group" and cur["provenance"] == {"kind": "recording", "title": "Project Meeting", "day": "2026-11-18"}
    assert [m["key"] for m in cur["item"]["members"]] == ["t:3", "t:2", "t:1"] and cur["item"]["provenance"] == cur["provenance"]
    assert all(m["state"] == "waiting" and m["task"]["name"] for m in cur["item"]["members"])
    assert [o["id"] for o in cur["options"]][:2] == ["rec", "keep_all"]
    assert cur["recommendation"]["steps"] == ["keep_all"] and cur["recommendation"]["label"] == "Keep all 3"
    assert v["counts"]["items"] == 2 and v["counts"]["left"] == 4               # the tab's number counts tasks one by one


@pytest.mark.parametrize("label, title", [(LABEL, "Project Meeting"), ("11-19 Meeting: Vehicle-Sound AI Development, Data Collection", "Meeting: Vehicle-Sound AI Development"),
                                          ("2026-11-19 Standup", "Standup"), ("Lab sync", "Lab sync"), ("", None), ("11-19 ", None)])
def test_a_recordings_title_drops_its_date_and_what_follows_the_colon(label, title):
    assert P._title(label) == title


def test_a_group_needs_two_and_splits_after_eight(conn, cfg):
    new(conn, 1, rec="of_one")
    for i in range(10, 21):
        new(conn, i, rec="of_big")
    v = begin(conn, cfg)
    assert "t:1" in keys(v) and "g:rec:of_one" not in keys(v)
    assert members(conn, "g:rec:of_big") == list(range(10, 16)) and members(conn, "g:rec:of_big:2") == list(range(16, 21))
    assert all(2 <= len(members(conn, k)) <= P.GROUP_MAX for k in keys(v) if k.startswith("g:"))


def _e(task=None, reasons=("new",), **kw):
    t = dict({"id": 1, "name": "x", "status": "open", "hard": 0, "due": None, "top_rank": None}, **(task or {}))
    return dict({"key": "t:1", "task": t, "reasons": list(reasons), "evidence": [], "link_suggestions": [],
                 "recommendation": {"answer": "keep", "steps": ["keep"]}}, **kw)


@pytest.mark.parametrize("entry", [
    _e(reasons=("new", "fading")), _e(reasons=("last_call", "new")), _e(task={"top_rank": 1}),
    _e(task={"hard": 1, "due": "2026-11-21"}), _e(task={"hard": 1, "due": "2026-11-12"}),
    _e(evidence=[{"id": 4, "kind": "thread_updated"}]), _e(link_suggestions=[{"evidence_id": 5, "kind": "waits_on"}]),
    _e(recommendation={"answer": "done", "steps": ["done"]})], ids=["fading", "last-call", "top", "hard-soon", "hard-past",
                                                                    "evidence", "link", "rec-close"])
def test_a_member_with_another_reason_evidence_a_link_top_a_near_hard_date_or_a_close_stands_alone(entry):
    assert not P.groupable(entry, D)


def test_groupable_is_new_alone_and_a_far_hard_date_or_no_recommendation_is_fine():
    assert P.groupable(_e(), D) and P.groupable(_e(recommendation=None), D)
    assert P.groupable(_e(task={"hard": 1, "due": "2026-11-30"}), D)


def test_a_member_on_top_or_near_its_hard_date_stands_alone_in_the_pass(conn, cfg):
    for i in (1, 2, 3, 4):
        new(conn, i, rec="of_aaa")
    conn.execute("UPDATE tasks SET top_rank = 1 WHERE id = 2")
    conn.execute("UPDATE tasks SET hard = 1, due = '2026-11-20' WHERE id = 3")
    conn.commit()
    v = begin(conn, cfg)
    assert members(conn, "g:rec:of_aaa") == [1, 4] and {"t:2", "t:3"} <= set(keys(v))


def test_course_and_thread_groups_and_a_recording_comes_first(conn, cfg):
    new(conn, 1, course="GEOG 582")
    new(conn, 2, course="GEOG 582")
    new(conn, 3, thread="conv-1")
    new(conn, 4, thread="conv-1")
    new(conn, 5, rec="of_r", thread="conv-1")
    new(conn, 6, rec="of_r", thread="conv-1")
    v = begin(conn, cfg)
    assert sorted(k for k in keys(v)) == ["g:course:GEOG 582", "g:rec:of_r", "g:thread:conv-1"]
    assert members(conn, "g:course:GEOG 582") == [1, 2] and members(conn, "g:thread:conv-1") == [3, 4] and members(conn, "g:rec:of_r") == [5, 6]
    _, v = mv(conn, cfg, "goto", key="g:thread:conv-1", from_key=None)
    assert v["current"]["provenance"] == {"kind": "thread", "title": "Pilot dates for December", "day": None}
    _, v = mv(conn, cfg, "goto", key="g:course:GEOG 582", from_key=None)
    assert v["current"]["provenance"] == {"kind": "course", "title": "GEOG 582", "day": None}


def test_none_when_review_group_alike_is_off(conn, cfg):
    for i in (1, 2, 3):
        new(conn, i, rec="of_aaa")
    v = begin(conn, off(cfg))
    assert keys(v) == ["t:1", "t:2", "t:3"]


def test_review_group_alike_is_a_settings_key_on_when_absent_and_free():
    from conftest import FIXTURE
    assert "review.group_alike" in C.SETTINGS_KEYS and C.DEFAULTS["review.group_alike"] is True
    assert C.value({}, "review.group_alike") is True
    assert "review.group_alike" not in C.PAID_SWITCHES and "review.group_alike" not in C.ON_WHEN_NEW
    for path in (C.EXAMPLE_CONFIG, FIXTURE, C.STARTER_CONFIG):
        assert C.get(C.load(path), "review.group_alike", "absent") is True, path.name


# --- moving onto and across a group --------------------------------------------------------------------------

def _world(c):
    """Task 9 is urgent (a firm date today) and so first; then the recording's three; then 4, slipped."""
    new(c, 9, hard=1, due=D.isoformat())
    for i in (1, 2, 3):
        new(c, i, rec="of_aaa")
    loose(c, 4)


def test_arriving_at_a_group_stamps_every_member_and_goto_a_member_lands_on_its_group(conn, cfg):
    _world(conn)
    v = begin(conn, cfg)
    assert v["cursor_key"] == "t:9" and [offered(conn, i) for i in (1, 2, 3)] == [None, None, None]
    out, v = mv(conn, cfg, "goto", key="t:2", from_key=None)
    assert out["moved"] and v["cursor_key"] == "g:rec:of_aaa"
    assert [offered(conn, i) for i in (1, 2, 3)] == [D.isoformat()] * 3 and offered(conn, 4) is None


def test_next_and_back_across_a_group(conn, cfg):
    _world(conn)
    v = begin(conn, cfg)
    assert keys(v) == ["t:9", "g:rec:of_aaa", "t:4"]
    _, v = mv(conn, cfg, "next")
    assert v["cursor_key"] == "g:rec:of_aaa" and v["ahead"]["key"] == "t:4"
    _, v = mv(conn, cfg, "next")
    assert v["cursor_key"] == "t:4" and {r["key"]: r["state"] for r in v["list"]}["g:rec:of_aaa"] == "passed"
    _, v = mv(conn, cfg, "back")
    assert v["cursor_key"] == "g:rec:of_aaa" and v["current"]["state"] == "passed"
    _, v = mv(conn, cfg, "back")
    assert v["cursor_key"] == "t:9"


def test_the_list_shows_the_group_and_its_members_states(conn, cfg):
    _world(conn)
    begin(conn, cfg)
    dec(conn, cfg, "t:2", "done")
    v = view(conn, cfg)
    g = group_of(v)
    assert g["kind"] == "group" and g["state"] == "waiting" and g["name"] and g["why"]
    assert [(m["key"], m["state"], m["staged"]) for m in g["members"]] == [("t:1", "waiting", None), ("t:2", "decided", "done"), ("t:3", "waiting", None)]


# --- deciding a group -------------------------------------------------------------------------------------

def test_keep_all_stages_one_keep_per_undecided_member_and_nothing_else(conn, cfg):
    _world(conn)
    begin(conn, cfg)
    g, v = first_on_group(conn, cfg)
    out, v = dec(conn, cfg, "t:2", "done")                            # one member decided alone: the group stays in front
    assert v["cursor_key"] == g and out["advanced"] is False
    assert v["current"]["recommendation"]["label"] == "Keep the other 2"
    assert [o for o in v["current"]["options"] if o["id"] == "keep_all"][0]["label"] == "Keep the other 2"
    before = tray.rev(conn)
    out, v = dec(conn, cfg, g, "keep_all")
    assert out["advanced"] is True and v["cursor_key"] == "t:4"
    assert entries(conn) == {"1": ("keep", "t:1"), "2": ("done", "t:2"), "3": ("keep", "t:3")}
    assert tray.rev(conn) == before + 1                                # one write
    assert not any(str(m["decides"]).startswith("g:") for _, _, m in tray.read(conn)["entries"])
    assert {r["key"]: r["state"] for r in v["list"]}[g] == "decided"
    out, v = dec(conn, cfg, "t:1", "undecide")                         # each taken back alone; the group comes in front
    assert entries(conn) == {"2": ("done", "t:2"), "3": ("keep", "t:3")} and v["cursor_key"] == g and v["current"]["state"] == "waiting"
    with pytest.raises(review_decide.Refused):                         # a group key never reaches the rule or the tray
        dec(conn, cfg, g, "keep")
    conn.rollback()


def test_keep_all_with_except(conn, cfg):
    _world(conn)
    begin(conn, cfg)
    g, _ = first_on_group(conn, cfg)
    dec(conn, cfg, g, "keep_all", args={"except": [{"key": "t:2", "option": "drop", "note": "not ours"}, {"key": "t:3", "option": "later", "args": {"until": "tomorrow"}}]})
    got = {k: (e.get("op"), m["decides"]) for k, e, m in tray.read(conn)["entries"] if not k.endswith(":note")}
    assert got == {"1": ("keep", "t:1"), "2": ("drop", "t:2"), "3": ("set", "t:3")}
    whole = {k: e for k, e, _ in tray.read(conn)["entries"]}
    assert whole["2"]["fields"].get("outcome") == "not ours" and whole["3"]["fields"]["snoozed_until"] == "2026-11-20"
    for bad in ([{"key": "t:4", "option": "drop"}], [{"key": "t:1", "option": "keep_all"}], [{"key": "t:1"}], "t:1", [{"key": "t:1", "option": "rec"}]):
        with pytest.raises(review_decide.Refused):
            dec(conn, cfg, g, "keep_all", args={"except": bad})
        conn.rollback()


def test_rec_on_a_group_is_keep_all_and_a_note_goes_with_one_task_only(conn, cfg):
    _world(conn)
    begin(conn, cfg)
    g, v = first_on_group(conn, cfg)
    assert v["current"]["recommendation"]["said"] == "Keep them all?"
    out, v = dec(conn, cfg, g, "rec")
    assert entries(conn) == {"1": ("keep", "t:1"), "2": ("keep", "t:2"), "3": ("keep", "t:3")} and v["cursor_key"] == "t:4"
    with pytest.raises(review_decide.Refused):                         # nothing undecided left: nothing to keep
        dec(conn, cfg, g, "keep_all")
    conn.rollback()
    with pytest.raises(review_decide.Refused):
        dec(conn, cfg, g, "keep_all", note="a note belongs to one task")
    conn.rollback()


def test_deciding_the_last_undecided_member_moves_on(conn, cfg):
    _world(conn)
    begin(conn, cfg)
    g, _ = first_on_group(conn, cfg)
    dec(conn, cfg, "t:1", "keep")
    out, v = dec(conn, cfg, "t:2", "drop")
    assert v["cursor_key"] == g and not out["advanced"]
    out, v = dec(conn, cfg, "t:3", "later", args={"until": "fri"})
    assert out["advanced"] and v["cursor_key"] == "t:4"


def test_after_apply_marks_a_group_and_ends_the_pass(conn, cfg):
    for i in (1, 2):
        new(conn, i, rec="of_aaa")
    v = begin(conn, cfg)
    assert v["cursor_key"] == "g:rec:of_aaa"
    dec(conn, cfg, "g:rec:of_aaa", "keep_all")
    res = tray.apply(conn, cfg, D, source="review", finish=True)
    assert res["applied"] == 2
    row = conn.execute("SELECT done_batch_id FROM review_pass_items WHERE key = 'g:rec:of_aaa'").fetchone()
    assert row[0] == res["batch_id"]
    assert conn.execute("SELECT end_why FROM review_passes").fetchone()[0] == "applied"


# --- regrouping -------------------------------------------------------------------------------------------

def test_take_out_puts_the_member_right_after_the_group(conn, cfg):
    _world(conn)
    begin(conn, cfg)
    g, v = first_on_group(conn, cfg)
    out = P.regroup(conn, cfg, D, v["id"], g, "take_out", task_id=2, by=BY)
    conn.commit()
    v = view(conn, cfg)
    assert keys(v) == ["t:9", g, "t:2", "t:4"] and members(conn, g) == [1, 3] and v["cursor_key"] == g
    out = P.regroup(conn, cfg, D, v["id"], "t:3", "take_out", by=BY)          # a member's own key names its group
    conn.commit()
    v = view(conn, cfg)
    assert keys(v) == ["t:9", "t:1", "t:3", "t:2", "t:4"]                     # a group left with one member is that task
    assert v["cursor_key"] == "t:1" and members(conn, g) is None
    with pytest.raises(review_decide.Refused):
        P.regroup(conn, cfg, D, v["id"], "t:1", "take_out", by=BY)
    conn.rollback()


def test_one_by_one_puts_the_members_in_place(conn, cfg):
    _world(conn)
    begin(conn, cfg)
    g, v = first_on_group(conn, cfg)
    dec(conn, cfg, "t:1", "keep")
    v = view(conn, cfg)
    P.regroup(conn, cfg, D, v["id"], g, "one_by_one", by=BY)
    conn.commit()
    v = view(conn, cfg)
    assert keys(v) == ["t:9", "t:1", "t:2", "t:3", "t:4"] and v["cursor_key"] == "t:2"       # the first undecided of them
    assert {r["key"]: r["state"] for r in v["list"]}["t:1"] == "decided"
    with pytest.raises(review_decide.Refused):
        P.regroup(conn, cfg, D, v["id"], g, "explode", by=BY)
    conn.rollback()


def test_a_group_left_with_one_member_is_that_task(conn, cfg):
    for i in (1, 2):
        new(conn, i, rec="of_aaa")
    loose(conn, 4)
    begin(conn, cfg)
    conn.execute("UPDATE tasks SET status = 'done' WHERE id = 2")         # closed elsewhere: gone from the group
    conn.commit()
    v = view(conn, cfg)
    assert "g:rec:of_aaa" not in keys(v) and keys(v)[:1] == ["t:1"] and v["cursor_key"] == "t:1"
    assert v["current"]["kind"] == "task"
    mv(conn, cfg, "next")                                                   # the next write keeps what the read showed
    assert members(conn, "g:rec:of_aaa") is None


def test_a_member_that_gains_a_reason_leaves_by_itself(conn, cfg):
    _world(conn)
    begin(conn, cfg)
    g, _ = first_on_group(conn, cfg)
    conn.execute("UPDATE tasks SET top_rank = 1 WHERE id = 2")
    E = relations.suggest(conn, "waits_on", 3, 4, "both name the pilot", "planner")
    conn.commit()
    begin(conn, cfg)                                                       # a resume tops up the day's link suggestions
    v = view(conn, cfg)
    assert keys(v) == ["t:9", "t:1", "t:2", "t:3", "t:4"]           # 2 and 3 right after the group, which, left with 1, is that task
    assert members(conn, g) is None and v["cursor_key"] == "t:1"
    assert E in {s["evidence_id"] for s in next(e for e in review_queue.items(conn, cfg, D)["tasks"] if e["key"] == "t:3")["link_suggestions"]}


def test_a_member_that_gains_a_reason_leaves_and_the_group_keeps_the_rest(conn, cfg):
    for i in (1, 2, 3, 4):
        new(conn, i, rec="of_aaa")
    begin(conn, cfg)
    conn.execute("UPDATE tasks SET top_rank = 1 WHERE id = 2")
    conn.commit()
    v = view(conn, cfg)                                                     # a read shows it, and writes nothing
    assert keys(v) == ["g:rec:of_aaa", "t:2"] and members(conn, "g:rec:of_aaa") == [1, 2, 3, 4]
    begin(conn, cfg)
    assert members(conn, "g:rec:of_aaa") == [1, 3, 4] and keys(view(conn, cfg)) == ["g:rec:of_aaa", "t:2"]


# --- the switch changing under a pass -------------------------------------------------------------------------

def test_a_pass_begun_before_the_switch_changed_is_not_broken_by_it(conn, cfg):
    for i in (1, 2, 3):
        new(conn, i, rec="of_aaa")
    begin(conn, cfg)
    no = off(cfg)
    v = view(conn, no)
    assert keys(v) == ["g:rec:of_aaa"] and v["current"]["kind"] == "group"     # switched off: the group stays
    new(conn, 5, rec="of_bbb")
    new(conn, 6, rec="of_bbb")
    v = begin(conn, no)
    assert keys(v) == ["g:rec:of_aaa", "t:5", "t:6"]                          # arrivals are not grouped any more
    dec(conn, no, "g:rec:of_aaa", "keep_all")
    assert set(entries(conn)) == {"1", "2", "3"}


def test_switched_on_under_a_pass_groups_what_arrives(conn, cfg):
    for i in (1, 2):
        new(conn, i, rec="of_aaa")
    begin(conn, off(cfg))
    for i in (5, 6):
        new(conn, i, rec="of_bbb")
    v = begin(conn, cfg)
    assert keys(v) == ["t:1", "t:2", "g:rec:of_bbb"]


# --- the route --------------------------------------------------------------------------------------------

def _client(cfg, tmp_path, **kw):
    client = TestClient(create_app(cfg, db_path=str(tmp_path / "g.db")), **kw)
    return client, client.app.state.conn


def test_the_group_route(cfg, tmp_path, monkeypatch):
    client, c = _client(cfg, tmp_path)
    for i in (1, 2, 3):
        new(c, i, rec="of_aaa")
    p = client.post("/api/review/pass", json={}).json()["pass"]
    assert p["cursor_key"] == "g:rec:of_aaa"
    r = client.post(f"/api/review/pass/{p['id']}/group", json={"key": "g:rec:of_aaa", "op": "take_out", "task_id": 2})
    assert r.status_code == 200 and [x["key"] for x in r.json()["pass"]["list"]] == ["g:rec:of_aaa", "t:2"] and not c.in_transaction
    r = client.post(f"/api/review/pass/{p['id']}/group", json={"key": "g:rec:of_aaa", "op": "one_by_one"})
    assert r.status_code == 200 and r.json()["pass"]["cursor_key"] == "t:1"
    for body, code in (({"key": "g:rec:of_aaa", "op": "one_by_one"}, 400), ({"key": "t:1", "op": "explode"}, 400),
                       ({"key": ["x"], "op": "one_by_one"}, 422), ({"op": "one_by_one"}, 422)):
        r = client.post(f"/api/review/pass/{p['id']}/group", json=body)
        assert r.status_code == code, (body, r.status_code, r.text[:200])
        assert not c.in_transaction
    assert client.post("/api/review/pass/99/group", json={"key": "g:x", "op": "one_by_one"}).status_code == 404
    assert client.post(f"/api/review/pass/{p['id']}/group", content="{}", headers={"Content-Type": "text/plain"}).status_code == 415
    client.post(f"/api/review/pass/{p['id']}/move", json={"to": "end"})
    r = client.post(f"/api/review/pass/{p['id']}/group", json={"key": "t:1", "op": "one_by_one"})
    assert r.status_code == 409 and not c.in_transaction


def test_the_group_route_lets_the_lock_go_on_an_unexpected_error(cfg, tmp_path, monkeypatch):
    client, c = _client(cfg, tmp_path, raise_server_exceptions=False)
    for i in (1, 2):
        new(c, i, rec="of_aaa")
    pid = client.post("/api/review/pass", json={}).json()["pass"]["id"]

    def boom(conn, *a, **k):
        conn.execute("BEGIN IMMEDIATE") if not conn.in_transaction else None
        raise RuntimeError("boom")
    monkeypatch.setattr(P, "regroup", boom)
    r = client.post(f"/api/review/pass/{pid}/group", json={"key": "g:rec:of_aaa", "op": "one_by_one"})
    assert r.status_code == 500 and not c.in_transaction


def test_keep_all_through_the_decide_route_and_each_member_taken_back_alone(cfg, tmp_path):
    client, c = _client(cfg, tmp_path)
    for i in (1, 2, 3):
        new(c, i, rec="of_aaa")
    client.post("/api/review/pass", json={})
    r = client.post("/api/review/decide", json={"key": "g:rec:of_aaa", "option": "keep_all", "args": {"except": [{"key": "t:3", "option": "done"}]}})
    assert r.status_code == 200 and sorted(r.json()["keys"]) == ["1", "2", "3"] and r.json()["pass"]["state"] == "end"
    r = client.post("/api/review/decide", json={"key": "t:2", "option": "undecide"})
    assert r.status_code == 200 and sorted(k for k, _, _ in r.json()["tray"]["entries"]) == ["1", "3"]
    assert r.json()["pass"]["cursor_key"] == "g:rec:of_aaa"
    res = client.post("/api/tray/apply", json={"source": "review", "finish": True}).json()
    assert res["applied"] == 2


def test_rec_on_a_group_keeps_only_the_members_that_were_shown_undecided(conn, cfg):
    _world(conn)
    begin(conn, cfg)
    g, v = first_on_group(conn, cfg)
    shown = [m["key"] for m in v["current"]["item"]["members"]]
    assert shown == ["t:1", "t:2", "t:3"] and v["current"]["recommendation"]["steps"] == ["keep_all"]
    review_decide.stage(conn, cfg, "t:2", "drop", {}, D, origin="page", device="phone")      # another device, meanwhile
    conn.commit()
    with pytest.raises(review_decide.Stale):
        P.decide(conn, cfg, D, v["id"], g, "rec", args={"steps": ["keep_all"], "members": shown}, origin="page", by=BY)
    conn.rollback()
    assert entries(conn) == {"2": ("drop", "t:2")}
    with pytest.raises(review_decide.Refused):
        P.decide(conn, cfg, D, v["id"], g, "rec", args={"steps": ["keep_all"]}, origin="page", by=BY)
    conn.rollback()
    P.decide(conn, cfg, D, v["id"], g, "rec", args={"steps": ["keep_all"], "members": ["t:3", "t:1"]}, origin="page", by=BY)
    conn.commit()
    assert entries(conn) == {"1": ("keep", "t:1"), "2": ("drop", "t:2"), "3": ("keep", "t:3")}


def test_a_groups_line_says_where_its_tasks_came_from_in_words(conn, cfg):
    """Design 6.9 (review 2026-11-20): "New from a recording · yesterday's Project Meeting", the day as the server says it
    (dates.say_whose), never the browser's own date; a course or a thread by its name alone."""
    _world(conn)
    begin(conn, cfg)
    g, v = first_on_group(conn, cfg)
    it = v["current"]["item"]
    assert it["why"] == "New from a recording" and it["from_words"] == "yesterday's Project Meeting"
    for i in (11, 12):
        new(conn, i, course="GEOG 582")
    v = P.begin_or_resume(conn, cfg, D, by=BY, fresh=True)
    conn.commit()
    gc = next(r["key"] for r in v["list"] if r["key"].startswith("g:course:"))
    _, v = mv(conn, cfg, "goto", key=gc, from_key=None)
    assert v["current"]["item"]["from_words"] == "GEOG 582"


def test_undoing_the_batch_a_keep_all_made_brings_the_group_back(conn, cfg):
    """Review of the second half (2026-11-20; checked by hand then, pinned here): Apply after Keep all ends the pass
    `applied`; History's Undo of that batch puts the tasks back in the queue, and the next pass groups them again."""
    from factotum import batches
    for i in (1, 2, 3):
        new(conn, i, rec="of_aaa")
    v = begin(conn, cfg)
    g = v["cursor_key"]
    assert g == "g:rec:of_aaa"
    dec(conn, cfg, g, "keep_all")
    res = tray.apply(conn, cfg, D)
    assert conn.execute("SELECT end_why FROM review_passes WHERE id = ?", (v["id"],)).fetchone()[0] == "applied"
    assert view(conn, cfg) is None and review_queue.items(conn, cfg, D)["tasks"] == []
    batches.undo(conn, res["batch_id"])
    assert sorted(e["task"]["id"] for e in review_queue.items(conn, cfg, D)["tasks"]) == [1, 2, 3]
    v = begin(conn, cfg)
    assert v["cursor_key"] == g and [m["key"] for m in v["current"]["item"]["members"]] == ["t:1", "t:2", "t:3"]
    assert all(m["state"] == "waiting" for m in v["current"]["item"]["members"])
