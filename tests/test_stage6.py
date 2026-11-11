"""Stage 6, in place and by hand: the add line, adding at once, finding any task."""
import json
import threading
from datetime import date

from fastapi.testclient import TestClient

from factotum import api, batches, commands
from factotum.db import connect, init_db, one, rows
from conftest import FIXTURE, TODAY                          # TODAY is Sunday 2026-11-01
from stage4_helpers import active_rels, link, mk

PROJECTS = ["BDA M.S.", "BDA 696", "COMPE 510", "Plaud Sync", "Personal", "Hardware Lab"]
EFFORTS = ["XS", "S", "M", "L"]
P = lambda s, **k: commands.parse_add(s, TODAY, PROJECTS, EFFORTS, **k)      # noqa: E731
K = "; kept in the name"


# --- Task 1: the add line's grammar

def test_the_designs_example_line():
    r = P("add Email the lab the dataset fri #hardware ~S after 120")
    assert r["name"] == "Email the lab the dataset" and r["refused"] == [] and r["after"] == 120 and r["under"] is None
    assert r["fields"] == {"name": "Email the lab the dataset", "due": "2026-11-06", "project": "Hardware Lab", "effort": "S"}
    assert [p["kind"] for p in r["parts"]] == ["due", "project", "effort", "waits_on"]
    assert r["parts"][0] == {"kind": "due", "words": "fri", "value": "2026-11-06", "label": "Fri Nov 6", "legend": "is the due date"}
    assert (r["parts"][3]["label"], r["parts"][3]["legend"]) == ("waits on Task-120", "makes it wait on Task-120")


def test_the_verbs_and_the_line_without_one():
    assert P("new Call mom")["name"] == P("+Call mom")["name"] == P("+ Call mom")["name"] == "Call mom"
    assert P("Call mom") is None and P("addition to the list") is None and P("add") is None
    assert P("Call mom", verb=False)["name"] == "Call mom" and P("add Call mom", verb=False)["name"] == "Call mom"
    r = P("add #personal ~S")
    assert r["name"] == "" and r["refused"] == [{"words": "", "why": "a task needs a name"}]


def test_add_text_that_looks_like_a_command_or_a_number_is_a_name():
    assert P("add done 12")["name"] == "done 12" and commands.parse("add done 12", TODAY) is None
    assert commands.parse("add Email the lab fri", TODAY) is None and commands.parse("new 12", TODAY) is None
    for line, name in (("add Plan monday meeting fri", "Plan monday meeting"), ("add Call Sun Life", "Call Sun Life"),
                       ("add Pay the 12 invoices", "Pay the 12 invoices"), ("add Fix issue #12", "Fix issue #12"),
                       ("add Read chapter 3 after lunch", "Read chapter 3 after lunch")):
        assert P(line)["name"] == name and P(line)["refused"] == [], line
    assert P("add Plan monday meeting fri")["fields"]["due"] == "2026-11-06" and "due" not in P("add Call Sun Life")["fields"]


def test_hard_deadlines_and_date_phrases():
    r = P("add Submit the form fri!")
    assert r["fields"]["due"] == "2026-11-06" and r["fields"]["hard"] == 1 and [p["kind"] for p in r["parts"]] == ["due", "hard"]
    assert P("add Submit the form tomorrow !")["fields"] == {"name": "Submit the form", "due": "2026-11-02", "hard": 1}
    for line in ("add Submit fri! !", "add Submit ! fri !"):                               # a second ! is refused and kept
        r = P(line)
        assert (r["name"], r["fields"]) == ("Submit !", {"name": "Submit !", "due": "2026-11-06", "hard": 1}), line
        assert r["refused"] == [{"words": "!", "why": "a second !" + K}], line
    r = P("add Fix it !")
    assert r["name"] == "Fix it !" and "hard" not in r["fields"] and r["refused"] == [{"words": "!", "why": "! needs a due date" + K}]
    r = P("add Send the form by nov 13")
    assert r["name"] == "Send the form" and r["fields"]["due"] == "2026-11-13" and r["parts"][0]["words"] == "by nov 13"
    r = P("add Call the office due next fri")
    assert r["name"] == "Call the office" and r["fields"]["due"] == "2026-11-13" and r["parts"][0]["words"] == "due next fri"
    for bad in ("feb 30", "2026-13-01"):
        r = P(f"add Pay rent {bad}")
        assert r["name"] == f"Pay rent {bad}" and r["refused"] == [{"words": bad, "why": f"{bad} is not a date" + K}]


