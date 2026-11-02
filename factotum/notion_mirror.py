"""The Notion tasks mirror (SPEC.md §7 "Notion tasks mirror"): a one-way upsert of every task row
into the Notion Tasks database, so the dashboards and relations there keep showing history.
Nothing is ever read back; the property names come from `sinks.notion_tasks_mirror.properties`
(the same map the importer reads) and the property TYPES from the database's own schema, fetched
once per pass, so a renamed or retyped column is a YAML edit or nothing at all.

    schema(cfg)                                 -> {property name: type}
    properties(cfg, task, schema, note_ids, today) -> the Notion `properties` payload
    sweep(conn, cfg, *, all_rows=False, dry_run=False, cap=60) -> {created, updated, failed, pending}
    sweep_job(conn, cfg, now_local)             -> what the scheduler calls

Which rows: every row with no `notion_page_id` yet, plus every row edited since the watermark
(`state.mirror.watermark`, the edited_at of the last row pushed). The page id a create returns is
stored on the row without touching edited_at, so a mirror pass never schedules itself again.
Read-only columns in Notion (unique_id, created_time, formulas, rollups) are skipped: "Task ID" in
Notion is Notion's own counter and only matches Factotum's id for rows that came from the import.
Notion allows ~3 requests a second; the scheduler pass is capped and the CLI loops with a pause.
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from datetime import date, datetime

import httpx

from . import config as C, scoring
from .db import get_settings, rows, set_setting

NOTION = "https://api.notion.com/v1"
TEXT_CAP = 2000
DEFAULT_PROPS = {"title": "Name", "task_id": "Task ID", "status": "Status", "project": "Project", "area": "Area", "due": "Due",
                 "hard": "Hard deadline", "effort": "Effort", "source": "Source", "source_key": "Source Key", "source_link": "Source Link",
                 "context": "Context", "why_now": "Why now", "score": "Score", "priority": "Priority", "mentions": "Mentions",
                 "completed": "Completed", "waiting_on": "Waiting On", "calendar_event": "Calendar Event", "notes_relation": "Notes",
                 "snoozed": "Snoozed Until", "manual_priority": "Manual Priority"}
DEFAULT_STATUS_MAP = {"open": "Next", "done": "Done", "dropped": "Dropped", "faded": "Dropped", "lapsed": "Dropped"}
READ_ONLY = {"unique_id", "created_time", "created_by", "last_edited_time", "last_edited_by", "formula", "rollup", "button", "verification"}
_HEX32 = re.compile(r"([0-9a-f]{32})$")
PAUSE_S = 0.35


class MirrorError(Exception):
    pass


def _headers(version: str) -> dict:
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        raise MirrorError("NOTION_TOKEN is not set (docs/SETUP.md §5)")
    return {"Authorization": f"Bearer {token}", "Notion-Version": version, "Content-Type": "application/json"}


def props_map(cfg: dict) -> dict:
    return dict(DEFAULT_PROPS, **(C.get(cfg, "sinks.notion_tasks_mirror.properties", {}) or {}))


def schema(cfg: dict, transport=None) -> dict[str, str]:
    """{property name: type} from the data source (2025-09 API) or the database (older API)."""
    ds = C.get(cfg, "sinks.notion_tasks_mirror.data_source_id")
    db = C.get(cfg, "sinks.notion_tasks_mirror.database_id")
    attempts = [(f"{NOTION}/data_sources/{ds}", "2025-09-03")] if ds else []
    if db:
        attempts.append((f"{NOTION}/databases/{db}", "2022-06-28"))
    last = "no database_id or data_source_id under sinks.notion_tasks_mirror"
    with httpx.Client(timeout=60, transport=transport) as client:
        for url, version in attempts:
            r = client.get(url, headers=_headers(version))
            if r.status_code == 200:
                return {name: p.get("type", "") for name, p in (r.json().get("properties") or {}).items()}
            last = f"{url} → {r.status_code} {r.text[:200]}"
    raise MirrorError(f"Notion schema fetch failed: {last}")


def note_ids(conn: sqlite3.Connection, task_id: int) -> list[str]:
    """Notion page ids of the task's note pages: `plaud_note` links hold the id, `note_page` links the URL."""
    out: list[str] = []
    for l in rows(conn, "SELECT kind, ref FROM links WHERE task_id = ? AND kind IN ('plaud_note', 'note_page') ORDER BY id", (task_id,)):
        m = _HEX32.search(str(l["ref"] or "").split("?")[0].split("#")[0].rstrip("/").replace("-", "").lower())
        if m and m.group(1) not in out:               # the id ENDS a Notion URL; the title before it may contain hex too
            out.append(m.group(1))
    return out


