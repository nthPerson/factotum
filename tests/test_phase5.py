"""Phase 5: `factotum mcp`, the streaming runner, chat turns, the chat API (SPEC.md §8.3, §9)."""
import io
import json
import sqlite3

import httpx
import pytest

from factotum.db import one, rows, upsert_task

from conftest import TODAY, task


# --- T1 the MCP server ------------------------------------------------------------------------

def _rpc(ctx, id_, method, params=None):
    from factotum import mcp_server as M
    msg = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        msg["params"] = params
    return M.dispatch(ctx, msg)


def _text(resp) -> str:
    return "".join(c["text"] for c in resp["result"]["content"] if c["type"] == "text")


def test_mcp_server_speaks_jsonrpc_and_serves_the_task_tools_read_only(conn, cfg, tmp_path):
    from factotum import mcp_server as M
    upsert_task(conn, task(id=5, name="Reply to Max about the session", counterpart="max@x.org", due="2026-11-05", context="c" * 3000))
    upsert_task(conn, task(id=6, name="Old one", status="done"))
    conn.execute("INSERT INTO reminders (task_id, fire_at, label, status, created_by, created_at) VALUES (5, '2026-11-04T09:00', 'ping', 'pending', 'ui', 'now')")
    conn.commit()
    ctx = M.Context(conn, cfg, run_dir=tmp_path)
    init = _rpc(ctx, 1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}})
    assert init["result"]["protocolVersion"] == "2025-06-18" and "tools" in init["result"]["capabilities"] and init["result"]["serverInfo"]["name"] == "factotum"
    assert M.dispatch(ctx, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert _rpc(ctx, 2, "ping")["result"] == {}
    tools = _rpc(ctx, 3, "tools/list")["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"tasks_query", "task_get", "reminders_list", "mail_search", "mail_thread", "mail_message", "mail_draft_reply", "mail_draft_new",
            "calendar_events", "plaud_search", "plaud_summary"} <= names
    assert all(t["inputSchema"]["type"] == "object" and t["description"] for t in tools)
    body = json.loads(_text(_rpc(ctx, 4, "tools/call", {"name": "tasks_query", "arguments": {"q": "max"}})))
    assert [t["id"] for t in body["tasks"]] == [5] and body["tasks"][0]["counterpart"] == "max@x.org" and "context" not in body["tasks"][0]
    body = json.loads(_text(_rpc(ctx, 5, "tools/call", {"name": "tasks_query", "arguments": {"status": "done"}})))
    assert [t["id"] for t in body["tasks"]] == [6]
    got = json.loads(_text(_rpc(ctx, 6, "tools/call", {"name": "task_get", "arguments": {"task_id": 5}})))
    assert got["task"]["name"].startswith("Reply") and len(got["task"]["context"]) < 3000 and got["reminders"][0]["label"] == "ping"
    rem = json.loads(_text(_rpc(ctx, 7, "tools/call", {"name": "reminders_list", "arguments": {}})))
    assert rem["reminders"][0]["task_id"] == 5
    bad = _rpc(ctx, 8, "tools/call", {"name": "nope", "arguments": {}})
    assert bad["result"]["isError"] and "nope" in _text(bad)
    assert _rpc(ctx, 9, "what")["error"]["code"] == -32601
    assert _rpc(ctx, 10, "tools/call", {"name": "task_get", "arguments": {"task_id": "x"}})["result"]["isError"]
    with pytest.raises(sqlite3.OperationalError):          # the server's connection cannot write task state
        ctx.conn.execute("UPDATE tasks SET name = 'x' WHERE id = 5")
    # the stdio loop: one JSON object per line in, one per line out, notifications answered with nothing
    out = io.StringIO()
    M.serve(ctx, io.StringIO('{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}\n'
                             '{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
                             'not json\n'
                             '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'), out)
    lines = [json.loads(l) for l in out.getvalue().splitlines()]
    assert [l.get("id") for l in lines] == [1, None, 2] and lines[1]["error"]["code"] == -32700 and lines[2]["result"]["tools"]
    assert ctx.tool_log == [("tasks_query", True), ("tasks_query", True), ("task_get", True), ("reminders_list", True), ("nope", False), ("task_get", False)]


class FakeMail:
    def __init__(self):
        self.calls, self.writes = [], []

    def call(self, tool, args=None):
        self.calls.append((tool, args))
        if tool == "list_accounts":
            return [{"id": 1, "email": "rashe7414@sdsu.edu", "name": "SDSU"}]
        if tool == "search_conversations":
            return [{"conversationId": "c1", "subject": "PD session", "lastMessageId": 77, "lastSender": "Max", "receivedAtUtc": "2026-10-29T15:03:00Z",
                     "accountId": 1, "messageCount": 2, "preview": "p" * 3000, "folderId": -2, "actualFolders": [1, 2]}]
        if tool == "get_conversation":
            return {"conversationId": "c1", "subject": "PD session", "messages": [{"id": 76, "from": {"address": "max@x.org", "name": "Max"}, "preview": "a", "receivedAtUtc": "2026-10-28T10:00:00Z"},
                                                                                {"id": 77, "from": {"address": "max@x.org", "name": "Max"}, "preview": "b", "receivedAtUtc": "2026-10-29T15:03:00Z"}]}
        if tool == "get_message":
            return {"id": 77, "conversationId": "c1", "subject": "PD session", "from": {"address": "max@x.org", "name": "Max"}, "to": [{"address": "rashe7414@sdsu.edu"}],
                    "receivedAtUtc": "2026-10-29T15:03:00Z", "body": "hello " * 5000, "attachments": [{"name": "a.pdf", "size": 1}]}
        raise AssertionError(tool)

    def call_write(self, tool, args=None):
        self.writes.append((tool, args))
        return {"messageId": 900}


def test_mcp_mail_tools_trim_results_record_drafts_and_seen_threads_and_never_send(conn, cfg, tmp_path):
    from factotum import mcp_server as M
    upsert_task(conn, task(id=9, name="Ask Erica for a block", counterpart="Erica <erica@x.org>")); conn.commit()
    mail = FakeMail()
    ctx = M.Context(conn, cfg, run_dir=tmp_path, mail=lambda: mail)
    r = _rpc(ctx, 1, "tools/call", {"name": "mail_search", "arguments": {"query": "from:max", "account": "rashe7414@sdsu.edu"}})
    res = json.loads(_text(r))
    assert res["conversations"][0]["conversation_id"] == "c1" and res["conversations"][0]["account"] == "rashe7414@sdsu.edu" and res["conversations"][0]["last_message_id"] == 77
    assert len(_text(r)) < 2000 and mail.calls[-1] == ("search_conversations", {"query": "from:max", "accountId": 1, "limit": 10})
    th = json.loads(_text(_rpc(ctx, 2, "tools/call", {"name": "mail_thread", "arguments": {"conversation_id": "c1"}})))
    assert [m["id"] for m in th["messages"]] == [76, 77]
    msg = json.loads(_text(_rpc(ctx, 3, "tools/call", {"name": "mail_message", "arguments": {"message_id": 77}})))
    assert msg["from"] == "Max <max@x.org>" and len(msg["body"]) <= 6100 and msg["truncated"] and msg["attachments"] == ["a.pdf"]
    d = json.loads(_text(_rpc(ctx, 4, "tools/call", {"name": "mail_draft_reply", "arguments": {"conversation_id": "c1", "body": "Thanks Max, Tuesday works."}})))
    assert d["draft_message_id"] == 900 and mail.writes == [("reply_to_conversation", {"conversationId": "c1", "body": "Thanks Max, Tuesday works.", "showInUi": False})]
    d2 = json.loads(_text(_rpc(ctx, 5, "tools/call", {"name": "mail_draft_new", "arguments": {"account": "rashe7414@sdsu.edu", "to": ["erica@x.org"], "subject": "Block", "body": "Hi"}})))
    assert d2["draft_message_id"] == 900 and mail.writes[-1][0] == "create_draft" and mail.writes[-1][1]["accountId"] == 1 and mail.writes[-1][1]["to"] == [{"email": "erica@x.org"}]
    assert _rpc(ctx, 6, "tools/call", {"name": "mail_draft_new", "arguments": {"account": "nobody@x.org", "to": ["erica@x.org"], "subject": "s", "body": "b"}})["result"]["isError"]
    # a stranger (an address no task, account or fetched message names) never gets a draft: injected mail cannot stage exfiltration
    stranger = _rpc(ctx, 8, "tools/call", {"name": "mail_draft_new", "arguments": {"account": "rashe7414@sdsu.edu", "to": ["attacker@evil.example"], "subject": "s", "body": "b"}})
    assert stranger["result"]["isError"] and "known contact" in _text(stranger) and len(mail.writes) == 2
    ok = _rpc(ctx, 9, "tools/call", {"name": "mail_draft_new", "arguments": {"account": "rashe7414@sdsu.edu", "to": ["max@x.org"], "subject": "s", "body": "b"}})   # Max wrote message 77, read above
    assert not ok["result"]["isError"] and len(mail.writes) == 3 and M.seen_threads(tmp_path)["c1"]["people"] == ["max@x.org", "rashe7414@sdsu.edu"]
    drafts = [json.loads(l) for l in (tmp_path / "drafts.jsonl").read_text().splitlines()]
    assert [d["draft_message_id"] for d in drafts] == [900, 900, 900] and drafts[0]["conversation_id"] == "c1" and drafts[0]["account"] == "rashe7414@sdsu.edu" and drafts[1]["to"] == ["erica@x.org"]
    seen = M.seen_threads(tmp_path)
    assert seen["c1"]["subject"] == "PD session" and seen["c1"]["account"] == "rashe7414@sdsu.edu"
    cfg["sources"]["mail"]["enabled"] = False
    off = _rpc(ctx, 7, "tools/call", {"name": "mail_search", "arguments": {"query": "x"}})
    assert off["result"]["isError"] and "Settings" in _text(off)


def test_mailbird_client_write_path_allows_drafts_only(cfg, monkeypatch):
    from factotum import mailbird
    monkeypatch.setenv("MAILBIRD_TOKEN", "tok")
    seen = []

    def handler(req):
        body = json.loads(req.content); seen.append(body.get("params", {}).get("name"))
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"content": [{"type": "text", "text": json.dumps({"messageId": 5})}], "isError": False}})
    c = mailbird.Client(cfg, transport=httpx.MockTransport(handler))
    assert c.call_write("reply_to_conversation", {"conversationId": "c", "body": "b"}) == {"messageId": 5}
    for bad in ("send_message_now", "trash_conversation", "move_conversation", "set_conversation_read", "discard_draft"):
        with pytest.raises(mailbird.MailbirdError):
            c.call_write(bad, {})
    with pytest.raises(mailbird.MailbirdError):
        c.call("create_draft", {})                                               # the read path stays read-only
    assert seen == ["reply_to_conversation"]
    assert mailbird.DRAFT_TOOLS.isdisjoint(mailbird.READ_TOOLS) and "send_message_now" not in mailbird.DRAFT_TOOLS


