"""SQLite schema and helpers. The schema is SPEC.md §4, verbatim where it matters."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  project TEXT, area TEXT,
  status TEXT NOT NULL DEFAULT 'open',          -- open|done|dropped|faded|lapsed
  legacy_status TEXT,                            -- Inbox/Next/In progress/Waiting from the Notion import
  top_rank INTEGER, longterm INTEGER NOT NULL DEFAULT 0,
  due TEXT, due_is_datetime INTEGER NOT NULL DEFAULT 0, hard INTEGER NOT NULL DEFAULT 0,
  effort TEXT,
  source TEXT, source_key TEXT UNIQUE, source_link TEXT, account TEXT,
  counterpart TEXT, waiting_on TEXT, waiting_since TEXT,
  context TEXT, why_now TEXT, score REAL, priority TEXT, manual_priority TEXT,
  mentions INTEGER NOT NULL DEFAULT 1, last_mention_at TEXT,
  created_at TEXT NOT NULL, edited_at TEXT NOT NULL,
  completed_at TEXT, closed_by TEXT,
  snoozed_until TEXT, last_reviewed_at TEXT,
  last_offered_at TEXT,                          -- last day a Review page showed this row (the automations only expire what was shown)
  calendar_event TEXT, notion_page_id TEXT, canvas_id TEXT,
  mirrored_at TEXT                               -- when the Notion mirror last pushed this row (not an edit)
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project);

CREATE TABLE IF NOT EXISTS reminders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER NOT NULL REFERENCES tasks(id),
  fire_at TEXT NOT NULL, label TEXT,
  repeat TEXT,                                   -- NULL|daily|weekdays|weekly|monthly
  status TEXT NOT NULL DEFAULT 'pending',        -- pending|sent|snoozed|done|cancelled
  ntfy_seq TEXT, token TEXT, created_by TEXT, created_at TEXT NOT NULL,
  sent_at TEXT                                   -- when it was pushed; the action token is valid 24 h from here
);
CREATE TABLE IF NOT EXISTS links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER NOT NULL REFERENCES tasks(id),
  kind TEXT NOT NULL,                            -- plaud_note|notion_page|canvas|mail_thread|mail_message|calendar|task
  ref TEXT NOT NULL, label TEXT, account TEXT,
  created_at TEXT NOT NULL, last_seen_at TEXT,
  UNIQUE(task_id, kind, ref)
);
CREATE TABLE IF NOT EXISTS mentions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER NOT NULL REFERENCES tasks(id),
  source_kind TEXT NOT NULL, source_ref TEXT, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL, applied_at TEXT, undone_at TEXT,
  source TEXT NOT NULL,                          -- ui|command|chat|review|automation|import
  undo_json TEXT
);
CREATE TABLE IF NOT EXISTS changes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id INTEGER NOT NULL REFERENCES batches(id),
  task_id INTEGER NOT NULL, field TEXT NOT NULL, old TEXT, new TEXT
);
CREATE TABLE IF NOT EXISTS chat_threads (
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, claude_session_id TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  thread_id INTEGER NOT NULL REFERENCES chat_threads(id),
  role TEXT NOT NULL, text TEXT, attached_json TEXT, proposals_json TEXT, cost_usd REAL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence (              -- what the sweeps found and the planner proposes (SPEC §6, §8.2)
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER REFERENCES tasks(id),             -- NULL for a candidate not yet matched to a task
  kind TEXT NOT NULL,                               -- reply_from_waiting|sent_to_counterpart|thread_updated|ics|confirmation|proposal
  ref TEXT, account TEXT, label TEXT, excerpt TEXT, at TEXT NOT NULL,
  judged_at TEXT, verdict TEXT                      -- set by the planner (or Review) once acted on
);
CREATE TABLE IF NOT EXISTS trigger_seen (          -- mail uids the Plaud trigger has handled (never reprocessed)
  account TEXT NOT NULL, uid INTEGER NOT NULL, subject TEXT, at TEXT NOT NULL, ok INTEGER,
  PRIMARY KEY (account, uid)
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL, started_at TEXT NOT NULL, duration_ms INTEGER, cost_usd REAL,
  ok INTEGER, summary TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  address TEXT NOT NULL UNIQUE, display TEXT, enabled INTEGER NOT NULL DEFAULT 1, roles_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY, value_json TEXT NOT NULL
);
"""

