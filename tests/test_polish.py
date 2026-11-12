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


# --- E2: ripples and filing on by default; a machine where Claude cannot run ------------------------------

import copy  # noqa: E402
from datetime import date, datetime, timedelta, timezone  # noqa: E402

from stage4_helpers import TODAY, link  # noqa: E402

from factotum import batches, filing, ripple  # noqa: E402
from factotum import config as C  # noqa: E402
from factotum.claude import runner as R  # noqa: E402
from factotum.claude.runner import RunResult  # noqa: E402
from factotum.db import get_settings, set_setting  # noqa: E402
from factotum.scheduler import Scheduler  # noqa: E402

REAL_RUN = R.run                     # taken before any test patches it
SIGNED_OUT = RunResult(False, None, "Not logged in · Please run /login", 0.0, 50, error="auth: Not logged in · Please run /login", auth_failed=True)


def _missing_binary(cfg, kind, prompt, **kw):
    """What runner.run answers when the `claude` program is not there (its FileNotFoundError branch)."""
    return REAL_RUN(cfg, kind, prompt, exec_=lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError(2, "No such file", "claude")), **{k: v for k, v in kw.items() if k in ("allowed_tools", "tools", "workdir", "model", "budget_usd")})


def _failed_rows(conn, kind):
    return conn.execute("SELECT COUNT(*) FROM runs WHERE kind = ? AND ok = 0", (kind,)).fetchone()[0]


def _starter_like(cfg):
    """The fixture with nothing set for ripples and filing: what a new person on the starter gets."""
    c = copy.deepcopy(cfg)
    c["claude"].pop("ripple", None); c["claude"].pop("filing", None)
    assert C.value(c, "claude.ripple.enabled") is True and C.value(c, "claude.filing.enabled") is True
    return c


@pytest.mark.parametrize("answer", ["signed_out", "missing"])
def test_ripples_on_a_machine_where_claude_cannot_run_fail_once_a_day_and_set_their_rows_aside(conn, cfg, tmp_path, monkeypatch, answer):
    monkeypatch.setattr(ripple, "RUNS_DIR", tmp_path / "runs")
    calls = []

    def run(c, kind, prompt, **kw):
        calls.append(kind)
        return SIGNED_OUT if answer == "signed_out" else _missing_binary(c, kind, prompt, **kw)
    monkeypatch.setattr(R, "run", run)
    on = _starter_like(cfg)
    s = Scheduler(on, conn, push=lambda *a, **k: None, ripple=ripple)
    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(timespec="seconds")
    for n in range(3):                                                    # three separate groups, each due
        a, b = 10 * n + 1, 10 * n + 2
        mk(conn, a); mk(conn, b); link(conn, b, a, "waits_on")
        batches.apply(conn, [{"op": "done", "task_id": a}], "ui", TODAY)
    conn.execute("UPDATE ripple_queue SET queued_at = ?", (old,)); conn.commit()
    t0 = datetime.now().replace(second=0, microsecond=0)
    for i in range(6):                                                   # ticks a minute apart, then an hour later
        s.tick(t0 + timedelta(minutes=i))
    s.tick(t0 + timedelta(hours=1, minutes=1))
    s.tick(t0 + timedelta(hours=2, minutes=2))
    assert len(calls) <= 3                                               # no retry every tick: one try, then an hour's back-off
    assert _failed_rows(conn, "ripple") + _failed_rows(conn, "ripples") == 1      # one row in the Log for this cause today
    assert conn.execute("SELECT COUNT(*) FROM ripple_queue").fetchone()[0] == 3 - len(calls)   # each group tried is set aside, never retried
    state = get_settings(conn)[R.UNAVAILABLE_KEY]
    assert state["why"] == answer and state["day"] == date.today().isoformat()


