"""Wave 9: removing a due date (UI, command, connector), and the Today summary (SPEC.md §8.4): the
planner's verdict kept in the store with what has changed since, a light Claude run that rewrites
it when it goes stale, and a refresh the user asks for (the sweeps, then the planner)."""
import json
from datetime import date, datetime
from types import SimpleNamespace

from factotum import batches
from factotum.db import connect, get_settings, one, rows, set_setting, upsert_task

from conftest import TODAY, task
from test_phase4 import _seed_planner
from test_phase8 import _rpc, _text

DAY = date(2026, 11, 2)


# --- removing a due date ------------------------------------------------------------------------

def test_removing_a_due_date_clears_the_hard_flag_with_it_and_undo_restores_both(conn):
    upsert_task(conn, task(id=1, due="2026-11-08T23:59", due_is_datetime=1, hard=1))
    upsert_task(conn, task(id=2, due="2026-11-09", hard=0)); conn.commit()
    res = batches.apply(conn, [{"task_id": 1, "op": "set", "fields": {"due": None}}, {"task_id": 2, "op": "set", "fields": {"due": ""}}], "ui", TODAY)
    assert res["applied"] == 2 and not res["skipped"]
    t = one(conn, "SELECT due, hard, due_is_datetime FROM tasks WHERE id = 1")
    assert (t["due"], t["hard"], t["due_is_datetime"]) == (None, 0, 0)                       # a deadline with no date is not a hard one
    assert one(conn, "SELECT due FROM tasks WHERE id = 2")["due"] is None                    # "" is the same request, never an empty string in the column
    assert [c["field"] for c in rows(conn, "SELECT field FROM changes WHERE task_id = 2")] == ["due"]   # only what changed is logged
    batches.undo(conn, res["batch_id"])
    t = one(conn, "SELECT due, hard, due_is_datetime FROM tasks WHERE id = 1")
    assert (t["due"], t["hard"], t["due_is_datetime"]) == ("2026-11-08T23:59", 1, 1)


def test_a_connector_removes_a_due_date_with_null_and_the_tool_says_so(cfg, tmp_path):
    from factotum import mcp_server as M
    from factotum.db import init_db
    db = tmp_path / "w.db"
    c = connect(db); init_db(c)
    upsert_task(c, task(id=5, name="Reply to Max", project="SCIBER-CT", due="2026-11-05", hard=1))
    upsert_task(c, task(id=6, name="Slides", project="SCIBER-CT", due="2026-11-06")); c.commit()
    ctx = M.Context(connect(db), cfg, run_dir=tmp_path / "r", writer=lambda: connect(db), apply_writes=True, source="connector")
    tools = {t["name"]: t for t in _rpc(ctx, 1, "tools/list")["result"]["tools"]}
    assert "due: null" in tools["apply_changes"]["description"] and "due: null" in tools["stage_changes"]["description"]
    res = json.loads(_text(_rpc(ctx, 2, "tools/call", {"name": "apply_changes", "arguments": {"changes": [
        {"task_id": 5, "op": "set", "fields": {"due": None}}, {"task_id": 6, "op": "set", "fields": {"due": ""}}]}})))
    assert res["applied"] == 2 and not res["rejected"] and not res["ignored"], res
    t5 = one(c, "SELECT due, hard FROM tasks WHERE id = 5")
    assert (t5["due"], t5["hard"]) == (None, 0) and one(c, "SELECT due FROM tasks WHERE id = 6")["due"] is None
    assert M._rows_written({"op": "set", "fields": {"due": None}}) == 3                      # the hourly cap's bound counts the flags cleared with it


def test_nodue_is_a_command():
    from factotum import commands
    assert commands.parse("nodue 364 Task-365", TODAY) == [{"task_id": 364, "op": "set", "fields": {"due": None}}, {"task_id": 365, "op": "set", "fields": {"due": None}}]
    assert commands.parse("undate 7", TODAY) == [{"task_id": 7, "op": "set", "fields": {"due": None}}]