# --- T2 the streaming runner --------------------------------------------------------------------

STREAM = [
    {"type": "system", "subtype": "init", "session_id": "S1", "mcp_servers": [{"name": "factotum", "status": "connected"}]},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "mcp__factotum__tasks_query", "input": {"q": "max"}}]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "content": "{}"}]}},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "```json\n{\"reply_md\": \"hi\"}\n```"}]}},
    {"type": "result", "subtype": "success", "is_error": False, "result": "```json\n{\"reply_md\": \"hi\"}\n```", "total_cost_usd": 0.21, "duration_ms": 4200, "session_id": "S1"},
]


class FakeProc:
    """What Popen hands back: a stdin to write the prompt to, stream-json lines on stdout."""
    made = []

    def __init__(self, cmd, **kw):
        FakeProc.made.append((cmd, kw))
        FakeProc.last = self
        self.written = []
        self.stdin = type("In", (), {"write": lambda s, t: self.written.append(t), "close": lambda s: None, "flush": lambda s: None})()
        self.stdout = io.StringIO("".join(json.dumps(e) + "\n" for e in kw.pop("_events", STREAM)))
        self.stderr = io.StringIO(kw.pop("_stderr", ""))
        self.returncode = kw.pop("_rc", 0)

    def wait(self, timeout=None):
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        pass


