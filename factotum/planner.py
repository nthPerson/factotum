"""The morning planner (SPEC.md §8.2): Python builds the bundle, one Claude run judges it, Python
applies what is proven and stages the rest.

    bundle(conn, cfg, today, events=None) -> dict
    build_prompt(cfg, bundle, today)      -> str
    apply(conn, cfg, data, bundle, today) -> {verdict, verdict_tasks, why_now, applied, staged, rejected, created}
    run(conn, cfg, today, ...)            -> {ok, verdict, applied, staged, cost_usd, …} | {ok: False, error}

Rules Python enforces, whatever the model says: `why_now` only on rows the bundle listed (≤ 15); a
proposal on a task is applied only when every `evidence` row it cites exists, is unjudged and was
attached to THAT task by the sweep (an automatic close always leaves a link to that thread);
evidence with no task can justify a `create` and nothing else; a proposal that is uncited, or whose
evidence the model merely attributed, becomes a "Planner suggests" item in Review with the thread
linked; dismissals reach only evidence about listed rows; everything else is rejected and reported.
All changes to existing rows and the verdicts the batch can honour (applied, dismissed) ride in one
batch (source `planner`) so the Log tab undoes a morning; a created row is not in the batch (like
every import) and shows as New in Review instead, and its evidence is moved onto it and judged
outside the batch, like the evidence behind a staged proposal, so an undo cannot make twins.
"""
from __future__ import annotations

import html
import json
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from . import batches, config as C, lifecycle, scoring
from .db import add_link, add_mention, get_settings, log_run, now_iso, rows, upsert_task
from .sync_file import _clean_task_fields

PROMPT = C.ROOT / "prompts" / "planner-morning.md"
RUNS_DIR = C.ROOT / "data" / "runs" / "planner"
ROW_CAP = 60
CAND_CAP = 40          # newest unjudged evidence rows the prompt carries; older ones wait for Review
WHY_CAP = 15
PROPOSAL_CAP = 12
TASK_OPS = {"done", "drop", "set", "add_reminder", "pin_top", "longterm"}
NON_PROVING = {"slack_mention"}       # anyone in a workspace can write "Task-42 is done": a hint to cite, never proof to act on


def bundle(conn: sqlite3.Connection, cfg: dict, today: date, events: list[dict] | None = None, now: datetime | None = None) -> dict:
    from . import reminders, summary
    from .mail_sweep import review_flags
    open_tasks = rows(conn, "SELECT * FROM tasks WHERE status = 'open'")
    for t in open_tasks:
        t["score"], t["priority"], _ = scoring.score(t, today, cfg)
    ordered = sorted(open_tasks, key=lambda t: (0 if t.get("top_rank") else 1, t.get("top_rank") or 0, -(t["score"] or 0), t["id"]))
    cands = rows(conn, "SELECT e.*, t.name AS task_name FROM evidence e LEFT JOIN tasks t ON t.id = e.task_id WHERE e.judged_at IS NULL AND e.kind != 'proposal' ORDER BY e.id DESC LIMIT ?", (CAND_CAP,))[::-1]
    anns = get_settings(conn).get("state.canvas.announcements") or []
    if isinstance(anns, str):
        try:
            anns = json.loads(anns)
        except ValueError:
            anns = []
    since = (today - timedelta(days=3)).isoformat()
    anns = [a for a in anns if isinstance(a, dict) and str(a.get("when") or "") >= since]
    rem = rows(conn, "SELECT r.fire_at, r.label, t.id, t.name FROM reminders r JOIN tasks t ON t.id = r.task_id WHERE r.status = 'pending' AND substr(r.fire_at, 1, 10) = ? ORDER BY r.fire_at", (today.isoformat(),))
    waiting = [t for t in open_tasks if t.get("waiting_on")]
    st = {r["key"]: r["value_json"] for r in rows(conn, "SELECT key, value_json FROM settings")}
    last = lifecycle.parse_date(st.get("state.last_review_at", "null").strip('"'))
    review_n = len(lifecycle.build_review_queue(open_tasks, today, cfg, last, flags_by_id=review_flags(conn), cap=10**9))
    pending = rows(conn, "SELECT label FROM evidence WHERE kind = 'proposal' AND judged_at IS NULL ORDER BY id DESC LIMIT 20")
    closed = rows(conn, "SELECT id, name, status FROM tasks WHERE status IN ('done', 'dropped') AND completed_at = ? ORDER BY id LIMIT 30", (today.isoformat(),))
    # `snap`: the open rows as this run reads them, so the summary it writes is remembered by what
    # the model saw (summary.store); `clock`: a run at 15:00 should not write a morning's verdict.
    return {"rows": ordered[:ROW_CAP], "candidates": cands, "announcements": anns, "reminders": rem, "events": events or [], "waiting": waiting, "review_n": review_n,
            "pending": pending, "closed": closed, "clock": (now or reminders.local_now(cfg)).strftime("%H:%M"), "snap": summary.snapshot(open_tasks, today)}


