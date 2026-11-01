"""One-way mirror of task rows into a Notion database (SPEC.md §7). Phase 6; this is the
interface and the property mapping so the Settings toggle has something real to switch.

    mirror_task(cfg, task) -> notion page id

Never reads back. Property names come from sinks.notion_tasks_mirror.properties so a user with a
different database layout only edits config, not code.
"""
from __future__ import annotations

import os

import httpx

from .. import config as C

NOTION = "https://api.notion.com/v1"


def enabled(cfg: dict) -> bool:
    return bool(C.get(cfg, "sinks.notion_tasks_mirror.enabled")) and bool(os.environ.get("NOTION_TOKEN"))


def _props(cfg: dict, task: dict) -> dict:
    p = C.get(cfg, "sinks.notion_tasks_mirror.properties", {}) or {}
    smap = C.get(cfg, "sinks.notion_tasks_mirror.status_map", {}) or {}
    out = {
        p.get("title", "Name"): {"title": [{"text": {"content": task["name"][:200]}}]},
        p.get("status", "Status"): {"select": {"name": smap.get(task["status"], "Next")}},
    }
    if task.get("project"):
        out[p.get("project", "Project")] = {"select": {"name": task["project"]}}
    if task.get("area"):
        out[p.get("area", "Area")] = {"select": {"name": task["area"]}}
    if task.get("due"):
        out[p.get("due", "Due")] = {"date": {"start": task["due"]}}
    out[p.get("hard", "Hard deadline")] = {"checkbox": bool(task.get("hard"))}
    if task.get("effort"):
        out[p.get("effort", "Effort")] = {"select": {"name": task["effort"]}}
    if task.get("source"):
        out[p.get("source", "Source")] = {"select": {"name": task["source"]}}
    if task.get("source_key"):
        out[p.get("source_key", "Source Key")] = {"rich_text": [{"text": {"content": task["source_key"]}}]}
    if task.get("source_link"):
        out[p.get("source_link", "Source Link")] = {"url": task["source_link"]}
    if task.get("context"):
        out[p.get("context", "Context")] = {"rich_text": [{"text": {"content": task["context"][:1900]}}]}
    if task.get("why_now"):
        out[p.get("why_now", "Why now")] = {"rich_text": [{"text": {"content": task["why_now"][:1900]}}]}
    if task.get("score") is not None:
        out[p.get("score", "Score")] = {"number": task["score"]}
    if task.get("priority"):
        out[p.get("priority", "Priority")] = {"select": {"name": task["priority"]}}
    out[p.get("mentions", "Mentions")] = {"number": task.get("mentions") or 1}
    if task.get("completed_at"):
        out[p.get("completed", "Completed")] = {"date": {"start": task["completed_at"]}}
    if task.get("waiting_on"):
        out[p.get("waiting_on", "Waiting On")] = {"rich_text": [{"text": {"content": task["waiting_on"]}}]}
    if task.get("calendar_event"):
        out[p.get("calendar_event", "Calendar Event")] = {"url": task["calendar_event"]}
    return out


def mirror_task(cfg: dict, task: dict) -> str | None:
    """Create or update the Notion row for this task. Returns the page id or None if disabled."""
    if not enabled(cfg):
        return None
    headers = {"Authorization": f"Bearer {os.environ['NOTION_TOKEN']}", "Notion-Version": "2025-09-03", "Content-Type": "application/json"}
    props = _props(cfg, task)
    with httpx.Client(timeout=30) as client:
        if task.get("notion_page_id"):
            r = client.patch(f"{NOTION}/pages/{task['notion_page_id']}", headers=headers, json={"properties": props})
            r.raise_for_status()
            return task["notion_page_id"]
        parent = {"type": "data_source_id", "data_source_id": C.get(cfg, "sinks.notion_tasks_mirror.data_source_id")}
        r = client.post(f"{NOTION}/pages", headers=headers, json={"parent": parent, "properties": props})
        r.raise_for_status()
        return r.json().get("id")
