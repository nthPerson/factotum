"""Wave 8: a connector or skill may write tasks (SPEC.md §15 "Writing from a connector"): `create`
as a batch op (undo drops the task), `watch_sent` (close the task when mail to an address is
sent), `apply_changes` beside `stage_changes` when the deployment opts in to direct writes."""
import json

from factotum.db import connect, one, rows, upsert_task

from conftest import TODAY, task
from test_phase4 import FakeMailbird, _msg


def _rpc(ctx, id_, method, params=None):
    from factotum import mcp_server as M
    msg = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        msg["params"] = params
    return M.dispatch(ctx, msg)


def _text(resp) -> str:
    return "".join(c["text"] for c in resp["result"]["content"] if c["type"] == "text")


def test_batch_create_makes_a_task_with_reminders_and_undo_drops_it(conn):
    from factotum import batches
    res = batches.apply(conn, [{"op": "create", "fields": {"name": "Study for the COMPE 510 midterm", "project": "COMPE 510", "area": "Academics", "due": "2026-11-12", "hard": 1, "score": 99},
                                "reminders": [{"fire_at": "2026-11-10T18:00", "label": "start studying"}, {"fire_at": "not a date"}]},
                               {"op": "create", "fields": {"project": "Personal"}}], "connector", TODAY)
    assert len(res["created"]) == 1 and res["applied"] == 2                                   # the task and its one good reminder
    tid = res["created"][0]
    t = one(conn, "SELECT * FROM tasks WHERE id = ?", (tid,))
    assert (t["name"], t["project"], t["area"], t["due"], t["hard"], t["status"], t["source"]) == ("Study for the COMPE 510 midterm", "COMPE 510", "Academics", "2026-11-12", 1, "open", "Claude")
    assert t["source_key"].startswith("connector:") and t["mentions"] == 1 and t["score"] is None      # derived fields are not a create's to set
    assert [r["label"] for r in rows(conn, "SELECT label FROM reminders WHERE task_id = ? AND status = 'pending'", (tid,))] == ["start studying"]
    whys = " | ".join(str(s["why"]) for s in res["skipped"])
    assert "fire_at" in whys and "name" in whys
    assert one(conn, "SELECT field FROM changes WHERE batch_id = ? AND task_id = ? AND field = 'created'", (res["batch_id"], tid))
    batches.undo(conn, res["batch_id"])
    t = one(conn, "SELECT status, closed_by FROM tasks WHERE id = ?", (tid,))
    assert (t["status"], t["closed_by"]) == ("dropped", "undo")                                # never deleted: ids are forever, and the mirror may have a page
    assert not rows(conn, "SELECT 1 AS x FROM reminders WHERE task_id = ? AND status = 'pending'", (tid,))


