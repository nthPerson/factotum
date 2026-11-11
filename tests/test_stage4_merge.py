import json

from factotum import batches, canvas, db, relations, sync_file
from factotum.db import one, rows, upsert_task
from stage4_helpers import TODAY, active_rels, link, mk, task


# --- Task 5: merge

def _merge(conn, goes, keeps, **fields):
    return batches.apply(conn, [{"op": "merge", "task_id": goes, "fields": dict(fields, into=keeps)}], "ui", TODAY)


def _setup_first(conn):
    mk(conn, 1, name="Assignment 3 (recording)"); mk(conn, 2, name="Assignment 3", source="Canvas"); mk(conn, 3); mk(conn, 4)
    db.add_link(conn, 1, "mail_thread", "c1", "Thread"); db.add_link(conn, 1, "plaud_note", "of_1", "Rec")
    batches.apply(conn, [{"task_id": 1, "op": "add_reminder", "fields": {"fire_at": "2026-11-03T09:00"}}], "ui", TODAY)
    link(conn, 3, 1, "waits_on"); link(conn, 1, 4, "part_of")


def test_merge_drops_the_duplicate_moves_links_reminders_and_relations(conn):
    _setup_first(conn)
    res = batches.apply(conn, [{"op": "merge", "task_id": 1, "fields": {"into": 2}}], "ui", TODAY)
    assert res["applied"] == 1 and res["skipped"] == []
    d = one(conn, "SELECT status, closed_by, top_rank FROM tasks WHERE id = 1")
    assert (d["status"], d["closed_by"]) == ("dropped", "merge")
    assert {r["task_id"] for r in rows(conn, "SELECT task_id FROM links")} == {2}
    assert one(conn, "SELECT task_id FROM reminders")["task_id"] == 2
    assert active_rels(conn) == {(3, 2, "waits_on"), (2, 4, "part_of"), (1, 2, "same_as")}
    survivor_row = json.loads(one(conn, "SELECT new FROM changes WHERE batch_id = ? AND task_id = 2 AND field = 'link' AND new LIKE '%same_as%'", (res["batch_id"],))["new"])
    assert survivor_row["dir"] == "in" and survivor_row["moved"] == {"links": 2, "reminders": 1, "tasks": 2}


def test_merge_leaves_an_outside_link_the_survivor_already_has(conn):
    mk(conn, 1); mk(conn, 2)
    db.add_link(conn, 1, "mail_thread", "c1"); db.add_link(conn, 2, "mail_thread", "c1"); db.add_link(conn, 1, "plaud_note", "of_1")
    conn.commit()
    res = _merge(conn, 1, 2)
    assert {(r["task_id"], r["ref"]) for r in rows(conn, "SELECT task_id, ref FROM links")} == {(1, "c1"), (2, "c1"), (2, "of_1")}
    new = json.loads(one(conn, "SELECT new FROM changes WHERE batch_id = ? AND task_id = 2 AND field = 'link'", (res["batch_id"],))["new"])
    assert new["moved"]["links"] == 1


def test_merge_gives_the_survivor_a_missing_due_date_and_never_other_fields(conn):
    mk(conn, 1, name="X", due="2026-11-05", hard=1, effort="L", context="dup notes"); mk(conn, 2, name="Y")
    _merge(conn, 1, 2)
    s = one(conn, "SELECT name, due, hard, due_is_datetime, effort, context FROM tasks WHERE id = 2")
    assert (s["name"], s["due"], s["hard"], s["effort"], s["context"]) == ("Y", "2026-11-05", 1, None, None)
    mk(conn, 3, due="2026-11-20"); mk(conn, 4, due="2026-11-09", hard=1)
    _merge(conn, 4, 3)
    s = one(conn, "SELECT due, hard FROM tasks WHERE id = 3")
    assert (s["due"], s["hard"]) == ("2026-11-20", 0)


def test_merge_skips_a_move_that_breaks_a_rule(conn):
    for i in (1, 2, 10, 11, 12):
        mk(conn, i)
    link(conn, 1, 10, "part_of"); link(conn, 2, 11, "part_of")
    link(conn, 1, 12, "waits_on"); link(conn, 12, 2, "waits_on")
    res = _merge(conn, 1, 2)
    whys = [s["why"] for s in res["skipped"]]
    assert any("Task-1's link to Task-10 was not moved" in w and "is already part of Task-11" in w for w in whys)
    assert any("Task-1's link to Task-12 was not moved" in w and "loop" in w for w in whys)
    assert {(1, 10, "part_of"), (1, 12, "waits_on"), (2, 11, "part_of"), (12, 2, "waits_on"), (1, 2, "same_as")} == active_rels(conn)
    assert one(conn, "SELECT status FROM tasks WHERE id = 1")["status"] == "dropped"


def test_merge_moves_steps_to_a_survivor_and_drops_a_link_between_the_two(conn):
    for i in (1, 2, 3, 4):
        mk(conn, i)
    link(conn, 3, 1, "part_of"); link(conn, 4, 1, "part_of"); link(conn, 2, 1, "waits_on")
    res = _merge(conn, 1, 2)
    assert res["skipped"] == []
    assert active_rels(conn) == {(3, 2, "part_of"), (4, 2, "part_of"), (1, 2, "same_as")}


