"""The Notion notes sink (SPEC.md §7 "Notion notes"): one page per recording in the Plaud notes
database, idempotent on Source Link, written with the REST API. Property names come from
`sinks.notion_notes.properties` so a renamed column is a YAML edit.

    md_to_blocks(md)                                   -> Notion block objects (headings, lists, code, paragraphs)
    upsert(cfg, rec, note, transcript_text, ...)       -> page url
"""
from __future__ import annotations

import os
import re

import httpx

from . import config as C

NOTION = "https://api.notion.com/v1"
TEXT_CAP = 2000          # Notion's limit per rich-text object
BATCH = 100              # blocks per request
DEFAULT_PROPS = {"title": "Name", "date": "Date", "source_link": "Source Link", "area": "Area", "type": "Type",
                 "project": "Project", "tags": "Tags", "meeting_date": "Meeting Date", "tasks": "Tasks"}
_SEG = re.compile(r"^\[(?P<t0>\d{1,2}:\d{2}(?::\d{2})?)\s*-\s*(?P<t1>\d{1,2}:\d{2}(?::\d{2})?)\]\s*(?P<who>[^:]{1,40}):\s*(?P<text>.*)$")


def _headers(version: str = "2025-09-03") -> dict:
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        raise RuntimeError("NOTION_TOKEN is not set (docs/SETUP.md §5)")
    return {"Authorization": f"Bearer {token}", "Notion-Version": version, "Content-Type": "application/json"}


def props_map(cfg: dict) -> dict:
    return dict(DEFAULT_PROPS, **(C.get(cfg, "sinks.notion_notes.properties", {}) or {}))


# --- markdown → blocks --------------------------------------------------------------------------

def rich(text: str, bold: bool = False) -> list[dict]:
    """Rich text with **bold** spans, each object ≤ 2000 chars."""
    out = []
    parts = re.split(r"(\*\*[^*]+\*\*)", text) if not bold else [text]
    for part in parts:
        if not part:
            continue
        b = bold or (part.startswith("**") and part.endswith("**") and len(part) > 4)
        s = part[2:-2] if (b and not bold) else part
        for i in range(0, len(s), TEXT_CAP):
            chunk = s[i:i + TEXT_CAP]
            o = {"type": "text", "text": {"content": chunk}}
            if b:
                o["annotations"] = {"bold": True}
            out.append(o)
    return out


def _block(kind: str, rt: list[dict], **extra) -> dict:
    return {"object": "block", "type": kind, kind: dict({"rich_text": rt}, **extra)}


def md_to_blocks(md: str) -> list[dict]:
    blocks: list[dict] = []
    para: list[str] = []

    def flush():
        if para:
            blocks.append(_block("paragraph", rich(" ".join(para))))
            para.clear()
    lines = (md or "").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if s.startswith("```"):
            flush()
            lang = s[3:].strip() or "plain text"
            body = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i]); i += 1
            blocks.append(_block("code", rich("\n".join(body), bold=False) or [{"type": "text", "text": {"content": ""}}], language=lang))
            i += 1
            continue
        if not s:
            flush()
        elif s.startswith("#"):
            flush()
            level = min(len(s) - len(s.lstrip("#")), 3)
            blocks.append(_block(f"heading_{level}", rich(s.lstrip("#").strip())))
        elif re.match(r"^[-*•]\s+", s):
            flush()
            blocks.append(_block("bulleted_list_item", rich(re.sub(r"^[-*•]\s+", "", s))))
        elif re.match(r"^\d+[.)]\s+", s):
            flush()
            blocks.append(_block("numbered_list_item", rich(re.sub(r"^\d+[.)]\s+", "", s))))
        else:
            para.append(s)
        i += 1
    flush()
    # code blocks: a plain-text run with no bold parsing is what the rich() call above produced only
    # when the body had no ** markers; make sure code is never bold-split
    for b in blocks:
        if b["type"] == "code":
            text = "".join(r["text"]["content"] for r in b["code"]["rich_text"])
            b["code"]["rich_text"] = [{"type": "text", "text": {"content": text[i:i + TEXT_CAP]}} for i in range(0, max(len(text), 1), TEXT_CAP)]
    return blocks


def transcript_blocks(text: str) -> list[dict]:
    out = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        m = _SEG.match(s)
        if m:
            out.append(_block("paragraph", rich(m["who"].strip(), bold=True) + rich(f" ({m['t0']}) {m['text']}", bold=False)))
        else:
            out.append(_block("paragraph", rich(s)))
    return out


# --- the page -----------------------------------------------------------------------------------

def _client(transport=None) -> httpx.Client:
    return httpx.Client(timeout=60, transport=transport)


