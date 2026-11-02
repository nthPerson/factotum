"""One recording in, one note page and its tasks out (SPEC.md §6 "Plaud", §8.1).

    build_prompt(cfg, conn, rec, subject, received, files, today) -> str
    apply_result(conn, cfg, rec, data, today, ...)              -> summary dict   (T4)
    run(conn, cfg, subject, received, ...)                      -> summary dict   (T6)

Claude reads the two files and returns JSON; Python writes. The prompt is `prompts/sync-file.md`
with the taxonomy from config, the open rows from the store and the recording's facts filled in.
The mail subject only locates the recording; the prompt carries the title the Plaud CLI returned.
"""
from __future__ import annotations

import html
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import batches, config as C
from .db import now_iso, rows

PROMPT = C.ROOT / "prompts" / "sync-file.md"
RUNS_DIR = C.ROOT / "data" / "runs"
SLICE_CAP = 150
RETRY_DELAY_S = 120


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


def slice_rows(conn: sqlite3.Connection, cap: int = SLICE_CAP) -> list[dict]:
    """The rows a run may reference: every open task, then recently faded or lapsed ones (a new
    mention resurrects them, SPEC.md §5), capped."""
    return rows(conn, "SELECT id, name, project, due, hard, waiting_on, top_rank, status FROM tasks "
                      "WHERE status = 'open' OR (status IN ('faded', 'lapsed') AND completed_at >= date('now', '-60 days')) "
                      "ORDER BY status = 'open' DESC, top_rank IS NULL, top_rank, score DESC, id DESC LIMIT ?", (cap,))


def open_rows_text(conn: sqlite3.Connection, cap: int = SLICE_CAP) -> str:
    lines = []
    for r in slice_rows(conn, cap):
        bits = [f"Task-{r['id']}", r["project"] or "—"]
        if r["status"] != "open":
            bits.append(r["status"])
        if r["due"]:
            bits.append(f"due {str(r['due'])[:16]}" + (" hard" if r["hard"] else ""))
        if r["waiting_on"]:
            bits.append(f"waiting on {r['waiting_on']}")
        if r["top_rank"]:
            bits.append(f"top {r['top_rank']}")
        bits.append((r["name"] or "")[:90])
        lines.append(" | ".join(bits))
    return "\n".join(lines) or "(no open tasks)"


