import json
from datetime import date

import pytest

from factotum import batches, commands, config as C, lifecycle, scoring
from factotum.db import connect, init_db, upsert_task
from factotum.importers.notion_tasks import parse_page

TODAY = date(2026, 11, 1)


@pytest.fixture
def cfg():
    return C.load(C.ROOT / "config" / "factotum.example.yaml")


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    init_db(c)
    return c


def task(**kw):
    base = dict(id=1, name="x", project="COMPE 510", status="open", hard=0, mentions=1,
                created_at="2026-10-01T00:00:00+00:00", edited_at="2026-10-01T00:00:00+00:00", last_mention_at="2026-10-01")
    base.update(kw)
    return base


# --- scoring ---------------------------------------------------------------------------------

def test_hard_due_today_is_p0(cfg):
    s, pr, _ = scoring.score(task(due="2026-11-01", hard=1), TODAY, cfg)
    assert s == 100 and pr == "P0"  # 90 + 15 high project, clamped


def test_soft_overdue_decays_not_clamps(cfg):
    one_day = scoring.score(task(due="2026-10-31", hard=0, project="Car Sounds"), TODAY, cfg)[0]
    ten_days = scoring.score(task(due="2026-10-22", hard=0, project="Car Sounds"), TODAY, cfg)[0]
    assert one_day == 70 - 5 + 5          # start - 1 day decay + medium weight
    assert ten_days == max(25, 70 - 50) + 5
    assert ten_days < one_day


def test_undated_backlog_is_low(cfg):
    s, pr, _ = scoring.score(task(due=None, project="LARK"), TODAY, cfg)
    assert s == 20 and pr == "P3"


def test_manual_p3_cap_yields_to_imminent_hard_deadline(cfg):
    s, _, _ = scoring.score(task(due="2026-11-02", hard=1, manual_priority="P3"), TODAY, cfg)
    assert s > 40


# --- lifecycle -------------------------------------------------------------------------------

def test_backlog_fades_after_21_quiet_days(cfg):
    t = task(due=None, last_mention_at="2026-10-05")
    assert lifecycle.fade_at(t, cfg) == date(2026, 10, 26)
    assert lifecycle.expire([t], TODAY, cfg) == [(1, "faded", "no mention since 2026-10-05")]


def test_longterm_and_never_fade_projects_do_not_fade(cfg):
    assert lifecycle.fade_at(task(due=None, longterm=1, last_mention_at="2026-01-01"), cfg) is None
    assert lifecycle.fade_at(task(due=None, project="BDA M.S.", last_mention_at="2026-01-01"), cfg) is None


def test_top_never_expires(cfg):
    t = task(due="2026-09-30", hard=1, top_rank=1)
    assert lifecycle.last_call_at(t, cfg) is None
    assert lifecycle.expire([t], TODAY, cfg) == []


def test_hard_last_call_then_lapse(cfg):
    t = task(due="2026-10-20", hard=1)  # 12 days past, last call at Oct 27
    assert lifecycle.last_call_at(t, cfg) == date(2026, 10, 27)
    assert "last_call" in lifecycle.review_reasons(t, TODAY, cfg, None)
    assert lifecycle.expire([t], TODAY, cfg)[0][1] == "lapsed"
    kept = dict(t, last_reviewed_at="2026-10-30")
    assert lifecycle.expire([kept], TODAY, cfg) == []


def test_review_queue_order_and_cap(cfg):
    ts = [task(id=i, due=None, created_at="2026-10-31T00:00:00+00:00", last_mention_at="2026-10-31") for i in range(1, 30)]
    ts.append(task(id=99, due="2026-10-20", hard=1))
    q = lifecycle.build_review_queue(ts, TODAY, cfg, date(2026, 10, 30))
    assert q[0]["task"]["id"] == 99 and q[0]["primary"] == "last_call"
    assert len(q) == 15


def test_review_new_means_never_reviewed_not_since_last_session(cfg):
    # an item you did not get to in one session must still be there in the next one
    old_unreviewed = task(id=1, due=None, created_at="2026-10-01T00:00:00+00:00", last_mention_at="2026-10-30")
    reviewed = task(id=2, due=None, created_at="2026-10-01T00:00:00+00:00", last_mention_at="2026-10-30", last_reviewed_at="2026-10-31")
    q = lifecycle.build_review_queue([old_unreviewed, reviewed], TODAY, cfg, last_review_at=date(2026, 10, 31))
    assert [i["task"]["id"] for i in q] == [1] and q[0]["primary"] == "new"


