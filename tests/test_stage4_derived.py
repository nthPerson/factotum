"""Stage 4a, the derived half (design §7): one step (`relations.annotate`) derives every neighbour fact
under `task["rel"]`, and every surface that scores or runs the lifecycle reads annotated rows."""
import copy
from datetime import date

from factotum import batches, config as C, lifecycle, relations, scoring
from factotum.db import connect, now_iso, rows
from conftest import task
from stage4_helpers import TODAY, link, mk


# --- Task 8: annotate and every surface

def _done(conn, tid: int, day: date = TODAY) -> dict:
    return batches.apply(conn, [{"op": "done", "task_id": tid}], "ui", day)


def _rel(conn) -> dict[int, dict]:
    return {t["id"]: t["rel"] for t in relations.annotate(conn, rows(conn, "SELECT * FROM tasks"))}


def test_annotate_derives_every_key(conn):
    extra = {4: {"last_mention_at": "2026-10-28"}, 9: {"due": "2026-11-06", "hard": 1}}
    for i in range(1, 12):
        mk(conn, i, **extra.get(i, {}))
    link(conn, 1, 2, "waits_on"); link(conn, 1, 3, "waits_on")
    _done(conn, 3, date(2026, 10, 30))                       # a link to a closed task is refused, so link first
    link(conn, 4, 5, "part_of"); link(conn, 6, 5, "part_of")
    _done(conn, 6)
    link(conn, 9, 8, "waits_on"); link(conn, 8, 7, "waits_on")
    b = batches.apply(conn, [{"op": "merge", "task_id": 10, "fields": {"into": 11}}], "ui", TODAY)
    assert b["applied"] == 1
    ts = rows(conn, "SELECT * FROM tasks")
    assert relations.annotate(conn, ts) is ts and all(isinstance(t["rel"], dict) for t in ts)
    by = _rel(conn)
    assert by[1]["waits_on_open"] == [2] and by[1]["waits_on_closed"] == [{"id": 3, "status": "done", "at": "2026-10-30"}] and by[2]["frees_open"] == [1]
    assert (by[1]["freed_at"], by[1]["freed_by"]) == (None, None)                 # still waits on 2
    assert (by[5]["steps_open"], by[5]["steps_total"], by[4]["goal_id"]) == (1, 2, 5) and by[5]["neighbour_activity_at"] == "2026-11-01"   # 6 closed TODAY
    assert by[4]["neighbour_activity_at"] is None and by[5]["goal_id"] is None
    assert (by[8]["needed_by"], by[8]["needed_by_id"], by[8]["needed_by_via"]) == ("2026-11-06", 9, "frees")
    assert (by[7]["needed_by"], by[7]["needed_by_via"]) == ("2026-11-06", "chain")
    assert by[9]["needed_by"] is None and by[9]["waits_on_open"] == [8]
    assert by[11]["merged_at"] and {"kind": "same_as", "dir": "in", "other": 10} in [{k: l[k] for k in ("kind", "dir", "other")} for l in by[11]["links"]]
    assert by[10]["merged_at"] is None and by[3]["changed"] is None and by[3]["suggestions"] == []
    _done(conn, 2)
    by = _rel(conn)
    assert by[1]["waits_on_open"] == [] and by[1]["freed_by"] == 2 and by[1]["freed_at"] == "2026-11-01"
    assert by[2]["frees_open"] == [1]                                              # 1 is still open; the link stays
    assert relations.annotate(conn, [{"id": 1}])[0]["rel"]["freed_by"] == 2       # a partial row needs only its id
    assert relations.annotate(conn, []) == []


