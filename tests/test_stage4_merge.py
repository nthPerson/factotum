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


# --- Task 7: suggestions and migration 1

def _sugg(conn, eid):
    return one(conn, "SELECT judged_at, verdict FROM evidence WHERE id = ?", (eid,))


def test_suggest_stores_evidence_and_never_repeats_a_judged_pair(conn):
    mk(conn, 1); mk(conn, 2); mk(conn, 3)
    e = relations.suggest(conn, "same_as", 1, 2, "same number 3", "twin")
    ev = one(conn, "SELECT * FROM evidence WHERE id = ?", (e,))
    assert ev["kind"] == "link_suggestion" and ev["task_id"] == 1 and json.loads(ev["ref"]) == {"kind": "same_as", "a": 1, "b": 2, "why": "same number 3", "signal": "twin"}
    assert ev["label"] == "same as Task-2?" and ev["excerpt"] == "same number 3"
    assert relations.suggest(conn, "same_as", 2, 1, "again", "text") is None                     # pending, unordered
    batches.apply(conn, [{"task_id": 1, "op": "judge_evidence", "fields": {"evidence_id": e, "verdict": "dismissed"}}], "review", TODAY)
    assert relations.pair_judged(conn, "same_as", 2, 1) and relations.suggest(conn, "same_as", 2, 1, "x", "thread") is None
    assert relations.suggest(conn, "waits_on", 1, 3, "x", "text") and relations.suggest(conn, "waits_on", 3, 1, "x", "text")   # directed: both orders are different pairs
    assert relations.suggest(conn, "waits_on", 3, 1, "x", "text") is None
    # a dismissed "any" for (2, 3) blocks waits_on (3, 2) and same_as (2, 3)
    a = relations.suggest(conn, "any", 2, 3, "same thread", "thread")
    assert one(conn, "SELECT label FROM evidence WHERE id = ?", (a,))["label"] == "linked to Task-3?"
    batches.apply(conn, [{"task_id": 2, "op": "judge_evidence", "fields": {"evidence_id": a, "verdict": "dismissed"}}], "review", TODAY)
    assert relations.pair_judged(conn, "waits_on", 3, 2) and relations.pair_judged(conn, "same_as", 2, 3)
    assert relations.suggest(conn, "waits_on", 3, 2, "x", "text") is None and relations.suggest(conn, "same_as", 2, 3, "x", "twin") is None
    assert not relations.pair_judged(conn, "waits_on", 1, 3)
    # unknown kind, the same task twice, a missing task, a pair already joined: nothing written
    mk(conn, 4); mk(conn, 5); link(conn, 4, 5, "part_of")
    n = one(conn, "SELECT COUNT(*) AS n FROM evidence")["n"]
    assert [relations.suggest(conn, *args) for args in (("blocks", 4, 5, "x", "t"), ("same_as", 4, 4, "x", "t"), ("same_as", 4, 99, "x", "t"), ("waits_on", 5, 4, "x", "t"))] == [None] * 4
    assert one(conn, "SELECT COUNT(*) AS n FROM evidence")["n"] == n


def test_pending_suggestions_list_both_ends(conn):
    mk(conn, 1); mk(conn, 2); mk(conn, 3)
    e1 = relations.suggest(conn, "same_as", 1, 2, "twin", "twin"); e2 = relations.suggest(conn, "waits_on", 3, 1, "after", "text")
    p = relations.pending_suggestions(conn)
    assert p[1] == [{"evidence_id": e1, "kind": "same_as", "other": 2, "keep": 2, "why": "twin", "signal": "twin"},
                    {"evidence_id": e2, "kind": "waits_on", "other": 3, "keep": None, "why": "after", "signal": "text"}]
    assert [s["other"] for s in p[2]] == [1] and [s["other"] for s in p[3]] == [1]
    assert set(relations.pending_suggestions(conn, {2})) == {2}


def test_accepting_a_suggestion_is_one_batch_and_undo_reopens_it(conn):
    mk(conn, 1); mk(conn, 2)
    e = relations.suggest(conn, "same_as", 1, 2, "same number 3", "twin"); conn.commit()
    res = batches.apply(conn, [{"op": "merge", "task_id": 1, "fields": {"into": 2}},
                               {"task_id": 1, "op": "judge_evidence", "fields": {"evidence_id": e, "verdict": "applied"}}], "review", TODAY)
    assert res["skipped"] == [] and _sugg(conn, e)["verdict"] == "applied"
    batches.undo(conn, res["batch_id"])
    s = _sugg(conn, e)
    assert (s["judged_at"], s["verdict"]) == (None, None)
    assert one(conn, "SELECT status FROM tasks WHERE id = 1")["status"] == "open" and not any(k == "same_as" for *_, k in active_rels(conn))