def _find(client: httpx.Client, cfg: dict, link: str) -> dict | None:
    pm = props_map(cfg)
    ds, db = C.get(cfg, "sinks.notion_notes.data_source_id"), C.get(cfg, "sinks.notion_notes.database_id")
    body = {"page_size": 1, "filter": {"property": pm["source_link"], "url": {"equals": link}}}
    for url, version in ((f"{NOTION}/data_sources/{ds}/query", "2025-09-03"), (f"{NOTION}/databases/{db}/query", "2022-06-28")):
        r = client.post(url, headers=_headers(version), json=body)
        if r.status_code == 404:
            continue
        r.raise_for_status()
        res = r.json().get("results") or []
        return res[0] if res else None
    raise RuntimeError("Notion notes database not reachable with either API version")


def properties(cfg: dict, rec: dict, note: dict, task_page_ids=()) -> dict:
    pm = props_map(cfg)
    p = note.get("properties") or {}
    link = f"{C.get(cfg, 'sinks.notion_notes.source_link_prefix', 'https://web.plaud.ai/file/')}{rec['id']}"
    props = {pm["title"]: {"title": [{"type": "text", "text": {"content": str(rec.get("name") or rec["id"])[:200]}}]},
             pm["source_link"]: {"url": link}}
    if rec.get("date"):
        props[pm["date"]] = {"date": {"start": str(rec["date"])[:10]}}
    for key in ("area", "project", "type"):
        if p.get(key):
            props[pm[key]] = {"select": {"name": str(p[key])}}
    tags = [str(t) for t in (p.get("tags") or []) if t]
    if tags:
        props[pm["tags"]] = {"multi_select": [{"name": t} for t in tags]}
    if p.get("type") == "Meeting" and p.get("meeting_date"):
        props[pm["meeting_date"]] = {"date": {"start": str(p["meeting_date"])[:16]}}
    ids = [i for i in task_page_ids if i]
    if ids:
        props[pm["tasks"]] = {"relation": [{"id": i} for i in ids]}
    return props


def page_blocks(note: dict, transcript_text: str, extra_sections: list[tuple[str, list[str]]] | None = None) -> list[dict]:
    blocks = md_to_blocks(note.get("body_md") or "")
    fups = [str(f) for f in (note.get("followups") or []) if f]
    if fups:
        blocks.append(_block("heading_2", rich("Mentioned follow-ups")))
        blocks += [_block("bulleted_list_item", rich(f)) for f in fups]
    for title, lines in extra_sections or []:
        if lines:
            blocks.append(_block("heading_2", rich(title)))
            blocks += [_block("bulleted_list_item", rich(str(l))) for l in lines]
    if transcript_text:
        blocks.append(_block("heading_2", rich("Transcript")))
        blocks += transcript_blocks(transcript_text)
    return blocks


def _append(client: httpx.Client, page_id: str, blocks: list[dict]) -> None:
    for i in range(0, len(blocks), BATCH):
        r = client.patch(f"{NOTION}/blocks/{page_id}/children", headers=_headers(), json={"children": blocks[i:i + BATCH]})
        r.raise_for_status()


def _clear(client: httpx.Client, page_id: str) -> int:
    n, cursor = 0, None
    ids = []
    while True:
        params = {"page_size": 100, **({"start_cursor": cursor} if cursor else {})}
        r = client.get(f"{NOTION}/blocks/{page_id}/children", headers=_headers(), params=params)
        r.raise_for_status()
        d = r.json()
        ids += [b["id"] for b in d.get("results") or []]
        if not d.get("has_more"):
            break
        cursor = d.get("next_cursor")
    for bid in ids:
        client.delete(f"{NOTION}/blocks/{bid}", headers=_headers()).raise_for_status()
        n += 1
    return n


def upsert(cfg: dict, rec: dict, note: dict, transcript_text: str, *, task_page_ids=(), extra_sections=None, transport=None) -> str:
    """Create the note page, or rewrite the one with this recording's Source Link. Returns its url."""
    link = f"{C.get(cfg, 'sinks.notion_notes.source_link_prefix', 'https://web.plaud.ai/file/')}{rec['id']}"
    props = properties(cfg, rec, note, task_page_ids)
    blocks = page_blocks(note, transcript_text, extra_sections)
    with _client(transport) as client:
        existing = _find(client, cfg, link)
        if existing:
            pid = existing["id"]
            r = client.patch(f"{NOTION}/pages/{pid}", headers=_headers(), json={"properties": props})
            r.raise_for_status()
            _clear(client, pid)
            _append(client, pid, blocks)
            return r.json().get("url") or existing.get("url") or pid
        ds, db = C.get(cfg, "sinks.notion_notes.data_source_id"), C.get(cfg, "sinks.notion_notes.database_id")
        for parent, version in (({"type": "data_source_id", "data_source_id": ds}, "2025-09-03"), ({"type": "database_id", "database_id": db}, "2022-06-28")):
            r = client.post(f"{NOTION}/pages", headers=_headers(version), json={"parent": parent, "properties": props, "children": blocks[:BATCH]})
            if r.status_code in (400, 404) and version == "2025-09-03":
                continue
            r.raise_for_status()
            page = r.json()
            _append(client, page["id"], blocks[BATCH:])
            return page.get("url") or page["id"]
    raise RuntimeError("Notion refused the page with both API versions")
