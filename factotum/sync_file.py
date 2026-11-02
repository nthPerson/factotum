"""One recording in, one note page and its tasks out (SPEC.md §6 "Plaud", §8.1).

    build_prompt(cfg, conn, rec, subject, received, files, today) -> str
    apply_result(conn, cfg, rec, data, today, ...)              -> summary dict   (T4)
    run(conn, cfg, subject, received, ...)                      -> summary dict   (T6)

Claude reads the two files and returns JSON; Python writes. The prompt is `prompts/sync-file.md`
with the taxonomy from config, the open rows from the store and the recording's facts filled in.
The mail subject is wrapped as data: it is attacker-controlled text.
"""
from __future__ import annotations

import html
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from . import batches, config as C
from .db import now_iso, rows

PROMPT = C.ROOT / "prompts" / "sync-file.md"
SLICE_CAP = 150


def taxonomy_text(cfg: dict) -> str:
    tx = C.get(cfg, "taxonomy", {}) or {}
    out = ["Areas: " + ", ".join(tx.get("areas") or []), "", "Projects (name — area — what belongs here):"]
    for p in tx.get("projects") or []:
        out.append(f"- {p.get('name')} — {p.get('area')} — {p.get('description') or ''}".rstrip(" —"))
    tb = (tx.get("tie_breakers") or "").strip()
    if tb:
        out += ["", "Tie-breakers:", tb]
    for key, title in (("note_types", "Type (one):"), ("note_tags", "Tags (any):")):
        items = tx.get(key) or []
        if items:
            out += ["", title] + [f"- {i.get('name')}: {i.get('description') or ''}".rstrip(": ") for i in items]
    return "\n".join(out)


def open_rows_text(conn: sqlite3.Connection, cap: int = SLICE_CAP) -> str:
    rs = rows(conn, "SELECT id, name, project, due, hard, waiting_on, top_rank FROM tasks WHERE status = 'open' "
                    "ORDER BY top_rank IS NULL, top_rank, score DESC, id DESC LIMIT ?", (cap,))
    lines = []
    for r in rs:
        bits = [f"Task-{r['id']}", r["project"] or "—"]
        if r["due"]:
            bits.append(f"due {str(r['due'])[:16]}" + (" hard" if r["hard"] else ""))
        if r["waiting_on"]:
            bits.append(f"waiting on {r['waiting_on']}")
        if r["top_rank"]:
            bits.append(f"top {r['top_rank']}")
        bits.append((r["name"] or "")[:90])
        lines.append(" | ".join(bits))
    return "\n".join(lines) or "(no open tasks)"


def _when(rec: dict) -> str:
    v = rec.get("start_at") or rec.get("date") or ""
    try:
        d = datetime.fromisoformat(str(v)[:16]) if "T" in str(v) else date.fromisoformat(str(v)[:10])
        return d.strftime("%A %Y-%m-%d %H:%M") if isinstance(d, datetime) else d.strftime("%A %Y-%m-%d")
    except ValueError:
        return str(v)


def build_prompt(cfg: dict, conn: sqlite3.Connection, rec: dict, subject: str, received: str, files, today: date) -> str:
    tpl = PROMPT.read_text(encoding="utf-8")
    cap = int(C.get(cfg, "sources.plaud.extraction.max_undated_per_recording", 3))
    fill = {
        "taxonomy": taxonomy_text(cfg),
        "open_tasks": open_rows_text(conn),
        "max_undated": f"{cap} undated",
        "rec_id": rec["id"],
        "rec_name": html.escape(str(rec.get("name") or ""), quote=False),
        "rec_when": _when(rec),
        "received": str(received or ""),
        "today": today.strftime("%A %Y-%m-%d"),
        "summary_path": str(Path(files.summary)),
        "transcript_path": str(Path(files.transcript)),
        "subject": html.escape(str(subject or ""), quote=False),
    }
    for k, v in fill.items():
        tpl = tpl.replace("{{" + k + "}}", v)
    return tpl


# --- applying what Claude returned (SPEC.md §8.1): Python writes --------------------------------

UPDATABLE = {"name", "project", "area", "due", "due_is_datetime", "hard", "effort", "context", "counterpart", "waiting_on"}
INSTRUCTION_OPS = {"done", "defer", "drop", "waiting", "priority", "start", "top", "longterm", "remind", "correct"}
PRIORITIES = {"P0", "P1", "P2", "P3"}


def _taxonomy(cfg: dict) -> dict[str, str]:
    """project name → area, from config."""
    return {p.get("name"): p.get("area") for p in (C.get(cfg, "taxonomy.projects", []) or []) if p.get("name")}


