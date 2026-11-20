"""Spoken Review, stage 1: the tray on the server (factotum/tray.py, design §5.3). One tray for every device: what
is staged, in the order shown and applied, which item each row decides, and Apply as one batch whose
transaction also empties the rows it applied. Every write starts with db.begin_write."""
import json
import sqlite3
import threading
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from stage4_helpers import mk
from test_polish import _card as _kind_card
from test_review_decide import _reply

from factotum import batches, config as C, db, review_decide, review_queue, tray
from factotum.api import create_app
from factotum.db import connect, get_settings, init_db, now_iso

DAY = date(2026, 11, 19)


def keep(tid, **kw):
    return dict({"task_id": tid, "op": "keep", "fields": {}, "label": "kept"}, **kw)


def put(key, entry, decides=None):
    op = {"put": key, "entry": entry}
    if decides is not None:
        op["decides"] = decides
    return op


def keys(c):
    return [k for k, _, _ in tray.read(c)["entries"]]


def _app(cfg, tmp_path, name="a.db"):
    client = TestClient(create_app(cfg, db_path=str(tmp_path / name)))
    return client, client.app.state.conn


# --- db.begin_write --------------------------------------------------------------------------------------

def test_begin_write_takes_the_lock_and_leaves_an_open_transaction_alone(tmp_path):
    a = connect(tmp_path / "w.db")
    init_db(a)
    b = connect(tmp_path / "w.db")
    b.execute("PRAGMA busy_timeout = 50")
    db.begin_write(a)
    assert a.in_transaction
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        b.execute("BEGIN IMMEDIATE")                          # the write lock is a's, before a writes anything
    a.rollback()
    a.execute("INSERT INTO settings (key, value_json) VALUES ('x', '1')")      # an implicit transaction already open
    db.begin_write(a)                                         # left alone: no "cannot start a transaction within a transaction"
    a.commit()
    assert b.execute("SELECT value_json FROM settings WHERE key = 'x'").fetchone()[0] == "1"


# --- write, read, the revision -----------------------------------------------------------------------------

def test_a_key_staged_again_keeps_its_place_and_a_new_key_goes_last(conn):
    for i in (1, 2, 3):
        mk(conn, i)
    tray.write(conn, [put("1", keep(1)), put("2", keep(2)), put("3:due", {"task_id": 3, "op": "set", "fields": {"due": "2026-12-01"}, "label": "due"})], "page")
    tray.write(conn, [put("1", {"task_id": 1, "op": "done", "fields": {}, "label": "done"}), put("e:9", {"task_id": 2, "op": "judge_evidence",
                       "fields": {"evidence_id": 9, "verdict": "reviewed"}, "label": "thread seen"})], "page")
    conn.commit()
    t = tray.read(conn)
    assert [k for k, _, _ in t["entries"]] == ["1", "2", "3:due", "e:9"]
    assert t["entries"][0][1]["op"] == "done"
    tray.write(conn, [{"del": "2"}], "page")
    tray.write(conn, [put("2", keep(2))], "page")
    assert keys(conn) == ["1", "3:due", "e:9", "2"]


def test_every_write_bumps_the_revision_once_and_an_identical_put_does_not(conn):
    mk(conn, 1)
    mk(conn, 2)
    assert tray.rev(conn) == 0
    changed = tray.write(conn, [put("1", keep(1)), put("2", keep(2))], "page", device="Phone")
    assert changed == ["1", "2"] and tray.rev(conn) == 1
    assert tray.write(conn, [put("1", keep(1))], "page") == [] and tray.rev(conn) == 1        # the same entry: nothing changes
    assert tray.write(conn, [put("2", keep(2, label="kept!"))], "page") == ["2"] and tray.rev(conn) == 2
    assert tray.write(conn, [{"del": "1"}, {"del": "nothing"}], "page") == ["1"] and tray.rev(conn) == 3
    assert tray.write(conn, [{"del": "nothing"}], "page") == [] and tray.rev(conn) == 3
    conn.commit()
    t = tray.read(conn)
    assert t["rev"] == 3 and t["entries"] == [["2", keep(2, label="kept!"), {"decides": "t:2", "origin": "page", "device": None, "rev": 2,
                                                                           "updated_at": t["entries"][0][2]["updated_at"]}]]
    assert tray.read(conn, since=3) == {"rev": 3, "same": True}
    assert tray.read(conn, since=2)["entries"] == t["entries"]