def test_projects_and_efforts_match_or_say_why():
    assert P("add Read #BDA696")["fields"]["project"] == "BDA 696"
    assert P("add Read #compe-510")["fields"]["project"] == "COMPE 510"
    assert P("add Read #plaud")["fields"]["project"] == "Plaud Sync" and P("add Read #personal")["fields"]["project"] == "Personal"
    assert P("add Read ~xs")["fields"]["effort"] == "XS"
    for line, words, why in (("add Write it #hardwre", "#hardwre", "no project matches #hardwre"), ("add Read #b", "#b", "no project matches #b"),
                             ("add Read #bda", "#bda", "#bda could be BDA M.S. or BDA 696"), ("add Read ~XL", "~XL", "~XL is not an effort (XS, S, M, L)")):
        r = P(line)
        assert r["refused"] == [{"words": words, "why": why + K}] and r["name"].endswith(words) and "project" not in r["fields"] and "effort" not in r["fields"], line


def test_the_last_of_a_kind_wins_and_the_earlier_stays_in_the_name():
    r = P("add Plan #personal trip #compe510")
    assert r["fields"]["project"] == "COMPE 510" and r["name"] == "Plan #personal trip" and r["refused"] == [{"words": "#personal", "why": "a second project" + K}]
    r = P("add Plan trip fri mon")
    assert r["fields"]["due"] == "2026-11-02" and r["name"] == "Plan trip fri" and r["refused"] == [{"words": "fri", "why": "a second due date" + K}]
    r = P("add Draft the intro under 30 after Task-12")
    assert (r["name"], r["under"], r["after"]) == ("Draft the intro", 30, 12) and [p["label"] for p in r["parts"]] == ["part of Task-30", "waits on Task-12"]
    r = P("add X after 3 after 4")
    assert r["after"] == 4 and r["name"] == "X after 3" and r["refused"] == [{"words": "after 3", "why": "a second after" + K}]


def test_task_numbers_are_checked_when_a_lookup_is_given():
    known = {30: {"id": 30, "name": "Goal", "status": "open", "goal": None}, 40: {"id": 40, "name": "Step", "status": "open", "goal": 30},
             7: {"id": 7, "name": "Old", "status": "done", "goal": None}}
    Q = lambda s: P(s, task=known.get)                                                      # noqa: E731
    assert Q("add Read after 99")["refused"] == [{"words": "after 99", "why": "no Task-99" + K}] and Q("add Read after 99")["name"] == "Read after 99"
    assert Q("add Read after 7")["refused"][0]["why"] == "Task-7 is already done" + K
    assert Q("add Read under 40")["refused"][0]["why"] == "Task-40 is a step of Task-30; a step cannot have steps" + K
    r = Q("add Read under 30 after 30")
    assert r["under"] == 30 and r["after"] is None and r["refused"] == [{"words": "after 30", "why": "Task-30 is its goal; a task cannot wait on its own goal" + K}]
    r = Q("add Read under 30")
    assert r["parts"][0]["other"] == {"id": 30, "name": "Goal", "status": "open"}


# --- Task 2: the preview, checked against the database

def _client(cfg, tmp_path):
    path = tmp_path / "t.db"
    conn = connect(path)
    init_db(conn)
    return TestClient(api.create_app(cfg, db_path=str(path))), conn


def test_the_preview_checks_the_tasks_it_names(cfg, tmp_path):
    client, conn = _client(cfg, tmp_path)
    mk(conn, 30, name="Assemble the committee"); mk(conn, 12, name="Ask the chair"); mk(conn, 7, name="Old", status="done"); mk(conn, 40)
    link(conn, 40, 30, "part_of")
    n_batches = one(conn, "SELECT COUNT(*) AS n FROM batches")["n"]
    pv = lambda t: client.get("/api/add/preview", params={"text": t}).json()       # noqa: E731
    r = pv("Draft the intro under 30 after 12")
    assert r["is_add"] and r["name"] == "Draft the intro" and r["refused"] == []
    assert r["change"] == {"op": "create", "fields": {"name": "Draft the intro", "links": [{"kind": "part_of", "other": 30}, {"kind": "waits_on", "other": 12}]}}
    assert [p["other"]["name"] for p in r["parts"]] == ["Assemble the committee", "Ask the chair"]
    assert pv("add Read")["name"] == "Read"                                         # a verb is allowed, not needed
    assert pv("Pay the 12 invoices after 99")["refused"] == [{"words": "after 99", "why": "no Task-99" + K}]
    assert pv("Read after 7")["refused"][0]["why"] == "Task-7 is already done" + K
    assert pv("Read under 40")["refused"][0]["why"] == "Task-40 is a step of Task-30; a step cannot have steps" + K
    assert pv("Read #nosuch")["refused"][0]["why"] == "no project matches #nosuch" + K
    assert pv("")["refused"] == [{"words": "", "why": "a task needs a name"}]
    assert one(conn, "SELECT COUNT(*) AS n FROM batches")["n"] == n_batches        # a preview writes nothing


