"""HTTP API + static UI (SPEC.md §9). Phase 1 surface:

  GET  /api/tasks?status=open|all        tasks with computed score/priority/tier/fade/last-call
  GET  /api/tasks/{id}                   one task with links, reminders, recent changes
  POST /api/commands  {text}             parse the grammar → changes (nothing applied)
  POST /api/batches/apply {source, changes}   the one write path
  POST /api/batches/{id}/undo
  GET  /api/review?exclude=1,2           one page of the review queue + total, each card's pending evidence, planner suggestions
  POST /api/review/suggestions/{id}/create   the task a suggestion describes: a planner create or a Slack mention (evidence judged applied)
  GET  /api/settings · PUT /api/settings  Settings-tab overrides (whitelisted keys), accounts
  GET  /api/stats
  GET  /api/log                          recent batches with their changes, and the Claude runs with cost
  GET  /api/chat/threads · GET /api/chat/threads/{id}      chat history (phase 5, SPEC.md §8.3)
  POST /api/chat/turn {thread_id|null, text, attached, canned}   one turn, streamed as server-sent events
  POST /api/chat/create {message_id, index}                 create the task a proposal describes
  POST /api/chat/drafts/open {draft}                        show a draft the run saved in Mailbird's window
  GET  /api/proposals · POST /api/proposals/{id}/apply|dismiss   batches a skill staged (SPEC.md §15); Apply makes a batch
  POST /mcp                                  `factotum mcp` over HTTP for a remote skill (bearer FACTOTUM_MCP_TOKEN)
"""
from __future__ import annotations

import json
import queue
import threading
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from html import escape
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel

from . import batches, commands, config as C, lifecycle, reminders, scoring
from .db import connect, get_settings, get_task, init_db, rows, seed_accounts, set_setting
from .dates import parse_date

WEB = Path(__file__).resolve().parent / "web"


class ApplyBody(BaseModel):
    source: str = "ui"
    changes: list[dict[str, Any]]


class CommandBody(BaseModel):
    text: str


class SettingsBody(BaseModel):
    values: dict[str, Any] = {}
    accounts: list[dict[str, Any]] | None = None


class TurnBody(BaseModel):
    thread_id: int | None = None
    text: str = ""
    attached: list[int] = []
    canned: str | None = None


class CreateBody(BaseModel):
    message_id: int
    index: int


class DraftOpenBody(BaseModel):
    draft: dict[str, Any]


SUGGESTION_KINDS = ("proposal", "slack_mention")      # evidence with no task that Review offers as a Create / Dismiss card


def _page(title: str, text: str, base: str, undo: bool = False) -> str:
    """Tiny phone-sized confirmation page for a reminder action."""
    back = f"{base}/#today" if base else "/#today"
    hint = ' Undo is in the Log tab.' if undo else ''
    return (f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Factotum</title>'
            f'<body style="font:16px system-ui;margin:0;padding:24px;background:#fafaf7;color:#1a1a1a"><h2 style="margin:0 0 8px">{escape(title)}</h2>'
            f'<p style="color:#555">{escape(text)}{hint}</p><p><a href="{escape(back)}" style="color:#2a6">Open Factotum</a></p></body>')