def test_a_bad_entry_refuses_the_whole_call_and_writes_nothing(conn):
    mk(conn, 1)
    bad = [
        put("1", {"task_id": 2, "op": "keep", "fields": {}}),                 # a digit key names its own task
        put("1", {"task_id": 1, "op": "explode"}),                          # an op batches does not know
        put("1", {"task_id": True, "op": "keep"}),                           # True is not Task-1
        put("1", {"task_id": "1", "op": "keep"}),
        put("new:x", {"task_id": "s1", "op": "link", "fields": {}}),          # a ref is m<digits>-<digits>
        put("1", dict(keep(1), local=True)),                                 # a provisional page entry is never sent
        put("1 2", keep(1)),                                                 # a key outside KEY_RE
        put("1", ["keep"]),
        put("1", dict(keep(1), fields="x")),
        put("1", dict(keep(1), label="x" * 20000)),                          # over MAX_ENTRY_BYTES
        {"put": "1"},
        {"nothing": 1},
    ]
    for b in bad:
        with pytest.raises(ValueError):
            tray.write(conn, [put("1:due", {"task_id": 1, "op": "set", "fields": {"due": None}}), b], "page")
        assert tray.rev(conn) == 0
        conn.rollback()
        assert keys(conn) == []
    with pytest.raises(ValueError):
        tray.write(conn, [{"del": "1"}] * (tray.MAX_OPS + 1), "page")


def test_the_api_only_keys_are_stripped(conn):
    mk(conn, 1)
    e = {"task_id": 1, "op": "keep", "fields": {}, "label": "kept", "source_key": "x", "created_source": "Chat", "source_link": "http://x",
         "account": "a", "reminder_status": {"id": 1}, "decides": "t:9", "whatever": 1, "ev": 5, "fsug": 6, "prop": "1-0",
         "expect": {"name": "x"}, "note": "n"}
    tray.write(conn, [put("1", e)], "page")
    assert tray.read(conn)["entries"][0][1] == {"task_id": 1, "op": "keep", "fields": {}, "expect": {"name": "x"}, "note": "n",
                                                 "label": "kept", "ev": 5, "fsug": 6, "prop": "1-0"}


def test_decides_of():
    assert tray.decides_of("412", keep(412)) == "t:412"
    for op, f in (("done", {}), ("drop", {"outcome": "x"}), ("reopen", {}), ("set", {"snoozed_until": "2026-11-20", "last_reviewed_at": "2026-11-19"})):
        assert tray.decides_of("412", {"task_id": 412, "op": op, "fields": f}) == "t:412"
    assert tray.decides_of("412", {"task_id": 412, "op": "pin_top", "fields": {}}) is None          # an old page's pin under the bare id
    assert tray.decides_of("412", {"task_id": 412, "op": "set", "fields": {"top_rank": 2}}) is None
    assert tray.decides_of("412", {"task_id": 412, "op": "longterm", "fields": {}}) is None
    assert tray.decides_of("e:186", {"task_id": None, "op": "create_suggestion", "fields": {"evidence_id": 186}}) == "s:186"
    assert tray.decides_of("e:186", {"task_id": None, "op": "judge_evidence", "fields": {"evidence_id": 186, "verdict": "dismissed"}}) == "s:186"
    assert tray.decides_of("e:190", {"task_id": 412, "op": "judge_evidence", "fields": {"evidence_id": 190, "verdict": "dismissed"}}) is None
    assert tray.decides_of("e:190", {"task_id": 412, "op": "judge_evidence", "fields": {"evidence_id": 190, "verdict": "reviewed"}}) is None
    assert tray.decides_of("412:due", {"task_id": 412, "op": "set", "fields": {"due": None}}) is None
    assert tray.decides_of("412:top", {"task_id": 412, "op": "pin_top", "fields": {}}) is None
    assert tray.decides_of("e:190", {"task_id": 412, "op": "judge_evidence", "fields": {"evidence_id": 190, "verdict": "reviewed"}}, "t:412") == "t:412"