def test_runner_streams_events_resumes_the_session_and_isolates_every_run(cfg, tmp_path):
    from factotum.claude import runner
    FakeProc.made.clear()
    events = []
    res = runner.run(cfg, "chat_turn", "PROMPT", allowed_tools=["mcp__factotum__tasks_query"], workdir=tmp_path, resume="S0", tools="",
                     mcp_servers={"factotum": {"command": "python", "args": ["-m", "factotum", "mcp"]}}, on_event=events.append, popen_=FakeProc)
    cmd, kw = FakeProc.made[0]
    assert res.ok and res.data == {"reply_md": "hi"} and res.cost_usd == 0.21 and res.duration_ms == 4200 and res.session_id == "S1"
    assert cmd[cmd.index("--output-format") + 1] == "stream-json" and "--verbose" in cmd and cmd[cmd.index("--resume") + 1] == "S0"
    assert json.loads(cmd[cmd.index("--mcp-config") + 1])["mcpServers"]["factotum"]["args"] == ["-m", "factotum", "mcp"] and "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--tools") + 1] == "" and cmd[cmd.index("--setting-sources") + 1] == "" and "--disable-slash-commands" in cmd
    assert cmd[cmd.index("--model") + 1] == "sonnet" and cmd[cmd.index("--max-budget-usd") + 1] == "0.6" and "PROMPT" not in cmd
    assert kw["cwd"] == str(tmp_path) and "".join(FakeProc.last.written) == "PROMPT" and kw["env"]["FACTOTUM_RUN_DIR"] == str(tmp_path)
    assert [e["type"] for e in events] == ["system", "assistant", "user", "assistant", "result"]
    # a dead login in the stream is classified like the json path does it
    class Dead(FakeProc):
        def __init__(self, cmd, **kw):
            super().__init__(cmd, _events=[{"type": "result", "subtype": "error", "is_error": True, "result": "Not logged in · Please run /login", "total_cost_usd": 0, "duration_ms": 400, "session_id": "x"}], _rc=1, **kw)
    bad = runner.run(cfg, "chat_turn", "p", on_event=lambda e: None, popen_=Dead)
    assert not bad.ok and bad.auth_failed and bad.error.startswith("auth:")
    # no result event at all (the process died): the stderr tail is the error
    class Mute(FakeProc):
        def __init__(self, cmd, **kw):
            super().__init__(cmd, _events=[], _stderr="boom: segfault", _rc=139, **kw)
    mute = runner.run(cfg, "chat_turn", "p", on_event=lambda e: None, popen_=Mute)
    assert not mute.ok and "segfault" in mute.error and mute.cost_usd == 0.0
    # the json path keeps working and carries the isolation flags and session id too
    from types import SimpleNamespace
    env = json.dumps({"type": "result", "is_error": False, "result": "{\"a\": 1}", "total_cost_usd": 0.01, "duration_ms": 5, "session_id": "J1"})
    plain = runner.run(cfg, "sync_file", "p", allowed_tools=["Read"], exec_=lambda cmd, **kw: SimpleNamespace(returncode=0, stdout=env, stderr="", cmd=cmd))
    assert plain.ok and plain.session_id == "J1"
    c2 = runner.command(cfg, "sync_file", ["Read"])
    assert c2[c2.index("--setting-sources") + 1] == "" and "--disable-slash-commands" in c2 and "--tools" not in c2 and "--resume" not in c2


