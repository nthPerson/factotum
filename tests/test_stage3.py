import re, json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from factotum import config as C, notify
from factotum.api import create_app
from factotum.db import connect, get_settings, init_db, rows, seed_accounts, set_setting, upsert_task
from conftest import FIXTURE, TODAY, task


# --- Task 1: the suite's own fixture

def test_the_suite_reads_its_fixture_not_the_example():
    assert FIXTURE.exists()
    hits = [p.name for p in Path(__file__).parent.glob("*.py") if "factotum.example" in p.read_text(encoding="utf-8") and p.name != "test_stage3.py"]
    assert hits == []
