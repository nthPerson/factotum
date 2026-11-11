"""Helpers for the stage-4 (links) tests."""
from conftest import TODAY, task  # noqa: F401
from factotum.db import upsert_task


def mk(conn, id: int, **kw) -> int:
    upsert_task(conn, task(id=id, name=kw.pop("name", f"Task {id}"), **kw))
    conn.commit()
    return id


def link(conn, src: int, dst: int, kind: str, source: str = "ui", **fields) -> dict:
    """One batch holding one `link` change."""
    from factotum import batches
    return batches.apply(conn, [{"op": "link", "task_id": src, "fields": dict(fields, kind=kind, other=dst)}], source, TODAY)


def active_rels(conn) -> set[tuple[int, int, str]]:
    return {(r[0], r[1], r[2]) for r in conn.execute("SELECT src, dst, kind FROM relations WHERE removed_at IS NULL")}
