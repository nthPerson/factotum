"""Stage 4a, the API payloads (plan "API payloads"): what the list, the opened task and Review ship."""
import unittest.mock
from datetime import date

from fastapi.testclient import TestClient

from factotum import api, batches, relations, scoring
from factotum.db import connect, init_db, now_iso, rows
from stage4_helpers import TODAY, link, mk


def _client(cfg, tmp_path) -> tuple[TestClient, object]:
    """The app and a connection of the test's own, both on `tmp_path / "t.db"`."""
    path = tmp_path / "t.db"
    conn = connect(path)
    init_db(conn)
    return TestClient(api.create_app(cfg, db_path=str(path))), conn


def _done(conn, tid: int, **fields) -> dict:
    return batches.apply(conn, [{"op": "done", "task_id": tid, "fields": fields}], "ui", TODAY)


# --- Task 12: list and review payloads

def test_the_list_ships_compact_rel_only_where_there_is_something(cfg, tmp_path):
    client, conn = _client(cfg, tmp_path)
    for i in range(1, 6):
        mk(conn, i)
    link(conn, 1, 2, "waits_on")
    link(conn, 3, 4, "part_of")
    _done(conn, 4, outcome="Sent")
    body = client.get("/api/tasks").json()
    by = {t["id"]: t for t in body["tasks"]}
    assert set(by) == {1, 2, 3, 5}
    assert "rel" not in by[5] and set(by[1]["rel"]) == set(relations.COMPACT_KEYS)
    assert by[1]["rel"]["links"] == [{"id": unittest.mock.ANY, "kind": "waits_on", "dir": "out", "other": 2}] and by[2]["rel"]["frees_open"] == [1]
    assert by[3]["rel"]["goal_id"] == 4 and "waits_on_closed" not in by[3]["rel"]
    assert body["neighbours"] == {"4": {"id": 4, "name": "Task 4", "status": "done", "due": None, "completed_at": "2026-11-01", "outcome": "Sent"}}

    # Today's payload, checked: links off and no relations at all
    plain = tmp_path / "plain"
    plain.mkdir()
    cfg["scoring"]["links"]["enabled"] = False
    client, conn = _client(cfg, plain)
    for i in range(1, 4):
        mk(conn, i, due="2026-11-20" if i == 2 else None, hard=1 if i == 3 else 0)
    body = client.get("/api/tasks").json()
    assert body["neighbours"] == {} and len(body["tasks"]) == 3 and all("rel" not in t for t in body["tasks"])
    for t in body["tasks"]:
        row = rows(conn, "SELECT * FROM tasks WHERE id = ?", (t["id"],))[0]
        assert (t["score"], t["priority"], t["score_reasons"]) == tuple(scoring.score(row, date.today(), cfg)), t["id"]


def test_the_list_reads_outside_links_only_for_listed_tasks(cfg, tmp_path):
    client, conn = _client(cfg, tmp_path)
    mk(conn, 1); mk(conn, 2)
    _done(conn, 2)
    for tid, ref in ((1, "open-thread"), (2, "closed-thread")):
        conn.execute("INSERT INTO links (task_id, kind, ref, label, created_at) VALUES (?, 'mail_thread', ?, ?, ?)", (tid, ref, ref, now_iso()))
    conn.commit()
    sqls: list[str] = []
    client.app.state.conn.set_trace_callback(sqls.append)
    tasks = client.get("/api/tasks").json()["tasks"]
    client.app.state.conn.set_trace_callback(None)
    read = [s for s in sqls if "FROM links" in s]
    assert read and all("WHERE" in s and "kind != 'task'" in s for s in read), read
    assert [t["id"] for t in tasks] == [1]
    assert [l["ref"] for t in tasks for l in t["links"]] == ["open-thread"]


def test_review_items_carry_texts_and_link_suggestions(cfg, tmp_path):
    client, conn = _client(cfg, tmp_path)
    for i in range(1, 5):
        mk(conn, i, last_mention_at=date.today().isoformat())
    link(conn, 1, 2, "waits_on")
    _done(conn, 2)
    assert relations.suggest(conn, "same_as", 3, 4, "same number 3", "twin")
    conn.commit()
    body = client.get("/api/review").json()
    items = {i["task"]["id"]: i for i in body["items"]}
    assert "freed" in items[1]["reasons"] and items[1]["texts"] == {"freed": "Free now: Task-2 is done"}
    assert items[1]["task"]["rel"]["waits_on_open"] == [] and "waits_on_closed" not in items[1]["task"]["rel"]
    assert items[3]["link_suggestions"] == [{"evidence_id": unittest.mock.ANY, "kind": "same_as", "other": 4, "keep": 4, "why": "same number 3",
                                             "signal": "twin", "other_name": "Task 4"}]
    assert [(s["other"], s["other_name"]) for s in items[4]["link_suggestions"]] == [(3, "Task 3")]
    assert items[4]["texts"] == {"link_suggested": "Suggested: same as Task-3?"} and items[1]["link_suggestions"] == []
    assert not [e for i in body["items"] for e in i["evidence"] if e["kind"] == "link_suggestion"]
    assert "freed" in body["labels"]