TASK_FIELDS = [
    "id", "name", "project", "area", "status", "legacy_status", "top_rank", "longterm",
    "due", "due_is_datetime", "hard", "effort", "source", "source_key", "source_link", "account",
    "counterpart", "waiting_on", "waiting_since", "context", "why_now", "score", "priority",
    "manual_priority", "mentions", "last_mention_at", "created_at", "edited_at", "completed_at",
    "closed_by", "snoozed_until", "last_reviewed_at", "last_offered_at", "calendar_event", "notion_page_id", "canvas_id", "mirrored_at",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")   # the API and the scheduler each hold a connection
    return conn


# Columns added after a table first shipped. CREATE TABLE IF NOT EXISTS leaves an existing table
# alone, so init_db adds these to databases created before them.
MIGRATIONS = {"reminders": {"repeat": "TEXT", "sent_at": "TEXT"}, "tasks": {"last_offered_at": "TEXT", "mirrored_at": "TEXT"},
              "chat_messages": {"drafts_json": "TEXT", "rejected_json": "TEXT", "error": "TEXT", "duration_ms": "INTEGER"}}


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    for table, cols in MIGRATIONS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, decl in cols.items():
            if col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    conn.commit()


def rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> dict | None:
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r else None


def get_task(conn: sqlite3.Connection, task_id: int) -> dict | None:
    return one(conn, "SELECT * FROM tasks WHERE id = ?", (task_id,))


def upsert_task(conn: sqlite3.Connection, task: dict) -> int:
    """Insert or replace by id (when given) or by source_key. Returns the id."""
    t = {k: task.get(k) for k in TASK_FIELDS}
    t["edited_at"] = t.get("edited_at") or now_iso()
    t["created_at"] = t.get("created_at") or t["edited_at"]
    existing = None
    if t.get("id"):
        existing = get_task(conn, t["id"])
    elif t.get("source_key"):
        existing = one(conn, "SELECT * FROM tasks WHERE source_key = ?", (t["source_key"],))
    if existing:
        t["id"] = existing["id"]
        sets = ", ".join(f"{k} = ?" for k in TASK_FIELDS if k != "id")
        conn.execute(f"UPDATE tasks SET {sets} WHERE id = ?", tuple(t[k] for k in TASK_FIELDS if k != "id") + (t["id"],))
    else:
        cols = [k for k in TASK_FIELDS if t.get(k) is not None]
        conn.execute(f"INSERT INTO tasks ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", tuple(t[k] for k in cols))
        t["id"] = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return int(t["id"])


def add_link(conn: sqlite3.Connection, task_id: int, kind: str, ref: str, label: str | None = None, account: str | None = None) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO links (task_id, kind, ref, label, account, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (task_id, kind, ref, label, account, now_iso()),
    )


def add_mention(conn: sqlite3.Connection, task_id: int, source_kind: str, source_ref: str | None, at: str | None = None) -> None:
    at = at or now_iso()
    conn.execute("INSERT INTO mentions (task_id, source_kind, source_ref, at) VALUES (?, ?, ?, ?)", (task_id, source_kind, source_ref, at))
    conn.execute("UPDATE tasks SET mentions = mentions + 1, last_mention_at = MAX(COALESCE(last_mention_at, ''), ?) WHERE id = ?", (at, task_id))


def log_run(conn: sqlite3.Connection, kind: str, duration_ms: int, *, ok: bool, summary: str | None = None,
            error: str | None = None, cost_usd: float = 0.0, started_at: str | None = None) -> int:
    """One row in `runs`: Claude runs with their cost, and the automations (cost 0) so the Log tab
    shows what ran. The caller commits."""
    cur = conn.execute("INSERT INTO runs (kind, started_at, duration_ms, cost_usd, ok, summary, error) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (kind, started_at or now_iso(), duration_ms, cost_usd, 1 if ok else 0, summary, error))
    return int(cur.lastrowid)


def get_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    return {r["key"]: json.loads(r["value_json"]) for r in conn.execute("SELECT key, value_json FROM settings")}


def set_setting(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute("INSERT INTO settings (key, value_json) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json", (key, json.dumps(value)))


def seed_accounts(conn: sqlite3.Connection, seed: list[dict]) -> None:
    if conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]:
        return
    for a in seed or []:
        conn.execute("INSERT OR IGNORE INTO accounts (address, display, enabled, roles_json) VALUES (?, ?, 1, ?)",
                     (a["address"], a.get("display"), json.dumps(a.get("roles", []))))
    conn.commit()
