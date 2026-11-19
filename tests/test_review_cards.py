"""Spoken Review, stage 1: Review's queue and cards outside the API, and Review's day."""
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from stage4_helpers import mk
from test_polish import _card as _kind_card                    # one card of each kind, as the sweeps write them
from test_stage8 import _card

from factotum import batches, config as C, review_cards, review_queue
from factotum.api import create_app
from factotum.db import get_settings, now_iso

TASK_KEYS = {"task", "reasons", "primary", "texts", "link_suggestions", "flags", "evidence"}


def _app(cfg, tmp_path, name="a.db"):
    client = TestClient(create_app(cfg, db_path=str(tmp_path / name)))
    return client, client.app.state.conn


def _queue_fixture(c):
    """20 new tasks, a task with a thread_updated row, a suggestion card. Returns the card's evidence id."""
    for i in range(1, 21):
        mk(c, i)
    mk(c, 21, name="Max wrote back")
    c.execute("INSERT INTO evidence (task_id, kind, ref, label, at) VALUES (21, 'thread_updated', 'conv-1', 'Max wrote', ?)", (now_iso(),))
    _card(c, "Order the reagent")
    c.commit()
    return c.execute("SELECT MAX(id) FROM evidence").fetchone()[0]


# --- Task 1: review_queue.items, review_queue.today ------------------------------------------------

def test_items_reads_only_and_holds_the_whole_queue(cfg, tmp_path):
    client, c = _app(cfg, tmp_path)
    eid = _queue_fixture(c)
    ecfg = C.effective(cfg, get_settings(c))
    before = c.total_changes
    q = review_queue.items(c, ecfg, date.today())
    assert c.total_changes == before and not c.in_transaction
    assert len(q["tasks"]) == 21                                      # no cap
    for i in q["tasks"]:
        assert i["key"] == f"t:{i['task']['id']}" and TASK_KEYS <= set(i)
    item = next(i for i in q["tasks"] if i["task"]["id"] == 21)
    assert [e["label"] for e in item["evidence"]] == ["Max wrote"] and item["flags"] == {"thread_updated": True}
    assert [x["key"] for x in q["cards"]] == [f"s:{eid}"] and q["cards"][0]["fields"]["name"] == "Order the reagent"


def test_get_review_is_items_with_its_cap_its_exclude_and_its_stamp(cfg, tmp_path):
    client, c = _app(cfg, tmp_path)
    eid = _queue_fixture(c)
    ecfg = C.effective(cfg, get_settings(c))
    want = [i["task"]["id"] for i in review_queue.items(c, ecfg, date.today())["tasks"] if i["task"]["id"] not in (1, 2)][:15]
    rv = client.get("/api/review?exclude=1,2").json()
    assert [i["task"]["id"] for i in rv["items"]] == want and rv["total"] == 19 and rv["cap"] == 15
    assert [s["id"] for s in rv["suggestions"]] == [eid]
    offered = {r[0] for r in c.execute("SELECT id FROM tasks WHERE last_offered_at IS NOT NULL")}
    assert offered == set(want)


def test_reviews_day_is_the_owners_not_the_machines(cfg, tmp_path):
    C.set_(cfg, "owner.timezone", "Pacific/Kiritimati")
    client, c = _app(cfg, tmp_path)
    want = datetime.now(ZoneInfo("Pacific/Kiritimati")).date()
    assert review_queue.today(cfg) == want
    assert client.get("/api/review").json()["today"] == want.isoformat()
    assert client.post("/api/review/finish", json={}).status_code == 200
    assert get_settings(c)["state.last_review_at"] == want.isoformat()


# --- Task 2: review_cards.card_changes; the Create route built on it --------------------------------

TASK_COLS = ("source", "source_key", "source_link", "account", "name", "project", "due", "context")


def _four_cards(c):
    mk(c, 2, name="Register for the visit")
    return [_kind_card(c, k, other=2) for k in ("planner", "mail", "slack", "ripple")]


def _mail_card(c, conv, name):
    ref = {"op": "create", "fields": {"name": name},
           "mail": {"ref": conv, "account": "me@example.edu", "subject": f"About {name}", "sender": "Prof <prof@example.edu>", "at": "2026-11-02T09:00:00Z"}}
    cur = c.execute("INSERT INTO evidence (task_id, kind, ref, account, label, excerpt, at) VALUES (NULL, 'proposal', ?, 'me@example.edu', ?, 'r', ?)",
                    (json.dumps(ref), f"create: {name}", now_iso()))
    c.commit()
    return cur.lastrowid


