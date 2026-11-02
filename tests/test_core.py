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
    t = task(due=None, last_mention_at="2026-10-05", last_offered_at="2026-10-25")   # shown in Review while fading
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
    t = task(due="2026-10-20", hard=1, last_offered_at="2026-10-28")  # 12 days past, last call at Oct 27, shown since
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


def test_later_brings_the_item_back_to_review_on_the_snooze_date(cfg):
    # Later on Monday → Friday: hidden until Friday, then back in Review as the first thing after
    # last call, and suppressed again once decided on
    t = task(id=1, due=None, snoozed_until="2026-11-06", last_reviewed_at="2026-11-02", last_mention_at="2026-10-30")
    assert lifecycle.review_reasons(t, date(2026, 11, 5), cfg, None) == []
    assert lifecycle.review_reasons(t, date(2026, 11, 6), cfg, None) == ["snooze_over"]
    assert lifecycle.review_reasons(dict(t, last_reviewed_at="2026-11-06"), date(2026, 11, 7), cfg, None) == []
    assert lifecycle.REVIEW_ORDER.index("snooze_over") < lifecycle.REVIEW_ORDER.index("new")


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


def test_apply_skips_a_change_without_a_task_id_and_leaves_no_open_transaction(conn):
    # a tray saved by an older UI build has no task_id on its entries; that must not 500
    upsert_task(conn, task(id=1)); conn.commit()
    res = batches.apply(conn, [{"op": "done"}, {"task_id": "x", "op": "done"}, {"task_id": 1, "op": "done"}], "ui", TODAY)
    assert res["applied"] == 1 and len(res["skipped"]) == 2 and not conn.in_transaction
    assert conn.execute("SELECT status FROM tasks WHERE id = 1").fetchone()[0] == "done"


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


def test_task_list_carries_a_reminders_repeat(conn, cfg, tmp_path):
    from fastapi.testclient import TestClient
    from factotum.api import create_app
    app = create_app(cfg, db_path=str(tmp_path / "api.db"))
    upsert_task(app.state.conn, task(id=1)); app.state.conn.commit()
    client = TestClient(app)
    client.post("/api/batches/apply", json={"source": "ui", "changes": [{"task_id": 1, "op": "add_reminder", "fields": {"fire_at": "2026-11-04T09:30", "repeat": "weekly"}}]})
    t = next(t for t in client.get("/api/tasks").json()["tasks"] if t["id"] == 1)
    assert t["reminders"][0]["repeat"] == "weekly"


def test_static_assets_are_never_served_stale(cfg, tmp_path):
    # Cloudflare caches .js/.css by default and browsers apply heuristic freshness when the origin
    # is silent; the page must stamp its assets and the assets must say no-cache
    from fastapi.testclient import TestClient
    from factotum.api import create_app
    client = TestClient(create_app(cfg, db_path=str(tmp_path / "api.db")))
    page = client.get("/")
    assert "no-cache" in page.headers["cache-control"] and "/static/app.js?v=" in page.text and "/static/app.css?v=" in page.text
    asset = client.get("/static/app.js")
    assert asset.status_code == 200 and "no-cache" in asset.headers["cache-control"]


def test_log_lists_batches_with_their_changes_and_runs(cfg, tmp_path):
    from fastapi.testclient import TestClient
    from factotum.api import create_app
    app = create_app(cfg, db_path=str(tmp_path / "api.db"))
    c = app.state.conn
    upsert_task(c, task(id=1, name="Call Reza"))
    c.execute("INSERT INTO runs (kind, started_at, duration_ms, cost_usd, ok, summary) VALUES ('sync-file', '2026-11-01T06:30:00+00:00', 42000, 0.37, 1, 'filed one note')")
    c.commit()
    client = TestClient(app)
    client.post("/api/batches/apply", json={"source": "review", "changes": [{"task_id": 1, "op": "set", "fields": {"due": "2026-11-05"}}]})
    log = client.get("/api/log").json()
    b = log["batches"][0]
    assert b["source"] == "review" and b["undone_at"] is None
    assert b["changes"][0] == {"task_id": 1, "task_name": "Call Reza", "field": "due", "old": None, "new": "2026-11-05"}
    assert log["runs"][0]["kind"] == "sync-file" and log["runs"][0]["cost_usd"] == 0.37


