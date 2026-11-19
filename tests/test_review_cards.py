"""Spoken Review, stage 1: Review's queue and cards outside the API, and Review's day."""
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from stage4_helpers import mk
from test_stage8 import _card

from factotum import config as C, review_queue
from factotum.api import create_app
from factotum.db import get_settings, now_iso

TASK_KEYS = {"task", "reasons", "primary", "texts", "link_suggestions", "flags", "evidence"}


def _app(cfg, tmp_path, name="a.db"):
    client = TestClient(create_app(cfg, db_path=str(tmp_path / name)))
    return client, client.app.state.conn


def _queue_fixture(c):
    """20 new tasks, a task with a thread_updated row, a suggestion card. Returns the card's evidence id."""
    for i in range(1, 21):
        mk(c, i)
    mk(c, 21, name="Max wrote back")
    c.execute("INSERT INTO evidence (task_id, kind, ref, label, at) VALUES (21, 'thread_updated', 'conv-1', 'Max wrote', ?)", (now_iso(),))
    _card(c, "Order the reagent")
    c.commit()
    return c.execute("SELECT MAX(id) FROM evidence").fetchone()[0]


# --- Task 1: review_queue.items, review_queue.today ------------------------------------------------

def test_items_reads_only_and_holds_the_whole_queue(cfg, tmp_path):
    client, c = _app(cfg, tmp_path)
    eid = _queue_fixture(c)
    ecfg = C.effective(cfg, get_settings(c))
    before = c.total_changes
    q = review_queue.items(c, ecfg, date.today())
    assert c.total_changes == before and not c.in_transaction
    assert len(q["tasks"]) == 21                                      # no cap
    for i in q["tasks"]:
        assert i["key"] == f"t:{i['task']['id']}" and TASK_KEYS <= set(i)
    item = next(i for i in q["tasks"] if i["task"]["id"] == 21)
    assert [e["label"] for e in item["evidence"]] == ["Max wrote"] and item["flags"] == {"thread_updated": True}
    assert [x["key"] for x in q["cards"]] == [f"s:{eid}"] and q["cards"][0]["fields"]["name"] == "Order the reagent"


def test_get_review_is_items_with_its_cap_its_exclude_and_its_stamp(cfg, tmp_path):
    client, c = _app(cfg, tmp_path)
    eid = _queue_fixture(c)
    ecfg = C.effective(cfg, get_settings(c))
    want = [i["task"]["id"] for i in review_queue.items(c, ecfg, date.today())["tasks"] if i["task"]["id"] not in (1, 2)][:15]
    rv = client.get("/api/review?exclude=1,2").json()
    assert [i["task"]["id"] for i in rv["items"]] == want and rv["total"] == 19 and rv["cap"] == 15
    assert [s["id"] for s in rv["suggestions"]] == [eid]
    offered = {r[0] for r in c.execute("SELECT id FROM tasks WHERE last_offered_at IS NOT NULL")}
    assert offered == set(want)


def test_reviews_day_is_the_owners_not_the_machines(cfg, tmp_path):
    C.set_(cfg, "owner.timezone", "Pacific/Kiritimati")
    client, c = _app(cfg, tmp_path)
    want = datetime.now(ZoneInfo("Pacific/Kiritimati")).date()
    assert review_queue.today(cfg) == want
    assert client.get("/api/review").json()["today"] == want.isoformat()
    assert client.post("/api/review/finish", json={}).status_code == 200
    assert get_settings(c)["state.last_review_at"] == want.isoformat()

