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