# --- T3 chat turns ------------------------------------------------------------------------------

def _seed_chat(conn):
    upsert_task(conn, task(id=388, name="Reply to Max about the PD session", project="SCIBER-CT", counterpart="max@x.org", waiting_on=None, context="He asked for availability."))
    upsert_task(conn, task(id=23, name="Submit COMPE 510 Assignment 4", project="COMPE 510", due="2026-11-02T23:59", hard=1))
    conn.commit()


def test_chat_prompt_carries_the_attached_rows_the_rules_and_the_message_as_data(conn, cfg):
    from factotum import chat
    _seed_chat(conn)
    p = chat.build_prompt(cfg, conn, "When did Max last write, and what did he propose?", [388], TODAY)
    assert "Task-388" in p and "max@x.org" in p and "He asked for availability." in p and "Task-23" not in p
    assert "reply_md" in p and "proposals" in p and "mail_search" in p and "link_thread" in p
    assert p.rstrip().endswith("When did Max last write, and what did he propose?") or "When did Max last write" in p.split("## The user's message")[-1]
    canned = chat.build_prompt(cfg, conn, "", [388], TODAY, canned="search_mail")
    assert "mail_search" in canned and "link_thread" in canned and "Task-388" in canned
    later = chat.build_prompt(cfg, conn, "thanks", [], TODAY, first=False)
    assert len(later) < len(p) / 2 and "thanks" in later
    assert set(chat.allowed_tools()) >= {"mcp__factotum__tasks_query", "mcp__factotum__mail_search", "mcp__factotum__mail_draft_reply"}
    srv = chat.mcp_servers(cfg, "/tmp/x")
    assert srv["factotum"]["args"] == ["-m", "factotum", "mcp"] and srv["factotum"]["env"]["FACTOTUM_RUN_DIR"] == "/tmp/x"


