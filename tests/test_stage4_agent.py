"""Stage 4a, the agent's surface (design §11): what the MCP tools show of links, and what they record."""
import json
import re

from factotum import batches, config as C, lifecycle, mcp_server as M, relations
from factotum.db import connect, now_iso, one
from stage4_helpers import TODAY, link, mk


def _rpc(ctx, id_, method, params=None):
    msg = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        msg["params"] = params
    return M.dispatch(ctx, msg)


def _text(resp) -> str:
    return "".join(c["text"] for c in resp["result"]["content"] if c["type"] == "text")


def _call(ctx, name: str, **args) -> dict:
    resp = _rpc(ctx, 1, "tools/call", {"name": name, "arguments": args})
    assert not resp["result"]["isError"], _text(resp)
    return json.loads(_text(resp))


def _ctx(cfg, tmp_path, **kw):
    """A read-only connection of the tool's own on the test's database (the fixture's stays writable)."""
    return M.Context(connect(tmp_path / "t.db"), cfg, today=TODAY, **kw)


# --- Task 14: reading

def test_tasks_query_drops_rows_to_fit_and_reports_the_cut(conn, cfg, tmp_path):
    for i in range(1, 61):
        mk(conn, i, name=("Very long task name " * 10)[:199], why_now="w" * 400)
    ctx = M.Context(connect(tmp_path / "t.db"), cfg, today=TODAY)
    text = _text(_rpc(ctx, 1, "tools/call", {"name": "tasks_query", "arguments": {"limit": 60}}))
    res = json.loads(text)                                         # never cut mid-JSON
    assert len(text) <= M.RESULT_CAP and res["cut"] > 0 and len(res["tasks"]) + res["cut"] == 60
    small = _call(ctx, "tasks_query", limit=5)
    assert small["cut"] == 0 and len(small["tasks"]) == 5 and small["total"] == 60       # `cut` is always there


def test_tasks_query_rel_and_the_ready_linked_to_and_goal_filters(conn, cfg, tmp_path):
    for i in range(1, 6):
        mk(conn, i)
    link(conn, 1, 2, "waits_on")
    link(conn, 3, 4, "part_of")
    ctx = _ctx(cfg, tmp_path)
    by_id = {t["id"]: t for t in _call(ctx, "tasks_query")["tasks"]}
    assert (by_id[1]["rel"], by_id[2]["rel"], by_id[3]["rel"], by_id[4]["rel"]) == ("waits on 2", "frees 1", "part of 4", "steps 1/1 open")
    assert "rel" not in by_id[5]
    assert {t["id"] for t in _call(ctx, "tasks_query", ready=True)["tasks"]} == {2, 3, 4, 5}
    assert {t["id"] for t in _call(ctx, "tasks_query", linked_to=2)["tasks"]} == {1}
    assert {t["id"] for t in _call(ctx, "tasks_query", goal=4)["tasks"]} == {3}
    batches.apply(conn, [{"op": "done", "task_id": 2, "fields": {}}], "ui", TODAY)
    assert 1 in {t["id"] for t in _call(ctx, "tasks_query", ready=True)["tasks"]}          # what it waited on is done: ready now


def test_short_names_every_kind_of_link_and_a_merge():
    rel = {"links": [{"id": 9, "kind": "same_as", "dir": "out", "other": 12}, {"id": 10, "kind": "same_as", "dir": "in", "other": 13}],
           "waits_on_open": [412], "frees_open": [430, 431], "goal_id": 380, "steps_open": 2, "steps_total": 5}
    assert relations.short({"rel": rel}) == "waits on 412 · frees 430, 431 · part of 380 · steps 2/5 open · same as 12"
    assert relations.short({"rel": {"links": [], "waits_on_open": [], "frees_open": [], "goal_id": None, "steps_total": 0}}) == ""
    assert relations.short({}) == ""


def test_side_word_reads_each_kind_from_either_end():
    got = {(k, d): relations.side_word(k, d) for k in relations.KINDS for d in ("out", "in")}
    assert got == {("part_of", "out"): "part of", ("part_of", "in"): "has step", ("waits_on", "out"): "waits on", ("waits_on", "in"): "frees",
                   ("same_as", "out"): "merged into", ("same_as", "in"): "merged from"}


