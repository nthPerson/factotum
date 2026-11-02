"""Canvas as a source (SPEC.md §6 "Canvas"): planner items with submission state become hard-deadline
tasks; a submitted, graded, excused or ticked-off item closes its task (closed_by `canvas`); a moved
due date moves the task; a row Canvas says is open is never reopened (the user may have finished it
another way). Announcements are kept for the planner's bundle, never turned into tasks here.

    fetch(cfg, token, today)        -> {"items": [raw], "announcements": [{when, project, title, text, url}]}
    normalize(cfg, raw_items)       -> [{key, type, name, title, project, area, context, due, effort, state, dismissed, url, points}]
    sweep(conn, cfg, items, today)  -> summary | None     (one automation batch, source `canvas`)
    sweep_job(conn, cfg, now)       -> what the scheduler calls every sources.canvas.sweep_minutes
"""
from __future__ import annotations

import fnmatch
import html as _html
import json
import os
import re
import sqlite3
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from . import batches, config as C
from .db import add_mention, now_iso, set_setting, upsert_task

CLOSED_STATES = {"submitted", "graded", "excused", "marked_complete"}
VERBS = {"quiz": "Take", "discussion_topic": "Post in", "assignment": "Submit"}
EFFORT_TYPE = {"quiz": "Quiz", "discussion_topic": "Discussion", "assignment": "Assignment"}
ANNOUNCEMENTS_KEY = "state.canvas.announcements"


def _tz(cfg: dict) -> ZoneInfo:
    return ZoneInfo(str(C.get(cfg, "owner.timezone", "UTC")))


