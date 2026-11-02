"""Phase 6: the Notion tasks mirror, staged proposals from skills, the HTTP MCP route, Slack and
Discord read tools (SPEC.md §7, §14.6, §15; §6 inputs)."""
import json

import httpx
import pytest

from factotum.db import one, rows, upsert_task

from conftest import TODAY, task


# --- the Notion tasks mirror -------------------------------------------------------------------

SCHEMA = {"properties": {
    "Name": {"type": "title"}, "Task ID": {"type": "unique_id"}, "Status": {"type": "select"}, "Project": {"type": "select"}, "Area": {"type": "select"},
    "Due": {"type": "date"}, "Hard deadline": {"type": "checkbox"}, "Effort": {"type": "select"}, "Source": {"type": "select"}, "Source Key": {"type": "rich_text"},
    "Source Link": {"type": "url"}, "Context": {"type": "rich_text"}, "Why now": {"type": "rich_text"}, "Score": {"type": "number"}, "Priority": {"type": "select"},
    "Mentions": {"type": "number"}, "Completed": {"type": "date"}, "Waiting On": {"type": "rich_text"}, "Calendar Event": {"type": "url"}, "Notes": {"type": "relation"},
    "Snoozed Until": {"type": "date"}, "Manual Priority": {"type": "select"}, "Created": {"type": "created_time"}, "Edited": {"type": "last_edited_time"}}}


class FakeNotion:
    """Records every request; answers the schema, page creates and updates."""
    def __init__(self, fail_update_for=()):
        self.calls, self.n, self.fail = [], 0, set(fail_update_for)

    def transport(self):
        def handler(req: httpx.Request):
            body = json.loads(req.content) if req.content else {}
            self.calls.append((req.method, req.url.path, body, req.headers.get("Notion-Version")))
            if req.method == "GET" and req.url.path.startswith("/v1/data_sources/"):
                return httpx.Response(200, json=SCHEMA)
            if req.method == "POST" and req.url.path == "/v1/pages":
                self.n += 1
                return httpx.Response(200, json={"id": f"page-{self.n}", "url": f"https://www.notion.so/page-{self.n}"})
            if req.method == "PATCH" and req.url.path.startswith("/v1/pages/"):
                pid = req.url.path.rsplit("/", 1)[-1]
                if pid in self.fail:
                    return httpx.Response(400, json={"message": "Status is expected to be select."})
                return httpx.Response(200, json={"id": pid})
            return httpx.Response(404, json={"message": "nope"})
        return httpx.MockTransport(handler)

    def pages(self, method):
        return [(p, b) for m, p, b, _ in self.calls if m == method and "/pages" in p]


def test_mirror_properties_follow_the_schema_and_the_config_map(cfg, conn, monkeypatch):
    from factotum import notion_mirror as NM
    monkeypatch.setenv("NOTION_TOKEN", "t")
    upsert_task(conn, task(id=42, name="Reply to Max", project="COMPE 510", area="Academics", due="2026-11-05T14:00", due_is_datetime=1, hard=1, effort="S",
                           source="Chat", source_key="chat:1:0", source_link="https://x", context="ctx", why_now="soon", waiting_on="Max", manual_priority="P1",
                           completed_at=None, snoozed_until="2026-11-04", calendar_event="https://cal/1"))
    conn.execute("INSERT INTO links (task_id, kind, ref, label, created_at) VALUES (42, 'plaud_note', 'abcdef1234567890abcdef1234567890', 'rec', 'now')")
    conn.execute("INSERT INTO links (task_id, kind, ref, label, created_at) VALUES (42, 'note_page', 'https://www.notion.so/Title-0123456789abcdef0123456789abcdef', 'note', 'now')")
    conn.execute("INSERT INTO links (task_id, kind, ref, label, created_at) VALUES (42, 'mail_thread', 'c1', 'mail', 'now')")
    conn.commit()
    schema = NM.schema(cfg, transport=FakeNotion().transport())
    assert schema["Status"] == "select" and schema["Task ID"] == "unique_id"
    t = one(conn, "SELECT * FROM tasks WHERE id = 42")
    p = NM.properties(cfg, t, schema, NM.note_ids(conn, 42), TODAY)
    assert p["Name"] == {"title": [{"type": "text", "text": {"content": "Reply to Max"}}]}
    assert p["Status"] == {"select": {"name": "Next"}} and p["Project"] == {"select": {"name": "COMPE 510"}} and p["Effort"] == {"select": {"name": "S"}}
    assert p["Due"]["date"]["start"].startswith("2026-11-05T14:00:00") and len(p["Due"]["date"]["start"]) == 25 and p["Hard deadline"] == {"checkbox": True} and p["Completed"] == {"date": None}   # the owner's offset appended
    assert p["Snoozed Until"] == {"date": {"start": "2026-11-04"}} and p["Manual Priority"] == {"select": {"name": "P1"}}
    assert p["Score"]["number"] > 0 and p["Priority"]["select"]["name"].startswith("P") and p["Mentions"] == {"number": 1}
    assert p["Source Link"] == {"url": "https://x"} and p["Calendar Event"] == {"url": "https://cal/1"} and p["Context"]["rich_text"][0]["text"]["content"] == "ctx"
    assert p["Notes"] == {"relation": [{"id": "abcdef1234567890abcdef1234567890"}, {"id": "0123456789abcdef0123456789abcdef"}]}
    assert "Task ID" not in p and "Created" not in p and "Edited" not in p           # read-only in Notion
    # a dropped-by-automation row maps through status_map; a name is capped; missing text clears the column
    upsert_task(conn, task(id=43, name="x" * 3000, status="faded", context=None)); conn.commit()
    p2 = NM.properties(cfg, one(conn, "SELECT * FROM tasks WHERE id = 43"), schema, [], TODAY)
    assert p2["Status"] == {"select": {"name": "Dropped"}} and len(p2["Name"]["title"][0]["text"]["content"]) == 2000 and p2["Context"] == {"rich_text": []}
    assert p2["Notes"] == {"relation": []}