def _esc(v) -> str:
    return html.escape(str(v or ""), quote=False).replace("\n", " ")


def sections(b: dict, today: date) -> dict[str, str]:
    """The bundle as prompt text, one entry per {{placeholder}}; the Today summary's prompt uses the same."""
    row_lines = []
    for t in b["rows"]:
        due = (f"due {str(t['due'])[:16]}" + (" hard" if t.get("hard") else "")) if t.get("due") else "undated"
        bits = [f"Task-{t['id']}", f"{t['score']} {t['priority']}", t.get("project") or "—", due, _esc(t["name"])[:90], _esc(t.get("context"))[:140]]
        if t.get("top_rank"):
            bits.insert(2, f"top {t['top_rank']}")
        if t.get("waiting_on"):
            bits.insert(-1, f"waiting on {_esc(t['waiting_on'])[:60]}")
        row_lines.append(" | ".join(bits))
    cand_lines = [f"evidence {c['id']} | {c['kind']} | {c.get('account') or '—'} | " + (f"Task-{c['task_id']} ({_esc(c.get('task_name'))[:60]})" if c.get("task_id") else "no task") + f" | {_esc(c.get('label'))[:120]} | {_esc(c.get('excerpt'))[:500]}" for c in b["candidates"]]
    ann_lines = [f"{a.get('when') or '?'} | {_esc(a.get('project'))} | {_esc(a.get('title'))[:120]} | {_esc(a.get('text'))[:400]}" for a in b["announcements"]]
    rem_lines = [f"reminder {r['fire_at'][11:16]} Task-{r['id']} {_esc(r['name'])[:70]} ({_esc(r.get('label'))[:60]})" for r in b["reminders"]]
    ev_lines = [f"event {e.get('start')}{'' if e.get('all_day') else '–' + str(e.get('end') or '')[11:16]} {_esc(e.get('summary'))[:80]}" + (" (accepted)" if e.get("self_status") == "accepted" else "") for e in b["events"]]
    wait_lines = [f"waiting Task-{t['id']} {_esc(t['name'])[:70]} on {_esc(t['waiting_on'])[:60]}" for t in b["waiting"]]
    return {"today": today.strftime("%A %Y-%m-%d") + (f" · it is {b['clock']}" if b.get("clock") else "") + f" · Review holds {b['review_n']} items",
            "closed": "\n".join(f"Task-{c['id']} {_esc(c['name'])[:80]} ({c['status']})" for c in b.get("closed") or []) or "(nothing closed today)",
            "rows": "\n".join(row_lines) or "(no open rows)", "candidates": "\n".join(cand_lines) or "(nothing collected)",
            "announcements": "\n".join(ann_lines) or "(none)", "reminders": "\n".join(rem_lines) or "no reminders today",
            "events": "\n".join(ev_lines) or "calendar: off or empty", "waiting": "\n".join(wait_lines) or "nothing waiting on anyone",
            "pending": "\n".join("- " + _esc(p.get("label"))[:160] for p in b.get("pending") or []) or "(nothing)"}


