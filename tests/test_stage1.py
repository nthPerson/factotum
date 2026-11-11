import base64, json, os, struct
from datetime import datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from factotum import api, batches, commands, config as C, reminders
from factotum.db import get_settings, upsert_task
from conftest import TODAY, _example_env_names, task


# --- Task 1: tests never read real secrets

def test_the_suite_never_sees_the_owners_environment():
    names = _example_env_names()
    assert {"NOTION_TOKEN", "MAILBIRD_TOKEN", "SMTP_PASSWORD", "FACTOTUM_DB"} <= names
    C.load(C.ROOT / "config" / "factotum.example.yaml")          # would call load_dotenv(ROOT / ".env")
    assert not any(os.environ.get(n) for n in names)
    assert not any(k.startswith("SLACK_TOKEN_") for k in os.environ)


# --- Task 2: the command fault

def test_a_dateless_command_with_words_left_over_is_not_a_command():
    P = lambda s: commands.parse(s, TODAY)           # noqa: E731
    assert P("drop 12 duplicate of 14") is None      # the design's case: it used to drop both
    assert P("done 389 because it shipped") is None
    assert P("top 23 please") is None
    assert P("reopen 5 to friday") is None           # a date tail on a verb that takes none
    assert P("nodue 364 tomorrow") is None
    assert P("snooze 12 duplicate of 14") is None    # unreadable words never silently mean "tomorrow"
    # what keeps working (the plain forms, `done 389 392`, `snooze #23`, `push Task-364 fri`, `defer 388 to next week`,
    # `nodue 364 Task-365`, are already pinned in test_core.py and test_phase9.py; not repeated here)
    assert [c["task_id"] for c in P("done 12 and 14")] == [12, 14]
    assert [c["task_id"] for c in P("drop Task-12, Task-14")] == [12, 14]
    assert P("snooze 23 fri")[0]["fields"]["snoozed_until"] == "2026-11-06"
