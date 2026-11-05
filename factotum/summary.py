"""The Today summary (SPEC.md §8.4): the planner's verdict kept in the store, so the Today tab can
show it all day and say what has changed since it was written.

    store(conn, text, task_ids, today, source, at=None)   # `state.summary`; the caller commits
    get(conn, today)            -> {text, at, day, source, stale: [reason, …]} | None
    run(conn, cfg, today, …)    -> {ok, text, cost_usd}   # one light Claude run: the summary, nothing else
    request_refresh(conn, now)  /  refresh_state(conn, now) -> {state: idle|requested|running, last}

Stale is decided by Python, never by a model, and only for reasons the reader would care about: a
row the summary is about was closed, re-dated, put off or handed to someone; a row it could not know
became urgent (due today or tomorrow, overdue and hard, pinned to Top); or the day changed. An edit
to some other row leaves it fresh. The scheduler rewrites a stale summary with `run` (no writes but
the summary), and a refresh the user asks for runs the sweeps and the whole planner (scheduler.py).
"""
from __future__ import annotations

import re
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config as C
from .db import get_settings, log_run, rows, set_setting
from .lifecycle import parse_date

KEY = "state.summary"
PROMPT = C.ROOT / "prompts" / "today-summary.md"
RUNS_DIR = C.ROOT / "data" / "runs" / "summary"
TASKS_CAP = 8                       # rows the model may name as what the summary is "about"
ABOUT_CAP = 40                      # those, the codes in its text and the urgent rows together
CODE_RE = re.compile(r"\bTask-(\d{1,6})\b")
STALE_CAP = 12
FP = ("status", "due", "hard", "waiting_on", "snoozed_until")      # what a mentioned row is remembered by
STUCK_AFTER = timedelta(minutes=20)  # a request or a run older than this was lost to a restart


def _name(t: dict, n: int = 40) -> str:
    name = str(t.get("name") or "")
    return name if len(name) <= n else name[: n - 1].rstrip() + "…"


def _fp(t: dict) -> list:
    return [t.get("status"), t.get("due"), 1 if t.get("hard") else 0, t.get("waiting_on"), t.get("snoozed_until")]


def focus(tasks: list[dict], today: date) -> dict[int, str]:
    """The rows a summary of today cannot ignore, and why: {task id: "due today" | …}."""
    out: dict[int, str] = {}
    for t in tasks:
        if t.get("status") != "open" or (t.get("snoozed_until") and str(t["snoozed_until"])[:10] > today.isoformat()):
            continue
        d = parse_date(t["due"]) if t.get("due") else None
        days = (d - today).days if d else None
        if days is not None and days < 0 and t.get("hard"):
            out[t["id"]] = "overdue"
        elif days == 0:
            out[t["id"]] = "due today"
        elif days == 1:
            out[t["id"]] = "due tomorrow"
        elif t.get("top_rank"):
            out[t["id"]] = "pinned to Top"
    return out


def snapshot(open_tasks: list[dict], today: date) -> dict:
    """The open rows as a run is about to read them: {"fp": {id: fingerprint}, "focus": [ids]}."""
    return {"fp": {t["id"]: _fp(t) for t in open_tasks}, "focus": sorted(focus(open_tasks, today))}


def store(conn: sqlite3.Connection, text, task_ids, today: date, source: str, at: str | None = None, snap: dict | None = None, live=()) -> dict | None:
    """Keep a summary with the rows it is about: the ones the model named, the task codes in its
    text, and the urgent rows (a summary of today is about those whether it lists them or not).
    `snap` is the snapshot the run READ (planner.bundle): a row the user changed while Claude was
    writing is then remembered as the model saw it, and shows as stale at once. `live` names rows
    this run itself changed or created, which are remembered as they are now. Without `snap`, now."""
    text = " ".join(str(text or "").split())[:600]
    if not text:
        return None
    ids: list[int] = []
    for i in [i for i in (task_ids or []) if type(i) is int][:TASKS_CAP] + [int(m) for m in CODE_RE.findall(text)]:
        if i not in ids:
            ids.append(i)
    now = snapshot(rows(conn, "SELECT * FROM tasks WHERE status = 'open'"), today)
    seen = snap or now
    fps = dict(seen["fp"])
    urgent = set(seen["focus"])
    for i in live:
        if i in now["fp"]:
            fps[i] = now["fp"][i]
            urgent.add(i)                                    # a row this run made or moved is known to it, whatever its date
        else:
            fps.pop(i, None)                                 # closed by this run: nothing to watch
    about = [i for i in ids + sorted(urgent) if i in fps]
    s = {"text": text, "at": at or datetime.now().isoformat(timespec="minutes"), "day": today.isoformat(), "source": source,
         "tasks": {str(i): fps[i] for i in dict.fromkeys(about[:ABOUT_CAP])}, "focus": sorted(urgent)}
    set_setting(conn, KEY, s)
    return s


def _what(was: dict, now: dict) -> str:
    if now["status"] != was["status"]:
        return "reopened" if now["status"] == "open" else str(now["status"])
    if now["due"] != was["due"]:
        return f"due moved to {str(now['due'])[:16].replace('T', ' ')}" if now["due"] else "due date removed"
    if now["snoozed_until"] != was["snoozed_until"]:
        return f"put off until {str(now['snoozed_until'])[:10]}" if now["snoozed_until"] else "no longer put off"
    if now["waiting_on"] != was["waiting_on"]:
        return f"now waiting on {str(now['waiting_on'])[:40]}" if now["waiting_on"] else "no longer waiting on anyone"
    return "now a hard deadline" if now["hard"] else "no longer a hard deadline"