def test_making_the_link_judges_a_pending_suggestion_for_the_pair(conn):
    mk(conn, 1); mk(conn, 2); mk(conn, 3)
    e = relations.suggest(conn, "any", 1, 2, "same thread", "thread"); other = relations.suggest(conn, "any", 1, 3, "same thread", "thread"); conn.commit()
    res = link(conn, 1, 2, "waits_on")
    assert _sugg(conn, e)["verdict"] == "applied" and _sugg(conn, other)["verdict"] is None
    assert one(conn, "SELECT COUNT(*) AS n FROM changes WHERE batch_id = ? AND field = 'evidence'", (res["batch_id"],))["n"] == 1
    batches.undo(conn, res["batch_id"])
    s = _sugg(conn, e)
    assert (s["judged_at"], s["verdict"]) == (None, None) and active_rels(conn) == set()


def test_migration_1_turns_import_task_links_into_same_as_or_suggestions(conn, tmp_path):
    mk(conn, 10, status="dropped", completed_at="2026-10-01"); mk(conn, 11)
    mk(conn, 12, created_at="2026-10-20T00:00:00+00:00"); mk(conn, 13, created_at="2026-09-01T00:00:00+00:00")
    for loser, ref in ((10, "11"), (12, "13"), (12, "not-a-number")):
        conn.execute("INSERT INTO links (task_id, kind, ref, label, created_at) VALUES (?, 'task', ?, 'duplicate source key in import', 'now')", (loser, ref))
    conn.execute("PRAGMA user_version = 0"); conn.commit()
    assert db.migrate(conn) == ["import task links become same_as links or merge suggestions"]
    assert active_rels(conn) == {(10, 11, "same_as")}
    assert one(conn, "SELECT source FROM relations WHERE src = 10")["source"] == "import"
    sg = rows(conn, "SELECT ref, judged_at FROM evidence WHERE kind = 'link_suggestion'")
    assert len(sg) == 1 and sg[0]["judged_at"] is None
    assert json.loads(sg[0]["ref"]) == {"kind": "same_as", "a": 12, "b": 13, "why": "duplicate source key in import", "signal": "import"}
    assert not rows(conn, "SELECT 1 FROM links WHERE kind = 'task'")
    assert len(list((tmp_path / "backups").glob("*-before-v1.db"))) == 1
    assert db.schema_version(conn) == 1 and db.migrate(conn) == []


def test_the_importer_writes_relations_not_task_links(conn, cfg, tmp_path):
    from factotum.importers.notion_tasks import run
    from test_core import _notion_row
    upsert_task(conn, task(id=2, name="already here", source_key="gcal:abc")); conn.commit()
    f = tmp_path / "pages.json"
    f.write_text(json.dumps([_notion_row(5, "re-created by the old sync", "Next", "gcal:abc", created="2026-10-25T00:00:00.000Z")]))   # recent: imported open
    run(conn, cfg, apply=True, from_json=str(f), new_only=True, today=TODAY, out=lambda *_: None)
    assert one(conn, "SELECT status FROM tasks WHERE id = 5")["status"] == "open"
    assert not rows(conn, "SELECT 1 FROM links WHERE kind = 'task'")
    sg = rows(conn, "SELECT ref FROM evidence WHERE kind = 'link_suggestion' AND judged_at IS NULL")
    assert len(sg) == 1 and {json.loads(sg[0]["ref"])[k] for k in ("a", "b")} == {2, 5}
    assert active_rels(conn) == set()


def test_the_planner_and_review_cards_do_not_read_link_suggestions(conn, cfg, tmp_path):
    from fastapi.testclient import TestClient
    from factotum import api, planner
    mk(conn, 1); mk(conn, 2)
    relations.suggest(conn, "any", 1, 2, "same thread", "thread")
    conn.execute("INSERT INTO evidence (task_id, kind, label, at) VALUES (1, 'thread_updated', 'Max wrote', 'now')"); conn.commit()
    assert [c["kind"] for c in planner.bundle(conn, cfg, TODAY)["candidates"]] == ["thread_updated"]
    app = api.create_app(cfg, db_path=str(tmp_path / "t.db"))
    items = {i["task"]["id"]: i for i in TestClient(app).get("/api/review").json()["items"]}
    assert [e["kind"] for e in items[1]["evidence"]] == ["thread_updated"]
