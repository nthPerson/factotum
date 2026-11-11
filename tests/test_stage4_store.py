import sqlite3

import pytest

from factotum import db
from factotum.db import Migration, init_db, migrate, schema_version
from stage4_helpers import mk


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
