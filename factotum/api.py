"""HTTP API + static UI (SPEC.md §9). Phase 1 surface:

  GET  /api/tasks?status=open|all        tasks with computed score/priority/tier/fade/last-call
  GET  /api/tasks/{id}?before=&limit=    one task, any status: links, reminders, its task links, where it came from, a page of History (stamps neighbours_seen_at)
  POST /api/commands  {text}             parse the grammar → changes (nothing applied)
  POST /api/add {text|fields, key, verb?}  make a task now (one ui batch; the same key twice is one task)
  GET  /api/search?q=&limit=12           any task, open or closed, by number or words
  GET  /api/add/preview?text=&verb=   how an add line will be filed, the tasks it names checked (writes nothing)
  POST /api/batches/apply {source, changes}   the one write path
  POST /api/batches/{id}/undo
  GET  /api/review?exclude=1,2           one page of the review queue + total, each card's pending evidence, planner suggestions
  POST /api/review/suggestions/{id}/create   the task a suggestion card describes, as one undoable review batch (evidence judged applied)
  GET  /api/settings · PUT /api/settings  Settings-tab overrides (whitelisted keys), accounts
  GET  /api/push · POST /api/push/subscribe|unsubscribe|test   this device's notifications: the public key, the devices, a test (stage 2)
  GET  /api/stats
  GET  /api/log                          recent batches with their changes, and the Claude runs with cost
  GET  /api/chat/threads · GET /api/chat/threads/{id}      chat history (phase 5, SPEC.md §8.3)
  POST /api/chat/turn {thread_id|null, text, attached, canned, origin?, task_id?, fresh?}   one turn, streamed as server-sent events; 409 while that thread answers
  GET  /api/chat/place?origin=&task_id=                     the conversation a Review card, an opened task or the Today box continues
  POST /api/chat/create {message_id, index}                 create the task a proposal describes
  POST /api/chat/drafts/open {draft}                        show a draft the run saved in Mailbird's window
  GET  /api/proposals · POST /api/proposals/{id}/apply|dismiss   batches a skill staged (SPEC.md §15); Apply makes a batch
  POST /mcp                                  `factotum mcp` over HTTP for a remote skill (bearer FACTOTUM_MCP_TOKEN or an OAuth access token)
  GET  /.well-known/oauth-protected-resource · /.well-known/oauth-authorization-server   the connector's discovery documents (SPEC.md §15)
  POST /oauth/register · GET|POST /oauth/authorize · POST /oauth/token   registration, the consent page (behind Access), tokens
  GET  /api/connectors · POST /api/connectors/arm · POST /api/connectors/{client_id}/revoke   Settings → Connections
"""
from __future__ import annotations

import hashlib
import json
import re
import queue
import threading
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from html import escape
from urllib.parse import urlparse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel

from .claude import runner as claude_runner
from . import app_push, batches, by_hand, commands, config as C, filing, lifecycle, oauth, relations, reminders, review_cards, review_queue, ripple, search, suggestions, summary
from .db import connect, get_settings, get_task, init_db, now_iso, rows, seed_accounts, set_setting

WEB = Path(__file__).resolve().parent / "web"
PAGE_TABS = ("today", "timeline", "projects", "review", "search", "chat", "log", "settings")
# Page addresses that are not tabs: /new opens Today with New task (the app's long-press shortcut, stage 8).
SHORTCUT_PATHS = ("new",)
STAMPED = ("index.html", "app.js", "app.css", "manifest.webmanifest")


_ID_RE = re.compile(r"-?[0-9]+")


def exclude_ids(raw: str | None) -> set[int]:
    """`exclude=` of the two Review routes: comma-separated task ids, each ASCII digits with at most one
    leading minus; anything else (`²`, `--5`, `+4`, another script's digits) is ignored, never a 500."""
    return {int(s) for s in (x.strip() for x in (raw or "").split(",")) if _ID_RE.fullmatch(s)}


def _mtime(name: str) -> str:
    try:
        return str((WEB / name).stat().st_mtime_ns)
    except OSError:                  # missing mid-deploy: skipped, never a 500 on every /api call
        return "-"


def build_stamp() -> str:
    """Twelve hex digits over the modification times of the files the page is made of; read per call
    (the web files are served from disk, so a JS-only deploy changes it without a restart). A missing
    file counts as missing, so the stamp changes again when it appears."""
    return hashlib.sha1("|".join(_mtime(f) for f in STAMPED).encode()).hexdigest()[:12]


def _index_html() -> str:
    # The page stamps its assets with their mtime and everything says no-cache: Cloudflare caches
    # .js/.css by default and browsers guess freshness when the origin is silent, which left the
    # phone on an old app.js after a deploy.
    html = (WEB / "index.html").read_text(encoding="utf-8")
    for asset in ("app.js", "app.css"):
        html = html.replace(f"/static/{asset}", f"/static/{asset}?v={int((WEB / asset).stat().st_mtime)}")
    return html.replace('<meta name="factotum-build" content="">', f'<meta name="factotum-build" content="{build_stamp()}">')


class ApplyBody(BaseModel):
    source: str = "ui"
    changes: list[dict[str, Any]]


class AddBody(BaseModel):
    text: str | None = None
    fields: dict | None = None
    key: str | None = None
    verb: bool = False


class CommandBody(BaseModel):
    text: str


class SettingsBody(BaseModel):
    values: dict[str, Any] = {}
    accounts: list[dict[str, Any]] | None = None


class SubscribeBody(BaseModel):
    endpoint: str
    keys: dict[str, str]
    label: str = ""
    origin: str


class UnsubscribeBody(BaseModel):
    endpoint: str | None = None
    id: int | None = None


class PushTestBody(BaseModel):
    endpoint: str


class TurnBody(BaseModel):
    thread_id: int | None = None
    text: str = ""
    attached: list[int] = []
    canned: str | None = None
    origin: str | None = None            # a conversation in place (chat.ORIGINS); with no thread_id, the place's thread
    task_id: int | None = None
    fresh: bool = False                  # start a new thread in that place instead of continuing one
    card_id: int | None = None           # origin `suggestion`: the Review suggestion card (its evidence id)
    search: dict | None = None           # origin `search`: the Search tab's parameters, run again here for the prompt
    draft: dict | None = None            # origin `suggestion`: the card's draft as it stands on the page


class CreateBody(BaseModel):
    message_id: int
    index: int


class SuggestionCreateBody(BaseModel):
    fields: dict | None = None           # the card's draft as edited on the page: these values over the card's own
    links: list | None = None            # the links to make instead of the card's: [{kind: part_of | waits_on, other: task id}]


class DraftOpenBody(BaseModel):
    draft: dict[str, Any]


SUGGESTION_KINDS = review_queue.SUGGESTION_KINDS


def _json_value(s: str | None):
    """A `changes.old` / `new` cell parsed; text that is not JSON comes back as it is."""
    if s is None:
        return None
    try:
        return json.loads(s)
    except ValueError:
        return s


def _json_or_none(s: str | None) -> dict | None:
    """A batch's `cause_json` parsed, or None when absent or unreadable."""
    try:
        v = json.loads(s or "")
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


