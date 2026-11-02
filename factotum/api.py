"""HTTP API + static UI (SPEC.md §9). Phase 1 surface:

  GET  /api/tasks?status=open|all        tasks with computed score/priority/tier/fade/last-call
  GET  /api/tasks/{id}                   one task with links, reminders, recent changes
  POST /api/commands  {text}             parse the grammar → changes (nothing applied)
  POST /api/batches/apply {source, changes}   the one write path
  POST /api/batches/{id}/undo
  GET  /api/review?exclude=1,2           one page of the review queue + total (decisions go through batches)
  GET  /api/settings · PUT /api/settings  Settings-tab overrides (whitelisted keys), accounts
  GET  /api/stats
  GET  /api/log                          recent batches with their changes, and the Claude runs with cost
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from html import escape
from fastapi.staticfiles import StaticFiles
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


def _page(title: str, text: str, base: str, undo: bool = False) -> str:
    """Tiny phone-sized confirmation page for a reminder action."""
    back = f"{base}/#today" if base else "/#today"
    hint = ' Undo is in the Log tab.' if undo else ''
    return (f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Factotum</title>'
            f'<body style="font:16px system-ui;margin:0;padding:24px;background:#fafaf7;color:#1a1a1a"><h2 style="margin:0 0 8px">{escape(title)}</h2>'
            f'<p style="color:#555">{escape(text)}{hint}</p><p><a href="{escape(back)}" style="color:#2a6">Open Factotum</a></p></body>')


def create_app(cfg: dict | None = None, db_path: str | None = None) -> FastAPI:
    cfg = cfg or C.load()
    conn = connect(db_path or C.db_path())
    init_db(conn)
    seed_accounts(conn, C.get(cfg, "sources.mail.seed_accounts", []))
    app = FastAPI(title="Factotum", version="0.1.0")

    def eff_cfg() -> dict:
        return C.effective(cfg, get_settings(conn))

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
        full = lifecycle.build_review_queue(open_tasks, today, ecfg, last, flags_by_id=review_flags(conn), cap=10**9)
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
                "cap": cap, "total": len(full), "labels": lifecycle.REVIEW_LABELS, "items": page}

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
    return app
