"""The final review's eight findings on the linked-tasks branch, one test (or a few) each."""
import json
import threading
from datetime import date

import pytest
from fastapi.testclient import TestClient

from factotum import api, batches
from factotum.db import connect, init_db, now_iso, upsert_task

TODAY = date(2026, 11, 1)
JSON = {"Content-Type": "application/json"}


def _mk(conn, id, **kw):
    base = dict(id=id, name=kw.pop("name", f"Task {id}"), project="P", status="open", hard=0, mentions=1,
                created_at="2026-10-01T00:00:00+00:00", edited_at="2026-10-01T00:00:00+00:00", last_mention_at="2026-10-01")
    base.update(kw)
    upsert_task(conn, base)
    conn.commit()
    return id


def _client(cfg, tmp_path, **kw):
    path = tmp_path / "t.db"
    conn = connect(path)
    init_db(conn)
    return TestClient(api.create_app(cfg, db_path=str(path)), **kw), conn


# ---- 1. writes on the API's shared connection take commit_lock ---------------------------------------

def test_applies_and_other_writers_on_the_shared_connection_never_interleave(cfg, tmp_path):
    """race.py / race2.py through the routes: an Apply in one thread, another route that writes and commits
    in a second. Before commit_lock covered every writer, one thread's commit landed in the middle of the
    other's batch: batch rows committed with their changes but no undo data, and Applies that raised
    after their writes had been committed by the other thread."""
    client, conn = _client(cfg, tmp_path, raise_server_exceptions=False)      # a 500 is counted, not raised in the thread
    for i in (1, 2):
        _mk(conn, i)
    n = 300
    errors: list[str] = []
    applied: list[int] = []

    def applier():
        for k in range(n):
            r = client.post("/api/batches/apply", json={"source": "ui", "changes": [
                {"op": "set", "task_id": 1, "fields": {"why_now": f"w{k}"}},
                {"op": "set", "task_id": 2, "fields": {"why_now": f"w{k}"}}]})
            if r.status_code != 200:
                errors.append(f"apply {r.status_code} {r.text[:80]}")
            else:
                applied.append(r.json()["batch_id"])

    def stamper():
        for _ in range(n * 2):
            r = client.post("/api/review/finish", json={})
            if r.status_code != 200:
                errors.append(f"finish {r.status_code} {r.text[:80]}")

    ts = [threading.Thread(target=applier), threading.Thread(target=stamper)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    reader = connect(tmp_path / "t.db")
    no_undo = reader.execute("SELECT COUNT(*) FROM batches WHERE undo_json IS NULL OR undo_json = '[]'").fetchone()[0]
    assert errors == [] and no_undo == 0, (errors[:5], len(errors), no_undo)
    assert len(applied) == n and None not in applied
    # every applied batch undoes, newest first, and restores what it wrote
    for bid in reversed(applied):
        u = client.post(f"/api/batches/{bid}/undo", json={}).json()
        assert u["restored"] == 2 and not u["already"] and u["skipped"] == [], (bid, u)


# What writes on the API's shared connection: a call of one of these with `conn` as its first argument (a
# method of `conn` for the first three). A new helper that commits on the connection it is given joins the list.
_CONN_METHODS = {"commit", "rollback"}
_WRITERS = {("batches", "apply"), ("batches", "undo"), (None, "set_setting"), (None, "add_link"), ("chat", "new_thread"), ("chat", "turn"),
            ("chat", "create_from_proposal"), ("chat", "mark_staged"), ("by_hand", "add"), ("reminders", "act"), ("reminders", "save_outcome"),
            ("app_push", "keys"), ("app_push", "subscribe"), ("app_push", "unsubscribe"), ("app_push", "send_test"),
            ("summary", "request_refresh"), ("oauth", "arm"), ("oauth", "revoke_client"), ("suggestions", "session")}
_DML = ("INSERT", "UPDATE", "DELETE", "REPLACE")


def _unlocked_writers(source: str) -> list[str]:
    """Every write on `conn` in create_app that is neither inside `with commit_lock:` (lexically, or in a nested
    function every call of which is) nor handed `lock=commit_lock`, and any write in an `async def`."""
    import ast
    tree = ast.parse(source)
    app_fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "create_app")

    def is_writer(c: ast.Call) -> bool:
        f = c.func
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "conn":
            if f.attr in _CONN_METHODS:
                return True
            if f.attr == "execute" and c.args and isinstance(c.args[0], ast.Constant) and isinstance(c.args[0].value, str):
                return c.args[0].value.lstrip().upper().startswith(_DML)
            return False
        name = (f.value.id if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else None, f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None))
        if name not in _WRITERS or not c.args or not (isinstance(c.args[0], ast.Name) and c.args[0].id == "conn"):
            return False
        return not any(k.arg == "lock" and isinstance(k.value, ast.Name) and k.value.id == "commit_lock" for k in c.keywords)

    def locks(w: ast.With) -> bool:
        return any(isinstance(i.context_expr, ast.Name) and i.context_expr.id == "commit_lock" for i in w.items)

    # each call site: (callee name, locked?, enclosing function names innermost first, async?)
    found: list[tuple[ast.Call, bool, list[str], bool]] = []

    def walk(node, locked: bool, fns: list[str], is_async: bool):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(ch, False, [ch.name] + fns, isinstance(ch, ast.AsyncFunctionDef))
            elif isinstance(ch, ast.With):
                for i in ch.items:
                    walk(i, locked, fns, is_async)
                for b in ch.body:
                    walk_stmt(b, locked or locks(ch), fns, is_async)
            else:
                if isinstance(ch, ast.Call):
                    found.append((ch, locked, fns, is_async))
                walk(ch, locked, fns, is_async)

    def walk_stmt(st, locked, fns, is_async):
        if isinstance(st, ast.Call):
            found.append((st, locked, fns, is_async))
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.With)):
            holder = ast.Module(body=[st], type_ignores=[])
            walk(holder, locked, fns, is_async)
        else:
            walk(st, locked, fns, is_async)

    walk(app_fn, False, [], False)
    nested = {n.name for n in ast.walk(app_fn) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n is not app_fn}
    # A nested function is locked when it is called somewhere and every call of it is locked (to a fixed point).
    calls_of: dict[str, list[tuple[bool, list[str]]]] = {}
    for c, locked, fns, _ in found:
        if isinstance(c.func, ast.Name) and c.func.id in nested:
            calls_of.setdefault(c.func.id, []).append((locked, fns))
    locked_fns: set[str] = set()
    while True:
        more = {name for name, sites in calls_of.items()
                if all(lk or any(f in locked_fns for f in fns) for lk, fns in sites)} - locked_fns
        if not more:
            break
        locked_fns |= more
    bad = []
    for c, locked, fns, is_async in found:
        if not is_writer(c):
            continue
        where = f"line {c.lineno} in {fns[0] if fns else 'create_app'}"
        if is_async:
            bad.append(f"{where}: a write in an async def runs on the event loop")
        elif not (locked or any(f in locked_fns for f in fns)):
            bad.append(f"{where}: {ast.unparse(c.func)} outside commit_lock")
    return bad