def test_import_classifier_buckets(cfg):
    assert lifecycle.classify_import(task(due="2026-11-08", hard=1, source="Canvas"), TODAY, cfg) == ("canvas_future", "open")
    assert lifecycle.classify_import(task(due="2026-09-30", hard=1, mentions=1), TODAY, cfg) == ("hard_lapsed", "lapsed")
    assert lifecycle.classify_import(task(due="2026-09-30", hard=1, mentions=3), TODAY, cfg) == ("hard_last_call", "open")
    assert lifecycle.classify_import(task(due="2026-10-01", hard=0), TODAY, cfg) == ("soft_faded", "faded")
    assert lifecycle.classify_import(task(due=None, mentions=1, edited_at="2026-09-20T00:00:00+00:00"), TODAY, cfg) == ("undated_faded", "faded")
    assert lifecycle.classify_import(task(due=None, mentions=2, edited_at="2026-09-20T00:00:00+00:00"), TODAY, cfg) == ("undated_keep", "open")
    assert lifecycle.classify_import(task(due=None, legacy_status="Waiting"), TODAY, cfg) == ("waiting_keep", "open")


def test_import_last_call_ignores_sync_created_calendar_events(cfg):
    # plaud-sync put every dated extraction on the calendar, so for imported rows an event is only a
    # user signal when the task itself came from the calendar; everything else rides on mentions
    past = dict(due="2026-09-30", hard=1, mentions=1, calendar_event="https://calendar.google.com/x")
    assert lifecycle.classify_import(task(source="Plaud", **past), TODAY, cfg) == ("hard_lapsed", "lapsed")
    assert lifecycle.classify_import(task(source="Calendar", **past), TODAY, cfg) == ("hard_last_call", "open")


def test_import_single_mention_quiet_clock_starts_at_creation(cfg):
    # the old planner rewrote Why now / Score on every open row, so Notion's last_edited_time is not
    # a human signal; a row mentioned once was mentioned when it was created
    row = dict(due=None, mentions=1, created_at="2026-09-20T00:00:00+00:00", edited_at="2026-10-30T00:00:00+00:00")
    assert lifecycle.classify_import(task(**row), TODAY, cfg) == ("undated_faded", "faded")
    assert lifecycle.classify_import(task(**dict(row, created_at="2026-10-20T00:00:00+00:00")), TODAY, cfg) == ("undated_keep", "open")


def _mention_page(mentions, created="2026-09-20T00:00:00.000Z", edited="2026-10-30T00:00:00.000Z"):
    return {"id": "p", "created_time": created, "last_edited_time": edited, "properties": {
        "Name": {"title": [{"plain_text": "x"}]}, "Task ID": {"unique_id": {"prefix": "Task", "number": 9}},
        "Status": {"select": {"name": "Next"}}, "Mentions": {"number": mentions}}}


def test_parse_page_single_mention_anchors_fade_clock_at_creation():
    assert parse_page(_mention_page(1))["last_mention_at"] == "2026-09-20T00:00:00.000Z"
    assert parse_page(_mention_page(2))["last_mention_at"] == "2026-10-30T00:00:00.000Z"


# --- commands --------------------------------------------------------------------------------

def test_command_grammar():
    assert commands.parse("done 389 392", TODAY) == [
        {"task_id": 389, "op": "done", "fields": {}}, {"task_id": 392, "op": "done", "fields": {}}]
    assert commands.parse("push Task-364 fri", TODAY)[0]["fields"]["due"] == "2026-11-06"
    assert commands.parse("defer 388 to next week", TODAY)[0]["fields"]["due"] == "2026-11-02"
    assert commands.parse("snooze #23", TODAY)[0]["fields"]["snoozed_until"] == "2026-11-02"
    assert commands.parse("top 23", TODAY)[0]["op"] == "pin_top"
    assert commands.parse("any new email about these?", TODAY) is None
    assert commands.parse("done the thing", TODAY) is None


# --- batches ---------------------------------------------------------------------------------

def test_apply_and_undo(conn, cfg):
    tid = upsert_task(conn, task(id=None, source_key="plaud:x:1"))
    res = batches.apply(conn, [{"task_id": tid, "op": "done"}, {"task_id": tid, "op": "set", "fields": {"due": "2026-11-05"}}], "ui", TODAY)
    assert res["applied"] == 2
    row = conn.execute("SELECT status, due, completed_at FROM tasks WHERE id = ?", (tid,)).fetchone()
    assert row["status"] == "done" and row["due"] == "2026-11-05" and row["completed_at"] == "2026-11-01"
    batches.undo(conn, res["batch_id"])
    row = conn.execute("SELECT status, due FROM tasks WHERE id = ?", (tid,)).fetchone()
    assert row["status"] == "open" and row["due"] is None


