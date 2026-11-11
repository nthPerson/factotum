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