def test_annotate_is_safe_against_a_loop_in_old_data_and_stops_at_five_hops(conn):
    mk(conn, 1); mk(conn, 2, due="2026-11-05", hard=1)
    for s, d in ((1, 2), (2, 1)):                                                  # no batches: rows from before the rules
        conn.execute("INSERT INTO relations (src, dst, kind, source, created_at) VALUES (?, ?, 'waits_on', 'test', ?)", (s, d, now_iso()))
    conn.commit()
    by = _rel(conn)
    assert by[1]["waits_on_open"] == [2] and by[1]["frees_open"] == [2]
    assert (by[1]["needed_by"], by[1]["needed_by_id"], by[1]["needed_by_via"]) == ("2026-11-05", 2, "frees")
    for i in range(20, 27):
        mk(conn, i)
    mk(conn, 27, due="2026-11-20", hard=1)
    for i in range(21, 28):
        link(conn, i, i - 1, "waits_on")
    by = _rel(conn)
    assert (by[26]["needed_by"], by[26]["needed_by_id"], by[26]["needed_by_via"]) == ("2026-11-20", 27, "frees")
    assert (by[22]["needed_by"], by[22]["needed_by_id"], by[22]["needed_by_via"]) == ("2026-11-20", 27, "chain")    # 5 hops
    assert by[21]["needed_by"] is None and by[20]["needed_by"] is None                                             # 6 and 7


def test_changed_names_the_task_whose_batch_touched_this_one_since_last_seen(conn):
    mk(conn, 1); mk(conn, 5)
    res = batches.apply(conn, [{"op": "set", "task_id": 1, "fields": {"context": "the data arrived"}}], "ripple", TODAY, cause={"kind": "ripple", "from": [5], "run": 1})
    batches.apply(conn, [{"op": "set", "task_id": 1, "fields": {"effort": "S"}}], "ui", TODAY)       # no cause: not a neighbour's doing
    ch = _rel(conn)[1]["changed"]
    assert (ch["by"], ch["batch_id"]) == (5, res["batch_id"]) and ch["at"]
    assert _rel(conn)[5]["changed"] is None
    conn.execute("UPDATE tasks SET neighbours_seen_at = ? WHERE id = 1", (now_iso(),)); conn.commit()
    assert _rel(conn)[1]["changed"] is None
    conn.execute("UPDATE tasks SET neighbours_seen_at = NULL WHERE id = 1"); conn.commit()
    assert _rel(conn)[1]["changed"]["by"] == 5
    batches.undo(conn, res["batch_id"])
    assert _rel(conn)[1]["changed"] is None


def test_only_relations_calls_scoring_score():
    for p in (C.ROOT / "factotum").rglob("*.py"):
        if p.name not in ("scoring.py", "relations.py"):
            assert "scoring.score(" not in p.read_text(encoding="utf-8"), p


