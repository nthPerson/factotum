"""Helpers for the stage-5 (ripples) tests. Both stage-5 test files import `no_claude` from here,
so no test can reach a real Claude run."""
import copy
import json

import pytest

import factotum.claude.runner
from factotum import ripple
from factotum.claude.runner import RunResult


def fake_runner(data, cost=0.12, calls=None, during=None):
    """A stand-in for `runner.run`: records each call in `calls`, runs `during()` (an edit made while
    the run is in flight), and answers `data`, or a reply with no JSON object when `data` is None."""
    def run(cfg, kind, prompt, **kw):
        if calls is not None:
            calls.append(dict(kw, kind=kind, prompt=prompt))
        if during:
            during()
        if data is None:
            return RunResult(False, None, "Sorry", cost, 900, error="final message has no JSON object: Sorry")
        return RunResult(True, data, json.dumps(data), cost, 900)
    return run


def entry(task_id, effect="text", context=None, why="w", proposals=None) -> dict:
    """One entry of a ripple reply, every key present."""
    return {"task_id": task_id, "effect": effect, "context": context, "why": why, "proposals": proposals or []}


def on(cfg) -> dict:
    """A copy of `cfg` with ripples switched on."""
    c = copy.deepcopy(cfg)
    c.setdefault("claude", {}).setdefault("ripple", {})["enabled"] = True
    return c


def queue(conn) -> dict[int, dict]:
    """The ripple queue by task id."""
    return {r["task_id"]: r for r in ripple.pending(conn)}


@pytest.fixture(autouse=True)
def no_claude(monkeypatch, tmp_path):
    def refuse(*a, **kw):
        raise AssertionError("a real Claude run")
    monkeypatch.setattr(factotum.claude.runner, "run", refuse)
    monkeypatch.setattr(ripple, "RUNS_DIR", tmp_path / "runs")
