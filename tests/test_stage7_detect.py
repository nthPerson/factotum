"""Stage 7, suggestions (design §10.1): one card per pair, the detectors, the scheduler job, the Review cap."""
import json
import time
from datetime import date, datetime

from factotum import batches, canvas, relations, suggestions, sync_file
from factotum.db import add_link, connect, get_settings, init_db, one, rows
from stage4_helpers import TODAY, active_rels, link, mk


def _sugg(conn):
    """Every link_suggestion row, oldest first: its ref plus evidence_id and judged."""
    return [dict(json.loads(r["ref"]), evidence_id=r["id"], judged=r["judged_at"] is not None)
            for r in rows(conn, "SELECT id, ref, judged_at FROM evidence WHERE kind = 'link_suggestion' ORDER BY id")]


def test_twins_need_the_same_number_a_canvas_row_and_36_hours(conn):
    mk(conn, 1, name="Submit COMPE 510 Assignment 03", source="Canvas", due="2026-11-10T23:59")
    mk(conn, 2, name="Assignment 3 writeup", source="Plaud", due="2026-11-11T08:00", created_at="2026-10-20T00:00:00+00:00")
    mk(conn, 3, name="Assignment 4 writeup", source="Plaud", due="2026-11-10")                      # a sibling: another number
    mk(conn, 4, name="Assignment 3 slides", source="Plaud", due="2026-11-14")                       # three days later
    mk(conn, 5, name="Assignment 3 notes", source="Plaud", due="2026-11-10", project="Personal")    # another project
    assert [(c["kind"], c["a"], c["b"], c["signal"]) for c in suggestions.twins(conn)] == [("same_as", 2, 1, "twin")]
    assert suggestions.twins(conn, {3}) == []


def test_a_course_series_makes_no_twins_and_an_ambiguous_row_makes_none(conn):
    for i, name in enumerate(["Take COMPE 510 Quiz 5", "Post in COMPE 510 Discussion 5", "Submit COMPE 510 Reading Response 5",
                              "Take COMPE 510 Quiz 4", "Take COMPE 510 Quiz 6"], start=1):
        mk(conn, i, name=name, source="Canvas", due="2026-11-10T23:59")
    assert suggestions.twins(conn) == []                                        # three Canvas items of one week are three items
    mk(conn, 10, name="Finish the discussion post 5", source="Plaud", due="2026-11-10")
    mk(conn, 11, name="Week 5 stuff", source="Plaud", due="2026-11-10")
    assert [(c["a"], c["b"]) for c in suggestions.twins(conn)] == [(10, 2)]     # the one it shares a word with; 11 is ambiguous: none
    mk(conn, 12, name="Take COMPE 510 Quiz 05", source="Canvas", due="2026-11-11T09:00", created_at="2026-10-02T00:00:00+00:00")
    assert (12, 1) in [(c["a"], c["b"]) for c in suggestions.twins(conn)]       # Canvas posted Quiz 5 twice: the older is kept


