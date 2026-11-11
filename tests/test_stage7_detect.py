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