def _consent_page(req: dict, csrf: str, base: str, who: str, tools: int, again: bool, direct: bool = False) -> str:
    """The one human step of the connector flow: what the client is, when it registered, what it
    will be able to do. The name is whatever the client sent; the registration line is ours."""
    from urllib.parse import urlsplit
    cl = req["client"]
    cimd = cl.get("auth_method") == "cimd"
    name = escape(str(cl.get("name") or "a connector"))
    asks = escape(urlsplit(req["client_id"]).netloc) if cimd else f'"{name}"'      # a document is self-asserted: its host is the requester
    hidden = "".join(f'<input type="hidden" name="{k}" value="{escape(str(v))}">' for k, v in
                     (("response_type", "code"), ("client_id", req["client_id"]), ("redirect_uri", req["redirect_uri"]), ("state", req["state"]),
                      ("code_challenge", req["code_challenge"]), ("code_challenge_method", "S256"), ("scope", req["scope"]), ("resource", req["resource"]), ("csrf", csrf)))
    reg = (f"identified by its published document {escape(req['client_id'])} · says it is {name}" if cimd else
           f"registered {escape(str(cl.get('created_at') or '')[:16].replace('T', ' '))} UTC · id {escape(req['client_id'][:8])}… · says it is {name}")
    prov = ("<p class=\"warn\">This connector was approved before; allowing again issues it fresh access.</p>" if again and not cimd else
            "<p class=\"warn\">Approved only because you armed Settings → Connections just now. "
            "If you did not start this from the connector yourself a moment ago, choose <b>Deny</b>: this link may have been made by someone else.</p>")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Factotum · connect</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;margin:0;background:#f6f7f8;color:#111}} main{{max-width:520px;margin:48px auto;padding:24px;background:#fff;border-radius:12px;box-shadow:0 2px 12px rgba(0,0,0,.06)}}