def test_task_get_shows_linked_tasks_provenance_and_pending_evidence(conn, cfg, tmp_path):
    for i in range(1, 7):
        mk(conn, i)
    link(conn, 1, 2, "waits_on", why="needs the data first")
    for tid in (1, 6):
        conn.execute("INSERT INTO links (task_id, kind, ref, label, created_at) VALUES (?, 'plaud_note', 'of_a', 'Lab meeting', ?)", (tid, now_iso()))
    conn.execute("INSERT INTO links (task_id, kind, ref, label, created_at) VALUES (1, 'task', '5', NULL, ?)", (now_iso(),))
    for n in range(7):
        conn.execute("INSERT INTO evidence (task_id, kind, label, excerpt, at, judged_at) VALUES (1, 'thread_updated', ?, ?, ?, ?)",
                     (f"mail {n}", "x" * 1000, f"2026-10-2{n}T00:00:00+00:00", "2026-10-30" if n == 6 else None))
    conn.commit()
    got = _call(_ctx(cfg, tmp_path), "task_get", task_id=1)
    assert got["task"]["rel"] == "waits on 2"
    assert [ln["kind"] for ln in got["links"]] == ["plaud_note"]                              # outside things only
    (lk,) = got["linked"]
    assert lk["kind"] == "waits on" and lk["other"] == {"id": 2, "name": "Task 2", "status": "open", "due": None}
    assert (lk["why"], lk["made_by"]) == ("needs the data first", "ui") and lk["at"]
    assert [(n["id"], n["via"]) for n in got["nearby"]] == [(6, "recording")]
    assert [e["label"] for e in got["evidence"]] == ["mail 5", "mail 4", "mail 3", "mail 2", "mail 1"]   # newest unjudged, at most 5
    assert all(len(e["excerpt"]) <= 300 and set(e) == {"id", "kind", "label", "excerpt", "at"} for e in got["evidence"])
    other = _call(_ctx(cfg, tmp_path), "task_get", task_id=2)
    assert other["task"]["rel"] == "frees 1" and other["linked"][0]["kind"] == "frees"
    assert _call(_ctx(cfg, tmp_path), "task_get", task_id=3)["task"]["rel"] == ""


def test_the_new_read_tools(conn, cfg, tmp_path):
    for i in range(1, 4):
        mk(conn, i)
    link(conn, 1, 2, "waits_on"); link(conn, 2, 3, "waits_on")
    ctx = _ctx(cfg, tmp_path)
    nb = _call(ctx, "task_neighbours", task_id=1, depth=2)
    assert nb["task"] == {"id": 1, "name": "Task 1", "status": "open"} and nb["cut"] == 0
    assert [(n["id"], n["depth"]) for n in nb["nodes"]] == [(2, 1), (3, 2)]
    assert {(ln["src"], ln["dst"], ln["kind"]) for ln in nb["links"]} == {(1, 2, "waits_on"), (2, 3, "waits_on")}
    assert [n["id"] for n in _call(ctx, "task_neighbours", task_id=1)["nodes"]] == [2]

    # waiting: 10 (hard Nov 5) waits on 11 (due Nov 8: late) and 12 (no date); 13 frees 15 (hard Nov 6), so 13 is needed by Nov 6 and 14 (Nov 9) is late
    mk(conn, 10, project="Thesis", due="2026-11-05", hard=1); mk(conn, 11, project="Thesis", due="2026-11-08"); mk(conn, 12, project="Thesis")
    mk(conn, 13, project="Thesis"); mk(conn, 14, project="Thesis", due="2026-11-09"); mk(conn, 15, project="Thesis", due="2026-11-06", hard=1)
    link(conn, 10, 11, "waits_on"); link(conn, 10, 12, "waits_on"); link(conn, 13, 14, "waits_on"); link(conn, 15, 13, "waits_on")
    w = {r["id"]: r for r in _call(ctx, "waiting_tasks", project="thes")["waiting"]}
    assert set(w) == {10, 13, 15}
    assert {b["id"]: b["late"] for b in w[10]["waits_on"]} == {11: True, 12: False}
    assert w[13]["needed_by"] == "2026-11-06" and {b["id"]: b["late"] for b in w[13]["waits_on"]} == {14: True}
    assert {b["id"]: b["late"] for b in w[15]["waits_on"]} == {13: False}

    # twins: the same number after normalising, one from Canvas, within 36 hours; a sibling with another number never pairs
    mk(conn, 20, name="Assignment 03", source="Canvas", due="2026-11-10T23:59", created_at="2026-10-05T00:00:00+00:00")
    mk(conn, 21, name="Assignment 3", source="Plaud", due="2026-11-11")
    mk(conn, 22, name="Assignment 4", source="Canvas", due="2026-11-10")
    (p,) = _call(ctx, "twin_candidates")["pairs"]
    assert (p["a"]["id"], p["b"]["id"], p["keep"], p["suggestion"]) == (21, 20, 20, None) and p["why"] == "same number 3, due 0 h apart"
    assert set(p["a"]) == {"id", "name", "due", "source"}
    ev = relations.suggest(conn, "same_as", 21, 20, p["why"], "twin"); conn.commit()
    assert _call(ctx, "twin_candidates")["pairs"][0]["suggestion"] == ev
    mk(conn, 30, name="Lab report", project="Thesis"); mk(conn, 31, name="Lab report", project="Thesis")
    ev2 = relations.suggest(conn, "same_as", 31, 30, relations.IMPORT_WHY, "import"); conn.commit()
    pairs = {(q["a"]["id"], q["b"]["id"]): q for q in _call(ctx, "twin_candidates")["pairs"]}
    assert set(pairs) == {(21, 20), (31, 30)} and pairs[(31, 30)]["suggestion"] == ev2 and pairs[(31, 30)]["keep"] == 30
    assert set(pairs[(31, 30)]) == {"a", "b", "keep", "why", "suggestion"}
    assert [(q["a"]["id"], q["b"]["id"]) for q in _call(ctx, "twin_candidates", project="thes")["pairs"]] == [(31, 30)]
    conn.execute("UPDATE evidence SET judged_at = ?, verdict = 'dismissed' WHERE id = ?", (now_iso(), ev)); conn.commit()
    assert [(q["a"]["id"], q["b"]["id"]) for q in _call(ctx, "twin_candidates")["pairs"]] == [(31, 30)]   # a judged pair is left out

    # a goal read from one of its steps
    mk(conn, 40, name="Thesis chapter 2", due="2026-11-20", hard=1); mk(conn, 41); mk(conn, 42); mk(conn, 43)
    link(conn, 41, 40, "part_of"); link(conn, 42, 40, "part_of"); link(conn, 42, 43, "waits_on")
    batches.apply(conn, [{"op": "done", "task_id": 41, "fields": {"outcome": "Draft sent"}}], "ui", TODAY)
    g = _call(ctx, "goal_tree", task_id=42)
    assert g["goal"] == {"id": 40, "name": "Thesis chapter 2", "status": "open", "due": "2026-11-20", "hard": 1, "outcome": None}
    assert [(s["id"], s["status"], s["outcome"], s["waits_on"]) for s in g["steps"]] == [(41, "done", "Draft sent", []), (42, "open", None, [43])]
    assert (g["done"], g["total"]) == (1, 2) and _call(ctx, "goal_tree", task_id=40)["goal"]["id"] == 40
    err = _rpc(ctx, 2, "tools/call", {"name": "goal_tree", "arguments": {"task_id": 43}})
    assert err["result"]["isError"] and _text(err) == "Task-43 is not a goal or a step"