def test_watch_sent_closes_the_task_when_mail_to_the_address_is_sent_after_the_watch(conn, cfg):
    from datetime import datetime
    from factotum import batches, mail_sweep
    from factotum.db import seed_accounts
    seed_accounts(conn, cfg["sources"]["mail"]["seed_accounts"]); conn.commit()
    upsert_task(conn, task(id=1, name="Budget numbers for Reza", project="Caltrans", created_at="2026-10-20T00:00:00+00:00"))
    upsert_task(conn, task(id=2, name="No address anywhere", project="Personal"))
    res = batches.apply(conn, [{"task_id": 1, "op": "watch_sent", "fields": {"to": "Reza@SDSU.edu"}}, {"task_id": 2, "op": "watch_sent"}, {"task_id": 2, "op": "watch_sent", "fields": {"to": "not an address"}}], "connector", TODAY)
    assert res["applied"] == 1 and len(res["skipped"]) == 2 and "address" in res["skipped"][0]["why"]
    t = one(conn, "SELECT counterpart, close_on_sent_since FROM tasks WHERE id = 1")
    assert "reza@sdsu.edu" in t["counterpart"] and t["close_on_sent_since"]
    conn.execute("UPDATE tasks SET close_on_sent_since = '2026-11-01T12:00:00+00:00' WHERE id = 1"); conn.commit()
    mb = FakeMailbird()
    mb.add(7, "conv-early", "numbers (draft)", "2026-11-01T10:00:00", [_msg(201, "rashe7414@sdsu.edu", ["reza@sdsu.edu"], "2026-11-01T10:00:00", "early")])
    mail_sweep.sweep(conn, cfg, mb, datetime(2026, 11, 1, 19, 0))
    assert one(conn, "SELECT status FROM tasks WHERE id = 1")["status"] == "open"              # sent before the watch began: not what was asked for
    mb.add(7, "conv-sent", "Budget numbers", "2026-11-01T17:00:00", [_msg(202, "rashe7414@sdsu.edu", ["reza@sdsu.edu", "x@example.com"], "2026-11-01T17:00:00", "attached")])
    out = mail_sweep.sweep(conn, cfg, mb, datetime(2026, 11, 1, 19, 20))
    t = one(conn, "SELECT status, closed_by FROM tasks WHERE id = 1")
    assert (t["status"], t["closed_by"]) == ("done", "mail-evidence") and "closed 1" in out
    e = one(conn, "SELECT verdict, ref FROM evidence WHERE task_id = 1 AND kind = 'sent_watched'")
    assert e["verdict"] == "applied" and e["ref"] == "conv-sent"
    assert one(conn, "SELECT 1 AS x FROM links WHERE task_id = 1 AND kind = 'mail_thread' AND ref = 'conv-sent'")
    # unwatch clears it
    upsert_task(conn, task(id=3, name="Other", counterpart="a@b.org")); conn.commit()
    batches.apply(conn, [{"task_id": 3, "op": "watch_sent"}], "ui", TODAY)                     # the address already on the task
    assert one(conn, "SELECT close_on_sent_since FROM tasks WHERE id = 3")["close_on_sent_since"]
    batches.apply(conn, [{"task_id": 3, "op": "unwatch_sent"}], "ui", TODAY)
    assert one(conn, "SELECT close_on_sent_since FROM tasks WHERE id = 3")["close_on_sent_since"] is None


def test_apply_changes_is_offered_only_when_the_deployment_opts_in_and_writes_one_undoable_batch(cfg, tmp_path):
    from factotum import batches, chat, mcp_server as M
    from factotum.db import init_db
    db = tmp_path / "w.db"
    c = connect(db); init_db(c)
    upsert_task(c, task(id=5, name="Reply to Max", project="SCIBER-CT")); upsert_task(c, task(id=6, name="Old thing", status="done", completed_at="2026-10-20")); c.commit()
    writer = lambda: connect(db)
    staged = M.Context(connect(db), cfg, run_dir=tmp_path / "r1", writer=writer)
    names = {t["name"] for t in _rpc(staged, 1, "tools/list")["result"]["tools"]}
    assert "stage_changes" in names and "apply_changes" not in names and "mcp__factotum__apply_changes" not in chat.allowed_tools()
    r = _rpc(staged, 2, "tools/call", {"name": "apply_changes", "arguments": {"changes": [{"task_id": 5, "op": "done"}]}})
    assert r["result"]["isError"] and one(c, "SELECT status FROM tasks WHERE id = 5")["status"] == "open"
    ctx = M.Context(connect(db), cfg, run_dir=tmp_path / "r2", writer=writer, apply_writes=True, source="connector")
    tools = {t["name"]: t for t in _rpc(ctx, 3, "tools/list")["result"]["tools"]}
    assert tools["tasks_query"]["annotations"]["readOnlyHint"] is True and tools["apply_changes"]["annotations"] == {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False}
    res = json.loads(_text(_rpc(ctx, 4, "tools/call", {"name": "apply_changes", "arguments": {"label": "midterm prep", "changes": [
        {"op": "create", "fields": {"name": "Study for the COMPE 510 midterm", "project": "COMPE 510", "due": "2026-11-12", "hard": True, "effort": "L", "context": "Chapters 4-7"},
         "reminders": [{"fire_at": "2026-11-09T18:00", "label": "start"}, {"fire_at": "2026-11-11T18:00", "label": "review", "repeat": None}]},
        {"task_id": 5, "op": "set", "fields": {"due": "2026-11-05"}},
        {"task_id": 5, "op": "watch_sent", "fields": {"to": "max@sdsu.edu"}},
        {"task_id": 6, "op": "done"},
        {"task_id": 6, "op": "reopen"},
        {"op": "create", "fields": {"name": "reply to max"}},
        {"op": "create", "fields": {"name": "Nowhere task", "project": "Not A Project"}},
        {"op": "create", "fields": {"name": "Bad watch"}, "watch_sent_to": "nope"}]}})))
    assert res["applied"] == 6 and len(res["created"]) == 1 and len(res["rejected"]) == 4 and res["batch_id"], res
    tid = res["created"][0]["task_id"]
    t = one(c, "SELECT * FROM tasks WHERE id = ?", (tid,))
    assert (t["name"], t["project"], t["area"], t["due"], t["hard"], t["effort"], t["source"]) == ("Study for the COMPE 510 midterm", "COMPE 510", "Academics", "2026-11-12", 1, "L", "Claude")
    assert len(rows(c, "SELECT 1 AS x FROM reminders WHERE task_id = ? AND status = 'pending'", (tid,))) == 2
    t5 = one(c, "SELECT due, counterpart, close_on_sent_since FROM tasks WHERE id = 5")
    assert t5["due"] == "2026-11-05" and "max@sdsu.edu" in t5["counterpart"] and t5["close_on_sent_since"]
    assert one(c, "SELECT status FROM tasks WHERE id = 6")["status"] == "open"                 # reopen is the one op a closed row takes
    assert one(c, "SELECT source FROM batches WHERE id = ?", (res["batch_id"],))["source"] == "connector"
    whys = " | ".join(res["rejected"])
    assert "not open" in whys and "already" in whys and "taxonomy" in whys and "address" in whys
    batches.undo(c, res["batch_id"])
    assert one(c, "SELECT status FROM tasks WHERE id = ?", (tid,))["status"] == "dropped" and one(c, "SELECT due FROM tasks WHERE id = 5")["due"] is None