def recording(cfg: dict, d: dict, fallback_name: str = "", fallback_date: str | None = None) -> dict:
    """The rec dict from `plaud file` details. Plaud reports start_at/created_at in UTC; the prompt,
    the note page and the task dates use the owner's wall time, created_at stays UTC."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(str(C.get(cfg, "owner.timezone", "UTC")))
    raw = d.get("start_at") or d.get("created_at") or ""
    rec = {"id": d["id"], "name": d.get("name") or fallback_name or d["id"], "start_at": None, "start_utc": None, "date": fallback_date}
    try:
        utc = datetime.fromisoformat(str(raw)[:19]).replace(tzinfo=timezone.utc)
        local = utc.astimezone(tz)
        rec.update(start_utc=utc.isoformat(), start_at=local.strftime("%Y-%m-%dT%H:%M"), date=local.strftime("%Y-%m-%d"))
    except ValueError:
        pass
    return rec


def _when(rec: dict) -> str:
    v = rec.get("start_at") or rec.get("date") or ""
    try:
        d = datetime.fromisoformat(str(v)[:16]) if "T" in str(v) else date.fromisoformat(str(v)[:10])
        return d.strftime("%A %Y-%m-%d %H:%M") if isinstance(d, datetime) else d.strftime("%A %Y-%m-%d")
    except ValueError:
        return str(v)


def build_prompt(cfg: dict, conn: sqlite3.Connection, rec: dict, received: str, files, today: date) -> str:
    """The mail subject never enters the prompt: it only located the recording. The title here is
    the one the Plaud CLI returned, escaped, and the template says it is data."""
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


def apply_result(conn: sqlite3.Connection, cfg: dict, rec: dict, data: dict, today: date, *, calendar=None, source: str = "plaud",
                 allowed_ids: set[int] | None = None) -> dict:
    """Write what the run decided: new rows (upsert by source_key), one batch for every change to
    an existing row (updates, voice instructions, reminders, calendar links) so the Log tab's Undo
    reverts the recording in one tap, mentions and links on every row touched. `calendar` is a
    callable(event) -> {"id", "link"} or None when the calendar is off. Returns a summary."""
    from .db import add_link, add_mention, get_task, upsert_task
    unresolved: list[str] = [str(u) for u in (data.get("unresolved") or []) if u]
    at = str(rec.get("start_utc") or rec.get("start_at") or rec.get("date") or now_iso())
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

    def _target(tid):
        row = get_task(conn, int(tid)) if isinstance(tid, (int, float, str)) and str(tid).isdigit() else None
        if row and allowed_ids is not None and row["id"] not in allowed_ids:
            return None
        return row
    for i, t in enumerate(data.get("tasks") or []):
        if not isinstance(t, dict):
            continue
        op = t.get("op") or "create"
        f = _clean_task_fields(cfg, t)
        if op == "update":
            tid = t.get("id")
            row = _target(tid)
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
        key = str(t.get("source_key") or "")
        if not key.startswith(f"plaud:{rec_id}:"):     # only this recording's keys; anything else is the model wandering
            key = f"plaud:{rec_id}:{n}"
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
        row = _target(tid)
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
        if not tid or not (tid in created or _target(tid)):
            unresolved.append(f"reminder {r.get('label') or r.get('fire_at')}: no task to attach to" + (f" (Task-{tid} is not one this run may touch)" if tid else ""))
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
        if tid and (tid in created or _target(tid)):
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


# --- the whole run (SPEC.md §6 "Plaud"): fetch, ask, write, log, alert ---------------------------

def _calendar_writer(cfg: dict):
    """create_event bound to the stored token, deduplicated by title and day; None when the calendar is off."""
    if not C.get(cfg, "sources.calendar.enabled", False):
        return None
    from . import gcal

    def make(ev: dict):
        token = gcal.access_token(cfg)
        day = str(ev.get("start") or "")[:10]
        d0 = datetime.fromisoformat(day)
        for e in gcal.fetch_events(cfg, token, d0, d0 + timedelta(days=1)):
            if (e.get("summary") or "").strip().lower() == str(ev.get("title") or "").strip().lower():
                return {"id": e["id"], "link": e.get("link")}      # already there: link it, do not duplicate
        return gcal.create_event(cfg, token, ev)
    return make


def _alert(cfg: dict):
    """Failure alert: ntfy when enabled, else the brief's mailbox."""
    from . import brief, notify

    def send(title: str, body: str):
        if notify.enabled(cfg):
            notify.publish(cfg, title, body[:1500], tags=["warning"], priority=4)
        elif C.get(cfg, "notify.morning_brief.to"):
            brief.smtp_mailer(cfg, C.get(cfg, "notify.morning_brief.to"), title, body, f"<pre>{html.escape(body)}</pre>")
    return send