def test_one_pair_gets_one_card_whichever_signal_finds_it_and_never_again_once_judged(conn):
    mk(conn, 1); mk(conn, 2); mk(conn, 3)
    e = suggestions.suggest_pair(conn, "waits_on", 1, 2, "x", "text")
    assert e and suggestions.suggest_pair(conn, "any", 2, 1, "y", "thread") is None           # pending: any kind, either order
    assert suggestions.why_not(conn, "same_as", 1, 2) == "suggested before"
    batches.apply(conn, [{"task_id": 1, "op": "judge_evidence", "fields": {"evidence_id": e, "verdict": "dismissed"}}], "review", TODAY)
    assert suggestions.suggest_pair(conn, "same_as", 2, 1, "z", "twin") is None               # dismissed: whichever signal finds it next
    link(conn, 1, 3, "part_of")
    assert suggestions.why_not(conn, "waits_on", 3, 1) == "already linked"
    batches.apply(conn, [{"op": "unlink", "task_id": 1, "fields": {"kind": "part_of", "other": 3}}], "ui", TODAY)
    assert suggestions.why_not(conn, "any", 1, 3) == "linked before"                          # the owner unlinked it: decided
    assert suggestions.why_not(conn, "any", 1, 1) == "the same task" and suggestions.why_not(conn, "any", 1, 99) == "no Task-99"
    mk(conn, 4, status="done", completed_at="2026-10-30"); mk(conn, 5)
    assert suggestions.why_not(conn, "waits_on", 5, 4) == "Task-4 is already done"
    assert suggestions.why_not(conn, "any", 5, 4) == "Task-4 is not open"
    assert suggestions.why_not(conn, "same_as", 5, 4) is None                                 # a duplicate may be merged into a finished task
    assert suggestions.live(conn, {"kind": "any", "a": 1, "b": 2}) and not suggestions.live(conn, {"kind": "any", "a": 5, "b": 4})


def test_two_tasks_on_one_thread_or_event_are_a_pair_but_a_recording_or_a_person_is_not(conn):
    for i in range(1, 10):
        mk(conn, i)
    add_link(conn, 1, "mail_thread", "conv-a"); add_link(conn, 2, "mail_thread", "conv-a")
    add_link(conn, 3, "calendar", "ev-1"); add_link(conn, 4, "calendar", "ev-1")
    add_link(conn, 5, "plaud_note", "of_abc"); add_link(conn, 6, "plaud_note", "of_abc")              # a shared recording: provenance
    conn.execute("UPDATE tasks SET counterpart = 'prof@example.edu' WHERE id IN (7, 8)")                  # a shared person: provenance
    add_link(conn, 9, "mail_thread", "conv-a"); conn.execute("UPDATE tasks SET status = 'done' WHERE id = 9")   # closed: not a pair
    conn.commit()
    assert [(c["a"], c["b"], c["kind"], c["why"]) for c in suggestions.threads(conn)] == [
        (1, 2, "any", "both follow the same mail thread"), (3, 4, "any", "both are on the same calendar event")]
    for i in range(20, 25):
        mk(conn, i); add_link(conn, i, "mail_thread", "conv-list")
    conn.commit()
    assert all(c["a"] < 20 for c in suggestions.threads(conn))                                           # five on one thread: a list, not a pair


CASES = [
    ("Merged into Task-20 on Oct 3", [("same_as", 20)]),
    ("Duplicate of Task-20.", [("same_as", 20)]),
    ("Do this after Task-20 is in.", [("waits_on", 20)]),
    ("Blocked on Task-20 (the chair).", [("waits_on", 20)]),
    ("Waiting on Task-20 first", [("waits_on", 20)]),
    ("This unlocks Task-20.", [("frees", 20)]),
    ("Needed before Task-20.", [("frees", 20)]),
    ("Sub-task of Task-20", [("part_of", 20)]),
    ("Step 2 of Task-20", [("part_of", 20)]),
    ("Part of task-20", [("part_of", 20)]),
    ("Sub-tasks: Task-20, Task-21 (the form)", [("goal_of", 20), ("goal_of", 21)]),
    ("See Task-20 for the numbers", [("any", 20)]),
    ("Like #20, and task 20, and Task-10", []),                                                      # "#N" is noise, "task 20" is not the form, Task-10 is itself
    ("After Task-20. See Task-20 again.", [("waits_on", 20)]),                                       # one entry per task, the first wins
]


def test_task_n_in_text_is_read_with_its_kind_guessed_from_the_words_nearby():
    for text, want in CASES:
        assert suggestions.refs_in(text, 10) == want, text


