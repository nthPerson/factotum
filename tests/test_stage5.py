"""Stage 5, ripples (Part A)."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import factotum.claude.runner as runner_mod
from conftest import FIXTURE, TODAY
from stage4_helpers import link, mk
from stage5_helpers import entry, fake_runner, no_claude, on, queue  # noqa: F401  (no_claude is autouse: no test reaches a real run)

from factotum import batches, relations, ripple
from factotum import config as C
from factotum.api import create_app
from factotum.claude import runner
from factotum.claude.runner import run as real_run
from factotum.db import get_settings, get_task, log_run, now_iso
from factotum.scheduler import Scheduler

# --- Task 1: the ripple keys and the runner's per-run model and budget
RIPPLE_DEFAULTS = {"claude.ripple.enabled": False, "claude.ripple.model": "sonnet", "claude.ripple.delay_seconds": 60,
                   "claude.ripple.hourly_cap": 10, "claude.budgets_usd.ripple": 0.30}


def test_ripple_keys_are_settings_keys_and_ripples_are_off_when_missing():
    for k, v in RIPPLE_DEFAULTS.items():
        assert k in C.SETTINGS_KEYS and C.DEFAULTS[k] == v, k
    assert C.value({}, "claude.ripple.enabled") is False and "claude.ripple.enabled" not in C.PAID_SWITCHES


def test_the_example_the_fixture_and_the_starter_carry_the_ripple_keys():
    for path in (C.EXAMPLE_CONFIG, FIXTURE, C.STARTER_CONFIG):
        c = C.load(path)
        for k, v in RIPPLE_DEFAULTS.items():
            assert C.get(c, k, "absent") == v, (path.name, k)


def test_the_runner_takes_a_model_and_a_budget_for_one_run(cfg):
    cmd = runner.command(cfg, "ripple", [], tools="", model="haiku", budget_usd=0.25)
    assert cmd[cmd.index("--model") + 1] == "haiku" and cmd[cmd.index("--max-budget-usd") + 1] == "0.25"
    plain = runner.command(cfg, "sync_file", [])
    assert plain[plain.index("--model") + 1] == C.get(cfg, "claude.models.sync_file")
    seen = []
    real_run(cfg, "ripple", "p", tools="", model="opus", budget_usd=0.5,
             exec_=lambda cmd, **kw: seen.append(cmd) or type("R", (), {"stdout": "{}", "stderr": "", "returncode": 0})())
    assert seen[0][seen[0].index("--model") + 1] == "opus" and seen[0][seen[0].index("--max-budget-usd") + 1] == "0.5"


# --- Task 2: the queue
def test_closing_a_linked_task_queues_it_and_an_unlinked_one_does_not(conn):
    mk(conn, 1); mk(conn, 2); mk(conn, 3); link(conn, 2, 1, "waits_on")
    res = batches.apply(conn, [{"op": "done", "task_id": 1}, {"op": "done", "task_id": 3}], "ui", TODAY)
    assert res["ripple_queued"] == [1] and queue(conn)[1]["what"] == ["closed"] and 3 not in queue(conn)


@pytest.mark.parametrize("fields, what", [({"name": "New"}, ["name"]), ({"due": "2026-11-20"}, ["due"]), ({"hard": 1, "due": "2026-11-20"}, ["due", "hard"]),
                                          ({"outcome": "It worked"}, ["outcome"]), ({"context": "A rewritten plan"}, ["text"])])
def test_a_material_change_queues(conn, fields, what):
    mk(conn, 1, context="The plan"); mk(conn, 2); link(conn, 1, 2, "part_of")
    batches.apply(conn, [{"op": "set", "task_id": 1, "fields": fields}], "ui", TODAY)
    assert queue(conn)[1]["what"] == what


def test_whitespace_why_now_effort_fade_and_reopen_do_not_queue(conn):
    mk(conn, 1, context="The  plan"); mk(conn, 2); link(conn, 1, 2, "waits_on")
    batches.apply(conn, [{"op": "set", "task_id": 1, "fields": {"context": "The plan\n", "why_now": "x", "effort": "S"}}], "ui", TODAY)
    batches.apply(conn, [{"op": "fade", "task_id": 1}], "automation", TODAY)
    batches.apply(conn, [{"op": "reopen", "task_id": 1}], "ui", TODAY)
    assert queue(conn) == {}


def test_a_ripple_batch_never_queues(conn):
    mk(conn, 1); mk(conn, 2); link(conn, 2, 1, "waits_on")
    res = batches.apply(conn, [{"op": "set", "task_id": 2, "fields": {"context": "new"}}], "ripple", TODAY, cause={"kind": "ripple", "from": [1]})
    assert res["ripple_queued"] == [] and queue(conn) == {}


def test_two_edits_in_the_quiet_period_are_one_row(conn):
    mk(conn, 1, context="A"); mk(conn, 2); link(conn, 2, 1, "waits_on")
    b1 = batches.apply(conn, [{"op": "set", "task_id": 1, "fields": {"context": "B"}}], "ui", TODAY)["batch_id"]
    b2 = batches.apply(conn, [{"op": "done", "task_id": 1}], "ui", TODAY)["batch_id"]
    q = queue(conn)[1]
    assert q["batches"] == [b1, b2] and q["last_batch_id"] == b2 and q["what"] == ["closed", "text"] and q["now"] == 0


def test_an_outcome_runs_at_once_and_wakes_the_scheduler(conn):
    mk(conn, 1); mk(conn, 2); link(conn, 2, 1, "waits_on")
    woke = []
    ripple.set_waker(lambda: woke.append(1))
    try:
        batches.apply(conn, [{"op": "done", "task_id": 1}], "ui", TODAY)
        assert woke == [] and queue(conn)[1]["now"] == 0
        batches.apply(conn, [{"op": "set", "task_id": 1, "fields": {"outcome": "Chair can serve remotely"}}], "ui", TODAY)
        assert woke == [1] and queue(conn)[1]["now"] == 1 and queue(conn)[1]["what"] == ["closed", "outcome"]
    finally:
        ripple.set_waker(None)


def test_undo_takes_its_batch_out_of_the_queue(conn):
    mk(conn, 1); mk(conn, 2); mk(conn, 3); link(conn, 2, 1, "waits_on"); link(conn, 3, 2, "waits_on")
    b1 = batches.apply(conn, [{"op": "done", "task_id": 1}, {"op": "set", "task_id": 2, "fields": {"name": "Two"}}], "ui", TODAY)["batch_id"]
    b2 = batches.apply(conn, [{"op": "set", "task_id": 2, "fields": {"due": "2026-11-20"}}], "ui", TODAY)["batch_id"]
    batches.undo(conn, b1)
    assert set(queue(conn)) == {2} and queue(conn)[2]["batches"] == [b2] and queue(conn)[2]["what"] == ["due"]
    batches.undo(conn, b2)
    assert queue(conn) == {}


def test_a_merge_queues_the_survivor_not_the_duplicate(conn):
    mk(conn, 1); mk(conn, 2); mk(conn, 3); link(conn, 3, 1, "waits_on")
    batches.apply(conn, [{"op": "merge", "task_id": 1, "fields": {"into": 2}}], "ui", TODAY)
    assert set(queue(conn)) == {2} and queue(conn)[2]["what"] == ["merge"]


# --- Task 6: the scheduler
def _age(conn, tid, seconds):
    """Make a queue row look `seconds` old."""
    at = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).replace(microsecond=0).isoformat()
    conn.execute("UPDATE ripple_queue SET queued_at = ? WHERE task_id = ?", (at, tid))
    conn.commit()


def test_ripples_off_run_nothing_and_empty_the_queue(conn, cfg):
    mk(conn, 1); mk(conn, 2); link(conn, 2, 1, "waits_on"); batches.apply(conn, [{"op": "done", "task_id": 1}], "ui", TODAY)
    Scheduler(cfg, conn, push=lambda *a, **k: None, ripple=ripple).tick(datetime(2026, 11, 1, 12, 0))      # the fixture has ripples off; no_claude would raise
    assert queue(conn) == {}


def test_the_quiet_period_then_one_run_per_group(conn, cfg):
    for i in range(1, 7):
        mk(conn, i)
    link(conn, 2, 1, "waits_on"); link(conn, 3, 2, "waits_on"); link(conn, 5, 4, "waits_on"); link(conn, 6, 5, "waits_on")
    batches.apply(conn, [{"op": "done", "task_id": 1}], "ui", TODAY); batches.apply(conn, [{"op": "set", "task_id": 3, "fields": {"name": "N"}}], "ui", TODAY)
    batches.apply(conn, [{"op": "set", "task_id": 4, "fields": {"name": "M"}}], "ui", TODAY)
    for t in (1, 3, 4):
        _age(conn, t, 30)
    assert ripple.next_group(conn, on(cfg)) == []
    _age(conn, 1, 90); _age(conn, 3, 61); _age(conn, 4, 70)
    assert ripple.next_group(conn, on(cfg)) == [1, 3]                    # 1 and 3 share Task-2; 4 is another group


def test_an_outcome_runs_at_once(conn, cfg):
    mk(conn, 1); mk(conn, 2); link(conn, 2, 1, "waits_on")
    batches.apply(conn, [{"op": "done", "task_id": 1, "fields": {"outcome": "ok"}}], "ui", TODAY)
    assert ripple.next_group(conn, on(cfg)) == [1]


def test_the_hourly_cap_holds_the_queue(conn, cfg):
    mk(conn, 1); mk(conn, 2); link(conn, 2, 1, "waits_on"); batches.apply(conn, [{"op": "done", "task_id": 1}], "ui", TODAY); _age(conn, 1, 120)
    for _ in range(10):
        log_run(conn, "ripple", 1, ok=True, cost_usd=0.1)
    conn.commit()
    assert ripple.next_group(conn, on(cfg)) == [] and 1 in queue(conn)


def test_a_failed_run_backs_off_an_hour(conn, cfg, monkeypatch):
    calls = []
    monkeypatch.setattr(runner_mod, "run", fake_runner(None, calls=calls))
    mk(conn, 1); mk(conn, 2); link(conn, 2, 1, "waits_on"); batches.apply(conn, [{"op": "done", "task_id": 1}], "ui", TODAY); _age(conn, 1, 120)
    s = Scheduler(on(cfg), conn, push=lambda *a, **k: None, ripple=ripple)
    s.tick(datetime(2026, 11, 1, 12, 0)); s.tick(datetime(2026, 11, 1, 12, 10))
    assert len(calls) == 1 and get_settings(conn)["state.last_fail.ripples"] == "2026-11-01T12:00"


def test_a_failure_before_the_run_rolls_back_and_frees_the_write_lock(conn, cfg, monkeypatch):
    mk(conn, 1); mk(conn, 2); link(conn, 2, 1, "waits_on"); batches.apply(conn, [{"op": "done", "task_id": 1}], "ui", TODAY)

    def broken(c, now_utc=None):
        c.execute("DELETE FROM ripple_queue")
        raise RuntimeError("disk")
    monkeypatch.setattr(ripple, "drop_stale", broken)
    s = Scheduler(on(cfg), conn, push=lambda *a, **k: None, ripple=ripple)
    with pytest.raises(RuntimeError):
        s._ripples(on(cfg), datetime(2026, 11, 1, 12, 0), {}, [])
    assert not conn.in_transaction and 1 in queue(conn)
    monkeypatch.setattr(ripple, "clear", lambda c: (c.execute("DELETE FROM ripple_queue"), 1 / 0))
    with pytest.raises(ZeroDivisionError):
        s._ripples(cfg, datetime(2026, 11, 1, 12, 0), {}, [])
    assert not conn.in_transaction and 1 in queue(conn)


def test_a_tick_runs_the_due_group(conn, cfg, monkeypatch):
    monkeypatch.setattr(runner_mod, "run", fake_runner({"ripples": [entry(2, context="Now free to go.")]}))
    mk(conn, 1); mk(conn, 2); link(conn, 2, 1, "waits_on"); batches.apply(conn, [{"op": "done", "task_id": 1}], "ui", TODAY); _age(conn, 1, 120)
    did = Scheduler(on(cfg), conn, push=lambda *a, **k: None, ripple=ripple).tick(datetime(2026, 11, 1, 12, 0))
    assert any(d.startswith("ripples: Ripple from Task-1") for d in did) and get_task(conn, 2)["context"] == "Now free to go." and queue(conn) == {}


def test_start_sets_the_waker_and_stop_clears_it(conn, cfg, monkeypatch):
    monkeypatch.setattr(Scheduler, "tick", lambda self, now_local=None: [])        # the thread must not tick on the test's connection
    s = Scheduler(cfg, conn, push=lambda *a, **k: None, ripple=ripple)
    s.start(interval_s=3600)
    try:
        assert ripple._WAKER == s.wake
    finally:
        s.stop()
    assert ripple._WAKER is None


def test_the_api_says_ripples_wait_for_a_scheduler(cfg, tmp_path):
    client = TestClient(create_app(cfg, db_path=str(tmp_path / "a.db")))
    c = client.app.state.conn
    mk(c, 1); mk(c, 2); link(c, 2, 1, "waits_on")
    client.put("/api/settings", json={"values": {"claude.ripple.enabled": True}})
    res = client.post("/api/batches/apply", json={"source": "ui", "changes": [{"op": "done", "task_id": 1}]}).json()
    assert res["ripple_queued"] == [1]
    st = client.get("/api/ripple").json()
    assert st["enabled"] and not st["scheduler"] and st["waiting_for_scheduler"] and st["pending"][0]["task_id"] == 1 and st["landed"] == []
    rb = batches.apply(c, [{"op": "set", "task_id": 2, "fields": {"context": "free"}}], "ripple", TODAY, cause={"kind": "ripple", "from": [1], "run": 0, "batches": [res["batch_id"]]})
    landed = client.get("/api/ripple", params={"after": res["batch_id"]}).json()["landed"]
    assert landed == [{"batch_id": rb["batch_id"], "at": landed[0]["at"], "from": [1], "tasks": [{"id": 2, "name": "Task 2"}], "undone": False}]


# --- Task 7: undo follows the cause
def _rippled(conn, cfg):
    """Tasks 2 and 3 wait on 1; `b` closes 1 and `r` is the ripple it caused. Returns (b, r)."""
    mk(conn, 1); mk(conn, 2, context="Old two"); mk(conn, 3, context="Old three")
    link(conn, 2, 1, "waits_on"); link(conn, 3, 1, "waits_on")
    b = batches.apply(conn, [{"op": "done", "task_id": 1}], "ui", TODAY)["batch_id"]
    r = batches.apply(conn, [{"op": "set", "task_id": 2, "fields": {"context": "New two"}},
                             {"op": "set", "task_id": 3, "fields": {"context": "New three"}}],
                      "ripple", TODAY, cause={"kind": "ripple", "from": [1], "run": 0, "batches": [b]})["batch_id"]
    return b, r


def test_undoing_the_cause_undoes_its_ripple_when_the_text_is_in_place(conn, cfg):
    b, r = _rippled(conn, cfg)
    res = batches.undo(conn, b)
    assert res["ripples_undone"] == [r] and res["ripples_kept"] == []
    assert get_task(conn, 1)["status"] == "open" and get_task(conn, 2)["context"] == "Old two" and get_task(conn, 3)["context"] == "Old three"
    assert conn.execute("SELECT undone_at FROM batches WHERE id = ?", (r,)).fetchone()[0]


def test_undoing_the_cause_after_a_hand_edit_keeps_the_ripple_and_flags_it(conn, cfg):
    b, r = _rippled(conn, cfg)
    batches.apply(conn, [{"op": "set", "task_id": 2, "fields": {"context": "Mine now"}}], "ui", TODAY)
    res = batches.undo(conn, b)
    assert res["ripples_undone"] == [] and res["ripples_kept"] == [r]
    assert get_task(conn, 2)["context"] == "Mine now" and get_task(conn, 3)["context"] == "New three"
    cause = json.loads(conn.execute("SELECT cause_json, undone_at FROM batches WHERE id = ?", (r,)).fetchone()[0])
    assert cause["cause_undone_batch"] == b and cause["cause_undone_at"]
    alone = batches.undo(conn, r)
    assert get_task(conn, 3)["context"] == "Old three" and get_task(conn, 2)["context"] == "Mine now"
    assert alone["skipped"] == [{"task_id": 2, "why": "Task-2 context was changed after this batch; left as it is"}]


def test_undo_reverts_the_ripple_before_the_cause_so_a_field_both_wrote_comes_back(conn, cfg):
    mk(conn, 1); mk(conn, 2, context="Old two"); link(conn, 2, 1, "waits_on")
    b = batches.apply(conn, [{"op": "done", "task_id": 1}, {"op": "set", "task_id": 2, "fields": {"context": "Cause two"}}], "ui", TODAY)["batch_id"]
    r = batches.apply(conn, [{"op": "set", "task_id": 2, "fields": {"context": "Ripple two"}}], "ripple", TODAY,
                      cause={"kind": "ripple", "from": [1], "run": 0, "batches": [b]})["batch_id"]
    res = batches.undo(conn, b)
    assert res["ripples_undone"] == [r] and res["skipped"] == [] and get_task(conn, 2)["context"] == "Old two" and get_task(conn, 1)["status"] == "open"


def test_a_ripple_batch_undoes_on_its_own_and_leaves_its_cause(conn, cfg):
    b, r = _rippled(conn, cfg)
    assert batches.undo(conn, r)["restored"] == 2 and get_task(conn, 1)["status"] == "done"
    assert batches.undo(conn, b)["ripples_undone"] == []


def test_undoing_a_batch_that_caused_nothing_says_so(conn):
    mk(conn, 1); b = batches.apply(conn, [{"op": "done", "task_id": 1}], "ui", TODAY)["batch_id"]
    res = batches.undo(conn, b)
    assert res["ripples_undone"] == [] and res["ripples_kept"] == [] and batches.undo(conn, b)["ripples_undone"] == []


def test_the_changed_marker_goes_with_an_undone_ripple(conn, cfg):
    b, r = _rippled(conn, cfg)
    assert relations.annotate(conn, [{"id": 2}])[0]["rel"]["changed"]["batch_id"] == r
    batches.undo(conn, b)
    assert relations.annotate(conn, [{"id": 2}])[0]["rel"]["changed"] is None


# --- Task 8: the outcome after a reminder's Done
def _reminded(cfg, tmp_path, linked=True):
    from test_core import _rem
    app = create_app(cfg, db_path=str(tmp_path / "r.db"))
    c = app.state.conn
    mk(c, 1, name="Ask the chair")
    if linked:
        mk(c, 2); link(c, 2, 1, "waits_on")
    batches.apply(c, [{"task_id": 1, "op": "add_reminder", "fields": {"fire_at": "2026-11-03T09:00"}}], "ui", TODAY)
    from factotum import reminders
    reminders.fire(c, cfg, datetime(2026, 11, 3, 9, 0, 30), lambda *a, **k: "m1")
    tok = [r["token"] for r in _rem(c, 1) if r["status"] == "sent"][0]
    return TestClient(app), c, tok


def test_the_page_after_done_asks_how_it_turned_out_for_a_linked_task(cfg, tmp_path):
    client, c, tok = _reminded(cfg, tmp_path)
    r = client.get(f"/r/{tok}", params={"a": "done"})
    assert r.status_code == 200 and "How did it turn out?" in r.text and f'/r/{tok}/outcome"' in r.text and 'method="post"' in r.text
    assert "One task hangs on this one." in r.text and "kept with this task" in r.text      # ripples are off in the fixture


def test_an_unlinked_task_is_not_asked(cfg, tmp_path):
    client, c, tok = _reminded(cfg, tmp_path, linked=False)
    assert "How did it turn out?" not in client.get(f"/r/{tok}", params={"a": "done"}).text


def test_saving_the_outcome_goes_through_the_write_path_and_ripples_at_once(cfg, tmp_path):
    client, c, tok = _reminded(cfg, tmp_path)
    client.get(f"/r/{tok}", params={"a": "done"})
    r = client.post(f"/r/{tok}/outcome", data={"outcome": "  The chair can serve remotely "})
    assert r.status_code == 200 and "outcome saved" in r.text and "The chair can serve remotely" in r.text
    assert get_task(c, 1)["outcome"] == "The chair can serve remotely"
    assert c.execute("SELECT source FROM batches ORDER BY id DESC LIMIT 1").fetchone()[0] == "ntfy" and queue(c)[1]["now"] == 1


def test_an_outcome_needs_a_token_closed_from_this_reminder(cfg, tmp_path):
    client, c, tok = _reminded(cfg, tmp_path)
    assert client.post(f"/r/{tok}/outcome", data={"outcome": "x"}).status_code == 409          # not done from here yet
    assert client.post("/r/nope/outcome", data={"outcome": "x"}).status_code == 404
    client.get(f"/r/{tok}", params={"a": "done"})
    n = c.execute("SELECT COUNT(*) FROM batches").fetchone()[0]
    assert "nothing saved" in client.post(f"/r/{tok}/outcome", data={"outcome": "   "}).text and c.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == n
    c.execute("UPDATE reminders SET sent_at = '2026-01-01T00:00:00+00:00'"); c.commit()
    assert client.post(f"/r/{tok}/outcome", data={"outcome": "x"}).status_code == 410


# --- Task 9: Review says what the linked task did and what a ripple suggests
def _ripple_on_two(client) -> int:
    c = client.app.state.conn
    mk(c, 1, name="Ask the chair"); mk(c, 2, context="Old"); link(c, 2, 1, "waits_on")
    client.post("/api/batches/apply", json={"source": "ui", "changes": [{"op": "done", "task_id": 1},
                                                                         {"op": "set", "task_id": 1, "fields": {"outcome": "The chair can serve remotely"}}]})
    b = ripple.bundle(c, C.load(FIXTURE), [1], TODAY)
    out = ripple.apply(c, {}, b, ripple.parse({"ripples": [entry(2, context="Free to send the form.", why="Task-1 settled it")]}, b), None, TODAY)
    return out["batch_id"]


def test_review_says_what_the_linked_task_did_and_what_the_ripple_wrote(cfg, tmp_path):
    client = TestClient(create_app(cfg, db_path=str(tmp_path / "a.db")))
    r = _ripple_on_two(client)
    item = next(i for i in client.get("/api/review").json()["items"] if i["task"]["id"] == 2)
    assert "linked_changed" in item["reasons"]
    s = item["since"]
    assert s["batch_id"] == r and not s["undone"] and not s["cause_undone"] and not s["noted"]
    assert s["by"] == [{"id": 1, "name": "Ask the chair", "status": "done", "outcome": "The chair can serve remotely",
                        "completed_at": date.today().isoformat(), "did": "done", "what": ["closed", "outcome"]}]
    assert s["text"] == {"before": "Old", "after": "Free to send the form.", "note": "Task-1 settled it"}
    client.get("/api/tasks/2")                                               # opening the task is looking at it
    assert not any(i["task"]["id"] == 2 and "linked_changed" in i["reasons"] for i in client.get("/api/review").json()["items"])


def test_a_ripple_proposal_is_its_own_review_reason(cfg, tmp_path):
    client = TestClient(create_app(cfg, db_path=str(tmp_path / "a.db")))
    c = client.app.state.conn
    mk(c, 5)
    c.execute("INSERT INTO evidence (task_id, kind, ref, account, label, excerpt, at) VALUES (5, 'proposal', ?, 'ripple', 'set due 2026-11-20 on Task-5', 'the form can go', ?)",
              (json.dumps({"task_id": 5, "op": "set", "fields": {"due": "2026-11-20"}}), now_iso())); c.commit()
    rv = client.get("/api/review").json()
    item = next(i for i in rv["items"] if i["task"]["id"] == 5)
    assert item["primary"] == "ripple" and "planner" not in item["reasons"] and item["evidence"][0]["by"] == "ripple"
    assert rv["labels"]["ripple"] == "Ripple suggests"


def test_a_ripple_create_card_makes_a_ripple_task(cfg, tmp_path):
    client = TestClient(create_app(cfg, db_path=str(tmp_path / "a.db")))
    c = client.app.state.conn
    c.execute("INSERT INTO evidence (task_id, kind, ref, account, label, excerpt, at) VALUES (NULL, 'proposal', ?, 'ripple', 'create: Book the bench', 'r', ?)",
              (json.dumps({"task_id": None, "op": "create", "fields": {"name": "Book the bench"}}), now_iso())); c.commit()
    eid = c.execute("SELECT id FROM evidence").fetchone()[0]
    tid = client.post(f"/api/review/suggestions/{eid}/create", json={}).json()["task_id"]
    assert get_task(c, tid)["source"] == "Ripple"


# --- Task 10: free now
def _free(conn, hard=1, due="2026-11-05"):
    mk(conn, 1, name="Get the data"); mk(conn, 2, name="Deliver the units", hard=hard, due=due); link(conn, 2, 1, "waits_on")


def test_a_hard_dated_task_freed_today_is_announced_once(conn, cfg):
    import copy
    from factotum import reminders
    c = copy.deepcopy(cfg); C.set_(c, "notify.ntfy.topic", "t"); C.set_(c, "notify.provider", "both")
    pushed, devices = [], []
    push = lambda cfg, title, body, **k: pushed.append((title, body, k.get("click"))) or "m1"      # noqa: E731
    dev = lambda conn, cfg, notice: devices.append(notice) or {"sent": 1}                         # noqa: E731
    _free(conn)
    assert reminders.free_now(conn, c, TODAY, push, dev) == [] and pushed == []
    batches.apply(conn, [{"op": "done", "task_id": 1}], "ui", TODAY)
    sent = reminders.free_now(conn, c, TODAY, push, dev)
    assert [s["task_id"] for s in sent] == [2] and pushed[0][0] == "Task-2 is free now" and pushed[0][2].endswith("/t/2")
    assert "Task-1 is done" in pushed[0][1] and devices[0]["path"] == "/t/2" and devices[0]["actions"] == [] and devices[0]["tag"] == "free-2"
    assert reminders.free_now(conn, c, TODAY, push, dev) == []


@pytest.mark.parametrize("hard, due", [(0, "2026-11-05"), (1, None)])
def test_a_task_without_a_hard_date_is_never_announced(conn, cfg, hard, due):
    import copy
    from factotum import reminders
    c = copy.deepcopy(cfg); C.set_(c, "notify.ntfy.topic", "t")
    pushed = []
    _free(conn, hard, due); reminders.free_now(conn, c, TODAY, lambda *a, **k: pushed.append(a) or "m")
    batches.apply(conn, [{"op": "done", "task_id": 1}], "ui", TODAY)
    assert reminders.free_now(conn, c, TODAY, lambda *a, **k: pushed.append(a) or "m") == [] and pushed == []


def test_still_waiting_or_freed_another_day_is_not_announced(conn, cfg):
    import copy
    from factotum import reminders
    c = copy.deepcopy(cfg); C.set_(c, "notify.ntfy.topic", "t")
    _free(conn); mk(conn, 3); link(conn, 2, 3, "waits_on")
    reminders.free_now(conn, c, TODAY, lambda *a, **k: "m")
    batches.apply(conn, [{"op": "done", "task_id": 1}], "ui", TODAY)
    assert reminders.free_now(conn, c, TODAY, lambda *a, **k: "m") == []
    batches.apply(conn, [{"op": "done", "task_id": 3}], "ui", TODAY)
    assert reminders.free_now(conn, c, TODAY + timedelta(days=1), lambda *a, **k: "m") == []


def test_a_notice_nobody_received_is_tried_again(conn, cfg):
    import copy
    from factotum import reminders
    c = copy.deepcopy(cfg); C.set_(c, "notify.ntfy.topic", "t")
    _free(conn); reminders.free_now(conn, c, TODAY, lambda *a, **k: "m")
    batches.apply(conn, [{"op": "done", "task_id": 1}], "ui", TODAY)
    assert reminders.free_now(conn, c, TODAY, lambda *a, **k: None) == []
    assert [s["task_id"] for s in reminders.free_now(conn, c, TODAY, lambda *a, **k: "m")] == [2]


def test_the_scheduler_logs_free_now_like_a_reminder(conn, cfg):
    import copy
    c = copy.deepcopy(cfg); C.set_(c, "notify.ntfy.topic", "t")
    _free(conn)
    s = Scheduler(c, conn, push=lambda *a, **k: "m", device_push=lambda *a, **k: {"sent": 0})
    s.tick(datetime(2026, 11, 1, 12, 0))
    batches.apply(conn, [{"op": "done", "task_id": 1}], "ui", TODAY)
    s.tick(datetime(2026, 11, 1, 12, 1))
    assert "free now: Task-2 Deliver the units" in [r["summary"] for r in conn.execute("SELECT summary FROM runs WHERE kind = 'reminder'")]


# --- Task 11: the outcome is asked only when something hangs on the task
def _reminded_with(cfg, tmp_path, links):
    """Task 1 with a sent reminder, tasks 2 and 3 open, and `links` as (src, dst, kind)."""
    from test_core import _rem
    from factotum import reminders
    app = create_app(cfg, db_path=str(tmp_path / "r.db"))
    c = app.state.conn
    mk(c, 1, name="Ask the chair"); mk(c, 2); mk(c, 3)
    for src, dst, kind in links:
        link(c, src, dst, kind)
    batches.apply(c, [{"task_id": 1, "op": "add_reminder", "fields": {"fire_at": "2026-11-03T09:00"}}], "ui", TODAY)
    reminders.fire(c, cfg, datetime(2026, 11, 3, 9, 0, 30), lambda *a, **k: "m1")
    tok = [r["token"] for r in _rem(c, 1) if r["status"] == "sent"][0]
    return TestClient(app), c, tok


def test_a_task_that_only_waits_on_another_is_not_asked(cfg, tmp_path):
    client, c, tok = _reminded_with(cfg, tmp_path, [(1, 2, "waits_on"), (3, 1, "part_of")])     # 1 waits on 2; 3 is a step of 1
    assert "How did it turn out?" not in client.get(f"/r/{tok}", params={"a": "done"}).text


def test_a_step_of_an_open_goal_is_asked(cfg, tmp_path):
    client, c, tok = _reminded_with(cfg, tmp_path, [(1, 2, "part_of")])
    assert "One task hangs on this one." in client.get(f"/r/{tok}", params={"a": "done"}).text


def test_hanging_on_counts_open_tasks_that_wait_on_it_and_its_open_goal(conn):
    from factotum import reminders
    for i in range(1, 6):
        mk(conn, i)
    link(conn, 2, 1, "waits_on"); link(conn, 3, 1, "waits_on")       # two wait on 1
    link(conn, 1, 4, "part_of")                                       # 1 is a step of 4
    link(conn, 1, 5, "waits_on")                                      # 1 waits on 5: 5 does not hang on 1
    assert reminders.hanging_on(conn, 1) == 3
    batches.apply(conn, [{"op": "done", "task_id": 3}, {"op": "drop", "task_id": 4}], "ui", TODAY)
    assert reminders.hanging_on(conn, 1) == 1