def test_a_staged_create_reaches_the_tray_and_the_http_route_follows_the_setting(cfg, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from factotum import config as C, mcp_server as M
    from factotum.api import create_app
    monkeypatch.setenv("FACTOTUM_MCP_TOKEN", "s3cret")
    app = create_app(cfg, db_path=str(tmp_path / "api.db"))
    c = app.state.conn
    client = TestClient(app)
    ctx = M.Context(connect(tmp_path / "api.db"), cfg, run_dir=tmp_path / "run", writer=lambda: connect(tmp_path / "api.db"), source="connector")
    res = json.loads(_text(_rpc(ctx, 1, "tools/call", {"name": "stage_changes", "arguments": {"label": "a new task", "changes": [
        {"op": "create", "fields": {"name": "Book the dentist", "project": "Personal"}, "reminders": [{"fire_at": "2026-11-06T09:00"}], "watch_sent_to": "dentist@example.com"}]}})))
    assert res["staged"] == 1 and not rows(c, "SELECT 1 AS x FROM tasks WHERE name = 'Book the dentist'")
    p = client.get("/api/proposals").json()["proposals"][0]
    assert p["source"] == "connector" and p["changes"][0]["op"] == "create"
    out = client.post(f"/api/proposals/{p['id']}/apply", json={}).json()
    t = one(c, "SELECT * FROM tasks WHERE name = 'Book the dentist'")
    assert one(c, "SELECT source FROM batches WHERE id = ?", (out["batch_id"],))["source"] == "connector"
    assert out["created"] == [t["id"]] and t["status"] == "open" and "dentist@example.com" in t["counterpart"] and t["close_on_sent_since"]
    assert one(c, "SELECT COUNT(*) AS n FROM reminders WHERE task_id = ?", (t["id"],))["n"] == 1
    # the HTTP route: apply_changes exists only when server.mcp_writes says apply
    h = {"Authorization": "Bearer s3cret"}
    lst = lambda: {x["name"] for x in client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=h).json()["result"]["tools"]}
    assert "apply_changes" not in lst() and "server.mcp_writes" in C.SETTINGS_KEYS
    client.put("/api/settings", json={"values": {"server.mcp_writes": "apply"}})
    assert "apply_changes" in lst()
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "apply_changes", "arguments": {"changes": [{"task_id": t["id"], "op": "set", "fields": {"due": "2026-11-07"}}]}}}, headers=h).json()
    body = json.loads(r["result"]["content"][0]["text"])
    assert body["applied"] == 1 and one(c, "SELECT due FROM tasks WHERE id = ?", (t["id"],))["due"] == "2026-11-07"
    assert one(c, "SELECT source FROM batches WHERE id = ?", (body["batch_id"],))["source"] == "connector"
