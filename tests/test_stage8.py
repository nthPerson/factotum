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


# --- Task 2: the count in pushes that are sent anyway

# NOTICE, a subscribed device and the fake push service are stage 2's (imported at the top); these two
# wrappers only fit their returns to this file: (ua, auth), and a service that answers 201 to everything.
def _device(conn, endpoint="https://push.invalid/d1"):
    return _subscribe_device(conn, endpoint)[1:]

def _service():
    return _push_service({})

def _sent(got, ua, auth, i=-1):
    return json.loads(W.decrypt(got[i][1].content, ua, auth))

def test_the_badge_is_a_top_level_integer_and_only_a_good_one():
    plain = json.loads(AP.payload(NOTICE, "https://f.example"))
    p = json.loads(AP.payload({**NOTICE, "badge": 7}, "https://f.example"))
    assert p["app_badge"] == 7 and p["notification"] == plain["notification"] and "app_badge" not in plain
    assert json.loads(AP.payload({**NOTICE, "badge": 0}, "https://f.example"))["app_badge"] == 0
    for bad in (True, -1, "3", 2.0, None):
        assert "app_badge" not in json.loads(AP.payload({**NOTICE, "badge": bad}, "https://f.example")), bad
    raw = AP.payload({**NOTICE, "badge": 12, "title": "T" * 5000, "body": "é" * 5000}, "https://f.example")
    assert len(raw) <= W.MAX_PLAINTEXT and json.loads(raw)["app_badge"] == 12

def test_send_all_carries_the_count_unless_switched_off(conn, cfg):
    AP.keys(conn, create=True); ua, auth = _device(conn)
    mk(conn, 1); mk(conn, 2); conn.commit()
    got, t = _service()
    AP.send_all(conn, cfg, NOTICE, transport=t)
    assert _sent(got, ua, auth)["app_badge"] == review_queue.count(conn, cfg, date.today()) == 2
    off = copy.deepcopy(cfg); off["notify"].setdefault("app", {})["badge"] = False
    AP.send_all(conn, off, NOTICE, transport=t)
    assert "app_badge" not in _sent(got, ua, auth)

def test_the_test_push_carries_the_count_too(conn, cfg):
    AP.keys(conn, create=True); ua, auth = _device(conn, "https://push.invalid/me")
    mk(conn, 1); conn.commit()
    got, t = _service()
    assert AP.send_test(conn, cfg, "https://push.invalid/me", transport=t)["ok"]
    assert _sent(got, ua, auth, 0)["app_badge"] == 1

def test_a_count_that_fails_never_stops_a_push(conn, cfg, monkeypatch):
    AP.keys(conn, create=True); ua, auth = _device(conn)
    monkeypatch.setattr(review_queue, "count", lambda *a, **k: 1 / 0)
    got, t = _service()
    assert AP.send_all(conn, cfg, NOTICE, transport=t)["sent"] == 1
    assert "app_badge" not in _sent(got, ua, auth, 0)

def test_the_count_rides_only_on_pushes_that_are_sent_anyway(conn, cfg):
    ecfg = copy.deepcopy(cfg); ecfg["notify"]["provider"] = "app"
    AP.keys(conn, create=True); ua, auth = _device(conn)
    mk(conn, 1); mk(conn, 2); conn.commit()
    got, t = _service()
    sch = Scheduler(ecfg, conn, push=lambda *a, **k: None, device_push=lambda c, cf, n: AP.send_all(c, cf, n, transport=t))
    sch.tick(datetime(2026, 11, 1, 10, 0))
    assert got == []                       # two items wait on Review and nothing is due: no push
    sch.tick(datetime(2026, 11, 1, 19, 31))
    assert len(got) == 1                   # the evening nudge, which is sent anyway, carries the count
    body = _sent(got, ua, auth)
    assert body["notification"]["tag"] == "review" and body["app_badge"] == review_queue.count(conn, ecfg, date.today())

def test_the_worker_sets_the_badge_and_still_has_no_fetch_handler():
    js = (api.WEB / "sw.js").read_text()
    assert "function badgeOf(" in js and "function setBadge(" in js and "app_badge" in js
    assert "addEventListener('fetch'" not in js and 'addEventListener("fetch"' not in js and "onfetch" not in js
