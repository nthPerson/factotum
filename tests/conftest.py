"""Shared fixtures. test_core.py keeps its own copies; new test modules use these."""
import os
import re
from datetime import date

import pytest

from factotum import config as C
from factotum.db import connect, init_db

TODAY = date(2026, 11, 1)


def _example_env_names() -> set[str]:
    """The variable names `.env.example` lists (lines like NAME=...)."""
    text = (C.ROOT / ".env.example").read_text()
    return set(re.findall(r"^([A-Z][A-Z0-9_]*)=", text, re.M))


@pytest.fixture(autouse=True)
def _no_real_env(monkeypatch):
    """The owner's .env sits in the main checkout's root; a test that passes only because a real token is present proves nothing (and may reach the real service)."""
    monkeypatch.setattr(C, "load_dotenv", lambda *a, **k: False)
    for name in _example_env_names():
        monkeypatch.delenv(name, raising=False)
    for name in [k for k in os.environ if k.startswith("SLACK_TOKEN_")]:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def cfg():
    return C.load(C.ROOT / "config" / "factotum.example.yaml")


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    init_db(c)
    return c


def task(**kw):
    base = dict(id=1, name="x", project="COMPE 510", status="open", hard=0, mentions=1,
                created_at="2026-10-01T00:00:00+00:00", edited_at="2026-10-01T00:00:00+00:00", last_mention_at="2026-10-01")
    base.update(kw)
    return base
