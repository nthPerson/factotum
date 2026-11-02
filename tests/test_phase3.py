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


# --- T2 the Plaud CLI -------------------------------------------------------------------------

SEARCH_OUT = """- Searching for "Molina"...

Matched 2 of 185 scanned

  of_7a19f5574ec383b17337cc2fc4757834  11-01 Action Plan: Molina Healthcare Duplicate Payment and Auto-Pay Alignment  2026-11-01  1m39s
  of_0000000000000000000000000000beef  10-02 Molina enrollment call  2026-10-02  12m05s
"""
FILE_OUT = """- Fetching file...

File Details:

  id:           of_7a19f5574ec383b17337cc2fc4757834
  name:         11-01 Action Plan: Molina Healthcare Duplicate Payment and Auto-Pay Alignment
  created_at:   2026-11-02T05:41:48
  start_at:     2026-11-02T05:40:00
  duration:     1m39s
  serial_number: 8810B30294766599
  audio:        available
  transcript:   available
  summary:      available
"""


def _fake_plaud(tmp_path):
    calls = []

    def run_(cmd, **kw):
        calls.append(cmd)
        sub = cmd[1]
        if sub == "search":
            return SimpleNamespace(returncode=0, stdout=SEARCH_OUT, stderr="")
        if sub == "file":
            return SimpleNamespace(returncode=0, stdout=FILE_OUT, stderr="")
        if sub in ("summary", "transcript"):
            out = cmd[cmd.index("-o") + 1]
            (tmp_path / out).write_text(f"{sub} of {cmd[2]}\n") if not out.startswith("/") else __import__("pathlib").Path(out).write_text(f"{sub} of {cmd[2]}\n")
            return SimpleNamespace(returncode=0, stdout=f"{sub} saved\n", stderr="")
        raise AssertionError(cmd)
    return run_, calls


def test_plaud_locates_a_recording_by_subject_and_date(cfg, tmp_path):
    from factotum import plaud
    run_, calls = _fake_plaud(tmp_path)
    rec = plaud.locate(cfg, "[Plaud-AutoFlow] 11-01 Action Plan: Molina Healthcare Duplicate Payment and Auto-Pay Alignment", "2026-11-01", run_=run_)
    assert rec.id == "of_7a19f5574ec383b17337cc2fc4757834" and rec.date == "2026-11-01"
    assert rec.name.startswith("11-01 Action Plan")
    assert calls[0][:2] == ["plaud", "search"] and "--from" in calls[0] and "--to" in calls[0]
    # the same title on another day is not this recording; nothing in the window → None, no guessing
    assert plaud.locate(cfg, "Molina", "2026-09-01", run_=run_) is None


def test_plaud_details_and_files(cfg, tmp_path):
    from factotum import plaud
    run_, calls = _fake_plaud(tmp_path)
    d = plaud.details(cfg, "of_7a19f5574ec383b17337cc2fc4757834", run_=run_)
    assert d["name"].startswith("11-01") and d["start_at"] == "2026-11-02T05:40:00" and d["duration"] == "1m39s"
    files = plaud.fetch_files(cfg, "of_7a19f5574ec383b17337cc2fc4757834", tmp_path / "rec", run_=run_)
    assert files.summary.read_text().startswith("summary of of_7a19") and files.transcript.read_text().startswith("transcript of of_7a19")
    assert files.summary.parent == tmp_path / "rec"