h1{{font-size:20px;margin:0 0 4px}} p{{margin:8px 0}} ul{{margin:8px 0 16px 20px;padding:0}} .who,.reg{{color:#666;font-size:13px}} .warn{{background:#fff7e6;border:1px solid #f0d9a8;border-radius:8px;padding:8px 12px;font-size:14px}}
form{{display:flex;gap:12px;margin-top:20px}} button{{font:inherit;padding:10px 18px;border-radius:8px;border:1px solid #0f766e;background:#0f766e;color:#fff;cursor:pointer}} button.deny{{background:#fff;color:#111;border-color:#bbb}}</style></head>
<body><main><h1>Connect {asks} to Factotum?</h1><p class="reg">{reg}</p>
{prov}
<p>It will be able to, as you:</p>
<ul><li>use {tools} Factotum tools: read your tasks, reminders, mail, calendar, Slack and Discord</li>
<li>{"create and change your tasks directly: each call is one batch you can undo in Factotum's Log. Nobody taps Apply first, so something it read in the same chat (a mail, a Slack message) can steer what it writes; Settings → Connections switches this to staging" if direct else "stage task changes that appear in Factotum's tray for you to Apply or Dismiss — it never changes a task itself"}</li>
<li>nothing is ever sent on your behalf</li></ul>
<p class="who">Signed in as {escape(who)} · revoke any time in Settings → Connections at <a href="{escape(base)}/settings">{escape(base)}</a>.</p>
<form method="post" action="{escape(base)}/oauth/authorize">{hidden}<button type="submit" name="decision" value="deny" class="deny">Deny</button><button type="submit" name="decision" value="allow">Allow</button></form>
</main></body></html>"""


def _form(body: bytes) -> dict:
    """application/x-www-form-urlencoded without the multipart dependency."""
    from urllib.parse import parse_qs
    return {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True).items()}


def _page(title: str, text: str, base: str, undo: bool = False, task_id: int | None = None,
          free: list[dict] | None = None, ask: dict | None = None) -> str:
    """Tiny phone-sized confirmation page for a reminder action. `free` lists the tasks a Done freed
    ("Free now", each a link to its address); `ask` adds the outcome question
    ({"action": url, "hang": k, "ripples": bool}). Both sit between the text and the Open link, Free now first."""
    back = f"{base}/t/{task_id}" if task_id is not None else f"{base}/"
    label = f"Open Task-{task_id}" if task_id is not None else "Open Factotum"
    hint = '<p style="color:#555">Undo is in the Log tab.</p>' if undo else ''      # its own line: the text before it is a task's name
    freed = ""
    if free:
        items = "".join(f'<li><a href="{escape(base)}/t/{f["id"]}" style="color:#2a6">Task-{f["id"]}</a> {escape(f["name"])}</li>' for f in free)
        freed = f'<p style="margin:16px 0 4px;font-weight:600">Free now:</p><ul style="margin:0;padding-left:20px">{items}</ul>'
    form = ""
    if ask:
        k = ask["hang"]
        hangs = "One task hangs on this one." if k == 1 else f"{k} tasks hang on this one."
        tail = (" What you write is what they are told. Leave it empty and they are still brought up to date." if ask.get("ripples")
                else " What you write is kept with this task where they list it.")
        form = (f'<form method="post" action="{escape(ask["action"])}" style="margin:16px 0">'
                '<label for="outcome" style="display:block;font-weight:600;margin-bottom:4px">How did it turn out?</label>'
                f'<p style="color:#555;margin:0 0 8px">{hangs}{tail}</p>'
                '<input id="outcome" name="outcome" maxlength="300" autocomplete="off" style="font:16px system-ui;width:100%;box-sizing:border-box;padding:10px;border:1px solid #ccc;border-radius:6px">'
                '<button style="margin-top:8px;font:16px system-ui;padding:10px 18px;min-height:44px">Save</button></form>')
    return (f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Factotum</title>'
            f'<body style="font:16px system-ui;margin:0;padding:24px;background:#fafaf7;color:#1a1a1a"><h2 style="margin:0 0 8px">{escape(title)}</h2>'
            f'<p style="color:#555">{escape(text)}</p>{hint}{freed}{form}<p><a href="{escape(back)}" style="color:#2a6">{escape(label)}</a></p></body>')


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


class BuildStamp:
    """Adds X-Factotum-Build to every /api/ response so an open page can tell a new deploy. Pure ASGI
    (the chat stream must stay untouched); outermost, so a 415 from JsonOnlyMutations carries it too."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            await self.app(scope, receive, send)
            return
        stamp = build_stamp().encode()

        async def send_with(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message.get("headers", []), (b"x-factotum-build", stamp)]}
            await send(message)
        await self.app(scope, receive, send_with)


def create_app(cfg: dict | None = None, db_path: str | None = None, chat_runner=None) -> FastAPI:
    cfg = cfg or C.load()
    conn = connect(db_path or C.db_path(), shared=True)      # one connection for every request thread: no statement cache (db.connect)
    # Every write on that connection takes turns here, from its first write to its commit or rollback: the
    # request threads share ONE transaction, so one thread's commit committed another's half-written batch (a
    # batch row with its changes and no undo data) and a rollback threw another's work away; Applies then
    # answered 500 for writes that had landed. Every route that writes (batches.apply / undo, a commit, a
    # helper that commits) and the chat turn's worker take it; tests/test_review_fixes.py fails a route that
    # does not. Re-entrant, so a helper may take it again. Never held across a Claude run or a network call
    # (a push, Mailbird): such a path commits, lets go, calls, and takes it again to write what came back.
    # Lock order: turn_lock, then chat._CREATE_LOCK, then by_hand._ADD_LOCK, then commit_lock, always last;
    # nothing takes one of the others while holding commit_lock. The scheduler has its own connection
    # (SQLite's own locking serialises the two) and never takes it.
    commit_lock = threading.RLock()
    init_db(conn)
    if not cfg.get("_starter"):
        seed_accounts(conn, C.get(cfg, "sources.mail.seed_accounts", []))
    app = FastAPI(title="Factotum", version="0.1.0")

    def eff_cfg() -> dict:
        return C.effective(cfg, get_settings(conn))

    app.add_middleware(JsonOnlyMutations)
    app.add_middleware(BuildStamp)

    @app.get("/api/tasks")
    def list_tasks(status: str = "open", project: str | None = None):
        today = date.today()
        ecfg = eff_cfg()
        where, params = [], ()
        if status != "all":
            where.append("status = ?")
            params += (status,)
        if project:
            where.append("project = ?")
            params += (project,)
        cond = " WHERE " + " AND ".join(where) if where else ""
        out = relations.decorate(conn, rows(conn, "SELECT * FROM tasks" + cond + " ORDER BY COALESCE(due, '9999'), id", params), today, ecfg)
        links = rows(conn, f"SELECT task_id, kind, ref, label, account FROM links WHERE kind != 'task' AND task_id IN (SELECT id FROM tasks{cond})", params)
        rem = rows(conn, "SELECT task_id, id, fire_at, label, repeat, status FROM reminders WHERE status IN ('pending','snoozed')")
        by_l: dict[int, list] = {}
        for l in links:
            by_l.setdefault(l["task_id"], []).append(l)
        by_r: dict[int, list] = {}
        for r in rem:
            by_r.setdefault(r["task_id"], []).append(r)
        named: set[int] = set()
        for t in out:
            relations.ship(t)
            t["links"] = by_l.get(t["id"], [])
            t["reminders"] = by_r.get(t["id"], [])
            rel = t.get("rel") or {}
            named.update(x["other"] for x in rel.get("links") or [])
            named.update(x["other"] for x in rel.get("suggestions") or [])
            if (rel.get("changed") or {}).get("by") is not None:
                named.add(rel["changed"]["by"])
        neighbours = relations.briefs(conn, named - {t["id"] for t in out})
        chips = filing.chips(conn, {t["id"] for t in out})
        for t in out:
            if t["id"] in chips:
                t["filing"] = chips[t["id"]]
        return {"today": today.isoformat(), "tasks": out, "neighbours": {str(k): v for k, v in neighbours.items()},
                "projects": C.project_names(ecfg), "top_cap": C.get(ecfg, "lifecycle.top_cap", 7)}

    def history(task_id: int, before: int | None, limit: int) -> dict:
        """One page of the task's History, newest first (plan "GET /api/tasks/{id}"): each change with
        its batch's source, cause and undone flag, its note, how much the batch wrote (for "Undo all 3
        changes made together") and, for a link change, the other task as it is now."""
        es = rows(conn,
                  "SELECT c.id AS change_id, c.batch_id, b.applied_at AS at, b.source, b.cause_json, b.undone_at, c.field, c.old, c.new, c.note, "
                  "g.n AS batch_changes, g.tasks AS batch_tasks FROM changes c JOIN batches b ON b.id = c.batch_id "
                  "JOIN (SELECT batch_id, COUNT(*) AS n, COUNT(DISTINCT task_id) AS tasks FROM changes "
                  "      WHERE batch_id IN (SELECT batch_id FROM changes WHERE task_id = ?) GROUP BY batch_id) g ON g.batch_id = c.batch_id "
                  f"WHERE c.task_id = ?{' AND c.id < ?' if before is not None else ''} ORDER BY c.id DESC LIMIT ?",
                  (task_id, task_id, *((before,) if before is not None else ()), limit + 1))
        more = len(es) > limit
        es = es[:limit]
        for e in es:
            e["cause"] = _json_or_none(e.pop("cause_json"))
            e["undone"] = e.pop("undone_at") is not None
            e["old"], e["new"] = _json_value(e["old"]), _json_value(e["new"])
        ends = {e["change_id"]: next((v["other"] for v in (e["new"], e["old"]) if isinstance(v, dict) and type(v.get("other")) is int), None)
                for e in es if e["field"] == "link"}
        named = {r["id"]: r for r in rows(conn, "SELECT id, name, status FROM tasks WHERE id IN (SELECT value FROM json_each(?))",
                                          (json.dumps(sorted({x for x in ends.values() if x is not None})),))} if ends else {}
        entries = []
        for e in es:                                             # the plan's key order
            entries.append({k: e[k] for k in ("change_id", "batch_id", "at", "source", "cause", "undone", "field", "old", "new", "note")}
                           | {"other": named.get(ends.get(e["change_id"])), "batch_changes": e["batch_changes"], "batch_tasks": e["batch_tasks"]})
        return {"entries": entries, "more": more, "next_before": entries[-1]["change_id"] if more else None}

    @app.get("/api/tasks/{task_id}")
    def get_one(task_id: int, before: int | None = None, limit: int = 30, peek: bool = False):
        t = get_task(conn, task_id)
        if not t:
            raise HTTPException(404)
        limit = max(1, min(int(limit), 100))
        relations.decorate(conn, [t], date.today(), eff_cfg())
        t["rel"] = relations.compact(t.get("rel") or {}, keep_empty=True)
        t["links"] = rows(conn, "SELECT * FROM links WHERE task_id = ? AND kind != 'task'", (task_id,))
        t["reminders"] = rows(conn, "SELECT * FROM reminders WHERE task_id = ?", (task_id,))
        t["mentions_log"] = rows(conn, "SELECT * FROM mentions WHERE task_id = ? ORDER BY at DESC LIMIT 20", (task_id,))
        t["changes"] = rows(conn, "SELECT c.*, b.source, b.applied_at FROM changes c JOIN batches b ON b.id = c.batch_id WHERE c.task_id = ? ORDER BY c.id DESC LIMIT 30", (task_id,))
        t["relations"] = relations.detail(conn, task_id)
        if (fc := filing.chips(conn, {task_id}).get(task_id)):
            t["filing"] = fc
        t["nearby"] = relations.nearby(conn, task_id)
        t["history"] = history(task_id, before, limit)
        # The value before this request (the page marks what changed since), then the stamp that
        # clears the task's "changed" marker: opening a task is looking at its neighbours.
        if peek:                                                 # Review's editor reads the task without having looked at its neighbours
            return t
        with commit_lock:                                        # the read above stays outside
            conn.execute("UPDATE tasks SET neighbours_seen_at = ? WHERE id = ?", (now_iso(), task_id))
            conn.commit()
        return t

    @app.post("/api/commands")
    def parse_command(body: CommandBody):
        ch = commands.parse(body.text, date.today())
        if ch is None:
            return JSONResponse({"is_command": False, "changes": []})
        return {"is_command": True, "changes": ch}

    @app.get("/api/add/preview")
    def add_preview(text: str = "", verb: bool = False):
        return by_hand.preview(conn, eff_cfg(), text, date.today(), verb=verb)

    @app.get("/api/search")
    def search_tasks(q: str = "", limit: int = 12):
        return by_hand.search(conn, q, limit)

    @app.get("/api/search/advanced")
    def search_advanced(q: str = "", mode: str = "match", within: str = "all", status: list[str] = Query(default=[]), project: list[str] = Query(default=[]),
                        source: list[str] = Query(default=[]), effort: list[str] = Query(default=[]), date_field: str = "due",
                        date_from: str | None = None, date_to: str | None = None, flags: list[str] = Query(default=[]), sort: str = "relevance",
                        limit: int = 50, offset: int = 0):
        """The Search tab's search (search.run): words as written or close spellings, a date range, filters, with the
        counts beside each filter. Reads only; `/api/search` above stays the picker's."""
        return search.run(conn, eff_cfg(), date.today(), q=q, mode=mode, within=within, status=status, project=project, source=source, effort=effort,
                          date_field=date_field, date_from=date_from, date_to=date_to, flags=flags, sort=sort, limit=limit, offset=offset)

    @app.post("/api/add")
    def add_task(body: AddBody):
        try:
            res = by_hand.add(conn, eff_cfg(), text=body.text, fields=body.fields, key=body.key, today=date.today(), verb=body.verb, lock=commit_lock)
        except ValueError as e:
            raise HTTPException(400, str(e))
        sch = getattr(app.state, "scheduler", None)
        if not res["already"] and sch is not None:   # a new task is one filing may place (stage 7)
            sch.wake()
        return res

    @app.post("/api/batches/apply")
    def apply_batch(body: ApplyBody):
        if body.source == ripple.RUN_KIND:          # only ripple.apply writes as a ripple: a client's would never queue one, and History would call it a ripple
            raise HTTPException(400, "source 'ripple' is written only by a ripple run")
        changes = [{k: v for k, v in c.items() if k not in ("source_key", "created_source", "source_link", "account", "reminder_status")} for c in body.changes]   # the page never chooses these
        try:
            with chat._CREATE_LOCK, commit_lock:     # a chat create staged here and its Create button never make two
                left_out: list[dict] = []
                changes = chat.resolve_staged(conn, eff_cfg(), changes, left_out)
                if not changes:                      # every entry was a create made already: no empty batch row, no Undo for nothing
                    return {"batch_id": None, "applied": 0, "skipped": left_out, "created": [], "refs": {}, "ripple_queued": []}
                res = batches.apply(conn, changes, body.source, date.today())
                res["skipped"] = left_out + res["skipped"]
                chat.mark_staged(conn, changes, res)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if res.get("created"):                      # a new task is one filing may place (stage 7)
            sch = getattr(app.state, "scheduler", None)
            if sch is not None:
                sch.wake()
        return res

    @app.post("/api/batches/{batch_id}/undo")
    def undo_batch(batch_id: int):
        try:
            with commit_lock:
                return batches.undo(conn, batch_id)
        except batches.NoSuchBatch:
            raise HTTPException(404, "no such batch")
        except batches.BrokenUndo as e:
            raise HTTPException(400, str(e))

    # The Today summary (SPEC §8.4). Its clock is the owner's, like the scheduler's that writes it.
    # `planner` says the morning planner will write one (it runs only inside the brief); with it, `auto`
    # and `can_refresh` all false nothing will ever write one, and the page shows no Summary box.
    @app.get("/api/summary")
    def today_summary():
        ecfg = eff_cfg()
        now = reminders.local_now(ecfg)
        return {"summary": summary.get(conn, now.date()), "refresh": summary.refresh_state(conn, now),
                "auto": bool(C.value(ecfg, "claude.summary.auto")),
                "planner": bool(C.value(ecfg, "notify.morning_brief.enabled") and C.value(ecfg, "claude.planner.enabled")),
                "can_refresh": getattr(app.state, "scheduler", None) is not None}

    @app.post("/api/summary/refresh")
    def refresh_summary():
        """Ask the scheduler thread for a refresh (every enabled sweep, then the planner). The request
        is a settings row it reads on its next tick, and it is woken at once; the API never sweeps
        itself, so there is still one sweeper per database."""
        sch = getattr(app.state, "scheduler", None)
        if sch is None:
            raise HTTPException(409, "this instance runs no scheduler (serve --no-scheduler), so nothing here can sweep")
        now = reminders.local_now(eff_cfg())
        with commit_lock:
            idle = summary.refresh_state(conn, now)["state"] == "idle"
            if idle:
                summary.request_refresh(conn, now)
                conn.commit()
        if idle:
            sch.wake()
        return today_summary()

    @app.get("/api/ripple")
    def ripple_status(after: int | None = None):
        """What ripples wait, whether a scheduler here will run them (with --no-scheduler they wait),
        and the ripple batches that landed after batch `after` (the page polls this after an Apply)."""
        return ripple.status(conn, eff_cfg(), scheduler=getattr(app.state, "scheduler", None) is not None, after=after)

    @app.get("/api/review")
    def review(exclude: str | None = None):
        """One page of the review queue. `exclude` is a comma-separated list of task ids the client
        has already decided on (staged in the tray, not yet applied), so the next page skips them.
        `total` is the whole eligible set after exclusion; `items` is the first `cap` of it.
        Two at once take turns: each writes (the day's suggestions, the offered stamp) and commits on
        the shared connection, and two commits interleaved there answered 500."""
        with commit_lock:
            return _review(exclude)

    @app.get("/api/review/count")
    def review_count(exclude: str | None = None):
        """The number beside Review and on the app's icon (review_queue.count). Reads only; no lock needed."""
        skip = exclude_ids(exclude)
        ecfg = eff_cfg()
        return {"count": review_queue.count(conn, ecfg, review_queue.today(ecfg), exclude=skip)}

    def _review(exclude: str | None):
        ecfg = eff_cfg()
        today = review_queue.today(ecfg)
        st = get_settings(conn)
        skip = exclude_ids(exclude)
        suggestions.session(conn, today, suggestions.per_review(ecfg))   # today's few link suggestions, written before the queue reads them
        conn.commit()
        q = review_queue.items(conn, ecfg, today)
        full = [i for i in q["tasks"] if i["task"]["id"] not in skip]
        cap = int(C.get(ecfg, "lifecycle.review_cap", 15))
        page = full[:cap]
        if page:
            # The automations only expire rows Review has shown (lifecycle.expire); this is the record.
            ids = [i["task"]["id"] for i in page]
            conn.execute(f"UPDATE tasks SET last_offered_at = ? WHERE id IN ({','.join('?' * len(ids))})", (today.isoformat(), *ids))
            conn.commit()
            for i in page:
                i["task"]["last_offered_at"] = today.isoformat()
        # `suggestions`: planner creates with no task (an announcement, a hunch), Slack mentions naming no
        # task, mail and ripple creates: cards with Create / Dismiss (review_cards).
        return {"today": today.isoformat(), "last_review_at": st.get("state.last_review_at"),
                "cap": cap, "total": len(full), "labels": lifecycle.REVIEW_LABELS, "items": page, "suggestions": q["cards"]}

    @app.post("/api/review/suggestions/{evidence_id}/create")
    def create_suggestion(evidence_id: int, body: SuggestionCreateBody | None = None):
        """Create the task a suggestion card describes (a planner create, a Slack mention, a task suggested
        from mail, a ripple's create) as ONE `review` batch: the `create` (Source Planner / Slack / Mail /
        Ripple, keyed by the card so a second tap is the same task), the links the card showed (a rule one
        breaks by now is in `skipped`), a mail card's thread followed (`follow_thread`, seen up to the mail)
        and the card's verdict (`judge_evidence`). Its Undo drops the task, removes its links and brings the
        card back; Create after that Undo reopens the same task in one batch (`reopened`). A Slack card's
        message link is provenance, added beside the batch like Chat's cited threads. Two taps at once take
        turns. Answers {task_id, batch_id (None when nothing was written), skipped: [why], reopened, already}.
        The card is a draft the owner may edit first (2026-11-18): `fields` are the edited values, laid over the
        card's own and cleaned by the same cleaner (a name is needed; a null date removes the date and its hard
        flag), and `links`, when given, are made instead of the card's. Source, key, source link and account stay
        the card's: nothing in the body sets them. What is written is built by `review_cards.card_changes`
        (2026-11-19), the same build a staged Create in the tray applies; this route applies it and adds the post."""
        with commit_lock:
            ecfg = eff_cfg()
            today = review_queue.today(ecfg)
            try:
                plan = review_cards.card_changes(conn, ecfg, evidence_id, body.model_dump() if body else None, today)
            except review_cards.NoCard as ex:
                raise HTTPException(404, str(ex))
            except ValueError as ex:
                raise HTTPException(400, str(ex))
            made = plan.summary
            if not plan.changes:                # a second tap: the same task, nothing written
                return {"task_id": made["task_id"], "batch_id": None, "skipped": [], "reopened": False, "already": True}
            res = batches.apply(conn, plan.changes, "review", today)
            if made["task_id"] is not None:     # Create after Undo (reopened), or a card whose task was closed since (judged only)
                return {"task_id": made["task_id"], "batch_id": res["batch_id"], "skipped": [s["why"] for s in res["skipped"]],
                        "reopened": made["reopened"], "already": made["already"]}
            tid = res["refs"].get(made["ref"])
            if tid is None:
                key = plan.changes[0]["source_key"]
                again = rows(conn, "SELECT id FROM tasks WHERE source_key = ?", (key,))
                if again:                       # made meanwhile by another process: that task is the answer
                    return {"task_id": again[0]["id"], "batch_id": res["batch_id"], "skipped": [], "reopened": False, "already": True}
                raise HTTPException(400, "; ".join(s["why"] for s in res["skipped"]) or "nothing was created")
            from .db import add_link
            for p in plan.post:                 # a Slack card's message link: provenance, beside the batch like Chat's cited threads
                add_link(conn, tid, p["kind"], p["ref"], label=p["label"], account=p["account"])
            if plan.post:
                conn.commit()
            return {"task_id": tid, "batch_id": res["batch_id"], "skipped": [s["why"] for s in res["skipped"]], "reopened": False, "already": False}

    @app.post("/api/review/suggestions/{evidence_id}/open")
    def open_suggestion(evidence_id: int):
        """Show the mail behind a suggestion in Mailbird's window (it selects the conversation; nothing
        is read back, written or sent). Only a conversation a waiting card names can be asked for."""
        from . import mailbird
        e = rows(conn, "SELECT * FROM evidence WHERE id = ? AND kind = 'proposal' AND task_id IS NULL", (evidence_id,))
        mail = review_cards.suggestion_mail(e[0]) if e else None
        if not mail:
            raise HTTPException(404, "no mail behind this suggestion")
        try:
            client = mailbird.Client(eff_cfg())
            try:
                client.call_write("select_conversation", {"conversationId": str(mail["ref"])})
            finally:
                client.close()
        except mailbird.MailbirdError as ex:
            raise HTTPException(502, str(ex))
        return {"ok": True}

    @app.post("/api/review/finish")
    def finish_review():
        with commit_lock:
            set_setting(conn, "state.last_review_at", review_queue.today(eff_cfg()).isoformat())
            conn.commit()
        return {"ok": True}

    @app.get("/api/settings")
    def settings():
        st = get_settings(conn)
        ecfg = C.effective(cfg, st)
        return {
            "config_file": cfg.get("_path"),
            "starter": bool(cfg.get("_starter")),
            "keys": C.SETTINGS_KEYS,
            "overrides": {k: v for k, v in st.items() if k in C.SETTINGS_KEYS},
            "effective": {k: C.value(ecfg, k) for k in C.SETTINGS_KEYS},
            "projects": C.get(ecfg, "taxonomy.projects", []),
            "accounts": [dict(a, roles=json.loads(a["roles_json"])) for a in rows(conn, "SELECT * FROM accounts ORDER BY id")],
            "sources": {k: {"enabled": C.value(ecfg, f"sources.{k}.enabled")} for k in ("plaud", "canvas", "mail", "calendar")},
            "sinks": {k: {"enabled": C.value(ecfg, f"sinks.{k}.enabled")} for k in ("notion_notes", "notion_tasks_mirror")},
            # "missing" / "signed_out" when a ripple or filing run found today that Claude cannot run on this machine
            "claude_unavailable": claude_runner.unavailable_today(conn, date.today()),
        }

    def push_state() -> dict:
        public, error = None, None
        try:
            with commit_lock:                    # the first call makes the key pair and empties the devices
                public = app_push.keys(conn, create=True).public
        except app_push.KeyFileError as e:
            error = str(e)
        hidden = ("p256dh", "auth")
        return {
            "public_key": public,
            "error": error,
            "provider": C.value(eff_cfg(), "notify.provider"),
            # factotum_refused: Factotum would not post to this address now, so a failure on it was its own refusal
            "devices": [{**{k: v for k, v in d.items() if k not in hidden}, "factotum_refused": app_push.endpoint_problem(d["endpoint"], eff_cfg()) is not None}
                        for d in app_push.devices(conn)],
        }

    @app.get("/api/push")
    def push_get():
        return push_state()

    @app.post("/api/push/subscribe")
    def push_subscribe(body: SubscribeBody):
        try:
            with commit_lock:
                app_push.keys(conn, create=True)
        except app_push.KeyFileError as e:
            return JSONResponse({"detail": str(e)}, status_code=409)
        p256dh, auth = body.keys.get("p256dh"), body.keys.get("auth")
        ecfg = eff_cfg()
        with commit_lock:
            try:
                if p256dh is None:
                    raise ValueError("the device key is not a P-256 public key")
                if auth is None:
                    raise ValueError("the device secret must be 16 bytes")
                app_push.subscribe(conn, body.endpoint, p256dh, auth, body.label, body.origin, cfg=ecfg)
            except ValueError as e:
                conn.rollback()
                # the address is one Factotum does not post to (its own list), not a push service's answer: the page says which
                return JSONResponse({"detail": str(e), "factotum_refused": app_push.endpoint_problem(body.endpoint, ecfg) is not None}, status_code=400)
            conn.commit()
        return push_state()

    @app.post("/api/push/unsubscribe")
    def push_unsubscribe(body: UnsubscribeBody):
        if body.endpoint is None and body.id is None:
            return JSONResponse({"detail": "name a device"}, status_code=400)
        with commit_lock:
            app_push.unsubscribe(conn, endpoint=body.endpoint, device_id=body.id)
            conn.commit()
        return push_state()

    @app.post("/api/push/test")
    def push_test(body: PushTestBody):
        out = app_push.send_test(conn, eff_cfg(), body.endpoint, transport=app.state.push_transport, lock=commit_lock)   # the push goes out unlocked
        if out is None:
            return JSONResponse({"detail": "This device is not subscribed"}, status_code=404)
        return out

    @app.put("/api/settings")
    def put_settings(body: SettingsBody):
        bad = [p for p in (C.setting_problem(k, v) for k, v in body.values.items() if k in C.SETTINGS_KEYS) if p]   # all checked before any is written
        if bad:
            raise HTTPException(400, "; ".join(bad))
        for k, v in body.values.items():
            if k not in C.SETTINGS_KEYS:
                if cfg.get("_starter"):
                    raise HTTPException(400, f"{k} is not a Settings key; it belongs in config/factotum.yaml, which does not exist yet: "
                                             "copy config/factotum.example.yaml to config/factotum.yaml, edit it and restart")
                raise HTTPException(400, f"{k} is not a Settings key; edit {cfg.get('_path')} instead")
        with commit_lock:                                    # every key checked above: nothing is written for a refused body
            try:
                for k, v in body.values.items():
                    set_setting(conn, k, v)
                if body.accounts is not None:
                    conn.execute("DELETE FROM accounts")
                    for a in body.accounts:
                        if not a.get("address"):
                            continue
                        conn.execute("INSERT INTO accounts (address, display, enabled, roles_json) VALUES (?, ?, ?, ?)",
                                     (a["address"].strip(), a.get("display"), 1 if a.get("enabled", True) else 0, json.dumps(a.get("roles", []))))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
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
        bs = rows(conn, "SELECT id, created_at, applied_at, undone_at, source, cause_json FROM batches ORDER BY id DESC LIMIT ?", (limit,))
        for b in bs:
            b["cause"] = _json_or_none(b.pop("cause_json"))
        if bs:
            ids = tuple(b["id"] for b in bs)
            ch = rows(conn, f"SELECT c.batch_id, c.task_id, t.name AS task_name, c.field, c.old, c.new, c.note FROM changes c "
                            f"LEFT JOIN tasks t ON t.id = c.task_id WHERE c.batch_id IN ({','.join('?' * len(ids))}) ORDER BY c.id", ids)
            by: dict[int, list] = {}
            for c in ch:
                by.setdefault(c.pop("batch_id"), []).append(dict(c, old=json.loads(c["old"]) if c["old"] else None, new=json.loads(c["new"]) if c["new"] else None))
            for b in bs:
                b["changes"] = by.get(b["id"], [])
        runs = rows(conn, "SELECT id, kind, started_at, duration_ms, cost_usd, ok, summary, error FROM runs ORDER BY id DESC LIMIT ?", (limit,))
        # `more`: whether each list was cut at the limit. The Log's timeline merges the two by time and
        # must stop at the older list's last entry, or its tail would show only one kind.
        more = {"batches": conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0] > len(bs), "runs": conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] > len(runs)}
        return {"batches": bs, "runs": runs, "more": more}

    # ---- Chat (SPEC.md §8.3) -------------------------------------------------------------------
    from . import chat
    turn_lock = app.state.chat_turn_lock = threading.Lock()   # the turn route finds a place's thread and marks it busy under this; a delete checks under it

    @app.get("/api/chat/threads")
    def chat_threads():
        return {"threads": chat.threads(conn)}

    @app.get("/api/chat/threads/{thread_id}")
    def chat_thread(thread_id: int):
        th = rows(conn, "SELECT id, title, created_at FROM chat_threads WHERE id = ?", (thread_id,))
        if not th:
            raise HTTPException(404)
        return {"thread": th[0], "messages": chat.messages(conn, thread_id)}

    @app.get("/api/chat/place")
    def chat_place(origin: str, task_id: int | None = None, card_id: int | None = None):
        """The conversation a place continues (a Review card, an opened task, the Today box, the Search tab's box,
        a suggestion card), with every task its messages read, attached or proposed named; `thread` is None when
        the place has none yet."""
        if origin not in chat.ORIGINS:
            raise HTTPException(400, "unknown origin")
        tid = chat.place_thread(conn, origin, task_id if origin in ("review", "task") else None, card_id=card_id if origin == "suggestion" else None)
        found = rows(conn, "SELECT id, title, origin, task_id, card_id, created_at FROM chat_threads WHERE id = ?", (tid,)) if tid is not None else []
        if not found:                                        # none yet, or deleted between the two reads
            return {"thread": None, "messages": [], "named": {}, "busy": False}
        th = found[0]
        msgs = chat.messages(conn, tid)
        ids: set[int] = set()
        for m in msgs:
            ids |= {i for i in m["read"] + m["attached"] if type(i) is int} | chat.proposal_ids(m["proposals"])
        return {"thread": th, "messages": msgs, "named": chat.named(conn, ids), "busy": tid in app.state.chat_busy}

    @app.delete("/api/chat/threads/{thread_id}")
    def chat_thread_delete(thread_id: int):
        """Remove a thread and its messages (chat history only: nothing a turn applied is undone, and
        the run rows with their cost stay in the Log)."""
        with turn_lock:                                      # a turn that has just found this thread marks it busy under the same lock
            if not rows(conn, "SELECT 1 FROM chat_threads WHERE id = ?", (thread_id,)):
                raise HTTPException(404, "no such thread")
            if thread_id in app.state.chat_busy:             # the turn would end by writing into a thread that is gone
                raise HTTPException(409, "a turn is still running in this thread; delete it when it has answered")
            with commit_lock:
                conn.execute("DELETE FROM chat_messages WHERE thread_id = ?", (thread_id,))
                conn.execute("DELETE FROM chat_threads WHERE id = ?", (thread_id,))
                conn.commit()
        return {"deleted": thread_id}

    @app.post("/api/chat/turn")
    def chat_turn(body: TurnBody):
        """One turn, streamed: `meta`, `status`/`text` events as the run works, then `done` with the
        stored message (or `error`). The run happens on a worker thread; this generator relays."""
        if body.canned is not None and body.canned not in chat.CANNED:
            raise HTTPException(400, f"unknown canned prompt {body.canned!r}")
        with turn_lock:                                      # two quick sends to one place find (or make) one thread, and only one runs
            if body.thread_id:
                thread_id = body.thread_id
                if not rows(conn, "SELECT 1 FROM chat_threads WHERE id = ?", (thread_id,)):
                    raise HTTPException(404, "no such thread")
            elif body.origin is not None:
                if body.origin not in chat.ORIGINS:
                    raise HTTPException(400, "unknown origin")
                of_task = body.origin in ("review", "task")
                task_id = body.task_id if of_task else None
                card_id = body.card_id if body.origin == "suggestion" else None
                if of_task and (task_id is None or not get_task(conn, task_id)):
                    raise HTTPException(404, "no such task")
                if body.origin == "suggestion" and (card_id is None or not review_cards.row(conn, card_id)):
                    raise HTTPException(404, "no such suggestion")
                found = None if body.fresh else chat.place_thread(conn, body.origin, task_id, card_id=card_id)
                if not found:
                    with commit_lock:
                        found = chat.new_thread(conn, None, body.origin, task_id, card_id)
                thread_id = found
            else:
                with commit_lock:
                    thread_id = chat.new_thread(conn)
            if thread_id in app.state.chat_busy:
                raise HTTPException(409, "Claude is still answering in this conversation")
            # What the place adds to the prompt, read before the run: the search as the server runs it (with the first
            # results when nothing is ticked), or the card's draft (with the tasks it links to).
            attached, place_note = list(body.attached), None
            th = rows(conn, "SELECT origin, card_id FROM chat_threads WHERE id = ?", (thread_id,))[0]
            if body.origin != th["origin"]:
                pass                                         # continued from the Chat tab: no search and no card in view there
            elif th["origin"] == "search":
                attached, place_note = chat.search_place(conn, eff_cfg(), date.today(), body.search, attached)
            elif th["origin"] == "suggestion":
                e = review_cards.row(conn, th["card_id"]) if th["card_id"] is not None else None
                if e:
                    ecfg = eff_cfg()
                    draft = body.draft if body.draft is not None else dict(review_cards.suggestion_fields(ecfg, e), links=review_cards.suggestion_links(e))
                    linked, place_note = chat.suggestion_place(conn, ecfg, {"why": e["label"], "reason": e["excerpt"], "mail": review_cards.suggestion_mail(e)}, draft)
                    attached = attached + [i for i in linked if i not in attached]
            app.state.chat_busy.add(thread_id)
        q: queue.Queue = queue.Queue()
        runner = app.state.chat_runner

        def work():
            try:
                res = chat.turn(conn, eff_cfg(), thread_id, body.text, attached, today=date.today(), canned=body.canned, runner=runner, on_event=q.put,
                                 asked=(body.origin, body.task_id),      # the prompt says where it was asked (a card and a task share a thread)
                                 lock=commit_lock,                       # taken for each write, never across the run
                                 place_note=place_note)
                q.put({"type": "done", "result": res} if res.get("ok") else {"type": "error", "error": res.get("error") or "run failed", "result": res})
            except Exception as e:   # noqa: BLE001 — the stream must end with a reason, never hang
                try:
                    with commit_lock:
                        conn.rollback()                      # a failed write must not leave this connection holding the write lock
                except Exception:   # noqa: BLE001
                    pass
                q.put({"type": "error", "error": f"{type(e).__name__}: {e}"})
            finally:
                app.state.chat_busy.discard(thread_id)
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
            tid = chat.create_from_proposal(conn, eff_cfg(), body.message_id, body.index, date.today(), lock=commit_lock)
        except ValueError as e:
            raise HTTPException(400, str(e))
        (m,) = rows(conn, "SELECT proposals_json FROM chat_messages WHERE id = ?", (body.message_id,))
        props = chat._loads(m["proposals_json"], [])
        return {"task_id": tid, "skipped": props[body.index].get("skipped") or []}

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
        """A batch Claude staged (a skill, or the connector) becomes a real one by the user's tap:
        source `skill` or `connector` as staged, undoable like any other; a `create` in it makes its task now."""
        with commit_lock:
            return _proposal_apply(pid)

    def _proposal_apply(pid: int) -> dict:
        from .db import now_iso
        p = _proposal(pid)
        # Claim it first: two quick taps must not apply it twice (a create would make a twin).
        claimed = conn.execute("UPDATE proposals SET applied_at = ? WHERE id = ? AND applied_at IS NULL AND dismissed_at IS NULL", (now_iso(), pid)).rowcount
        conn.commit()
        if claimed != 1:
            raise HTTPException(409, "already applied")
        try:
            res = batches.apply(conn, json.loads(p["changes_json"] or "[]"), p["source"] if p["source"] in ("skill", "connector") else "skill", date.today())
        except Exception as e:
            conn.rollback()
            conn.execute("UPDATE proposals SET applied_at = NULL WHERE id = ?", (pid,))
            conn.commit()
            if isinstance(e, ValueError):
                raise HTTPException(400, str(e))
            raise
        conn.execute("UPDATE proposals SET batch_id = ? WHERE id = ?", (res["batch_id"], pid))
        conn.commit()
        return res

    @app.post("/api/proposals/{pid}/dismiss")
    def proposal_dismiss(pid: int):
        from .db import now_iso
        with commit_lock:
            _proposal(pid)
            conn.execute("UPDATE proposals SET dismissed_at = ? WHERE id = ?", (now_iso(), pid))
            conn.commit()
        return {"ok": True}

    def _oauth_on() -> bool:
        return oauth.enabled(eff_cfg())

    def _oauth_db():
        return connect(db_path or C.db_path())

    def _oauth_json(e: oauth.OAuthError):
        return JSONResponse(e.body(), status_code=e.status, headers={"Cache-Control": "no-store"})

    @app.get("/.well-known/oauth-protected-resource")
    @app.get("/.well-known/oauth-protected-resource/mcp")
    def oauth_prm():
        """RFC 9728: where /mcp's authorization server is (SPEC.md §15). Only while the connector is on."""
        if not _oauth_on():
            raise HTTPException(404)
        return oauth.resource_metadata(eff_cfg())

    @app.get("/.well-known/oauth-authorization-server")
    def oauth_asm():
        if not _oauth_on():
            raise HTTPException(404)
        return oauth.server_metadata(eff_cfg())

    @app.post("/oauth/register", status_code=201)
    async def oauth_register(request: Request):
        """RFC 7591 dynamic registration, restricted to the claude.ai callbacks (oauth.register)."""
        if not _oauth_on():
            raise HTTPException(404)
        try:
            body = json.loads(await request.body() or b"{}")
        except ValueError:
            return _oauth_json(oauth.OAuthError("invalid_client_metadata", "the body must be JSON"))
        w = _oauth_db()
        try:
            return JSONResponse(oauth.register(w, eff_cfg(), body), status_code=201, headers={"Cache-Control": "no-store"})
        except oauth.OAuthError as e:
            return _oauth_json(e)
        finally:
            w.close()

    def _consent(request: Request, params: dict, decision: str | None):
        """GET renders the consent page, POST (with the page's token) answers it. Identity comes from
        Cloudflare Access (oauth.consent_identity); a bad client or redirect never redirects."""
        ecfg = eff_cfg()
        base = oauth.base_url(ecfg)
        w = _oauth_db()
        try:
            try:
                who = oauth.consent_identity(ecfg, request.headers)
            except oauth.OAuthError as e:
                return HTMLResponse(_page("Factotum", e.description, base), status_code=e.status)
            try:
                req = oauth.authorize_request(w, ecfg, params)
            except oauth.OAuthError as e:
                if e.redirect:
                    return RedirectResponse(oauth.error_url(e.redirect, e), status_code=302, headers={"Cache-Control": "no-store"})
                return HTMLResponse(_page("Factotum", f"This connection request is not valid: {e.description}", base), status_code=e.status)
            # form-action must also allow the callback origins: Chrome applies it to the redirect that follows the POST
            origins = " ".join(sorted({f"{u.scheme}://{u.netloc}" for u in (urlparse(str(x)) for x in (C.get(ecfg, "server.oauth.redirect_uris", None) or oauth.DEFAULT_REDIRECTS)) if u.scheme and u.netloc}))
            heads = {"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Content-Security-Policy": f"default-src 'none'; style-src 'unsafe-inline'; form-action 'self' {base} {origins}".strip(),
                     "Referrer-Policy": "no-referrer"}
            again = oauth.granted_before(w, req["client_id"])
            # A link anyone can mint must not be approvable by reflex: a never-granted client needs the owner
            # to have said "I am adding a connector now" in Settings first — and a CIMD client every time,
            # since its client_id is public.
            if (not again or req["client"].get("auth_method") == "cimd") and not oauth.armed(w):
                return HTMLResponse(_page("Factotum was not expecting a new connector",
                                          f"Open Factotum → Settings → Connections, tap \"Allow a new connector\" ({oauth.ARM_MINUTES} minutes), then try again from the app that asked. "
                                          f"If you did not start this, nothing happened: close this page.", base), status_code=403, headers=heads)
            if decision is None:
                from . import mcp_server
                allowed = _mcp_allowed(ecfg)
                direct = str(C.get(ecfg, "server.mcp_writes", "stage")) == "apply" and "apply_changes" in allowed
                n = len([t for t in mcp_server.TOOLS if t["name"] in allowed and (direct or not t.get("apply_only"))])
                return HTMLResponse(_consent_page(req, oauth.consent_token(req), base, who, n, again, direct), headers=heads)
            if not oauth.consent_ok(req, params.get("csrf")):
                return HTMLResponse(_page("Factotum", "This consent page has expired; go back to the connector and try again.", base), status_code=400, headers=heads)
            if decision != "allow":
                return RedirectResponse(oauth.error_url(req, oauth.OAuthError("access_denied", "the user declined")), status_code=302, headers=heads)
            oauth.remember_client(w, req["client"])            # a CIMD client exists for Factotum from the moment it is allowed
            return RedirectResponse(oauth.issue_code(w, ecfg, req), status_code=302, headers=heads)
        finally:
            w.close()

    @app.get("/oauth/authorize")
    def oauth_authorize_get(request: Request):
        if not _oauth_on():
            raise HTTPException(404)
        return _consent(request, dict(request.query_params), None)

    @app.post("/oauth/authorize")
    async def oauth_authorize_post(request: Request):
        if not _oauth_on():
            raise HTTPException(404)
        form = _form(await request.body())
        return _consent(request, form, str(form.get("decision") or "deny"))

    @app.post("/oauth/token")
    async def oauth_token(request: Request):
        """Code → tokens (PKCE verified), or refresh → rotated tokens (oauth.token). Form-encoded,
        the client either public (client_id in the form) or with its secret (form or Basic)."""
        if not _oauth_on():
            raise HTTPException(404)
        form = _form(await request.body())
        auth = request.headers.get("authorization") or ""
        if auth.lower().startswith("basic ") and "client_id" not in form:
            import base64
            try:
                cid, _, secret = base64.b64decode(auth[6:].strip()).decode("utf-8").partition(":")
                form["client_id"], form["client_secret"] = cid, secret
            except (ValueError, UnicodeDecodeError):
                pass
        w = _oauth_db()
        try:
            return JSONResponse(oauth.token(w, eff_cfg(), form), headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
        except oauth.OAuthError as e:
            return _oauth_json(e)
        finally:
            w.close()

    @app.get("/api/connectors")
    def connectors():
        """Settings → Connections: every connector that registered, when it last spoke, live tokens."""
        ecfg = eff_cfg()
        return {"enabled": oauth.enabled(ecfg), "url": oauth.resource(ecfg), "armed_until": oauth.armed(conn), "connectors": oauth.connectors(conn)}

    @app.post("/api/connectors/arm")
    def connectors_arm():
        """'Allow a new connector': for the next few minutes a never-granted client may reach the consent page."""
        with commit_lock:
            return {"armed_until": oauth.arm(conn)}

    @app.post("/api/connectors/{client_id:path}/revoke")
    def connector_revoke(client_id: str):                   # a CIMD client_id is a URL, slashes and all
        with commit_lock:
            revoked = oauth.revoke_client(conn, client_id)
        if not revoked:
            raise HTTPException(404, "no such connector, or already revoked")
        return {"ok": True}

    def _oauth_ok(bearer: str) -> bool:
        w = _oauth_db()
        try:
            return oauth.check_access(w, bearer) is not None
        finally:
            w.close()

    @app.post("/mcp")
    async def mcp_http(request: Request):
        """`factotum mcp` over streamable HTTP for a skill on another machine or a claude.ai
        connector: one JSON-RPC message (or a batch) per POST, answered as JSON; notifications get
        202. The bearer is either `$FACTOTUM_MCP_TOKEN` or an OAuth access token this server issued
        (SPEC.md §15); with neither configured the route does not exist, and a bad token gets 401 with
        the metadata pointer a connector needs. Each request gets its own read-only connection (the
        Context's query_only pragma must not touch the API's connection) and a writer for stage_changes."""
        import hmac
        import os
        token = os.environ.get("FACTOTUM_MCP_TOKEN") or ""
        oa = _oauth_on()
        if not token and not oa:
            raise HTTPException(404)
        auth = request.headers.get("authorization") or ""
        bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        try:
            ok = bool(token and bearer and hmac.compare_digest(bearer, token))
        except TypeError:                                    # a non-ASCII header is just a wrong token
            ok = False
        if not ok and oa and bearer:
            ok = await run_in_threadpool(_oauth_ok, bearer)
        if not ok:
            www = 'Bearer realm="factotum"'
            if oa:
                www += f', resource_metadata="{oauth.base_url(eff_cfg())}/.well-known/oauth-protected-resource"'
            if bearer:
                www += ', error="invalid_token"'
            return JSONResponse({"detail": "bad or missing bearer token"}, status_code=401, headers={"WWW-Authenticate": www})
        body = await request.body()
        return await run_in_threadpool(_mcp_handle, body)     # tool calls block (Mailbird, Slack, SQLite): never on the event loop

    def _mcp_allowed(ecfg: dict) -> set:
        """What the HTTP route (a remote skill or a connector) may call. Reads and stage_changes by
        default; a new draft (a leaked token must not fill Mailbird with drafts) and the Plaud
        subprocess only when server.mcp_http_tools opts in (a list, or "all"). A reply draft is never
        offered here: each request is its own run, so no thread was "seen"."""
        from . import mcp_server
        allowed = C.get(ecfg, "server.mcp_http_tools")
        if allowed == "all":
            allowed = {t["name"] for t in mcp_server.TOOLS}
        elif not isinstance(allowed, list):
            allowed = {t["name"] for t in mcp_server.TOOLS} - {"mail_draft_new", "plaud_search", "plaud_summary"}
        return set(allowed) - {"mail_draft_reply"}

    def _mcp_handle(body: bytes):
        from . import mcp_server
        try:
            msg = json.loads(body)
        except ValueError:
            return JSONResponse(mcp_server._err(None, -32700, "parse error"))
        db = db_path or C.db_path()
        reader = connect(db)
        ecfg = eff_cfg()
        allowed = _mcp_allowed(ecfg)
        try:
            ctx = mcp_server.Context(reader, ecfg, run_dir=None, writer=lambda: connect(db), allowed=allowed,
                                     apply_writes=str(C.get(ecfg, "server.mcp_writes", "stage")) == "apply", source="connector")
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
            with commit_lock:                    # a GET that writes: Done and Snooze commit on the shared connection
                res = reminders.act(conn, eff_cfg(), token, a)
        except reminders.TokenError as e:
            return HTMLResponse(_page("Factotum", e.why, base), status_code=e.status)
        if res["action"] == "open":
            return RedirectResponse(f"{base}/t/{res['task_id']}")
        what = {"done": "marked done", "snooze": f"snoozed; it fires again at {res.get('fire_at', '')[11:16]}"}[res["action"]]
        free = ask = None
        if res["action"] == "done":
            free = relations.freed_by(conn, res["task_id"])           # tasks that waited only on this one (stage 4b)
            k = reminders.hanging_on(conn, res["task_id"])            # open tasks that wait on it, its open goal: the outcome question (stage 5)
            if k > 0:
                ask = {"action": f"{base}/r/{token}/outcome", "hang": k, "ripples": bool(C.value(eff_cfg(), "claude.ripple.enabled"))}
        return HTMLResponse(_page(f"Task-{res['task_id']} {what}", res["task_name"], base, undo=bool(res.get("batch_id")), task_id=res["task_id"],
                                  free=free, ask=ask))

    @app.post("/r/{token}/outcome")
    def reminder_outcome(token: str, form: bytes = Body(b"")):
        """The optional outcome line after Done. Urlencoded form post outside /api; only a token whose
        reminder was closed from its link (and is still inside the 24 h) is accepted. A plain def (the raw
        body as bytes), so its batch runs on the thread pool under commit_lock, never on the event loop."""
        base = str(C.get(cfg, "server.public_url", "")).rstrip("/")
        text = _form(form).get("outcome", "")
        try:
            with commit_lock:
                res = reminders.save_outcome(conn, eff_cfg(), token, text)
        except reminders.TokenError as e:
            return HTMLResponse(_page("Factotum", e.why, base), status_code=e.status)
        if res.get("already"):                      # the form is used once: a second POST writes nothing and queues nothing
            return HTMLResponse(_page(f"Task-{res['task_id']}: nothing saved", "The outcome is already saved; change it in the task.", base, task_id=res["task_id"]))
        if not res["saved"]:
            return HTMLResponse(_page(f"Task-{res['task_id']}: nothing saved", res["task_name"], base, task_id=res["task_id"]))
        return HTMLResponse(_page(f"Task-{res['task_id']}: outcome saved", f"How it turned out: \u201c{res['outcome']}\u201d", base, undo=True, task_id=res["task_id"]))

    def index():
        return HTMLResponse(_index_html(), headers={"Cache-Control": "no-cache, must-revalidate"})

    # One explicit path per tab, never a `/{tab}` pattern (it would shadow /mcp).
    app.add_api_route("/", index, methods=["GET"], include_in_schema=False)
    for _tab in PAGE_TABS + SHORTCUT_PATHS:
        app.add_api_route(f"/{_tab}", index, methods=["GET"], include_in_schema=False)
    app.add_api_route("/t/{task_id:int}", lambda task_id: index(), methods=["GET"], include_in_schema=False)

    # The notifications-only service worker, at the root so its scope is the whole app; read from disk
    # per request and never cached unchecked, so a new version is active at once. Not in STAMPED: a
    # worker change must not reload open pages (the browser re-checks the worker itself).
    def service_worker():
        return Response((WEB / "sw.js").read_bytes(), media_type="text/javascript; charset=utf-8",
                        headers={"Cache-Control": "no-cache, must-revalidate"})

    app.add_api_route("/sw.js", service_worker, methods=["GET"], include_in_schema=False)

    class NoCacheStatic(StaticFiles):
        async def get_response(self, path, scope):
            r = await super().get_response(path, scope)
            r.headers["Cache-Control"] = "no-cache, must-revalidate"
            if path.endswith(".webmanifest"):        # the host's mime table decides otherwise
                r.headers["content-type"] = "application/manifest+json"
            return r

    app.mount("/static", NoCacheStatic(directory=str(WEB)), name="static")
    app.state.conn = conn
    app.state.push_transport = None              # tests set an httpx.MockTransport for the push services
    app.state.chat_busy = set()                  # threads with a turn in flight (they cannot be deleted meanwhile)
    app.state.cfg = cfg
    app.state.chat_runner = chat_runner          # tests inject a fake; None means the real headless run
    return app