def test_a_pin_staged_on_today_does_not_decide_the_task_on_review(conn):
    mk(conn, 1)
    tray.write(conn, [put("1:top", {"task_id": 1, "op": "pin_top", "fields": {}, "label": "pinned"}),
                      put("1:lt", {"task_id": 1, "op": "set", "fields": {"longterm": 1}, "label": "long-term"}),
                      put("1", {"task_id": 1, "op": "pin_top", "fields": {}, "label": "pinned"})], "page")     # an old page's bare-key pin too
    assert tray.decided(conn) == {}
    tray.write(conn, [put("1", keep(1))], "page")
    assert tray.decided(conn) == {"t:1": ["1"]}


def test_summary_counts_decisions_beside_and_who(conn):
    for i in (1, 2, 3, 4):
        mk(conn, i)
    tray.write(conn, [put("1", keep(1)), put("2", {"task_id": 2, "op": "done", "fields": {}}),
                      put("3", {"task_id": 3, "op": "set", "fields": {"snoozed_until": "2026-11-20", "last_reviewed_at": "2026-11-19"}}),
                      put("e:7", {"task_id": 1, "op": "judge_evidence", "fields": {"evidence_id": 7, "verdict": "reviewed"}}, "t:1"),
                      put("e:8", {"task_id": None, "op": "create_suggestion", "fields": {"evidence_id": 8}}),
                      put("e:9", {"task_id": None, "op": "judge_evidence", "fields": {"evidence_id": 9, "verdict": "dismissed"}})], "page")
    tray.write(conn, [put("4:due", {"task_id": 4, "op": "set", "fields": {"due": "2026-12-01"}})], "connector")
    assert tray.summary(conn) == {"entries": 7, "decisions": {"keep": 1, "done": 1, "drop": 0, "later": 1, "create": 1, "dismiss": 1},
                                  "beside": 1, "by": {"page": 6, "connector": 1}, "decided_by": {"page": 5}}


# --- the carry ---------------------------------------------------------------------------------------------

def test_a_carry_puts_what_is_new_keeps_out_what_another_device_staged_and_fixes_old_entries(conn):
    for i in (1, 2, 3, 9):
        mk(conn, i)
    tray.write(conn, [put("2", keep(2))], "page", device="Phone")
    old = [[1, {"op": "keep", "fields": {}, "label": "kept"}],                         # saved before entries carried task_id
           ["1:due", {"op": "set", "fields": {"due": "2026-12-01"}, "label": "due"}],
           ["e:5", {"task_id": 1, "op": "judge_evidence", "fields": {"evidence_id": 5, "verdict": "reviewed"}, "label": "thread seen"}],
           ["e:6", {"task_id": 3, "op": "judge_evidence", "fields": {"evidence_id": 6, "verdict": "reviewed"}, "label": "thread seen"}],
           ["rel:3:waits_on:9", {"task_id": 3, "op": "link", "fields": {"kind": "waits_on", "other": 9}, "label": "waits on Task-9"}],
           [2, {"task_id": 2, "op": "done", "fields": {}, "label": "done"}],                  # staged on the phone too: the phone's is kept
           ["3", {"task_id": 3, "op": "explode"}],                                           # refused, with the reason
           ["junk"]]
    out = tray.carry(conn, "c-1", old, "Chrome on Windows")
    conn.commit()
    assert out["carried"] == 5 and out["skipped"] == 0
    assert [k["key"] for k in out["kept_out"]] == ["2", "3", "junk"]
    assert "another device" in out["kept_out"][0]["why"]
    t = {k: (e, m) for k, e, m in tray.read(conn)["entries"]}
    assert t["1"][0]["task_id"] == 1 and t["1:due"][0]["task_id"] == 1 and t["2"][0]["op"] == "keep"
    assert t["1"][1]["origin"] == "carry" and t["1"][1]["device"] == "Chrome on Windows"
    assert t["e:5"][1]["decides"] == "t:1"                       # a verdict beside a carried decision of its task belongs to it
    assert t["e:6"][1]["decides"] is None                        # Task-3's decision did not come with it
    assert tray.decided(conn) == {"t:2": ["2"], "t:1": ["1", "e:5"]}