def test_text_references_become_cards_only_for_tasks_that_exist_and_obey_the_link_rules(conn):
    mk(conn, 10, context="After Task-20.\nMerged into Task-30.\nSub-task of Task-40.\nSee Task-99.\nBefore Task-50.")
    mk(conn, 20); mk(conn, 30, status="done", completed_at="2026-10-30"); mk(conn, 40); mk(conn, 41)
    mk(conn, 50, status="dropped", completed_at="2026-10-30")
    link(conn, 40, 41, "part_of")                                                                    # 40 is a step: it cannot have steps
    got = [(c["kind"], c["a"], c["b"], c["why"], c["signal"]) for c in suggestions.text_refs(conn)]
    assert got == [("waits_on", 10, 20, "Task-10's text names Task-20", "text"), ("same_as", 10, 30, "Task-10's text names Task-30", "text")]
    assert not [c for c in suggestions.text_refs(conn) if "blocked" in c["why"].lower()]


def test_hostile_text_is_read_within_bounds():
    huge = ("after " * 50000) + ("Task-20 " * 50000) + ("Sub-tasks: " + "Task-21, " * 20000)
    t0 = time.monotonic()
    refs = suggestions.refs_in(huge, 10)
    assert time.monotonic() - t0 < 1.0 and len(refs) <= suggestions.MAX_REFS


def _item(key, name, due):
    return {"key": key, "type": "assignment", "name": name, "title": name, "project": "COMPE 510", "area": "Academics", "context": "",
            "due": due, "effort": "M", "state": None, "dismissed": False, "url": None, "points": None}


def test_the_sweep_writes_each_pair_once_and_a_second_pass_writes_nothing(conn):
    mk(conn, 1, name="Take COMPE 510 Quiz 7", source="Canvas", due="2026-11-10T23:59")
    mk(conn, 2, name="Quiz 7 prep", source="Plaud", due="2026-11-10", context="Same as Task-1, the Canvas one.")
    mk(conn, 3); mk(conn, 4); add_link(conn, 3, "mail_thread", "c"); add_link(conn, 4, "mail_thread", "c"); conn.commit()
    out = suggestions.sweep(conn, TODAY)
    assert out["written"] == {"twin": 1, "thread": 1}
    assert [(s["a"], s["b"], s["signal"]) for s in _sugg(conn)] == [(2, 1, "twin"), (3, 4, "thread")]
    assert suggestions.sweep(conn, TODAY)["written"] == {} and active_rels(conn) == set()          # cards only, never a link


def test_a_create_from_a_batch_canvas_or_a_recording_is_checked_at_once(conn, cfg):
    mk(conn, 1, name="Submit COMPE 510 Assignment 5", source="Canvas", due="2026-11-08T23:59")
    res = batches.apply(conn, [{"op": "create", "fields": {"name": "Assignment 5 report", "project": "COMPE 510", "due": "2026-11-08"}}], "chat", TODAY)
    assert [(s["a"], s["b"], s["signal"]) for s in _sugg(conn)] == [(res["created"][0], 1, "twin")]
    mk(conn, 50, name="Assignment 6 notes", source="Plaud", due="2026-11-15")
    canvas.sweep(conn, cfg, [_item("canvas:assignment:66", "Submit COMPE 510 Assignment 6", "2026-11-15T23:59")], TODAY)
    new = one(conn, "SELECT id FROM tasks WHERE source_key = 'canvas:assignment:66'")["id"]
    assert (50, new, "twin") in [(s["a"], s["b"], s["signal"]) for s in _sugg(conn)]
    data = {"tasks": [{"op": "create", "name": "Draft the dataset request", "project": "COMPE 510", "due": None, "context": "Do this after Task-1.",
                       "source_key": "plaud:of_s7:1"}], "instructions": [], "reminders": [], "calendar": [], "unresolved": []}
    r = sync_file.apply_result(conn, cfg, {"id": "of_s7", "name": "Lab sync", "start_utc": "2026-11-02T17:00:00Z"}, data, TODAY, calendar=None)
    assert ("waits_on", r["created"][0], 1, "text") in [(s["kind"], s["a"], s["b"], s["signal"]) for s in _sugg(conn)]