def test_every_surface_reads_annotated_rows(conn, cfg, tmp_path, monkeypatch, capsys):
    """A probe score that only an annotated row can earn, and a probe review reason likewise: every
    surface must show them. A caller that forgets `relations` fails here by name."""
    from fastapi.testclient import TestClient
    from factotum import __main__ as cli, brief, chat, mcp_server as M, notion_mirror as NM, planner
    from factotum.api import create_app
    from factotum.scheduler import Scheduler
    from test_phase6 import FakeNotion
    D = date.today()
    for i in (1, 2, 3):
        mk(conn, i, project="FAMAIL", last_mention_at=D.isoformat())      # fresh: the real expire must find nothing to fade
    link(conn, 1, 2, "waits_on")
    path = tmp_path / "t.db"
    monkeypatch.setattr(scoring, "score", lambda t, today, cfg: (77 if isinstance(t.get("rel"), dict) else 1, "P1", ["probe"]))
    monkeypatch.setattr(lifecycle, "review_reasons", lambda t, today, cfg, last, flags=None: ["new"] if isinstance(t.get("rel"), dict) and t.get("status") == "open" else [])
    real_expire, seen = lifecycle.expire, []
    monkeypatch.setattr(lifecycle, "expire", lambda ts, today, cfg: (seen.append(all(isinstance(t.get("rel"), dict) for t in ts)), real_expire(ts, today, cfg))[1])

    client = TestClient(create_app(cfg, db_path=str(path)))
    assert [t["score"] for t in client.get("/api/tasks").json()["tasks"]] == [77, 77, 77], "API GET /api/tasks"
    assert client.get("/api/tasks/1").json()["score"] == 77, "API GET /api/tasks/1"
    assert client.get("/api/review").json()["total"] == 3, "API GET /api/review"

    assert "Review holds 3 items" in brief.build(conn, cfg, D)["text"], "brief.build"
    b = planner.bundle(conn, cfg, D)
    assert [t["score"] for t in b["rows"]] == [77, 77, 77] and b["review_n"] == 3, "planner.bundle"

    ctx = M.Context(connect(path), cfg, today=D)
    assert [t["score"] for t in M.tasks_query(ctx, {})["tasks"]] == [77, 77, 77], "mcp tasks_query"
    assert M.task_get(ctx, {"task_id": 1})["task"]["score"] == 77, "mcp task_get"

    assert [r["score"] for r in chat.attached_rows(conn, cfg, [1, 2, 3], D)] == [77, 77, 77], "chat.attached_rows"

    pushed, real_properties = [], NM.properties
    monkeypatch.setenv("NOTION_TOKEN", "t")
    monkeypatch.setattr(NM, "properties", lambda cfg, t, sch, notes, today: (pushed.append(t), real_properties(cfg, t, sch, notes, today))[1])
    NM.sweep(conn, cfg, all_rows=True, transport=FakeNotion().transport(), pause=0)
    assert len(pushed) == 3 and all(isinstance(t.get("rel"), dict) for t in pushed), "notion_mirror.sweep"

    sent = []
    sch = Scheduler(cfg, conn, push=lambda *a, **k: sent.append(a), mailer=lambda *a, **k: None)
    sch._review_push(cfg, D, {})
    assert sent and sent[-1][1] == "Review: 3 items", "scheduler._review_push"
    seen.clear()
    sch._expire(cfg, D)
    assert seen == [True], "scheduler._expire"

    monkeypatch.setenv("FACTOTUM_CONFIG", str(C.ROOT / "config" / "factotum.example.yaml"))
    monkeypatch.setenv("FACTOTUM_DB", str(path))
    capsys.readouterr()
    cli.main(["score"])
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 3 and all(ln.startswith(" 77") for ln in lines), "__main__ score"
    cli.main(["review"])
    assert capsys.readouterr().out.rstrip().endswith("3 items"), "__main__ review"
    seen.clear()
    cli.main(["expire"])
    assert seen == [True], "__main__ expire"


# --- Task 9: scoring

def _r(**kw): return dict({"waits_on_open": [], "frees_open": [], "needed_by": None, "needed_by_id": None, "needed_by_via": None}, **kw)


def test_links_off_reproduces_todays_scores_exactly(cfg):
    off = copy.deepcopy(cfg); off["scoring"]["links"]["enabled"] = False
    for t in (task(project="FAMAIL", rel=_r(waits_on_open=[2])), task(project="FAMAIL", rel=_r(frees_open=[3, 4], needed_by="2026-11-06", needed_by_id=3, needed_by_via="frees"))):
        bare = {k: v for k, v in t.items() if k != "rel"}
        assert scoring.score(t, TODAY, off) == scoring.score(bare, TODAY, cfg)


def test_a_waiting_task_is_capped_unless_its_own_hard_date_is_near(cfg):
    soft = task(project="FAMAIL", due="2026-11-02", rel=_r(waits_on_open=[2]))
    s, _, why = scoring.score(soft, TODAY, cfg); assert s == 30 and "waits on Task-2" in why
    assert scoring.score(task(project="FAMAIL", due="2026-11-03", hard=1, rel=_r(waits_on_open=[2])), TODAY, cfg)[0] == 80   # exempt: hard, 2 days
    assert scoring.score(task(project="FAMAIL", due="2026-11-02", manual_priority="P1", rel=_r(waits_on_open=[2])), TODAY, cfg)[0] == 65  # floor wins
    many = task(project="FAMAIL", rel=_r(waits_on_open=[2, 3, 4]))
    assert "waits on Task-2, Task-3 and 1 more" in scoring.score(many, TODAY, cfg)[2][-1]