def test_mirror_sweep_creates_updates_since_the_watermark_caps_and_records_page_ids(cfg, conn, monkeypatch):
    from factotum import notion_mirror as NM
    monkeypatch.setenv("NOTION_TOKEN", "t")
    upsert_task(conn, task(id=1, name="Old mirrored", notion_page_id="page-old", edited_at="2026-10-01T00:00:00+00:00", mirrored_at="2026-11-01T00:00:00+00:00"))
    upsert_task(conn, task(id=2, name="Edited since", notion_page_id="page-2", edited_at="2026-11-02T10:00:00+00:00", mirrored_at="2026-11-01T00:00:00+00:00"))
    upsert_task(conn, task(id=3, name="Never mirrored", edited_at="2026-10-05T00:00:00+00:00"))
    upsert_task(conn, task(id=4, name="Also new", edited_at="2026-11-02T11:00:00+00:00"))
    conn.commit()
    fake = FakeNotion()
    dry = NM.sweep(conn, cfg, dry_run=True, transport=fake.transport())
    assert dry["pending"] == 3 and fake.pages("POST") == [] and fake.pages("PATCH") == []
    res = NM.sweep(conn, cfg, cap=2, transport=fake.transport())
    # creates go first, in id order (Notion's Task ID counter keeps pace with Factotum's ids); the cap leaves the edit for the next pass
    assert res["created"] == 2 and res["updated"] == 0 and res["pending"] == 1
    assert one(conn, "SELECT notion_page_id FROM tasks WHERE id = 3")["notion_page_id"] == "page-1" and one(conn, "SELECT notion_page_id FROM tasks WHERE id = 4")["notion_page_id"] == "page-2"
    assert one(conn, "SELECT edited_at FROM tasks WHERE id = 3")["edited_at"] == "2026-10-05T00:00:00+00:00"     # storing the page id is not an edit
    assert fake.pages("PATCH") == [] and one(conn, "SELECT mirrored_at FROM tasks WHERE id = 3")["mirrored_at"] == "2026-10-05T00:00:00+00:00"
    create_body = fake.pages("POST")[0][1]
    assert create_body["parent"] == {"type": "data_source_id", "data_source_id": cfg["sinks"]["notion_tasks_mirror"]["data_source_id"]} and create_body["properties"]["Name"]["title"][0]["text"]["content"] == "Never mirrored"
    assert sum(1 for m, p, _, _ in fake.calls if m == "GET") == 1                                   # the schema once per sweep
    res2 = NM.sweep(conn, cfg, transport=fake.transport())
    assert res2["updated"] == 1 and res2["created"] == 0 and res2["pending"] == 0 and [p for p, _ in fake.pages("PATCH")] == ["/v1/pages/page-2"]
    # an edit after the push is pending again; a refused row is stamped and reported, not retried every pass
    conn.execute("UPDATE tasks SET edited_at = '2026-11-02T12:00:00+00:00' WHERE id = 1"); conn.commit()
    bad = FakeNotion(fail_update_for={"page-old"})
    res3 = NM.sweep(conn, cfg, transport=bad.transport())
    assert res3["failed"] == ["Task-1: 400 Status is expected to be select."] and res3["pending"] == 0
    assert NM.sweep(conn, cfg, transport=fake.transport())["pending"] == 0
    # a refused CREATE is stamped too: it waits for its next edit instead of filling every pass
    upsert_task(conn, task(id=9, name="Refused create", edited_at="2026-10-09T00:00:00+00:00")); conn.commit()
    class RefuseCreate(FakeNotion):
        def transport(self):
            inner = super().transport()
            def handler(req):
                if req.method == "POST" and req.url.path == "/v1/pages":
                    return httpx.Response(400, json={"message": "Status is expected to be status."})
                return inner.handler(req)
            return httpx.MockTransport(handler)
    r5 = NM.sweep(conn, cfg, transport=RefuseCreate().transport())
    assert r5["failed"] == ["Task-9: 400 Status is expected to be status."] and r5["pending"] == 0 and NM.sweep(conn, cfg, transport=RefuseCreate().transport())["failed"] == []
    conn.execute("UPDATE tasks SET edited_at = '2026-11-02T13:00:00+00:00' WHERE id = 9"); conn.commit()
    assert NM.sweep(conn, cfg, dry_run=True, transport=fake.transport())["pending"] == 1
    # --all re-pushes every row
    fake2 = FakeNotion()
    res4 = NM.sweep(conn, cfg, all_rows=True, transport=fake2.transport())
    assert res4["updated"] == 4 and res4["created"] == 1 and res4["failed"] == [] and one(conn, "SELECT notion_page_id FROM tasks WHERE id = 1")["notion_page_id"] == "page-old"
    assert conn.in_transaction is False                                                           # never a transaction left open across HTTP


def test_mirror_commits_per_row_and_values_are_notion_safe(cfg, conn, monkeypatch):
    """A pass commits after every row (the write lock is never held across an HTTP call: the
    2026-11-01 'database is locked' gotcha); select names carry no commas; a naive datetime gets
    the owner's offset so Notion shows the right hour."""
    from factotum import notion_mirror as NM
    monkeypatch.setenv("NOTION_TOKEN", "t")
    cfg["owner"]["timezone"] = "America/Los_Angeles"
    upsert_task(conn, task(id=1, name="a", project="Caltrans, District 11", due="2026-11-05T14:00", due_is_datetime=1))
    upsert_task(conn, task(id=2, name="b")); conn.commit()
    seen = []
    class Spy(FakeNotion):
        def transport(self):
            inner = super().transport()
            def handler(req):
                seen.append(conn.in_transaction)       # the connection must not be mid-transaction while Notion is on the line
                return inner.handler(req)
            return httpx.MockTransport(handler)
    fake = Spy()
    NM.sweep(conn, cfg, transport=fake.transport())
    assert seen and not any(seen)
    body = fake.pages("POST")[0][1]["properties"]
    assert body["Project"] == {"select": {"name": "Caltrans District 11"}} and body["Due"] == {"date": {"start": "2026-11-05T14:00:00-08:00"}}