@pytest.mark.parametrize("answer", ["signed_out", "missing"])
def test_filing_on_a_machine_where_claude_cannot_run_fails_once_a_day_and_drops_its_queue(conn, cfg, tmp_path, monkeypatch, answer):
    monkeypatch.setattr(filing, "RUNS_DIR", tmp_path / "runs")
    calls = []

    def run(c, kind, prompt, **kw):
        calls.append(kind)
        return SIGNED_OUT if answer == "signed_out" else _missing_binary(c, kind, prompt, **kw)
    monkeypatch.setattr(R, "run", run)
    on = _starter_like(cfg)
    s = Scheduler(on, conn, push=lambda *a, **k: None, mailer=None)
    now = datetime.now().replace(second=0, microsecond=0)
    for n in range(5):
        batches.apply(conn, [{"op": "create", "fields": {"name": f"Water plant {n}"}}], "ui", TODAY)
    s.tick(now); s.tick(now + timedelta(minutes=1))
    batches.apply(conn, [{"op": "create", "fields": {"name": "Call the bank"}}], "ui", TODAY)     # later the same day
    s.tick(now + timedelta(minutes=2))
    assert calls == ["filing", "filing"]                                 # one try per batch of adds, never one per task
    assert filing.queued(conn, now.date()) == []                         # a filing run not made is simply not made
    assert conn.execute("SELECT COUNT(*) FROM runs WHERE kind = 'filing'").fetchone()[0] == 1 and _failed_rows(conn, "filing") == 1
    assert get_settings(conn)[R.UNAVAILABLE_KEY]["why"] == answer


def test_settings_say_ripples_and_filing_wait_while_claude_cannot_run_and_a_good_run_clears_it(cfg, tmp_path, monkeypatch):
    client, c = _app(cfg, tmp_path)
    assert client.get("/api/settings").json()["claude_unavailable"] is None
    R.note_unavailable(c, "filing", "signed_out", date.today()); c.commit()
    assert client.get("/api/settings").json()["claude_unavailable"] == "signed_out"
    set_setting(c, R.UNAVAILABLE_KEY, {"day": "2020-01-01", "why": "missing", "logged": ["filing"]}); c.commit()
    assert client.get("/api/settings").json()["claude_unavailable"] is None              # yesterday's is not today's
    R.note_unavailable(c, "ripple", "missing", date.today()); c.commit()
    monkeypatch.setattr(filing, "RUNS_DIR", tmp_path / "runs")
    tid = batches.apply(c, [{"op": "create", "fields": {"name": "Water the plants"}}], "ui", TODAY)["created"][0]
    good = RunResult(True, {"project": None, "effort": "S", "link": None, "twin": None}, "{}", 0.01, 900)
    assert filing.run(c, cfg, tid, runner=lambda *a, **k: good)["ok"]
    assert client.get("/api/settings").json()["claude_unavailable"] is None


def test_unavailable_is_only_a_missing_program_or_a_dead_login():
    assert R.unavailable(SIGNED_OUT) == "signed_out"
    assert R.unavailable(_missing_binary({}, "filing", "p")) == "missing"
    assert R.unavailable(RunResult(False, None, "Sorry", 0.1, 900, error="final message has no JSON object: Sorry")) is None
    assert R.unavailable(RunResult(True, {}, "{}", 0.1, 900)) is None


# --- A1: a missing web file never makes the build stamp raise -------------------------------------------

def test_the_build_stamp_skips_a_missing_file_and_changes_when_it_appears(tmp_path, monkeypatch, cfg):
    from factotum import api
    for f in api.STAMPED:
        (tmp_path / f).write_text("x")
    monkeypatch.setattr(api, "WEB", tmp_path)
    full = api.build_stamp()
    (tmp_path / "app.css").unlink()
    gone = api.build_stamp()                                              # no FileNotFoundError
    assert gone != full and len(gone) == 12
    (tmp_path / "app.css").write_text("y")
    assert api.build_stamp() != gone
    (tmp_path / "manifest.webmanifest").unlink()
    client = TestClient(create_app(cfg, db_path=str(tmp_path / "a.db")))
    r = client.get("/api/review/count")
    assert r.status_code == 200 and len(r.headers["x-factotum-build"]) == 12


# --- A2: undo of a batch whose stored undo data is broken -----------------------------------------------

@pytest.mark.parametrize("stored", ["{not json", "7", '"text"'])
def test_undo_of_a_batch_with_broken_undo_data_answers_400_and_changes_nothing(cfg, tmp_path, stored):
    client, c = _app(cfg, tmp_path)
    mk(c, 1, name="Water the plants")
    bid = client.post("/api/batches/apply", json={"source": "ui", "changes": [{"op": "done", "task_id": 1}]}).json()["batch_id"]
    c.execute("UPDATE batches SET undo_json = ? WHERE id = ?", (stored, bid)); c.commit()
    r = client.post(f"/api/batches/{bid}/undo", json={})
    assert r.status_code == 400 and "undo" in r.json()["detail"]
    assert c.execute("SELECT undone_at FROM batches WHERE id = ?", (bid,)).fetchone()[0] is None
    assert c.execute("SELECT status FROM tasks WHERE id = 1").fetchone()[0] == "done" and not c.in_transaction


