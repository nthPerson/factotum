"""Where stage 6 (by hand) meets stages 5 (ripples) and 7 (suggestions, filing) after the merge."""
from datetime import date

from fastapi.testclient import TestClient

from factotum import api, filing
from factotum.db import connect, init_db

from stage5_helpers import no_claude, queue  # noqa: F401  (no_claude: autouse, no real Claude run)


def _client(cfg, tmp_path):
    path = tmp_path / "t.db"
    conn = connect(path)
    init_db(conn)
    return TestClient(api.create_app(cfg, db_path=str(path))), conn


def _row(conn, tid):
    return conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()


def test_a_task_added_by_hand_with_only_a_name_is_queued_for_filing(cfg, tmp_path):
    client, conn = _client(cfg, tmp_path)
    r = client.post("/api/add", json={"text": "Email the chair", "key": "abc123"})
    assert r.status_code == 200, r.text
    tid = r.json()["task_id"]
    t = _row(conn, tid)
    assert t["source"] == "Manual" and t["source_key"] == "add:abc123"
    assert filing.wants(conn, tid) and tid in filing.queued(conn, date.today())
    # filing leaves alone a task already placed by its links (a step of a goal): it is not "only a name"
    r2 = client.post("/api/add", json={"fields": {"name": "Book the room", "under": tid}})
    assert r2.status_code == 200, r2.text
    assert r2.json()["task_id"] not in filing.queued(conn, date.today())


NEW = {"ripple_queue": None, "chat_threads": ["origin", "task_id"], "chat_messages": ["read_json"]}


def _have(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    return tables, {t: {r[1] for r in conn.execute(f"PRAGMA table_info({t})")} for t in tables}


def test_a_database_from_either_branch_or_their_base_migrates_to_the_merged_schema(tmp_path):
    fresh = connect(tmp_path / "fresh.db")
    init_db(fresh)
    want = _have(fresh)
    # af49c98 had neither side's additions; suggestions alone lacks byhand's columns; byhand alone lacks ripple_queue
    for name, drop in (("base", ("ripple_queue", "chat_threads", "chat_messages")),
                       ("suggestions", ("chat_threads", "chat_messages")), ("byhand", ("ripple_queue",))):
        conn = connect(tmp_path / f"{name}.db")
        init_db(conn)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for t in drop:
            if NEW[t] is None:
                conn.execute(f"DROP TABLE {t}")
            else:
                for col in NEW[t]:
                    conn.execute(f"ALTER TABLE {t} DROP COLUMN {col}")
        conn.commit()
        assert _have(conn) != want
        init_db(conn)
        init_db(conn)                                   # twice: nothing to add the second time
        assert _have(conn) == want, name
        assert conn.execute("PRAGMA user_version").fetchone()[0] == version


def test_added_tasks_ripple_on_an_outcome_and_an_undo_drops_the_add_and_its_links(cfg, tmp_path):
    client, conn = _client(cfg, tmp_path)
    other = client.post("/api/add", json={"text": "Draft the agenda"}).json()["task_id"]
    goal = client.post("/api/add", json={"text": "Run the workshop"}).json()["task_id"]
    r = client.post("/api/add", json={"text": f"Book the room under {goal} after {other}"}).json()
    step, step_batch = r["task_id"], r["batch_id"]
    rel = {(x["src"], x["dst"], x["kind"]) for x in conn.execute("SELECT * FROM relations WHERE removed_at IS NULL")}
    assert rel == {(step, goal, "part_of"), (step, other, "waits_on")}

    res = client.post("/api/batches/apply", json={"changes": [{"op": "done", "task_id": goal, "fields": {"outcome": "Held online"}}]}).json()
    assert res["ripple_queued"] == [goal]
    q = queue(conn)[goal]
    assert q["now"] == 1 and "outcome" in q["what"] and "closed" in q["what"]

    u = client.post(f"/api/batches/{step_batch}/undo", json={}).json()
    assert u["already"] is False
    t = _row(conn, step)
    assert t is not None and t["status"] == "dropped" and t["closed_by"] == "undo"
    assert not conn.execute("SELECT 1 FROM relations WHERE src = ? AND removed_at IS NULL", (step,)).fetchall()
    assert conn.execute("SELECT COUNT(*) FROM relations WHERE src = ?", (step,)).fetchone()[0] == 2   # soft-removed, kept

    # the goal's own add batch can no longer drop it: it was closed since, and is left as it is
    gb = conn.execute("SELECT batch_id FROM changes WHERE task_id = ? AND field = 'created'", (goal,)).fetchone()[0]
    u2 = client.post(f"/api/batches/{gb}/undo", json={}).json()
    assert _row(conn, goal)["status"] == "done" and any("closed after it was created" in s["why"] for s in u2["skipped"])