def test_a_carried_old_pin_or_longterm_moves_beside_and_decides_nothing(conn):
    for i in (1, 2, 3, 4):
        mk(conn, i)
    old = [[1, {"task_id": 1, "op": "pin_top", "fields": {}, "label": "pinned"}],
           [2, {"task_id": 2, "op": "longterm", "fields": {}, "label": "long-term"}],
           [3, {"task_id": 3, "op": "set", "fields": {"top_rank": 2}, "label": "Top 2"}],
           [4, {"task_id": 4, "op": "set", "fields": {"longterm": 0}, "label": "not long-term"}]]
    assert tray.carry(conn, "c-1", old, None)["carried"] == 4
    assert keys(conn) == ["1:top", "2:lt", "3:top", "4:lt"]
    assert tray.decided(conn) == {}


def test_a_second_carry_of_the_same_entries_under_a_new_id_puts_nothing(conn, cfg):
    mk(conn, 1)
    old = [[1, {"task_id": 1, "op": "keep", "fields": {}, "label": "kept"}]]
    assert tray.carry(conn, "c-1", old, None)["carried"] == 1
    conn.commit()
    tray.apply(conn, cfg, DAY)                                   # applied in between: the key is free again
    assert keys(conn) == []
    out = tray.carry(conn, "c-2", old, None)                     # an old tab wrote factotum.staged again
    assert out == {"carried": 0, "kept_out": [], "skipped": 1} and keys(conn) == []


# --- apply ---------------------------------------------------------------------------------------------------

def _chat_create(conn, name="Book the bench"):
    mid = _reply(conn, "chat", None, [{"op": "create", "task_id": None, "fields": {"name": name}, "label": f"new: {name}", "evidence": [], "applied": False}])
    return f"new:c{mid}-0", {"task_id": None, "op": "create", "fields": {"name": name}, "label": f"new task: {name}", "from_chat": {"message_id": mid, "index": 0}}


def test_apply_writes_one_batch_creates_first_and_empties_those_keys(conn, cfg):
    mk(conn, 1, context="Draft")
    mk(conn, 2)
    nk, ne = _chat_create(conn)
    tray.write(conn, [put("1", keep(1)), put("1:note", {"task_id": 1, "op": "add_note", "fields": {"text": "Max agreed"}}, "t:1"),
                      put(nk, ne), put("2:due", {"task_id": 2, "op": "set", "fields": {"due": "2026-12-01"}})], "page")
    conn.commit()
    res = tray.apply(conn, cfg, DAY, keys=["1", "1:note", nk])
    assert res["batch_id"] and len(res["created"]) == 1 and res["skipped"] == [] and res["gone"] == []
    new = res["created"][0]
    assert [r[0] for r in conn.execute("SELECT DISTINCT batch_id FROM changes")] == [res["batch_id"]]
    first = conn.execute("SELECT task_id FROM changes WHERE batch_id = ? ORDER BY id LIMIT 1", (res["batch_id"],)).fetchone()[0]
    assert first == new                                          # the create first
    assert conn.execute("SELECT context FROM tasks WHERE id = 1").fetchone()[0] == "Draft\n2026-11-19: Max agreed"
    assert conn.execute("SELECT source_key FROM tasks WHERE id = ?", (new,)).fetchone()[0] == f"chat:{ne['from_chat']['message_id']}:0"
    assert keys(conn) == ["2:due"] and [k for k, _, _ in res["tray"]["entries"]] == ["2:due"]
    props = json.loads(conn.execute("SELECT proposals_json FROM chat_messages WHERE id = ?", (ne["from_chat"]["message_id"],)).fetchone()[0])
    assert props[0]["applied"] is True and props[0]["task_id"] == new
    assert not conn.in_transaction