def run(conn: sqlite3.Connection, cfg: dict, *, subject: str | None, received: str | None, plaud_id: str | None = None, dry_run: bool = False,
        today: date | None = None, runner=None, plaud_run=None, notes=None, calendar="auto", alert=None, sleep=None, retry_delay_s: int | None = None) -> dict:
    """Sync one recording. Locates it from the mail subject (or takes `plaud_id`), pulls the two
    files, runs Claude once (one retry on any failure), applies the JSON, writes the note page,
    logs the run. A failure alerts and writes nothing. Returns {"ok", "rec", "summary", …}."""
    import subprocess
    import time as _time
    from . import plaud
    from .claude import runner as _runner
    from .db import add_link, log_run
    today = today or date.today()
    runner = runner or _runner.run
    plaud_run = plaud_run or subprocess.run
    sleep = sleep or _time.sleep
    alert = alert or _alert(cfg)
    delay = RETRY_DELAY_S if retry_delay_s is None else retry_delay_s
    started = _time.monotonic()
    label = subject or plaud_id or "?"

    def fail(why: str, cost: float = 0.0) -> dict:
        log_run(conn, "sync-file", int((_time.monotonic() - started) * 1000), ok=False, summary=label[:200], error=why[:1000], cost_usd=cost)
        conn.commit()
        alert(f"Plaud sync failed: {label[:80]}", f"{why}\n\nRe-run: python -m factotum sync-file {plaud_id or '<id>'}")
        return {"ok": False, "error": why, "rec": None}
    try:
        if plaud_id:
            rec = recording(cfg, plaud.details(cfg, plaud_id, run_=plaud_run))
        else:
            found = plaud.locate(cfg, subject or "", (received or today.isoformat())[:10], run_=plaud_run)
            if not found:
                return fail(f"no Plaud recording matches the subject within a day of the mail: {subject!r}")
            rec = recording(cfg, plaud.details(cfg, found.id, run_=plaud_run), found.name, found.date)
        workdir = Path(RUNS_DIR) / rec["id"]
        files = plaud.fetch_files(cfg, rec["id"], workdir, run_=plaud_run)
    except plaud.PlaudError as e:
        return fail(f"plaud: {e}")
    allowed = {r["id"] for r in slice_rows(conn)}
    prompt = build_prompt(cfg, conn, rec, received or "", files, today)
    (workdir / "prompt.md").write_text(prompt, encoding="utf-8")
    cost = 0.0
    res = None
    for attempt in (1, 2):
        res = runner(cfg, "sync_file", prompt, allowed_tools=["Read"], workdir=workdir)
        cost += res.cost_usd
        (workdir / f"result-{attempt}.txt").write_text(res.result_text or "", encoding="utf-8")
        if res.ok and isinstance(res.data, dict) and isinstance(res.data.get("tasks", []), list):
            break
        why = res.error or "unexpected JSON shape"
        log_run(conn, "sync-file", res.duration_ms, ok=False, summary=f"attempt {attempt}: {label[:160]}", error=why[:1000], cost_usd=res.cost_usd)
        conn.commit()
        if attempt == 1 and not res.auth_failed:
            sleep(delay)
            continue
        return fail(("auth: " if res.auth_failed else "") + why, 0.0)
    data = res.data
    if dry_run:
        log_run(conn, "sync-file", res.duration_ms, ok=True, summary=f"dry run: {rec['name'][:120]} → {len(data.get('tasks') or [])} tasks, {len(data.get('instructions') or [])} instructions", cost_usd=res.cost_usd)
        conn.commit()
        return {"ok": True, "rec": rec, "data": data, "prompt": prompt, "cost_usd": cost}
    cal = _calendar_writer(cfg) if calendar == "auto" else calendar
    summary = apply_result(conn, cfg, rec, data, today, calendar=cal, allowed_ids=allowed)
    note_url = None
    note_err = None
    if C.get(cfg, "sinks.notion_notes.enabled", True):
        notes = notes or (lambda cfg, rec, note, transcript, **kw: __import__("factotum.notion_notes", fromlist=["upsert"]).upsert(cfg, rec, note, transcript, **kw))
        page_ids = [r["notion_page_id"] for r in rows(conn, f"SELECT notion_page_id FROM tasks WHERE id IN ({','.join('?' * len(summary['task_ids']))}) AND notion_page_id IS NOT NULL", tuple(summary["task_ids"]))] if summary["task_ids"] else []
        extra = []
        applied_lines = [f"{i.get('op')} Task-{i.get('task_id')}: {i.get('evidence') or ''}"[:200] for i in (data.get("instructions") or []) if isinstance(i, dict)]
        if applied_lines:
            extra.append(("Instructions applied", applied_lines))
        if summary["unresolved"]:
            extra.append(("Unresolved", summary["unresolved"]))
        try:
            note_url = notes(cfg, rec, data.get("note") or {}, files.transcript.read_text(encoding="utf-8", errors="replace"), task_page_ids=page_ids, extra_sections=extra)
            for tid in summary["task_ids"]:
                add_link(conn, tid, "note_page", note_url, label=str(rec.get("name") or "")[:120])
            conn.commit()
        except Exception as e:   # the tasks are in; the page can be re-synced
            note_err = f"note page: {type(e).__name__}: {e}"[:300]
            summary["unresolved"].append(note_err)
    line = (f"{rec['name'][:80]}: {len(summary['created'])} created, {len(summary['updated'])} updated, {summary['applied']} instructions, "
            f"{summary['reminders']} reminders, {summary['events']} events, {len(summary['unresolved'])} unresolved"
            + (f"; note: {note_url}" if note_url else "") + (f"; batch {summary['batch_id']}" if summary["batch_id"] else ""))
    log_run(conn, "sync-file", int((_time.monotonic() - started) * 1000), ok=True, summary=line[:1000], error=note_err, cost_usd=cost)
    conn.commit()
    return {"ok": True, "rec": rec, "summary": summary, "note_url": note_url, "cost_usd": cost, "line": line}
