"""Stage 8, app extras: the Review count on the icon, the shortcuts."""
import copy
import json
from datetime import date, datetime

from fastapi.testclient import TestClient

from conftest import FIXTURE, TODAY, task
from test_stage1 import _png_size                                  # the helpers stages 1 and 2 already have, never copies
from test_stage2 import NOTICE, _device as _subscribe_device, _service as _push_service
from factotum import api, app_push as AP, config as C, review_queue, webpush as W
from factotum.api import create_app
from factotum.db import get_settings, get_task, now_iso, upsert_task
from factotum.scheduler import Scheduler


# --- Task 1: one Review count, and the switch

def mk(c, i, **kw):
    upsert_task(c, task(id=i, name=f"T{i}", **kw))

def _card(c, name):
    c.execute("INSERT INTO evidence (task_id, kind, ref, account, label, excerpt, at) VALUES (NULL, 'proposal', ?, NULL, ?, 'r', ?)",
              (json.dumps({"task_id": None, "op": "create", "fields": {"name": name}}), f"create: {name}", now_iso()))

def test_one_review_count_for_the_tab_the_icon_and_the_pushes(cfg, tmp_path):
    client = TestClient(create_app(cfg, db_path=str(tmp_path / "a.db")))
    c = client.app.state.conn
    for i in (1, 2, 3):
        mk(c, i)                                   # never decided on: "new" on Review
    mk(c, 4, snoozed_until="2099-01-01")           # snoozed: not on Review
    mk(c, 5, status="done")
    _card(c, "Order the reagent")
    c.commit()
    n = client.get("/api/review/count").json()["count"]          # asked first: it must not depend on Review having been loaded
    assert n == 4
    assert review_queue.count(c, C.effective(cfg, get_settings(c)), date.today()) == n
    rv = client.get("/api/review").json()
    assert rv["total"] + len(rv["suggestions"]) == n
    assert client.get("/api/review/count").json()["count"] == n
    assert client.get("/api/review/count?exclude=1,2").json()["count"] == 2
    assert api.SUGGESTION_KINDS is review_queue.SUGGESTION_KINDS

def test_the_count_writes_nothing(cfg, tmp_path):
    client = TestClient(create_app(cfg, db_path=str(tmp_path / "a.db")))
    c = client.app.state.conn
    mk(c, 1); c.commit()
    before = c.total_changes
    assert client.get("/api/review/count").status_code == 200
    assert c.total_changes == before and get_task(c, 1)["last_offered_at"] is None

def test_the_review_nudge_counts_what_review_shows(conn, cfg):
    # decided on today, so not "new"; only the mail sweep's flag puts it on Review
    mk(conn, 1, last_reviewed_at=TODAY.isoformat(), last_mention_at=TODAY.isoformat())
    conn.execute("INSERT INTO evidence (task_id, kind, label, at) VALUES (1, 'thread_updated', 'Re: data', ?)", (now_iso(),))
    conn.commit()
    titles = []
    sch = Scheduler(cfg, conn, push=lambda cfg, title, body, **k: titles.append(title))
    sch._review_push(cfg, TODAY, {})
    assert titles == ["Review: 1 item"]
    _card(conn, "Order the reagent"); conn.commit()          # a suggestion card is on Review, so it is in the nudge's number too
    sch._review_push(cfg, TODAY, {})
    assert titles[-1] == "Review: 2 items"

def test_the_badge_switch_is_a_settings_key_and_on_when_absent():
    assert "notify.app.badge" in C.SETTINGS_KEYS and C.DEFAULTS["notify.app.badge"] is True
    assert C.value({}, "notify.app.badge") is True and "notify.app.badge" not in C.PAID_SWITCHES
    for path in (C.EXAMPLE_CONFIG, FIXTURE, C.STARTER_CONFIG):
        assert C.get(C.load(path), "notify.app.badge", "absent") is True, path.name