def test_apply_with_a_changed_entry_is_stale_and_writes_nothing(conn, cfg):
    mk(conn, 1)
    mk(conn, 2)
    tray.write(conn, [put("1", keep(1))], "page")
    conn.commit()
    seen = tray.rev(conn)
    tray.write(conn, [put("2", keep(2))], "connector")             # staged elsewhere after the page last read the tray
    conn.commit()
    with pytest.raises(tray.Stale) as ex:
        tray.apply(conn, cfg, DAY, seen_rev=seen)
    assert ex.value.changed == ["2"]
    assert not conn.in_transaction and keys(conn) == ["1", "2"]
    assert conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 0
    res = tray.apply(conn, cfg, DAY, keys=["1"], seen_rev=seen)    # what the page saw is still applicable alone
    assert res["applied"] == 1 and keys(conn) == ["2"]


def test_two_applies_on_two_connections_apply_each_row_once(cfg, tmp_path):
    path = tmp_path / "two.db"
    a = connect(path)
    init_db(a)
    mk(a, 1, context="Draft")
    nk, ne = _chat_create(a)
    tray.write(a, [put("1:note", {"task_id": 1, "op": "add_note", "fields": {"text": "Max agreed"}}), put(nk, ne)], "page")
    a.commit()
    b = connect(path)
    gate = threading.Barrier(2)
    out, errors = {}, []

    def run(name, c):
        try:
            gate.wait()
            out[name] = tray.apply(c, cfg, DAY, keys=["1:note", nk])
        except Exception as e:                                   # noqa: BLE001
            errors.append(repr(e))

    ts = [threading.Thread(target=run, args=("a", a)), threading.Thread(target=run, args=("b", b))]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert errors == []
    assert a.execute("SELECT context FROM tasks WHERE id = 1").fetchone()[0] == "Draft\n2026-11-19: Max agreed"      # one note
    assert a.execute("SELECT COUNT(*) FROM tasks WHERE source_key LIKE 'chat:%'").fetchone()[0] == 1                 # one task
    won, lost = sorted(out.values(), key=lambda r: r["batch_id"] is None)
    assert won["batch_id"] and len(won["created"]) == 1 and won["gone"] == []
    assert lost["batch_id"] is None and lost["gone"] == ["1:note", nk] and lost["applied"] == 0
    assert keys(a) == []


def test_a_failed_batch_leaves_the_tray_as_it_was(conn, cfg, monkeypatch):
    mk(conn, 1)
    tray.write(conn, [put("1", keep(1))], "page")
    conn.commit()
    before = tray.read(conn)

    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(batches, "_apply_one", boom)
    with pytest.raises(RuntimeError):
        tray.apply(conn, cfg, DAY)
    assert not conn.in_transaction and tray.read(conn) == before
    assert conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 0


def test_an_apply_whose_changes_were_all_skipped_still_empties_those_keys_and_says_why(conn, cfg):
    tray.write(conn, [put("999", keep(999))], "page")                # no such task (gone since it was staged)
    conn.commit()
    res = tray.apply(conn, cfg, DAY)
    assert res["batch_id"] is None and res["applied"] == 0
    assert res["skipped"] == [{"task_id": 999, "why": "no such task"}]
    assert keys(conn) == [] and res["tray"]["entries"] == []


