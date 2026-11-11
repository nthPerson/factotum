"""Stage 5, ripples (Part A)."""
from conftest import FIXTURE

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
