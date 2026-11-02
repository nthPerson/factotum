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
    assert p["Due"] == {"date": {"start": "2026-11-05T14:00"}} and p["Hard deadline"] == {"checkbox": True} and p["Completed"] == {"date": None}
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
    # --all re-pushes every row
    fake2 = FakeNotion()
    res4 = NM.sweep(conn, cfg, all_rows=True, transport=fake2.transport())
    assert res4["updated"] == 4 and res4["failed"] == [] and one(conn, "SELECT notion_page_id FROM tasks WHERE id = 1")["notion_page_id"] == "page-old"


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