# --- A3: GET routes that write and commit take turns ----------------------------------------------------

def test_overlapping_review_and_task_reads_that_stamp_answer_only_200(cfg, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    client, c = _app(cfg, tmp_path)
    for i in range(1, 41):
        mk(c, i, name=f"Task number {i}", last_mention_at="2026-09-01")
    link(c, 2, 1, "waits_on")
    urls = [f"/api/tasks/{1 + i % 40}" if i % 2 else "/api/review" for i in range(600)]
    with ThreadPoolExecutor(16) as ex:
        codes = list(ex.map(lambda u: client.get(u).status_code, urls))
    assert set(codes) == {200}, {u: s for u, s in zip(urls, codes) if s != 200}
    assert not c.in_transaction


# --- A4: `factotum effective` leaves nothing beside a database it only read ---------------------------------

def test_effective_leaves_no_wal_or_shm_beside_the_database(tmp_path, monkeypatch, capsys):
    from conftest import FIXTURE
    from factotum.__main__ import main
    from factotum.db import connect, init_db, set_setting
    db = tmp_path / "f.db"
    c = connect(db); init_db(c); set_setting(c, "lifecycle.top_cap", 5); c.commit(); c.close()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.db"]
    before = db.read_bytes()
    monkeypatch.setattr(C, "DEFAULT_CONFIG_PATHS", [FIXTURE])
    monkeypatch.setenv("FACTOTUM_DB", str(db))
    assert main(["effective", "lifecycle.top_cap"]) == 0
    assert capsys.readouterr().out.splitlines()[-1].split()[1:] == ["5", "settings"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.db"] and db.read_bytes() == before


# --- F3: `exclude=` is parsed safely, once, for both Review routes ---------------------------------------

def test_exclude_takes_ascii_digits_with_one_minus_and_ignores_the_rest(cfg, tmp_path):
    from factotum.api import exclude_ids
    assert exclude_ids("1, 2,-3,²,--5,3²,٣,+4,,x, 7 ") == {1, 2, -3, 7}
    assert exclude_ids(None) == set() and exclude_ids("") == set()
    client, c = _app(cfg, tmp_path)
    mk(c, 1, name="Water the plants", last_mention_at="2026-09-01")
    for route in ("/api/review", "/api/review/count"):
        for bad in ("²", "--5", "1,²,--5", "٣"):
            assert client.get(route, params={"exclude": bad}).status_code == 200, (route, bad)


# --- C1: a task with no project says "no project" in its score reasons -----------------------------------

def test_a_task_with_no_project_says_no_project_in_its_score_reasons(cfg):
    from datetime import date
    from factotum import scoring
    for project in (None, ""):
        _, _, reasons = scoring.score({"id": 1, "name": "x", "project": project, "mentions": 1, "status": "open"}, date(2026, 11, 11), cfg)
        assert not any(r.startswith(("None ", " ")) for r in reasons), reasons
        assert any(r.startswith("no project medium +") for r in reasons), reasons
    _, _, reasons = scoring.score({"id": 1, "name": "x", "project": "Personal", "mentions": 1, "status": "open"}, date(2026, 11, 11), cfg)
    assert any(r.startswith("Personal ") for r in reasons), reasons


# --- C3: after a reminder's Done the task name and "Undo is in the Log tab." are two lines ----------------

def test_the_done_page_puts_the_task_name_and_the_undo_line_apart():
    from factotum.api import _page
    html = _page("Task-5 marked done", "Call the bank", "", undo=True, task_id=5)
    assert "Call the bank Undo" not in html and "Call the bankUndo" not in html
    assert '>Call the bank</p>' in html
    assert '>Undo is in the Log tab.</p>' in html
    assert "Undo is in the Log tab." not in _page("Task-5 snoozed", "Call the bank", "", undo=False, task_id=5)


# --- C6: Factotum's own refusal of a push address is told apart from a push service's -------------------

def test_factotums_own_refusal_of_a_push_address_is_marked_as_such(cfg, tmp_path):
    import os
    from cryptography.hazmat.primitives.asymmetric import ec
    from factotum import app_push as AP, webpush as W
    client, c = _app(cfg, tmp_path)
    client.get("/api/push")

    def body(endpoint):
        ua = ec.generate_private_key(ec.SECP256R1())
        return {"endpoint": endpoint, "keys": {"p256dh": W.b64u(W.public_bytes(ua)), "auth": W.b64u(os.urandom(16))},
                "label": "Chrome on Linux", "origin": "https://f.example"}
    r = client.post("/api/push/subscribe", json=body("https://intranet.corp/x"))           # not a known push service: Factotum's refusal
    assert r.status_code == 400 and r.json()["factotum_refused"] is True and "intranet.corp" in r.json()["detail"]
    b = body("https://push.invalid/x"); b["keys"]["auth"] = "AA"                               # a broken device secret: not about the address
    r = client.post("/api/push/subscribe", json=b)
    assert r.status_code == 400 and not r.json().get("factotum_refused")
    assert client.post("/api/push/subscribe", json=body("https://push.invalid/ok")).status_code == 200
    c.execute("UPDATE push_subscriptions SET endpoint = 'https://intranet.corp/admin' WHERE endpoint = 'https://push.invalid/ok'"); c.commit()
    out = client.post("/api/push/test", json={"endpoint": "https://intranet.corp/admin"}).json()
    assert out["ok"] is False and out["factotum_refused"] is True
    dev = client.get("/api/push").json()["devices"][0]
    assert dev["factotum_refused"] is True and dev["last_error"].startswith("not sent: ")
    # a push service's own answer is not Factotum's refusal
    import httpx
    assert client.post("/api/push/subscribe", json=body("https://push.invalid/svc")).status_code == 200
    client.app.state.push_transport = httpx.MockTransport(lambda req: httpx.Response(500))
    out = client.post("/api/push/test", json={"endpoint": "https://push.invalid/svc"}).json()
    assert out["ok"] is False and out["status"] == 500 and out["factotum_refused"] is False
    devs = {d["endpoint"]: d for d in client.get("/api/push").json()["devices"]}
    assert devs["https://push.invalid/svc"]["factotum_refused"] is False and devs["https://intranet.corp/admin"]["factotum_refused"] is True


# --- G1 / G3: a chat reply's mail threads are followed in a batch, never at once ------------------------

def _chat_reply(c, cfg, tmp_path, monkeypatch, proposals, seen, attached=(5,)):
    """One stored chat turn whose run read the `seen` records and replied with `proposals`."""
    from pathlib import Path
    from factotum import chat
    from factotum.claude.runner import RunResult
    from conftest import TODAY
    monkeypatch.setattr(chat, "RUNS_DIR", tmp_path / "runs")
    data = {"reply_md": "Found it.", "proposals": proposals}

    def fake(cfg, kind, prompt, **kw):
        (Path(kw["workdir"]) / "seen.jsonl").write_text("".join(json.dumps(s) + "\n" for s in seen))
        return RunResult(True, data, json.dumps(data), 0.1, 100, session_id="S")
    return chat.turn(c, cfg, chat.new_thread(c), "find the thread", list(attached), today=TODAY, runner=fake)


SEEN = [{"conversation_id": "conv-1", "subject": "Visit dates", "account": "me@example.edu", "at": "2026-11-01T08:00:00Z"},
        {"conversation_id": "conv-1", "subject": "Visit dates", "at": "2026-11-01T09:30:00Z"},          # mail_thread after mail_search
        {"conversation_id": "conv-1", "at": "2026-10-30T07:00:00Z"}]                                    # an older message read last


def test_a_chat_link_thread_is_a_proposal_written_by_apply_and_taken_back_by_undo(cfg, tmp_path, monkeypatch):
    client, c = _app(cfg, tmp_path)
    mk(c, 5, name="Plan the visit")
    mk(c, 6, name="Book the room")
    c.execute("INSERT INTO links (task_id, kind, ref, created_at) VALUES (6, 'mail_thread', 'conv-1', ?)", (now_iso(),)); c.commit()
    res = _chat_reply(c, cfg, tmp_path, monkeypatch, [
        {"op": "link_thread", "task_id": 5, "fields": {"conversation_id": "conv-1"}, "label": "link the visit thread"},
        {"op": "link_thread", "task_id": 6, "fields": {"conversation_id": "conv-1"}, "label": "already followed"},
        {"op": "link_thread", "task_id": 5, "fields": {"conversation_id": "never-read"}, "label": "a thread it never read"}], SEEN, attached=(5, 6))
    assert not c.execute("SELECT 1 FROM links WHERE task_id = 5").fetchone()          # nothing written by the turn
    p = res["proposals"][0]
    assert p["op"] == "link_thread" and p["applied"] is False and p["task_id"] == 5
    assert p["fields"] == {"conversation_id": "conv-1", "subject": "Visit dates", "account": "me@example.edu", "at": "2026-11-01T09:30:00Z"}
    assert res["proposals"][1]["applied"] is True                                       # a thread the task follows already
    assert len(res["proposals"]) == 2 and any("never read" in r for r in res["rejected"])
    # staged as the page stages it: one follow_thread change, written by Apply, seen up to the newest mail the run read
    r = client.post("/api/batches/apply", json={"source": "ui", "changes": [{"task_id": 5, "op": "follow_thread", "fields": p["fields"]}]}).json()
    ln = c.execute("SELECT ref, label, account, last_seen_at FROM links WHERE task_id = 5 AND kind = 'mail_thread'").fetchone()
    assert dict(ln) == {"ref": "conv-1", "label": "Visit dates", "account": "me@example.edu", "last_seen_at": "2026-11-01T09:30:00Z"}
    assert c.execute("SELECT field FROM changes WHERE batch_id = ?", (r["batch_id"],)).fetchone()[0] == "thread"
    assert client.post(f"/api/batches/{r['batch_id']}/undo", json={}).json()["undone"] is True
    assert not c.execute("SELECT 1 FROM links WHERE task_id = 5").fetchone()


def test_seen_threads_keep_the_newest_time_the_run_read():
    import tempfile
    from pathlib import Path
    from factotum import mcp_server
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "seen.jsonl").write_text("".join(json.dumps(s) + "\n" for s in SEEN))
        s = mcp_server.seen_threads(d)["conv-1"]
    assert s["at"] == "2026-11-01T09:30:00Z" and s["subject"] == "Visit dates" and s["account"] == "me@example.edu"


def test_the_chat_prompts_say_a_thread_is_followed_once_the_user_applies_it():
    from factotum import config as C
    for name in ("chat-turn.md", "chat-search-mail.md", "chat-draft-reply.md"):
        text = (C.ROOT / "prompts" / name).read_text(encoding="utf-8")
        assert "link_thread" in text and "Factotum then watches it for replies." not in text
    assert "once the user applies it" in (C.ROOT / "prompts" / "chat-turn.md").read_text(encoding="utf-8")


def test_a_chat_create_follows_its_cited_threads_inside_its_batch(cfg, tmp_path, monkeypatch):
    from conftest import TODAY
    from factotum import chat
    client, c = _app(cfg, tmp_path)
    mk(c, 5, name="Plan the visit")
    ev = [{"kind": "mail_thread", "ref": "conv-1", "label": "Oct 31 from Max"}]
    res = _chat_reply(c, cfg, tmp_path, monkeypatch, [
        {"op": "create", "fields": {"name": "Book the room"}, "label": "new task", "evidence": ev},
        {"op": "create", "fields": {"name": "Send the agenda"}, "label": "new task", "evidence": ev}], SEEN)
    mid = res["message_id"]
    assert res["proposals"][0]["evidence"][0]["at"] == "2026-11-01T09:30:00Z"
    # Create: the task and its thread in one batch, one Undo takes back both
    tid = chat.create_from_proposal(c, cfg, mid, 0, TODAY)
    bid = chat.messages(c, res["thread_id"])[-1]["proposals"][0]["batch_id"]
    ln = c.execute("SELECT id, last_seen_at FROM links WHERE task_id = ? AND kind = 'mail_thread' AND ref = 'conv-1'", (tid,)).fetchone()
    assert ln and ln["last_seen_at"] == "2026-11-01T09:30:00Z"
    assert {r[0] for r in c.execute("SELECT field FROM changes WHERE batch_id = ?", (bid,))} == {"created", "thread"}
    client.post(f"/api/batches/{bid}/undo", json={})
    assert not c.execute("SELECT 1 FROM links WHERE task_id = ?", (tid,)).fetchone()
    assert c.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()[0] == "dropped"
    # staged in the tray (from_chat): the same, through the apply route
    r = client.post("/api/batches/apply", json={"source": "ui", "changes": [{"op": "create", "fields": {}, "from_chat": {"message_id": mid, "index": 1}}]}).json()
    t2 = r["created"][0]
    assert c.execute("SELECT 1 FROM links WHERE task_id = ? AND kind = 'mail_thread' AND ref = 'conv-1'", (t2,)).fetchone()
    assert {row[0] for row in c.execute("SELECT field FROM changes WHERE batch_id = ?", (r["batch_id"],))} == {"created", "thread"}
    client.post(f"/api/batches/{r['batch_id']}/undo", json={})
    assert not c.execute("SELECT 1 FROM links WHERE task_id = ?", (t2,)).fetchone()


# --- G2: a notification's Done and Snooze write in one undoable batch -------------------------------------

def _sent_reminder(c, cfg, tid, fire_at="2026-11-03T09:00", label="Call back"):
    from datetime import datetime
    from conftest import TODAY
    from factotum import batches, reminders

    class Push:
        def __call__(self, *a, **k):
            return "m1"
    batches.apply(c, [{"task_id": tid, "op": "add_reminder", "fields": {"fire_at": fire_at, "label": label}}], "ui", TODAY)
    reminders.fire(c, cfg, datetime(2026, 11, 3, 9, 0, 30), Push())
    return c.execute("SELECT * FROM reminders WHERE task_id = ? AND status = 'sent' ORDER BY id DESC", (tid,)).fetchone()


def test_a_notification_s_snooze_is_one_undoable_batch_the_log_can_name(cfg, tmp_path):
    client, c = _app(cfg, tmp_path)
    mk(c, 1, name="Call Reza")
    cfg["notify"]["ntfy"]["topic"] = "factotum-test"
    r0 = _sent_reminder(c, cfg, 1)
    before = c.execute("SELECT COALESCE(MAX(id), 0) FROM batches").fetchone()[0]
    assert client.get(f"/r/{r0['token']}", params={"a": "snooze"}).status_code == 200
    b = c.execute("SELECT * FROM batches WHERE id > ?", (before,)).fetchall()
    assert len(b) == 1 and b[0]["source"] == "ntfy"
    assert c.execute("SELECT status FROM reminders WHERE id = ?", (r0["id"],)).fetchone()[0] == "snoozed"
    new = c.execute("SELECT * FROM reminders WHERE task_id = 1 AND status = 'pending'").fetchall()
    assert len(new) == 1 and new[0]["label"] == "Call back" and new[0]["created_by"] == "ntfy"
    ch = c.execute("SELECT field, new FROM changes WHERE batch_id = ?", (b[0]["id"],)).fetchall()
    assert [x["field"] for x in ch] == ["reminder"]
    said = json.loads(ch[0]["new"])
    assert said["fire_at"] == new[0]["fire_at"] and said["snoozed"] is True       # the Log says "snoozed to <when>"
    # Undo: the new reminder is cancelled and the one that fired is armed again (its buttons work again)
    u = client.post(f"/api/batches/{b[0]['id']}/undo", json={}).json()
    assert u["undone"] is True and u["skipped"] == []
    assert c.execute("SELECT status FROM reminders WHERE id = ?", (new[0]["id"],)).fetchone()[0] == "cancelled"
    assert c.execute("SELECT status FROM reminders WHERE id = ?", (r0["id"],)).fetchone()[0] == "sent"
    assert client.get(f"/r/{r0['token']}", params={"a": "snooze"}).status_code == 200


def test_undo_of_a_notification_s_done_re_arms_its_reminder(cfg, tmp_path):
    client, c = _app(cfg, tmp_path)
    mk(c, 1, name="Call Reza")
    cfg["notify"]["ntfy"]["topic"] = "factotum-test"
    r0 = _sent_reminder(c, cfg, 1)
    assert client.get(f"/r/{r0['token']}", params={"a": "done"}).status_code == 200
    assert c.execute("SELECT status FROM reminders WHERE id = ?", (r0["id"],)).fetchone()[0] == "done"
    bid = c.execute("SELECT id FROM batches WHERE source = 'ntfy' ORDER BY id DESC").fetchone()[0]
    assert c.execute("SELECT status FROM tasks WHERE id = 1").fetchone()[0] == "done"
    u = client.post(f"/api/batches/{bid}/undo", json={}).json()
    assert u["undone"] is True and u["skipped"] == []
    assert c.execute("SELECT status FROM tasks WHERE id = 1").fetchone()[0] == "open"
    assert c.execute("SELECT status FROM reminders WHERE id = ?", (r0["id"],)).fetchone()[0] == "sent"
    # the button works again, and a reminder acted on since is left as it is by a second undo attempt
    assert client.get(f"/r/{r0['token']}", params={"a": "done"}).status_code == 200


def test_undo_leaves_a_reminder_alone_that_changed_since(conn, cfg):
    from conftest import TODAY
    from factotum import batches
    mk(conn, 1)
    cfg["notify"]["ntfy"]["topic"] = "factotum-test"
    r0 = _sent_reminder(conn, cfg, 1)
    res = batches.apply(conn, [{"task_id": 1, "op": "done", "reminder_status": {"id": r0["id"], "status": "done"}}], "ntfy", TODAY)
    conn.execute("UPDATE reminders SET status = 'cancelled' WHERE id = ?", (r0["id"],)); conn.commit()
    u = batches.undo(conn, res["batch_id"])
    assert conn.execute("SELECT status FROM reminders WHERE id = ?", (r0["id"],)).fetchone()[0] == "cancelled"
    assert any("reminder" in s["why"] for s in u["skipped"]) and conn.execute("SELECT status FROM tasks WHERE id = 1").fetchone()[0] == "open"


def test_the_page_cannot_send_a_reminder_status(cfg, tmp_path):
    client, c = _app(cfg, tmp_path)
    mk(c, 1)
    cfg["notify"]["ntfy"]["topic"] = "factotum-test"
    r0 = _sent_reminder(c, cfg, 1)
    client.post("/api/batches/apply", json={"source": "ui", "changes": [{"task_id": 1, "op": "keep", "reminder_status": {"id": r0["id"], "status": "done"}}]})
    assert c.execute("SELECT status FROM reminders WHERE id = ?", (r0["id"],)).fetchone()[0] == "sent"


# --- H15: the outcome after a notification's Done is saved once ------------------------------------------

def test_the_outcome_form_saves_once_and_an_empty_one_does_not_use_it_up(cfg, tmp_path):
    from stage4_helpers import link
    client, c = _app(cfg, tmp_path)
    mk(c, 1, name="Ask the chair"); mk(c, 2); link(c, 2, 1, "waits_on")
    cfg["notify"]["ntfy"]["topic"] = "factotum-test"
    tok = _sent_reminder(c, cfg, 1)["token"]
    assert client.get(f"/r/{tok}", params={"a": "done"}).status_code == 200
    c.execute("DELETE FROM ripple_queue"); c.commit()
    n = _batches(c)
    r = client.post(f"/r/{tok}/outcome", data={"outcome": "   "})                       # empty: nothing saved, the form still works
    assert "nothing saved" in r.text and _batches(c) == n
    r = client.post(f"/r/{tok}/outcome", data={"outcome": "The chair can serve remotely"})
    assert r.status_code == 200 and "outcome saved" in r.text and _batches(c) == n + 1
    assert c.execute("SELECT outcome FROM tasks WHERE id = 1").fetchone()[0] == "The chair can serve remotely"
    assert c.execute("SELECT COUNT(*) FROM ripple_queue WHERE task_id = 1").fetchone()[0] == 1
    c.execute("DELETE FROM ripple_queue"); c.commit()
    r = client.post(f"/r/{tok}/outcome", data={"outcome": "something else, posted again"})
    assert r.status_code == 200 and "The outcome is already saved; change it in the task" in r.text
    assert c.execute("SELECT outcome FROM tasks WHERE id = 1").fetchone()[0] == "The chair can serve remotely"
    assert _batches(c) == n + 1 and c.execute("SELECT COUNT(*) FROM ripple_queue").fetchone()[0] == 0
    # Undo of the outcome's batch gives the form back
    bid = c.execute("SELECT MAX(id) FROM batches").fetchone()[0]
    client.post(f"/api/batches/{bid}/undo", json={})
    assert "outcome saved" in client.post(f"/r/{tok}/outcome", data={"outcome": "Third"}).text