# --- the Today summary --------------------------------------------------------------------------

def test_a_summary_goes_stale_only_for_what_it_is_about_or_for_something_newly_urgent(conn):
    from factotum import summary
    upsert_task(conn, task(id=1, name="Submit PCA", due="2026-11-02", hard=1))
    upsert_task(conn, task(id=2, name="Slides for Max"))
    upsert_task(conn, task(id=3, name="Backlog thing")); conn.commit()
    assert summary.get(conn, DAY) is None
    summary.store(conn, "PCA is the day; the slides can wait.", [1, 2, 999], DAY, "planner", at="2026-11-02T06:31"); conn.commit()
    s = summary.get(conn, DAY)
    assert (s["text"], s["at"], s["source"], s["stale"]) == ("PCA is the day; the slides can wait.", "2026-11-02T06:31", "planner", [])
    batches.apply(conn, [{"task_id": 3, "op": "drop"}], "ui", DAY)                           # a row it never mentioned, and nothing urgent: still fresh
    assert summary.get(conn, DAY)["stale"] == []
    done = batches.apply(conn, [{"task_id": 1, "op": "done"}], "ui", DAY)
    st = summary.get(conn, DAY)["stale"]
    assert len(st) == 1 and "Task-1" in st[0] and "done" in st[0] and "Submit PCA" in st[0]
    batches.apply(conn, [{"task_id": 2, "op": "set", "fields": {"due": "2026-11-05"}}], "ui", DAY)
    st = summary.get(conn, DAY)["stale"]
    assert len(st) == 2 and any("Task-2" in x and "due" in x for x in st)
    upsert_task(conn, task(id=9, name="Call the registrar", due="2026-11-02")); conn.commit()           # new and due today: the summary could not know
    st = summary.get(conn, DAY)["stale"]
    assert len(st) == 3 and any("Task-9" in x and "due today" in x for x in st)
    batches.undo(conn, done["batch_id"])
    assert not any("Task-1" in x for x in summary.get(conn, DAY)["stale"])                   # an undone change is no change
    nxt = summary.get(conn, date(2026, 11, 3))["stale"]
    assert len(nxt) == 1 and "yesterday" in nxt[0]                                           # a new day says so once, not a line per row


def test_a_planner_run_stores_the_summary_and_a_second_run_the_same_day_makes_no_twin(conn, cfg):
    from factotum import planner, summary
    from factotum.claude.runner import RunResult
    _seed_planner(conn, cfg)
    fake = lambda out: (lambda cfg, kind, prompt, **kw: RunResult(True, out, json.dumps(out), 0.3, 2000))   # noqa: E731
    create = lambda name, eid: {"op": "create", "task_id": None, "fields": {"name": name}, "label": f"create: {name}", "evidence": [{"evidence_id": eid}] if eid else [], "reason": "r"}   # noqa: E731
    r1 = planner.run(conn, cfg, DAY, runner=fake({"verdict": "PCA first.", "verdict_tasks": [1, 4, 12345, "x"], "why_now": [], "proposals": [create("Go to the dentist", 12)], "dismiss": []}))
    assert r1["ok"] and len(r1["created"]) == 1
    s = summary.get(conn, DAY)
    assert s["text"] == "PCA first." and s["source"] == "planner" and s["stale"] == []
    about = get_settings(conn)["state.summary"]["tasks"]
    assert {"1", "4"} <= set(about) and "12345" not in about                                 # the rows it named that the bundle listed (and the urgent ones)
    r2 = planner.run(conn, cfg, DAY, runner=fake({"verdict": "Still PCA.", "why_now": [], "proposals": [create("Attend the UVA Link Lab visit", 11), create("Study for Quiz 7", None)], "dismiss": []}))
    assert len(r2["created"]) == 1 and r2["created"][0] != r1["created"][0]
    assert one(conn, "SELECT name FROM tasks WHERE id = ?", (r1["created"][0],))["name"] == "Go to the dentist"      # the morning's create is not overwritten
    assert summary.get(conn, DAY)["text"] == "Still PCA."
    prompt = planner.build_prompt(cfg, planner.bundle(conn, cfg, DAY), DAY)
    assert "Study for Quiz 7" in prompt and "verdict_tasks" in prompt                        # what already waits in Review is shown, so a later run does not suggest it again


