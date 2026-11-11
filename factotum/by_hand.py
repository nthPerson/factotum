"""Adding, editing and finding tasks by hand (stage 6): the add line's preview and its write, search. Nothing here runs Claude."""
from __future__ import annotations

import re
import threading
from datetime import date
from typing import Callable

from . import batches, commands, config as C

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


KEY_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
FIELD_KEYS = ("name", "due", "hard", "project", "effort", "context", "counterpart", "waiting_on", "after", "under")
_ADD_LOCK = threading.Lock()


def _clean_fields(conn, cfg: dict, fields: dict) -> tuple[dict, list[dict]]:
    """The fields of a new task, each checked on its own: (what is kept, [{"words", "why"}] for what is not)."""
    given = {k: v for k, v in fields.items() if not (v is None or v == "" or (k == "hard" and not v))}
    out: dict = {}
    refused: list[dict] = []
    find = lookup(conn)

    def refuse(key, why):
        refused.append({"words": key, "why": why})

    for k in FIELD_KEYS:
        if k not in given:
            continue
        v = given[k]
        if k == "name":
            v = str(v).strip()
            if v:
                out["name"] = v
        elif k == "due":
            try:
                s = str(v).strip()
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
                    raise ValueError(s)
                out["due"] = date.fromisoformat(s).isoformat()
            except ValueError:
                refuse(k, f"'{v}' is not a date")
        elif k == "hard":
            if "due" in out:
                out["hard"] = 1
            else:
                refuse(k, "! needs a due date")
        elif k == "project":
            if v in C.project_names(cfg):
                out["project"] = v
            else:
                refuse(k, f"'{v}' is not a project")
        elif k == "effort":
            if v in efforts(cfg):
                out["effort"] = v
            else:
                refuse(k, f"'{v}' is not an effort ({', '.join(efforts(cfg))})")
        elif k in ("context", "counterpart", "waiting_on"):
            v = str(v).strip()
            if v:
                out[k] = v if k == "context" else v[:2000]
        elif k in ("after", "under"):
            if type(v) is not int:
                refuse(k, f"'{v}' is not a task number")
                continue
            kind = "waits_on" if k == "after" else "part_of"
            why = commands.link_refusal(kind, v, find(v), out.get("under") if kind == "waits_on" else None)
            if why:
                refuse(k, why)
            else:
                out[k] = v
    for k in given:
        if k not in FIELD_KEYS:
            refuse(k, f"{k} cannot be set on a new task")
    return out, refused


def add(conn, cfg: dict, *, text: str | None = None, fields: dict | None = None, key: str | None = None, today: date) -> dict:
    """Make a task now, in one `ui` batch (Source Manual; its undo drops the row). `key` makes a repeat answer with the first task."""
    if (text is None) == (fields is None):
        raise ValueError("give text or fields")
    if key is not None and not KEY_RE.match(key):
        raise ValueError("key must be 6 to 64 letters, digits, - or _")
    if text is not None:
        pv = preview(conn, cfg, text, today, verb=False)
        create = dict(pv["change"]["fields"])
        parts, refused = pv["parts"], pv["refused"]
    else:
        kept, refused = _clean_fields(conn, cfg, fields)
        parts = []
        create = {k: v for k, v in kept.items() if k not in ("after", "under")}
        links = ([{"kind": "part_of", "other": kept["under"]}] if "under" in kept else []) + \
                ([{"kind": "waits_on", "other": kept["after"]}] if "after" in kept else [])
        if links:
            create["links"] = links
    name = str(create.get("name") or "").strip()
    if not name:
        raise ValueError("a task needs a name")
    if len(name) > 200:
        raise ValueError("a name is at most 200 characters")
    ch: dict = {"op": "create", "fields": create}
    with _ADD_LOCK:
        if key is not None:
            old = conn.execute("SELECT id, name FROM tasks WHERE source_key = ?", (f"add:{key}",)).fetchone()
            if old:
                return {"task_id": old[0], "batch_id": None, "already": True, "name": old[1], "parts": parts, "refused": refused, "skipped": []}
            ch["source_key"] = f"add:{key}"
        res = batches.apply(conn, [ch], "ui", today)
    if not res.get("created"):
        raise ValueError((res["skipped"][0]["why"] if res.get("skipped") else "the task was not made"))
    return {"task_id": res["created"][0], "batch_id": res["batch_id"], "already": False, "name": name, "parts": parts, "refused": refused, "skipped": res["skipped"]}
