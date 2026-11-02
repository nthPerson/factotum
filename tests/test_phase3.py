"""Phase 3: the Claude runner, the Plaud fetch, the sync-file prompt and apply, the Notion notes
sink and the IMAP trigger (SPEC.md §6, §7, §8.1)."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from factotum import batches
from factotum.db import upsert_task

from conftest import TODAY, task


# --- T1 the runner ----------------------------------------------------------------------------

def _envelope(result, *, is_error=False, cost=0.12, ms=1500):
    return json.dumps({"type": "result", "is_error": is_error, "result": result, "total_cost_usd": cost, "duration_ms": ms, "num_turns": 3})


def test_runner_builds_the_headless_command_and_parses_the_envelope(cfg, tmp_path):
    from factotum.claude import runner
    calls = []

    def fake_exec(cmd, **kw):
        calls.append((cmd, kw))
        return SimpleNamespace(returncode=0, stdout=_envelope("```json\n{\"tasks\": []}\n```"), stderr="")

    res = runner.run(cfg, "sync_file", "PROMPT", allowed_tools=["Read"], workdir=tmp_path, exec_=fake_exec)
    cmd, kw = calls[0]
    assert cmd[0] == "claude" and "-p" in cmd and "PROMPT" not in cmd          # the prompt goes over stdin, never argv
    assert kw["input"] == "PROMPT" and kw["cwd"] == str(tmp_path)
    assert cmd[cmd.index("--model") + 1] == "sonnet" and cmd[cmd.index("--max-budget-usd") + 1] == "1.0"
    assert cmd[cmd.index("--output-format") + 1] == "json" and "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--allowedTools") + 1] == "Read"
    assert res.ok and res.data == {"tasks": []} and res.cost_usd == 0.12 and res.duration_ms == 1500 and res.error is None


def test_runner_classifies_a_dead_login_a_timeout_and_a_prose_answer(cfg):
    from factotum.claude import runner

    def dead(cmd, **kw):
        return SimpleNamespace(returncode=1, stdout=_envelope("Not logged in · Please run /login", is_error=True, cost=0, ms=389), stderr="")
    res = runner.run(cfg, "sync_file", "p", exec_=dead)
    assert not res.ok and res.auth_failed and res.error.startswith("auth:") and "login" in res.error

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 5)
    res = runner.run(cfg, "sync_file", "p", exec_=slow)
    assert not res.ok and not res.auth_failed and "timeout" in res.error

    def prose(cmd, **kw):
        return SimpleNamespace(returncode=0, stdout=_envelope("Sorry, I could not do that.", cost=0.3), stderr="")
    res = runner.run(cfg, "sync_file", "p", exec_=prose)
    assert not res.ok and res.data is None and "no JSON" in res.error and res.cost_usd == 0.3 and res.result_text.startswith("Sorry")