def test_twin_pairs_counts_a_date_only_due_as_midnight_and_needs_canvas():
    base = dict(status="open", project="COMPE 510", created_at="2026-10-01T00:00:00+00:00")
    ts = [dict(base, id=1, name="Quiz 7", source="Canvas", due="2026-11-10"), dict(base, id=2, name="quiz 07 prep", source="Plaud", due="2026-11-11T12:00"),
          dict(base, id=3, name="Quiz 7", source="Plaud", due="2026-11-12T13:00"),        # 61 h after task 1, 25 h after task 2 but neither is Canvas
          dict(base, id=4, name="Quiz 7", source="Canvas", due=None), dict(base, id=5, name="Quiz 7", source="Canvas", due="2026-11-10", status="done"),
          dict(base, id=6, name="Quiz 7", source="Canvas", due="2026-11-10", project="Thesis"), dict(base, id=7, name="Quiz", source="Canvas", due="2026-11-10")]
    assert relations.twin_pairs(ts) == [(2, 1, "same number 7, due 36 h apart")]
    old = dict(base, id=9, name="HW 2", source="Plaud", due="2026-11-10", created_at="2026-09-01T00:00:00+00:00")
    assert relations.twin_pairs([dict(base, id=8, name="HW 2", source="Canvas", due="2026-11-10"), old]) == [(9, 8, "same number 2, due 0 h apart")]
    both = [dict(old, id=8, source="Canvas", created_at="2026-10-01T00:00:00+00:00"), dict(old, source="Canvas")]   # both from Canvas: the older stays
    assert relations.twin_pairs(both) == [(8, 9, "same number 2, due 0 h apart")]


def test_reading_tools_record_the_tasks_they_showed(conn, cfg, tmp_path):
    for i in range(1, 7):
        mk(conn, i, name=f"Alpha {i}" if i in (3, 4) else f"Task {i}")
    link(conn, 1, 2, "waits_on")
    for tid in (1, 6):
        conn.execute("INSERT INTO links (task_id, kind, ref, label, created_at) VALUES (?, 'plaud_note', 'of_a', 'Lab meeting', ?)", (tid, now_iso()))
    conn.commit()
    run_dir = tmp_path / "run"
    ctx = _ctx(cfg, tmp_path, run_dir=run_dir)
    assert M.tasks_seen(run_dir) == set()
    got = _call(ctx, "task_get", task_id=1)
    q = _call(ctx, "tasks_query", q="alpha")
    shown = {got["task"]["id"]} | {lk["other"]["id"] for lk in got["linked"]} | {n["id"] for n in got["nearby"]} | {t["id"] for t in q["tasks"]}
    assert shown == {1, 2, 6, 3, 4} and M.tasks_seen(run_dir) == shown
    _call(ctx, "task_neighbours", task_id=2)
    assert M.tasks_seen(run_dir) == shown                       # 2's neighbour is 1, already seen
    _call(ctx, "waiting_tasks")
    assert M.tasks_seen(run_dir) == shown


def test_each_new_tool_description_starts_with_a_sentence_that_stands_alone():
    for name in ("task_neighbours", "waiting_tasks", "twin_candidates", "goal_tree"):
        first = next(t for t in M.TOOLS if t["name"] == name)["description"].split(". ")[0]
        assert len(first) > 30 and "this" not in first.lower().split()[:2]


def test_the_new_tools_are_read_only_named_for_the_chat_status_line_and_free_of_banned_words():
    from factotum import chat
    for name in ("task_neighbours", "waiting_tasks", "twin_candidates", "goal_tree"):
        t = next(t for t in M.TOOLS if t["name"] == name)
        assert t["annotations"] == M.READ_ONLY and not t["skill_only"] and name in chat.FRIENDLY
    for t in M.TOOLS:
        if t["name"] in ("tasks_query", "task_get", "task_neighbours", "waiting_tasks", "twin_candidates", "goal_tree"):
            assert not {"blocked", "dependency", "dependencies"} & set(t["description"].lower().replace(",", " ").replace(".", " ").split())


