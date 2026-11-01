"""One-time import of the Notion Tasks database (SPEC.md §12).

    python -m factotum import-notion --dry-run          # bucket report, writes nothing
    python -m factotum import-notion --apply            # writes tasks with the classified statuses
    python -m factotum import-notion --from-json x.json # same, from a saved API dump (for tests / offline)

Uses the Notion REST API with an internal integration token (NOTION_TOKEN). The integration must
be connected to the Tasks database (docs/SETUP.md §5). Tries the 2025-09 data-source endpoint
first and falls back to the database endpoint for older API versions.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import date

import httpx

from .. import config as C
from ..db import add_link, now_iso, upsert_task
from ..lifecycle import IMPORT_BUCKETS, classify_import

NOTION = "https://api.notion.com/v1"
STATUS_MAP = {"Inbox": "open", "Next": "open", "In progress": "open", "Waiting": "open", "Done": "done", "Dropped": "dropped"}


def _headers(version: str) -> dict:
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        raise SystemExit("NOTION_TOKEN is not set (see .env.example and docs/SETUP.md §5)")
    return {"Authorization": f"Bearer {token}", "Notion-Version": version, "Content-Type": "application/json"}


def fetch_all(cfg: dict) -> list[dict]:
    """Page through every row of the Tasks data source. Returns raw Notion page objects."""
    ds = C.get(cfg, "sinks.notion_tasks_mirror.data_source_id")
    db = C.get(cfg, "sinks.notion_tasks_mirror.database_id")
    attempts = [
        (f"{NOTION}/data_sources/{ds}/query", "2025-09-03"),
        (f"{NOTION}/databases/{db}/query", "2022-06-28"),
    ]
    last_err = None
    for url, version in attempts:
        pages: list[dict] = []
        cursor = None
        try:
            with httpx.Client(timeout=60) as client:
                while True:
                    body = {"page_size": 100}
                    if cursor:
                        body["start_cursor"] = cursor
                    r = client.post(url, headers=_headers(version), json=body)
                    if r.status_code == 404 and not pages:
                        last_err = f"{url} → 404"
                        break
                    r.raise_for_status()
                    data = r.json()
                    pages.extend(data.get("results", []))
                    if not data.get("has_more"):
                        return pages
                    cursor = data.get("next_cursor")
        except httpx.HTTPStatusError as e:
            last_err = f"{url} → {e.response.status_code} {e.response.text[:200]}"
            continue
    raise SystemExit(f"Notion query failed: {last_err}")


# --- property parsing ------------------------------------------------------------------------

def _text(prop: dict | None) -> str | None:
    if not prop:
        return None
    parts = prop.get("rich_text") or prop.get("title") or []
    s = "".join(p.get("plain_text", "") for p in parts).strip()
    return s or None


def _select(prop: dict | None) -> str | None:
    if not prop:
        return None
    v = prop.get("select") or prop.get("status")
    return v.get("name") if v else None


def _date(prop: dict | None) -> tuple[str | None, int]:
    if not prop or not prop.get("date"):
        return None, 0
    start = prop["date"].get("start")
    return start, 1 if start and len(start) > 10 else 0


def _num(prop: dict | None):
    if not prop:
        return None
    if "number" in prop:
        return prop["number"]
    if "unique_id" in prop and prop["unique_id"]:
        return prop["unique_id"].get("number")
    return None


def parse_page(page: dict, props_cfg: dict | None = None) -> dict:
    """Notion page object → task dict (plus `_notes` relation ids)."""
    p = props_cfg or {}
    g = lambda key, default: page["properties"].get(p.get(key, default))  # noqa: E731
    due, due_dt = _date(g("due", "Due"))
    completed, _ = _date(g("completed", "Completed"))
    snoozed, _ = _date(g("snoozed", "Snoozed Until"))
    legacy = _select(g("status", "Status"))
    hard = (g("hard", "Hard deadline") or {}).get("checkbox")
    task = {
        "id": _num(g("task_id", "Task ID")),
        "name": _text(g("title", "Name")) or "(untitled)",
        "project": _select(g("project", "Project")),
        "area": _select(g("area", "Area")),
        "status": STATUS_MAP.get(legacy, "open"),
        "legacy_status": legacy,
        "due": due, "due_is_datetime": due_dt,
        "hard": 1 if hard else 0,
        "effort": _select(g("effort", "Effort")),
        "source": _select(g("source", "Source")),
        "source_key": (_text(g("source_key", "Source Key")) or "").replace("\\", "") or None,
        "source_link": (g("source_link", "Source Link") or {}).get("url"),
        "waiting_on": _text(g("waiting_on", "Waiting On")),
        "context": _text(g("context", "Context")),
        "why_now": _text(g("why_now", "Why now")),
        "score": _num(g("score", "Score")),
        "priority": _select(g("priority", "Priority")),
        "manual_priority": _select(g("manual_priority", "Manual Priority")),
        "mentions": int(_num(g("mentions", "Mentions")) or 1),
        "created_at": page.get("created_time") or now_iso(),
        "edited_at": page.get("last_edited_time") or now_iso(),
        "completed_at": completed,
        "snoozed_until": snoozed,
        "calendar_event": (g("calendar_event", "Calendar Event") or {}).get("url"),
        "notion_page_id": page.get("id"),
    }
    task["last_mention_at"] = task["edited_at"]
    if task["source"] == "Canvas" and task["source_link"]:
        task["canvas_id"] = task["source_link"].rstrip("/").split("/")[-1]
    notes = g("notes_relation", "Notes") or {}
    task["_notes"] = [r.get("id") for r in notes.get("relation", []) if r.get("id")]
    if task["id"] is None:
        task["id"] = None  # will be assigned by the store (should not happen for real rows)
    return task


# --- the import ------------------------------------------------------------------------------

def run(conn, cfg: dict, *, apply: bool, from_json: str | None = None, today: date | None = None, out=print) -> dict:
    today = today or date.today()
    pages = json.load(open(from_json, encoding="utf-8")) if from_json else fetch_all(cfg)
    props_cfg = C.get(cfg, "sinks.notion_tasks_mirror.properties", {}) or {}
    counts: Counter[str] = Counter()
    by_bucket: dict[str, list[dict]] = {b: [] for b, _ in IMPORT_BUCKETS}
    parsed = []
    for page in pages:
        t = parse_page(page, props_cfg)
        bucket, new_status = classify_import(t, today, cfg)
        t["_bucket"], t["_new_status"] = bucket, new_status
        counts[bucket] += 1
        by_bucket[bucket].append(t)
        parsed.append(t)

    out(f"Imported {len(parsed)} rows from Notion ({'dry run' if not apply else 'APPLYING'}), as of {today}:\n")
    for bucket, desc in IMPORT_BUCKETS:
        out(f"  {counts[bucket]:4d}  {bucket:16s} {desc}")
    open_after = sum(1 for t in parsed if t["_new_status"] == "open")
    out(f"\n  open before: {sum(1 for t in parsed if t['status'] == 'open')}   open after: {open_after}   "
        f"new in Review: {counts['undated_keep'] + counts['hard_last_call']}")
    for bucket in ("hard_last_call", "undated_keep"):
        if by_bucket[bucket]:
            out(f"\n  {bucket} ({len(by_bucket[bucket])}):")
            for t in sorted(by_bucket[bucket], key=lambda x: x.get("id") or 0):
                out(f"    Task-{t['id']:<4} {t['project'] or '-':<22} {(t['name'] or '')[:70]}")

    if apply:
        for t in parsed:
            if t["_new_status"] != t["status"]:
                t["status"] = t["_new_status"]
                t["completed_at"] = t.get("completed_at") or today.isoformat()
                t["closed_by"] = "import"
            # Only the buckets that need a human decision surface as New in Review; everything
            # else is marked reviewed today so the first sessions are bounded (SPEC.md §12).
            if t["_bucket"] not in ("hard_last_call", "undated_keep"):
                t["last_reviewed_at"] = today.isoformat()
            notes = t.pop("_notes", [])
            t.pop("_bucket", None); t.pop("_new_status", None)
            tid = upsert_task(conn, t)
            if t.get("notion_page_id"):
                add_link(conn, tid, "notion_page", t["notion_page_id"], "Notion task row")
            for n in notes:
                add_link(conn, tid, "plaud_note", n, "Plaud note (Notion page id)")
        conn.commit()
        out(f"\nWrote {len(parsed)} tasks.")
    return {"counts": dict(counts), "open_after": open_after, "total": len(parsed)}
