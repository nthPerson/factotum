"""Adding, editing and finding tasks by hand (stage 6): the add line's preview and its write, search. Nothing here runs Claude."""
from __future__ import annotations

from datetime import date
from typing import Callable

from . import commands, config as C

TEXT_MAX = 500


def efforts(cfg: dict) -> list[str]:
    return C.get(cfg, "taxonomy.efforts", []) or ["XS", "S", "M", "L"]


def lookup(conn) -> Callable[[int], dict | None]:
    """Task n by primary key: {id, name, status, goal}; `goal` is the id of its active part_of target."""
    def find(n: int) -> dict | None:
        r = conn.execute("SELECT id, name, status FROM tasks WHERE id = ?", (n,)).fetchone()
        if r is None:
            return None
        g = conn.execute("SELECT dst FROM relations WHERE src = ? AND kind = 'part_of' AND removed_at IS NULL LIMIT 1", (n,)).fetchone()
        return {"id": r[0], "name": r[1], "status": r[2], "goal": g[0] if g else None}
    return find


def preview(conn, cfg: dict, text: str, today: date, *, verb: bool = False) -> dict:
    """How an add line will be filed, with the tasks it names checked. Writes nothing."""
    r = commands.parse_add((text or "")[:TEXT_MAX], today, C.project_names(cfg), efforts(cfg), verb=verb, task=lookup(conn))
    if r is None:
        return {"is_add": False}
    links = []
    if r["under"] is not None:
        links.append({"kind": "part_of", "other": r["under"]})
    if r["after"] is not None:
        links.append({"kind": "waits_on", "other": r["after"]})
    fields = dict(r["fields"], **({"links": links} if links else {}))
    return dict(r, is_add=True, change={"op": "create", "fields": fields})
