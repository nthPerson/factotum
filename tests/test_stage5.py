"""Stage 5, ripples (Part A)."""
import pytest
from conftest import FIXTURE, TODAY
from stage4_helpers import link, mk
from stage5_helpers import no_claude, queue  # noqa: F401  (no_claude is autouse: no test reaches a real run)

from factotum import batches, ripple
from factotum import config as C
from factotum.claude import runner
from factotum.claude.runner import run as real_run

# --- Task 1: the ripple keys and the runner's per-run model and budget
RIPPLE_DEFAULTS = {"claude.ripple.enabled": False, "claude.ripple.model": "sonnet", "claude.ripple.delay_seconds": 60,
                   "claude.ripple.hourly_cap": 10, "claude.budgets_usd.ripple": 0.30}


def test_ripple_keys_are_settings_keys_and_ripples_are_off_when_missing():
    for k, v in RIPPLE_DEFAULTS.items():
        assert k in C.SETTINGS_KEYS and C.DEFAULTS[k] == v, k
    assert C.value({}, "claude.ripple.enabled") is False and "claude.ripple.enabled" not in C.PAID_SWITCHES


def test_the_example_the_fixture_and_the_starter_carry_the_ripple_keys():
    for path in (C.EXAMPLE_CONFIG, FIXTURE, C.STARTER_CONFIG):
        c = C.load(path)
        for k, v in RIPPLE_DEFAULTS.items():
            assert C.get(c, k, "absent") == v, (path.name, k)


def test_the_runner_takes_a_model_and_a_budget_for_one_run(cfg):
    cmd = runner.command(cfg, "ripple", [], tools="", model="haiku", budget_usd=0.25)
    assert cmd[cmd.index("--model") + 1] == "haiku" and cmd[cmd.index("--max-budget-usd") + 1] == "0.25"
    plain = runner.command(cfg, "sync_file", [])
    assert plain[plain.index("--model") + 1] == C.get(cfg, "claude.models.sync_file")
    seen = []
    real_run(cfg, "ripple", "p", tools="", model="opus", budget_usd=0.5,
             exec_=lambda cmd, **kw: seen.append(cmd) or type("R", (), {"stdout": "{}", "stderr": "", "returncode": 0})())
    assert seen[0][seen[0].index("--model") + 1] == "opus" and seen[0][seen[0].index("--max-budget-usd") + 1] == "0.5"


# --- Task 2: the queue
def test_closing_a_linked_task_queues_it_and_an_unlinked_one_does_not(conn):
    mk(conn, 1); mk(conn, 2); mk(conn, 3); link(conn, 2, 1, "waits_on")
    res = batches.apply(conn, [{"op": "done", "task_id": 1}, {"op": "done", "task_id": 3}], "ui", TODAY)
    assert res["ripple_queued"] == [1] and queue(conn)[1]["what"] == ["closed"] and 3 not in queue(conn)


@pytest.mark.parametrize("fields, what", [({"name": "New"}, ["name"]), ({"due": "2026-11-20"}, ["due"]), ({"hard": 1, "due": "2026-11-20"}, ["due", "hard"]),
                                          ({"outcome": "It worked"}, ["outcome"]), ({"context": "A rewritten plan"}, ["text"])])
def test_a_material_change_queues(conn, fields, what):
    mk(conn, 1, context="The plan"); mk(conn, 2); link(conn, 1, 2, "part_of")
    batches.apply(conn, [{"op": "set", "task_id": 1, "fields": fields}], "ui", TODAY)
    assert queue(conn)[1]["what"] == what


def test_whitespace_why_now_effort_fade_and_reopen_do_not_queue(conn):
    mk(conn, 1, context="The  plan"); mk(conn, 2); link(conn, 1, 2, "waits_on")
    batches.apply(conn, [{"op": "set", "task_id": 1, "fields": {"context": "The plan\n", "why_now": "x", "effort": "S"}}], "ui", TODAY)
    batches.apply(conn, [{"op": "fade", "task_id": 1}], "automation", TODAY)
    batches.apply(conn, [{"op": "reopen", "task_id": 1}], "ui", TODAY)
    assert queue(conn) == {}


def test_a_ripple_batch_never_queues(conn):
    mk(conn, 1); mk(conn, 2); link(conn, 2, 1, "waits_on")
    res = batches.apply(conn, [{"op": "set", "task_id": 2, "fields": {"context": "new"}}], "ripple", TODAY, cause={"kind": "ripple", "from": [1]})
    assert res["ripple_queued"] == [] and queue(conn) == {}


def test_two_edits_in_the_quiet_period_are_one_row(conn):
    mk(conn, 1, context="A"); mk(conn, 2); link(conn, 2, 1, "waits_on")
    b1 = batches.apply(conn, [{"op": "set", "task_id": 1, "fields": {"context": "B"}}], "ui", TODAY)["batch_id"]
    b2 = batches.apply(conn, [{"op": "done", "task_id": 1}], "ui", TODAY)["batch_id"]
    q = queue(conn)[1]
    assert q["batches"] == [b1, b2] and q["last_batch_id"] == b2 and q["what"] == ["closed", "text"] and q["now"] == 0


def test_an_outcome_runs_at_once_and_wakes_the_scheduler(conn):
    mk(conn, 1); mk(conn, 2); link(conn, 2, 1, "waits_on")
    woke = []
    ripple.set_waker(lambda: woke.append(1))
    try:
        batches.apply(conn, [{"op": "done", "task_id": 1}], "ui", TODAY)
        assert woke == [] and queue(conn)[1]["now"] == 0
        batches.apply(conn, [{"op": "set", "task_id": 1, "fields": {"outcome": "Chair can serve remotely"}}], "ui", TODAY)
        assert woke == [1] and queue(conn)[1]["now"] == 1 and queue(conn)[1]["what"] == ["closed", "outcome"]
    finally:
        ripple.set_waker(None)


def test_a_merge_queues_the_survivor_not_the_duplicate(conn):
    mk(conn, 1); mk(conn, 2); mk(conn, 3); link(conn, 3, 1, "waits_on")
    batches.apply(conn, [{"op": "merge", "task_id": 1, "fields": {"into": 2}}], "ui", TODAY)
    assert set(queue(conn)) == {2} and queue(conn)[2]["what"] == ["merge"]
