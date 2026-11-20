"""Spoken Review, stage 2a: Review as a pass held on the server (design §6.1-6.6). A pass holds the order and where
the owner stands, never what an item says (built from the live queue on every read) and never what was decided (the
tray's `decides`). Next leaves an item undecided and writes nothing to the task; Back returns; Next after the last
comes round to what was left; arriving at an item writes the offered stamp `expire` acts on."""
import copy
import threading
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from stage4_helpers import mk
from test_polish import _card as _kind_card

from factotum import batches, relations, review_decide, review_pass as P, review_queue, suggestions, tray
from factotum.api import create_app
from factotum.db import connect, get_task, init_db

D = date(2026, 11, 19)                   # Review's day in the module-level tests: a Thursday
BY = "page · Desk"


def keep(tid):
    return {"task_id": tid, "op": "keep", "fields": {}, "label": "kept"}


def begin(c, cfg, today=D, **kw):
    v = P.begin_or_resume(c, cfg, today, by=BY, **kw)
    c.commit()
    return v


def view(c, cfg, today=D):
    return P.view(c, cfg, today)


def mv(c, cfg, to, today=D, **kw):
    v = view(c, cfg, today)
    kw.setdefault("from_key", v["cursor_key"])
    out = P.move(c, cfg, today, v["id"], to, by=BY, **kw)
    if out.get("moved") is False:
        c.rollback()
    else:
        c.commit()
    return out, view(c, cfg, today)


def dec(c, cfg, key, option, today=D, **kw):
    v = view(c, cfg, today)
    out = P.decide(c, cfg, today, v["id"], key, option, origin="page", by=BY, **kw)
    c.commit()
    return out, view(c, cfg, today)


def states(v):
    return {r["key"]: r["state"] for r in v["list"]}


def offered(c, tid):
    return get_task(c, tid)["last_offered_at"]


def quiet(c, *ids):
    """Reviewed yesterday and mentioned yesterday: no reason of its own to be on Review."""
    for i in ids:
        c.execute("UPDATE tasks SET last_reviewed_at = '2026-11-18', last_mention_at = '2026-11-18' WHERE id = ?", (i,))
    c.commit()


def tasks(c, n):
    for i in range(1, n + 1):
        mk(c, i)


# --- beginning and resuming -------------------------------------------------------------------------------

def test_begin_makes_one_live_pass_and_a_second_begin_resumes_it(conn, cfg):
    tasks(conn, 3)
    v = begin(conn, cfg)
    assert v["state"] == "item" and v["cursor_key"] == "t:1" and v["day"] == D.isoformat() and v["lap"] == 1
    assert v["counts"]["items"] == 3 and v["counts"]["position"] == 1 and v["counts"]["undecided"] == 3
    assert v["current"]["key"] == "t:1" and v["current"]["item"]["task"]["id"] == 1 and v["started_by"] == BY
    assert [r["key"] for r in v["list"]] == ["t:1", "t:2", "t:3"]
    again = begin(conn, cfg)
    assert again["id"] == v["id"] and again["cursor_key"] == "t:1"
    assert conn.execute("SELECT COUNT(*) FROM review_passes").fetchone()[0] == 1
    fresh = begin(conn, cfg, fresh=True)
    assert fresh["id"] != v["id"]
    assert conn.execute("SELECT end_why FROM review_passes WHERE id = ?", (v["id"],)).fetchone()[0] == "replaced"
    assert conn.execute("SELECT COUNT(*) FROM review_passes WHERE ended_at IS NULL").fetchone()[0] == 1