def test_merge_only_removes_a_link_the_survivor_already_has(conn):
    for i in (1, 2, 3):
        mk(conn, i)
    link(conn, 3, 1, "waits_on"); link(conn, 3, 2, "waits_on")
    res = _merge(conn, 1, 2)
    assert active_rels(conn) == {(3, 2, "waits_on"), (1, 2, "same_as")}
    new = json.loads(one(conn, "SELECT new FROM changes WHERE batch_id = ? AND task_id = 2 AND field = 'link'", (res["batch_id"],))["new"])
    assert new["moved"]["tasks"] == 0


def test_merge_writes_no_mention(conn):
    mk(conn, 1, mentions=3); mk(conn, 2, mentions=5)
    db.add_mention(conn, 1, "plaud", "of_1"); conn.commit()
    before = rows(conn, "SELECT id, task_id FROM mentions"), rows(conn, "SELECT id, mentions, last_mention_at FROM tasks ORDER BY id")
    _merge(conn, 1, 2)
    assert (rows(conn, "SELECT id, task_id FROM mentions"), rows(conn, "SELECT id, mentions, last_mention_at FROM tasks ORDER BY id")) == before


def test_undo_of_a_merge_restores_everything(conn):
    _setup_first(conn)
    conn.execute("UPDATE tasks SET due = '2026-11-07', hard = 1 WHERE id = 1"); conn.commit()
    rels_before = active_rels(conn)
    res = _merge(conn, 1, 2)
    assert one(conn, "SELECT due FROM tasks WHERE id = 2")["due"] == "2026-11-07"
    out = batches.undo(conn, res["batch_id"])
    assert out["skipped"] == []
    d = one(conn, "SELECT status, closed_by, completed_at FROM tasks WHERE id = 1")
    assert (d["status"], d["closed_by"], d["completed_at"]) == ("open", None, None)
    assert {r["task_id"] for r in rows(conn, "SELECT task_id FROM links")} == {1}
    assert one(conn, "SELECT task_id FROM reminders")["task_id"] == 1
    assert active_rels(conn) == rels_before
    s = one(conn, "SELECT due, hard FROM tasks WHERE id = 2")
    assert (s["due"], s["hard"]) == (None, 0)


def test_undo_of_a_merge_leaves_a_link_changed_since(conn):
    mk(conn, 1); mk(conn, 2); mk(conn, 3)
    db.add_link(conn, 1, "mail_thread", "c1"); conn.commit()
    res = _merge(conn, 1, 2)
    conn.execute("UPDATE links SET task_id = 3"); conn.commit()       # moved again by something later
    out = batches.undo(conn, res["batch_id"])
    assert [s["why"] for s in out["skipped"]] == ["Task-2: a link or reminder moved by this merge was changed since; left as it is"]
    assert one(conn, "SELECT task_id FROM links")["task_id"] == 3


def test_merge_refuses_a_closed_duplicate_and_a_merged_target(conn):
    for i in (1, 2, 5, 6):
        mk(conn, i)
    batches.apply(conn, [{"op": "done", "task_id": 6}], "ui", TODAY)
    res = _merge(conn, 6, 2)
    assert res["applied"] == 0 and "only an open task can be merged away" in res["skipped"][0]["why"]
    _merge(conn, 1, 2)
    res = _merge(conn, 5, 1)
    assert res["applied"] == 0 and res["skipped"][0]["why"] == "Task-1 was itself merged into Task-2; merge into Task-2"
    res = _merge(conn, 5, 5)
    assert res["skipped"][0]["why"] == "a task cannot be merged into itself"
    res = batches.apply(conn, [{"op": "merge", "task_id": 5, "fields": {}}], "ui", TODAY)
    assert res["skipped"][0]["why"] == "merge needs task_id and fields.into, two task ids"
    assert one(conn, "SELECT status FROM tasks WHERE id = 5")["status"] == "open"


def test_default_survivor_is_the_canvas_row_else_the_older(conn):
    mk(conn, 1, source="Plaud", created_at="2026-09-01T00:00:00+00:00"); mk(conn, 2, source="Canvas", created_at="2026-10-01T00:00:00+00:00")
    assert relations.default_survivor(conn, 1, 2) == (2, 1)
    assert relations.default_survivor(conn, 2, 1) == (2, 1)
    mk(conn, 3, created_at="2026-10-05T00:00:00+00:00"); mk(conn, 4, created_at="2026-10-02T00:00:00+00:00")
    assert relations.default_survivor(conn, 3, 4) == (4, 3)
    mk(conn, 5); mk(conn, 6)
    assert relations.default_survivor(conn, 6, 5) == (5, 6)


# --- Task 6: redirects

REC = {"id": "of_aa", "name": "Lab sync", "start_utc": "2026-11-02T17:00:00Z"}