def build_prompt(cfg: dict, b: dict, today: date) -> str:
    tpl = PROMPT.read_text(encoding="utf-8")
    for k, v in sections(b, today).items():
        tpl = tpl.replace("{{" + k + "}}", v)
    return tpl


def _norm(v) -> str:
    return " ".join(str(v or "").split()).casefold()


def _same_suggestion(a: dict, b: dict) -> bool:
    if a.get("op") != b.get("op"):
        return False
    if a.get("op") == "create":
        return _norm((a.get("fields") or {}).get("name")) == _norm((b.get("fields") or {}).get("name"))
    return a.get("task_id") == b.get("task_id") and (a.get("fields") or {}) == (b.get("fields") or {})


def apply(conn: sqlite3.Connection, cfg: dict, data: dict, b: dict, today: date) -> dict:
    allowed = {t["id"] for t in b["rows"]}
    # Suggestions already waiting in Review. The prompt lists them, but the planner now runs more than
    # once a day and a model can repeat itself: Python never stages the same card twice.
    waiting: list[dict] = []
    for r in rows(conn, "SELECT ref FROM evidence WHERE kind = 'proposal' AND judged_at IS NULL"):
        try:
            waiting.append(json.loads(r["ref"]))
        except (TypeError, ValueError):
            pass
    evidence = {e["id"]: e for e in rows(conn, "SELECT * FROM evidence WHERE judged_at IS NULL AND kind != 'proposal'")}
    changes: list[dict] = []
    applied: list[str] = []
    staged: list[str] = []
    rejected: list[str] = []
    created: list[int] = []
    judged: dict[int, str] = {}
    n_why = 0
    for w in (data.get("why_now") or [])[:WHY_CAP * 2]:
        if not isinstance(w, dict) or n_why >= WHY_CAP:
            continue
        tid, text = w.get("task_id"), str(w.get("why_now") or "").strip()
        if isinstance(tid, int) and tid in allowed and text:
            changes.append({"task_id": tid, "op": "set", "fields": {"why_now": text[:300]}}); n_why += 1
    n_create = 0
    for p in (data.get("proposals") or [])[:PROPOSAL_CAP]:
        if not isinstance(p, dict):
            continue
        op, tid, fields = p.get("op"), p.get("task_id"), dict(p.get("fields") or {})
        label = str(p.get("label") or f"{op} Task-{tid}")[:160]
        reason = str(p.get("reason") or "")[:300]
        cited = [evidence.get(int(e.get("evidence_id"))) for e in (p.get("evidence") or []) if isinstance(e, dict) and str(e.get("evidence_id") or "").isdigit()]
        cited = [e for e in cited if e and e["id"] not in judged]
        if op == "create":
            f = _clean_task_fields(cfg, fields)
            if not f.get("name"):
                rejected.append(f"{label}: no name"); continue
            if p.get("evidence") and not cited:
                rejected.append(f"{label}: cites evidence that does not exist or was already judged"); continue
            if not cited or any(e.get("kind") in NON_PROVING for e in cited):
                # An announcement, a hunch, or someone's Slack message: a suggestion card in Review, created
                # by nobody but the user. A non-proving citation is judged staged so no second card appears.
                sugg = {"op": "create", "fields": f}
                dup = any(_same_suggestion(sugg, w) for w in waiting)
                if not dup:
                    conn.execute("INSERT INTO evidence (task_id, kind, ref, label, excerpt, at) VALUES (NULL, 'proposal', ?, ?, ?, ?)",
                                 (json.dumps(sugg), label, reason, now_iso()))
                    waiting.append(sugg)
                for e in cited:
                    conn.execute("UPDATE evidence SET judged_at = ?, verdict = 'staged' WHERE id = ?", (now_iso(), e["id"]))
                    judged[e["id"]] = None
                (rejected if dup else staged).append(f"{label}: already waiting in Review" if dup else label); continue
            if any(e.get("task_id") is not None for e in cited):
                rejected.append(f"{label}: cites evidence that belongs to a task"); continue
            n_create += 1
            tid = upsert_task(conn, dict(f, status="open", source="Planner", source_key=f"planner:e{cited[0]['id']}", mentions=0, created_at=now_iso(), edited_at=now_iso()))
            add_mention(conn, tid, "planner", cited[0].get("ref"))
            for e in cited:
                if e.get("ref"):
                    add_link(conn, tid, "slack_message" if e.get("kind") == "slack_mention" else "mail_thread", e["ref"], label=e.get("label"), account=e.get("account"))
                # The create is not in the batch (like every creation), so its evidence is judged outside it
                # too and now belongs to the new task: an undo of the morning cannot make a twin.
                conn.execute("UPDATE evidence SET task_id = ?, judged_at = ?, verdict = 'applied' WHERE id = ?", (tid, now_iso(), e["id"]))
                judged[e["id"]] = None
            created.append(tid); applied.append(label)
            continue
        if op not in TASK_OPS or not isinstance(tid, int) or tid not in allowed:
            rejected.append(f"{label}: " + ("unknown op" if op not in TASK_OPS else "not a listed row")); continue
        if cited and any(e.get("task_id") not in (None, tid) for e in cited):
            rejected.append(f"{label}: cites evidence about another task"); continue
        if p.get("evidence") and not cited:
            rejected.append(f"{label}: cites evidence that does not exist or was already judged"); continue
        # Only evidence the sweep itself attached to this task proves anything; evidence with no task
        # (a confirmation, an invitation) attributed by the model is a judgment, so the proposal goes to
        # Review with the thread attached rather than being applied.
        proven = bool(cited) and all(e.get("task_id") == tid and e.get("kind") not in NON_PROVING for e in cited)
        if op == "set":
            f = _clean_task_fields(cfg, fields)
            if not f:
                rejected.append(f"{label}: nothing settable"); continue
            ch = {"task_id": tid, "op": "set", "fields": f}
        elif op == "add_reminder":
            try:
                ch = {"task_id": tid, "op": "add_reminder", "fields": batches._reminder_fields({"fields": fields})}
            except ValueError as e:   # a malformed reminder is rejected here, never judged "applied" for a change the batch skipped
                rejected.append(f"{label}: {e}"); continue
        else:
            ch = {"task_id": tid, "op": op, "fields": {}}
        for e in cited:
            if e.get("ref"):
                add_link(conn, tid, "slack_message" if e.get("kind") == "slack_mention" else "mail_thread", e["ref"], label=e.get("label"), account=e.get("account"))
            if proven:
                judged[e["id"]] = "applied"                   # in the batch: undoing the change un-judges the evidence
            else:                                             # the proposal row lives outside the batch, so its verdict does too
                conn.execute("UPDATE evidence SET judged_at = ?, verdict = 'staged' WHERE id = ?", (now_iso(), e["id"]))
                judged[e["id"]] = None
        if proven:
            changes.append(ch)
            applied.append(label)
        elif any(_same_suggestion(ch, w) for w in waiting):
            rejected.append(f"{label}: already waiting in Review")
        else:
            conn.execute("INSERT INTO evidence (task_id, kind, ref, label, excerpt, at) VALUES (?, 'proposal', ?, ?, ?, ?)",
                         (tid, json.dumps(ch), label, reason, now_iso()))
            waiting.append(ch)
            staged.append(label)
    for d in (data.get("dismiss") or [])[:20]:
        if not (isinstance(d, dict) and str(d.get("evidence_id") or "").isdigit()):
            continue
        eid = int(d["evidence_id"])
        e = evidence.get(eid)
        if e and eid not in judged and (e.get("task_id") is None or e.get("task_id") in allowed):
            judged[eid] = "dismissed"
    # Every verdict rides in the planner's batch (a judge_evidence change each), so undoing the
    # morning in the Log tab un-judges the evidence as well as reverting the rows.
    for eid, verdict in judged.items():
        if verdict:   # None = already judged outside the batch (a create or a staged proposal)
            changes.append({"task_id": evidence[eid].get("task_id"), "op": "judge_evidence", "fields": {"evidence_id": eid, "verdict": verdict}})
    res = batches.apply(conn, changes, "planner", today, closed_by="planner") if changes else {"skipped": [], "batch_id": None}
    for s in res["skipped"]:
        rejected.append(f"Task-{s.get('task_id')}: {s.get('why')}")
    conn.commit()
    about = [i for i in (data.get("verdict_tasks") if isinstance(data.get("verdict_tasks"), list) else []) if type(i) is int and i in allowed]
    return {"verdict": str(data.get("verdict") or "").strip()[:400], "verdict_tasks": about, "why_now": n_why, "applied": applied, "staged": staged,
            "rejected": rejected, "created": created, "batch_id": res.get("batch_id")}


