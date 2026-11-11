import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from factotum import api, batches, db
from factotum.db import Migration, connect, init_db, migrate, one, rows, schema_version
from stage4_helpers import TODAY, active_rels, link, mk


# --- Task 1: migrations and schema

def test_fresh_database_has_relations_and_the_new_columns(conn):
    cols = lambda t: {r[1] for r in conn.execute(f"PRAGMA table_info({t})")}
    assert {"outcome", "neighbours_seen_at"} <= cols("tasks") and "cause_json" in cols("batches") and "note" in cols("changes")
    assert {"id", "src", "dst", "kind", "why", "source", "batch_id", "created_at", "removed_at", "removed_batch_id"} == cols("relations")
    mk(conn, 1); mk(conn, 2)
    ins = "INSERT INTO relations (src, dst, kind, source, created_at) VALUES (?, ?, 'waits_on', 'ui', 'now')"
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(ins, (1, 1))
    conn.execute(ins, (1, 2))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(ins, (1, 2))                                   # one active row per (src, dst, kind)
    conn.execute("UPDATE relations SET removed_at = 'now'")
    conn.execute(ins, (1, 2))                                       # a removed row does not count


def test_an_old_database_gains_the_columns_without_losing_rows(conn):
    mk(conn, 1)
    conn.execute("ALTER TABLE tasks DROP COLUMN outcome")
    conn.execute("ALTER TABLE tasks DROP COLUMN neighbours_seen_at")
    conn.execute("ALTER TABLE batches DROP COLUMN cause_json")
    conn.execute("ALTER TABLE changes DROP COLUMN note")
    conn.commit()
    init_db(conn)
    cols = lambda t: {r[1] for r in conn.execute(f"PRAGMA table_info({t})")}
    assert {"outcome", "neighbours_seen_at"} <= cols("tasks") and "cause_json" in cols("batches") and "note" in cols("changes")
    assert conn.execute("SELECT name FROM tasks WHERE id = 1").fetchone()[0] == "Task 1"


def test_migrations_run_in_order_once_and_set_user_version(conn):
    conn.execute("PRAGMA user_version = 0")
    ran = []
    ms = [Migration(2, "two", lambda c: ran.append(2)), Migration(1, "one", lambda c: ran.append(1))]
    assert migrate(conn, ms) == ["one", "two"]
    assert migrate(conn, ms) == []
    assert ran == [1, 2] and schema_version(conn) == 2


def test_a_data_migration_backs_up_first_with_the_backup_api(conn, tmp_path):
    conn.execute("PRAGMA user_version = 0")
    mk(conn, 1, name="before")
    ms = [Migration(1, "rename", lambda c: c.execute("UPDATE tasks SET name = 'after' WHERE id = 1"), lambda c: True)]
    migrate(conn, ms)
    files = list((tmp_path / "backups").glob("*.db"))
    assert len(files) == 1 and "before-v1" in files[0].name
    old = sqlite3.connect(str(files[0]))
    assert old.execute("SELECT name FROM tasks WHERE id = 1").fetchone()[0] == "before"
    assert old.execute("PRAGMA user_version").fetchone()[0] == 0
    old.close()
    assert conn.execute("SELECT name FROM tasks WHERE id = 1").fetchone()[0] == "after"


def test_a_failing_migration_rolls_back_and_raises(conn):
    conn.execute("PRAGMA user_version = 0")
    mk(conn, 1, name="keep")
    def boom(c):
        c.execute("UPDATE tasks SET name = 'changed' WHERE id = 1")
        raise RuntimeError("no")
    with pytest.raises(RuntimeError):
        migrate(conn, [Migration(1, "boom", boom)])
    assert conn.execute("SELECT name FROM tasks WHERE id = 1").fetchone()[0] == "keep"
    assert schema_version(conn) == 0 and conn.in_transaction is False


# --- Task 2: link, unlink, undo

def _unlink(conn, src, dst, kind):
    return batches.apply(conn, [{"op": "unlink", "task_id": src, "fields": {"kind": kind, "other": dst}}], "ui", TODAY)