def test_the_light_summary_run_writes_the_summary_and_nothing_else(conn, cfg):
    from factotum import summary
    from factotum.claude.runner import RunResult
    _seed_planner(conn, cfg)
    n_batches = one(conn, "SELECT COUNT(*) AS n FROM batches")["n"]
    calls = []
    good = {"summary": "PCA first; the visit is registered.", "task_ids": [1, 3, 555]}
    res = summary.run(conn, cfg, DAY, runner=lambda cfg, kind, prompt, **kw: (calls.append((kind, prompt, kw)), RunResult(True, good, json.dumps(good), 0.05, 900))[1], at="2026-11-02T10:00")
    kind, prompt, kw = calls[0]
    assert res["ok"] and kind == "today_summary" and kw["allowed_tools"] == [] and "Task-1" in prompt and "Submit COMPE 510 Assignment 5: PCA" in prompt
    s = summary.get(conn, DAY)
    assert (s["text"], s["source"], s["at"]) == ("PCA first; the visit is registered.", "summary", "2026-11-02T10:00")
    assert sorted(get_settings(conn)["state.summary"]["tasks"]) == ["1", "3"]
    assert one(conn, "SELECT COUNT(*) AS n FROM batches")["n"] == n_batches                  # no task was touched
    r = one(conn, "SELECT * FROM runs WHERE kind = 'today-summary'")
    assert r["ok"] == 1 and r["cost_usd"] == 0.05
    bad = summary.run(conn, cfg, DAY, runner=lambda *a, **k: RunResult(False, None, "Sorry", 0.01, 300, error="final message has no JSON object"), at="2026-11-02T10:30")
    assert not bad["ok"] and summary.get(conn, DAY)["at"] == "2026-11-02T10:00"
    assert one(conn, "SELECT COUNT(*) AS n FROM runs WHERE kind = 'today-summary' AND ok = 0")["n"] == 1


# --- the scheduler: a requested refresh, and the automatic rewrite -------------------------------

def _sched(cfg, conn, **kw):
    from factotum.scheduler import Scheduler
    return Scheduler(cfg, conn, push=lambda *a, **k: None, mailer=None, **kw)


def test_a_requested_refresh_sweeps_then_runs_the_planner_once_and_says_what_happened(conn, cfg):
    from factotum import summary
    calls = []
    mail = SimpleNamespace(sweep_job=lambda c, e, now: (calls.append("mail"), "flipped 1, closed 0, threads 0, candidates 0")[1])

    def canvas_job(c, e, now):
        calls.append("canvas")
        raise RuntimeError("canvas down")
    pl = SimpleNamespace(run=lambda c, e, today, calendar=None: (calls.append("planner"), {"ok": True, "verdict": "V.", "applied": ["a"], "staged": [], "line": "V. · applied 1"})[1])
    s = _sched(cfg, conn, mail=mail, canvas=SimpleNamespace(sweep_job=canvas_job), planner=pl)
    s.tick(datetime(2026, 11, 2, 10, 0))
    assert calls == ["canvas", "mail"]                                                       # the interval sweeps, as before; no planner on a plain tick
    calls.clear()
    s.tick(datetime(2026, 11, 2, 10, 1))
    assert calls == []
    summary.request_refresh(conn); conn.commit()
    assert summary.refresh_state(conn)["state"] == "requested"
    s.tick(datetime(2026, 11, 2, 10, 2))
    assert calls == ["mail", "canvas", "planner"]                                            # a sweep that fails does not stop the rest
    st = summary.refresh_state(conn)
    assert st["state"] == "idle" and st["last"]["ok"] and st["last"]["at"] == "2026-11-02T10:02"
    line = st["last"]["line"]
    assert "mail: flipped 1" in line and "canvas failed" in line and "applied 1" in line
    assert one(conn, "SELECT summary FROM runs WHERE kind = 'refresh' AND ok = 1")["summary"] == line
    calls.clear()
    s.tick(datetime(2026, 11, 2, 10, 3)); s.tick(datetime(2026, 11, 2, 10, 21))
    assert calls == []                                                                       # asked once, runs once; and the mail interval restarts from the refresh
    s.tick(datetime(2026, 11, 2, 10, 23))
    assert calls == ["mail"]