def test_every_write_on_the_shared_connection_in_the_api_takes_commit_lock():
    import inspect
    assert _unlocked_writers(inspect.getsource(api)) == []


def test_the_commit_lock_check_finds_a_route_that_forgets():
    src = '''
def create_app():
    commit_lock = None
    @app.post("/a")
    def a():
        batches.apply(conn, [], "ui")
    @app.post("/b")
    def b():
        with commit_lock:
            conn.commit()
    def helper():
        conn.execute("UPDATE tasks SET x = 1")
    @app.post("/c")
    def c():
        with commit_lock:
            return helper()
    def loose():
        conn.commit()
    @app.post("/d")
    def d():
        loose()
    @app.post("/e")
    async def e():
        set_setting(conn, "k", 1)
    @app.post("/f")
    def f():
        by_hand.add(conn, cfg, lock=commit_lock)
'''
    bad = _unlocked_writers(src)
    assert [b.split(":")[0] for b in bad] == ["line 6 in a", "line 18 in loose", "line 24 in e"], bad


def test_commit_lock_is_reentrant():
    """A helper that takes it again under a route that holds it (push_state inside push routes) must not hang."""
    import inspect
    src = inspect.getsource(api.create_app)
    assert "commit_lock = threading.RLock()" in src


# ---- 2 and 3. a ripple never rewrites a text it showed cut, and writes back what the owner wrote ------------