# --- phase 2: what the automations may expire ------------------------------------------------

def test_expire_waits_until_review_has_shown_the_item(cfg):
    # fade_at Oct 26, warning from Oct 23. Never shown in Review → the automation waits; shown
    # during the warning window → fades; shown only before the window → still waits.
    t = task(due=None, last_mention_at="2026-10-05")
    assert lifecycle.expire([t], TODAY, cfg) == []
    assert lifecycle.expire([dict(t, last_offered_at="2026-10-24")], TODAY, cfg)[0][1] == "faded"
    assert lifecycle.expire([dict(t, last_offered_at="2026-10-10")], TODAY, cfg) == []
    hard = task(due="2026-10-20", hard=1)   # last call Oct 27
    assert lifecycle.expire([hard], TODAY, cfg) == []
    assert lifecycle.expire([dict(hard, last_offered_at="2026-10-27")], TODAY, cfg)[0][1] == "lapsed"


def test_a_review_decision_resets_the_quiet_clock(cfg):
    # Keep on a fading item means "I still want this": it comes back as Fading in fade_days, not
    # never (which is what a Keep that only suppressed the warning would have done)
    t = task(due=None, last_mention_at="2026-10-05", last_reviewed_at="2026-10-28")
    assert lifecycle.fade_at(t, cfg) == date(2026, 11, 18)
    assert lifecycle.review_reasons(t, TODAY, cfg, None) == []


def test_review_page_stamps_the_items_it_shows(cfg, tmp_path):
    from fastapi.testclient import TestClient
    from factotum.api import create_app
    app = create_app(cfg, db_path=str(tmp_path / "api.db"))
    c = app.state.conn
    for i in range(1, 21):
        upsert_task(c, task(id=i, due=None, created_at="2026-10-30T00:00:00+00:00", last_mention_at="2026-10-30"))
    c.commit()
    shown = {i["task"]["id"] for i in TestClient(app).get("/api/review").json()["items"]}
    stamped = {r[0] for r in c.execute("SELECT id FROM tasks WHERE last_offered_at IS NOT NULL")}
    assert stamped == shown and len(stamped) == 15


def test_init_db_adds_phase2_columns_to_old_tables(tmp_path):
    c = connect(tmp_path / "old.db")
    c.executescript("CREATE TABLE tasks (id INTEGER PRIMARY KEY, name TEXT NOT NULL, project TEXT, status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL, edited_at TEXT NOT NULL);"
                    "CREATE TABLE reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL, fire_at TEXT NOT NULL, label TEXT, repeat TEXT, status TEXT NOT NULL DEFAULT 'pending', ntfy_seq TEXT, token TEXT, created_by TEXT, created_at TEXT NOT NULL);")
    init_db(c)
    assert "last_offered_at" in {r[1] for r in c.execute("PRAGMA table_info(tasks)")}
    assert "sent_at" in {r[1] for r in c.execute("PRAGMA table_info(reminders)")}


# --- phase 2: reminders fire through ntfy, actions come back through /r/<token> ----------------

from datetime import datetime  # noqa: E402


class FakePush:
    """Stands in for notify.publish: records every message, returns a fake ntfy id."""
    def __init__(self):
        self.sent = []

    def __call__(self, cfg, title, body, **kw):
        self.sent.append(dict(kw, title=title, body=body))
        return f"m{len(self.sent)}"


def test_next_occurrence_of_a_repeating_reminder():
    from factotum.reminders import next_occurrence
    assert next_occurrence("2026-11-03T09:00", "daily") == "2026-11-04T09:00"
    assert next_occurrence("2026-11-06T09:00", "weekdays") == "2026-11-09T09:00"   # Fri → Mon
    assert next_occurrence("2026-11-03T09:00", "weekly") == "2026-11-10T09:00"
    assert next_occurrence("2026-01-31T09:00", "monthly") == "2026-02-28T09:00"    # clamped to the month