def test_link_records_one_relation_two_changes_and_bumps_both_tasks(conn):
    mk(conn, 1, name="Run the analysis"); mk(conn, 2, name="Get the data")
    res = link(conn, 1, 2, "waits_on", why="needs the data")
    rel = one(conn, "SELECT * FROM relations")
    assert (rel["src"], rel["dst"], rel["kind"], rel["source"], rel["batch_id"], rel["why"]) == (1, 2, "waits_on", "ui", res["batch_id"], "needs the data")
    ch = {r["task_id"]: json.loads(r["new"]) for r in rows(conn, "SELECT task_id, new FROM changes WHERE field = 'link'")}
    assert ch[1] == {"relation_id": rel["id"], "kind": "waits_on", "dir": "out", "other": 2, "other_name": "Get the data", "why": "needs the data"}
    assert ch[2]["dir"] == "in" and ch[2]["other"] == 1 and ch[2]["other_name"] == "Run the analysis"
    assert all(t["edited_at"] > "2026-10-01T00:00:00+00:00" for t in rows(conn, "SELECT edited_at FROM tasks"))
    assert json.loads(one(conn, "SELECT undo_json FROM batches")["undo_json"]) == [{"relation_id": rel["id"], "undo": "remove"}]


def test_an_unchanged_link_is_a_no_op(conn):
    mk(conn, 1); mk(conn, 2)
    assert link(conn, 1, 2, "part_of")["applied"] == 1
    res = link(conn, 1, 2, "part_of")
    assert (res["applied"], res["skipped"]) == (0, [])
    assert one(conn, "SELECT COUNT(*) AS n FROM relations")["n"] == 1
    assert one(conn, "SELECT COUNT(*) AS n FROM changes WHERE batch_id = ?", (res["batch_id"],))["n"] == 0


def test_link_rules_are_enforced_on_the_batchs_own_cursor(conn):
    for i in (1, 2, 3, 4, 5, 6, 8, 9):
        mk(conn, i)
    mk(conn, 7, status="done", completed_at="2026-10-20")
    L = lambda a, b, kind: {"op": "link", "task_id": a, "fields": {"kind": kind, "other": b}}
    res = batches.apply(conn, [L(2, 1, "part_of"), L(3, 2, "part_of"), L(2, 4, "part_of"), L(1, 5, "part_of"), L(2, 1, "waits_on"),
                               L(6, 7, "waits_on"), L(6, 6, "waits_on"), L(6, 999, "waits_on")], "ui", TODAY)
    assert res["applied"] == 1 and active_rels(conn) == {(2, 1, "part_of")}
    whys = [s["why"] for s in res["skipped"]]
    expected = ["a step cannot have steps", "is already part of Task-1", "has steps of its own", "cannot wait on its own goal",
                "is already done", "linked to itself", "no Task-999"]
    assert len(whys) == len(expected) and all(e in w for e, w in zip(expected, whys)), whys
    assert "Task-2 is a step of Task-1" in whys[0] and "Task-7 is already done" in whys[4]

    res = batches.apply(conn, [L(8, 9, "waits_on"), L(9, 8, "waits_on")], "ui", TODAY)
    assert res["applied"] == 1 and "that would make a loop" in res["skipped"][0]["why"]

    for i in range(10, 31):
        mk(conn, i)
    res = batches.apply(conn, [L(i, 30, "part_of") for i in range(9, 30)], "ui", TODAY)          # 21 steps for goal 30
    assert res["applied"] == 20 and len(res["skipped"]) == 1 and "Task-30 already has 20 part of links" in res["skipped"][0]["why"]


def test_a_cycle_across_two_batches_is_refused_when_the_second_applies(conn):
    mk(conn, 1); mk(conn, 2); mk(conn, 3)
    assert link(conn, 1, 2, "waits_on")["applied"] == 1
    assert link(conn, 2, 3, "waits_on")["applied"] == 1
    c = link(conn, 3, 1, "waits_on")
    assert c["applied"] == 0 and "that would make a loop" in c["skipped"][0]["why"]
    assert active_rels(conn) == {(1, 2, "waits_on"), (2, 3, "waits_on")}


def test_unlink_soft_removes_and_undo_restores(conn):
    mk(conn, 1); mk(conn, 2)
    link(conn, 1, 2, "waits_on")
    res = _unlink(conn, 1, 2, "waits_on")
    assert res["applied"] == 1
    rel = one(conn, "SELECT * FROM relations")
    assert rel["removed_at"] and rel["removed_batch_id"] == res["batch_id"] and active_rels(conn) == set()
    ch = rows(conn, "SELECT task_id, old, new FROM changes WHERE batch_id = ? AND field = 'link' ORDER BY task_id", (res["batch_id"],))
    assert [c["task_id"] for c in ch] == [1, 2] and all(c["new"] is None and json.loads(c["old"])["relation_id"] == rel["id"] for c in ch)
    u = batches.undo(conn, res["batch_id"])
    assert (u["restored"], u["skipped"], u["already"]) == (1, [], False)
    again = one(conn, "SELECT * FROM relations")
    assert again["id"] == rel["id"] and again["removed_at"] is None and again["removed_batch_id"] is None
    assert batches.undo(conn, res["batch_id"])["already"] is True