def test_scheduler_runs_the_mirror_on_its_interval_when_the_sink_is_on(conn, cfg):
    from datetime import datetime
    from types import SimpleNamespace
    from factotum.scheduler import Scheduler
    calls = []
    s = Scheduler(cfg, conn, push=lambda *a, **k: None, mailer=lambda *a, **k: None, mirror=SimpleNamespace(sweep_job=lambda c, e, now: (calls.append(now), "2 rows")[1]))
    s.tick(datetime(2026, 11, 2, 10, 0)); s.tick(datetime(2026, 11, 2, 10, 5)); s.tick(datetime(2026, 11, 2, 10, 11))
    assert len(calls) == 2 and one(conn, "SELECT summary FROM runs WHERE kind = 'notion_mirror'")["summary"] == "2 rows"
    cfg["sinks"]["notion_tasks_mirror"]["enabled"] = False
    s.tick(datetime(2026, 11, 2, 10, 30))
    assert len(calls) == 2


# --- skills: staged proposals and the HTTP MCP route ---------------------------------------------

def _rpc(ctx, id_, method, params=None):
    from factotum import mcp_server as M
    msg = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        msg["params"] = params
    return M.dispatch(ctx, msg)


def _text(resp) -> str:
    return "".join(c["text"] for c in resp["result"]["content"] if c["type"] == "text")


def test_stage_changes_writes_a_pending_proposal_that_the_tray_applies_or_dismisses(cfg, tmp_path):
    """A skill never writes task state: stage_changes validates like a chat proposal and records a
    pending proposal; the UI's tray applies it as one batch (source skill) or dismisses it."""
    from fastapi.testclient import TestClient
    from factotum import chat, mcp_server as M
    from factotum.api import create_app
    from factotum.db import connect
    app = create_app(cfg, db_path=str(tmp_path / "api.db"))
    c = app.state.conn
    upsert_task(c, task(id=5, name="Reply to Max")); upsert_task(c, task(id=6, name="Slides")); c.commit()
    reader = connect(tmp_path / "api.db")
    ctx = M.Context(reader, cfg, run_dir=tmp_path / "run", writer=lambda: connect(tmp_path / "api.db"))
    assert "mcp__factotum__stage_changes" not in chat.allowed_tools()                      # chat turns propose in their JSON instead
    assert any(t["name"] == "stage_changes" for t in _rpc(ctx, 1, "tools/list")["result"]["tools"])
    res = json.loads(_text(_rpc(ctx, 2, "tools/call", {"name": "stage_changes", "arguments": {"label": "Max replied; slides are the next thing", "changes": [
        {"task_id": 5, "op": "done"},
        {"task_id": 6, "op": "set", "fields": {"due": "2026-11-05", "score": 99, "context": "Max wants them Thursday"}},
        {"task_id": 6, "op": "add_reminder", "fields": {"fire_at": "2026-11-04T09:00", "label": "slides"}},
        {"task_id": 999, "op": "done"},
        {"task_id": 5, "op": "reopen"}]}})))
    assert res["proposal_id"] and res["staged"] == 3 and len(res["rejected"]) == 2 and "Apply" in res["note"]
    assert one(c, "SELECT status FROM tasks WHERE id = 5")["status"] == "open"              # nothing applied
    client = TestClient(app)
    pend = client.get("/api/proposals").json()["proposals"]
    assert len(pend) == 1 and pend[0]["label"].startswith("Max replied") and pend[0]["source"] == "skill" and [ch["op"] for ch in pend[0]["changes"]] == ["done", "set", "add_reminder"]
    assert pend[0]["changes"][1]["fields"] == {"due": "2026-11-05", "due_is_datetime": 0, "context": "Max wants them Thursday"}
    r = client.post(f"/api/proposals/{pend[0]['id']}/apply", json={}).json()
    assert r["applied"] == 3 and one(c, "SELECT status, closed_by FROM tasks WHERE id = 5")["closed_by"] == "skill"
    assert one(c, "SELECT source FROM batches WHERE id = ?", (r["batch_id"],))["source"] == "skill" and client.get("/api/proposals").json()["proposals"] == []
    assert client.post(f"/api/proposals/{pend[0]['id']}/apply", json={}).status_code == 409              # already applied
    # a second proposal, dismissed
    json.loads(_text(_rpc(ctx, 3, "tools/call", {"name": "stage_changes", "arguments": {"changes": [{"task_id": 6, "op": "drop"}]}})))
    pid = client.get("/api/proposals").json()["proposals"][0]["id"]
    assert client.post(f"/api/proposals/{pid}/dismiss", json={}).json()["ok"] and client.get("/api/proposals").json()["proposals"] == []
    assert one(c, "SELECT status FROM tasks WHERE id = 6")["status"] == "open"
    # the reader connection stayed read-only
    with pytest.raises(Exception):
        reader.execute("UPDATE tasks SET name = 'x' WHERE id = 6")
    # a context without a writer refuses to stage
    ctx2 = M.Context(connect(tmp_path / "api.db"), cfg, run_dir=tmp_path / "run2")
    assert _rpc(ctx2, 4, "tools/call", {"name": "stage_changes", "arguments": {"changes": [{"task_id": 6, "op": "drop"}]}})["result"]["isError"]