def test_a_task_that_frees_a_hard_date_is_scored_as_if_due_then(cfg):
    s, _, why = scoring.score(task(project="FAMAIL", rel=_r(frees_open=[3], needed_by="2026-11-06", needed_by_id=3, needed_by_via="frees")), TODAY, cfg)
    assert s == 65 and "frees Task-3, hard, Nov 6" in why and "frees 1 +5" in why
    s, _, why = scoring.score(task(project="FAMAIL", rel=_r(frees_open=[3], needed_by="2026-11-06", needed_by_id=3, needed_by_via="chain")), TODAY, cfg)
    assert s == 65 and "needed for Task-3, hard, Nov 6" in why
    s, _, why = scoring.score(task(project="FAMAIL", rel=_r(needed_by="2026-11-06", needed_by_id=3, needed_by_via="goal")), TODAY, cfg)
    assert s == 60 and "part of Task-3, hard, Nov 6" in why
    s, _, why = scoring.score(task(project="FAMAIL", due="2026-11-03", rel=_r(frees_open=[3], needed_by="2026-11-06", needed_by_id=3, needed_by_via="frees")), TODAY, cfg)
    assert s == 70 and not any("hard, Nov" in r for r in why)


def test_the_frees_bonus_is_capped_at_15(cfg):
    s, _, why = scoring.score(task(project="FAMAIL", rel=_r(frees_open=[2, 3, 4, 5])), TODAY, cfg)
    assert s == 30 and "frees 4 +15" in why


def test_open_steps_never_cap_a_goal(cfg):
    t = task(project="FAMAIL", due="2026-11-02")
    assert scoring.score(dict(t, rel=_r(steps_open=3)), TODAY, cfg) == scoring.score(t, TODAY, cfg)


def test_real_scores_agree_across_surfaces(conn, cfg, tmp_path):
    from factotum import chat, mcp_server as M, planner
    from factotum.api import create_app
    from fastapi.testclient import TestClient
    D = date.today()
    mk(conn, 1, project="FAMAIL", due=(D + __import__("datetime").timedelta(days=1)).isoformat())
    mk(conn, 2, project="FAMAIL")
    mk(conn, 3, project="FAMAIL", due=(D + __import__("datetime").timedelta(days=5)).isoformat(), hard=1)
    mk(conn, 4, project="FAMAIL", due=(D + __import__("datetime").timedelta(days=1)).isoformat(), hard=1)
    for i in (1, 3, 4):
        link(conn, i, 2, "waits_on")
    want = {1: 30, 2: 95, 3: 30, 4: 80}
    path = tmp_path / "t.db"
    conn.execute("PRAGMA wal_checkpoint(FULL)")
    client = TestClient(create_app(cfg, db_path=str(path)))
    assert {t["id"]: t["score"] for t in client.get("/api/tasks").json()["tasks"]} == want
    assert {r["id"]: r["score"] for r in planner.bundle(conn, cfg, D)["rows"]} == want
    ctx = M.Context(connect(path), cfg, today=D)
    assert {t["id"]: t["score"] for t in M.tasks_query(ctx, {})["tasks"]} == want
    assert {r["id"]: r["score"] for r in chat.attached_rows(conn, cfg, [1, 2, 3, 4], D)} == want


# --- Task 10: lifecycle and Review