def _clean_task_fields(cfg: dict, t: dict) -> dict:
    tax = _taxonomy(cfg)
    f = {k: t.get(k) for k in UPDATABLE if k in t}
    if "project" in f:
        if f["project"] not in tax:
            f["project"] = "Unknown" if "Unknown" in tax else None
        f["area"] = tax.get(f["project"]) or f.get("area")
    if "hard" in f:
        f["hard"] = 1 if f["hard"] else 0
    if "due" in f and f["due"]:
        f["due"] = str(f["due"])[:16]
        f["due_is_datetime"] = 1 if "T" in f["due"] else 0
        try:
            datetime.fromisoformat(f["due"])
        except ValueError:
            f.pop("due"); f.pop("due_is_datetime", None)
    if "effort" in f and f["effort"] not in (C.get(cfg, "taxonomy.efforts", []) or ["XS", "S", "M", "L"]):
        f.pop("effort")
    for k in ("name", "context", "counterpart", "waiting_on"):
        if k in f and f[k] is not None:
            f[k] = str(f[k]).strip()[: 80 if k == "name" else 2000] or None
    return f


def _ref_index(ref) -> int | None:
    import re
    m = re.fullmatch(r"\s*tasks\[(\d+)\]\s*", str(ref or ""))
    return int(m.group(1)) if m else None


