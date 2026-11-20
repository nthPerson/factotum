"""Spoken Review, stage 1: the tray's routes and the decide route (design §5.4). JSON only, never 401, 403 or a
redirect; every error that can follow a decision carries the tray beside `detail`; the count and GET /api/review
leave out what the tray decides; the routes a page loaded before the deploy calls still work."""
import json

from fastapi.testclient import TestClient

from stage4_helpers import mk
from test_polish import _card as _kind_card
from test_review_decide import _reply

from factotum import batches, review_queue, tray
from factotum.api import create_app
from factotum.db import now_iso


def _app(cfg, tmp_path, name="a.db"):
    client = TestClient(create_app(cfg, db_path=str(tmp_path / name)), follow_redirects=False)
    return client, client.app.state.conn


def keep(tid):
    return {"task_id": tid, "op": "keep", "fields": {}, "label": "kept"}


def _proposal(c, tid, change, label="a suggestion"):
    cur = c.execute("INSERT INTO evidence (task_id, kind, ref, label, excerpt, at) VALUES (?, 'proposal', ?, ?, 'r', ?)",
                    (tid, json.dumps(change), label, now_iso()))
    c.commit()
    return cur.lastrowid


NEW_POSTS = ("/api/tray/ops", "/api/tray/carry", "/api/tray/apply", "/api/review/decide")


def test_the_new_routes_take_json_only(cfg, tmp_path):
    client, _ = _app(cfg, tmp_path)
    for path in NEW_POSTS:
        r = client.post(path, content="{}", headers={"Content-Type": "text/plain"})
        assert r.status_code == 415, path


def test_no_failure_answers_401_403_or_a_redirect(cfg, tmp_path):
    client, c = _app(cfg, tmp_path)
    mk(c, 1)
    mk(c, 2, status="done")
    eid = _kind_card(c, "planner")
    calls = [("post", "/api/tray/ops", {"ops": [{"put": "1", "entry": {"task_id": 2, "op": "keep"}}]}),
             ("post", "/api/tray/ops", {"ops": "nope"}),
             ("post", "/api/tray/carry", {"carry_id": "bad id!", "entries": []}),
             ("post", "/api/tray/apply", {"source": "ripple"}),
             ("post", "/api/tray/apply", {"seen_rev": -1}),
             ("post", "/api/review/decide", {"key": "t:2", "option": "keep"}),
             ("post", "/api/review/decide", {"key": "t:1", "option": "explode"}),
             ("post", "/api/review/decide", {"key": "g:rec:1", "option": "keep"}),
             ("post", "/api/review/decide", {"key": f"s:{eid}", "option": "create", "args": {"draft": {"fields": {"name": " "}}}}),
             ("post", "/api/review/decide", {"key": "t:1", "option": "accept:999"}),
             ("post", "/api/review/decide", {"key": "t:1"}),
             ("post", "/api/review/decide", {"key": "t:1", "from_chat": {"message_id": "x"}}),
             ("post", "/api/review/decide", {"key": "s:abc", "option": "create"}),
             ("post", "/api/review/decide", {"key": "t:1", "option": "later", "args": {"until": 5}}),
             ("post", "/api/review/decide", {"key": "t:1", "option": "remind", "args": {}}),
             ("post", "/api/review/decide", {"key": "t:1", "option": "due", "args": {"due": "zz"}}),
             ("post", "/api/tray/ops", {"ops": [{"put": "x", "entry": None}]}),
             ("post", "/api/tray/carry", {"carry_id": "a", "entries": [["1", "x"], 5]}),
             ("get", "/api/tray?since=abc", None)]
    for method, path, body in calls:
        r = client.post(path, json=body) if method == "post" else client.get(path)
        assert r.status_code not in (301, 302, 303, 307, 308, 401, 403), (path, body, r.status_code)
        assert r.status_code < 500, (path, body, r.status_code, r.text[:200])