def test_two_begins_on_two_connections_make_one_pass(cfg, tmp_path):
    path = tmp_path / "two.db"
    c0 = connect(path)
    init_db(c0)
    tasks(c0, 3)
    gate, ids, errors = threading.Barrier(2), [], []

    def run():
        c = connect(path)
        try:
            gate.wait()
            ids.append(P.begin_or_resume(c, cfg, D, by="connector")["id"])
            c.commit()
        except Exception as e:                    # pragma: no cover - reported below
            errors.append(repr(e))
            c.rollback()
    ts = [threading.Thread(target=run) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert errors == [] and len(set(ids)) == 1
    assert c0.execute("SELECT COUNT(*) FROM review_passes").fetchone()[0] == 1
    assert c0.execute("SELECT COUNT(*) FROM review_pass_items").fetchone()[0] == 3


def test_begin_records_the_days_link_suggestions_once(conn, cfg, monkeypatch):
    mk(conn, 1)
    mk(conn, 2)
    E = relations.suggest(conn, "waits_on", 1, 2, "both name the numbers", "planner")
    conn.commit()
    calls = []
    real = suggestions.session
    monkeypatch.setattr(suggestions, "session", lambda *a, **k: calls.append(a[1]) or real(*a, **k))
    v = begin(conn, cfg)
    assert calls == [D]
    assert relations.shown_ids(conn) == {E}                       # written before the items were built
    assert [s["evidence_id"] for s in v["current"]["item"]["link_suggestions"]] == [E]
    begin(conn, cfg)                                               # a resume rebuilds nothing
    assert calls == [D]


def test_the_pass_reads_write_nothing(cfg, tmp_path):
    client = TestClient(create_app(cfg, db_path=str(tmp_path / "a.db")))
    c = client.app.state.conn
    tasks(c, 3)
    assert client.get("/api/review/pass").json() == {"pass": None}
    assert client.get("/api/review/pass/now").json()["pass_id"] is None
    v = client.post("/api/review/pass", json={"device": "Desk"}).json()["pass"]
    assert v["cursor_key"] == "t:1" and v["started_by"] == "page · Desk"
    before = c.total_changes
    got = client.get("/api/review/pass").json()["pass"]
    now = client.get("/api/review/pass/now").json()
    assert c.total_changes == before and not c.in_transaction
    assert got["id"] == v["id"] and got["rev"] == v["rev"]
    assert now == {"pass_id": v["id"], "rev": v["rev"], "state": "item", "cursor_key": "t:1", "tray_rev": 0, "speaking": None, "day": v["day"]}


# --- the offered stamp ------------------------------------------------------------------------------------

def test_arriving_stamps_last_offered_at_on_that_item_only(conn, cfg):
    tasks(conn, 3)
    card = _kind_card(conn, "planner")
    begin(conn, cfg)
    assert [offered(conn, i) for i in (1, 2, 3)] == [D.isoformat(), None, None]
    mv(conn, cfg, "next")
    assert [offered(conn, i) for i in (1, 2, 3)] == [D.isoformat(), D.isoformat(), None]
    _, v = mv(conn, cfg, "goto", key=f"s:{card}", from_key=None)  # a card has no task: nothing stamped
    assert v["cursor_key"] == f"s:{card}" and offered(conn, 3) is None
    arrived = {r[0]: r[1] for r in conn.execute("SELECT key, arrived_at IS NOT NULL FROM review_pass_items")}
    assert arrived == {"t:1": 1, "t:2": 1, "t:3": 0, f"s:{card}": 1}
    # a stamp never moves a later one back
    conn.execute("UPDATE tasks SET last_offered_at = '2026-11-25' WHERE id = 3")
    conn.commit()
    mv(conn, cfg, "goto", key="t:3", from_key=None)
    assert offered(conn, 3) == "2026-11-25"


# --- moves ------------------------------------------------------------------------------------------------

def test_next_leaves_the_item_undecided_and_writes_nothing_to_the_task(conn, cfg):
    tasks(conn, 3)
    begin(conn, cfg)
    row = {k: v for k, v in get_task(conn, 1).items() if k != "last_offered_at"}
    counts = [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("changes", "batches", "tray")]
    trev = tray.rev(conn)
    out, v = mv(conn, cfg, "next")
    assert out["moved"] is True and v["cursor_key"] == "t:2"
    assert {k: v for k, v in get_task(conn, 1).items() if k != "last_offered_at"} == row
    assert [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("changes", "batches", "tray")] == counts
    assert tray.rev(conn) == trev
    assert states(v)["t:1"] == "passed" and v["counts"]["passed"] == 1 and v["counts"]["undecided"] == 3


def test_next_after_the_last_comes_round_to_the_first_undecided_and_says_how_many(conn, cfg):
    tasks(conn, 3)
    begin(conn, cfg)
    dec(conn, cfg, "t:2", "keep")                     # decided elsewhere: Next skips it
    out, v = mv(conn, cfg, "next")
    assert v["cursor_key"] == "t:3" and out.get("came_round") is not True
    out, v = mv(conn, cfg, "next")
    assert out["came_round"] is True and out["left"] == 2 and v["cursor_key"] == "t:1" and v["lap"] == 2


def test_next_on_the_only_undecided_item_stays_on_it(conn, cfg):
    tasks(conn, 2)
    begin(conn, cfg)
    dec(conn, cfg, "t:2", "keep")
    out, v = mv(conn, cfg, "next")
    assert out["moved"] is True and out["came_round"] is True and out["left"] == 1
    assert v["cursor_key"] == "t:1" and v["state"] == "item" and states(v)["t:1"] == "passed"


def test_next_with_nothing_undecided_is_the_end(conn, cfg):
    tasks(conn, 2)
    begin(conn, cfg)
    dec(conn, cfg, "t:2", "drop")
    out, v = dec(conn, cfg, "t:1", "keep")            # the last undecided item, in front: the pass reaches its end
    assert v["state"] == "end" and v["cursor_key"] is None and v["current"] is None and v["ahead"] is None
    out, v = mv(conn, cfg, "next", from_key=None)
    assert v["state"] == "end" and v["cursor_key"] is None


def test_back_reaches_a_decided_item_and_shows_its_staged_decision(conn, cfg):
    tasks(conn, 2)
    begin(conn, cfg)
    _, v = dec(conn, cfg, "t:1", "keep")
    assert v["cursor_key"] == "t:2"
    out, v = mv(conn, cfg, "back")
    assert out["moved"] is True and v["cursor_key"] == "t:1"
    cur = v["current"]
    assert cur["state"] == "decided" and cur["staged"]["decision"]["label"] == "kept" and cur["staged"]["decision"]["keys"] == ["1"]
    assert "undecide" in [o["id"] for o in cur["options"]]


def test_back_at_the_first_does_not_move(conn, cfg):
    tasks(conn, 2)
    v0 = begin(conn, cfg)
    out, v = mv(conn, cfg, "back")
    assert out == {"moved": False, "why": "first"} and v["cursor_key"] == "t:1" and v["rev"] == v0["rev"]


def test_goto_a_named_item_lands_on_it(conn, cfg):
    tasks(conn, 3)
    begin(conn, cfg)
    out, v = mv(conn, cfg, "goto", key="t:3", from_key=None)
    assert out["moved"] is True and v["cursor_key"] == "t:3" and offered(conn, 3) == D.isoformat()
    out, v = mv(conn, cfg, "first", from_key=None)
    assert v["cursor_key"] == "t:1"


def test_goto_a_gone_item_is_refused(conn, cfg):
    tasks(conn, 3)
    begin(conn, cfg)
    batches.apply(conn, [{"task_id": 2, "op": "done", "fields": {}}], "ui", D)
    with pytest.raises(review_decide.Refused):
        mv(conn, cfg, "goto", key="t:2", from_key=None)
    conn.rollback()
    with pytest.raises(review_decide.Refused):
        mv(conn, cfg, "goto", key="t:99", from_key=None)
    conn.rollback()
    assert view(conn, cfg)["cursor_key"] == "t:1"


def test_a_decision_on_the_item_in_front_moves_on_and_one_elsewhere_does_not(conn, cfg):
    tasks(conn, 3)
    begin(conn, cfg)
    out, v = dec(conn, cfg, "t:3", "keep")
    assert v["cursor_key"] == "t:1" and out["keys"] == ["3"]
    out, v = dec(conn, cfg, "t:1", "done", note="Sent it")
    assert v["cursor_key"] == "t:2" and out["advanced"] is True
    assert states(v) == {"t:1": "decided", "t:2": "waiting", "t:3": "decided"}
    assert conn.execute("SELECT passes FROM review_pass_items WHERE key = 't:1'").fetchone()[0] == 0   # decided, not passed
    assert offered(conn, 2) == D.isoformat()


def test_changing_a_decision_replaces_it_and_keeps_what_is_beside(conn, cfg):
    tasks(conn, 2)
    begin(conn, cfg)
    dec(conn, cfg, "t:1", "pin")
    _, v = dec(conn, cfg, "t:1", "keep")
    assert v["cursor_key"] == "t:2"
    out, v = dec(conn, cfg, "t:1", "done")
    assert v["cursor_key"] == "t:2"                   # a decision on an item not in front moves nothing
    rows = {k: e["op"] for k, e, _ in tray.read(conn)["entries"]}
    assert rows == {"1:top": "pin_top", "1": "done"}
    back, v = mv(conn, cfg, "back")
    assert v["current"]["staged"]["decision"]["label"] == "done" and [b["key"] for b in v["current"]["staged"]["beside"]] == ["1:top"]


def test_undecide_makes_it_undecided_and_puts_it_in_front(conn, cfg):
    tasks(conn, 3)
    begin(conn, cfg)
    dec(conn, cfg, "t:1", "keep")
    _, v = dec(conn, cfg, "t:1", "undecide")
    assert v["cursor_key"] == "t:1" and v["current"]["state"] == "waiting" and tray.read(conn)["entries"] == []


def test_a_move_from_a_stale_item_answers_moved_false_and_moves_nothing(conn, cfg):
    tasks(conn, 3)
    begin(conn, cfg)
    mv(conn, cfg, "next")
    rev = view(conn, cfg)["rev"]
    out, v = mv(conn, cfg, "next", from_key="t:1")
    assert out["moved"] is False and out["why"] == "moved" and out["by"] == BY
    assert v["cursor_key"] == "t:2" and v["rev"] == rev
    out, v = mv(conn, cfg, "back", from_key="t:3")
    assert out["moved"] is False and v["cursor_key"] == "t:2"


def test_two_moves_on_two_connections_move_once(cfg, tmp_path):
    path = tmp_path / "two.db"
    c0 = connect(path)
    init_db(c0)
    tasks(c0, 3)
    pid = begin(c0, cfg)["id"]
    gate, outs, errors = threading.Barrier(2), [], []

    def run():
        c = connect(path)
        try:
            gate.wait()
            out = P.move(c, cfg, D, pid, "next", from_key="t:1", by="connector")
            (c.commit if out["moved"] else c.rollback)()
            outs.append(out["moved"])
        except Exception as e:                    # pragma: no cover - reported below
            errors.append(repr(e))
            c.rollback()
    ts = [threading.Thread(target=run) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert errors == [] and sorted(outs) == [False, True]
    v = view(c0, cfg)
    assert v["cursor_key"] == "t:2"
    assert c0.execute("SELECT passes FROM review_pass_items WHERE key = 't:1'").fetchone()[0] == 1


# --- what is decided --------------------------------------------------------------------------------------

def test_a_staged_create_on_a_card_counts_as_decided_and_the_pass_reaches_its_end(conn, cfg):
    eid = _kind_card(conn, "planner")
    v = begin(conn, cfg)
    assert v["cursor_key"] == f"s:{eid}" and v["current"]["kind"] == "card"
    assert [o["id"] for o in v["current"]["options"]] == ["create", "dismiss"]
    out, v = dec(conn, cfg, f"s:{eid}", "create")
    assert out["keys"] == [f"e:{eid}"] and v["state"] == "end" and v["counts"]["left"] == 0 and states(v) == {f"s:{eid}": "decided"}


def test_an_item_decided_in_the_tray_by_another_route_counts_as_decided(conn, cfg):
    tasks(conn, 2)
    begin(conn, cfg)
    tray.write(conn, [{"put": "2", "entry": keep(2)}], "page", "Phone")
    conn.commit()
    v = view(conn, cfg)
    assert states(v) == {"t:1": "waiting", "t:2": "decided"} and v["counts"]["undecided"] == 1
    assert v["list"][1]["staged"] == "kept"


def test_a_carried_bare_id_entry_decides_its_item(conn, cfg):
    tasks(conn, 2)
    begin(conn, cfg)
    tray.carry(conn, "old-desk", [["2", keep(2)]], "Desk")
    conn.commit()
    assert states(view(conn, cfg))["t:2"] == "decided"


def test_an_item_whose_reason_went_away_is_gone_and_skipped(conn, cfg):
    tasks(conn, 3)
    begin(conn, cfg)
    batches.apply(conn, [{"task_id": 2, "op": "done", "fields": {}}], "ui", D)
    v = view(conn, cfg)
    assert states(v)["t:2"] == "gone" and v["counts"]["items"] == 2
    _, v = mv(conn, cfg, "next")
    assert v["cursor_key"] == "t:3"
    batches.apply(conn, [{"task_id": 3, "op": "drop", "fields": {}}], "ui", D)   # the item in front goes: it stays, settled
    v = view(conn, cfg)
    assert v["cursor_key"] == "t:3" and v["current"]["state"] == "gone" and v["current"]["settled"] == "It was dropped."
    _, v = mv(conn, cfg, "next")
    assert v["cursor_key"] == "t:1"


def test_a_link_answered_on_another_card_answers_an_item_with_no_other_reason(conn, cfg):
    mk(conn, 1)
    mk(conn, 2)
    quiet(conn, 1, 2)
    E = relations.suggest(conn, "waits_on", 1, 2, "both name the numbers", "planner")
    conn.commit()
    v = begin(conn, cfg)
    assert [r["key"] for r in v["list"]] == ["t:1", "t:2"]
    out, v = dec(conn, cfg, "t:1", f"link:{E}")
    assert states(v) == {"t:1": "answered", "t:2": "answered"}
    assert v["state"] == "end" and v["counts"]["undecided"] == 0


def test_a_new_item_arrives_at_the_end_and_an_urgent_one_right_after_the_item_in_front(conn, cfg):
    tasks(conn, 3)
    begin(conn, cfg)
    mk(conn, 4)
    mk(conn, 5, due=D.isoformat(), hard=1)
    v = view(conn, cfg)
    assert [r["key"] for r in v["list"]] == ["t:1", "t:5", "t:2", "t:3", "t:4"]
    assert conn.execute("SELECT COUNT(*) FROM review_pass_items").fetchone()[0] == 3     # a read persists nothing
    _, v = mv(conn, cfg, "next")
    assert v["cursor_key"] == "t:5"
    assert [k for k, in conn.execute("SELECT key FROM review_pass_items ORDER BY pos")] == ["t:1", "t:5", "t:2", "t:3", "t:4"]


def test_an_arrival_after_the_end_puts_the_pass_back_on_an_item(conn, cfg):
    tasks(conn, 1)
    begin(conn, cfg)
    _, v = dec(conn, cfg, "t:1", "keep")
    assert v["state"] == "end"
    mk(conn, 2)
    v = view(conn, cfg)
    assert v["state"] == "item" and v["cursor_key"] == "t:2"
    assert conn.execute("SELECT state FROM review_passes").fetchone()[0] == "end"         # the next write persists it
    begin(conn, cfg)
    assert tuple(conn.execute("SELECT state, cursor_key FROM review_passes").fetchone()) == ("item", "t:2")
    assert offered(conn, 2) == D.isoformat()


def test_a_pass_from_yesterday_ends_and_a_new_one_begins(cfg, tmp_path, monkeypatch):
    client = TestClient(create_app(cfg, db_path=str(tmp_path / "a.db")))
    c = client.app.state.conn
    tasks(c, 2)
    day = {"d": D - timedelta(days=1)}
    monkeypatch.setattr(review_queue, "today", lambda cfg: day["d"])     # Review's day, never the machine's
    old = client.post("/api/review/pass", json={}).json()["pass"]
    assert old["day"] == (D - timedelta(days=1)).isoformat()
    day["d"] = D
    assert client.get("/api/review/pass").json() == {"pass": None}         # a read ends nothing
    assert c.execute("SELECT ended_at FROM review_passes WHERE id = ?", (old["id"],)).fetchone()[0] is None
    new = client.post("/api/review/pass", json={}).json()["pass"]
    assert new["id"] != old["id"] and new["day"] == D.isoformat()
    assert c.execute("SELECT end_why FROM review_passes WHERE id = ?", (old["id"],)).fetchone()[0] == "another_day"


def test_the_checkpoint_comes_after_review_cap_items_and_continue_or_next_goes_on(conn, cfg):
    cfg = copy.deepcopy(cfg)
    cfg["lifecycle"]["review_cap"] = 5
    tasks(conn, 7)
    begin(conn, cfg)
    for _ in range(4):
        mv(conn, cfg, "next")
    assert view(conn, cfg)["cursor_key"] == "t:5"
    _, v = mv(conn, cfg, "next")
    assert v["state"] == "checkpoint" and v["cursor_key"] == "t:6" and v["checkpoint"] == {"looked": 5, "cap": 5}
    assert offered(conn, 6) is None                                            # not in front yet
    _, v = mv(conn, cfg, "continue")
    assert v["state"] == "item" and v["cursor_key"] == "t:6" and v["checkpoint"]["looked"] == 1 and offered(conn, 6) == D.isoformat()
    # a spoken "next" must not stall: from a checkpoint it goes on as continue does
    begin(conn, cfg, fresh=True)
    for _ in range(5):
        mv(conn, cfg, "next")
    _, v = mv(conn, cfg, "next")
    assert v["state"] == "item" and v["cursor_key"] == "t:6"
    # a decision that moves on reaches the checkpoint too
    begin(conn, cfg, fresh=True)
    for i in range(1, 6):
        _, v = dec(conn, cfg, f"t:{i}", "keep")
    assert v["state"] == "checkpoint" and v["cursor_key"] == "t:6" and v["counts"]["undecided"] == 2


def test_a_checkpoint_with_nothing_left_undecided_reads_as_the_end(conn, cfg):
    """Found rendering 2a-2b: decisions made after the checkpoint (another device, a conversation) leave nothing
    undecided; the pass then reads as the end, not as a checkpoint asking to keep going over decided items."""
    cfg = copy.deepcopy(cfg)
    cfg["lifecycle"]["review_cap"] = 5
    tasks(conn, 7)
    begin(conn, cfg)
    for i in range(1, 6):
        _, v = dec(conn, cfg, f"t:{i}", "keep")
    assert v["state"] == "checkpoint"
    for k in ("t:6", "t:7"):
        _, v = dec(conn, cfg, k, "keep")
    assert v["state"] == "end" and v["cursor_key"] is None and v["counts"]["undecided"] == 0
    v = view(conn, cfg)
    assert v["state"] == "end" and v["current"] is None


def test_the_view_carries_the_item_a_move_on_would_bring(conn, cfg):
    tasks(conn, 3)
    v = begin(conn, cfg)
    assert v["ahead"]["key"] == "t:2" and v["ahead"]["item"]["task"]["id"] == 2 and v["ahead"]["options"]
    dec(conn, cfg, "t:2", "keep")
    v = view(conn, cfg)
    assert v["ahead"]["key"] == "t:3"
    _, v = mv(conn, cfg, "next")
    assert v["cursor_key"] == "t:3" and v["ahead"]["key"] == "t:1"           # coming round
    dec(conn, cfg, "t:1", "keep")
    assert view(conn, cfg)["ahead"] is None                                    # nothing else is undecided


def test_a_decision_on_an_open_task_that_left_the_queue_still_stages(conn, cfg):
    tasks(conn, 3)
    quiet(conn, 2)                                    # open, with no reason to be on Review
    begin(conn, cfg)
    assert "t:2" not in states(view(conn, cfg))
    out, v = dec(conn, cfg, "t:2", "done", note="Sent")
    assert out["keys"] == ["2"] and v["cursor_key"] == "t:1"
    batches.apply(conn, [{"task_id": 3, "op": "done", "fields": {}}], "ui", D)
    with pytest.raises(review_decide.Gone):
        dec(conn, cfg, "t:3", "keep")
    conn.rollback()


# --- after Apply ------------------------------------------------------------------------------------------

def test_after_apply_marks_items_and_ends_the_pass_when_nothing_is_undecided(conn, cfg):
    tasks(conn, 3)
    v = begin(conn, cfg)
    dec(conn, cfg, "t:1", "keep")
    dec(conn, cfg, "t:2", "keep")
    res = tray.apply(conn, cfg, D, keys=["1"])
    assert [tuple(r) for r in conn.execute("SELECT key FROM review_pass_items WHERE done_batch_id = ?", (res["batch_id"],))] == [("t:1",)]
    v = view(conn, cfg)
    assert v is not None and states(v) == {"t:1": "decided", "t:2": "decided", "t:3": "waiting"}
    dec(conn, cfg, "t:3", "drop")
    res = tray.apply(conn, cfg, D)
    assert conn.execute("SELECT end_why FROM review_passes WHERE id = ?", (v["id"],)).fetchone()[0] == "applied"
    assert view(conn, cfg) is None


def test_undoing_the_applied_batch_makes_its_items_undecided_again(conn, cfg):
    tasks(conn, 2)
    begin(conn, cfg)
    dec(conn, cfg, "t:1", "keep")
    res = tray.apply(conn, cfg, D)
    assert states(view(conn, cfg))["t:1"] == "decided"
    batches.undo(conn, res["batch_id"])
    assert states(view(conn, cfg))["t:1"] == "waiting"


def test_a_passed_item_is_in_the_queue_next_time(conn, cfg):
    tasks(conn, 2)
    v = begin(conn, cfg)
    mv(conn, cfg, "next")
    mv(conn, cfg, "end")
    assert conn.execute("SELECT end_why FROM review_passes WHERE id = ?", (v["id"],)).fetchone()[0] == "finished"
    assert view(conn, cfg) is None
    assert "t:1" in [i["key"] for i in review_queue.items(conn, cfg, D)["tasks"]]
    v = begin(conn, cfg)
    assert v["cursor_key"] == "t:1" and states(v) == {"t:1": "waiting", "t:2": "waiting"}


def test_finish_keeps_the_tray(conn, cfg):
    tasks(conn, 2)
    begin(conn, cfg)
    dec(conn, cfg, "t:1", "keep")
    before = tray.read(conn)
    mv(conn, cfg, "end")
    assert tray.read(conn) == before and get_task(conn, 1)["last_reviewed_at"] is None


# --- the routes -------------------------------------------------------------------------------------------

def _client(cfg, tmp_path, **kw):
    client = TestClient(create_app(cfg, db_path=str(tmp_path / "a.db")), follow_redirects=False, **kw)
    return client, client.app.state.conn


def test_the_tab_number_is_undecided_count_with_and_without_a_pass(cfg, tmp_path):
    client, c = _client(cfg, tmp_path)
    tasks(c, 3)
    assert client.get("/api/review/count").json()["count"] == 3
    client.post("/api/review/decide", json={"key": "t:2", "option": "keep"})              # no pass: the rule alone
    assert client.get("/api/review/count").json()["count"] == 2
    v = client.post("/api/review/pass", json={}).json()["pass"]
    assert v["counts"]["left"] == 2
    j = client.post("/api/review/decide", json={"key": "t:1", "option": "keep"}).json()
    assert client.get("/api/review/count").json()["count"] == 1 == j["pass"]["counts"]["left"]


def test_the_decide_route_takes_the_pass_and_answers_it(cfg, tmp_path):
    client, c = _client(cfg, tmp_path)
    tasks(c, 2)
    client.post("/api/review/pass", json={"device": "Phone"})
    j = client.post("/api/review/decide", json={"key": "t:1", "option": "keep", "device": "Phone"}).json()
    assert j["keys"] == ["1"] and j["pass"]["cursor_key"] == "t:2" and j["pass"]["moved_by"] == "page · Phone"
    assert [k for k, _, _ in j["tray"]["entries"]] == ["1"]
    bad = client.post("/api/review/decide", json={"key": "t:1", "option": "later", "args": {"until": D.isoformat()}})
    assert bad.status_code == 400 and bad.json()["pass"]["cursor_key"] == "t:2" and "tray" in bad.json()
    m = client.post(f"/api/review/pass/{j['pass']['id']}/move", json={"to": "back", "from": "t:2"}).json()
    assert m["moved"] is True and m["pass"]["cursor_key"] == "t:1"
    m = client.post(f"/api/review/pass/{j['pass']['id']}/move", json={"to": "next", "from": "t:2"}).json()
    assert m == {"moved": False, "why": "moved", "by": "page", "pass": m["pass"]} and m["pass"]["cursor_key"] == "t:1"


def test_a_move_on_yesterdays_pass_is_refused_and_the_next_begin_ends_it(cfg, tmp_path, monkeypatch):
    client, c = _client(cfg, tmp_path)
    tasks(c, 2)
    day = {"d": D - timedelta(days=1)}
    monkeypatch.setattr(review_queue, "today", lambda cfg: day["d"])
    old = client.post("/api/review/pass", json={}).json()["pass"]
    client.post("/api/review/decide", json={"key": "t:2", "option": "keep"})
    day["d"] = D
    r = client.post(f"/api/review/pass/{old['id']}/move", json={"to": "next", "from": "t:1"})
    assert r.status_code == 409 and r.json() == {"detail": "this review has ended", "pass": None}
    assert c.execute("SELECT ended_at FROM review_passes").fetchone()[0] is None and not c.in_transaction
    new = client.post("/api/review/pass", json={}).json()["pass"]
    assert new["id"] != old["id"] and states(new)["t:2"] == "decided"           # the tray stayed and decides in the new pass
    assert c.execute("SELECT end_why FROM review_passes WHERE id = ?", (old["id"],)).fetchone()[0] == "another_day"
    r = client.post(f"/api/review/pass/{old['id']}/move", json={"to": "next", "from": "t:1"})
    assert r.status_code == 409 and r.json()["pass"]["id"] == new["id"]
    assert client.post("/api/review/pass/999/move", json={"to": "next"}).status_code == 404


def test_after_apply_is_called_by_tray_apply_and_ends_the_pass(cfg, tmp_path):
    client, c = _client(cfg, tmp_path)
    tasks(c, 1)
    pid = client.post("/api/review/pass", json={}).json()["pass"]["id"]
    j = client.post("/api/review/decide", json={"key": "t:1", "option": "keep"}).json()
    assert j["pass"]["state"] == "end"
    res = client.post("/api/tray/apply", json={"finish": True}).json()
    assert res["applied"] == 1
    assert tuple(c.execute("SELECT end_why, ended_at IS NOT NULL FROM review_passes WHERE id = ?", (pid,)).fetchone()) == ("applied", 1)
    assert c.execute("SELECT done_batch_id FROM review_pass_items WHERE key = 't:1'").fetchone()[0] == res["batch_id"]
    assert client.get("/api/review/pass").json() == {"pass": None} and not c.in_transaction


def test_a_failing_after_apply_never_fails_the_apply(cfg, tmp_path, monkeypatch):
    client, c = _client(cfg, tmp_path)
    tasks(c, 2)
    client.post("/api/review/pass", json={})
    client.post("/api/review/decide", json={"key": "t:1", "option": "keep"})

    def boom(*a, **k):
        c.execute("UPDATE review_passes SET looked = 99")
        raise RuntimeError("boom")
    monkeypatch.setattr(P, "after_apply", boom)
    res = client.post("/api/tray/apply", json={"finish": True})
    assert res.status_code == 200 and res.json()["applied"] == 1 and not c.in_transaction
    assert c.execute("SELECT looked FROM review_passes").fetchone()[0] != 99        # its half-write is let go
    assert get_task(c, 1)["last_reviewed_at"] is not None


PASS_POSTS = ("/api/review/pass", "/api/review/pass/1/move")


def test_the_pass_routes_take_json_only(cfg, tmp_path):
    client, _ = _client(cfg, tmp_path)
    for path in PASS_POSTS:
        r = client.post(path, content="{}", headers={"Content-Type": "text/plain"})
        assert r.status_code == 415, path


def test_no_pass_route_answers_401_403_a_redirect_or_a_5xx(cfg, tmp_path):
    client, c = _client(cfg, tmp_path)
    tasks(c, 2)
    calls = [("post", "/api/review/pass/1/move", {"to": "next"}),               # no pass yet: 404
             ("post", "/api/review/pass", {"fresh": "x"}),
             ("post", "/api/review/pass", {"device": ["x"]}),
             ("post", "/api/review/pass", {}),
             ("post", "/api/review/pass/1/move", {"to": "explode"}),
             ("post", "/api/review/pass/1/move", {"to": "goto", "key": "t:99"}),
             ("post", "/api/review/pass/1/move", {"to": "goto", "key": ["t:1"]}),
             ("post", "/api/review/pass/1/move", {"to": "goto"}),
             ("post", "/api/review/pass/1/move", {"to": "next", "from": 5}),
             ("post", "/api/review/pass/1/move", {"to": "next", "from": "t:2"}),
             ("post", "/api/review/pass/abc/move", {"to": "next"}),
             ("post", "/api/review/pass/2/move", {"to": "next", "from": "t:1"}),
             ("post", "/api/review/decide", {"key": "t:1", "option": "rec"}),
             ("post", "/api/review/decide", {"key": "t:1", "option": "keep_all"}),
             ("post", "/api/review/decide", {"key": "g:rec:1", "option": "keep"}),
             ("post", "/api/review/decide", {"key": "t:1", "option": "explode"}),
             ("post", "/api/review/decide", {"key": "t:9", "option": "keep"}),
             ("post", "/api/review/decide", {"key": "t:1", "option": "remind", "args": {"fire_at": "2026-11-25T09:00", "repeat": {"a": 1}}}),
             ("get", "/api/review/pass", None),
             ("get", "/api/review/pass/now", None)]
    for method, path, body in calls:
        r = client.post(path, json=body) if method == "post" else client.get(path)
        assert r.status_code not in (301, 302, 303, 307, 308, 401, 403), (path, body, r.status_code)
        assert r.status_code < 500, (path, body, r.status_code, r.text[:200])
        assert not c.in_transaction, (path, body)


def test_every_pass_route_lets_the_lock_go_on_an_unexpected_error(cfg, tmp_path, monkeypatch):
    client, c = _client(cfg, tmp_path, raise_server_exceptions=False)
    tasks(c, 2)
    pid = client.post("/api/review/pass", json={}).json()["pass"]["id"]

    def boom(conn, *a, **k):
        conn.execute("BEGIN IMMEDIATE") if not conn.in_transaction else None
        raise RuntimeError("boom")
    for name in ("begin_or_resume", "move", "decide"):
        monkeypatch.setattr(P, name, boom)
    for path, body in (("/api/review/pass", {}), (f"/api/review/pass/{pid}/move", {"to": "next", "from": "t:1"}),
                       ("/api/review/decide", {"key": "t:1", "option": "keep"})):
        r = client.post(path, json=body)
        assert r.status_code == 500, (path, r.status_code, r.text[:200])
        assert not c.in_transaction, path


def test_the_pass_holds_no_group_until_2d(conn, cfg):
    tasks(conn, 2)
    v = begin(conn, cfg)
    with pytest.raises(review_decide.Refused):
        P.regroup(conn, cfg, D, v["id"], "t:1", "one_by_one", by=BY)
    cur = v["current"]
    assert cur["recommendation"] is None and "spoken" not in cur and not any(r["key"].startswith("g:") for r in v["list"])
    assert "rec" not in [o["id"] for o in cur["options"]]
