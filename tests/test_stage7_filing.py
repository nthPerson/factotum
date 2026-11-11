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