# --- Task 3: a new task exists on Enter

def test_a_task_made_by_hand_exists_at_once_and_its_undo_drops_it(cfg, tmp_path):
    client, conn = _client(cfg, tmp_path)
    mk(conn, 12, name="Ask the chair")
    r = client.post("/api/add", json={"text": "Email the lab #personal ~S after 12", "key": "k1abcdef"}).json()
    t = one(conn, "SELECT * FROM tasks WHERE id = ?", (r["task_id"],))
    assert not r["already"] and r["refused"] == [] and r["skipped"] == []
    assert (t["name"], t["project"], t["effort"], t["source"], t["status"]) == ("Email the lab", "Personal", "S", "Manual", "open")
    assert (r["task_id"], 12, "waits_on") in active_rels(conn)
    assert one(conn, "SELECT source FROM batches WHERE id = ?", (r["batch_id"],))["source"] == "ui"
    client.post(f"/api/batches/{r['batch_id']}/undo", json={})
    t = one(conn, "SELECT status, closed_by FROM tasks WHERE id = ?", (r["task_id"],))
    assert (t["status"], t["closed_by"]) == ("dropped", "undo") and (r["task_id"], 12, "waits_on") not in active_rels(conn)


def test_two_quick_enters_make_one_task(cfg, tmp_path):
    from factotum import by_hand
    client, conn = _client(cfg, tmp_path)
    a = client.post("/api/add", json={"text": "Water the plants", "key": "samekey1"}).json()
    b = client.post("/api/add", json={"text": "Water the plants", "key": "samekey1"}).json()
    assert b["already"] and b["task_id"] == a["task_id"] and b["batch_id"] is None
    shared = connect(tmp_path / "t.db", shared=True)
    got = []
    ts = [threading.Thread(target=lambda: got.append(by_hand.add(shared, cfg, text="Feed the cat", key="samekey2", today=TODAY))) for _ in range(2)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len({g["task_id"] for g in got}) == 1 and sorted(g["already"] for g in got) == [False, True]
    assert one(conn, "SELECT COUNT(*) AS n FROM tasks WHERE name IN ('Water the plants', 'Feed the cat')")["n"] == 2


def test_refused_words_stay_in_the_name_and_are_reported(cfg, tmp_path):
    client, conn = _client(cfg, tmp_path)
    r = client.post("/api/add", json={"text": "Read after 99", "key": "k2abcdef"}).json()
    assert one(conn, "SELECT name FROM tasks WHERE id = ?", (r["task_id"],))["name"] == "Read after 99"
    assert r["refused"] == [{"words": "after 99", "why": "no Task-99" + K}] and not active_rels(conn)


def test_add_with_fields_for_the_phone_sheet_add_a_step_and_duplicate(cfg, tmp_path):
    client, conn = _client(cfg, tmp_path)
    mk(conn, 30); mk(conn, 12)
    r = client.post("/api/add", json={"key": "k3abcdef", "fields": {"name": "Copy", "due": "2026-11-20", "hard": True, "project": "Personal", "effort": "M",
                                                                   "context": "ctx", "counterpart": "a@b.c", "under": 30, "after": 12}}).json()
    t = one(conn, "SELECT * FROM tasks WHERE id = ?", (r["task_id"],))
    assert (t["due"], t["hard"], t["project"], t["effort"], t["context"], t["counterpart"]) == ("2026-11-20", 1, "Personal", "M", "ctx", "a@b.c")
    assert {(r["task_id"], 30, "part_of"), (r["task_id"], 12, "waits_on")} <= active_rels(conn)
    r = client.post("/api/add", json={"key": "k4abcdef", "fields": {"name": "X", "project": "Nope", "effort": "XXL", "due": "soon", "score": 9, "after": "12"}}).json()
    assert [x["why"] for x in r["refused"]] == ["'soon' is not a date", "'Nope' is not a project", "'XXL' is not an effort (XS, S, M, L)",
                                                "'12' is not a task number", "score cannot be set on a new task"]
    assert one(conn, "SELECT project, effort, due FROM tasks WHERE id = ?", (r["task_id"],)) == {"project": None, "effort": None, "due": None}
    for bad in ("20261120", "2026-W47-1"):
        r = client.post("/api/add", json={"key": "kd" + bad[:6] + "xx", "fields": {"name": "D", "due": bad}}).json()
        assert [x["why"] for x in r["refused"]] == [f"'{bad}' is not a date"]
        assert one(conn, "SELECT due FROM tasks WHERE id = ?", (r["task_id"],))["due"] is None
    r = client.post("/api/add", json={"key": "k5abcdef", "fields": {"name": "Own goal", "under": 30, "after": 30}}).json()
    assert r["refused"] == [{"words": "after", "why": "Task-30 is its goal; a task cannot wait on its own goal"}] and r["skipped"] == []
    assert (r["task_id"], 30, "part_of") in active_rels(conn) and (r["task_id"], 30, "waits_on") not in active_rels(conn)
    assert client.post("/api/add", json={"fields": {"name": " "}}).status_code == 400
    n = one(conn, "SELECT COUNT(*) AS n FROM tasks")["n"]
    long = "Long " * 41                                                               # 205 characters: a 400, never cut at 200 (ruling R5)
    for body in ({"text": long, "key": "klongtxt"}, {"fields": {"name": long}, "key": "klongfld"}):
        r = client.post("/api/add", json=body)
        assert (r.status_code, r.json()["detail"]) == (400, "a name is at most 200 characters"), body
    assert client.post("/api/add", json={"text": "x" * 200, "key": "kexact200"}).status_code == 200
    assert one(conn, "SELECT COUNT(*) AS n FROM tasks")["n"] == n + 1
    assert client.post("/api/add", json={"text": "x", "key": "bad key!"}).status_code == 400
    assert client.post("/api/add", json={}).status_code == 400


def test_the_add_command_line(cfg, tmp_path, monkeypatch, capsys):
    from factotum import __main__ as cli
    monkeypatch.setenv("FACTOTUM_CONFIG", str(FIXTURE)); monkeypatch.setenv("FACTOTUM_DB", str(tmp_path / "c.db"))
    cli.main(["add", "Read the paper #personal", "--dry-run"])
    assert json.loads(capsys.readouterr().out)["name"] == "Read the paper"
    c = connect(tmp_path / "c.db")
    assert one(c, "SELECT COUNT(*) AS n FROM tasks")["n"] == 0
    cli.main(["add", "Read the paper #nosuch"])
    out = capsys.readouterr().out
    assert out.startswith("Task-") and "#nosuch: no project matches #nosuch" in out


# --- Task 7: finding any task

def _found(cfg, tmp_path):
    client, conn = _client(cfg, tmp_path)
    mk(conn, 12, name="Ask the chair"); mk(conn, 120, name="Firmware tests"); mk(conn, 121, name="Firmware box")
    batches.apply(conn, [{"op": "done", "task_id": 120, "fields": {"outcome": "passed"}}], "ui", date(2026, 11, 10))
    batches.apply(conn, [{"op": "merge", "task_id": 121, "fields": {"into": 12}}], "ui", TODAY)
    return client, conn


def test_any_task_can_be_found_by_number_or_words(cfg, tmp_path):
    from factotum import by_hand
    client, conn = _found(cfg, tmp_path)
    ids = lambda q, **k: [r["id"] for r in by_hand.search(conn, q, **k)["results"]]     # noqa: E731
    assert ids("12") == [12, 120, 121] and ids("Task-120")[0] == 120 and ids("#121")[0] == 121
    assert ids("firmware") == [120, 121] and ids("firm tests") == [120] and ids("") == [] and ids("100%") == []
    r = client.get("/api/search", params={"q": "firmware"}).json()["results"]
    assert r[0] == {"id": 120, "name": "Firmware tests", "status": "done", "project": "COMPE 510", "due": None, "completed_at": "2026-11-10",
                    "closed_by": "ui", "outcome": "passed", "merged_into": None}
    assert r[1]["merged_into"] == 12 and r[1]["status"] == "dropped"


def test_search_is_capped_and_says_so(conn):
    from factotum import by_hand
    for i in range(1, 41):
        mk(conn, i, name=f"Note {i}")
    r = by_hand.search(conn, "note")
    assert len(r["results"]) == 12 and r["more"] is True and len(by_hand.search(conn, "note", limit=50)["results"]) == 30


def test_the_find_command_line(cfg, tmp_path, monkeypatch, capsys):
    from factotum import __main__ as cli
    monkeypatch.setenv("FACTOTUM_CONFIG", str(FIXTURE)); monkeypatch.setenv("FACTOTUM_DB", str(tmp_path / "t.db"))
    _found(cfg, tmp_path)
    cli.main(["find", "firmware"])
    assert capsys.readouterr().out.splitlines() == ["Task-120  done 2026-11-10  Firmware tests", "Task-121  dropped 2026-11-01 merged into Task-12  Firmware box"]