def test_chat_turn_stores_the_exchange_validates_proposals_links_seen_threads_and_logs(conn, cfg, tmp_path, monkeypatch):
    from pathlib import Path
    from factotum import chat
    from factotum.claude.runner import RunResult
    _seed_chat(conn)
    monkeypatch.setattr(chat, "RUNS_DIR", tmp_path)
    calls = []

    def fake_runner(cfg, kind, prompt, **kw):
        calls.append(kw)
        wd = Path(kw["workdir"])
        (wd / "seen.jsonl").write_text(json.dumps({"conversation_id": "c1", "subject": "PD session", "account": "rashe7414@sdsu.edu"}) + "\n")
        (wd / "drafts.jsonl").write_text(json.dumps({"draft_message_id": 900, "kind": "reply", "conversation_id": "c1", "account": "rashe7414@sdsu.edu", "subject": "PD session"}) + "\n")
        for ev in STREAM:
            kw["on_event"](ev)
        data = {"reply_md": "Max wrote on **Oct 29** and proposed Tuesday.", "proposals": [
            {"op": "set", "task_id": 388, "fields": {"due": "2026-11-05", "context": "Max proposed Tue"}, "label": "Task-388 → due Thu Nov 5",
             "evidence": [{"kind": "mail_thread", "ref": "c1", "label": "Oct 29 from Max"}, {"kind": "mail_thread", "ref": "zz", "label": "made up"}]},
            {"op": "link_thread", "task_id": 388, "fields": {"conversation_id": "c1"}, "label": "link PD session"},
            {"op": "link_thread", "task_id": 388, "fields": {"conversation_id": "zz"}, "label": "link a thread the run never saw"},
            {"op": "done", "task_id": 9999, "label": "no such task"},
            {"op": "create", "fields": {"name": "Book the room for the PD session", "project": "SCIBER-CT", "due": "2026-11-04"}, "label": "create Book the room"},
            {"op": "set", "task_id": 388, "fields": {"score": 99}, "label": "nothing settable"},
            {"op": "delete", "task_id": 388, "label": "unknown op"},
            {"op": "add_reminder", "task_id": 23, "fields": {"fire_at": "2026-11-02T18:00", "label": "Submit"}, "label": "remind 6 pm"}],
            "drafts": [{"account": "x", "message_id": 123, "to": "y", "subject": "the model's own claim"}]}
        return RunResult(True, data, json.dumps(data), 0.33, 9000, session_id="S1")

    tid = chat.new_thread(conn)
    events = []
    res = chat.turn(conn, cfg, tid, "When did Max write?", [388], today=TODAY, runner=fake_runner, on_event=events.append)
    assert res["ok"] and res["reply_md"].startswith("Max wrote") and res["cost_usd"] == 0.33
    assert [(p["op"], p.get("task_id")) for p in res["proposals"]] == [("set", 388), ("link_thread", 388), ("create", None), ("add_reminder", 23)]
    assert res["proposals"][0]["fields"] == {"due": "2026-11-05", "due_is_datetime": 0, "context": "Max proposed Tue"} and res["proposals"][0]["evidence"] == [{"kind": "mail_thread", "ref": "c1", "label": "Oct 29 from Max"}]
    assert res["proposals"][1]["applied"] is True and one(conn, "SELECT ref, label FROM links WHERE task_id = 388 AND kind = 'mail_thread'") == {"ref": "c1", "label": "PD session"}
    assert res["proposals"][3]["fields"] == {"fire_at": "2026-11-02T18:00", "label": "Submit", "repeat": None}
    assert len(res["rejected"]) == 4 and any("9999" in r for r in res["rejected"])
    assert [d["draft_message_id"] for d in res["drafts"]] == [900]                 # the sidecar, not the model's claim
    msgs = chat.messages(conn, tid)
    assert [m["role"] for m in msgs] == ["user", "assistant"] and msgs[0]["attached"] == [388] and msgs[1]["proposals"][2]["fields"]["name"].startswith("Book")
    th = one(conn, "SELECT * FROM chat_threads WHERE id = ?", (tid,))
    assert th["claude_session_id"] == "S1" and th["title"] == "When did Max write?"
    r = one(conn, "SELECT * FROM runs WHERE kind = 'chat-turn'")
    assert r["ok"] == 1 and r["cost_usd"] == 0.33
    assert events[0]["type"] == "meta" and any(e["type"] == "status" and "tasks_query" in e["text"] for e in events)
    assert calls[0]["tools"] == "" and calls[0]["resume"] is None and "factotum" in calls[0]["mcp_servers"] and set(calls[0]["allowed_tools"]) == set(chat.allowed_tools())
    assert "Task-388" in (tmp_path / f"{tid}-{res['user_message_id']}" / "prompt.md").read_text() and (tmp_path / f"{tid}-{res['user_message_id']}" / "result.txt").exists()
    # the next turn resumes the session with the short prompt
    res2 = chat.turn(conn, cfg, tid, "thanks", [], today=TODAY, runner=lambda cfg, kind, prompt, **kw: (calls.append(dict(kw, prompt=prompt)), RunResult(True, {"reply_md": "ok", "proposals": []}, "{}", 0.1, 100, session_id="S1"))[1])
    assert res2["ok"] and calls[1]["resume"] == "S1" and len(calls[1]["prompt"]) < 1500
    # Create from the stored proposal: an upsert by source_key, so a second click is a no-op
    new_id = chat.create_from_proposal(conn, cfg, res["message_id"], 2, TODAY)
    t = one(conn, "SELECT * FROM tasks WHERE id = ?", (new_id,))
    assert t["name"].startswith("Book the room") and t["source"] == "Chat" and t["source_key"] == f"chat:{res['message_id']}:2" and t["project"] == "SCIBER-CT"
    assert chat.create_from_proposal(conn, cfg, res["message_id"], 2, TODAY) == new_id
    with pytest.raises(ValueError):
        chat.create_from_proposal(conn, cfg, res["message_id"], 0, TODAY)         # not a create


