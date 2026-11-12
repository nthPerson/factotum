"""Filing a task the owner made by hand (design §10.3): one small run with no tools.

The run sees the task's name and the open rows and proposes a project, an effort, a likely link and
a likely twin. It writes no task field: project and effort become `filing` evidence on the task, the
link and the twin become `link_suggestion`s with signal `filing` (through `suggestions.suggest_pair`),
each only for a row the run was shown and only for a field still empty. The queue that decides which
tasks are filed is Task 9's; `factotum file-task ID [--dry-run]` runs one by hand.

    run(conn, cfg, task_id, runner=None, dry_run=False, today=None) -> {ok, stored, dropped, cost_usd, line}
"""
from __future__ import annotations

import html
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config as C
from . import relations, suggestions
from .db import get_settings, log_run, now_iso, one, rows, set_setting
from .relations import LINK_KINDS, WORDS
from .sync_file import flat, slice_rows, taxonomy_text

KIND = "filing"
PROMPT = C.ROOT / "prompts" / "filing.md"
RUNS_DIR = C.ROOT / "data" / "runs" / "filing"
ROW_CAP = 80


def _efforts(cfg: dict) -> list[str]:
    return list(C.get(cfg, "taxonomy.efforts", []) or ["XS", "S", "M", "L"])


def _unset(task: dict, field: str) -> bool:
    """The one test of "still empty" (apply, and Task 9's `wants` and `chips`)."""
    v = task.get(field)
    if field == "project":
        return v in (None, "", "Unknown")
    return v in (None, "")


def build_prompt(cfg: dict, conn, task: dict, rows_: list[dict]) -> str:
    lines = [f"Task-{r['id']} | {flat(r['project']) or '—'} | {flat(str(r['due'])[:16]) if r['due'] else 'undated'} | {flat(r['name'])[:90]}"
             for r in rows_ if r["id"] != task["id"]]
    vals = {"task_id": str(task["id"]), "made": str(task.get("created_at") or "")[:10],
            "due": f"due {task['due']}" if task.get("due") else "undated",
            "name": html.escape(flat(task.get("name"))), "taxonomy": taxonomy_text(cfg),
            "efforts": ", ".join(_efforts(cfg)), "rows": "\n".join(lines) or "(no other open tasks)"}
    tpl = PROMPT.read_text(encoding="utf-8")
    for k, v in vals.items():
        tpl = tpl.replace("{{" + k + "}}", v)
    return tpl


def _field(conn, task: dict, field: str, v, valid: list[str], noun: str, stored: list, dropped: list) -> None:
    if v is None:
        return
    if not isinstance(v, str) or v not in valid:
        dropped.append(f"{field}: '{v}' is not {noun}")
    elif not _unset(task, field):
        dropped.append(f"{field}: already set")
    elif not one(conn, "SELECT 1 AS x FROM evidence WHERE kind = 'filing' AND task_id = ? AND judged_at IS NULL AND ref = ?",
                 (task["id"], json.dumps({"field": field, "value": v}))):
        conn.execute("INSERT INTO evidence (task_id, kind, ref, label, excerpt, at) VALUES (?, 'filing', ?, ?, NULL, ?)",
                     (task["id"], json.dumps({"field": field, "value": v}), f"{v}?", now_iso()))
        stored.append(f"{v}?")


def _why(d: dict) -> str:
    return str(d.get("why") or "")[:300]