def test_a_detector_that_fails_at_create_fails_neither_the_sweep_nor_the_recording(conn, cfg, monkeypatch, capsys):
    def boom(db, ids):
        db.execute("INSERT INTO evidence (task_id, kind, ref, label, at) VALUES (NULL, 'link_suggestion', '{}', 'half-written', '2026-11-02')")
        raise RuntimeError("detector broke")
    monkeypatch.setattr(suggestions, "detect", boom)
    assert canvas.sweep(conn, cfg, [_item("canvas:assignment:77", "Submit COMPE 510 Assignment 7", "2026-11-22T23:59")], TODAY).startswith("created 1")
    data = {"tasks": [{"op": "create", "name": "Draft the dataset request", "project": "COMPE 510", "due": None, "context": "x",
                       "source_key": "plaud:of_s7:1"}], "instructions": [], "reminders": [], "calendar": [], "unresolved": []}
    r = sync_file.apply_result(conn, cfg, {"id": "of_s7", "name": "Lab sync", "start_utc": "2026-11-02T17:00:00Z"}, data, TODAY, calendar=None)
    assert len(r["created"]) == 1 and not conn.in_transaction
    assert one(conn, "SELECT COUNT(*) AS n FROM tasks WHERE source_key IN ('canvas:assignment:77', 'plaud:of_s7:1')")["n"] == 2
    assert _sugg(conn) == []                                                                # the detector's own half-write is undone
    assert capsys.readouterr().err.count("suggestions at create failed: RuntimeError: detector broke") == 2


def test_the_scheduler_runs_the_detectors_after_the_sweeps_every_half_hour(conn, cfg):
    from factotum.scheduler import Scheduler
    mk(conn, 1, name="Take COMPE 510 Quiz 7", source="Canvas", due="2026-11-10T23:59"); mk(conn, 2, name="Quiz 7 prep", source="Plaud", due="2026-11-10")
    s = Scheduler(cfg, conn, push=lambda *a, **k: None, mailer=None)
    assert "link_suggestions: 1 suggested: twin 1" in s.tick(datetime(2026, 11, 2, 10, 0))
    assert get_settings(conn)["state.links.session"]["ids"]
    assert not [d for d in s.tick(datetime(2026, 11, 2, 10, 10)) if d.startswith("link_suggestions")]
    assert one(conn, "SELECT summary FROM runs WHERE kind = 'link_suggestions'")["summary"] == "1 suggested: twin 1"
    assert not conn.in_transaction


def test_links_check_reads_and_sweep_links_writes(conn, cfg, tmp_path, monkeypatch, capsys):
    from conftest import FIXTURE
    from factotum import __main__ as cli
    path = tmp_path / "cli.db"
    c = connect(path); init_db(c)
    mk(c, 1, name="Take COMPE 510 Quiz 7", source="Canvas", due="2026-11-10T23:59"); mk(c, 2, name="Quiz 7 prep", source="Plaud", due="2026-11-10")
    monkeypatch.setenv("FACTOTUM_CONFIG", str(FIXTURE)); monkeypatch.setenv("FACTOTUM_DB", str(path))
    capsys.readouterr()
    cli.main(["links-check"])
    out = capsys.readouterr().out
    assert "twin    Task-2 → Task-1  same as" in out and "would write" in out and _sugg(c) == []
    cli.main(["sweep", "links"])
    assert [(s["a"], s["b"]) for s in _sugg(c)] == [(2, 1)]


def _many(conn, n):
    """n `any` suggestions on n separate pairs (100, 101), (102, 103), …; their ids, oldest first."""
    ids = []
    for i in range(n):
        a, b = 100 + 2 * i, 101 + 2 * i
        mk(conn, a); mk(conn, b)
        ids.append(relations.suggest(conn, "any", a, b, "x", "thread"))
    conn.commit()
    return ids