def week_events(cfg: dict, today: date, calendar) -> list[dict] | None:
    if calendar is None or not C.get(cfg, "sources.calendar.enabled", False):
        return None
    try:
        return calendar.events(cfg, datetime.combine(today, datetime.min.time()), datetime.combine(today + timedelta(days=7), datetime.min.time()))
    except Exception:   # the planner runs without the calendar rather than not at all
        return None


def run(conn: sqlite3.Connection, cfg: dict, today: date, *, runner=None, calendar=None, dry_run: bool = False) -> dict:
    from .claude import runner as _runner
    runner = runner or _runner.run
    started = time.monotonic()
    b = bundle(conn, cfg, today, events=week_events(cfg, today, calendar))
    prompt = build_prompt(cfg, b, today)
    workdir = Path(RUNS_DIR) / today.isoformat()
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "prompt.md").write_text(prompt, encoding="utf-8")
    res = runner(cfg, "planner_morning", prompt, allowed_tools=[], workdir=workdir)
    (workdir / "result.txt").write_text(res.result_text or "", encoding="utf-8")
    ms = int((time.monotonic() - started) * 1000)
    if not res.ok or not isinstance(res.data, dict):
        log_run(conn, "planner-morning", ms, ok=False, summary="planner", error=(res.error or "no JSON")[:1000], cost_usd=res.cost_usd)
        conn.commit()
        return {"ok": False, "error": res.error or "no JSON", "cost_usd": res.cost_usd}
    if dry_run:
        log_run(conn, "planner-morning", ms, ok=True, summary=f"dry run: {str(res.data.get('verdict') or '')[:120]}", cost_usd=res.cost_usd)
        conn.commit()
        return {"ok": True, "dry_run": True, "data": res.data, "prompt": prompt, "cost_usd": res.cost_usd, "verdict": res.data.get("verdict")}
    s = apply(conn, cfg, res.data, b, today)
    if s["verdict"]:
        # The verdict is the Today tab's summary until something rewrites it (SPEC §8.4).
        from . import reminders, summary
        mine = [r["task_id"] for r in rows(conn, "SELECT DISTINCT task_id FROM changes WHERE batch_id = ?", (s["batch_id"],))] if s.get("batch_id") else []
        summary.store(conn, s["verdict"], s["verdict_tasks"], today, "planner", at=reminders.local_now(cfg).isoformat(timespec="minutes"),
                      snap=b["snap"], live=mine + s["created"])       # as the model read them, except what this run itself changed
    line = (f"{s['verdict'][:160]} · why_now {s['why_now']}, applied {len(s['applied'])}, for Review {len(s['staged'])}, rejected {len(s['rejected'])}"
            + (f"; batch {s['batch_id']}" if s.get("batch_id") else ""))
    log_run(conn, "planner-morning", ms, ok=True, summary=line[:1000], cost_usd=res.cost_usd)
    conn.commit()
    return dict(s, ok=True, cost_usd=res.cost_usd, line=line)