# --- Task 15: writing

def _db(tmp_path, n: int = 0):
    """The test's database with tasks 1..n, opened writable (the tools' own connections are read-only)."""
    from factotum.db import init_db
    c = connect(tmp_path / "t.db")
    init_db(c)
    for i in range(1, n + 1):
        mk(c, i)
    return c


def _writer_ctx(cfg, tmp_path, **kw):
    return _ctx(cfg, tmp_path, run_dir=tmp_path / "run", writer=lambda: connect(tmp_path / "t.db"), **kw)


def _lk(a, kind, b, op="link"):
    return {"op": op, "task_id": a, "fields": {"kind": kind, "other": b}}


def test_two_staged_links_that_make_a_loop_skip_the_second_at_apply(cfg, tmp_path):
    """Each proposal is fine when staged; applied hours apart, the second would close a loop."""
    from fastapi.testclient import TestClient
    from factotum.api import create_app
    from stage4_helpers import active_rels
    app = create_app(cfg, db_path=str(tmp_path / "t.db"))
    client = TestClient(app)
    mk(app.state.conn, 1); mk(app.state.conn, 2)
    ctx = _writer_ctx(cfg, tmp_path)
    p1 = _call(ctx, "stage_changes", changes=[_lk(1, "waits_on", 2)])
    p2 = _call(ctx, "stage_changes", changes=[_lk(2, "waits_on", 1)])
    assert (p1["staged"], p1["rejected"], p2["staged"], p2["rejected"]) == (1, [], 1, [])
    assert client.post(f"/api/proposals/{p1['proposal_id']}/apply", json={}).json()["applied"] == 1
    out = client.post(f"/api/proposals/{p2['proposal_id']}/apply", json={}).json()
    assert out["applied"] == 0 and out["batch_id"] is None and "that would make a loop" in out["skipped"][0]["why"]
    assert active_rels(app.state.conn) == {(1, 2, "waits_on")}
    n = one(app.state.conn, "SELECT COUNT(*) AS n FROM batches")["n"]           # the tray's Apply: the same loop, no batch row left
    out = client.post("/api/batches/apply", json={"changes": [_lk(2, "waits_on", 1)]}).json()
    assert out["batch_id"] is None and out["applied"] == 0 and "that would make a loop" in out["skipped"][0]["why"]
    assert one(app.state.conn, "SELECT COUNT(*) AS n FROM batches")["n"] == n


def test_merge_always_stages_and_link_may_apply_directly(cfg, tmp_path):
    from stage4_helpers import active_rels
    c = _db(tmp_path, 2)
    ctx = _writer_ctx(cfg, tmp_path, apply_writes=True, source="connector")
    merge = {"op": "merge", "task_id": 1, "fields": {"into": 2}}
    err = _rpc(ctx, 1, "tools/call", {"name": "apply_changes", "arguments": {"changes": [merge]}})
    assert err["result"]["isError"] and "always goes to the tray" in _text(err)
    assert c.execute("SELECT status FROM tasks WHERE id = 1").fetchone()[0] == "open"
    res = _call(ctx, "apply_changes", changes=[_lk(1, "waits_on", 2)])
    assert res["applied"] == 1 and active_rels(c) == {(1, 2, "waits_on")}
    assert [r[0] for r in c.execute("SELECT source FROM batches")] == ["connector"]
    st = _call(ctx, "stage_changes", changes=[merge])
    assert st["staged"] == 1 and not st["rejected"]
    assert json.loads(c.execute("SELECT changes_json FROM proposals WHERE id = ?", (st["proposal_id"],)).fetchone()[0]) == [merge]
    assert c.execute("SELECT status FROM tasks WHERE id = 1").fetchone()[0] == "open"        # staged, not applied