# --- Task 13: task detail and the Log

RIPPLE = {"kind": "ripple", "from": [2], "run": 9}


def test_an_opened_task_has_history_with_sources_causes_notes_and_link_names(cfg, tmp_path):
    client, conn = _client(cfg, tmp_path)
    mk(conn, 1); mk(conn, 2)
    first = link(conn, 1, 2, "waits_on", why="needs data")["batch_id"]
    second = batches.apply(conn, [{"op": "set", "task_id": 1, "fields": {"context": "The data is in."}, "note": "data arrived"}], "ripple", TODAY, cause=RIPPLE)["batch_id"]
    batches.undo(conn, first)
    body = client.get("/api/tasks/1").json()
    h = body["history"]
    assert [e["batch_id"] for e in h["entries"]] == [second, first] and h["more"] is False and h["next_before"] is None
    s, ln = h["entries"]
    assert s["change_id"] > ln["change_id"]
    assert (s["source"], s["cause"], s["note"], s["undone"], s["other"]) == ("ripple", RIPPLE, "data arrived", False, None)
    assert (s["field"], s["old"], s["new"], s["batch_changes"], s["batch_tasks"]) == ("context", None, "The data is in.", 1, 1) and s["at"]
    assert (ln["source"], ln["cause"], ln["note"], ln["undone"], ln["field"], ln["old"]) == ("ui", None, None, True, "link", None)
    assert ln["other"] == {"id": 2, "name": "Task 2", "status": "open"}
    assert ln["new"]["other_name"] == "Task 2" and ln["new"]["kind"] == "waits_on" and ln["new"]["dir"] == "out" and ln["new"]["why"] == "needs data"
    assert (ln["batch_changes"], ln["batch_tasks"]) == (2, 2)
    # the other end's History names this task
    other = client.get("/api/tasks/2").json()["history"]["entries"]
    assert [(e["field"], e["other"]["id"], e["new"]["dir"]) for e in other] == [("link", 1, "in")]
    assert client.get("/api/tasks/99").status_code == 404


def test_history_pages_with_before(cfg, tmp_path):
    client, conn = _client(cfg, tmp_path)
    mk(conn, 1)
    for i in range(35):
        batches.apply(conn, [{"op": "set", "task_id": 1, "fields": {"context": f"v{i}"}}], "ui", TODAY)
    h = client.get("/api/tasks/1").json()["history"]
    ids = [e["change_id"] for e in h["entries"]]
    assert len(ids) == 30 and ids == sorted(ids, reverse=True) and h["more"] is True and h["next_before"] == min(ids)
    assert h["entries"][0]["new"] == "v34"
    h2 = client.get(f"/api/tasks/1?before={h['next_before']}").json()["history"]
    assert len(h2["entries"]) == 5 and h2["more"] is False and h2["next_before"] is None
    assert max(e["change_id"] for e in h2["entries"]) < min(ids) and h2["entries"][-1]["new"] == "v0"
    assert len(client.get("/api/tasks/1?limit=500").json()["history"]["entries"]) == 35
    small = client.get("/api/tasks/1?limit=0").json()["history"]
    assert len(small["entries"]) == 1 and small["more"] is True