def test_due_reminders_fire_once_and_a_repeat_schedules_the_next(conn, cfg):
    from factotum import reminders
    upsert_task(conn, task(id=1, name="Call Reza", due="2026-11-05")); conn.commit()
    batches.apply(conn, [{"task_id": 1, "op": "add_reminder", "fields": {"fire_at": "2026-11-03T09:00", "label": "before the meeting", "repeat": "weekly"}},
                         {"task_id": 1, "op": "add_reminder", "fields": {"fire_at": "2026-11-03T18:00"}}], "ui", TODAY)
    push = FakePush()
    fired = reminders.fire(conn, cfg, datetime(2026, 11, 3, 9, 0, 30), push)
    assert len(fired) == 1 and len(push.sent) == 1
    m = push.sent[0]
    assert m["title"] == "Call Reza" and "before the meeting" in m["body"] and "Nov 5" in m["body"]
    assert [a["label"] for a in m["actions"]] == ["Snooze 1 h", "Done", "Open"] and all("/r/" in a["url"] for a in m["actions"])
    rows_ = _rem(conn)
    sent = [r for r in rows_ if r["status"] == "sent"]
    assert len(sent) == 1 and sent[0]["sent_at"] and sent[0]["token"] and sent[0]["ntfy_seq"] == "m1"
    nxt = [r for r in rows_ if r["status"] == "pending" and r["repeat"] == "weekly"]
    assert len(nxt) == 1 and nxt[0]["fire_at"] == "2026-11-10T09:00" and nxt[0]["label"] == "before the meeting"
    assert reminders.fire(conn, cfg, datetime(2026, 11, 3, 9, 1, 0), push) == [] and len(push.sent) == 1


def test_reminder_action_links_are_single_use_and_expire(cfg, tmp_path):
    from fastapi.testclient import TestClient
    from factotum import reminders
    from factotum.api import create_app
    app = create_app(cfg, db_path=str(tmp_path / "api.db"))
    c = app.state.conn
    upsert_task(c, task(id=1, name="Call Reza")); upsert_task(c, task(id=2, name="Other")); c.commit()
    batches.apply(c, [{"task_id": 1, "op": "add_reminder", "fields": {"fire_at": "2026-11-03T09:00"}},
                      {"task_id": 2, "op": "add_reminder", "fields": {"fire_at": "2026-11-03T09:00"}}], "ui", TODAY)
    reminders.fire(c, cfg, datetime(2026, 11, 3, 9, 0, 30), FakePush())
    tok1, tok2 = [r["token"] for r in _rem(c, 1) + _rem(c, 2) if r["status"] == "sent"]
    client = TestClient(app)
    r = client.get(f"/r/{tok1}", params={"a": "done"})
    assert r.status_code == 200 and "Task-1" in r.text
    assert c.execute("SELECT status, closed_by FROM tasks WHERE id = 1").fetchone()[:] == ("done", "ntfy")
    assert client.get(f"/r/{tok1}", params={"a": "done"}).status_code == 410          # used
    assert client.get("/r/nope", params={"a": "done"}).status_code == 404
    r = client.get(f"/r/{tok2}", params={"a": "snooze"})
    assert r.status_code == 200
    again = [x for x in _rem(c, 2) if x["status"] == "pending"]
    from datetime import timedelta
    drift = abs(datetime.fromisoformat(again[0]["fire_at"]) - (datetime.now() + timedelta(hours=1)))
    assert len(again) == 1 and drift < timedelta(minutes=2) and again[0]["created_by"] == "ntfy"   # +1 h from the wall clock, not from the send time
    c.execute("UPDATE reminders SET sent_at = '2026-10-01T00:00:00+00:00', token = 'old' WHERE id = 1"); c.commit()
    assert client.get("/r/old", params={"a": "open"}).status_code == 410             # older than 24 h



# --- phase 2: the scheduler ------------------------------------------------------------------

def _sched(conn, cfg, push=None, mailer=None):
    from factotum.scheduler import Scheduler
    cfg["notify"]["ntfy"]["topic"] = "factotum-test"      # no topic → reminders wait instead of being marked sent
    return Scheduler(cfg, conn, push=push or FakePush(), mailer=mailer or (lambda *a, **k: None), calendar=None)


