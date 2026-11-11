"""Stage 4b, the page: the few server pieces it needs (suggestion direction, Free now after Done) and
a guard on the words it shows."""
import re

from fastapi.testclient import TestClient

from factotum import api, relations
from factotum.db import connect, init_db
from stage4_helpers import mk


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