def test_chat_turn_retries_a_dead_session_without_resume_and_records_a_failure(conn, cfg, tmp_path, monkeypatch):
    from factotum import chat
    from factotum.claude.runner import RunResult
    _seed_chat(conn)
    monkeypatch.setattr(chat, "RUNS_DIR", tmp_path)
    tid = chat.new_thread(conn, "old thread")
    conn.execute("UPDATE chat_threads SET claude_session_id = 'OLD' WHERE id = ?", (tid,)); conn.commit()
    conn.execute("INSERT INTO chat_messages (thread_id, role, text, created_at) VALUES (?, 'user', 'first question about Max', 'x')", (tid,))
    conn.execute("INSERT INTO chat_messages (thread_id, role, text, created_at) VALUES (?, 'assistant', 'first answer', 'x')", (tid,)); conn.commit()
    calls = []

    def flaky(cfg, kind, prompt, **kw):
        calls.append((kw.get("resume"), prompt))
        if kw.get("resume"):
            return RunResult(False, None, "No conversation found with session ID: OLD", 0.0, 50, error="claude: No conversation found with session ID: OLD")
        return RunResult(True, {"reply_md": "fresh", "proposals": []}, "{}", 0.2, 800, session_id="NEW")
    res = chat.turn(conn, cfg, tid, "and now?", [], today=TODAY, runner=flaky)
    assert res["ok"] and [c[0] for c in calls] == ["OLD", None] and "first question about Max" in calls[1][1] and "first answer" in calls[1][1]
    assert one(conn, "SELECT claude_session_id FROM chat_threads WHERE id = ?", (tid,))["claude_session_id"] == "NEW"
    bad = chat.turn(conn, cfg, tid, "again", [], today=TODAY, runner=lambda *a, **k: RunResult(False, None, "Sorry, I cannot", 0.05, 300, error="final message has no JSON object: Sorry"))
    assert not bad["ok"] and "no JSON" in bad["error"]
    last = chat.messages(conn, tid)[-1]
    assert last["role"] == "assistant" and last["error"] and one(conn, "SELECT COUNT(*) AS n FROM runs WHERE kind = 'chat-turn' AND ok = 0")["n"] == 1