def test_the_validator_checks_links_before_the_open_task_rule(cfg, tmp_path):
    c = _db(tmp_path)
    mk(c, 1, status="done", completed_at="2026-10-30")
    for i in range(2, 6):
        mk(c, i)
    link(c, 4, 5, "waits_on")
    v = lambda *chs, direct=False: M._validate_changes(_ctx(cfg, tmp_path), list(chs), direct=direct)   # noqa: E731
    assert v(_lk(1, "part_of", 3)) == ([_lk(1, "part_of", 3)], [], [])                    # a closed task may still be linked
    kept, rejected, _ = v(_lk(True, "waits_on", 2))
    assert not kept and "integer" in rejected[0]
    kept, rejected, _ = v(_lk(5, "waits_on", 4))
    assert not kept and rejected == ["#0: Task-4 already waits on Task-5, directly or through other tasks; that would make a loop"]
    assert v(_lk(4, "waits_on", 5))[1] == ["#0: Task-4 already waits on Task-5"]
    assert v(_lk(2, "waits_on", 3, op="unlink"))[1] == ["#0: Task-2 has no waits on link to Task-3"]
    assert v(_lk(2, "same_as", 3))[1] == ["#0: kind must be part_of or waits_on"]
    assert v(_lk(2, "waits_on", 2))[1] == ["#0: a task cannot be linked to itself"]
    assert "no task 99" in v(_lk(2, "waits_on", 99))[1][0]
    # a create's ref, named by a later link in the same call; a ref is only good after its create
    chs = [{"op": "create", "fields": {"name": "Step A", "ref": "a", "links": [{"kind": "part_of", "other": 3}]}}, {"op": "link", "task_id": "a", "fields": {"kind": "waits_on", "other": 4, "why": "  needs   it " + "w" * 200}}]
    kept, rejected, ignored = v(*chs)
    assert (rejected, ignored) == ([], []) and [k["op"] for k in kept] == ["create", "link"]
    assert kept[0]["fields"] == {"name": "Step A", "ref": "a", "links": [{"kind": "part_of", "other": 3}]}
    assert kept[1]["task_id"] == "a" and kept[1]["fields"]["why"] == ("needs it " + "w" * 200)[:relations.WHY_MAX]
    assert "integer" in v(*reversed(chs))[1][0]                                           # the link comes before the create
    kept, rejected, _ = v({"op": "create", "fields": {"name": "B", "ref": "b"}}, {"op": "create", "fields": {"name": "C", "ref": "b"}},
                          {"op": "create", "fields": {"name": "D", "ref": "9x"}}, {"op": "create", "fields": {"name": "E", "links": [{"kind": "waits_on", "other": 1}]}},
                          {"op": "create", "fields": {"name": "F", "links": [{"kind": "part_of", "other": "nope"}]}})
    assert [k["fields"]["name"] for k in kept] == ["B"] and len(rejected) == 4
    assert "used twice" in rejected[0] and "ref" in rejected[1] and "Task-1 is already done" in rejected[2] and "ref of an earlier create" in rejected[3]


def test_a_link_counts_two_rows_against_the_hourly_cap(cfg, tmp_path):
    c = _db(tmp_path, 4)
    cfg["server"]["mcp_apply_hourly_cap"] = 3
    ctx = _writer_ctx(cfg, tmp_path, apply_writes=True, source="connector")
    over = _rpc(ctx, 1, "tools/call", {"name": "apply_changes", "arguments": {"changes": [_lk(1, "waits_on", 2), _lk(3, "waits_on", 4)]}})
    assert over["result"]["isError"] and "capped at 3" in _text(over)
    assert c.execute("SELECT COUNT(*) FROM relations").fetchone()[0] == 0
    assert _call(ctx, "apply_changes", changes=[_lk(1, "waits_on", 2)])["applied"] == 1
    assert _call(ctx, "apply_changes", changes=[{"op": "add_note", "task_id": 1, "fields": {"text": "asked the lab"}}])["applied"] == 1
    assert c.execute("SELECT context FROM tasks WHERE id = 1").fetchone()[0].endswith("2026-11-01: asked the lab")
    rw = M._rows_written
    assert (rw(_lk(1, "waits_on", 2)), rw(_lk(1, "waits_on", 2, op="unlink")), rw({"op": "merge", "task_id": 1, "fields": {"into": 2}})) == (2, 2, 2)
    assert rw({"op": "create", "fields": {"name": "x", "links": [{"kind": "part_of", "other": 3}] * 2}, "reminders": [{}]}) == 1 + 1 + 4
    assert (rw({"op": "done", "fields": {}}), rw({"op": "drop", "fields": {"outcome": "gone"}}), rw({"op": "add_note", "fields": {"text": "x"}})) == (4, 5, 1)


def test_outcome_note_and_expect_pass_the_validator(cfg, tmp_path):
    _db(tmp_path, 1)
    v = lambda *chs: M._validate_changes(_ctx(cfg, tmp_path), list(chs))                  # noqa: E731
    assert v({"op": "done", "task_id": 1, "fields": {"outcome": "x" * 301}}) == ([], ["#0: outcome is over 300 characters"], [])
    assert v({"op": "done", "task_id": 1, "fields": {"outcome": "x" * 300}})[0] == [{"task_id": 1, "op": "done", "fields": {"outcome": "x" * 300}}]
    assert v({"op": "drop", "task_id": 1})[0] == [{"task_id": 1, "op": "drop", "fields": {}}]
    for bad in ("", "   ", None, "x" * 1001):
        kept, rejected, _ = v({"op": "add_note", "task_id": 1, "fields": {"text": bad}})
        assert not kept and "text" in rejected[0]
    assert v({"op": "add_note", "task_id": 1, "fields": {"text": "Called the lab"}})[0] == [{"task_id": 1, "op": "add_note", "fields": {"text": "Called the lab"}}]
    assert v({"op": "set", "task_id": 1, "fields": {"context": "B"}, "expect": {"context": "A"}})[0] == [
        {"task_id": 1, "op": "set", "fields": {"context": "B"}, "expect": {"context": "A"}}]
    kept, rejected, _ = v({"op": "set", "task_id": 1, "fields": {"context": "B"}, "expect": {"score": 3}})
    assert not kept and "expect" in rejected[0]
    assert v({"op": "set", "task_id": 1, "fields": {"outcome": "y" * 301}})[1] == ["#0: outcome is over 300 characters"]
    assert v({"op": "set", "task_id": 1, "fields": {"outcome": None}})[0] == [{"task_id": 1, "op": "set", "fields": {"outcome": None}}]