def test_a_refresh_left_running_by_a_restart_is_not_running_for_ever(conn, cfg):
    from factotum import summary
    set_setting(conn, "state.refresh.running", "2026-11-02T10:00"); conn.commit()
    assert summary.refresh_state(conn, now=datetime(2026, 11, 2, 10, 5))["state"] == "running"
    assert summary.refresh_state(conn, now=datetime(2026, 11, 2, 10, 40))["state"] == "idle"


def test_the_scheduler_rewrites_a_stale_summary_but_no_more_often_than_the_setting(conn, cfg):
    from factotum import config as C, summary
    for k in ("claude.summary.auto", "claude.summary.auto_minutes"):
        assert k in C.SETTINGS_KEYS, k
    upsert_task(conn, task(id=1, name="Submit PCA", due="2026-11-02", hard=1)); upsert_task(conn, task(id=2, name="Slides")); conn.commit()
    summary.store(conn, "Old.", [1], DAY, "planner", at="2026-11-02T06:31"); conn.commit()
    calls = []

    def run(c, e, today, calendar=None, at=None):
        calls.append(at)
        summary.store(c, "New.", [2], today, "summary", at=at)
        return {"ok": True, "text": "New.", "cost_usd": 0.05}
    s = _sched(cfg, conn, summary=SimpleNamespace(get=summary.get, run=run))
    s.tick(datetime(2026, 11, 2, 10, 0))
    assert calls == []                                                                       # fresh: nothing to do
    batches.apply(conn, [{"task_id": 1, "op": "done"}], "ui", DAY)
    s.tick(datetime(2026, 11, 2, 10, 1))
    assert calls == ["2026-11-02T10:01"] and summary.get(conn, DAY)["text"] == "New."
    batches.apply(conn, [{"task_id": 2, "op": "set", "fields": {"due": "2026-11-03"}}], "ui", DAY)
    s.tick(datetime(2026, 11, 2, 10, 5))
    assert len(calls) == 1                                                                   # stale again, but the last one is four minutes old
    s.tick(datetime(2026, 11, 2, 10, 12))
    assert len(calls) == 2
    set_setting(conn, "claude.summary.auto", False); conn.commit()
    batches.apply(conn, [{"task_id": 2, "op": "done"}], "ui", DAY)
    s.tick(datetime(2026, 11, 2, 11, 0))
    assert len(calls) == 2                                                                   # off in Settings: the badge stays until someone refreshes


def test_yesterdays_summary_is_rewritten_in_the_morning_not_at_midnight(conn, cfg):
    from factotum import summary
    upsert_task(conn, task(id=1, name="Submit PCA", due="2026-11-03", hard=1)); conn.commit()
    summary.store(conn, "Old.", [1], DAY, "planner", at="2026-11-02T06:31"); conn.commit()
    calls = []
    s = _sched(cfg, conn, summary=SimpleNamespace(get=summary.get, run=lambda c, e, today, calendar=None, at=None: (calls.append(at), {"ok": True, "text": "x", "cost_usd": 0})[1]))
    s.tick(datetime(2026, 11, 3, 0, 5))
    assert calls == []
    s.tick(datetime(2026, 11, 3, 6, 31))                                                     # no mailer here, so no brief wrote one first
    assert calls == ["2026-11-03T06:31"]


