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