def _text(v) -> list[dict]:
    s = str(v or "")
    return [{"type": "text", "text": {"content": s[i:i + TEXT_CAP]}} for i in range(0, min(len(s), TEXT_CAP * 5), TEXT_CAP)] if s else []


def _value(ptype: str, v) -> dict | None:
    """One property payload for a Notion type, or None when the type cannot carry the value."""
    if ptype == "title":
        return {"title": _text(v)[:1]}
    if ptype == "rich_text":
        return {"rich_text": _text(v)}
    if ptype == "select":
        return {"select": {"name": str(v)[:100]} if v not in (None, "") else None}
    if ptype == "status":
        return {"status": {"name": str(v)[:100]}} if v not in (None, "") else None
    if ptype == "multi_select":
        vals = v if isinstance(v, list) else ([v] if v not in (None, "") else [])
        return {"multi_select": [{"name": str(x)[:100]} for x in vals]}
    if ptype == "date":
        return {"date": {"start": str(v)} if v else None}
    if ptype == "checkbox":
        return {"checkbox": bool(v)}
    if ptype == "number":
        try:
            return {"number": None if v is None else float(v) if not float(v).is_integer() else int(v)}
        except (TypeError, ValueError):
            return {"number": None}
    if ptype == "url":
        return {"url": str(v) if v else None}
    if ptype == "email":
        return {"email": str(v) if v else None}
    if ptype == "relation":
        return {"relation": [{"id": x} for x in (v or [])]}
    return None


def properties(cfg: dict, t: dict, sch: dict[str, str], notes: list[str], today: date) -> dict:
    """The `properties` payload for one task row. Only columns the schema has, never read-only ones."""
    pm = props_map(cfg)
    smap = dict(DEFAULT_STATUS_MAP, **(C.get(cfg, "sinks.notion_tasks_mirror.status_map", {}) or {}))
    score, priority, _ = scoring.score(t, today, cfg) if t.get("status") == "open" else (t.get("score"), t.get("priority"), [])
    values = {
        "title": t.get("name"), "status": smap.get(t.get("status"), t.get("status")), "project": t.get("project"), "area": t.get("area"),
        "due": t.get("due"), "hard": t.get("hard"), "effort": t.get("effort"), "source": t.get("source"), "source_key": t.get("source_key"),
        "source_link": t.get("source_link"), "context": t.get("context"), "why_now": t.get("why_now"), "score": score, "priority": priority,
        "mentions": t.get("mentions"), "completed": t.get("completed_at"), "waiting_on": t.get("waiting_on"), "calendar_event": t.get("calendar_event"),
        "notes_relation": notes, "snoozed": t.get("snoozed_until"), "manual_priority": t.get("manual_priority"),
    }
    out = {}
    for key, v in values.items():
        name = pm.get(key)
        ptype = sch.get(name or "")
        if not name or not ptype or ptype in READ_ONLY:
            continue
        payload = _value(ptype, v)
        if payload is not None:
            out[name] = payload
    return out