class JsonOnlyMutations:
    """Every state-changing /api call carries application/json (the UI's helper always sets it). A
    cross-site form or a bare fetch cannot, so a body-less POST (undo, apply a proposal, finish
    review) is never a browser "simple request" that rides on the Access cookie. Pure ASGI, not
    BaseHTTPMiddleware, so the chat's streaming response is untouched."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path", "").startswith("/api/") and scope.get("method") not in ("GET", "HEAD", "OPTIONS"):
            ctype = next((v for k, v in scope.get("headers", []) if k == b"content-type"), b"").decode("latin-1").lower()
            if not ctype.startswith("application/json"):
                await JSONResponse({"detail": "send Content-Type: application/json"}, status_code=415)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_app(cfg: dict | None = None, db_path: str | None = None, chat_runner=None) -> FastAPI:
    cfg = cfg or C.load()
    conn = connect(db_path or C.db_path())
    init_db(conn)
    seed_accounts(conn, C.get(cfg, "sources.mail.seed_accounts", []))
    app = FastAPI(title="Factotum", version="0.1.0")

    def eff_cfg() -> dict:
        return C.effective(cfg, get_settings(conn))

    app.add_middleware(JsonOnlyMutations)

    def decorate(t: dict, today: date, ecfg: dict) -> dict:
        s, pr, reasons = scoring.score(t, today, ecfg)
        t["score"], t["priority"], t["score_reasons"] = s, pr, reasons
        t["tier"] = lifecycle.tier(t)
        t["longterm_effective"] = lifecycle.is_long_term(t, ecfg)
        fa = lifecycle.fade_at(t, ecfg)
        lc = lifecycle.last_call_at(t, ecfg)
        t["fade_at"] = fa.isoformat() if fa else None
        t["last_call_at"] = lc.isoformat() if lc else None
        t["days_until_due"] = (parse_date(t["due"]) - today).days if t.get("due") and parse_date(t["due"]) else None
        return t

    @app.get("/api/tasks")
    def list_tasks(status: str = "open", project: str | None = None):
        today = date.today()
        ecfg = eff_cfg()
        sql = "SELECT * FROM tasks"
        params: tuple = ()
        if status != "all":
            sql += " WHERE status = ?"
            params = (status,)
        if project:
            sql += (" AND" if params else " WHERE") + " project = ?"
            params += (project,)
        out = [decorate(t, today, ecfg) for t in rows(conn, sql + " ORDER BY COALESCE(due, '9999'), id", params)]
        links = rows(conn, "SELECT task_id, kind, ref, label, account FROM links")
        rem = rows(conn, "SELECT task_id, id, fire_at, label, repeat, status FROM reminders WHERE status IN ('pending','snoozed')")
        by_l: dict[int, list] = {}
        for l in links:
            by_l.setdefault(l["task_id"], []).append(l)
        by_r: dict[int, list] = {}
        for r in rem:
            by_r.setdefault(r["task_id"], []).append(r)
        for t in out:
            t["links"] = by_l.get(t["id"], [])
            t["reminders"] = by_r.get(t["id"], [])
        return {"today": today.isoformat(), "tasks": out, "projects": C.project_names(ecfg),
                "top_cap": C.get(ecfg, "lifecycle.top_cap", 7)}

    @app.get("/api/tasks/{task_id}")
    def get_one(task_id: int):
        t = get_task(conn, task_id)
        if not t:
            raise HTTPException(404)
        decorate(t, date.today(), eff_cfg())
        t["links"] = rows(conn, "SELECT * FROM links WHERE task_id = ?", (task_id,))
        t["reminders"] = rows(conn, "SELECT * FROM reminders WHERE task_id = ?", (task_id,))
        t["mentions_log"] = rows(conn, "SELECT * FROM mentions WHERE task_id = ? ORDER BY at DESC LIMIT 20", (task_id,))
        t["changes"] = rows(conn, "SELECT c.*, b.source, b.applied_at FROM changes c JOIN batches b ON b.id = c.batch_id WHERE c.task_id = ? ORDER BY c.id DESC LIMIT 30", (task_id,))
        return t

    @app.post("/api/commands")
    def parse_command(body: CommandBody):
        ch = commands.parse(body.text, date.today())
        if ch is None:
            return JSONResponse({"is_command": False, "changes": []})
        return {"is_command": True, "changes": ch}

    @app.post("/api/batches/apply")
    def apply_batch(body: ApplyBody):
        try:
            return batches.apply(conn, body.changes, body.source, date.today())
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/batches/{batch_id}/undo")
    def undo_batch(batch_id: int):
        try:
            return batches.undo(conn, batch_id)
        except KeyError:
            raise HTTPException(404)

    @app.get("/api/review")
    def review(exclude: str | None = None):
        """One page of the review queue. `exclude` is a comma-separated list of task ids the client
        has already decided on (staged in the tray, not yet applied), so the next page skips them.
        `total` is the whole eligible set after exclusion; `items` is the first `cap` of it."""
        today = date.today()
        ecfg = eff_cfg()
        st = get_settings(conn)
        last = parse_date(st.get("state.last_review_at"))
        skip = {int(x) for x in (exclude or "").split(",") if x.strip().lstrip("-").isdigit()}
        open_tasks = [decorate(t, today, ecfg) for t in rows(conn, "SELECT * FROM tasks WHERE status = 'open'") if t["id"] not in skip]
        from .mail_sweep import review_flags
        flags = review_flags(conn)
        full = lifecycle.build_review_queue(open_tasks, today, ecfg, last, flags_by_id=flags, cap=10**9)
        pending = rows(conn, "SELECT id, task_id, kind, ref, account, label, excerpt, at FROM evidence WHERE judged_at IS NULL ORDER BY id")
        by_task: dict[int, list] = {}
        for e in pending:
            if e["task_id"] is not None:
                change = None
                if e["kind"] == "proposal":
                    try:
                        change = json.loads(e["ref"]) if e["ref"] else None
                    except ValueError:
                        change = None
                by_task.setdefault(int(e["task_id"]), []).append({"id": e["id"], "kind": e["kind"], "label": e["label"], "reason": e["excerpt"], "change": change, "at": e["at"]})
        for i in full:
            i["flags"] = flags.get(i["task"]["id"], {})
            i["evidence"] = by_task.get(i["task"]["id"], [])      # judged in the same batch as the decision; a proposal's change can be accepted
        # Planner creates with no task (an announcement, a hunch) and Slack mentions naming no task:
        # suggestion cards with Create / Dismiss.
        suggestions = [{"id": e["id"], "kind": e["kind"], "label": e["label"], "reason": e["excerpt"], "at": e["at"], "fields": _suggestion_fields(ecfg, e)}
                       for e in pending if e["task_id"] is None and e["kind"] in SUGGESTION_KINDS]
        cap = int(C.get(ecfg, "lifecycle.review_cap", 15))
        page = full[:cap]
        if page:
            # The automations only expire rows Review has shown (lifecycle.expire); this is the record.
            ids = [i["task"]["id"] for i in page]
            conn.execute(f"UPDATE tasks SET last_offered_at = ? WHERE id IN ({','.join('?' * len(ids))})", (today.isoformat(), *ids))
            conn.commit()
            for i in page:
                i["task"]["last_offered_at"] = today.isoformat()
        return {"today": today.isoformat(), "last_review_at": st.get("state.last_review_at"),
                "cap": cap, "total": len(full), "labels": lifecycle.REVIEW_LABELS, "items": page, "suggestions": suggestions}

    def _suggestion_fields(ecfg: dict, e: dict) -> dict:
        """What Create would make: the planner's fields for an uncited create (evidence.ref holds
        them; older rows have only a label), or for a Slack mention the message's text as the name,
        the workspace's project and the permalink as context and source link."""
        if e["kind"] == "slack_mention":
            ws = next((w for w in (C.get(ecfg, "sources.slack.workspaces", []) or []) if str(w.get("name") or "").strip() == (e.get("account") or "")), {})
            name = str(e.get("label") or "").split(": ", 1)[-1].strip()[:80] or "(untitled)"
            return {"name": name, "project": ws.get("project"), "context": f"{e.get('excerpt') or ''}\n{e.get('ref') or ''}".strip(), "source_link": e.get("ref")}
        try:
            d = json.loads(e["ref"]) if e.get("ref") else {}
        except ValueError:
            d = {}
        f = dict((d or {}).get("fields") or {})
        if not f.get("name"):
            f["name"] = str(e.get("label") or "").split(":", 1)[-1].strip()[:80] or "(untitled)"
        return f

    @app.post("/api/review/suggestions/{evidence_id}/create")
    def create_suggestion(evidence_id: int):
        """Create the task a suggestion describes: a planner create (label "create: <name>", the
        planner's reason) or a Slack mention with no task (source Slack, the message linked). An
        upsert by source_key, so a second tap returns the same id; the evidence is judged `applied`.
        Like every creation, not a batch (SPEC §12 precedent)."""
        from .db import add_link, now_iso, upsert_task
        e = rows(conn, f"SELECT * FROM evidence WHERE id = ? AND kind IN ({','.join('?' * len(SUGGESTION_KINDS))}) AND task_id IS NULL", (evidence_id, *SUGGESTION_KINDS))
        if not e:
            raise HTTPException(404, "no such suggestion")
        e = e[0]
        is_slack = e["kind"] == "slack_mention"
        key = f"{'slack-mention' if is_slack else 'planner-suggestion'}:{evidence_id}"
        ex = rows(conn, "SELECT id FROM tasks WHERE source_key = ?", (key,))
        if ex:
            return {"task_id": ex[0]["id"]}
        if e["judged_at"]:
            raise HTTPException(404, "this suggestion was already judged")
        from .sync_file import _clean_task_fields
        ecfg = eff_cfg()
        want = _suggestion_fields(ecfg, e)
        f = _clean_task_fields(ecfg, want)
        now = now_iso()
        tid = upsert_task(conn, dict(f, status="open", source="Slack" if is_slack else "Planner", source_key=key, source_link=want.get("source_link"),
                                     account=e.get("account") if is_slack else None, context=f.get("context") or (e["excerpt"] or None),
                                     mentions=1, last_mention_at=date.today().isoformat(), created_at=now, edited_at=now))
        if is_slack and e.get("ref"):
            add_link(conn, tid, "slack_message", e["ref"], label=str(e.get("label") or "")[:120], account=e.get("account"))
        conn.execute("UPDATE evidence SET judged_at = ?, verdict = 'applied' WHERE id = ?", (now, evidence_id))
        conn.commit()
        return {"task_id": tid}

    @app.post("/api/review/finish")
    def finish_review():
        set_setting(conn, "state.last_review_at", date.today().isoformat())
        conn.commit()
        return {"ok": True}

    @app.get("/api/settings")
    def settings():
        st = get_settings(conn)
        ecfg = C.effective(cfg, st)
        return {
            "config_file": cfg.get("_path"),
            "keys": C.SETTINGS_KEYS,
            "overrides": {k: v for k, v in st.items() if k in C.SETTINGS_KEYS},
            "effective": {k: C.get(ecfg, k) for k in C.SETTINGS_KEYS},
            "projects": C.get(ecfg, "taxonomy.projects", []),
            "accounts": [dict(a, roles=json.loads(a["roles_json"])) for a in rows(conn, "SELECT * FROM accounts ORDER BY id")],
            "sources": {k: {"enabled": C.get(ecfg, f"sources.{k}.enabled")} for k in ("plaud", "canvas", "mail", "calendar")},
            "sinks": {k: {"enabled": C.get(ecfg, f"sinks.{k}.enabled")} for k in ("notion_notes", "notion_tasks_mirror")},
        }

    @app.put("/api/settings")
    def put_settings(body: SettingsBody):
        for k, v in body.values.items():
            if k not in C.SETTINGS_KEYS:
                raise HTTPException(400, f"{k} is not a Settings key; edit {cfg.get('_path')} instead")
            set_setting(conn, k, v)
        if body.accounts is not None:
            conn.execute("DELETE FROM accounts")
            for a in body.accounts:
                if not a.get("address"):
                    continue
                conn.execute("INSERT INTO accounts (address, display, enabled, roles_json) VALUES (?, ?, ?, ?)",
                             (a["address"].strip(), a.get("display"), 1 if a.get("enabled", True) else 0, json.dumps(a.get("roles", []))))
        conn.commit()
        return settings()

    @app.get("/api/stats")
    def stats():
        by = {r["status"]: r["n"] for r in rows(conn, "SELECT status, COUNT(*) n FROM tasks GROUP BY status")}
        cost = rows(conn, "SELECT COALESCE(SUM(cost_usd),0) c FROM runs WHERE started_at >= date('now','-7 days')")[0]["c"]
        return {"by_status": by, "cost_7d_usd": cost}

    @app.get("/api/log")
    def log(limit: int = 40):
        """Recent batches with every field change, task named, plus the Claude runs with their cost."""
        limit = max(1, min(int(limit), 200))
        bs = rows(conn, "SELECT id, created_at, applied_at, undone_at, source FROM batches ORDER BY id DESC LIMIT ?", (limit,))
        if bs:
            ids = tuple(b["id"] for b in bs)
            ch = rows(conn, f"SELECT c.batch_id, c.task_id, t.name AS task_name, c.field, c.old, c.new FROM changes c "
                            f"LEFT JOIN tasks t ON t.id = c.task_id WHERE c.batch_id IN ({','.join('?' * len(ids))}) ORDER BY c.id", ids)
            by: dict[int, list] = {}
            for c in ch:
                by.setdefault(c.pop("batch_id"), []).append(dict(c, old=json.loads(c["old"]) if c["old"] else None, new=json.loads(c["new"]) if c["new"] else None))
            for b in bs:
                b["changes"] = by.get(b["id"], [])
        runs = rows(conn, "SELECT id, kind, started_at, duration_ms, cost_usd, ok, summary, error FROM runs ORDER BY id DESC LIMIT ?", (limit,))
        return {"batches": bs, "runs": runs}

    # ---- Chat (SPEC.md §8.3) -------------------------------------------------------------------
    from . import chat

    @app.get("/api/chat/threads")
    def chat_threads():
        return {"threads": chat.threads(conn)}

    @app.get("/api/chat/threads/{thread_id}")
    def chat_thread(thread_id: int):
        th = rows(conn, "SELECT id, title, created_at FROM chat_threads WHERE id = ?", (thread_id,))
        if not th:
            raise HTTPException(404)
        return {"thread": th[0], "messages": chat.messages(conn, thread_id)}

    @app.post("/api/chat/turn")
    def chat_turn(body: TurnBody):
        """One turn, streamed: `meta`, `status`/`text` events as the run works, then `done` with the
        stored message (or `error`). The run happens on a worker thread; this generator relays."""
        if body.canned is not None and body.canned not in chat.CANNED:
            raise HTTPException(400, f"unknown canned prompt {body.canned!r}")
        thread_id = body.thread_id or chat.new_thread(conn)
        if not rows(conn, "SELECT 1 FROM chat_threads WHERE id = ?", (thread_id,)):
            raise HTTPException(404, "no such thread")
        q: queue.Queue = queue.Queue()
        runner = app.state.chat_runner

        def work():
            try:
                res = chat.turn(conn, eff_cfg(), thread_id, body.text, body.attached, today=date.today(), canned=body.canned, runner=runner, on_event=q.put)
                q.put({"type": "done", "result": res} if res.get("ok") else {"type": "error", "error": res.get("error") or "run failed", "result": res})
            except Exception as e:   # noqa: BLE001 — the stream must end with a reason, never hang
                q.put({"type": "error", "error": f"{type(e).__name__}: {e}"})
            q.put(None)

        threading.Thread(target=work, daemon=True).start()

        def gen():
            while True:
                try:
                    ev = q.get(timeout=20)
                except queue.Empty:
                    yield ": keepalive\n\n"                   # Cloudflare closes idle streams; a comment keeps it open
                    continue
                if ev is None:
                    return
                yield "data: " + json.dumps(ev, default=str) + "\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/chat/create")
    def chat_create(body: CreateBody):
        try:
            return {"task_id": chat.create_from_proposal(conn, eff_cfg(), body.message_id, body.index, date.today())}
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/chat/drafts/open")
    def chat_draft_open(body: DraftOpenBody):
        """Bring a saved draft to the user's eyes in Mailbird: focus its conversation, or show the
        assistant card listing the draft. Reads nothing back; never sends."""
        from . import mailbird
        d = body.draft
        try:
            client = mailbird.Client(eff_cfg())
            if d.get("conversation_id"):
                client.call_write("select_conversation", {"conversationId": str(d["conversation_id"])})
            else:
                acc = {str(a.get("email") or "").lower(): a.get("id") for a in client.call("list_accounts", {}) or []}
                aid = acc.get(str(d.get("account") or "").lower())
                if aid is None or not d.get("draft_message_id"):
                    raise HTTPException(400, "the draft has no conversation and its account is not in Mailbird")
                client.call_write("show_notification", {"title": "Factotum drafted a message", "body": str(d.get("subject") or "")[:200],
                                                        "drafts": [{"accountId": int(aid), "messageId": int(d["draft_message_id"]), "recipientDisplayName": str((d.get("to") or ["?"])[0])[:80], "subject": str(d.get("subject") or "")[:120]}]})
            return {"ok": True}
        except mailbird.MailbirdError as e:
            raise HTTPException(502, str(e))

    # ---- Skills (SPEC.md §15): staged proposals and the HTTP MCP route -----------------------------
    @app.get("/api/proposals")
    def proposals():
        out = []
        for p in rows(conn, "SELECT * FROM proposals WHERE applied_at IS NULL AND dismissed_at IS NULL ORDER BY id"):
            out.append({"id": p["id"], "source": p["source"], "label": p["label"], "created_at": p["created_at"], "changes": json.loads(p["changes_json"] or "[]")})
        return {"proposals": out}

    def _proposal(pid: int) -> dict:
        p = rows(conn, "SELECT * FROM proposals WHERE id = ?", (pid,))
        if not p:
            raise HTTPException(404, "no such proposal")
        if p[0]["applied_at"] or p[0]["dismissed_at"]:
            raise HTTPException(409, "already " + ("applied" if p[0]["applied_at"] else "dismissed"))
        return p[0]

    @app.post("/api/proposals/{pid}/apply")
    def proposal_apply(pid: int):
        """The one write a skill can cause, and only by the user's tap: its staged changes become a
        batch (source `skill`, undoable like any other)."""
        from .db import now_iso
        p = _proposal(pid)
        try:
            res = batches.apply(conn, json.loads(p["changes_json"] or "[]"), "skill", date.today())
        except ValueError as e:
            raise HTTPException(400, str(e))
        conn.execute("UPDATE proposals SET applied_at = ?, batch_id = ? WHERE id = ?", (now_iso(), res["batch_id"], pid))
        conn.commit()
        return res

    @app.post("/api/proposals/{pid}/dismiss")
    def proposal_dismiss(pid: int):
        from .db import now_iso
        _proposal(pid)
        conn.execute("UPDATE proposals SET dismissed_at = ? WHERE id = ?", (now_iso(), pid))
        conn.commit()
        return {"ok": True}

    @app.post("/mcp")
    async def mcp_http(request: Request):
        """`factotum mcp` over streamable HTTP for a skill on another machine: one JSON-RPC message
        (or a batch) per POST, answered as JSON; notifications get 202. Needs `Authorization: Bearer
        $FACTOTUM_MCP_TOKEN`; with no token configured the route does not exist. Each request gets its
        own read-only connection (the Context's query_only pragma must not touch the API's connection)
        and a writer for stage_changes; sidecars go under data/runs/mcp-http/<day>."""
        import hmac
        import os
        from . import mcp_server
        token = os.environ.get("FACTOTUM_MCP_TOKEN") or ""
        if not token:
            raise HTTPException(404)
        auth = request.headers.get("authorization") or ""
        if not (auth.lower().startswith("bearer ") and hmac.compare_digest(auth[7:].strip(), token)):
            raise HTTPException(401, "bad or missing bearer token")
        body = await request.body()
        return await run_in_threadpool(_mcp_handle, body)     # tool calls block (Mailbird, Slack, SQLite): never on the event loop

    def _mcp_handle(body: bytes):
        from . import mcp_server
        try:
            msg = json.loads(body)
        except ValueError:
            return JSONResponse(mcp_server._err(None, -32700, "parse error"))
        db = db_path or C.db_path()
        reader = connect(db)
        ecfg = eff_cfg()
        # Reads and stage_changes by default; a new draft (a leaked token must not fill Mailbird with
        # drafts) and the Plaud subprocess only when server.mcp_http_tools opts in (a list, or "all").
        # A reply draft is never offered here: each request is its own run, so no thread was "seen".
        allowed = C.get(ecfg, "server.mcp_http_tools")
        if allowed == "all":
            allowed = {t["name"] for t in mcp_server.TOOLS}
        elif not isinstance(allowed, list):
            allowed = {t["name"] for t in mcp_server.TOOLS} - {"mail_draft_new", "plaud_search", "plaud_summary"}
        allowed = set(allowed) - {"mail_draft_reply"}
        try:
            ctx = mcp_server.Context(reader, ecfg, run_dir=None, writer=lambda: connect(db), allowed=allowed)
            if isinstance(msg, list):
                out = [r for r in (mcp_server.dispatch(ctx, m) for m in msg) if r is not None]
                return JSONResponse(out) if out else JSONResponse(None, status_code=202)
            resp = mcp_server.dispatch(ctx, msg)
        finally:
            reader.close()
        return JSONResponse(resp) if resp is not None else JSONResponse(None, status_code=202)

    @app.get("/mcp")
    def mcp_http_get():
        raise HTTPException(405, "POST JSON-RPC messages here; no server-initiated stream")

    @app.get("/r/{token}")
    def reminder_action(token: str, a: str = "open"):
        """The three buttons on a reminder push (SPEC.md §10). `view` links, so the phone's Access
        session authorises them; the token is single use for done/snooze and expires after 24 h."""
        base = str(C.get(cfg, "server.public_url", "")).rstrip("/")
        try:
            res = reminders.act(conn, eff_cfg(), token, a)
        except reminders.TokenError as e:
            return HTMLResponse(_page("Factotum", e.why, base), status_code=e.status)
        if res["action"] == "open":
            return RedirectResponse(f"{base}/#today" if base else "/#today")
        what = {"done": "marked done", "snooze": f"snoozed; it fires again at {res.get('fire_at', '')[11:16]}"}[res["action"]]
        return HTMLResponse(_page(f"Task-{res['task_id']} {what}", res["task_name"], base, undo=res["action"] == "done"))

    @app.get("/")
    def index():
        # The page stamps its assets with their mtime and everything says no-cache: Cloudflare caches
        # .js/.css by default and browsers guess freshness when the origin is silent, which left the
        # phone on an old app.js after a deploy.
        html = (WEB / "index.html").read_text(encoding="utf-8")
        for asset in ("app.js", "app.css"):
            html = html.replace(f"/static/{asset}", f"/static/{asset}?v={int((WEB / asset).stat().st_mtime)}")
        return HTMLResponse(html, headers={"Cache-Control": "no-cache, must-revalidate"})

    class NoCacheStatic(StaticFiles):
        async def get_response(self, path, scope):
            r = await super().get_response(path, scope)
            r.headers["Cache-Control"] = "no-cache, must-revalidate"
            return r

    app.mount("/static", NoCacheStatic(directory=str(WEB)), name="static")
    app.state.conn = conn
    app.state.cfg = cfg
    app.state.chat_runner = chat_runner          # tests inject a fake; None means the real headless run
    return app