def _lrel(**kw):
    return dict({"links": [], "waits_on_open": [], "waits_on_closed": [], "frees_open": [], "goal_id": None, "steps_open": 0, "steps_total": 0,
                 "freed_at": None, "freed_by": None, "neighbour_activity_at": None, "merged_at": None, "changed": None, "suggestions": []}, **kw)


def test_a_waiting_task_does_not_fade_and_its_clock_starts_when_freed(cfg):
    t = task(project="FAMAIL", last_mention_at="2026-09-01", rel=_lrel(waits_on_open=[2]))
    assert lifecycle.fade_at(t, cfg) is None and lifecycle.expire([dict(t, last_offered_at="2026-10-30")], TODAY, cfg) == []
    freed = task(project="FAMAIL", last_mention_at="2026-09-01", rel=_lrel(freed_at="2026-10-30", freed_by=2, waits_on_closed=[{"id": 2, "status": "done", "at": "2026-10-30"}]))
    assert lifecycle.fade_at(freed, cfg) == date(2026, 11, 20)


def test_a_steps_activity_keeps_its_goal_alive_and_not_the_reverse(cfg):
    goal = task(project="FAMAIL", last_mention_at="2026-09-01", rel=_lrel(steps_total=2, steps_open=1, neighbour_activity_at="2026-10-28"))
    assert lifecycle.fade_at(goal, cfg) == date(2026, 11, 18)
    step = task(project="FAMAIL", last_mention_at="2026-09-01", rel=_lrel(goal_id=5))
    assert lifecycle.fade_at(step, cfg) == date(2026, 9, 22)


def test_the_five_reasons_come_from_rel_without_flags(cfg):
    def t(**rel):
        return task(project="FAMAIL", last_mention_at=TODAY.isoformat(), last_reviewed_at="2026-10-01", rel=_lrel(**rel))
    cases = {
        "freed": t(freed_at="2026-10-30", freed_by=2, waits_on_closed=[{"id": 2, "status": "done", "at": "2026-10-30"}]),
        "blocker_gone": t(waits_on_closed=[{"id": 2, "status": "dropped", "at": "2026-10-30"}]),
        "steps_done": t(steps_total=2, steps_open=0, neighbour_activity_at="2026-10-30"),
        "linked_changed": t(changed={"by": 5, "at": "2026-10-30T00:00:00+00:00", "batch_id": 1}),
        "link_suggested": t(suggestions=[{"evidence_id": 1, "kind": "same_as", "other": 9, "keep": 9}]),
    }
    for reason, tk in cases.items():
        assert lifecycle.review_reasons(tk, TODAY, cfg, None) == [reason], reason
    texts = {r: lifecycle.reason_texts(tk, [r])[r] for r, tk in cases.items()}
    assert texts == {"freed": "Free now: Task-2 is done", "blocker_gone": "Task-2 was dropped. Is this still needed?",
                     "steps_done": "All steps are done. Close this, or is there more?", "linked_changed": "A linked task changed (Task-5)",
                     "link_suggested": "Suggested: same as Task-9?"}
    assert lifecycle.reason_texts(cases["linked_changed"], ["linked_changed", "new"]).keys() == {"linked_changed"}
    # a Keep on or after the event suppresses it
    kept = dict(cases["freed"], last_reviewed_at="2026-10-30")
    assert lifecycle.review_reasons(kept, TODAY, cfg, None) == []
    kept = dict(cases["steps_done"], last_reviewed_at="2026-10-30")
    assert lifecycle.review_reasons(kept, TODAY, cfg, None) == []
    # never reviewed: plus "new"
    fresh = dict(cases["blocker_gone"], last_reviewed_at=None)
    assert lifecycle.review_reasons(fresh, TODAY, cfg, None) == ["blocker_gone", "new"]
    # a freed link to a task that was dropped is not "freed"
    d = t(freed_at="2026-10-30", freed_by=2, waits_on_closed=[{"id": 2, "status": "dropped", "at": "2026-10-30"}])
    assert "freed" not in lifecycle.review_reasons(d, TODAY, cfg, None)