def test_undo_restore_is_skipped_when_it_would_loop_or_duplicate(conn):
    for i in (1, 2, 3, 4):
        mk(conn, i)
    link(conn, 1, 2, "waits_on")
    b2 = _unlink(conn, 1, 2, "waits_on")
    link(conn, 2, 1, "waits_on")
    u = batches.undo(conn, b2["batch_id"])
    assert u["restored"] == 0 and "not restored" in u["skipped"][0]["why"] and "loop" in u["skipped"][0]["why"]
    assert active_rels(conn) == {(2, 1, "waits_on")}

    link(conn, 3, 4, "part_of")
    b5 = _unlink(conn, 3, 4, "part_of")
    link(conn, 3, 4, "part_of")
    u = batches.undo(conn, b5["batch_id"])
    assert u["restored"] == 0 and "an identical link exists" in u["skipped"][0]["why"]
    assert active_rels(conn) == {(2, 1, "waits_on"), (3, 4, "part_of")}


def test_undo_skips_an_entry_it_does_not_recognise_and_reports_it(conn):
    conn.execute("INSERT INTO batches (created_at, applied_at, source, undo_json) VALUES ('now', 'now', 'ui', ?)", (json.dumps([{"nonsense": 1}]),))
    conn.commit()
    bid = one(conn, "SELECT MAX(id) AS id FROM batches")["id"]
    u = batches.undo(conn, bid)
    assert u["restored"] == 0 and u["skipped"] == [{"task_id": None, "why": "an undo entry Factotum does not recognise was left alone"}]
    assert one(conn, "SELECT undone_at FROM batches WHERE id = ?", (bid,))["undone_at"]


def test_undo_route_answers_404_only_for_a_missing_batch_and_never_leaves_a_transaction_open(cfg, tmp_path):
    path = tmp_path / "t.db"
    app = api.create_app(cfg, db_path=str(path))
    client = TestClient(app, raise_server_exceptions=False)
    assert client.post("/api/batches/999/undo", json={}).status_code == 404
    c = app.state.conn
    mk(c, 1)
    c.execute("INSERT INTO batches (created_at, applied_at, source, undo_json) VALUES ('now', 'now', 'ui', ?)",
              (json.dumps([{"task_id": 1, "field": "no_such_column", "old": 1}, {"task_id": 1, "field": "name", "old": "Before"}]),))   # newest first: the name is written, then the bad entry fails
    c.commit()
    bid = one(c, "SELECT MAX(id) AS id FROM batches")["id"]
    assert client.post(f"/api/batches/{bid}/undo", json={}).status_code == 500
    assert app.state.conn.in_transaction is False and one(c, "SELECT name FROM tasks WHERE id = 1")["name"] == "Task 1"
    other = connect(path)
    other.execute("BEGIN IMMEDIATE")
    other.rollback()
    other.close()


# --- Task 3: undo checks, expect, add_note, note, cause

def test_undo_skips_a_field_changed_after_the_batch_and_says_so(conn):
    mk(conn, 1)
    a = batches.apply(conn, [{"task_id": 1, "op": "set", "fields": {"due": "2026-11-05", "effort": "S"}}], "ui", TODAY)
    batches.apply(conn, [{"task_id": 1, "op": "set", "fields": {"due": "2026-11-09"}}], "ui", TODAY)
    res = batches.undo(conn, a["batch_id"])
    t = one(conn, "SELECT due, effort FROM tasks WHERE id = 1")
    assert t["due"] == "2026-11-09" and t["effort"] is None                       # the later edit stays; the untouched field is undone
    assert res["restored"] == 1 and res["skipped"] == [{"task_id": 1, "why": "Task-1 due was changed after this batch; left as it is"}]


def test_legacy_undo_entries_without_new_are_restored_as_before(conn):
    mk(conn, 1, due="2026-11-05")
    cur = conn.execute("INSERT INTO batches (created_at, applied_at, source, undo_json) VALUES ('t', 't', 'ui', ?)",
                       (json.dumps([{"task_id": 1, "field": "due", "old": None}]),))
    conn.commit()
    res = batches.undo(conn, cur.lastrowid)
    assert one(conn, "SELECT due FROM tasks WHERE id = 1")["due"] is None and res["restored"] == 1 and res["skipped"] == []


