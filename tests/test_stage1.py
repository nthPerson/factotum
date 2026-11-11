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