def test_a_closed_task_opens_with_its_links_and_provenance(cfg, tmp_path):
    client, conn = _client(cfg, tmp_path)
    for i in range(1, 6):
        mk(conn, i)
    link(conn, 1, 2, "waits_on", why="needs the data first")
    link(conn, 5, 1, "waits_on")
    for tid, kind, ref, label in ((1, "plaud_note", "of_x", "Lab meeting Nov 3"), (3, "plaud_note", "of_x", "Lab meeting Nov 3"),
                                  (1, "mail_thread", "c9", "Re: the data"), (4, "mail_thread", "c9", "Re: the data"),
                                  (5, "plaud_note", "of_x", "Lab meeting Nov 3"), (5, "mail_thread", "c9", "Re: the data"),
                                  (1, "task", "4", None)):
        conn.execute("INSERT INTO links (task_id, kind, ref, label, created_at) VALUES (?, ?, ?, ?, ?)", (tid, kind, ref, label, now_iso()))
    conn.commit()
    _done(conn, 1, outcome="Sent to the lab")
    body = client.get("/api/tasks/1").json()
    assert body["status"] == "done" and body["outcome"] == "Sent to the lab"
    assert {l["kind"] for l in body["links"]} == {"plaud_note", "mail_thread"}
    assert [(r["kind"], r["dir"], r["other"]["id"]) for r in body["relations"]] == [("waits_on", "out", 2), ("waits_on", "in", 5)]
    r0 = body["relations"][0]
    assert r0["other"] == {"id": 2, "name": "Task 2", "status": "open", "due": None, "hard": 0, "completed_at": None, "outcome": None}
    assert (r0["why"], r0["source"]) == ("needs the data first", "ui") and r0["batch_id"] and r0["created_at"] and r0["id"]
    assert {n["id"] for n in body["nearby"]} == {3, 4}
    assert {n["id"]: (n["via"], n["label"], n["name"], n["status"]) for n in body["nearby"]} == {
        3: ("recording", "Lab meeting Nov 3", "Task 3", "open"), 4: ("mail thread", "Re: the data", "Task 4", "open")}
    assert set(body["rel"]) == set(relations.COMPACT_KEYS) and body["rel"]["frees_open"] == [5]
    # the other end sees the closed task, with its outcome
    two = client.get("/api/tasks/2").json()
    assert two["relations"][0]["other"] == {"id": 1, "name": "Task 1", "status": "done", "due": None, "hard": 0, "completed_at": "2026-11-01", "outcome": "Sent to the lab"}
    assert two["nearby"] == [] and set(two["rel"]) == set(relations.COMPACT_KEYS)


def test_nearby_puts_open_tasks_first_then_the_newest_and_stops_at_the_limit(cfg, tmp_path):
    client, conn = _client(cfg, tmp_path)
    mk(conn, 1)
    for i in range(2, 13):
        mk(conn, i, created_at=f"2026-10-{i:02d}T00:00:00+00:00")
    _done(conn, 12)
    for tid in range(1, 13):
        conn.execute("INSERT INTO links (task_id, kind, ref, label, created_at) VALUES (?, 'plaud_note', 'of_y', 'Standup', ?)", (tid, now_iso()))
    conn.commit()
    assert [n["id"] for n in relations.nearby(conn, 1)] == [11, 10, 9, 8, 7, 6, 5, 4]
    assert [n["id"] for n in relations.nearby(conn, 1, limit=20)] == [11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 12]


def test_opening_a_task_stamps_neighbours_seen_at_and_clears_the_changed_marker(cfg, tmp_path):
    client, conn = _client(cfg, tmp_path)
    mk(conn, 1); mk(conn, 2)
    batches.apply(conn, [{"op": "set", "task_id": 1, "fields": {"context": "The data is in."}}], "ripple", TODAY, cause=RIPPLE)
    t1 = next(t for t in client.get("/api/tasks").json()["tasks"] if t["id"] == 1)
    assert t1["rel"]["changed"]["by"] == 2
    body = client.get("/api/tasks/1").json()
    assert body["neighbours_seen_at"] is None and body["rel"]["changed"]["by"] == 2
    assert conn.execute("SELECT neighbours_seen_at FROM tasks WHERE id = 1").fetchone()[0]
    t1 = next(t for t in client.get("/api/tasks").json()["tasks"] if t["id"] == 1)
    assert (t1.get("rel") or {}).get("changed") is None
    assert client.get("/api/tasks/1").json()["neighbours_seen_at"] is not None


def test_the_log_carries_cause_and_notes(cfg, tmp_path):
    client, conn = _client(cfg, tmp_path)
    mk(conn, 1); mk(conn, 2)
    plain = batches.apply(conn, [{"op": "set", "task_id": 2, "fields": {"context": "plain"}}], "ui", TODAY)["batch_id"]
    rip = batches.apply(conn, [{"op": "set", "task_id": 1, "fields": {"context": "The data is in."}, "note": "data arrived"}], "ripple", TODAY, cause=RIPPLE)["batch_id"]
    by = {b["id"]: b for b in client.get("/api/log").json()["batches"]}
    assert by[rip]["cause"] == RIPPLE and [c["note"] for c in by[rip]["changes"]] == ["data arrived"]
    assert by[plain]["cause"] is None and [c["note"] for c in by[plain]["changes"]] == [None]
    assert "cause_json" not in by[rip]