def test_tray_ops_read_and_refuse(cfg, tmp_path):
    client, c = _app(cfg, tmp_path)
    mk(c, 1)
    mk(c, 2)
    r = client.post("/api/tray/ops", json={"ops": [{"put": "1", "entry": keep(1)}, {"put": "2:due", "entry": {"task_id": 2, "op": "set",
                                           "fields": {"due": "2026-12-01"}, "label": "due"}}], "device": "Phone"}).json()
    assert r["changed"] == ["1", "2:due"] and r["rev"] == 1
    assert [(k, m["decides"], m["origin"], m["device"]) for k, _, m in r["entries"]] == [("1", "t:1", "page", "Phone"), ("2:due", None, "page", "Phone")]
    assert client.get("/api/tray?since=1").json() == {"rev": 1, "same": True}
    assert len(client.get("/api/tray").json()["entries"]) == 2
    bad = client.post("/api/tray/ops", json={"ops": [{"del": "1"}, {"put": "2", "entry": {"task_id": 3, "op": "keep"}}]})
    assert bad.status_code == 400 and bad.json()["detail"].startswith("2: ")
    assert [k for k, _, _ in client.get("/api/tray").json()["entries"]] == ["1", "2:due"]          # nothing written
    # the page never says what a row decides: a pin under any key decides nothing, whatever it claims
    r = client.post("/api/tray/ops", json={"ops": [{"put": "2:top", "entry": {"task_id": 2, "op": "pin_top", "fields": {}}, "decides": "t:2"}]}).json()
    assert next(m for k, _, m in r["entries"] if k == "2:top")["decides"] is None


def test_decide_answers_the_tray(cfg, tmp_path):
    client, c = _app(cfg, tmp_path)
    mk(c, 1)
    thr = c.execute("INSERT INTO evidence (task_id, kind, ref, label, at) VALUES (1, 'thread_updated', 'conv-1', 'Max wrote', ?)", (now_iso(),)).lastrowid
    c.commit()
    r = client.post("/api/review/decide", json={"key": "t:1", "option": "done", "note": "Sent it", "device": "Phone"})
    assert r.status_code == 200
    j = r.json()
    assert j["keys"] == ["1", f"e:{thr}"] and j["removed"] == [] and j["said"] == ""
    assert [(k, e["op"], m["decides"], m["device"]) for k, e, m in j["tray"]["entries"]] == [("1", "done", "t:1", "Phone"), (f"e:{thr}", "judge_evidence", "t:1", "Phone")]
    assert j["tray"]["entries"][0][1]["fields"] == {"outcome": "Sent it"}
    j = client.post("/api/review/decide", json={"key": "t:1", "option": "undecide"}).json()
    assert j["tray"]["entries"] == [] and j["removed"] == ["1", f"e:{thr}"]
    # prop sent by a client is dropped: only a stored proposal marks one
    j = client.post("/api/review/decide", json={"key": "t:1", "option": "keep", "args": {"prop": "9-9"}}).json()
    assert "prop" not in j["tray"]["entries"][0][1]


def test_decide_from_the_cards_conversation(cfg, tmp_path):
    client, c = _app(cfg, tmp_path)
    mk(c, 1)
    mid = _reply(c, "review", 1, [{"op": "done", "task_id": 1, "fields": {"outcome": "Sent"}, "label": "done", "evidence": [], "applied": False},
                                  {"op": "add_note", "task_id": 1, "fields": {"text": "x"}, "label": "note", "evidence": [], "applied": False}])
    j = client.post("/api/review/decide", json={"key": "t:1", "from_chat": {"message_id": mid, "index": 0}}).json()
    assert j["tray"]["entries"][0][1] == {"task_id": 1, "op": "done", "fields": {"outcome": "Sent"}, "label": "done · “Sent”", "prop": f"{mid}-0"}
    r = client.post("/api/review/decide", json={"key": "t:1", "from_chat": {"message_id": mid, "index": 1}})
    assert r.status_code == 400 and r.json()["tray"]["entries"][0][0] == "1"