def test_review_order_puts_link_reasons_after_the_planner(cfg):
    p = task(id=1, project="FAMAIL", last_mention_at=TODAY.isoformat(), last_reviewed_at="2026-10-01", rel=_lrel())
    f = task(id=2, project="FAMAIL", last_mention_at=TODAY.isoformat(), last_reviewed_at="2026-10-01",
             rel=_lrel(freed_at="2026-10-30", freed_by=3, waits_on_closed=[{"id": 3, "status": "done", "at": "2026-10-30"}]))
    q = lifecycle.build_review_queue([f, p], TODAY, cfg, None, {1: {"planner": True}})
    assert [i["task"]["id"] for i in q] == [1, 2] and [i["primary"] for i in q] == ["planner", "freed"]


def test_a_review_decision_stamps_neighbours_seen_at(conn):
    mk(conn, 1)
    batches.apply(conn, [{"op": "set", "task_id": 1, "fields": {"due": "2026-11-09"}}], "ui", TODAY)
    assert conn.execute("SELECT neighbours_seen_at FROM tasks WHERE id = 1").fetchone()[0] is None
    batches.apply(conn, [{"op": "keep", "task_id": 1}], "ui", TODAY)
    assert conn.execute("SELECT neighbours_seen_at FROM tasks WHERE id = 1").fetchone()[0]


def test_a_second_keep_the_same_day_restamps_neighbours_seen_at(conn):
    mk(conn, 1)
    batches.apply(conn, [{"op": "keep", "task_id": 1}], "ui", TODAY)
    conn.execute("UPDATE tasks SET neighbours_seen_at = '2000-01-01T00:00:00+00:00' WHERE id = 1"); conn.commit()
    batches.apply(conn, [{"op": "keep", "task_id": 1}], "ui", TODAY)
    assert conn.execute("SELECT neighbours_seen_at FROM tasks WHERE id = 1").fetchone()[0] > "2000-01-01"


# --- Task 11: brief, reminders, summary, planner rows

def test_the_brief_says_waits_on_and_lists_free_since_yesterday(conn, cfg):
    from datetime import timedelta
    from factotum import brief
    mk(conn, 1, due=TODAY.isoformat()); mk(conn, 2); mk(conn, 3); mk(conn, 4); mk(conn, 5)
    link(conn, 1, 2, "waits_on"); link(conn, 3, 4, "waits_on"); link(conn, 5, 4, "waits_on")
    _done(conn, 4, TODAY - timedelta(days=1))
    conn.execute("UPDATE tasks SET status = 'done' WHERE id = 5"); conn.commit()   # a closed task freed by 4 is not listed
    text = brief.build(conn, cfg, TODAY)["text"]
    line = next(ln for ln in text.splitlines() if "Task-1 " in ln)
    assert "waits on Task-2" in line
    after = text.split("Free since yesterday", 1)[1]
    assert text.index("Closed since yesterday") < text.index("Free since yesterday")
    assert "Task-3 Task 3  (Task-4 is done)" in after and "Task-5" not in after
    assert "waits on" not in next(ln for ln in text.splitlines() if "Task-3 " in ln)


def test_a_reminder_body_says_waits_on_and_frees(conn, cfg):
    from datetime import datetime
    from factotum import reminders
    mk(conn, 1); mk(conn, 2); mk(conn, 3)
    link(conn, 1, 2, "waits_on")
    for tid in (1, 2, 3):
        conn.execute("INSERT INTO reminders (task_id, fire_at, label, status, created_by, created_at) VALUES (?, '2026-11-01T08:00', 'Go', 'pending', 'ui', ?)", (tid, now_iso()))
    conn.commit()
    due = {r["task_id"]: r for r in reminders.due(conn, datetime(2026, 11, 1, 9, 0))}
    assert set(due) == {1, 2, 3}                                                     # a waiting task's reminder still fires
    assert reminders._body(due[1]) == "Go · waits on Task-2"
    assert reminders._body(due[2]) == "Go · frees Task-1 when done"
    assert reminders._body(due[3]) == "Go"
    many = {"label": "Go", "task_due": "2026-11-05", "rel": {"waits_on_open": [2, 3, 4, 5, 6], "frees_open": [7]}}
    assert reminders._body(many) == "Go · due Nov 5 · waits on Task-2, Task-3, Task-4 +2 · frees Task-7 when done"


