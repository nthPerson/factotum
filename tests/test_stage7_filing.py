"""Stage 7, filing a task made by hand (design §10.3): the run, its queue, its chips."""
import copy
import json
from datetime import date, datetime

from factotum import batches, config as C, filing, planner, relations, suggestions
from factotum.claude.runner import RunResult
from factotum.db import connect, get_settings, init_db, one, rows, set_setting
from stage4_helpers import TODAY, active_rels, mk

GOODF = {"project": "DiCE Lab", "effort": "S", "link": {"kind": "part_of", "task_id": 1, "why": "a piece of chapter 2"}, "twin": {"task_id": 2, "why": "asked before"}}


def _made(conn):
    mk(conn, 1, name="Thesis chapter 2", project="BDA M.S."); mk(conn, 2, name="Email the lab for data", project="DiCE Lab")
    return batches.apply(conn, [{"op": "create", "fields": {"name": "Ask the lab for the dataset"}}], "ui", TODAY)["created"][0]


def _runner(data, calls=None):
    return lambda cfg, kind, prompt, **kw: ((calls.append((kind, prompt, kw)) if calls is not None else None), RunResult(True, data, json.dumps(data), 0.01, 900))[1]


def test_filing_stores_proposals_and_changes_nothing_the_owner_typed(conn, cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(filing, "RUNS_DIR", tmp_path / "runs")
    tid = _made(conn)
    before = dict(one(conn, "SELECT * FROM tasks WHERE id = ?", (tid,)))
    calls = []
    res = filing.run(conn, cfg, tid, runner=_runner(GOODF, calls), today=TODAY)
    kind, prompt, kw = calls[0]
    assert res["ok"] and kind == "filing" and kw["tools"] == "" and kw["allowed_tools"] == []
    assert "Ask the lab for the dataset" in prompt and "Task-1 | BDA M.S. |" in prompt and f"Task-{tid} |" not in prompt and "{{" not in prompt
    assert dict(one(conn, "SELECT * FROM tasks WHERE id = ?", (tid,))) == before                        # nothing the owner typed is changed
    fe = rows(conn, "SELECT ref, label FROM evidence WHERE kind = 'filing' AND task_id = ? ORDER BY id", (tid,))
    assert [(json.loads(r["ref"]), r["label"]) for r in fe] == [({"field": "project", "value": "DiCE Lab"}, "DiCE Lab?"), ({"field": "effort", "value": "S"}, "S?")]
    assert {(s["kind"], s["a"], s["b"], s["signal"]) for s in suggestions.pending(conn)} == {("part_of", tid, 1, "filing"), ("same_as", tid, 2, "filing")}
    assert active_rels(conn) == set()
    assert one(conn, "SELECT kind, ok, cost_usd FROM runs ORDER BY id DESC LIMIT 1") == {"kind": "filing", "ok": 1, "cost_usd": 0.01}
    assert res["line"].startswith(f"Task-{tid}: 4 suggested") and f"Task-{tid}, made" in prompt


def test_filing_names_only_tasks_it_was_shown_and_never_a_field_set_meanwhile(conn, cfg):
    tid = _made(conn)
    mk(conn, 90, name="Closed one", status="done", completed_at="2026-10-30")
    batches.apply(conn, [{"op": "set", "task_id": tid, "fields": {"project": "Personal"}}], "ui", TODAY)       # filed by the owner while the run was out
    before = dict(one(conn, "SELECT * FROM tasks WHERE id = ?", (tid,)))
    out = filing.apply(conn, cfg, tid, {"project": "DiCE Lab", "effort": "XXL", "link": {"kind": "waits_on", "task_id": 90, "why": "x"},
                                        "twin": {"task_id": 999, "why": "x"}}, shown={1, 2})
    assert out["stored"] == [] and sorted(out["dropped"]) == sorted(["project: already set", "effort: 'XXL' is not an effort",
                                                                      "link: Task-90 was not shown", "twin: Task-999 was not shown"])
    assert not rows(conn, "SELECT 1 FROM evidence WHERE kind IN ('filing', 'link_suggestion')")
    assert dict(one(conn, "SELECT * FROM tasks WHERE id = ?", (tid,))) == before
    out = filing.apply(conn, cfg, tid, {"project": None, "effort": "M", "link": None, "twin": None}, shown={1, 2})
    assert out == {"stored": ["M?"], "dropped": []}


def test_filing_evidence_is_neither_the_planners_nor_a_review_decisions(conn, cfg, tmp_path):
    from fastapi.testclient import TestClient
    from factotum import api
    path = tmp_path / "t.db"
    c = connect(path); init_db(c)
    tid = _made(c)
    filing.apply(c, cfg, tid, {"project": "Personal", "effort": None, "link": None, "twin": None}, shown=set()); c.commit()
    assert "filing" not in [e["kind"] for e in planner.bundle(c, cfg, TODAY)["candidates"]]
    items = {i["task"]["id"]: i for i in TestClient(api.create_app(cfg, db_path=str(path))).get("/api/review").json()["items"]}
    assert items[tid]["evidence"] == []


def test_file_task_dry_run_prints_and_stores_nothing(conn, cfg, tmp_path, monkeypatch, capsys):
    from conftest import FIXTURE
    from factotum import __main__ as cli
    from factotum.claude import runner as R
    path = tmp_path / "cli.db"
    c = connect(path); init_db(c)
    tid = _made(c)
    monkeypatch.setenv("FACTOTUM_CONFIG", str(FIXTURE)); monkeypatch.setenv("FACTOTUM_DB", str(path))
    monkeypatch.setattr(filing, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(R, "run", _runner(GOODF))
    capsys.readouterr()
    cli.main(["file-task", str(tid), "--dry-run"])
    out = capsys.readouterr().out
    assert '"project": "DiCE Lab"' in out and "dry run" in out
    assert not rows(c, "SELECT 1 FROM evidence WHERE kind IN ('filing', 'link_suggestion')")


def test_the_filing_run_kind_has_a_small_model_and_budget_in_the_example_and_the_fixture():
    import yaml
    from conftest import FIXTURE
    for p in (C.EXAMPLE_CONFIG, FIXTURE):
        y = yaml.safe_load(p.read_text())
        assert (C.get(y, "claude.models.filing"), C.get(y, "claude.budgets_usd.filing"), C.get(y, "claude.timeouts_s.filing")) == ("haiku", 0.1, 120), p


def test_a_task_made_by_hand_with_only_a_name_is_queued_and_nothing_else_is(conn):
    plain = batches.apply(conn, [{"op": "create", "fields": {"name": "Water the plants"}}], "ui", TODAY)["created"][0]
    filed = batches.apply(conn, [{"op": "create", "fields": {"name": "Pay rent", "project": "Personal"}}], "ui", TODAY)["created"][0]
    chat = batches.apply(conn, [{"op": "create", "fields": {"name": "From chat"}}], "chat", TODAY)["created"][0]
    mk(conn, 500)                                                                              # a high id: the creates above took the low ones
    linked = batches.apply(conn, [{"op": "create", "fields": {"name": "A step", "links": [{"kind": "part_of", "other": 500}]}}], "ui", TODAY)["created"][0]
    assert filing.queued(conn, date.today()) == [plain] and not conn.in_transaction            # not `filed`, `chat` or `linked`


def test_filing_runs_from_the_scheduler_only_when_switched_on_and_never_twice(conn, cfg, tmp_path, monkeypatch):
    from factotum.claude import runner as R
    from factotum.scheduler import Scheduler
    monkeypatch.setattr(filing, "RUNS_DIR", tmp_path / "runs")
    calls = []
    monkeypatch.setattr(R, "run", _runner({"project": None, "effort": "S", "link": None, "twin": None}, calls))
    tid = batches.apply(conn, [{"op": "create", "fields": {"name": "Water the plants"}}], "ui", TODAY)["created"][0]
    now = datetime.now().replace(second=0, microsecond=0)
    Scheduler(cfg, conn, push=lambda *a, **k: None, mailer=None).tick(now)                    # the fixture leaves filing off
    assert calls == [] and filing.queued(conn, now.date()) == [tid]
    on = copy.deepcopy(cfg); C.set_(on, "claude.filing.enabled", True)
    did = Scheduler(on, conn, push=lambda *a, **k: None, mailer=None).tick(now)
    assert [k for k, *_ in calls] == ["filing"] and filing.queued(conn, now.date()) == [] and any(d.startswith("filing: ") for d in did)
    assert [f["value"] for f in filing.chips(conn, {tid})[tid]] == ["S"]
    Scheduler(on, conn, push=lambda *a, **k: None, mailer=None).tick(now)
    assert len(calls) == 1                                                                       # dequeued: never filed twice
    set_setting(conn, filing.QUEUE_KEY, [{"task_id": tid, "day": "2020-01-01"}]); conn.commit()
    assert filing.queued(conn, now.date()) == []
    assert filing.run_queue(conn, on, now) is None and len(calls) == 1 and get_settings(conn)[filing.QUEUE_KEY] == []   # a stale entry is dropped unrun


def test_a_queued_task_closed_or_filed_meanwhile_is_dequeued_without_a_run(conn, cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(filing, "RUNS_DIR", tmp_path / "runs")
    calls = []
    run = _runner({"project": None, "effort": "S", "link": None, "twin": None}, calls)
    made = [batches.apply(conn, [{"op": "create", "fields": {"name": n}}], "ui", TODAY)["created"][0] for n in ("Water the plants", "Call the bank", "Fix the bike")]
    now = datetime.now().replace(second=0, microsecond=0)
    assert filing.queued(conn, now.date()) == made                                              # a create queues it
    batches.apply(conn, [{"op": "done", "task_id": made[0]},
                         {"op": "set", "task_id": made[1], "fields": {"project": "DiCE Lab", "effort": "S"}}], "ui", TODAY)
    line = filing.run_queue(conn, cfg, now, runner=run)
    assert len(calls) == 1 and str(made[2]) in calls[0][1] and filing.queued(conn, now.date()) == []
    assert f"Task-{made[0]}: skipped" in line and f"Task-{made[1]}: skipped" in line


def test_the_list_carries_filing_chips_until_the_field_is_set_and_a_create_wakes_the_scheduler(cfg, tmp_path):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from factotum import api
    app = api.create_app(cfg, db_path=str(tmp_path / "t.db"))
    c = app.state.conn
    woke = []
    app.state.scheduler = SimpleNamespace(wake=lambda: woke.append(1))
    client = TestClient(app)
    tid = client.post("/api/batches/apply", json={"source": "ui", "changes": [{"op": "create", "fields": {"name": "Water the plants"}}]}).json()["created"][0]
    assert woke == [1]
    filing.apply(c, cfg, tid, {"project": "Personal", "effort": "XS", "link": None, "twin": None}, shown=set()); c.commit()
    t = {x["id"]: x for x in client.get("/api/tasks").json()["tasks"]}[tid]
    assert [(f["field"], f["value"], f["label"]) for f in t["filing"]] == [("project", "Personal", "Personal?"), ("effort", "XS", "XS?")]
    client.post("/api/batches/apply", json={"source": "ui", "changes": [{"task_id": tid, "op": "set", "fields": {"effort": "M"}}]})
    assert woke == [1] and [f["field"] for f in client.get(f"/api/tasks/{tid}").json()["filing"]] == ["project"]


def test_the_filing_switch_is_off_when_its_key_is_missing():
    import yaml
    from conftest import FIXTURE
    assert "claude.filing.enabled" in C.SETTINGS_KEYS and C.DEFAULTS["claude.filing.enabled"] is False and C.value({}, "claude.filing.enabled") is False
    assert "claude.filing.enabled" not in C.PAID_SWITCHES                                       # OLD_DEFAULTS would invent a difference for `effective --as-before`
    for p in (C.EXAMPLE_CONFIG, C.STARTER_CONFIG, FIXTURE):
        assert C.get(yaml.safe_load(p.read_text()), "claude.filing.enabled") is False, p