def test_decide_on_a_closed_task_is_404_with_the_tray(cfg, tmp_path):
    client, c = _app(cfg, tmp_path)
    mk(c, 1, status="done")
    mk(c, 2)
    client.post("/api/tray/ops", json={"ops": [{"put": "2", "entry": keep(2)}]})
    r = client.post("/api/review/decide", json={"key": "t:1", "option": "keep"})
    assert r.status_code == 404 and "Task-1" in r.json()["detail"]
    assert [k for k, _, _ in r.json()["tray"]["entries"]] == ["2"]
    r = client.post("/api/review/decide", json={"key": "t:2", "option": "later", "args": {"until": "1999-01-01"}})
    assert r.status_code == 400 and r.json()["tray"]["rev"] == 1
    assert not c.in_transaction


def test_decide_on_an_answered_suggestion_is_409_with_the_tray(cfg, tmp_path):
    client, c = _app(cfg, tmp_path)
    mk(c, 1)
    e = _proposal(c, 1, {"op": "pin_top", "task_id": 1}, "pin it")
    batches.apply(c, [{"op": "judge_evidence", "task_id": 1, "fields": {"evidence_id": e, "verdict": "dismissed"}}], "ui")    # answered on another device
    r = client.post("/api/review/decide", json={"key": "t:1", "option": f"accept:{e}"})
    assert r.status_code == 409 and r.json()["tray"] == {"rev": 0, "entries": []} and r.json()["detail"]
    eid = _kind_card(c, "planner")
    batches.apply(c, [{"op": "judge_evidence", "task_id": None, "fields": {"evidence_id": eid, "verdict": "dismissed"}}], "ui")
    r = client.post("/api/review/decide", json={"key": f"s:{eid}", "option": "create"})
    assert r.status_code == 404 and "tray" in r.json()


def test_the_count_leaves_out_what_the_tray_decides(cfg, tmp_path):
    client, c = _app(cfg, tmp_path)
    for i in (1, 2, 3):
        mk(c, i)
    e1, e2 = _kind_card(c, "planner"), _kind_card(c, "ripple")
    ecfg = cfg
    assert client.get("/api/review/count").json()["count"] == 5
    client.post("/api/review/decide", json={"key": "t:1", "option": "keep"})
    client.post("/api/review/decide", json={"key": f"s:{e1}", "option": "create"})          # a staged card Create
    client.post("/api/tray/ops", json={"ops": [{"put": "2:top", "entry": {"task_id": 2, "op": "pin_top", "fields": {}}}]})  # beside: still waiting
    assert client.get("/api/review/count").json()["count"] == 3
    assert review_queue.undecided_count(c, ecfg, review_queue.today(ecfg)) == 3
    assert client.get("/api/review/count?exclude=2").json()["count"] == 2                    # an old page's exclude still counts
    assert review_queue.count(c, ecfg, review_queue.today(ecfg)) == 5                        # the icon's count is unchanged in stage 1
    rv = client.get("/api/review").json()
    assert sorted(i["task"]["id"] for i in rv["items"]) == [2, 3] and rv["total"] == 2
    assert sorted(i["task"]["id"] for i in client.get("/api/review?exclude=3").json()["items"]) == [2]
    client.post("/api/review/decide", json={"key": f"s:{e2}", "option": "dismiss"})
    assert client.get("/api/review/count").json()["count"] == 2


def test_two_clients_see_one_tray(cfg, tmp_path):
    a, ca = _app(cfg, tmp_path, "one.db")
    b, _ = _app(cfg, tmp_path, "one.db")                       # another device: another app, another connection, one file
    mk(ca, 1)
    mk(ca, 2)
    a.post("/api/review/decide", json={"key": "t:1", "option": "keep", "device": "Phone"})
    seen = b.get("/api/tray").json()
    assert [(k, m["device"]) for k, _, m in seen["entries"]] == [("1", "Phone")]
    b.post("/api/tray/ops", json={"ops": [{"put": "2", "entry": keep(2)}], "device": "Desktop"})
    assert [k for k, _, _ in a.get(f"/api/tray?since={seen['rev']}").json()["entries"]] == ["1", "2"]
    r = a.post("/api/tray/apply", json={"seen_rev": seen["rev"]})                               # the phone had not seen the desktop's row
    assert r.status_code == 409 and r.json()["changed"] == ["2"] and len(r.json()["tray"]["entries"]) == 2
    rev = a.get("/api/tray").json()["rev"]
    res = a.post("/api/tray/apply", json={"seen_rev": rev, "finish": True}).json()
    assert res["applied"] == 2 and res["finished"] and res["tray"]["entries"] == []
    assert b.get("/api/tray").json()["entries"] == []
    assert b.get("/api/review/count").json()["count"] == 0


