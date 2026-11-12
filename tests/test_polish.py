"""The polish pass at the end of the linked-tasks branch (2026-11-11)."""
import json

import pytest
from fastapi.testclient import TestClient

from stage4_helpers import mk

from factotum.api import create_app
from factotum.db import now_iso


# --- E1: Create on a Review suggestion card is one undoable batch -------------------------------------

def _app(cfg, tmp_path):
    client = TestClient(create_app(cfg, db_path=str(tmp_path / "api.db")))
    return client, client.app.state.conn


def _card(c, kind, **kw):
    """One suggestion card of each kind, as the planner, the Slack sweep, the mail triage and a ripple write it."""
    if kind == "planner":
        ref = {"op": "create", "fields": {"name": "Go to the dentist", "project": "Personal", "effort": "S"}}
        row = (None, "proposal", json.dumps(ref), None, "create: Go to the dentist", "appointment mail Nov 12")
    elif kind == "ripple":
        ref = {"task_id": None, "op": "create", "fields": {"name": "Book the bench"}}
        row = (None, "proposal", json.dumps(ref), "ripple", "create: Book the bench", "the form can go")
    elif kind == "mail":
        ref = {"op": "create", "fields": {"name": "Send the abstract", "counterpart": "prof@example.edu"},
               "mail": {"ref": "conv-ask", "account": "me@example.edu", "subject": "Abstract for the workshop", "sender": "Prof <prof@example.edu>", "at": "2026-11-02T09:00:00Z"},
               "links": [{"kind": "waits_on", "other": kw["other"]}]}
        row = (None, "proposal", json.dumps(ref), "me@example.edu", "create: Send the abstract", "A direct request.")
    else:                                                    # a Slack mention naming no task
        row = (None, "slack_mention", "https://dice.slack.com/archives/C1/p1", "DiCE Lab", "#general · Max M: lunch tomorrow?", "Max M in #general")
    cur = c.execute("INSERT INTO evidence (task_id, kind, ref, account, label, excerpt, at) VALUES (?, ?, ?, ?, ?, ?, ?)", (*row, now_iso()))
    c.commit()
    return cur.lastrowid


EXPECT = {"planner": ("Planner", "planner-suggestion"), "ripple": ("Ripple", "ripple-suggestion"),
          "mail": ("Mail", "mail-suggestion"), "slack": ("Slack", "slack-mention")}


def _batches(c):
    return c.execute("SELECT COUNT(*) FROM batches").fetchone()[0]


