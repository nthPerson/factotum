"""Stage 4b, the page: the few server pieces it needs (suggestion direction, Free now after Done) and
a guard on the words it shows."""
import re
from datetime import datetime

from fastapi.testclient import TestClient

from factotum import api, batches, relations, reminders
from factotum.db import connect, init_db
from stage4_helpers import TODAY, link, mk


def _client(cfg, tmp_path):
    path = tmp_path / "t.db"
    conn = connect(path)
    init_db(conn)
    return TestClient(api.create_app(cfg, db_path=str(path))), conn


def test_a_suggestion_says_which_end_the_task_is(cfg, tmp_path):
    client, conn = _client(cfg, tmp_path)
    mk(conn, 1); mk(conn, 2)
    eid = relations.suggest(conn, "waits_on", 1, 2, "after", "text"); conn.commit()
    by = {t["id"]: t for t in client.get("/api/tasks").json()["tasks"]}
    assert by[1]["rel"]["suggestions"] == [{"evidence_id": eid, "kind": "waits_on", "other": 2, "keep": None, "why": "after", "signal": "text", "dir": "out"}]
    assert by[2]["rel"]["suggestions"][0]["other"] == 1 and by[2]["rel"]["suggestions"][0]["dir"] == "in"
    items = {i["task"]["id"]: i for i in client.get("/api/review").json()["items"]}
    assert items[2]["link_suggestions"][0]["dir"] == "in" and items[1]["link_suggestions"][0]["dir"] == "out"


def test_the_page_never_says_blocked_or_dependency():
    for f in ("app.js", "app.css", "index.html"):
        assert not re.search(r"\bblocked\b|\bdependenc", (api.WEB / f).read_text(encoding="utf-8"), re.I), f


def _done_page(cfg, tmp_path, setup):
    from test_core import _rem
    app = api.create_app(cfg, db_path=str(tmp_path / "a.db"))
    c = app.state.conn
    setup(c)
    batches.apply(c, [{"task_id": 1, "op": "add_reminder", "fields": {"fire_at": "2026-11-03T09:00"}}], "ui", TODAY)
    reminders.fire(c, cfg, datetime(2026, 11, 3, 9, 0, 30), lambda *a, **k: "m1")
    tok = [r["token"] for r in _rem(c, 1) if r["status"] == "sent"][0]
    return TestClient(app).get(f"/r/{tok}", params={"a": "done"})


def test_the_page_after_done_names_what_is_now_free(cfg, tmp_path):
    def setup(c):
        for i, name in ((1, "Get the data"), (2, "Run <the> analysis"), (3, "Write it up"), (4, "Other"), (5, "Closed one")):
            mk(c, i, name=name)
        link(c, 2, 1, "waits_on"); link(c, 3, 1, "waits_on"); link(c, 3, 4, "waits_on"); link(c, 5, 1, "waits_on")
        batches.apply(c, [{"op": "done", "task_id": 5}], "ui", TODAY)
    r = _done_page(cfg, tmp_path, setup)
    assert r.status_code == 200 and "Free now:" in r.text
    assert '/t/2"' in r.text and "Run &lt;the&gt; analysis" in r.text
    assert '/t/3"' not in r.text and '/t/5"' not in r.text and "Open Task-1" in r.text
    assert not re.search(r"\bblocked\b|\bdependenc", r.text, re.I)


def test_the_page_after_done_with_nothing_waiting_is_unchanged(cfg, tmp_path):
    r = _done_page(cfg, tmp_path, lambda c: mk(c, 1, name="Alone"))
    assert r.status_code == 200 and "Free now" not in r.text and "Open Task-1" in r.text


def test_freed_by_lists_only_open_tasks_with_nothing_else_open_to_wait_on(conn):
    for i in range(1, 6):
        mk(conn, i)
    link(conn, 2, 1, "waits_on"); link(conn, 3, 1, "waits_on"); link(conn, 3, 4, "waits_on"); link(conn, 5, 1, "waits_on")
    batches.apply(conn, [{"op": "done", "task_id": 5}], "ui", TODAY)
    batches.apply(conn, [{"op": "done", "task_id": 1}], "ui", TODAY)
    assert relations.freed_by(conn, 1) == [{"id": 2, "name": "Task 2"}]