# --- the API ------------------------------------------------------------------------------------

def test_the_summary_endpoint_reports_staleness_and_a_refresh_goes_through_the_scheduler(cfg, tmp_path):
    from fastapi.testclient import TestClient
    from factotum import summary
    from factotum.api import create_app
    app = create_app(cfg, db_path=str(tmp_path / "api.db"))
    c = app.state.conn
    client = TestClient(app)
    r = client.get("/api/summary").json()
    assert r["summary"] is None and r["refresh"]["state"] == "idle" and r["can_refresh"] is False
    assert client.post("/api/summary/refresh", json={}).status_code == 409                   # an instance with no scheduler has nothing to run it
    woke = []
    app.state.scheduler = SimpleNamespace(wake=lambda: woke.append(1))
    from factotum import reminders
    today = reminders.local_now(cfg).date()                                                  # the endpoint's clock is the owner's
    upsert_task(c, task(id=1, name="Submit PCA", due=today.isoformat(), hard=1))
    summary.store(c, "PCA.", [1], today, "planner", at=today.isoformat() + "T06:31"); c.commit()
    r = client.get("/api/summary").json()
    assert r["summary"]["text"] == "PCA." and r["summary"]["stale"] == [] and r["can_refresh"] is True and r["auto"] is True
    client.post("/api/batches/apply", json={"source": "ui", "changes": [{"task_id": 1, "op": "done"}]})
    assert "Task-1" in client.get("/api/summary").json()["summary"]["stale"][0]
    p = client.post("/api/summary/refresh", json={})
    assert p.status_code == 200 and p.json()["refresh"]["state"] == "requested" and woke == [1]
    assert client.get("/api/summary").json()["refresh"]["state"] == "requested"
    client.post("/api/summary/refresh", json={})
    assert woke == [1]                                                                       # already asked: one refresh, not two