@pytest.mark.parametrize("kind", ["planner", "ripple", "mail", "slack"])
def test_create_on_a_suggestion_card_is_one_undoable_batch(cfg, tmp_path, kind):
    client, c = _app(cfg, tmp_path)
    mk(c, 2, name="Register for the visit")
    eid = _card(c, kind, other=2)
    before = _batches(c)
    r = client.post(f"/api/review/suggestions/{eid}/create", json={}).json()
    tid, bid = r["task_id"], r["batch_id"]
    assert bid is not None and _batches(c) == before + 1 and r["skipped"] == [] and not r.get("reopened")
    t = c.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
    source, prefix = EXPECT[kind]
    assert (t["source"], t["source_key"], t["status"], t["mentions"]) == (source, f"{prefix}:{eid}", "open", 1)
    assert t["context"]                                                   # the card's reason or text when the fields carry none
    assert c.execute("SELECT verdict FROM evidence WHERE id = ?", (eid,)).fetchone()[0] == "applied"
    assert {row["batch_id"] for row in c.execute("SELECT batch_id FROM changes WHERE batch_id >= ?", (bid,))} == {bid}
    if kind == "mail":
        ln = c.execute("SELECT ref, last_seen_at, account FROM links WHERE task_id = ? AND kind = 'mail_thread'", (tid,)).fetchone()
        assert (ln["ref"], ln["last_seen_at"], ln["account"]) == ("conv-ask", "2026-11-02T09:00:00Z", "me@example.edu")
        assert t["account"] == "me@example.edu" and t["counterpart"] == "prof@example.edu"
        assert c.execute("SELECT 1 FROM relations WHERE src = ? AND dst = 2 AND kind = 'waits_on' AND removed_at IS NULL", (tid,)).fetchone()
    if kind == "slack":
        assert t["source_link"] == "https://dice.slack.com/archives/C1/p1" and t["account"] == "DiCE Lab"
        assert c.execute("SELECT 1 FROM links WHERE task_id = ? AND kind = 'slack_message'", (tid,)).fetchone()
    assert not [s for s in client.get("/api/review").json()["suggestions"] if s["id"] == eid]

    # a second tap is the same task and writes no second batch
    again = client.post(f"/api/review/suggestions/{eid}/create", json={}).json()
    assert again["task_id"] == tid and again["batch_id"] is None and _batches(c) == before + 1

    # Undo drops the row (never deletes it), removes its links and brings the card back
    client.post(f"/api/batches/{bid}/undo", json={})
    t = c.execute("SELECT status, closed_by FROM tasks WHERE id = ?", (tid,)).fetchone()
    assert (t["status"], t["closed_by"]) == ("dropped", "undo")
    assert not c.execute("SELECT 1 FROM relations WHERE (src = ? OR dst = ?) AND removed_at IS NULL", (tid, tid)).fetchone()
    assert not c.execute("SELECT 1 FROM links WHERE task_id = ? AND kind = 'mail_thread'", (tid,)).fetchone()
    assert c.execute("SELECT judged_at FROM evidence WHERE id = ?", (eid,)).fetchone()[0] is None
    assert [s["id"] for s in client.get("/api/review").json()["suggestions"] if s["id"] == eid] == [eid]

    # Create after Undo reopens the same task in one batch, judges the card again, and that batch undoes too
    re = client.post(f"/api/review/suggestions/{eid}/create", json={}).json()
    assert re["task_id"] == tid and re["reopened"] is True and re["batch_id"] not in (None, bid)
    assert c.execute("SELECT COUNT(*) FROM tasks WHERE source_key = ?", (f"{prefix}:{eid}",)).fetchone()[0] == 1
    assert c.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()[0] == "open"
    assert c.execute("SELECT verdict FROM evidence WHERE id = ?", (eid,)).fetchone()[0] == "applied"
    if kind == "mail":
        assert c.execute("SELECT 1 FROM links WHERE task_id = ? AND kind = 'mail_thread'", (tid,)).fetchone()
        assert c.execute("SELECT 1 FROM relations WHERE src = ? AND dst = 2 AND kind = 'waits_on' AND removed_at IS NULL", (tid,)).fetchone()
    client.post(f"/api/batches/{re['batch_id']}/undo", json={})
    assert c.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()[0] == "dropped"
    assert c.execute("SELECT judged_at FROM evidence WHERE id = ?", (eid,)).fetchone()[0] is None


def test_two_creates_at_once_make_one_task_and_one_batch(cfg, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    client, c = _app(cfg, tmp_path)
    eid = _card(c, "planner")
    before = _batches(c)
    with ThreadPoolExecutor(4) as ex:
        out = list(ex.map(lambda _: client.post(f"/api/review/suggestions/{eid}/create", json={}), range(4)))
    assert {r.status_code for r in out} == {200} and len({r.json()["task_id"] for r in out}) == 1
    assert _batches(c) == before + 1 and [r.json()["batch_id"] is not None for r in out].count(True) == 1


def test_the_page_cannot_choose_a_create_s_source_link_or_account(cfg, tmp_path):
    client, c = _app(cfg, tmp_path)
    r = client.post("/api/batches/apply", json={"source": "ui", "changes": [{"op": "create", "fields": {"name": "Mine"}, "source_link": "https://evil.example", "account": "x@y.z", "created_source": "Slack"}]}).json()
    t = c.execute("SELECT source, source_link, account FROM tasks WHERE id = ?", (r["created"][0],)).fetchone()
    assert (t["source"], t["source_link"], t["account"]) == ("Manual", None, None)


def test_a_card_s_new_task_still_goes_through_the_link_detectors(cfg, tmp_path):
    client, c = _app(cfg, tmp_path)
    mk(c, 2, name="Register for the visit")
    c.execute("INSERT INTO evidence (task_id, kind, ref, label, excerpt, at) VALUES (NULL, 'proposal', ?, 'create: Book the hotel', 'r', ?)",
              (json.dumps({"op": "create", "fields": {"name": "Book the hotel", "context": "after Task-2 is done"}}), now_iso())); c.commit()
    eid = c.execute("SELECT MAX(id) FROM evidence").fetchone()[0]
    tid = client.post(f"/api/review/suggestions/{eid}/create", json={}).json()["task_id"]
    assert c.execute("SELECT 1 FROM evidence WHERE kind = 'link_suggestion' AND task_id IN (?, 2)", (tid,)).fetchone()
