"""Stage 5 (ripples) and stage 7 (suggestions, filing) together: what neither stage's own tests prove."""
import copy
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

from conftest import TODAY
from stage4_helpers import link, mk
from stage5_helpers import entry, no_claude, on, queue  # noqa: F401  (no_claude is autouse: no test reaches a real run)

import factotum.claude.runner as runner_mod
from factotum import batches, config as C, filing, relations, ripple, suggestions
from factotum.claude.runner import RunResult
from factotum.db import get_task, rows
from factotum.scheduler import Scheduler


def _twin_and_close(conn):
    """An open Canvas item, a task waiting on Task-2; one batch makes a hand-made twin of the item, a
    task with only a name (filing wants it) and closes Task-2. Returns (result, twin id, plain id)."""
    mk(conn, 1, name="Submit COMPE 510 Assignment 03", project="BDA M.S.", source="Canvas", due="2026-11-10T23:59")
    mk(conn, 2, name="Get the dataset"); mk(conn, 3, name="Clean the dataset", context="Waits on Task-2.")
    link(conn, 3, 2, "waits_on")
    res = batches.apply(conn, [{"op": "create", "fields": {"name": "Assignment 3 writeup", "project": "BDA M.S.", "due": "2026-11-11"}},
                               {"op": "create", "fields": {"name": "Water the plants"}},
                               {"op": "done", "task_id": 2}], "ui", TODAY)
    return res, res["created"][0], res["created"][1]


def test_one_batch_makes_a_twin_card_and_queues_a_ripple_and_its_undo_takes_both_back(conn, cfg):
    res, new, plain = _twin_and_close(conn)
    assert res["ripple_queued"] == [2] and set(queue(conn)) == {2}
    assert [(s["kind"], s["a"], s["b"], s["signal"]) for s in suggestions.pending(conn)] == [("same_as", new, 1, "twin")]
    assert [s["other"] for s in relations.annotate(conn, [{"id": 1}])[0]["rel"]["suggestions"]] == [new]
    assert filing.queued(conn, date.today()) == [plain] and not conn.in_transaction     # the hook ran after the batch committed

    out = batches.undo(conn, res["batch_id"])
    assert not out["already"] and queue(conn) == {}                                  # the ripple it queued never runs
    assert get_task(conn, new)["status"] == get_task(conn, plain)["status"] == "dropped" and get_task(conn, 2)["status"] == "open"
    ref = json.loads(rows(conn, "SELECT ref FROM evidence WHERE kind = 'link_suggestion'")[0]["ref"])
    assert not suggestions.live(conn, ref)                                           # the card leaves both ends at once
    assert relations.annotate(conn, [{"id": 1}])[0]["rel"]["suggestions"] == []


def test_one_tick_runs_a_due_ripple_and_a_queued_filing_each_in_its_own_transaction(conn, cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(filing, "RUNS_DIR", tmp_path / "filing-runs")
    _, _, plain = _twin_and_close(conn)
    at = (datetime.now(timezone.utc) - timedelta(seconds=600)).replace(microsecond=0).isoformat()
    conn.execute("UPDATE ripple_queue SET queued_at = ?", (at,)); conn.commit()     # past the quiet period
    calls = []

    def canned(cfg_, kind, prompt, **kw):
        calls.append((kind, conn.in_transaction))                                    # neither run starts inside the other's transaction
        data = ({"ripples": [entry(3, context="Task-2 is done: clean it.")]} if kind == "ripple"
                else {"project": None, "effort": "S", "link": None, "twin": None})
        return RunResult(True, data, json.dumps(data), 0.02, 900)
    monkeypatch.setattr(runner_mod, "run", canned)
    both = on(cfg); C.set_(both, "claude.filing.enabled", True)
    now = datetime.now().replace(second=0, microsecond=0)
    did = Scheduler(both, conn, push=lambda *a, **k: None, mailer=None, ripple=ripple).tick(now)

    assert sorted(calls) == [("filing", False), ("ripple", False)]
    assert any(d.startswith("filing: ") for d in did) and any(d.startswith("ripples: ") for d in did)
    assert get_task(conn, 3)["context"] == "Task-2 is done: clean it." and queue(conn) == {}
    assert filing.queued(conn, now.date()) == [] and [f["value"] for f in filing.chips(conn, {plain})[plain]] == ["S"]
    assert {r["kind"] for r in rows(conn, "SELECT kind FROM runs WHERE ok = 1")} >= {"ripple", "filing"}
    assert not conn.in_transaction
    other = sqlite3.connect(conn.execute("PRAGMA database_list").fetchone()[2], timeout=0.1)
    try:                                                                             # nothing holds the write lock after the tick
        other.execute("UPDATE tasks SET edited_at = edited_at WHERE id = 1"); other.commit()
    finally:
        other.close()
