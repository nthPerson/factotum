"""Helpers for the stage-4 (links) tests."""
from conftest import TODAY, task  # noqa: F401
from factotum.db import upsert_task


def mk(conn, id: int, **kw) -> int:
    upsert_task(conn, task(id=id, name=kw.pop("name", f"Task {id}"), **kw))
    conn.commit()
    return id