# --- Task 16: chat

def _chat_msg(conn, proposals) -> int:
    """A stored assistant message carrying `proposals` (as a turn would have kept them)."""
    from factotum import chat
    tid = chat.new_thread(conn, "t")
    cur = conn.execute("INSERT INTO chat_messages (thread_id, role, text, proposals_json, created_at) VALUES (?, 'assistant', 'ok', ?, ?)",
                       (tid, json.dumps(proposals), now_iso()))
    conn.commit()
    return int(cur.lastrowid)


def test_a_chat_link_must_name_tasks_the_turn_attached_or_read(conn, cfg):
    from factotum import chat
    from factotum.db import rows
    for i in range(1, 5):
        mk(conn, i)
    data = {"proposals": [{"op": "link", "task_id": 1, "fields": {"kind": "waits_on", "other": 2}},
                          {"op": "link", "task_id": 1, "fields": {"kind": "waits_on", "other": 3}},
                          {"op": "merge", "task_id": 4, "fields": {"into": 2}}]}
    kept, rejected = chat._clean_proposals(conn, cfg, data, {}, tasks_seen={2}, attached=[1, 4])
    assert [p["op"] for p in kept] == ["link", "merge"] and all(not p["applied"] for p in kept)
    assert kept[0]["fields"] == {"kind": "waits_on", "other": 2, "why": None} and kept[1]["fields"] == {"into": 2}
    assert "Task-3 was not attached or read in this turn" in rejected[0]
    assert not rows(conn, "SELECT 1 FROM relations")                      # nothing applied
    kept, rejected = chat._clean_proposals(conn, cfg, {"proposals": [{"op": "link", "task_id": True, "fields": {"kind": "waits_on", "other": 2}}]}, {},
                                           tasks_seen={2}, attached=[1])
    assert not kept and rejected                                          # True is not Task-1


def test_chat_rejects_a_link_that_breaks_a_rule_and_keeps_outcome_and_notes(conn, cfg):
    from factotum import chat
    for i in range(1, 4):
        mk(conn, i)
    mk(conn, 5, status="done", completed_at="2026-10-30")
    link(conn, 1, 2, "waits_on")
    data = {"proposals": [{"op": "link", "task_id": 2, "fields": {"kind": "waits_on", "other": 1}},
                          {"op": "link", "task_id": 1, "fields": {"kind": "same_as", "other": 3}},
                          {"op": "unlink", "task_id": 1, "fields": {"kind": "waits_on", "other": 3}},
                          {"op": "unlink", "task_id": 1, "fields": {"kind": "waits_on", "other": 2, "why": "dropped"}},
                          {"op": "merge", "task_id": 5, "fields": {"into": 3}},
                          {"op": "done", "task_id": 3, "fields": {"outcome": "  Sent " + "x" * 400}},
                          {"op": "drop", "task_id": 3, "fields": {}},
                          {"op": "add_note", "task_id": 1, "fields": {"text": "Asked the lab " + "y" * 1200}},
                          {"op": "add_note", "task_id": 1, "fields": {"text": "   "}}]}
    kept, rejected = chat._clean_proposals(conn, cfg, data, {}, tasks_seen={2, 3}, attached=[1, 5])
    assert [p["op"] for p in kept] == ["unlink", "done", "drop", "add_note"] and all(not p["applied"] for p in kept)
    assert kept[0]["fields"] == {"kind": "waits_on", "other": 2}
    assert kept[1]["fields"] == {"outcome": ("Sent " + "x" * 400)[:300]} and kept[2]["fields"] == {}
    assert kept[3]["fields"] == {"text": ("Asked the lab " + "y" * 1200)[:1000]}
    assert len(rejected) == 5
    assert "that would make a loop" in rejected[0] and "part_of or waits_on" in rejected[1] and "has no waits on link to Task-3" in rejected[2]
    assert "Task-5 is not open" in rejected[3] and "text" in rejected[4]


def test_chat_create_keeps_refs_and_links_it_can_vouch_for(conn, cfg):
    from factotum import chat
    for i in range(1, 4):
        mk(conn, i)
    data = {"proposals": [{"op": "create", "fields": {"name": "Goal", "ref": "g"}},
                          {"op": "create", "fields": {"name": "Step", "links": [{"kind": "part_of", "other": "g"}, {"kind": "waits_on", "other": 2, "why": " needs  it "},
                                                                              {"kind": "waits_on", "other": 3}, {"kind": "part_of", "other": "nope"}]}},
                          {"op": "create", "fields": {"name": "Bad ref", "ref": "9x", "links": [{"kind": "same_as", "other": 2}]}}]}
    kept, rejected = chat._clean_proposals(conn, cfg, data, {}, tasks_seen={2}, attached=[])
    assert [p["fields"]["name"] for p in kept] == ["Goal", "Step", "Bad ref"]
    assert kept[0]["fields"]["ref"] == "g" and "ref" not in kept[2]["fields"] and "links" not in kept[2]["fields"]
    assert kept[1]["fields"]["links"] == [{"kind": "part_of", "other": "g"}, {"kind": "waits_on", "other": 2, "why": "needs it"}]
    assert any("Task-3 was not attached or read in this turn" in r for r in rejected) and any("'nope'" in r for r in rejected)