def test_a_staged_create_of_a_card_answered_since_is_skipped_with_its_reason(conn, cfg):
    eid = _kind_card(conn, "planner")
    review_decide.stage(conn, cfg, f"s:{eid}", "create", None, DAY, origin="page")
    conn.commit()
    batches.apply(conn, [{"op": "judge_evidence", "task_id": None, "fields": {"evidence_id": eid, "verdict": "dismissed"}}], "ui", DAY)   # on another device
    res = tray.apply(conn, cfg, DAY)
    assert res["created"] == [] and res["cards"] == []
    assert res["skipped"] == [{"task_id": None, "why": f"suggestion {eid}: this suggestion was already judged"}]
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    assert conn.execute("SELECT verdict FROM evidence WHERE id = ?", (eid,)).fetchone()[0] == "dismissed"      # judged once, by the other device
    assert keys(conn) == []


def _four(c):
    mk(c, 2, name="Register for the visit")
    return [_kind_card(c, k, other=2) for k in ("planner", "mail", "slack", "ripple")]


def test_a_staged_card_create_is_the_routes_batch(cfg, tmp_path):
    _, a = _app(cfg, tmp_path, "a.db")
    client_b, b = _app(cfg, tmp_path, "b.db")
    ids_a, ids_b = _four(a), _four(b)
    assert ids_a == ids_b
    ecfg = C.effective(cfg, get_settings(a))
    day = review_queue.today(ecfg)
    for eid in ids_a:
        review_decide.stage(a, ecfg, f"s:{eid}", "create", None, day, origin="page")
    a.commit()
    res = tray.apply(a, ecfg, day)
    for eid in ids_b:
        assert client_b.post(f"/api/review/suggestions/{eid}/create", json={}).status_code == 200
    q = "SELECT task_id, field, old, new FROM changes ORDER BY id"
    assert [tuple(r) for r in a.execute(q)] == [tuple(r) for r in b.execute(q)]
    q = "SELECT source, source_key, source_link, account, name, project, due, context FROM tasks WHERE id != 2 ORDER BY id"
    assert [tuple(r) for r in a.execute(q)] == [tuple(r) for r in b.execute(q)] and len(a.execute(q).fetchall()) == 4
    q = "SELECT task_id, kind, ref, label, account FROM links ORDER BY task_id, kind, ref"
    assert [tuple(r) for r in a.execute(q)] == [tuple(r) for r in b.execute(q)]               # the Slack card's message link too
    made = {r[0]: r[1] for r in a.execute("SELECT source_key, id FROM tasks WHERE id != 2")}
    assert [c["evidence_id"] for c in res["cards"]] == ids_a
    assert [c["task_id"] for c in res["cards"]] == res["created"] == sorted(made.values()) and not any(c["already"] for c in res["cards"])
    assert keys(a) == []


def test_two_cards_created_in_one_apply_get_their_own_threads_and_links(conn, cfg):
    mk(conn, 2, name="Register for the visit")
    mk(conn, 3, name="Plan the trip")
    e1 = _kind_card(conn, "mail", other=2)
    ref = {"op": "create", "fields": {"name": "Book the room"}, "links": [{"kind": "part_of", "other": 3}],
           "mail": {"ref": "conv-two", "account": "me@example.edu", "subject": "Room", "sender": "Ann <ann@example.edu>", "at": "2026-11-02T09:00:00Z"}}
    e2 = conn.execute("INSERT INTO evidence (task_id, kind, ref, account, label, excerpt, at) VALUES (NULL, 'proposal', ?, 'me@example.edu', 'create: Book the room', 'r', ?)",
                      (json.dumps(ref), now_iso())).lastrowid
    conn.commit()
    for e in (e1, e2):
        review_decide.stage(conn, cfg, f"s:{e}", "create", None, DAY, origin="page")
    conn.commit()
    res = tray.apply(conn, cfg, DAY)
    assert len(res["created"]) == 2 and res["skipped"] == []
    t1, t2 = (c["task_id"] for c in res["cards"])
    assert {r["task_id"]: r["ref"] for r in conn.execute("SELECT task_id, ref FROM links WHERE kind = 'mail_thread'")} == {t1: "conv-ask", t2: "conv-two"}
    rels = {(r[0], r[1], r[2]) for r in conn.execute("SELECT src, dst, kind FROM relations WHERE removed_at IS NULL")}
    assert rels == {(t1, 2, "waits_on"), (t2, 3, "part_of")}