from factotum import config as C, ripple  # noqa: E402
from factotum.claude.runner import RunResult  # noqa: E402


@pytest.fixture
def rconn(tmp_path, monkeypatch):
    monkeypatch.setattr(ripple, "RUNS_DIR", tmp_path / "runs")
    c = connect(tmp_path / "r.db")
    init_db(c)
    return c


def _shown_text(prompt: str) -> str:
    """The first neighbour's text as the prompt shows it, read back as a JSON reader would."""
    return json.loads(prompt.split("  text: ", 1)[1].split("\n", 1)[0])


def _faithful(seen: dict | None = None):
    """A model that keeps what it was shown word for word and adds where things stand."""
    def runner(cfg, kind, prompt, **kw):
        if seen is not None:
            seen["prompt"] = prompt
        data = {"ripples": [{"task_id": 2, "effect": "text", "context": _shown_text(prompt) + "\nWhere this stands: Task-1 is done.",
                             "why": "Task-1 is done", "proposals": []}]}
        return RunResult(True, data, json.dumps(data), 0.03, 900)
    return runner


def _ripple_once(conn, cfg, context: str, seen: dict | None = None) -> dict:
    _mk(conn, 1)
    _mk(conn, 2, context=context)
    batches.apply(conn, [{"op": "link", "task_id": 2, "fields": {"kind": "waits_on", "other": 1}}], "ui", TODAY)
    batches.apply(conn, [{"op": "done", "task_id": 1}], "ui", TODAY)
    return ripple.run(conn, cfg, [1], TODAY, runner=_faithful(seen), at="2026-11-01T10:00")


def test_a_text_shown_cut_is_never_rewritten_its_ripple_is_a_note(rconn, cfg):
    """test_ripple_long_text.py::test_tail_lost: the model saw 1500 characters, and its rewrite replaced the
    whole text, so the owner's requirement past the cut was gone."""
    tail = " IMPORTANT: must be handed in person at the office, bring the signed form."
    long = ("Owner notes. " * 120 + tail).strip()
    assert len(long) > ripple.TEXT_CAP
    seen: dict = {}
    res = _ripple_once(rconn, cfg, long, seen)
    now = rconn.execute("SELECT context FROM tasks WHERE id = 2").fetchone()[0]
    assert res["rewritten"] == [] and [n["task_id"] for n in res["noted"]] == [2], res
    assert now.startswith(long) and "in person" in now and now.endswith("Ripple from Task-1: Task-1 is done")
    note = rconn.execute("SELECT note FROM changes WHERE batch_id = ? AND task_id = 2 AND field = 'context'", (res["batch_id"],)).fetchone()[0]
    assert "the text is too long to rewrite safely, so a note was added" in note
    assert "the text is too long to rewrite safely, so a note was added" in res["line"]
    assert f"only its first {ripple.TEXT_CAP} characters are shown" in seen["prompt"]       # the model is told


def test_a_text_exactly_at_the_cap_is_rewritten_one_character_more_is_a_note(rconn, cfg, tmp_path):
    at_cap = "a" * (ripple.TEXT_CAP - 9) + " in person"[:9]
    assert len(at_cap) == ripple.TEXT_CAP
    seen: dict = {}
    res = _ripple_once(rconn, cfg, at_cap, seen)
    assert res["rewritten"] == [2] and res["noted"] == []
    assert "characters are shown" not in seen["prompt"]
    other = connect(tmp_path / "r2.db")
    init_db(other)
    res = _ripple_once(other, cfg, at_cap + "!")
    assert res["rewritten"] == [] and [n["task_id"] for n in res["noted"]] == [2]