def _local(cfg: dict, iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(_tz(cfg)).strftime("%Y-%m-%dT%H:%M")


def strip_html(text: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text or "", flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", _html.unescape(text)).strip()


def project_for(cfg: dict, context_name: str | None) -> str | None:
    ctx = re.sub(r"[\s_]", "", (context_name or "")).upper()
    for key, proj in (C.get(cfg, "sources.canvas.course_map", {}) or {}).items():
        if ctx.startswith(re.sub(r"[\s_]", "", str(key)).upper()):
            return str(proj)
    return None


def _skip_context(cfg: dict, *names) -> bool:
    pats = C.get(cfg, "sources.canvas.skip_context_patterns", []) or []
    return any(fnmatch.fnmatch(n or "", p) for p in pats for n in names if n)


def normalize(cfg: dict, raw_items: list[dict]) -> list[dict]:
    base = str(C.get(cfg, "sources.canvas.base_url", "")).rstrip("/")
    skip = tuple(C.get(cfg, "sources.canvas.skip_title_prefixes", []) or [])
    efforts = C.get(cfg, "sources.canvas.effort_by_type", {}) or {}
    tax = {p.get("name"): p.get("area") for p in (C.get(cfg, "taxonomy.projects", []) or [])}
    out = []
    for it in raw_items:
        ptype = str(it.get("plannable_type") or "item")
        p = it.get("plannable") or {}
        title = (p.get("title") or p.get("name") or "").strip()
        if not title or ptype == "announcement" or (skip and title.startswith(skip)) or _skip_context(cfg, it.get("context_name")):
            continue
        sub = it.get("submissions") if isinstance(it.get("submissions"), dict) else {}
        over = it.get("planner_override") or {}
        state = ("excused" if sub.get("excused") else "graded" if sub.get("graded") else "submitted" if sub.get("submitted")
                 else "marked_complete" if over.get("marked_complete") else "missing" if sub.get("missing") else "open")
        url = it.get("html_url") or ""
        if url.startswith("/"):
            url = base + url
        project = project_for(cfg, it.get("context_name"))
        kind = EFFORT_TYPE.get(ptype, "Assignment")
        if re.search(r"\b(project|term paper|proposal)\b", title, re.I):
            kind = "Project"
        verb = VERBS.get(ptype, "Do")
        name = f"{verb} {project} {title}" if project else f"{verb} {title}"
        pts = p.get("points_possible")
        out.append({"key": f"canvas:{ptype}:{p.get('id')}", "type": ptype, "name": name[:80], "title": title, "project": project or "Unknown",
                    "area": tax.get(project) or "Academics", "context": it.get("context_name") or "", "due": _local(cfg, p.get("due_at") or p.get("todo_date") or it.get("plannable_date")),
                    "effort": str(efforts.get(kind) or "M"), "state": state, "dismissed": bool(over.get("dismissed")), "url": url,
                    "points": pts if isinstance(pts, (int, float)) else None})
    return out


def sweep(conn: sqlite3.Connection, cfg: dict, items: list[dict], today: date) -> str | None:
    created, changes = [], []
    closed = moved = 0
    for it in items:
        row = conn.execute("SELECT id, status, due FROM tasks WHERE source_key = ?", (it["key"],)).fetchone()
        if row:
            tid, status, due = row
            if status != "open":
                continue
            if it["state"] in CLOSED_STATES:
                changes.append({"task_id": tid, "op": "done", "fields": {}}); closed += 1
            elif it["due"] and not it["dismissed"] and (due or "")[:16] != it["due"]:
                changes.append({"task_id": tid, "op": "set", "fields": {"due": it["due"], "due_is_datetime": 1}}); moved += 1
            continue
        if it["dismissed"] or it["state"] in CLOSED_STATES:
            continue
        ctx = it["context"] + (f" · {it['points']:g} pts" if it["points"] is not None else "")
        tid = upsert_task(conn, {"name": it["name"], "project": it["project"], "area": it["area"], "status": "open", "hard": 1, "effort": it["effort"],
                                 "due": it["due"], "due_is_datetime": 1 if it["due"] else 0, "source": "Canvas", "source_key": it["key"], "source_link": it["url"] or None,
                                 "context": ctx, "mentions": 0, "created_at": now_iso(), "edited_at": now_iso()})
        add_mention(conn, tid, "canvas", it["key"])
        created.append(tid)
    if changes:
        batches.apply(conn, changes, "canvas", today, closed_by="canvas")
    conn.commit()
    if not (created or changes):
        return None
    return f"created {len(created)}, closed {closed}, moved {moved}" + (": " + " ".join(f"Task-{t}" for t in created) if created else "")


def _get(client: httpx.Client, base: str, token: str, path: str, params: dict) -> list:
    """GET with Canvas's Link-header pagination."""
    url, out = f"{base}/api/v1/{path}", []
    while url:
        r = client.get(url, headers={"Authorization": f"Bearer {token}"}, params=params)
        r.raise_for_status()
        out += r.json() if isinstance(r.json(), list) else []
        m = re.search(r'<([^>]+)>;\s*rel="next"', r.headers.get("Link", ""))
        url, params = (m.group(1), None) if m else (None, None)
    return out


def fetch(cfg: dict, token: str, today: date, transport=None) -> dict:
    from datetime import timedelta
    base = str(C.get(cfg, "sources.canvas.base_url", "")).rstrip("/")
    start = today - timedelta(days=int(C.get(cfg, "sources.canvas.days_back", 7)))
    end = today + timedelta(days=int(C.get(cfg, "sources.canvas.days_ahead", 90)))
    with httpx.Client(timeout=60, transport=transport) as client:
        items = _get(client, base, token, "planner/items", {"start_date": start.isoformat() + "T00:00:00Z", "end_date": end.isoformat() + "T23:59:59Z", "per_page": 100})
        anns: list[dict] = []
        try:
            courses = [c for c in _get(client, base, token, "courses", {"enrollment_state": "active", "per_page": 50})
                       if c.get("id") and not _skip_context(cfg, c.get("name"), c.get("course_code"))]
            if courses:
                raw = _get(client, base, token, "announcements", {"context_codes[]": [f"course_{c['id']}" for c in courses], "start_date": start.isoformat(), "end_date": end.isoformat(), "per_page": 50})
                names = {f"course_{c['id']}": (c.get("name") or c.get("course_code")) for c in courses}
                for a in raw:
                    ctx = names.get(a.get("context_code"), a.get("context_code"))
                    anns.append({"when": _local(cfg, a.get("posted_at")), "project": project_for(cfg, ctx) or ctx, "title": a.get("title"),
                                 "text": strip_html(a.get("message") or "")[:400], "url": a.get("html_url")})
        except httpx.HTTPError as e:   # announcements are a nicety; the items are the job
            anns.append({"when": None, "project": None, "title": f"(announcements unavailable: {e})", "text": "", "url": None})
    return {"items": items, "announcements": anns}


def sweep_job(conn: sqlite3.Connection, cfg: dict, now: datetime) -> str | None:
    token = os.environ.get("CANVAS_TOKEN")
    if not token:
        raise RuntimeError("CANVAS_TOKEN is not set (.env); turn sources.canvas off in Settings or add the token")
    data = fetch(cfg, token, now.date())
    set_setting(conn, ANNOUNCEMENTS_KEY, json.dumps(data["announcements"][-20:]))
    return sweep(conn, cfg, normalize(cfg, data["items"]), now.date())