def test_http_mcp_route_needs_the_bearer_token_and_speaks_jsonrpc(cfg, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from factotum.api import create_app
    monkeypatch.setenv("FACTOTUM_MCP_TOKEN", "s3cret")
    app = create_app(cfg, db_path=str(tmp_path / "api.db"))
    upsert_task(app.state.conn, task(id=7, name="HTTP one")); app.state.conn.commit()
    client = TestClient(app)
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
    assert client.post("/mcp", json=init).status_code == 401
    assert client.post("/mcp", json=init, headers={"Authorization": "Bearer wrong"}).status_code == 401
    h = {"Authorization": "Bearer s3cret"}
    r = client.post("/mcp", json=init, headers=h)
    assert r.status_code == 200 and r.json()["result"]["serverInfo"]["name"] == "factotum"
    assert client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=h).status_code == 202
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "tasks_query", "arguments": {"q": "http"}}}, headers=h)
    assert r.status_code == 200 and json.loads(r.json()["result"]["content"][0]["text"])["tasks"][0]["id"] == 7
    assert client.get("/mcp", headers=h).status_code == 405
    assert client.post("/mcp", content=b"not json", headers=dict(h, **{"Content-Type": "application/json"})).json()["error"]["code"] == -32700
    # the HTTP route serves reads and stage_changes; drafts and the Plaud subprocess only when the config opts in
    names = {t["name"] for t in client.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, headers=h).json()["result"]["tools"]}
    assert "tasks_query" in names and "stage_changes" in names and "mail_search" in names and not {"mail_draft_reply", "mail_draft_new", "plaud_search", "plaud_summary"} & names
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "mail_draft_new", "arguments": {}}}, headers=h).json()
    assert r["result"]["isError"] and "not available" in r["result"]["content"][0]["text"]
    cfg["server"]["mcp_http_tools"] = "all"
    names = {t["name"] for t in client.post("/mcp", json={"jsonrpc": "2.0", "id": 5, "method": "tools/list"}, headers=h).json()["result"]["tools"]}
    assert "mail_draft_new" in names and "mail_draft_reply" not in names                       # one message per request: no thread is ever "seen this run"
    monkeypatch.delenv("FACTOTUM_MCP_TOKEN")
    assert client.post("/mcp", json=init, headers=h).status_code == 404                        # no token configured: the route is off


def test_state_changing_api_posts_need_a_json_content_type(cfg, tmp_path):
    """A body-less POST (undo, apply a proposal, finish review) must not be a browser "simple request":
    the UI always sends application/json, a cross-site form or fetch without it cannot."""
    from fastapi.testclient import TestClient
    from factotum.api import create_app
    app = create_app(cfg, db_path=str(tmp_path / "api.db"))
    client = TestClient(app)
    assert client.post("/api/review/finish", headers={"Content-Type": "text/plain"}).status_code == 415
    assert client.post("/api/review/finish", data={"a": "b"}).status_code == 415
    assert client.post("/api/review/finish", headers={"Content-Type": "application/json"}).status_code == 200
    assert client.post("/api/review/finish", json={}).status_code == 200
    assert client.get("/api/tasks").status_code == 200


def test_stage_changes_validates_the_fields_the_cleaner_does_not(cfg, tmp_path):
    from factotum import mcp_server as M
    from factotum.db import connect, init_db
    w = connect(tmp_path / "s.db"); init_db(w); upsert_task(w, task(id=1)); w.commit()
    ctx = M.Context(connect(tmp_path / "s.db"), cfg, run_dir=tmp_path / "r", writer=lambda: connect(tmp_path / "s.db"))
    res = json.loads(_text(_rpc(ctx, 1, "tools/call", {"name": "stage_changes", "arguments": {"changes": [
        {"task_id": 1, "op": "set", "fields": {"snoozed_until": "2026-11-09"}},
        {"task_id": 1, "op": "set", "fields": {"snoozed_until": "next tuesday"}},
        {"task_id": 1, "op": "set", "fields": {"manual_priority": "P1"}},
        {"task_id": 1, "op": "set", "fields": {"manual_priority": "urgent"}},
        {"task_id": 1, "op": "set", "fields": {"longterm": "yes"}},
        {"task_id": 1, "op": "set", "fields": {"longterm": None}}]}})))
    assert res["staged"] == 3 and len(res["rejected"]) == 3
    kept = json.loads(one(w, "SELECT changes_json FROM proposals")["changes_json"])
    assert [c["fields"] for c in kept] == [{"snoozed_until": "2026-11-09"}, {"manual_priority": "P1"}, {"longterm": 1}]
    upsert_task(w, task(id=2, status="dropped")); w.commit()
    bad = _rpc(ctx, 2, "tools/call", {"name": "stage_changes", "arguments": {"changes": [{"task_id": 2, "op": "done"}, {"task_id": 1, "op": "set", "fields": {"project": "Nope Inc"}}]}})
    assert bad["result"]["isError"] and "not open" in _text(bad) and "project" in _text(bad)


# --- Slack and Discord read tools --------------------------------------------------------------

def _slack_cfg(cfg):
    cfg["sources"]["slack"] = {"enabled": True, "workspaces": [{"name": "DiCE Lab", "token_env": "SLACK_TOKEN_DICE"}, {"name": "Car Sounds", "token_env": "SLACK_TOKEN_CAR"}]}
    return cfg


class FakeSlack:
    def __init__(self):
        self.calls = []

    def transport(self):
        def handler(req: httpx.Request):
            method = req.url.path.rsplit("/", 1)[-1]
            params = dict(req.url.params) if req.method == "GET" else dict(httpx.QueryParams(req.content.decode()))
            self.calls.append((method, req.headers.get("Authorization"), params))
            if method == "auth.test":
                return httpx.Response(200, json={"ok": True, "team": "DiCE Lab", "user": "robert", "user_id": "U1"})
            if method == "conversations.list":
                return httpx.Response(200, json={"ok": True, "channels": [{"id": "C1", "name": "general", "is_member": True, "num_members": 9, "topic": {"value": "chatter"}},
                                                                            {"id": "C2", "name": "papers", "is_member": True, "num_members": 4, "topic": {"value": ""}}], "response_metadata": {"next_cursor": ""}})
            if method == "conversations.history":
                return httpx.Response(200, json={"ok": True, "messages": [{"ts": "1762000000.000100", "user": "U2", "text": "Draft due <@U1> by Friday " + "x" * 3000, "reply_count": 2, "thread_ts": "1762000000.000100"},
                                                                            {"ts": "1761990000.000100", "user": "U1", "text": "ok"}]})
            if method == "conversations.replies":
                return httpx.Response(200, json={"ok": True, "messages": [{"ts": "1762000000.000100", "user": "U2", "text": "Draft due"}, {"ts": "1762000100.000100", "user": "U1", "text": "will do"}]})
            if method == "search.messages":
                if params.get("query", "").startswith("fail"):
                    return httpx.Response(200, json={"ok": False, "error": "not_allowed_token_type"})
                return httpx.Response(200, json={"ok": True, "messages": {"total": 1, "matches": [{"ts": "1762000000.000100", "user": "U2", "username": "max", "text": "Draft due by Friday",
                                                                                                   "channel": {"id": "C1", "name": "general"}, "permalink": "https://dice.slack.com/archives/C1/p1762000000000100"}]}})
            if method == "users.info":
                return httpx.Response(200, json={"ok": True, "user": {"id": params["user"], "name": "max" if params["user"] == "U2" else "robert", "real_name": "Max M" if params["user"] == "U2" else "Robert"}})
            if method == "chat.postMessage":
                raise AssertionError("a write reached the network")
            return httpx.Response(200, json={"ok": False, "error": f"unknown_method {method}"})
        return httpx.MockTransport(handler)