def test_the_apis_connection_survives_requests_running_at_once(cfg, tmp_path):
    """Sync routes run on a thread pool and share one connection. Python's sqlite3 caches prepared
    statements per connection, and two threads running the same SQL got the same statement: one
    reset it under the other, and a read came back short (a 500 on roughly one request in twenty
    when two arrived together, measured 2026-11-04). The API's connection keeps no statement cache."""
    import threading
    from factotum.api import create_app
    app = create_app(cfg, db_path=str(tmp_path / "api.db"))
    c = app.state.conn
    for i in range(60):
        set_setting(c, f"state.pad.{i}", {"i": i, "pad": "x" * 200})
    c.commit()
    bad = []

    def work():
        for _ in range(150):
            try:
                assert len(get_settings(c)) >= 60
            except Exception as e:   # noqa: BLE001 — any failure is the bug
                bad.append(f"{type(e).__name__}: {e}")
    ts = [threading.Thread(target=work) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not bad, f"{len(bad)} of 1200 reads failed, e.g. {bad[0]}"


# --- the review of wave 9 (2026-11-04) -----------------------------------------------------------

def test_a_change_made_while_the_summary_is_being_written_still_shows_as_stale(conn, cfg):
    """The run takes seconds to minutes. What the model read is the bundle, so the summary is
    remembered by the rows as they were in the bundle, not as they are when the text comes back."""
    from factotum import planner, summary
    from factotum.claude.runner import RunResult
    upsert_task(conn, task(id=1, name="Submit PCA", due="2026-11-02", hard=1)); upsert_task(conn, task(id=2, name="Slides")); conn.commit()

    def during(out, change):
        def runner(cfg, kind, prompt, **kw):
            batches.apply(conn, [change], "ui", DAY)                                         # the user, while Claude is thinking
            return RunResult(True, out, json.dumps(out), 0.1, 1000)
        return runner
    r = summary.run(conn, cfg, DAY, runner=during({"summary": "Submit the PCA today.", "task_ids": [1]}, {"task_id": 1, "op": "set", "fields": {"due": "2026-11-09"}}), at="2026-11-02T10:00")
    assert r["ok"] and any("Task-1" in x and "due moved" in x for x in summary.get(conn, DAY)["stale"])
    r = planner.run(conn, cfg, DAY, runner=during({"verdict": "Submit the PCA.", "verdict_tasks": [1], "why_now": [{"task_id": 2, "why_now": "soon"}], "proposals": [], "dismiss": []}, {"task_id": 1, "op": "done"}))
    st = summary.get(conn, DAY)["stale"]
    assert r["ok"] and len(st) == 1 and "Task-1" in st[0] and "done" in st[0]                # and the planner's own why_now edit to Task-2 is not a reason


def test_a_summary_is_about_the_urgent_rows_and_the_codes_in_its_text_even_when_the_model_lists_none(conn):
    from factotum import summary
    upsert_task(conn, task(id=1, name="Submit PCA", due="2026-11-02", hard=1)); upsert_task(conn, task(id=7, name="Read the GAIL paper")); conn.commit()
    summary.store(conn, "Submit the PCA; Task-7 can wait.", [], DAY, "summary", at="2026-11-02T10:00"); conn.commit()
    assert summary.get(conn, DAY)["stale"] == []
    batches.apply(conn, [{"task_id": 1, "op": "done"}, {"task_id": 7, "op": "drop"}], "ui", DAY)
    st = summary.get(conn, DAY)["stale"]
    assert any("Task-1" in x and "done" in x for x in st) and any("Task-7" in x and "dropped" in x for x in st)


def test_a_second_planner_run_does_not_stage_a_suggestion_that_is_already_waiting(conn, cfg):
    from factotum import planner
    from factotum.claude.runner import RunResult
    _seed_planner(conn, cfg)
    out = {"verdict": "V.", "why_now": [], "dismiss": [], "proposals": [
        {"op": "create", "task_id": None, "fields": {"name": "Study for Quiz 7", "project": "COMPE 510"}, "label": "create: Study for Quiz 7", "evidence": [], "reason": "announced"},
        {"op": "set", "task_id": 3, "fields": {"due": "2026-11-11"}, "label": "Task-3 → due Nov 11", "evidence": [], "reason": "hunch"}]}
    again = dict(out, proposals=[dict(out["proposals"][0], fields={"name": "study for  quiz 7", "project": "COMPE 510"}, label="create: study for quiz 7"), out["proposals"][1]])
    run = lambda o: planner.run(conn, cfg, DAY, runner=lambda cfg, kind, prompt, **kw: RunResult(True, o, json.dumps(o), 0.1, 1000))   # noqa: E731
    r1, r2 = run(out), run(again)
    assert len(r1["staged"]) == 2 and r2["staged"] == [] and len(r2["rejected"]) == 2 and "already" in r2["rejected"][0]
    assert one(conn, "SELECT COUNT(*) AS n FROM evidence WHERE kind = 'proposal' AND judged_at IS NULL")["n"] == 2


def test_a_recording_never_removes_a_due_date_by_leaving_it_empty(conn, cfg):
    from factotum import sync_file
    from test_phase3 import REC, _create
    upsert_task(conn, task(id=10, name="Submit the report", due="2026-11-06T23:59", due_is_datetime=1, hard=1))
    upsert_task(conn, task(id=11, name="Call Molina", due="2026-11-03", hard=1, source="Plaud", source_key=f"plaud:{REC['id']}:1")); conn.commit()
    data = {"note": {"properties": {"area": "Personal", "project": "Personal", "type": "Reminder", "tags": []}, "body_md": "x", "followups": []},
            "tasks": [{"op": "update", "id": 10, "due": None, "context": "Mentioned again"}, _create("Call Molina", due=None, source_key=f"plaud:{REC['id']}:1")],
            "instructions": [], "reminders": [], "calendar": [], "unresolved": []}
    sync_file.apply_result(conn, cfg, REC, data, TODAY, calendar=None)
    t = one(conn, "SELECT due, hard, due_is_datetime, context FROM tasks WHERE id = 10")
    assert (t["due"], t["hard"], t["due_is_datetime"]) == ("2026-11-06T23:59", 1, 1) and "Mentioned again" in (t["context"] or "")
    t = one(conn, "SELECT due, hard FROM tasks WHERE id = 11")
    assert (t["due"], t["hard"]) == ("2026-11-03", 1)                                        # the same recording synced twice keeps the date the row has
    upsert_task(conn, task(id=12, name="Soft thing", due="2026-11-20", hard=0)); conn.commit()
    sync_file.apply_result(conn, cfg, REC, dict(data, tasks=[{"op": "update", "id": 12, "hard": True}]), TODAY, calendar=None)
    assert one(conn, "SELECT hard FROM tasks WHERE id = 12")["hard"] == 1                    # an update that names no due at all still sets what it names


def test_due_null_with_hard_true_is_still_an_undated_soft_row(conn):
    upsert_task(conn, task(id=1, due="2026-11-08", hard=1)); conn.commit()
    batches.apply(conn, [{"task_id": 1, "op": "set", "fields": {"due": None, "hard": 1}}], "ui", TODAY)
    t = one(conn, "SELECT due, hard FROM tasks WHERE id = 1")
    assert (t["due"], t["hard"]) == (None, 0)


def test_a_failing_rewrite_backs_off_an_hour_and_a_bad_interval_does_not_stop_the_tick(conn, cfg):
    from factotum import summary
    upsert_task(conn, task(id=1, name="Submit PCA", due="2026-11-02", hard=1)); conn.commit()
    summary.store(conn, "Old.", [1], DAY, "planner", at="2026-11-02T06:31"); conn.commit()
    batches.apply(conn, [{"task_id": 1, "op": "done"}], "ui", DAY)
    calls = []
    s = _sched(cfg, conn, summary=SimpleNamespace(get=summary.get, run=lambda c, e, today, calendar=None, at=None: (calls.append(at), {"ok": False, "error": "auth: Not logged in"})[1]))
    s.tick(datetime(2026, 11, 2, 10, 0)); s.tick(datetime(2026, 11, 2, 10, 1)); s.tick(datetime(2026, 11, 2, 10, 30))
    assert len(calls) == 1                                                                   # stale, failing, and not a run every 30 seconds
    s.tick(datetime(2026, 11, 2, 11, 1))
    assert len(calls) == 2
    set_setting(conn, "claude.summary.auto_minutes", "soon"); set_setting(conn, "state.last_fail.today_summary", None); conn.commit()
    s.tick(datetime(2026, 11, 2, 12, 5))                                                      # must not raise
    assert len(calls) == 3


def test_a_refresh_request_older_than_twenty_minutes_is_dropped_not_run(conn, cfg):
    from factotum import summary
    calls = []
    pl = SimpleNamespace(run=lambda c, e, today, calendar=None: (calls.append("planner"), {"ok": True, "verdict": "V."})[1])
    summary.request_refresh(conn, datetime(2026, 11, 2, 9, 0)); conn.commit()
    _sched(cfg, conn, planner=pl).tick(datetime(2026, 11, 2, 10, 0))                           # the service was down when it was asked
    assert calls == [] and not get_settings(conn).get("state.refresh.requested")


def test_the_planner_is_told_the_time_and_what_was_closed_today(conn, cfg):
    from factotum import planner
    _seed_planner(conn, cfg)
    batches.apply(conn, [{"task_id": 2, "op": "done"}], "ui", DAY)
    prompt = planner.build_prompt(cfg, planner.bundle(conn, cfg, DAY, now=datetime(2026, 11, 2, 15, 4)), DAY)
    assert "it is 15:04" in prompt and "Task-2 Prepare the slides Max asked for (done)" in prompt