def test_card_changes_writes_nothing_and_is_what_the_route_applies(cfg, tmp_path):
    _, a = _app(cfg, tmp_path, "a.db")
    client_b, b = _app(cfg, tmp_path, "b.db")
    ids_a, ids_b = _four_cards(a), _four_cards(b)
    assert ids_a == ids_b
    ecfg = C.effective(cfg, get_settings(a))
    day = review_queue.today(ecfg)
    for eid in ids_a:
        before = a.total_changes
        plan = review_cards.card_changes(a, ecfg, eid, None, day)
        assert a.total_changes == before and not a.in_transaction
        assert plan.summary == {"task_id": None, "reopened": False, "already": False, "ref": f"card{eid}"} and plan.skipped == []
        res = batches.apply(a, plan.changes, "review", day)
        assert res["refs"] == {f"card{eid}": res["created"][0]}
    for eid in ids_b:
        assert client_b.post(f"/api/review/suggestions/{eid}/create", json={}).status_code == 200
    q = "SELECT task_id, field, old, new FROM changes ORDER BY id"
    assert [tuple(r) for r in a.execute(q)] == [tuple(r) for r in b.execute(q)]
    q = f"SELECT {', '.join(TASK_COLS)} FROM tasks WHERE id != 2 ORDER BY id"
    assert [tuple(r) for r in a.execute(q)] == [tuple(r) for r in b.execute(q)]
    assert len(a.execute(q).fetchall()) == 4
    slack = review_cards.card_changes(a, ecfg, ids_a[2], None, day)          # judged now: nothing to write, the task it made
    assert slack.changes == [] and slack.summary["already"] and slack.summary["task_id"] is not None


def test_a_slack_cards_post_is_its_message_link(cfg, tmp_path):
    _, c = _app(cfg, tmp_path)
    eid = _kind_card(c, "slack")
    plan = review_cards.card_changes(c, C.effective(cfg, get_settings(c)), eid, None, date.today())
    assert plan.post == [{"task_id": None, "kind": "slack_message", "ref": "https://dice.slack.com/archives/C1/p1",
                          "label": "#general · Max M: lunch tomorrow?", "account": "DiCE Lab"}]
    create = plan.changes[0]
    assert (create["op"], create["created_source"], create["source_key"], create["fields"]["project"]) == ("create", "Slack", f"slack-mention:{eid}", "DiCE Lab")


def test_two_cards_in_one_batch_get_their_own_refs(cfg, tmp_path):
    _, c = _app(cfg, tmp_path)
    e1, e2 = _mail_card(c, "conv-one", "Send the abstract"), _mail_card(c, "conv-two", "Book the room")
    ecfg = C.effective(cfg, get_settings(c))
    p1, p2 = (review_cards.card_changes(c, ecfg, e, None, date.today()) for e in (e1, e2))
    res = batches.apply(c, p1.changes + p2.changes, "review", date.today())
    assert len(res["created"]) == 2 and res["skipped"] == []
    t1, t2 = res["refs"][f"card{e1}"], res["refs"][f"card{e2}"]
    threads = {r["task_id"]: r["ref"] for r in c.execute("SELECT task_id, ref FROM links WHERE kind = 'mail_thread'")}
    assert threads == {t1: "conv-one", t2: "conv-two"}
    assert {r[0] for r in c.execute("SELECT verdict FROM evidence WHERE id IN (?, ?)", (e1, e2))} == {"applied"}


def test_card_changes_refuses_an_empty_name_and_a_judged_card(cfg, tmp_path):
    client, c = _app(cfg, tmp_path)
    ecfg = C.effective(cfg, get_settings(c))
    eid = _kind_card(c, "planner")
    with pytest.raises(ValueError, match="a task needs a name"):
        review_cards.card_changes(c, ecfg, eid, {"fields": {"name": "  "}}, date.today())
    with pytest.raises(LookupError, match="no such suggestion"):
        review_cards.card_changes(c, ecfg, 9999, None, date.today())
    c.execute("INSERT INTO evidence (task_id, kind, label, excerpt, at, judged_at, verdict) VALUES (NULL, 'proposal', 'old', 'x', 'now', 'now', 'dismissed')")
    c.commit()
    judged = c.execute("SELECT MAX(id) FROM evidence").fetchone()[0]
    with pytest.raises(LookupError, match="this suggestion was already judged"):
        review_cards.card_changes(c, ecfg, judged, None, date.today())
    tid = client.post(f"/api/review/suggestions/{eid}/create", json={}).json()["task_id"]
    again = review_cards.card_changes(c, ecfg, eid, None, date.today())
    assert again.changes == [] and again.post == [] and again.summary == {"task_id": tid, "reopened": False, "already": True, "ref": f"card{eid}"}
    assert again.skipped == [f"suggestion {eid}: already created as Task-{tid}"]


def test_card_changes_after_undo_reopens_with_the_draft(cfg, tmp_path):
    client, c = _app(cfg, tmp_path)
    ecfg = C.effective(cfg, get_settings(c))
    eid = _kind_card(c, "planner")
    made = client.post(f"/api/review/suggestions/{eid}/create", json={}).json()
    client.post(f"/api/batches/{made['batch_id']}/undo", json={})
    plan = review_cards.card_changes(c, ecfg, eid, {"fields": {"name": "Renamed"}}, date.today())
    assert [ch["op"] for ch in plan.changes] == ["reopen", "set", "judge_evidence"]
    assert plan.changes[1] == {"op": "set", "task_id": made["task_id"], "fields": {"name": "Renamed"}}
    assert plan.summary == {"task_id": made["task_id"], "reopened": True, "already": False, "ref": f"card{eid}"}