def test_slack_client_is_read_only_and_the_tools_resolve_workspaces_names_and_threads(conn, cfg, monkeypatch, tmp_path):
    from factotum import mcp_server as M, slack
    _slack_cfg(cfg)
    monkeypatch.setenv("SLACK_TOKEN_DICE", "xoxp-dice"); monkeypatch.delenv("SLACK_TOKEN_CAR", raising=False)
    fake = FakeSlack()
    ws = slack.workspaces(cfg)
    assert [(w["name"], w["ok"]) for w in ws] == [("DiCE Lab", True), ("Car Sounds", False)] and "SLACK_TOKEN_CAR" in ws[1]["why"]
    client = slack.Client("xoxp-dice", transport=fake.transport())
    with pytest.raises(slack.SlackError):
        client.call("chat.postMessage", {"channel": "C1", "text": "hi"})
    assert client.call("auth.test")["team"] == "DiCE Lab" and fake.calls[-1][1] == "Bearer xoxp-dice"
    ctx = M.Context(conn, cfg, run_dir=tmp_path, slack=lambda token: slack.Client(token, transport=fake.transport()))
    chans = json.loads(_text(_rpc(ctx, 1, "tools/call", {"name": "slack_channels", "arguments": {"workspace": "dice"}})))
    assert [c["name"] for c in chans["channels"]] == ["general", "papers"] and chans["workspace"] == "DiCE Lab"
    hist = json.loads(_text(_rpc(ctx, 2, "tools/call", {"name": "slack_history", "arguments": {"workspace": "DiCE Lab", "channel": "general", "limit": 5}})))
    assert hist["messages"][0]["from"] == "Max M" and "@Robert" in hist["messages"][0]["text"] and len(hist["messages"][0]["text"]) < 1500 and hist["messages"][0]["replies"] == 2
    assert hist["messages"][0]["at"].startswith("2025-11-01") and hist["messages"][0]["thread_ts"] == "1762000000.000100"
    assert sum(1 for m, _, _ in fake.calls if m == "users.info") == 2                         # resolved once per user per run
    th = json.loads(_text(_rpc(ctx, 3, "tools/call", {"name": "slack_thread", "arguments": {"workspace": "DiCE Lab", "channel": "C1", "thread_ts": "1762000000.000100"}})))
    assert [m["from"] for m in th["messages"]] == ["Max M", "Robert"]
    hits = json.loads(_text(_rpc(ctx, 4, "tools/call", {"name": "slack_search", "arguments": {"query": "draft", "workspace": "DiCE Lab"}})))
    assert hits["matches"][0]["channel"] == "general" and hits["matches"][0]["permalink"].startswith("https://dice.slack.com")
    nope = _rpc(ctx, 5, "tools/call", {"name": "slack_search", "arguments": {"query": "fail", "workspace": "DiCE Lab"}})
    assert nope["result"]["isError"] and "user token" in _text(nope)
    assert _rpc(ctx, 6, "tools/call", {"name": "slack_channels", "arguments": {"workspace": "Car Sounds"}})["result"]["isError"]
    assert _rpc(ctx, 7, "tools/call", {"name": "slack_channels", "arguments": {"workspace": "nowhere"}})["result"]["isError"]
    cfg["sources"]["slack"]["enabled"] = False
    off = _rpc(ctx, 8, "tools/call", {"name": "slack_channels", "arguments": {"workspace": "DiCE Lab"}})
    assert off["result"]["isError"] and "Settings" in _text(off)


class FakeDiscord:
    def __init__(self):
        self.calls = []

    def transport(self):
        def handler(req: httpx.Request):
            self.calls.append((req.method, req.url.path, dict(req.url.params), req.headers.get("Authorization")))
            p = req.url.path
            if p == "/api/v10/users/@me":
                return httpx.Response(200, json={"id": "B1", "username": "factotum-bot"})
            if p == "/api/v10/users/@me/guilds":
                return httpx.Response(200, json=[{"id": "G1", "name": "LARK dev"}, {"id": "G2", "name": "Elsewhere"}])
            if p == "/api/v10/guilds/G1/channels":
                return httpx.Response(200, json=[{"id": "CH1", "name": "general", "type": 0, "position": 0, "topic": "t"}, {"id": "CH2", "name": "voice", "type": 2}, {"id": "CH3", "name": "dev", "type": 0, "position": 1}])
            if p.startswith("/api/v10/channels/") and p.endswith("/messages"):
                cid = p.split("/")[4]
                if cid == "CH3":
                    return httpx.Response(403, json={"message": "Missing Access", "code": 50001})
                return httpx.Response(200, json=[{"id": "M2", "author": {"id": "U9", "username": "max", "global_name": "Max"}, "content": "Deploy LARK on Friday " + "y" * 3000, "timestamp": "2026-11-01T10:00:00.000000+00:00"},
                                                 {"id": "M1", "author": {"id": "U1", "username": "robert"}, "content": "ok", "timestamp": "2026-10-31T10:00:00.000000+00:00"}])
            if req.method != "GET":
                raise AssertionError("a write reached the network")
            return httpx.Response(404, json={"message": "nope"})
        return httpx.MockTransport(handler)