def test_apply_rejects_unknown_field(conn):
    tid = upsert_task(conn, task(id=None))
    res = batches.apply(conn, [{"task_id": tid, "op": "set", "fields": {"score": 99}}], "ui", TODAY)
    assert res["applied"] == 0 and res["skipped"][0]["why"].startswith("field score")


# --- notion import ---------------------------------------------------------------------------

def _page(task_id, name, status="Next", created="2026-10-30T00:00:00.000Z"):
    return {"id": f"page-{task_id}", "created_time": created, "last_edited_time": created, "properties": {
        "Name": {"title": [{"plain_text": name}]}, "Task ID": {"unique_id": {"prefix": "Task", "number": task_id}},
        "Status": {"select": {"name": status}}, "Project": {"select": {"name": "LARK"}}, "Mentions": {"number": 1},
        "Source Key": {"rich_text": [{"plain_text": f"plaud:x:{task_id}"}]}}}


def test_new_only_sync_never_overwrites_existing_rows(conn, cfg, tmp_path):
    from factotum.importers.notion_tasks import run
    upsert_task(conn, task(id=2, name="existing, kept by Robert", source_key="plaud:x:2", last_reviewed_at="2026-10-31"))
    conn.commit()
    dump = tmp_path / "pages.json"
    dump.write_text(json.dumps([_page(2, "existing, renamed in Notion"), _page(5, "brand new from a recording")]))
    res = run(conn, cfg, apply=True, from_json=str(dump), new_only=True, today=TODAY, out=lambda *_: None)
    assert res["total"] == 1 and res["skipped_existing"] == 1
    rows = {r["id"]: r for r in map(dict, conn.execute("SELECT id, name, status, last_reviewed_at FROM tasks"))}
    assert rows[2]["name"] == "existing, kept by Robert" and rows[2]["last_reviewed_at"] == "2026-10-31"
    assert rows[5]["status"] == "open" and rows[5]["last_reviewed_at"] is None  # surfaces as New in Review


def test_new_only_sync_duplicate_of_a_stored_key_keeps_the_stored_row_as_owner(conn, cfg, tmp_path):
    from factotum.importers.notion_tasks import run
    upsert_task(conn, task(id=2, name="already here", source_key="gcal:abc"))
    conn.commit()
    f = tmp_path / "pages.json"
    f.write_text(json.dumps([_notion_row(5, "re-created by the old sync", "Next", "gcal:abc")]))
    run(conn, cfg, apply=True, from_json=str(f), new_only=True, today=TODAY, out=lambda *_: None)
    rows = {r["id"]: r for r in conn.execute("SELECT id, source_key FROM tasks")}
    assert rows[2]["source_key"] == "gcal:abc" and rows[5]["source_key"] is None
    assert conn.execute("SELECT ref FROM links WHERE task_id = 5 AND kind = 'task'").fetchone()[0] == "2"


# --- reminders ------------------------------------------------------------------------------

def _rem(conn, task_id=1):
    return [dict(r) for r in conn.execute("SELECT * FROM reminders WHERE task_id = ? ORDER BY id", (task_id,))]


def test_batch_add_reminder_and_undo_cancels_it(conn, cfg):
    upsert_task(conn, task(id=1)); conn.commit()
    res = batches.apply(conn, [{"task_id": 1, "op": "add_reminder", "fields": {"fire_at": "2026-11-03T09:00", "label": "Call Reza", "repeat": "weekly"}}], "review", TODAY)
    assert res["applied"] == 1 and not res["skipped"]
    r = _rem(conn)
    assert len(r) == 1 and r[0]["status"] == "pending" and r[0]["repeat"] == "weekly" and r[0]["created_by"] == "review"
    batches.undo(conn, res["batch_id"])
    assert _rem(conn)[0]["status"] == "cancelled"


def test_add_reminder_validates_fire_at_and_repeat(conn, cfg):
    upsert_task(conn, task(id=1)); conn.commit()
    res = batches.apply(conn, [
        {"task_id": 1, "op": "add_reminder", "fields": {"label": "no time"}},
        {"task_id": 1, "op": "add_reminder", "fields": {"fire_at": "2026-11-03T09:00", "repeat": "fortnightly"}},
    ], "ui", TODAY)
    assert res["applied"] == 0 and len(res["skipped"]) == 2 and not _rem(conn)