def test_the_summary_goes_stale_when_a_task_it_names_is_freed(conn):
    from factotum import summary
    mk(conn, 1); mk(conn, 2)
    link(conn, 1, 2, "waits_on")
    s = summary.store(conn, "Task-1 waits on Task-2", [1], TODAY, "planner")
    conn.commit()
    assert s["tasks"]["1"][-1] == [2] and summary.stale(conn, s, TODAY) == []
    _done(conn, 2)
    assert "Task-1 Task 1: free now" in summary.stale(conn, s, TODAY)
    mk(conn, 3)
    batches.apply(conn, [{"op": "reopen", "task_id": 2}], "ui", TODAY)
    s = summary.store(conn, "Task-1 is free", [1], TODAY, "planner")
    link(conn, 1, 3, "waits_on")
    assert summary.stale(conn, s, TODAY) == ["Task-1 Task 1: now waits on Task-2, Task-3"]


def test_a_fingerprint_stored_before_this_stage_does_not_read_as_changed(conn):
    from factotum import summary
    from factotum.db import set_setting
    mk(conn, 1); mk(conn, 2)
    link(conn, 1, 2, "waits_on")
    set_setting(conn, "state.summary", {"text": "x", "day": TODAY.isoformat(), "tasks": {"1": ["open", None, 0, None, None]}, "focus": []})
    conn.commit()
    s = summary.get(conn, TODAY)
    assert s["stale"] == []
    conn.execute("UPDATE tasks SET hard = 1 WHERE id = 1"); conn.commit()
    assert summary.get(conn, TODAY)["stale"] == ["Task-1 Task 1: now a hard deadline"]


def test_planner_rows_carry_link_facts(conn, cfg):
    from factotum import planner
    mk(conn, 1, project="FAMAIL", context="the ctx"); mk(conn, 2, project="FAMAIL"); mk(conn, 3, project="FAMAIL")
    link(conn, 1, 2, "waits_on")
    lines = {ln.split(" | ")[0]: ln for ln in planner.sections(planner.bundle(conn, cfg, TODAY), TODAY)["rows"].splitlines()}
    assert lines["Task-1"].endswith(" | waits on Task-2 | the ctx")
    assert " | frees Task-1 | " in lines["Task-2"]
    assert lines["Task-3"].count(" | ") == 5                                         # no link, no extra piece


def test_words_says_each_link_fact():
    assert relations.words({"id": 1}) == "" and relations.words({"rel": {}}) == ""
    rel = {"waits_on_open": [9, 10], "frees_open": [12, 14], "goal_id": 380, "steps_open": 2, "steps_total": 5, "needed_by": "2026-11-06"}
    assert relations.words({"rel": rel}) == "waits on Task-9, Task-10 · frees Task-12, 14 · part of Task-380 · 2 of 5 steps open · needed for Nov 6"


def test_steps_done_clears_after_a_keep_when_no_step_carries_an_activity_date(cfg):
    t = task(project="FAMAIL", last_mention_at=TODAY.isoformat(), last_reviewed_at=None, rel=_lrel(steps_total=2, steps_open=0, neighbour_activity_at=None))
    assert lifecycle.review_reasons(t, TODAY, cfg, None) == ["steps_done", "new"]
    assert lifecycle.review_reasons(dict(t, last_reviewed_at="2026-10-30"), TODAY, cfg, None) == []