def apply(conn, cfg: dict, task_id: int, data: dict, shown: set[int]) -> dict:
    """Store what a filing run proposed. The caller commits; no task field is written."""
    stored: list[str] = []
    dropped: list[str] = []
    task = one(conn, "SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not task or task["status"] != "open":
        return {"stored": [], "dropped": [f"task: Task-{task_id} is not open"]}
    projects = [p for p in C.project_names(cfg) if p != "Unknown"]
    _field(conn, task, "project", data.get("project"), projects, "a project", stored, dropped)
    _field(conn, task, "effort", data.get("effort"), _efforts(cfg), "an effort", stored, dropped)

    def named(d, label):
        n = d.get("task_id")
        if type(n) is not int or n not in shown:
            dropped.append(f"{label}: Task-{n} was not shown")
            return None
        return n

    link = data.get("link")
    if isinstance(link, dict):
        n = named(link, "link")
        if n is not None:
            kind = link.get("kind")
            if kind not in LINK_KINDS:
                dropped.append("link: unknown kind")
            elif (why := suggestions.why_not(conn, kind, task_id, n)):
                dropped.append(f"link: {why}")
            else:
                suggestions.suggest_pair(conn, kind, task_id, n, _why(link), "filing")
                stored.append(f"{WORDS[kind]} Task-{n}?")
    twin = data.get("twin")
    if isinstance(twin, dict):
        n = named(twin, "twin")
        if n is not None:
            keep, goes = relations.default_survivor(conn, task_id, n)
            if (why := suggestions.why_not(conn, "same_as", goes, keep)):
                dropped.append(f"twin: {why}")
            else:
                suggestions.suggest_pair(conn, "same_as", goes, keep, _why(twin), "filing")
                stored.append(f"same as Task-{n}?")
    return {"stored": stored, "dropped": dropped}


def run(conn, cfg: dict, task_id: int, *, runner=None, dry_run: bool = False, today: date | None = None) -> dict:
    from .claude import runner as _runner
    runner = runner or _runner.run
    task = one(conn, "SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not task:
        return {"ok": False, "error": f"no Task-{task_id}"}
    task = dict(task)
    open_rows = [r for r in slice_rows(conn, ROW_CAP) if r["status"] == "open"]
    shown = {r["id"] for r in open_rows} - {task_id}
    prompt = build_prompt(cfg, conn, task, open_rows)
    workdir = Path(RUNS_DIR) / str(task_id)
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "prompt.md").write_text(prompt, encoding="utf-8")
    t0 = time.time()
    res = runner(cfg, KIND, prompt, allowed_tools=[], tools="", workdir=workdir)
    ms = int((time.time() - t0) * 1000)
    why = _runner.unavailable(res)
    if why and not dry_run:              # Claude cannot run here: noted for Settings, one failed row a day for it
        if _runner.note_unavailable(conn, KIND, why, today or date.today()):
            log_run(conn, KIND, ms, ok=False, summary=f"Task-{task_id}", error=f"{(res.error or why)[:600]} · filing waits until Claude runs on this machine")
        conn.commit()
        return {"ok": False, "error": res.error or why, "unavailable": why, "cost_usd": 0.0}
    if res.ok and not dry_run:
        _runner.clear_unavailable(conn)
    if not res.ok or not isinstance(res.data, dict):
        err = (res.error or "the run returned no JSON object")[:1000]
        log_run(conn, KIND, ms, ok=False, summary=f"Task-{task_id}", error=err, cost_usd=res.cost_usd)
        conn.commit()
        return {"ok": False, "error": err, "cost_usd": res.cost_usd}
    if dry_run:
        log_run(conn, KIND, ms, ok=True, summary=f"dry run: Task-{task_id}", cost_usd=res.cost_usd)
        conn.commit()
        return {"ok": True, "dry_run": True, "data": res.data, "prompt": prompt, "cost_usd": res.cost_usd}
    out = apply(conn, cfg, task_id, res.data, shown)
    stored, dropped = out["stored"], out["dropped"]
    line = (f"Task-{task_id}: {len(stored)} suggested" + (f" ({'; '.join(stored)})" if stored else "")
            + (f", {len(dropped)} dropped" if dropped else ""))
    log_run(conn, KIND, ms, ok=True, summary=line[:1000], cost_usd=res.cost_usd)
    conn.commit()
    return {"ok": True, "stored": stored, "dropped": dropped, "cost_usd": res.cost_usd, "line": line}


# --- the queue (design §10.3): filled at create, emptied by the scheduler -----------------------------

QUEUE_KEY = "state.filing.pending"
QUEUE_MAX = 50
PER_TICK = 3


def _queue(conn) -> list[dict]:
    q = get_settings(conn).get(QUEUE_KEY)
    return [e for e in q if isinstance(e, dict) and type(e.get("task_id")) is int and isinstance(e.get("day"), str)] if isinstance(q, list) else []


def _fresh(q: list[dict], today: date) -> list[dict]:
    keep = {today.isoformat(), (today - timedelta(days=1)).isoformat()}
    return [e for e in q if e["day"] in keep]


def wants(conn, task_id: int) -> bool:
    t = one(conn, "SELECT * FROM tasks WHERE id = ?", (task_id,))
    return bool(t and t["status"] == "open" and t["source"] == "Manual" and _unset(t, "project") and _unset(t, "effort")
                and not one(conn, "SELECT 1 AS x FROM relations WHERE src = ? OR dst = ?", (task_id, task_id)))


def enqueue(conn, ids: list[int], today: date | None = None) -> list[int]:
    """Queue the ids that `wants`; the caller commits."""
    today = today or date.today()
    q = _fresh(_queue(conn), today)
    have = {e["task_id"] for e in q}
    added = [i for i in ids if i not in have and wants(conn, i)]
    q += [{"task_id": i, "day": today.isoformat()} for i in added]
    set_setting(conn, QUEUE_KEY, q[-QUEUE_MAX:])
    return added


def queued(conn, today: date) -> list[int]:
    return [e["task_id"] for e in _fresh(_queue(conn), today)]


def run_queue(conn, cfg: dict, now: datetime, runner=None) -> str | None:
    q = _queue(conn)
    if len(_fresh(q, now.date())) != len(q):
        set_setting(conn, QUEUE_KEY, _fresh(q, now.date()))
        conn.commit()
    lines: list[str] = []
    for tid in queued(conn, now.date())[:PER_TICK]:
        set_setting(conn, QUEUE_KEY, [e for e in _queue(conn) if e["task_id"] != tid])
        conn.commit()                                   # dequeued before the run: a failure is not retried
        if not wants(conn, tid):                        # closed, filed by hand or linked meanwhile: no paid run
            lines.append(f"Task-{tid}: skipped (no longer only a name)")
            continue
        try:
            res = run(conn, cfg, tid, runner=runner, today=now.date())
        except Exception as e:
            conn.rollback()
            res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        if res.get("unavailable"):           # every other run would fail the same way: what waits is not filed (the run logged it)
            set_setting(conn, QUEUE_KEY, [])
            conn.commit()
            break
        lines.append(res.get("line") if res.get("ok") else f"Task-{tid}: failed ({str(res.get('error') or '')[:80]})")
    return "; ".join(lines) or None


def chips(conn, ids: set[int]) -> dict[int, list[dict]]:
    if not ids:
        return {}
    out: dict[int, list[dict]] = {}
    tasks = {t["id"]: t for t in rows(conn, f"SELECT * FROM tasks WHERE id IN ({','.join('?' * len(ids))})", tuple(ids))}
    for e in rows(conn, f"SELECT id, task_id, ref, label FROM evidence WHERE kind = 'filing' AND judged_at IS NULL AND task_id IN ({','.join('?' * len(ids))}) ORDER BY id", tuple(ids)):
        try:
            ref = json.loads(e["ref"])
            field, value = ref["field"], ref["value"]
        except (TypeError, ValueError, KeyError):
            continue
        t = tasks.get(e["task_id"])
        if t is None or not _unset(t, field):
            continue
        out.setdefault(e["task_id"], []).append({"evidence_id": e["id"], "field": field, "value": value, "label": e["label"]})
    return out
