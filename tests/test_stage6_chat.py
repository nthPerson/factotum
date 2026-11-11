"""Stage 6, the conversation in place: threads that belong to a place, proposals staged together, Split into steps."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from factotum import api, batches, chat
from factotum.claude.runner import RunResult
from factotum.db import one, rows
from conftest import TODAY
from stage4_helpers import active_rels, mk


def _sse(resp) -> list[dict]:
    return [json.loads(l[5:]) for l in resp.iter_lines() if l.startswith("data:")]


def _runner(data, seen=None, calls=None):
    def run(cfg, kind, prompt, **kw):
        if calls is not None:
            calls.append(dict(kw, prompt=prompt))
        if seen:
            (Path(kw["workdir"]) / "tasks_seen.jsonl").write_text(json.dumps({"ids": seen}) + "\n")
        return RunResult(True, data, json.dumps(data), 0.21, 2400, session_id="S1")
    return run


# --- Task 4: a conversation that belongs to a place

def test_a_card_and_an_opened_task_share_one_thread_and_today_has_its_own(conn):
    a = chat.new_thread(conn, None, "review", 5)
    assert chat.place_thread(conn, "review", 5) == a == chat.place_thread(conn, "task", 5)
    assert chat.place_thread(conn, "review", 6) is None
    chat.new_thread(conn, "from the Chat tab")                                        # no origin: never found in place
    assert chat.place_thread(conn, "task", 5) == a
    t = chat.new_thread(conn, None, "today")
    conn.execute("INSERT INTO chat_messages (thread_id, role, text, created_at) VALUES (?, 'user', 'hi', ?)", (t, datetime.now(timezone.utc).isoformat())); conn.commit()
    assert chat.place_thread(conn, "today", None) == t
    assert chat.place_thread(conn, "today", None, now=datetime.now(timezone.utc) + timedelta(hours=13)) is None
    assert [(r["origin"], r["task_id"]) for r in chat.threads(conn) if r["id"] == a] == [("review", 5)]


def test_a_turn_from_a_card_attaches_the_card_says_where_and_names_what_it_read(conn, cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(chat, "RUNS_DIR", tmp_path)
    mk(conn, 5, name="The goal"); mk(conn, 7, name="Read by a tool")
    calls = []
    tid = chat.new_thread(conn, None, "review", 5)
    res = chat.turn(conn, cfg, tid, "what is left?", [], today=TODAY, runner=_runner({"reply_md": "ok", "proposals": []}, seen=[7], calls=calls))
    assert "## Where the user asked" in calls[0]["prompt"] and "Review card of Task-5" in calls[0]["prompt"] and '"Task-5"' in calls[0]["prompt"]
    assert res["read"] == [5, 7] and res["named"]["7"] == {"id": 7, "name": "Read by a tool", "status": "open"}
    assert (res["origin"], res["task_id"]) == ("review", 5) and chat.messages(conn, tid)[-1]["read"] == [5, 7]


def test_the_turn_route_finds_the_place_and_refuses_a_second_turn_while_one_runs(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(chat, "RUNS_DIR", tmp_path / "runs")
    app = api.create_app(cfg, db_path=str(tmp_path / "t.db"), chat_runner=_runner({"reply_md": "ok", "proposals": []}))
    mk(app.state.conn, 5)
    c = TestClient(app)
    turn = lambda **b: c.post("/api/chat/turn", json=dict({"text": "hi", "attached": []}, **b))      # noqa: E731
    with c.stream("POST", "/api/chat/turn", json={"text": "hi", "attached": [], "origin": "review", "task_id": 5}) as r:
        first = _sse(r)[-1]["result"]
    with c.stream("POST", "/api/chat/turn", json={"text": "and?", "attached": [], "origin": "task", "task_id": 5}) as r:
        again = _sse(r)[-1]["result"]
    assert again["thread_id"] == first["thread_id"] and len(chat.messages(app.state.conn, first["thread_id"])) == 4
    app.state.chat_busy.add(first["thread_id"])
    r = turn(origin="review", task_id=5)
    assert r.status_code == 409 and r.json()["detail"] == "Claude is still answering in this conversation"
    assert turn(thread_id=first["thread_id"]).status_code == 409
    app.state.chat_busy.discard(first["thread_id"])
    assert turn(origin="elsewhere").status_code == 400 and turn(origin="review", task_id=999).status_code == 404
    with c.stream("POST", "/api/chat/turn", json={"text": "new", "attached": [], "origin": "review", "task_id": 5, "fresh": True}) as r:
        fresh = _sse(r)[-1]["result"]
    assert fresh["thread_id"] != first["thread_id"]
    p = c.get("/api/chat/place", params={"origin": "review", "task_id": 5}).json()
    assert p["thread"]["id"] == fresh["thread_id"] and p["thread"]["origin"] == "review" and len(p["messages"]) == 2 and p["busy"] is False
    assert c.get("/api/chat/place", params={"origin": "today"}).json()["thread"] is None
    assert c.get("/api/chat/place", params={"origin": "nope"}).status_code == 400