def test_review_push_fires_once_a_day_after_its_time_and_only_with_a_queue(conn, cfg):
    upsert_task(conn, task(id=1, due=None, created_at="2026-10-30T00:00:00+00:00", last_mention_at="2026-10-30")); conn.commit()
    push = FakePush()
    s = _sched(conn, cfg, push)       # example config: review_time 19:30
    s.tick(datetime(2026, 11, 1, 19, 29)); assert push.sent == []
    s.tick(datetime(2026, 11, 1, 19, 30)); assert len(push.sent) == 1 and "Review" in push.sent[0]["title"] and "1 item" in push.sent[0]["body"]
    s.tick(datetime(2026, 11, 1, 21, 0)); assert len(push.sent) == 1                      # once a day
    s.tick(datetime(2026, 11, 2, 19, 31)); assert len(push.sent) == 2                     # next day, late start still fires
    assert conn.execute("SELECT COUNT(*) FROM runs WHERE kind = 'review_push'").fetchone()[0] == 2
    from factotum.db import set_setting
    set_setting(conn, "state.last_review_at", "2026-11-03"); conn.commit()
    s.tick(datetime(2026, 11, 3, 19, 30)); assert len(push.sent) == 2                     # reviewed today → quiet
    batches.apply(conn, [{"task_id": 1, "op": "keep"}], "review", date(2026, 11, 4))
    s.tick(datetime(2026, 11, 4, 19, 30)); assert len(push.sent) == 2                     # empty queue → quiet


def test_scheduler_expires_as_an_automation_batch_and_fires_due_reminders(conn, cfg):
    upsert_task(conn, task(id=1, due=None, last_mention_at="2026-10-05", last_offered_at="2026-10-25"))
    upsert_task(conn, task(id=2, name="Call Reza")); conn.commit()
    batches.apply(conn, [{"task_id": 2, "op": "add_reminder", "fields": {"fire_at": "2026-11-01T09:00"}}], "ui", TODAY)
    push = FakePush()
    s = _sched(conn, cfg, push)
    s.tick(datetime(2026, 11, 1, 3, 29))
    assert conn.execute("SELECT status FROM tasks WHERE id = 1").fetchone()[0] == "open"
    s.tick(datetime(2026, 11, 1, 9, 0, 10))                                               # past 03:30 (expire) and 09:00 (reminder)
    assert conn.execute("SELECT status, closed_by FROM tasks WHERE id = 1").fetchone()[:] == ("faded", "automation")
    assert conn.execute("SELECT source FROM batches ORDER BY id DESC LIMIT 1").fetchone()[0] == "automation"
    assert [m["title"] for m in push.sent] == ["Call Reza"]
    kinds = {r[0] for r in conn.execute("SELECT kind FROM runs")}
    assert {"expire", "reminder"} <= kinds
    s.tick(datetime(2026, 11, 1, 9, 1))
    assert len(push.sent) == 1 and conn.execute("SELECT COUNT(*) FROM runs WHERE kind = 'expire'").fetchone()[0] == 1


# --- phase 2: the morning brief ---------------------------------------------------------------