def test_chat_create_rides_in_a_batch_with_its_links_and_one_undo(conn, cfg):
    from factotum import chat
    from factotum.db import one
    from stage4_helpers import active_rels
    props = [{"op": "create", "task_id": None, "fields": {"name": "Goal", "ref": "g"}, "label": "create Goal", "evidence": [], "applied": False},
             {"op": "create", "task_id": None, "fields": {"name": "Step", "links": [{"kind": "part_of", "other": "g"}]}, "label": "create Step",
              "evidence": [{"kind": "mail_thread", "ref": "c1", "label": "From Max"}], "applied": False}]
    mid = _chat_msg(conn, props)
    goal = chat.create_from_proposal(conn, cfg, mid, 0, TODAY)
    step = chat.create_from_proposal(conn, cfg, mid, 1, TODAY)
    assert active_rels(conn) == {(step, goal, "part_of")}
    t = one(conn, "SELECT * FROM tasks WHERE id = ?", (step,))
    assert (t["source"], t["source_key"], t["mentions"], t["last_mention_at"], t["status"]) == ("Chat", f"chat:{mid}:1", 1, TODAY.isoformat(), "open")
    assert one(conn, "SELECT ref FROM links WHERE task_id = ? AND kind = 'mail_thread'", (step,))["ref"] == "c1"
    assert chat.create_from_proposal(conn, cfg, mid, 1, TODAY) == step
    stored = chat.messages(conn, one(conn, "SELECT thread_id FROM chat_messages WHERE id = ?", (mid,))["thread_id"])[0]["proposals"][1]
    assert stored["applied"] is True and stored["task_id"] == step and stored["skipped"] == [] and stored["batch_id"]
    assert batches.undo(conn, stored["batch_id"])["restored"] >= 1
    assert one(conn, "SELECT status FROM tasks WHERE id = ?", (step,))["status"] == "dropped" and active_rels(conn) == set()

    mid2 = _chat_msg(conn, props)
    step2 = chat.create_from_proposal(conn, cfg, mid2, 1, TODAY)
    assert one(conn, "SELECT name FROM tasks WHERE id = ?", (step2,))["name"] == "Step" and active_rels(conn) == set()
    stored = json.loads(one(conn, "SELECT proposals_json FROM chat_messages WHERE id = ?", (mid2,))["proposals_json"])[1]
    assert stored["skipped"] == [{"task_id": None, "why": "Task for ref 'g' is not created yet; create it first"}]


def test_chat_create_route_returns_what_was_skipped(cfg, tmp_path):
    from fastapi.testclient import TestClient
    from factotum.api import create_app
    app = create_app(cfg, db_path=str(tmp_path / "t.db"))
    mid = _chat_msg(app.state.conn, [{"op": "create", "task_id": None, "fields": {"name": "Step", "links": [{"kind": "part_of", "other": "g"}]}},
                                     {"op": "create", "task_id": None, "fields": {"name": "Goal", "ref": "g"}}])
    r = TestClient(app).post("/api/chat/create", json={"message_id": mid, "index": 0}).json()
    assert r["task_id"] and "is not created yet" in r["skipped"][0]["why"]


def test_attached_rows_carry_link_facts(conn, cfg):
    from factotum import chat
    for i in range(1, 4):
        mk(conn, i)
    link(conn, 1, 2, "waits_on")
    a, b = chat.attached_rows(conn, cfg, [1, 3], TODAY)
    assert a["rel"] == "waits on Task-2" and a["linked"] == [{"kind": "waits on", "id": 2, "name": "Task 2", "status": "open"}]
    assert (b["rel"], b["linked"]) == ("", [])


def test_what_else_is_related_is_a_canned_prompt(conn, cfg):
    from factotum import chat
    mk(conn, 1)
    assert chat.CANNED["related"][1] == "What else is related?" and "task_neighbours" in chat.build_prompt(cfg, conn, "", [1], TODAY, canned="related")


# --- Task 17: prompts and skills

def test_the_chat_prompt_teaches_links_and_no_longer_says_it_has_no_other_tools():
    p = (C.ROOT / "prompts" / "chat-turn.md").read_text()
    assert "You have no other tools" not in p and "Links are proposals like any other change" in p
    assert "set|done|drop|pin_top|longterm|add_reminder|add_note|link_thread|link|unlink|merge|create" in p   # "link" and "ref" alone are already in today's text
    for word in ("`outcome`", "`ref`", "`into`", "`waits_on`", "`part_of`"):
        assert word in p


def test_the_planner_skill_names_the_new_tools_and_phrases():
    s = (C.ROOT / "skills" / "factotum-planner" / "SKILL.md").read_text()
    assert "skill-version: 2026-11-11" in s and all(n in s for n in ("task_neighbours", "waiting_tasks", "twin_candidates", "goal_tree", "can't start until", "break this into steps"))