def test_tray_apply_answers_and_refuses(cfg, tmp_path):
    client, c = _app(cfg, tmp_path)
    mk(c, 1)
    eid = _kind_card(c, "planner")
    assert client.post("/api/tray/apply", json={"source": "ripple"}).status_code == 400
    client.post("/api/review/decide", json={"key": f"s:{eid}", "option": "create"})
    client.post("/api/review/decide", json={"key": "t:1", "option": "keep"})
    res = client.post("/api/tray/apply", json={"keys": [f"e:{eid}", "nothing"], "source": "ui"}).json()
    assert res["gone"] == ["nothing"] and len(res["created"]) == 1
    assert res["cards"] == [{"evidence_id": eid, "task_id": res["created"][0], "reopened": False, "already": False}]
    assert [k for k, _, _ in res["tray"]["entries"]] == ["1"]
    assert c.execute("SELECT source FROM batches WHERE id = ?", (res["batch_id"],)).fetchone()[0] == "ui"


def test_a_bad_row_refuses_the_apply_it_is_in_and_not_the_others(cfg, tmp_path):
    client, c = _app(cfg, tmp_path)
    mk(c, 1)
    tray.write(c, [{"put": "new:c999-0", "entry": {"task_id": None, "op": "create", "fields": {"name": "x"}, "from_chat": {"message_id": 999, "index": 0}}},
                   {"put": "1", "entry": keep(1)}], "page")
    c.commit()
    before = client.get("/api/tray").json()
    r = client.post("/api/tray/apply", json={})
    assert r.status_code == 400 and "from_chat" in r.json()["detail"]
    assert client.get("/api/tray").json() == before
    r = client.post("/api/tray/apply", json={"keys": ["1"]})
    assert r.status_code == 200 and r.json()["applied"] == 1
    assert [k for k, _, _ in r.json()["tray"]["entries"]] == ["new:c999-0"]


def test_tray_carry_route(cfg, tmp_path):
    client, c = _app(cfg, tmp_path)
    mk(c, 1)
    old = [[1, {"task_id": 1, "op": "keep", "fields": {}, "label": "kept"}], ["1:top", {"task_id": 1, "op": "pin_top", "fields": {}, "label": "pinned"}]]
    r = client.post("/api/tray/carry", json={"carry_id": "c-1", "entries": old, "device": "Phone"}).json()
    assert r["carried"] == 2 and r["kept_out"] == [] and r["skipped"] == 0 and [k for k, _, _ in r["entries"]] == ["1", "1:top"]
    r = client.post("/api/tray/carry", json={"carry_id": "c-2", "entries": old}).json()
    assert r["carried"] == 0 and r["skipped"] == 2 and r["rev"] == 1


def test_the_old_create_and_apply_routes_still_work(cfg, tmp_path):
    client, c = _app(cfg, tmp_path)
    mk(c, 1)
    eid = _kind_card(c, "planner")
    client.post("/api/tray/ops", json={"ops": [{"put": "1", "entry": keep(1)}]})              # the new tray holds something meanwhile
    r = client.post(f"/api/review/suggestions/{eid}/create", json={})
    assert r.status_code == 200 and r.json()["already"] is False and r.json()["task_id"]
    r = client.post("/api/batches/apply", json={"source": "review", "changes": [{"task_id": 1, "op": "keep", "fields": {}}]})
    assert r.status_code == 200 and r.json()["applied"] == 1
    assert client.post("/api/review/finish", json={}).status_code == 200
    assert [k for k, _, _ in client.get("/api/tray").json()["entries"]] == ["1"]               # an old page's Apply leaves the server's tray alone