def stale(conn: sqlite3.Connection, s: dict, today: date) -> list[str]:
    day = str(s.get("day") or "")
    if day != today.isoformat():
        d = parse_date(day)
        return ["written yesterday" if d and (today - d).days == 1 else "written " + (d.strftime("%a %b ") + str(d.day) if d else "on another day")]
    out: list[str] = []
    about = {int(k): v for k, v in (s.get("tasks") or {}).items() if str(k).isdigit()}
    if about:
        cur = {t["id"]: t for t in rows(conn, f"SELECT * FROM tasks WHERE id IN ({','.join('?' * len(about))})", tuple(about))}
        for tid, was in about.items():
            t = cur.get(tid)
            if t and _fp(t) != list(was):
                out.append(f"Task-{tid} {_name(t)}: " + _what(dict(zip(FP, was)), dict(zip(FP, _fp(t)))))
    known = set(about) | {int(i) for i in (s.get("focus") or [])}
    open_tasks = rows(conn, "SELECT * FROM tasks WHERE status = 'open'")
    by = {t["id"]: t for t in open_tasks}
    for tid, why in focus(open_tasks, today).items():
        if tid not in known:
            out.append(f"Task-{tid} {_name(by[tid])}: {why}, and not in the summary")
    return out[:STALE_CAP]


def get(conn: sqlite3.Connection, today: date) -> dict | None:
    s = get_settings(conn).get(KEY)
    if not isinstance(s, dict) or not s.get("text"):
        return None
    return {"text": s["text"], "at": s.get("at"), "day": s.get("day"), "source": s.get("source"), "stale": stale(conn, s, today)}


# ---- the light run -----------------------------------------------------------------------------

def build_prompt(cfg: dict, b: dict, today: date) -> str:
    from .planner import sections
    fill = sections(b, today)
    fill["now"] = fill.pop("today")
    tpl = PROMPT.read_text(encoding="utf-8")
    for k, v in fill.items():
        tpl = tpl.replace("{{" + k + "}}", v)
    return tpl


def run(conn: sqlite3.Connection, cfg: dict, today: date, *, runner=None, calendar=None, at: str | None = None) -> dict:
    """One Claude run with no tools that returns {"summary", "task_ids"}; Python stores it. Tasks,
    evidence and proposals are not touched: that is the planner's job, once a morning or on request."""
    from . import planner, reminders
    from .claude import runner as _runner
    runner = runner or _runner.run
    at = at or reminders.local_now(cfg).isoformat(timespec="minutes")
    started = time.monotonic()
    try:
        clock = datetime.fromisoformat(at)
    except ValueError:
        clock = None
    b = planner.bundle(conn, cfg, today, events=planner.week_events(cfg, today, calendar), now=clock)
    prompt = build_prompt(cfg, b, today)
    workdir = Path(RUNS_DIR) / today.isoformat()
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "prompt.md").write_text(prompt, encoding="utf-8")
    res = runner(cfg, "today_summary", prompt, allowed_tools=[], workdir=workdir)
    (workdir / "result.txt").write_text(res.result_text or "", encoding="utf-8")
    ms = int((time.monotonic() - started) * 1000)
    text = str(res.data.get("summary") or "").strip() if res.ok and isinstance(res.data, dict) else ""
    if not text:
        err = (res.error or "the reply had no summary")[:1000]
        log_run(conn, "today-summary", ms, ok=False, summary="summary", error=err, cost_usd=res.cost_usd)
        conn.commit()
        return {"ok": False, "error": err, "cost_usd": res.cost_usd}
    listed = {t["id"] for t in b["rows"]}
    ids = [i for i in (res.data.get("task_ids") or []) if type(i) is int and i in listed]
    s = store(conn, text, ids, today, "summary", at=at, snap=b["snap"])
    log_run(conn, "today-summary", ms, ok=True, summary=s["text"][:300], cost_usd=res.cost_usd)
    conn.commit()
    return {"ok": True, "text": s["text"], "cost_usd": res.cost_usd}


# ---- a refresh somebody asked for ----------------------------------------------------------------
# The request is a row in `settings`; the scheduler (the one thread that sweeps) picks it up on its
# next tick, so a click in the UI can never run a second sweep beside the scheduled one.

def request_refresh(conn: sqlite3.Connection, now: datetime | None = None) -> None:
    set_setting(conn, "state.refresh.requested", (now or datetime.now()).isoformat(timespec="minutes"))


def refresh_state(conn: sqlite3.Connection, now: datetime | None = None) -> dict:
    st = get_settings(conn)
    now = now or datetime.now()

    def live(v) -> bool:
        if not v:
            return False
        try:
            return now - datetime.fromisoformat(str(v)[:16]) < STUCK_AFTER
        except ValueError:
            return False
    state = "running" if live(st.get("state.refresh.running")) else "requested" if live(st.get("state.refresh.requested")) else "idle"
    return {"state": state, "last": st.get("state.refresh.last")}