def test_reminder_rides_with_a_decision_on_the_same_task_even_a_drop(conn, cfg):
    upsert_task(conn, task(id=1)); conn.commit()
    res = batches.apply(conn, [
        {"task_id": 1, "op": "drop"},
        {"task_id": 1, "op": "add_reminder", "fields": {"fire_at": "2026-11-03T09:00", "label": "check anyway"}},
    ], "review", TODAY)
    assert res["applied"] == 2
    assert conn.execute("SELECT status FROM tasks WHERE id = 1").fetchone()[0] == "dropped"
    assert _rem(conn)[0]["status"] == "pending" and _rem(conn)[0]["repeat"] is None


def test_init_db_adds_repeat_column_to_an_old_reminders_table(tmp_path):
    c = connect(tmp_path / "old.db")
    c.executescript("CREATE TABLE reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL REFERENCES tasks(id), fire_at TEXT NOT NULL, label TEXT,"
                    " status TEXT NOT NULL DEFAULT 'pending', ntfy_seq TEXT, token TEXT, created_by TEXT, created_at TEXT NOT NULL);")  # pre-`repeat` shape
    init_db(c)
    assert "repeat" in {r[1] for r in c.execute("PRAGMA table_info(reminders)")}


# --- notion parsing --------------------------------------------------------------------------

def _notion_row(task_id, name, status, source_key, created="2026-09-20T00:00:00.000Z"):
    return {"id": f"page-{task_id}", "created_time": created, "last_edited_time": created, "properties": {
        "Name": {"title": [{"plain_text": name}]}, "Task ID": {"unique_id": {"prefix": "Task", "number": task_id}},
        "Status": {"select": {"name": status}}, "Source Key": {"rich_text": [{"plain_text": source_key}]},
        "Mentions": {"number": 1}}}


def test_import_duplicate_source_keys_keep_every_row_and_the_key_on_the_open_one(conn, cfg, tmp_path):
    # plaud-sync produced a few rows with the same Source Key; the store's UNIQUE constraint must not
    # lose either row, and the key stays on the row that is still open
    from factotum.importers.notion_tasks import run
    f = tmp_path / "pages.json"
    f.write_text(json.dumps([_notion_row(241, "Register", "Dropped", "gcal:abc"), _notion_row(30, "Register", "Next", "gcal:abc", created="2026-10-25T00:00:00.000Z")]))
    out = []
    run(conn, cfg, apply=True, from_json=str(f), today=TODAY, out=out.append)
    rows = {r["id"]: r for r in conn.execute("SELECT id, source_key FROM tasks")}
    assert set(rows) == {30, 241}
    assert rows[30]["source_key"] == "gcal:abc" and rows[241]["source_key"] is None
    assert any("duplicate source key" in line for line in out)


def test_parse_notion_page():
    page = {
        "id": "abc", "created_time": "2026-10-23T21:10:37.872Z", "last_edited_time": "2026-10-24T13:32:10.060Z",
        "properties": {
            "Name": {"title": [{"plain_text": "Send Jim a security overview"}]},
            "Task ID": {"unique_id": {"prefix": "Task", "number": 374}},
            "Status": {"select": {"name": "Next"}},
            "Project": {"select": {"name": "LARK"}},
            "Area": {"select": {"name": "Work"}},
            "Due": {"date": None},
            "Hard deadline": {"checkbox": False},
            "Effort": {"select": {"name": "S"}},
            "Source": {"select": {"name": "Plaud"}},
            "Source Key": {"rich_text": [{"plain_text": "plaud\\:of_bbc\\:2"}]},
            "Source Link": {"url": "https://web.plaud.ai/file/of_bbc"},
            "Mentions": {"number": 1},
            "Notes": {"relation": [{"id": "note-1"}]},
        },
    }
    t = parse_page(page)
    assert t["id"] == 374 and t["status"] == "open" and t["legacy_status"] == "Next"
    assert t["source_key"] == "plaud:of_bbc:2" and t["_notes"] == ["note-1"] and t["due"] is None


# --- api -------------------------------------------------------------------------------------

def test_review_endpoint_pages_and_excludes_staged_ids(conn, cfg, tmp_path):
    from fastapi.testclient import TestClient
    from factotum.api import create_app
    db = tmp_path / "api.db"
    app = create_app(cfg, db_path=str(db))
    c = app.state.conn
    for i in range(1, 21):
        upsert_task(c, task(id=i, due=None, created_at="2026-10-30T00:00:00+00:00", last_mention_at="2026-10-30"))
    c.commit()
    client = TestClient(app)
    page = client.get("/api/review").json()
    assert len(page["items"]) == 15 and page["total"] == 20
    page2 = client.get("/api/review", params={"exclude": "1,2,3"}).json()
    assert page2["total"] == 17 and not {1, 2, 3} & {i["task"]["id"] for i in page2["items"]}