def test_a_recording_synced_again_mentions_the_survivor_and_copies_nothing(conn, cfg):
    upsert_task(conn, task(id=1, name="Email Max the slides", source="Plaud", source_key="plaud:of_aa:1", mentions=1)); mk(conn, 2, name="Send Max the deck", due="2026-11-07")
    batches.apply(conn, [{"op": "merge", "task_id": 1, "fields": {"into": 2}}], "ui", TODAY)
    before = one(conn, "SELECT mentions FROM tasks WHERE id = 2")["mentions"]
    data = {"tasks": [{"op": "create", "name": "Email Max the new slides", "due": "2026-12-01", "source_key": "plaud:of_aa:1"}],
            "instructions": [], "reminders": [{"task_ref": "tasks[0]", "fire_at": "2026-11-04T09:00", "label": "slides"}], "calendar": [], "unresolved": []}
    res = sync_file.apply_result(conn, cfg, {"id": "of_aa", "name": "Lab sync", "start_utc": "2026-11-02T17:00:00Z"}, data, TODAY)
    s = one(conn, "SELECT name, due, mentions FROM tasks WHERE id = 2"); d = one(conn, "SELECT status, name FROM tasks WHERE id = 1")
    assert (s["name"], s["due"], s["mentions"]) == ("Send Max the deck", "2026-11-07", before + 1)
    assert (d["status"], d["name"]) == ("dropped", "Email Max the slides")
    assert one(conn, "SELECT 1 AS x FROM links WHERE task_id = 2 AND kind = 'plaud_note' AND ref = 'of_aa'")
    assert one(conn, "SELECT task_id FROM reminders WHERE label = 'slides'")["task_id"] == 2
    assert 2 in res["task_ids"] and not rows(conn, "SELECT 1 FROM changes WHERE task_id = 2 AND field IN ('name', 'due')")


def test_an_instruction_naming_the_duplicate_acts_on_the_survivor(conn, cfg):
    mk(conn, 1); mk(conn, 2)
    _merge(conn, 1, 2)
    res = sync_file.apply_result(conn, cfg, REC, {"tasks": [], "instructions": [{"op": "done", "task_id": 1, "evidence": "it is done"}],
                                                  "reminders": [{"task_id": 1, "fire_at": "2026-11-04T09:00", "label": "check"}], "calendar": [], "unresolved": []},
                                 TODAY, allowed_ids={2})                   # the prompt lists the survivor; the model named the duplicate
    assert res["unresolved"] == [] and res["applied"] == 1
    assert one(conn, "SELECT status FROM tasks WHERE id = 2")["status"] == "done"
    d = one(conn, "SELECT status, closed_by FROM tasks WHERE id = 1")
    assert (d["status"], d["closed_by"]) == ("dropped", "merge")
    assert one(conn, "SELECT task_id FROM reminders WHERE label = 'check'")["task_id"] == 2
    assert not rows(conn, "SELECT 1 FROM mentions WHERE task_id = 1")


def _canvas_item(**kw):
    it = {"key": "canvas:assignment:9", "type": "assignment", "name": "Submit Assignment 3", "title": "Assignment 3", "project": "COMPE 510",
          "area": "Academics", "context": "", "due": "2026-11-05T23:59", "effort": "M", "state": "open", "dismissed": False, "url": "", "points": None}
    it.update(kw)
    return it


def test_a_canvas_close_and_due_move_for_a_merged_item_reach_the_survivor(conn, cfg):
    mk(conn, 1, name="Submit Assignment 3", source="Canvas", source_key="canvas:assignment:9", due="2026-11-05T23:59", hard=1)
    mk(conn, 2, name="Assignment 3 from the lab meeting", due="2026-11-06")
    _merge(conn, 1, 2)                                                     # the owner kept the non-Canvas row
    canvas.sweep(conn, cfg, [_canvas_item(due="2026-11-09T23:59")], TODAY)
    assert one(conn, "SELECT due FROM tasks WHERE id = 2")["due"] == "2026-11-09T23:59"
    assert canvas.sweep(conn, cfg, [_canvas_item(due="2026-11-09T23:59")], TODAY) is None     # the survivor's due is what is compared
    canvas.sweep(conn, cfg, [_canvas_item(due="2026-11-09T23:59", state="submitted")], TODAY)
    s = one(conn, "SELECT status, closed_by FROM tasks WHERE id = 2"); d = one(conn, "SELECT status, closed_by, due FROM tasks WHERE id = 1")
    assert (s["status"], s["closed_by"]) == ("done", "canvas")
    assert (d["status"], d["closed_by"], d["due"]) == ("dropped", "merge", "2026-11-05T23:59")


def test_survivor_follows_a_chain_and_stops_on_a_repeat(conn):
    for i in (1, 2, 3, 4):
        mk(conn, i)
    _merge(conn, 1, 2); _merge(conn, 2, 3)
    assert (relations.survivor(conn, 1), relations.survivor(conn, 2), relations.survivor(conn, 3), relations.survivor(conn, 4)) == (3, 3, 3, 4)
    relations.add(conn, 3, 1, "same_as", None, "test", None)              # a loop in old data must not spin
    assert relations.survivor(conn, 1) == 3
