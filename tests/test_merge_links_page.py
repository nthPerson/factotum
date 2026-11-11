"""The merge of `suggestions` into `linked-tasks`: the one page after a reminder's Done carries both
stage 4b's "Free now" list and stage 5's outcome question, Free now first."""
from datetime import datetime

from fastapi.testclient import TestClient

from factotum import api, batches, reminders
from stage4_helpers import TODAY, link, mk


def _done(cfg, tmp_path, setup):
    from test_core import _rem
    app = api.create_app(cfg, db_path=str(tmp_path / "m.db"))
    c = app.state.conn
    setup(c)
    batches.apply(c, [{"task_id": 1, "op": "add_reminder", "fields": {"fire_at": "2026-11-03T09:00"}}], "ui", TODAY)
    reminders.fire(c, cfg, datetime(2026, 11, 3, 9, 0, 30), lambda *a, **k: "m1")
    tok = [r["token"] for r in _rem(c, 1) if r["status"] == "sent"][0]
    return TestClient(app).get(f"/r/{tok}", params={"a": "done"}), tok


def test_a_done_that_frees_a_task_shows_free_now_then_the_outcome_question(cfg, tmp_path):
    def setup(c):
        mk(c, 1, name="Ask the chair"); mk(c, 2, name="Book the room")
        link(c, 2, 1, "waits_on")
    r, tok = _done(cfg, tmp_path, setup)
    assert r.status_code == 200
    t = r.text
    assert "Free now:" in t and '/t/2"' in t and "Book the room" in t
    assert "How did it turn out?" in t and f'/r/{tok}/outcome"' in t and "One task hangs on this one." in t
    assert t.index("Free now:") < t.index("How did it turn out?") < t.index("Open Task-1")


def test_a_done_nothing_hangs_on_shows_neither(cfg, tmp_path):
    r, _ = _done(cfg, tmp_path, lambda c: mk(c, 1, name="Alone"))
    assert r.status_code == 200 and "Open Task-1" in r.text
    assert "Free now" not in r.text and "How did it turn out?" not in r.text


def test_a_neighbour_that_does_not_wait_is_asked_about_but_not_freed(cfg, tmp_path):
    def setup(c):
        mk(c, 1, name="Draft chapter one"); mk(c, 2, name="Thesis")
        link(c, 1, 2, "part_of")                      # Task-2 is the goal; nothing waits on Task-1
    r, _ = _done(cfg, tmp_path, setup)
    assert "How did it turn out?" in r.text and "Free now" not in r.text