def test_undo_of_a_create_reports_a_task_closed_since(conn):
    a = batches.apply(conn, [{"op": "create", "fields": {"name": "New thing"}}], "connector", TODAY)
    tid = a["created"][0]
    batches.apply(conn, [{"op": "done", "task_id": tid}], "ui", TODAY)
    res = batches.undo(conn, a["batch_id"])
    assert one(conn, "SELECT status FROM tasks WHERE id = ?", (tid,))["status"] == "done"
    assert res["skipped"] == [{"task_id": tid, "why": f"Task-{tid} was closed after it was created; left as it is"}]


def test_a_staged_text_edit_is_skipped_when_the_text_changed_since(conn):
    mk(conn, 1, context="B")
    r = batches.apply(conn, [{"task_id": 1, "op": "set", "fields": {"context": "A + mine"}, "expect": {"context": "A"}}], "ui", TODAY)
    assert r["applied"] == 0 and r["skipped"] == [{"task_id": 1, "why": "Task-1 context changed since this edit was staged; not applied"}]
    assert one(conn, "SELECT context FROM tasks WHERE id = 1")["context"] == "B"
    mk(conn, 2, context="A")
    r = batches.apply(conn, [{"task_id": 2, "op": "set", "fields": {"context": "A + mine"}, "expect": {"context": "A  "}}], "ui", TODAY)
    assert r["applied"] == 1 and one(conn, "SELECT context FROM tasks WHERE id = 2")["context"] == "A + mine"


def test_add_note_appends_a_dated_line_at_apply_time(conn):
    mk(conn, 1, context="Old")
    r = batches.apply(conn, [{"task_id": 1, "op": "add_note", "fields": {"text": "Lab replied"}}], "chat", TODAY)
    assert one(conn, "SELECT context FROM tasks WHERE id = 1")["context"] == f"Old\n{TODAY.isoformat()}: Lab replied"
    ch = one(conn, "SELECT old, new FROM changes WHERE batch_id = ? AND field = 'context'", (r["batch_id"],))
    assert json.loads(ch["old"]) == "Old"
    batches.undo(conn, r["batch_id"])
    assert one(conn, "SELECT context FROM tasks WHERE id = 1")["context"] == "Old"
    r = batches.apply(conn, [{"task_id": 1, "op": "add_note", "fields": {"text": "  "}}], "chat", TODAY)
    assert r["applied"] == 0 and r["skipped"][0]["why"] == "a note needs fields.text"
    mk(conn, 2)
    batches.apply(conn, [{"task_id": 2, "op": "add_note", "fields": {"text": "First"}}], "chat", TODAY)
    assert one(conn, "SELECT context FROM tasks WHERE id = 2")["context"] == f"{TODAY.isoformat()}: First"


def test_a_change_note_lands_on_every_row_it_writes(conn):
    mk(conn, 1); mk(conn, 2)
    r = batches.apply(conn, [{"task_id": 1, "op": "done", "note": "finished in the meeting"}, {"task_id": 2, "op": "done"}], "ui", TODAY)
    n1 = [x["note"] for x in conn.execute("SELECT note FROM changes WHERE batch_id = ? AND task_id = 1", (r["batch_id"],))]
    n2 = [x["note"] for x in conn.execute("SELECT note FROM changes WHERE batch_id = ? AND task_id = 2", (r["batch_id"],))]
    assert len(n1) >= 3 and set(n1) == {"finished in the meeting"}
    assert n2 and set(n2) == {None}


def test_cause_is_stored_on_the_batch(conn):
    mk(conn, 1)
    cause = {"kind": "ripple", "from": [5], "run": 3}
    a = batches.apply(conn, [{"task_id": 1, "op": "set", "fields": {"effort": "S"}}], "ripple", TODAY, cause=cause)
    b = batches.apply(conn, [{"task_id": 1, "op": "set", "fields": {"effort": "M"}}], "ui", TODAY)
    assert json.loads(one(conn, "SELECT cause_json FROM batches WHERE id = ?", (a["batch_id"],))["cause_json"]) == cause
    assert one(conn, "SELECT cause_json FROM batches WHERE id = ?", (b["batch_id"],))["cause_json"] is None