def apply_result(conn: sqlite3.Connection, cfg: dict, rec: dict, data: dict, today: date, *, calendar=None, source: str = "plaud") -> dict:
    """Write what the run decided: new rows (upsert by source_key), one batch for every change to
    an existing row (updates, voice instructions, reminders, calendar links) so the Log tab's Undo
    reverts the recording in one tap, mentions and links on every row touched. `calendar` is a
    callable(event) -> {"id", "link"} or None when the calendar is off. Returns a summary."""
    from .db import add_link, add_mention, get_task, upsert_task
    unresolved: list[str] = [str(u) for u in (data.get("unresolved") or []) if u]
    at = str(rec.get("start_at") or rec.get("date") or now_iso())
    rec_id = rec["id"]
    link = f"{C.get(cfg, 'sinks.notion_notes.source_link_prefix', 'https://web.plaud.ai/file/')}{rec_id}"
    cap = int(C.get(cfg, "sources.plaud.extraction.max_undated_per_recording", 3))
    created: list[int] = []
    updated: list[int] = []
    touched: list[int] = []
    by_index: dict[int, int | None] = {}
    changes: list[dict] = []
    undated = 0
    n = 0
    for i, t in enumerate(data.get("tasks") or []):
        if not isinstance(t, dict):
            continue
        op = t.get("op") or "create"
        f = _clean_task_fields(cfg, t)
        if op == "update":
            tid = t.get("id")
            row = get_task(conn, int(tid)) if isinstance(tid, (int, float, str)) and str(tid).isdigit() else None
            if not row:
                unresolved.append(f"update of unknown Task-{tid}: {t.get('name') or ''}".strip())
                by_index[i] = None
                continue
            if row["status"] in ("faded", "lapsed"):
                changes.append({"task_id": row["id"], "op": "reopen", "fields": {}})
            if f:
                changes.append({"task_id": row["id"], "op": "set", "fields": f})
            updated.append(row["id"]); touched.append(row["id"]); by_index[i] = row["id"]
            continue
        n += 1
        if not f.get("name"):
            unresolved.append(f"task #{n} skipped: no name")
            by_index[i] = None
            continue
        key = str(t.get("source_key") or f"plaud:{rec_id}:{n}")
        existing = conn.execute("SELECT id, status FROM tasks WHERE source_key = ?", (key,)).fetchone()
        if existing:   # the same recording synced twice: update, never a twin
            tid = int(existing[0])
            if existing[1] in ("faded", "lapsed"):
                changes.append({"task_id": tid, "op": "reopen", "fields": {}})
            changes.append({"task_id": tid, "op": "set", "fields": f})
            updated.append(tid); touched.append(tid); by_index[i] = tid
            continue
        if not f.get("due"):
            undated += 1
            if undated > cap:
                unresolved.append(f"over the {cap}-undated cap, not created: {f['name']}")
                by_index[i] = None
                continue
        tid = upsert_task(conn, dict(f, status="open", source="Plaud", source_key=key, source_link=link, mentions=0,
                                     created_at=at, edited_at=now_iso()))
        add_mention(conn, tid, "plaud", rec_id, at)
        add_link(conn, tid, "plaud_note", rec_id, label=str(rec.get("name") or "")[:120])
        created.append(tid); by_index[i] = tid
    applied = 0
    for ins in data.get("instructions") or []:
        if not isinstance(ins, dict):
            continue
        op, tid, fields = ins.get("op"), ins.get("task_id"), dict(ins.get("fields") or {})
        ev = str(ins.get("evidence") or "")[:300]
        row = get_task(conn, int(tid)) if isinstance(tid, (int, float, str)) and str(tid).isdigit() else None
        if op not in INSTRUCTION_OPS or not row:
            unresolved.append(f"{op or 'instruction'} on Task-{tid}: {'unknown task' if not row else 'unknown op'} ({ev})")
            continue
        if row["status"] in ("faded", "lapsed") and op not in ("done", "drop"):
            changes.append({"task_id": row["id"], "op": "reopen", "fields": {}})
        if op in ("done", "drop") and row["status"] != "open":
            unresolved.append(f"{op} on Task-{row['id']}: already {row['status']} ({ev})")
            continue
        if op == "done":
            changes.append({"task_id": row["id"], "op": "done", "fields": {}})
        elif op == "drop":
            changes.append({"task_id": row["id"], "op": "drop", "fields": {"context": ((row.get("context") or "") + f"\nDropped by voice {today.isoformat()}.").strip()}})
        elif op == "defer":
            f = _clean_task_fields(cfg, {"due": fields.get("due")}) if fields.get("due") else {}
            if f.get("due"):
                changes.append({"task_id": row["id"], "op": "set", "fields": dict(f, snoozed_until=None)})
            else:
                changes.append({"task_id": row["id"], "op": "set", "fields": {"snoozed_until": (today + timedelta(days=1)).isoformat()}})
        elif op == "waiting":
            changes.append({"task_id": row["id"], "op": "set", "fields": {"waiting_on": str(fields.get("waiting_on") or "someone")[:120]}})
        elif op == "priority":
            p = str(fields.get("manual_priority") or "").upper()
            if p not in PRIORITIES:
                unresolved.append(f"priority on Task-{row['id']}: {p!r} is not P0–P3 ({ev})")
                continue
            changes.append({"task_id": row["id"], "op": "set", "fields": {"manual_priority": p}})
        elif op == "start":
            changes.append({"task_id": row["id"], "op": "set", "fields": {"context": ((row.get("context") or "") + f"\nStarted by voice {today.isoformat()}.").strip()}})
        elif op == "top":
            changes.append({"task_id": row["id"], "op": "pin_top", "fields": {}})
        elif op == "longterm":
            changes.append({"task_id": row["id"], "op": "longterm", "fields": {}})
        elif op == "remind":
            changes.append({"task_id": row["id"], "op": "add_reminder", "fields": {"fire_at": fields.get("fire_at"), "label": fields.get("label"), "repeat": fields.get("repeat")}})
        elif op == "correct":
            f = _clean_task_fields(cfg, fields)
            if not f:
                unresolved.append(f"correct on Task-{row['id']}: nothing settable in {sorted(fields)} ({ev})")
                continue
            changes.append({"task_id": row["id"], "op": "set", "fields": f})
        applied += 1
        touched.append(row["id"])
    reminders = 0
    for r in data.get("reminders") or []:
        if not isinstance(r, dict):
            continue
        tid = by_index.get(_ref_index(r.get("task_ref"))) if r.get("task_ref") else r.get("task_id")
        if not tid or not get_task(conn, int(tid)):
            unresolved.append(f"reminder {r.get('label') or r.get('fire_at')}: no task to attach to")
            continue
        changes.append({"task_id": int(tid), "op": "add_reminder", "fields": {"fire_at": r.get("fire_at"), "label": r.get("label"), "repeat": r.get("repeat")}})
        reminders += 1
    events = 0
    for ev in data.get("calendar") or []:
        if not isinstance(ev, dict) or not ev.get("title") or not ev.get("start"):
            continue
        if calendar is None:
            unresolved.append(f"calendar is off, not created: {ev['title']} {ev['start']}")
            continue
        try:
            made = calendar(ev)
        except Exception as e:   # the recording still syncs; the event is reported, not lost silently
            unresolved.append(f"calendar error for {ev['title']}: {type(e).__name__}: {e}"[:300])
            continue
        if not made:
            continue
        events += 1
        tid = by_index.get(_ref_index(ev.get("task_ref"))) if ev.get("task_ref") else ev.get("task_id")
        if tid and get_task(conn, int(tid)):
            add_link(conn, int(tid), "calendar", str(made.get("id")), label=str(ev["title"])[:120])
            if made.get("link"):
                changes.append({"task_id": int(tid), "op": "set", "fields": {"calendar_event": made["link"]}})
    res = batches.apply(conn, changes, source, today, closed_by="voice") if changes else {"batch_id": None, "applied": 0, "skipped": []}
    for s in res["skipped"]:
        unresolved.append(f"change on Task-{s.get('task_id')} skipped: {s.get('why')}")
    for tid in dict.fromkeys(touched):
        add_mention(conn, tid, "plaud", rec_id, at)
        add_link(conn, tid, "plaud_note", rec_id, label=str(rec.get("name") or "")[:120])
    conn.commit()
    return {"created": created, "updated": list(dict.fromkeys(updated)), "applied": applied, "reminders": reminders, "events": events,
            "unresolved": unresolved, "batch_id": res["batch_id"], "task_ids": list(dict.fromkeys(created + touched))}