def test_nothing_the_user_or_the_agent_reads_says_blocked_or_dependency(conn, cfg):
    from factotum import review_decide
    from test_review_decide import _cases, _plan, _world
    texts = [p.read_text() for p in (C.ROOT / "prompts").glob("*.md")] + [p.read_text() for p in (C.ROOT / "skills").rglob("SKILL.md")]
    texts += [t["description"] for t in M.TOOLS] + [M.OPS_HELP] + list(lifecycle.REVIEW_LABELS.values()) + [lifecycle.REASON_TEXTS[k] for k in lifecycle.REASON_TEXTS]
    # Review's decision rule (spoken Review, 2026-11-19): its fixed words, and every label and toast it stages over the table's world.
    texts += list(review_decide.SAID.values()) + list(review_decide.LABELS.values())
    F = _world(conn)
    for _, key, option, args, _, _ in _cases():
        p = _plan(conn, cfg, key.format(**F), option.format(**F), args)
        texts += [e.get("label") or "" for _, e, _ in p.puts] + [p.said]
    # The pass (spoken Review stage 2a): its fixed words, and every option label it offers on the table's items.
    from datetime import date
    from factotum import review_pass
    texts += list(review_pass.WORDS.values())
    for key in ["t:1"] + [f"s:{F[n]}" for n in ("c_plan", "c_mail", "c_slack")]:
        item = review_decide.load_item(conn, cfg, date(2026, 11, 19), key)
        texts += [o["label"] for o in review_pass.options(item, None, [], date(2026, 11, 19))]
    # Recommendations (spoken Review stage 2c): every fixed text, the reasons a close is risky, and a recommendation's words on the table's items.
    from factotum import recommend, review_queue
    texts += list(recommend.TEXTS.values()) + list(review_queue.RISKY.values())
    for e in review_queue.items(conn, cfg, date(2026, 11, 19))["tasks"] + review_queue.items(conn, cfg, date(2026, 11, 19))["cards"]:
        r = e.get("recommendation") or {}
        texts += [r.get("label") or "", r.get("basis") or "", r.get("said") or "", r.get("basis_said") or "", e.get("rec_held") or ""]
    # The spoken card (spoken Review stage 2e): every fixed sentence, and everything it says over the table's items as the pass shows them.
    from factotum import spoken
    texts += list(spoken.TEMPLATES.values())
    v = review_pass.begin_or_resume(conn, cfg, date(2026, 11, 19), by="page · test")
    said = 0
    for k in [r["key"] for r in v["list"] if r["state"] != "gone"]:
        review_pass.move(conn, cfg, date(2026, 11, 19), v["id"], "goto", key=k, by="page · test")
        cur = review_pass.view(conn, cfg, date(2026, 11, 19))["current"] or {}
        for s in [cur.get("spoken")] + [m.get("spoken") for m in (cur.get("item") or {}).get("members") or []]:
            if s:
                said += 1
                texts += [s["say"], s["basis"], s["question"], *s["more"].values(), *(o["words"] for o in s["options"]), *(m["words"] for m in s["members"])]
    conn.rollback()
    assert said >= 4
    for t in texts:
        assert not re.search(r"\bblocked\b|\bdependenc", t, re.I), t[:120]


def test_a_merge_into_a_reopened_duplicate_passes_the_validator(cfg, tmp_path):
    c = _db(tmp_path, 3)
    batches.apply(c, [{"op": "merge", "task_id": 1, "fields": {"into": 2}}], "ui", TODAY)
    v = lambda *chs: M._validate_changes(_ctx(cfg, tmp_path), list(chs))                   # noqa: E731
    merge = {"op": "merge", "task_id": 3, "fields": {"into": 1}}
    assert v(merge)[1] == ["#0: Task-1 was itself merged into Task-2; merge into Task-2"]
    batches.apply(c, [{"op": "reopen", "task_id": 1}], "ui", TODAY)
    assert v(merge) == ([merge], [], [])


def test_two_create_clicks_at_once_make_one_task_and_no_empty_batch(conn, cfg, monkeypatch):
    import threading
    import time
    from factotum import chat
    mid = _chat_msg(conn, [{"op": "create", "task_id": None, "fields": {"name": "Once"}, "label": "create Once", "evidence": [], "applied": False}])
    real = batches.apply
    monkeypatch.setattr(chat.batches, "apply", lambda *a, **k: (time.sleep(0.2), real(*a, **k))[1])     # widen the window between the check and the write
    n0 = conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0]
    out = []
    ts = [threading.Thread(target=lambda: out.append(chat.create_from_proposal(conn, cfg, mid, 0, TODAY))) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(out) == 2 and out[0] == out[1]
    assert conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == n0 + 1


def test_create_returns_the_task_another_process_made_meanwhile(conn, cfg, monkeypatch):
    from factotum import chat
    from factotum.db import upsert_task
    from stage4_helpers import task
    mid = _chat_msg(conn, [{"op": "create", "task_id": None, "fields": {"name": "Once"}, "label": "create Once", "evidence": [], "applied": False}])
    real = batches.apply

    def racing(c, changes, *a, **k):                                       # the CLI made it between the check and the write
        upsert_task(c, task(id=77, name="Once", source="Chat", source_key=f"chat:{mid}:0")); c.commit()
        return real(c, changes, *a, **k)
    monkeypatch.setattr(chat.batches, "apply", racing)
    assert chat.create_from_proposal(conn, cfg, mid, 0, TODAY) == 77