def test_create_after_undo_reopens_the_same_task_in_the_trays_batch(conn, cfg):
    eid = _kind_card(conn, "planner")
    review_decide.stage(conn, cfg, f"s:{eid}", "create", None, DAY, origin="page")
    conn.commit()
    first = tray.apply(conn, cfg, DAY)
    tid = first["cards"][0]["task_id"]
    batches.undo(conn, first["batch_id"])                         # the card is back
    assert conn.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()[0] == "dropped"
    review_decide.stage(conn, cfg, f"s:{eid}", "create", {"draft": {"fields": {"name": "Go to the dentist on Friday"}}}, DAY, origin="page")
    conn.commit()
    again = tray.apply(conn, cfg, DAY)
    assert again["cards"] == [{"evidence_id": eid, "task_id": tid, "reopened": True, "already": False}]
    t = conn.execute("SELECT status, name FROM tasks WHERE id = ?", (tid,)).fetchone()
    assert tuple(t) == ("open", "Go to the dentist on Friday")
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1


def test_finish_marks_the_day_reviewed_unless_only_editor_edits_were_applied(conn, cfg):
    C.set_(cfg, "owner.timezone", "Pacific/Kiritimati")
    day = review_queue.today(cfg)
    assert day == datetime.now(ZoneInfo("Pacific/Kiritimati")).date()
    mk(conn, 1)
    mk(conn, 2)
    tray.write(conn, [put("1:edit-name", {"task_id": 1, "op": "set", "fields": {"name": "Renamed"}, "expect": {"name": "Task 1"}}), put("2", keep(2))], "page")
    conn.commit()
    res = tray.apply(conn, cfg, day, keys=["1:edit-name"], finish=True, whole_tray=False)
    assert res["applied"] == 1 and res["finished"] is False and "state.last_review_at" not in get_settings(conn)
    res = tray.apply(conn, cfg, day, keys=["2"], finish=True, whole_tray=False)
    assert res["finished"] is True and get_settings(conn)["state.last_review_at"] == day.isoformat()
    assert tray.finishes([("1:x", {"op": "set"}, {"decides": None})], False) is False
    assert tray.finishes([("e:1", {"op": "judge_evidence"}, {"decides": None})], False) is True
    assert tray.finishes([("1:x", {"op": "set"}, {"decides": None})], True) is True


def test_the_batch_cause_says_who_staged(conn, cfg):
    for i in (1, 2, 3):
        mk(conn, i)
    tray.write(conn, [put("1", keep(1)), put("2", keep(2))], "page")
    tray.write(conn, [put("3", keep(3))], "connector")
    conn.commit()
    res = tray.apply(conn, cfg, DAY, cause={"applied_by": "page"})
    cause = json.loads(conn.execute("SELECT cause_json FROM batches WHERE id = ?", (res["batch_id"],)).fetchone()[0])
    assert cause == {"applied_by": "page", "staged_by": {"page": 2, "connector": 1}}
    src = conn.execute("SELECT source FROM batches WHERE id = ?", (res["batch_id"],)).fetchone()[0]
    assert src == "review"
