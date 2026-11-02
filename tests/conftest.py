"""Shared fixtures. test_core.py keeps its own copies; new test modules use these."""
from datetime import date

import pytest

from factotum import config as C
from factotum.db import connect, init_db

TODAY = date(2026, 11, 1)


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