def test_morning_brief_lists_the_sections_the_spec_names(conn, cfg):
    from factotum import brief
    upsert_task(conn, task(id=1, name="Finish the Caltrans memo", top_rank=1))
    upsert_task(conn, task(id=2, name="Submit GEOG lab", due="2026-11-01", hard=1))
    upsert_task(conn, task(id=3, name="Call Reza"))
    upsert_task(conn, task(id=4, name="COMPE project proposal", due="2026-11-05", hard=1))
    upsert_task(conn, task(id=5, name="Quiz 3", status="done", closed_by="canvas", completed_at="2026-10-31"))
    upsert_task(conn, task(id=6, name="Letter from Prof. Lee", legacy_status="Waiting", waiting_on="Prof. Lee"))
    upsert_task(conn, task(id=7, name="Old thing", due="2026-10-20", hard=1))
    conn.commit()
    batches.apply(conn, [{"task_id": 3, "op": "add_reminder", "fields": {"fire_at": "2026-11-01T14:00", "label": "before 3pm"}}], "ui", TODAY)
    conn.execute("UPDATE batches SET applied_at = '2026-10-31T20:00:00+00:00'")
    batches.apply(conn, [{"task_id": 5, "op": "set", "fields": {"name": "Quiz 3"}}], "ui", TODAY)   # no-op, keeps ids simple
    conn.execute("INSERT INTO changes (batch_id, task_id, field, old, new) VALUES (1, 5, 'status', '\"open\"', '\"done\"')"); conn.commit()
    b = brief.build(conn, cfg, TODAY, events=[{"summary": "Lab meeting", "start": "2026-11-01T10:00", "end": "2026-11-01T11:00", "all_day": False}])
    h = b["html"]
    for heading, name in [("Top", "Finish the Caltrans memo"), ("Due today", "Submit GEOG lab"), ("Reminders today", "Call Reza"),
                          ("Hard deadlines this week", "COMPE project proposal"), ("Closed since yesterday", "Quiz 3"),
                          ("Waiting on", "Prof. Lee"), ("Overdue", "Old thing"), ("Calendar", "Lab meeting")]:
        assert heading in h and name in h, heading
    assert "canvas" in h                       # who closed it
    assert b["subject"].startswith("Factotum") and "1 due today" in b["subject"]
    assert "Finish the Caltrans memo" in b["text"]


def test_brief_send_uses_the_configured_recipient_and_reports(conn, cfg):
    from factotum import brief
    upsert_task(conn, task(id=1, name="x", due="2026-11-01")); conn.commit()
    got = {}
    def mailer(cfg_, to, subject, text, html): got.update(to=to, subject=subject)
    summary = brief.send(conn, cfg, TODAY, mailer, calendar=None)
    assert got["to"] == C.get(cfg, "notify.morning_brief.to") and "1 due today" in got["subject"] and got["to"] in summary


def test_smtp_settings_fall_back_to_the_imap_account(monkeypatch):
    from factotum import brief
    for k in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("IMAP_USER", "me@gmail.com"); monkeypatch.setenv("IMAP_PASSWORD", "app-pw")
    s = brief.smtp_settings()
    assert s == {"host": "smtp.gmail.com", "port": 465, "user": "me@gmail.com", "password": "app-pw"}
    monkeypatch.setenv("SMTP_USER", "other@x.org"); monkeypatch.setenv("SMTP_PORT", "587")
    assert brief.smtp_settings()["user"] == "other@x.org" and brief.smtp_settings()["port"] == 587


# --- phase 2: the calendar ---------------------------------------------------------------------

def test_calendar_fetch_parses_google_events(cfg):
    import httpx
    from factotum import gcal
    items = {"items": [
        {"id": "ev1", "summary": "Lab meeting", "htmlLink": "https://cal/ev1", "start": {"dateTime": "2026-11-01T10:00:00-07:00"}, "end": {"dateTime": "2026-11-01T11:00:00-07:00"},
         "attendees": [{"email": "robertashe2.0@gmail.com", "self": True, "responseStatus": "accepted"}]},
        {"id": "ev2", "summary": "Conference", "start": {"date": "2026-11-03"}, "end": {"date": "2026-11-05"}},
    ]}
    seen = {}
    def handler(req):
        seen["url"] = str(req.url); seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json=items)
    evs = gcal.fetch_events(cfg, "tok", datetime(2026, 11, 1), datetime(2026, 11, 8), transport=httpx.MockTransport(handler))
    assert seen["auth"] == "Bearer tok" and "calendars/primary/events" in seen["url"] and "singleEvents=true" in seen["url"]
    assert evs[0] == {"id": "ev1", "summary": "Lab meeting", "start": "2026-11-01T10:00", "end": "2026-11-01T11:00", "all_day": False, "self_status": "accepted", "link": "https://cal/ev1"}
    assert evs[1]["all_day"] is True and evs[1]["start"] == "2026-11-03" and evs[1]["self_status"] is None