def _pending(conn: sqlite3.Connection, all_rows: bool) -> list[dict]:
    if all_rows:
        return rows(conn, "SELECT * FROM tasks ORDER BY edited_at, id")
    wm = get_settings(conn).get("state.mirror.watermark") or ""
    return rows(conn, "SELECT * FROM tasks WHERE notion_page_id IS NULL OR edited_at > ? ORDER BY edited_at, id", (wm,))


def sweep(conn: sqlite3.Connection, cfg: dict, *, all_rows: bool = False, dry_run: bool = False, cap: int = 60, transport=None, pause: float | None = None) -> dict:
    """Push up to `cap` pending rows. Returns counts; `pending` is what is still left afterwards.
    A row Notion refuses (an option it lacks, a retyped column) is reported in `failed` and the
    pass goes on; the watermark advances past it, so it waits for its next edit or `--all`."""
    pend = _pending(conn, all_rows)
    if dry_run:
        return {"created": 0, "updated": 0, "failed": [], "pending": len(pend), "dry_run": True}
    todo = pend[:cap]
    if not todo:
        return {"created": 0, "updated": 0, "failed": [], "pending": 0}
    sch = schema(cfg, transport=transport)
    ds = C.get(cfg, "sinks.notion_tasks_mirror.data_source_id")
    db = C.get(cfg, "sinks.notion_tasks_mirror.database_id")
    parent = {"type": "data_source_id", "data_source_id": ds} if ds else {"type": "database_id", "database_id": db}
    version = "2025-09-03" if ds else "2022-06-28"
    pause = PAUSE_S if pause is None and transport is None else (pause or 0)
    today = date.today()
    created = updated = 0
    failed: list[str] = []
    last_edit = None
    with httpx.Client(timeout=60, transport=transport) as client:
        for t in todo:
            props = properties(cfg, t, sch, note_ids(conn, t["id"]), today)
            try:
                if t.get("notion_page_id"):
                    r = client.patch(f"{NOTION}/pages/{t['notion_page_id']}", headers=_headers(version), json={"properties": props})
                    if r.status_code == 404:   # the page was deleted in Notion: make a new one
                        r = client.post(f"{NOTION}/pages", headers=_headers(version), json={"parent": parent, "properties": props})
                        if r.status_code == 200:
                            conn.execute("UPDATE tasks SET notion_page_id = ? WHERE id = ?", (r.json().get("id"), t["id"]))
                    if r.status_code != 200:
                        raise MirrorError(f"{r.status_code} {_message(r)}")
                    updated += 1
                else:
                    r = client.post(f"{NOTION}/pages", headers=_headers(version), json={"parent": parent, "properties": props})
                    if r.status_code != 200:
                        raise MirrorError(f"{r.status_code} {_message(r)}")
                    conn.execute("UPDATE tasks SET notion_page_id = ? WHERE id = ?", (r.json().get("id"), t["id"]))
                    created += 1
            except (MirrorError, httpx.HTTPError) as e:
                failed.append(f"Task-{t['id']}: {e}"[:300])
            last_edit = t["edited_at"]
            if pause:
                time.sleep(pause)
    if last_edit and not all_rows:
        set_setting(conn, "state.mirror.watermark", last_edit)
    elif all_rows:
        set_setting(conn, "state.mirror.watermark", max((t["edited_at"] for t in todo), default=""))
    conn.commit()
    return {"created": created, "updated": updated, "failed": failed, "pending": len(_pending(conn, False))}


def _message(r: httpx.Response) -> str:
    try:
        return str(r.json().get("message") or r.text)[:200]
    except ValueError:
        return r.text[:200]


def sweep_job(conn: sqlite3.Connection, cfg: dict, now_local: datetime) -> str | None:
    res = sweep(conn, cfg)
    if not (res["created"] or res["updated"] or res["failed"]):
        return None
    out = f"created {res['created']}, updated {res['updated']}"
    if res["failed"]:
        out += f", failed {len(res['failed'])}: " + "; ".join(res["failed"][:3])
    if res["pending"]:
        out += f" · {res['pending']} more next pass"
    return out