def test_discord_client_is_read_only_and_the_tools_list_channels_read_and_search(conn, cfg, monkeypatch, tmp_path):
    from factotum import discord, mcp_server as M
    cfg["sources"]["discord"] = {"enabled": True, "guilds": [{"name": "LARK dev", "id": "G1"}]}
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "bot-token")
    fake = FakeDiscord()
    client = discord.Client("bot-token", transport=fake.transport())
    with pytest.raises(discord.DiscordError):
        client.post("/channels/CH1/messages", {"content": "hi"})
    assert client.get("/users/@me")["username"] == "factotum-bot" and fake.calls[-1][3] == "Bot bot-token"
    ctx = M.Context(conn, cfg, run_dir=tmp_path, discord=lambda token: discord.Client(token, transport=fake.transport()))
    chans = json.loads(_text(_rpc(ctx, 1, "tools/call", {"name": "discord_channels", "arguments": {"server": "lark"}})))
    assert [c["name"] for c in chans["channels"]] == ["general", "dev"] and chans["server"] == "LARK dev"          # text channels only
    msgs = json.loads(_text(_rpc(ctx, 2, "tools/call", {"name": "discord_messages", "arguments": {"server": "LARK dev", "channel": "general", "limit": 10}})))
    assert msgs["messages"][0]["from"] == "Max" and msgs["messages"][0]["at"].startswith("2026-11-01") and len(msgs["messages"][0]["text"]) < 1500
    hits = json.loads(_text(_rpc(ctx, 3, "tools/call", {"name": "discord_search", "arguments": {"server": "LARK dev", "query": "deploy"}})))
    assert [h["channel"] for h in hits["matches"]] == ["general"] and hits["matches"][0]["text"].startswith("Deploy") and "dev" in hits["skipped"][0]
    assert _rpc(ctx, 4, "tools/call", {"name": "discord_channels", "arguments": {"server": "Elsewhere"}})["result"]["isError"]     # not in the config
    monkeypatch.delenv("DISCORD_BOT_TOKEN")
    ctx2 = M.Context(conn, cfg, run_dir=tmp_path)
    assert "DISCORD_BOT_TOKEN" in _text(_rpc(ctx2, 5, "tools/call", {"name": "discord_channels", "arguments": {"server": "LARK dev"}}))


def test_mirror_notes_relation_ignores_plaud_recording_ids_and_task_ids_repush_rows(cfg, conn, monkeypatch):
    """sync-file links a task to its recording as plaud_note `of_<hex>` (the Plaud id); only a Notion
    page id belongs in the Notes relation (2026-11-02: Task-395..397 404'd on a Plaud id). `--task`
    re-pushes named rows, stamped or not."""
    from factotum import notion_mirror as NM
    monkeypatch.setenv("NOTION_TOKEN", "t")
    stamped = "2026-10-01T00:00:00+00:00"
    upsert_task(conn, task(id=395, name="Call Molina", notion_page_id="p395", mirrored_at=stamped))
    upsert_task(conn, task(id=396, name="Test", notion_page_id=None, mirrored_at=stamped))
    upsert_task(conn, task(id=397, name="Other", notion_page_id="p397", mirrored_at=stamped))
    conn.execute("INSERT INTO links (task_id, kind, ref, label, created_at) VALUES (395, 'plaud_note', 'of_7a19f5574ec383b17337cc2fc4757834', 'rec', 'now')")
    conn.execute("INSERT INTO links (task_id, kind, ref, label, created_at) VALUES (395, 'plaud_note', '40ceb306-5110-81c4-b511-c37681343d3a', 'Plaud note (Notion page id)', 'now')")
    conn.execute("INSERT INTO links (task_id, kind, ref, label, created_at) VALUES (395, 'note_page', 'https://app.notion.com/p/11-01-Action-Plan-40ceb306511081c4b511c37681343d3a', 'n', 'now')")
    conn.commit()
    assert NM.note_ids(conn, 395) == ["40ceb306511081c4b511c37681343d3a"]
    fake = FakeNotion()
    assert NM.sweep(conn, cfg, dry_run=True, transport=fake.transport())["pending"] == 0       # every row stamped: nothing pending
    res = NM.sweep(conn, cfg, ids=[395, 396], transport=fake.transport(), pause=0)
    assert res["updated"] == 1 and res["created"] == 1 and not res["failed"]
    assert [p for p, _ in fake.pages("PATCH")] == ["/v1/pages/p395"] and len(fake.pages("POST")) == 1
    assert fake.pages("PATCH")[0][1]["properties"]["Notes"] == {"relation": [{"id": "40ceb306511081c4b511c37681343d3a"}]}
    assert one(conn, "SELECT notion_page_id FROM tasks WHERE id = 396")["notion_page_id"] == "page-1"


class FakeDiscordThreads(FakeDiscord):
    """A server with a forum, an active thread under #general, a forum post and an archived thread."""
    def transport(self):
        inner = super().transport()

        def handler(req: httpx.Request):
            p = req.url.path
            self.calls.append((req.method, p, dict(req.url.params), req.headers.get("Authorization")))
            if p == "/api/v10/guilds/G1/channels":
                return httpx.Response(200, json=[{"id": "CH1", "name": "general", "type": 0, "position": 0, "topic": "t"}, {"id": "CH2", "name": "voice", "type": 2},
                                                 {"id": "F1", "name": "ideas", "type": 15, "position": 2, "topic": "forum"}])
            if p == "/api/v10/guilds/G1/threads/active":
                return httpx.Response(200, json={"threads": [{"id": "T1", "name": "Deploy plan", "type": 11, "parent_id": "CH1", "message_count": 3, "thread_metadata": {"archived": False}},
                                                             {"id": "T2", "name": "Sensor ideas", "type": 11, "parent_id": "F1", "message_count": 1, "thread_metadata": {"archived": False}}]})
            if p == "/api/v10/channels/CH1/threads/archived/public":
                return httpx.Response(200, json={"threads": [{"id": "T0", "name": "Old plan", "type": 11, "parent_id": "CH1", "message_count": 9, "thread_metadata": {"archived": True, "archive_timestamp": "2026-10-01T00:00:00+00:00"}}], "has_more": False})
            if p == "/api/v10/channels/T1":
                return httpx.Response(200, json={"id": "T1", "name": "Deploy plan", "type": 11, "parent_id": "CH1", "guild_id": "G1"})
            if p == "/api/v10/channels/T1/messages":
                return httpx.Response(200, json=[{"id": "M5", "author": {"id": "U9", "username": "max"}, "content": "deploy step two", "timestamp": "2026-11-02T10:00:00.000000+00:00"}])
            if p == "/api/v10/channels/T2/messages":
                return httpx.Response(200, json=[{"id": "M6", "author": {"id": "U9", "username": "max"}, "content": "sensor data idea", "timestamp": "2026-11-02T11:00:00.000000+00:00"}])
            if p == "/api/v10/channels/F1/messages":
                return httpx.Response(400, json={"message": "Cannot execute action on this channel type", "code": 50024})
            return inner.handler(req)
        return httpx.MockTransport(handler)