def test_calendar_sweep_closes_only_accepted_events_that_have_passed(conn, cfg):
    from factotum import gcal
    from factotum.db import add_link
    for i in (1, 2, 3, 4):
        upsert_task(conn, task(id=i, name=f"t{i}", due="2026-10-31", hard=1))
    add_link(conn, 1, "calendar", "ev1", "accepted, passed"); add_link(conn, 2, "calendar", "ev2", "not accepted")
    add_link(conn, 3, "calendar", "ev3", "future"); conn.commit()
    events = [{"id": "ev1", "summary": "a", "start": "2026-10-31T10:00", "end": "2026-10-31T11:00", "all_day": False, "self_status": "accepted", "link": None},
              {"id": "ev2", "summary": "b", "start": "2026-10-31T10:00", "end": "2026-10-31T11:00", "all_day": False, "self_status": None, "link": None},
              {"id": "ev3", "summary": "c", "start": "2026-11-09T10:00", "end": "2026-11-09T11:00", "all_day": False, "self_status": "accepted", "link": None}]
    summary = gcal.sweep(conn, cfg, events, datetime(2026, 11, 1, 8, 0))
    st = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT id, status, closed_by FROM tasks")}
    assert st[1] == ("done", "calendar") and st[2][0] == "open" and st[3][0] == "open" and st[4][0] == "open"
    assert "Task-1" in summary
    assert conn.execute("SELECT source FROM batches ORDER BY id DESC LIMIT 1").fetchone()[0] == "automation"
    assert gcal.sweep(conn, cfg, events, datetime(2026, 11, 1, 9, 0)) is None     # nothing new


def test_manual_consent_accepts_the_http_localhost_address_google_redirects_to():
    """oauthlib refuses `http://` in the pasted address; the local-server flow rewrites the scheme and
    the manual flow must too. A bare code (the user copied only `code=...`) is passed as the code."""
    from factotum import gcal
    pasted = "http://localhost:8765/?state=abc&code=4/0AUn6Hd8_x&scope=https://www.googleapis.com/auth/calendar.events"
    assert gcal.manual_token_kwargs(pasted) == {"authorization_response": "https://localhost:8765/?state=abc&code=4/0AUn6Hd8_x&scope=https://www.googleapis.com/auth/calendar.events"}
    assert gcal.manual_token_kwargs("  https://localhost:8765/?code=x  ") == {"authorization_response": "https://localhost:8765/?code=x"}
    assert gcal.manual_token_kwargs("4/0AUn6Hd8_x") == {"code": "4/0AUn6Hd8_x"}
    with pytest.raises(ValueError):
        gcal.manual_token_kwargs("")


def test_reminder_runs_are_committed_so_the_log_tab_sees_them(conn, cfg):
    """The service thread logs each reminder tick through its own connection; the Log tab reads
    through another. A run row that is only in the thread's transaction is invisible (and was)."""
    import sqlite3
    from factotum.db import set_setting
    upsert_task(conn, task(id=2, name="Call Reza"))
    for job in ("expire", "review_push", "morning_brief"):      # the daily jobs already ran: only the reminder branch runs
        set_setting(conn, f"state.last_run.{job}", "2026-11-01")
    conn.commit()
    batches.apply(conn, [{"task_id": 2, "op": "add_reminder", "fields": {"fire_at": "2026-11-01T09:00"}}], "ui", TODAY)
    _sched(conn, cfg, FakePush()).tick(datetime(2026, 11, 1, 9, 0, 10))
    other = sqlite3.connect(conn.execute("PRAGMA database_list").fetchone()[2])
    assert other.execute("SELECT ok FROM runs WHERE kind = 'reminder'").fetchall() == [(1,)]
    # the failure path too: ntfy down leaves the row pending and logs a failed run that others can see
    batches.apply(conn, [{"task_id": 2, "op": "add_reminder", "fields": {"fire_at": "2026-11-01T10:00"}}], "ui", TODAY)
    def boom(*a, **k): raise RuntimeError("ntfy down")
    _sched(conn, cfg, boom).tick(datetime(2026, 11, 1, 10, 0, 10))
    assert other.execute("SELECT ok FROM runs WHERE kind = 'reminder' ORDER BY id").fetchall() == [(1,), (0,)]