def test_a_day_offers_at_most_n_link_suggestions_and_keeps_the_rest(conn):
    ids = _many(conn, 8)
    d1 = date(2026, 11, 2)
    assert suggestions.session(conn, d1, 5) == ids[:5] and relations.shown_ids(conn) == set(ids[:5])
    batches.apply(conn, [{"task_id": 100, "op": "judge_evidence", "fields": {"evidence_id": ids[0], "verdict": "dismissed"}}], "review", d1)
    assert suggestions.session(conn, d1, 5) == ids[1:5]                                      # the same day: no top-up
    assert set(suggestions.session(conn, date(2026, 11, 3), 5)) == {ids[5], ids[6], ids[7], ids[1], ids[2]}
    assert suggestions.session(conn, date(2026, 11, 4), 0) == []


def test_an_ignored_suggestion_never_keeps_another_out_for_good(conn):
    ids = _many(conn, 7)
    seen = set()
    for d in (2, 3):
        seen |= set(suggestions.session(conn, date(2026, 11, d), 5))
    assert seen == set(ids)


def test_a_suggestion_whose_task_closed_gives_its_slot_up(conn):
    ids = _many(conn, 2)
    batches.apply(conn, [{"op": "done", "task_id": 100}], "ui", TODAY)
    assert suggestions.session(conn, date(2026, 11, 2), 5) == [ids[1]]


def test_a_damaged_session_row_or_a_bad_number_never_breaks_the_day(conn, cfg):
    from factotum.db import set_setting
    ids = _many(conn, 3)
    d = date(2026, 11, 2)
    for session_v, offered_v in (([1, 2], "x"), ({"day": d.isoformat(), "ids": "12"}, {"5": 3}), ({"day": d.isoformat(), "ids": ["x", None]}, [])):
        set_setting(conn, suggestions.SESSION_KEY, session_v); set_setting(conn, suggestions.OFFERED_KEY, offered_v); conn.commit()
        assert suggestions.session(conn, d, 2) == ids[:2]
    for v, n in (("7", 7), ("lots", 5), (True, 5), (-3, 0), (None, 0), ([], 5), (float("inf"), 5)):
        assert suggestions.per_review({"links": {"suggestions_per_review": v}}) == n
    assert suggestions.per_review({}) == 5


def test_review_the_badges_and_the_agent_see_the_same_days_suggestions(cfg, tmp_path):
    from fastapi.testclient import TestClient
    from factotum import api
    path = tmp_path / "t.db"
    conn = connect(path); init_db(conn)
    ids = _many(conn, 6)
    mk(conn, 300); mk(conn, 301)
    fe = relations.suggest(conn, "part_of", 300, 301, "a piece of it", "filing"); conn.commit()
    client = TestClient(api.create_app(cfg, db_path=str(path)))
    items = client.get("/api/review").json()["items"]
    shown = {s["evidence_id"] for i in items for s in i["link_suggestions"]}
    assert shown == set(ids[:5]) | {fe}                                                      # five a day, filing ones besides
    listed = {s["evidence_id"] for t in client.get("/api/tasks").json()["tasks"] for s in (t.get("rel") or {}).get("suggestions", [])}
    assert listed == shown
    assert not [i for i in items if i["task"]["id"] in (110, 111) and "link_suggested" in i["reasons"]]   # pair six waits for another day


def test_the_suggestions_number_is_a_settings_key_with_one_default_everywhere():
    import yaml
    from factotum import config as C
    from conftest import FIXTURE
    assert "links.suggestions_per_review" in C.SETTINGS_KEYS and C.DEFAULTS["links.suggestions_per_review"] == 5
    for p in (C.EXAMPLE_CONFIG, C.STARTER_CONFIG, FIXTURE):
        assert C.get(yaml.safe_load(p.read_text()), "links.suggestions_per_review") == 5, p