def test_discord_forums_and_threads_are_listed_read_and_searched(conn, cfg, tmp_path, monkeypatch):
    """Forums (type 15/16) are channels whose posts are threads; discord_threads lists a server's active
    threads (and a channel's archived ones on request), discord_messages reads a thread by id, and
    discord_search scans active threads beside the text channels."""
    from factotum import discord, mcp_server as M
    cfg["sources"]["discord"] = {"enabled": True, "guilds": [{"name": "LARK dev", "id": "G1"}]}
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "bot-token")
    fake = FakeDiscordThreads()
    ctx = M.Context(conn, cfg, run_dir=tmp_path, discord=lambda token: discord.Client(token, transport=fake.transport()))
    chans = json.loads(_text(_rpc(ctx, 1, "tools/call", {"name": "discord_channels", "arguments": {"server": "lark"}})))
    assert [(c["name"], c["kind"]) for c in chans["channels"]] == [("general", "text"), ("ideas", "forum")]
    th = json.loads(_text(_rpc(ctx, 2, "tools/call", {"name": "discord_threads", "arguments": {"server": "lark"}})))
    assert [(t["name"], t["channel"], t["archived"]) for t in th["threads"]] == [("Deploy plan", "general", False), ("Sensor ideas", "ideas", False)]
    th = json.loads(_text(_rpc(ctx, 3, "tools/call", {"name": "discord_threads", "arguments": {"server": "lark", "channel": "general", "archived": True}})))
    assert [t["name"] for t in th["threads"]] == ["Deploy plan", "Old plan"] and th["threads"][1]["id"] == "T0"
    msgs = json.loads(_text(_rpc(ctx, 4, "tools/call", {"name": "discord_messages", "arguments": {"server": "lark", "channel": "T1"}})))
    assert msgs["channel"] == "general" and msgs["thread"] == "Deploy plan" and msgs["messages"][0]["text"] == "deploy step two"
    forum = _rpc(ctx, 5, "tools/call", {"name": "discord_messages", "arguments": {"server": "lark", "channel": "ideas"}})
    assert forum["result"]["isError"] and "discord_threads" in _text(forum)
    hits = json.loads(_text(_rpc(ctx, 6, "tools/call", {"name": "discord_search", "arguments": {"server": "lark", "query": "deploy"}})))
    assert hits["threads_read"] == 2 and sorted(((h["channel"], h.get("thread")) for h in hits["matches"]), key=str) == [("general", "Deploy plan"), ("general", None)]
    hits = json.loads(_text(_rpc(ctx, 7, "tools/call", {"name": "discord_search", "arguments": {"server": "lark", "query": "sensor", "channels": ["ideas"]}})))
    assert [(h["channel"], h["thread"]) for h in hits["matches"]] == [("ideas", "Sensor ideas")]
    assert not any(c[1] == "/api/v10/channels/F1/messages" for c in fake.calls)      # a forum has no messages of its own: never asked


# --- the Slack mention sweep ---------------------------------------------------------------------

class FakeSlackSweep:
    """One workspace: the token's user is U1; #general has a mention naming Task-42, a mention with no
    task, and the user's own message; #papers is quiet; #random is not joined."""
    def __init__(self):
        self.calls = []

    def transport(self):
        def handler(req: httpx.Request):
            method = req.url.path.rsplit("/", 1)[-1]
            params = dict(httpx.QueryParams(req.content.decode()))
            self.calls.append((method, params))
            if method == "auth.test":
                return httpx.Response(200, json={"ok": True, "team": "DiCE Lab", "url": "https://dice.slack.com/", "user": "robert", "user_id": "U1"})
            if method == "conversations.list":
                return httpx.Response(200, json={"ok": True, "channels": [{"id": "C1", "name": "general", "is_member": True}, {"id": "C2", "name": "papers", "is_member": True},
                                                                            {"id": "C3", "name": "random", "is_member": False}], "response_metadata": {"next_cursor": ""}})
            if method == "conversations.history":
                if params["channel"] != "C1" or float(params.get("oldest") or 0) >= 1793536050.0001:
                    return httpx.Response(200, json={"ok": True, "messages": []})
                return httpx.Response(200, json={"ok": True, "messages": [
                    {"ts": "1793536050.000100", "user": "U2", "text": "<@U1> lunch tomorrow?"},
                    {"ts": "1793536000.000100", "user": "U2", "text": "<@U1> can you send the slides for Task-42 by Friday?", "reply_count": 1, "thread_ts": "1793536000.000100"},
                    {"ts": "1793535000.000100", "user": "U1", "text": "<@U2> done"},
                    {"ts": "1793534000.000100", "user": "U3", "text": "no mention here"}]})
            if method == "users.info":
                return httpx.Response(200, json={"ok": True, "user": {"id": params["user"], "real_name": {"U2": "Max M", "U1": "Robert"}.get(params["user"], params["user"])}})
            return httpx.Response(200, json={"ok": False, "error": f"unknown_method {method}"})
        return httpx.MockTransport(handler)


def _sweep_cfg(cfg):
    cfg["sources"]["slack"] = {"enabled": True, "sweep_minutes": 20, "lookback_days": 2, "workspaces": [{"name": "DiCE Lab", "token_env": "SLACK_TOKEN_DICE", "project": "DiCE Lab"}]}
    return cfg


