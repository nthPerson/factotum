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
from datetime import date
from pathlib import Path

from . import config as C
from . import relations, suggestions
from .db import log_run, now_iso, one, rows
from .relations import LINK_KINDS, WORDS
from .sync_file import slice_rows, taxonomy_text

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
    lines = [f"Task-{r['id']} | {r['project'] or '—'} | {str(r['due'])[:16] if r['due'] else 'undated'} | {(r['name'] or '')[:90]}"
             for r in rows_ if r["id"] != task["id"]]
    vals = {"task_id": str(task["id"]), "made": str(task.get("created_at") or "")[:10],
            "due": f"due {task['due']}" if task.get("due") else "undated",
            "name": html.escape(task.get("name") or ""), "taxonomy": taxonomy_text(cfg),
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