def test_slack_sweep_turns_mentions_into_evidence_and_task_mentions(conn, cfg, monkeypatch):
    from datetime import datetime
    from factotum import lifecycle, slack, slack_sweep
    from factotum.db import get_settings
    from factotum.mail_sweep import review_flags
    _sweep_cfg(cfg)
    monkeypatch.setenv("SLACK_TOKEN_DICE", "xoxp-dice")
    upsert_task(conn, task(id=42, name="Slides for Max", mentions=1, last_mention_at="2026-10-01")); conn.commit()
    fake = FakeSlackSweep()
    now = datetime(2026, 11, 2, 9, 0)
    out = slack_sweep.sweep(conn, cfg, now, clients=lambda tok: slack.Client(tok, transport=fake.transport()))
    assert out and "2 mention" in out and "1 on a task" in out
    assert {c["channel"] for m, c in fake.calls if m == "conversations.history"} == {"C1", "C2"}           # joined channels only
    ev = rows(conn, "SELECT * FROM evidence ORDER BY id")
    assert [(e["kind"], e["task_id"], e["account"]) for e in ev] == [("slack_mention", None, "DiCE Lab"), ("slack_mention", 42, "DiCE Lab")]
    on_task = ev[1]
    assert on_task["ref"] == "https://dice.slack.com/archives/C1/p1793536000000100" and on_task["label"].startswith("#general · Max M") and "@Robert can you send" in on_task["excerpt"]
    t = one(conn, "SELECT mentions, last_mention_at FROM tasks WHERE id = 42")
    assert t["mentions"] == 2 and t["last_mention_at"].startswith("2026-11-01")                             # ts 1793536000 → 2026-11-01T11:06:40Z
    assert one(conn, "SELECT ref, account FROM links WHERE task_id = 42 AND kind = 'slack_message'")["account"] == "DiCE Lab"
    assert review_flags(conn)[42] == {"mentioned": True} and "mentioned" in lifecycle.REVIEW_LABELS
    assert lifecycle.review_reasons(one(conn, "SELECT * FROM tasks WHERE id = 42"), TODAY, cfg, None, {"mentioned": True})[0] == "mentioned"
    assert get_settings(conn)["state.slack_sweep.last_ts.DiCE Lab"] == "1793536050.000100"
    # the next pass starts at the watermark and finds nothing new; the first pass looked back lookback_days
    assert fake.calls[2][1].get("oldest") and float(fake.calls[2][1]["oldest"]) < 1793536000
    assert slack_sweep.sweep(conn, cfg, now, clients=lambda tok: slack.Client(tok, transport=fake.transport())) is None
    assert [c for m, c in fake.calls if m == "conversations.history" and c["channel"] == "C1"][-1]["oldest"] == "1793536050.000100"
    assert len(rows(conn, "SELECT * FROM evidence")) == 2


def test_review_shows_a_slack_mention_as_a_suggestion_and_creates_the_task_from_it(cfg, tmp_path, monkeypatch):
    from datetime import datetime
    from fastapi.testclient import TestClient
    from factotum import slack, slack_sweep
    from factotum.api import create_app
    _sweep_cfg(cfg)
    monkeypatch.setenv("SLACK_TOKEN_DICE", "xoxp-dice")
    app = create_app(cfg, db_path=str(tmp_path / "api.db"))
    conn = app.state.conn
    upsert_task(conn, task(id=42, name="Slides for Max")); conn.commit()
    fake = FakeSlackSweep()
    slack_sweep.sweep(conn, cfg, datetime(2026, 11, 2, 9, 0), clients=lambda tok: slack.Client(tok, transport=fake.transport()))
    client = TestClient(app)
    r = client.get("/api/review").json()
    sg = [s for s in r["suggestions"] if s["kind"] == "slack_mention"]
    assert len(sg) == 1 and sg[0]["fields"]["name"] == "lunch tomorrow?" and sg[0]["label"].startswith("#general · Max M") and sg[0]["fields"]["project"] == "DiCE Lab"
    tid = client.post(f"/api/review/suggestions/{sg[0]['id']}/create", json={}).json()["task_id"]
    t = one(conn, "SELECT * FROM tasks WHERE id = ?", (tid,))
    assert t["source"] == "Slack" and t["project"] == "DiCE Lab" and t["source_link"] == "https://dice.slack.com/archives/C1/p1793536050000100" and t["name"] == "lunch tomorrow?"
    assert "Max M in #general (DiCE Lab)" in t["context"] and one(conn, "SELECT 1 AS x FROM links WHERE task_id = ? AND kind = 'slack_message'", (tid,))
    assert one(conn, "SELECT verdict FROM evidence WHERE id = ?", (sg[0]["id"],))["verdict"] == "applied"
    assert not [s for s in client.get("/api/review").json()["suggestions"] if s["kind"] == "slack_mention"]


def test_scheduler_runs_the_slack_sweep_on_its_interval_when_slack_is_on(conn, cfg):
    from datetime import datetime
    from factotum.scheduler import Scheduler
    from factotum.db import set_setting
    _sweep_cfg(cfg)
    for k in ("notify.ntfy.enabled", "notify.morning_brief.enabled", "sources.canvas.enabled", "sources.mail.enabled", "sources.calendar.enabled", "sinks.notion_tasks_mirror.enabled"):
        set_setting(conn, k, False)
    set_setting(conn, "state.last_run.expire", "2026-11-02"); set_setting(conn, "state.last_run.review_push", "2026-11-02"); conn.commit()
    calls = []

    class FakeSweep:
        @staticmethod
        def sweep_job(c, ecfg, now):
            calls.append(now); return "2 mentions"
    s = Scheduler(cfg, conn, push=lambda *a, **k: None, slack=FakeSweep)
    assert any(d.startswith("slack_sweep") for d in s.tick(datetime(2026, 11, 2, 10, 0)))
    s.tick(datetime(2026, 11, 2, 10, 10)); s.tick(datetime(2026, 11, 2, 10, 21))
    assert len(calls) == 2
    set_setting(conn, "sources.slack.enabled", False); conn.commit()
    s.tick(datetime(2026, 11, 2, 10, 45))
    assert len(calls) == 2
