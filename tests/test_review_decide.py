"""Spoken Review, stage 1: the decision rule (factotum/review_decide.py, design §5.2). Every row of the rule's
table stages exactly what the page staged for the same press (app.js reviewAct, linkSuggestionAct, the
[data-rv-accept], [data-follow] and [data-notabout] closures in wire(), act, stageDue, stageReminder), key order
included; Pin and Long-term stand beside under <id>:top / <id>:lt, and Later needs a day after Review's day."""
import json
import re
from datetime import date

import pytest

from stage4_helpers import mk
from test_polish import _card as _kind_card

from factotum import relations, review_cards, review_decide as R, review_queue
from factotum.db import now_iso

TODAY = date(2026, 11, 19)              # Review's day: a Thursday
TRAY0 = {"rev": 0, "entries": []}
FORBIDDEN = re.compile(r"\bblocked\b|\bdependenc", re.I)


def _ev(c, tid, kind, ref, label, account=None):
    cur = c.execute("INSERT INTO evidence (task_id, kind, ref, account, label, excerpt, at) VALUES (?, ?, ?, ?, ?, 'r', ?)",
                    (tid, kind, json.dumps(ref) if isinstance(ref, dict) else ref, account, label, now_iso()))
    return cur.lastrowid


MAIL = {"conversation_id": "conv-9", "account": "me@example.edu", "subject": "The numbers", "at": "2026-11-18T10:00:00Z"}


def _world(c):
    """Task-1 with pending evidence of every kind and link suggestions both ways (a same_as and an `any` among
    them); Tasks 2-5 at the other ends; three suggestion cards. Returns the ids by name."""
    mk(c, 1, name="Write the report", context="Draft in the folder")
    for i, n in ((2, "Get the numbers"), (3, "Plan the chapter"), (4, "Write the report again"), (5, "Ask about the grant")):
        mk(c, i, name=n)
    F = {}
    F["thr"] = _ev(c, 1, "thread_updated", "conv-1", "Max wrote")
    F["done"] = _ev(c, 1, "proposal", {"op": "done", "task_id": 1, "fields": {"outcome": "Sent it"}}, "done: sent it")
    F["pin"] = _ev(c, 1, "proposal", {"op": "pin_top", "task_id": 1}, "pin it: due soon")
    F["lt"] = _ev(c, 1, "proposal", {"op": "longterm", "task_id": 1}, "long-term: no date")
    F["rem"] = _ev(c, 1, "proposal", {"op": "add_reminder", "task_id": 1, "fields": {"fire_at": "2026-11-20T09:00", "label": None, "repeat": None}}, "remind tomorrow")
    F["link"] = _ev(c, 1, "proposal", {"op": "link", "task_id": 1, "fields": {"kind": "waits_on", "other": 2}}, "waits on the numbers")
    F["note"] = _ev(c, 1, "proposal", {"op": "add_note", "task_id": 1, "fields": {"text": "Max agreed"}}, "note what Max said")
    F["set"] = _ev(c, 1, "proposal", {"op": "set", "task_id": 1, "fields": {"context": "Draft sent to Max"}}, "where it stands", account="ripple")
    F["mail"] = _ev(c, 1, "proposal", {"op": "follow_thread", "task_id": 1, "fields": MAIL, "mail": {"subject": "The numbers", "sender": "Max"}},
                    "This mail is about Task-1. Follow its thread?")
    F["reopen"] = _ev(c, 1, "proposal", {"op": "reopen", "task_id": 1}, "reopen it")
    F["s_out"] = relations.suggest(c, "waits_on", 1, 2, "both name the numbers", "planner")
    F["s_in"] = relations.suggest(c, "part_of", 3, 1, "a chapter step", "planner")
    F["s_same"] = relations.suggest(c, "same_as", 1, 4, "twins", "twin")
    F["s_any"] = relations.suggest(c, "any", 1, 5, "one thread", "thread")
    F["c_plan"] = _kind_card(c, "planner")
    F["c_mail"] = _kind_card(c, "mail", other=2)
    F["c_slack"] = _kind_card(c, "slack")
    c.commit()
    return F


TASK_EV = ("thr", "done", "pin", "lt", "rem", "link", "note", "set", "mail", "reopen")


def J(F, skip=(), verdict="reviewed"):
    """The page's judges on Task-1's card: every pending row, a proposal "suggestion seen", the rest "thread seen"."""
    return [(f"e:{F[n]}", {"task_id": 1, "op": "judge_evidence", "fields": {"evidence_id": F[n], "verdict": verdict},
                           "label": "thread seen" if n == "thr" else "suggestion seen"}, "t:1") for n in TASK_EV if n not in skip]


def _js(puts):
    return [(k, json.dumps(e), d) for k, e, d in puts]


def tray_of(*rows):
    return {"rev": 1, "entries": [[k, e, {"decides": d}] for k, e, d in rows]}


def after(tray, p):
    """The tray once a plan is written: deletes, then puts (a key put again keeps its place)."""
    rows = [list(r) for r in tray["entries"] if r[0] not in p.dels]
    for k, e, d in p.puts:
        for r in rows:
            if r[0] == k:
                r[1], r[2] = e, {"decides": d}
                break
        else:
            rows.append([k, e, {"decides": d}])
    return {"rev": tray["rev"] + 1, "entries": rows}


@pytest.fixture
def world(conn):
    return _world(conn)


def _plan(conn, cfg, key, option, args=None, tray=TRAY0):
    return R.plan(conn, cfg, R.load_item(conn, cfg, TODAY, key), option, args, tray, TODAY)


LONG = "x" * 301
D = "t:1"


def _cases():
    """(id, key, option, args, expected puts as a function of the ids, said)"""
    def t(op, fields, label):
        return {"task_id": 1, "op": op, "fields": fields, "label": label}

    def judge(eid, tid, verdict, label):
        return {"task_id": tid, "op": "judge_evidence", "fields": {"evidence_id": eid, "verdict": verdict}, "label": label}

    later = lambda day, word: ("1", t("set", {"snoozed_until": day, "last_reviewed_at": "2026-11-19"}, "later · back " + word), D)   # noqa: E731
    return [
        ("done", "t:1", "done", None, lambda F: [("1", t("done", {}, "done"), D)] + J(F), ""),
        ("done-note", "t:1", "done", {"note": " Sent it "}, lambda F: [("1", t("done", {"outcome": "Sent it"}, "done · “Sent it”"), D)] + J(F), ""),
        ("done-long", "t:1", "done", {"note": LONG}, lambda F: [("1", t("done", {}, "done"), D),
                                                                ("1:note", t("add_note", {"text": LONG}, "note “" + "x" * 60 + "…”"), D)] + J(F), R.SAID["long_note"]),
        ("drop", "t:1", "drop", None, lambda F: [("1", t("drop", {}, "dropped"), D)] + J(F), ""),
        ("drop-note", "t:1", "drop", {"note": "No longer asked"}, lambda F: [("1", t("drop", {"outcome": "No longer asked"}, "dropped · “No longer asked”"), D)] + J(F), ""),
        ("keep", "t:1", "keep", None, lambda F: [("1", t("keep", {}, "kept"), D)] + J(F), ""),
        ("keep-note", "t:1", "keep", {"note": "Ask Max"}, lambda F: [("1", t("keep", {}, "kept"), D), ("1:note", t("add_note", {"text": "Ask Max"}, "note “Ask Max”"), D)] + J(F), ""),
        ("later-tomorrow", "t:1", "later", {"until": "tomorrow"}, lambda F: [later("2026-11-20", "tomorrow")] + J(F), ""),
        ("later-fri", "t:1", "later", {"until": "fri"}, lambda F: [later("2026-11-20", "Friday")] + J(F), ""),
        ("later-nextweek", "t:1", "later", {"until": "nextweek"}, lambda F: [later("2026-11-23", "next week")] + J(F), ""),
        ("later-date-note", "t:1", "later", {"until": "2026-12-01", "note": "after the break"},
         lambda F: [later("2026-12-01", "2026-12-01"), ("1:note", t("add_note", {"text": "after the break"}, "note “after the break”"), D)] + J(F), ""),
        ("accept-done", "t:1", "accept:{done}", None, lambda F: [("1", t("done", {"outcome": "Sent it"}, "done · “Sent it”"), D)] + J(F, skip=("done",))
         + [(f"e:{F['done']}", judge(F["done"], 1, "applied", "suggestion accepted"), D)], R.SAID["accepted_decision"]),
        ("accept-pin", "t:1", "accept:{pin}", None, lambda F: [(f"1:a{F['pin']}", t("pin_top", {}, "pin it: due soon"), None),
                                                               (f"e:{F['pin']}", judge(F["pin"], 1, "applied", "suggestion accepted"), None)], R.SAID["accepted_beside"]),
        ("accept-longterm", "t:1", "accept:{lt}", None, lambda F: [(f"1:a{F['lt']}", t("longterm", {}, "long-term: no date"), None),
                                                                   (f"e:{F['lt']}", judge(F["lt"], 1, "applied", "suggestion accepted"), None)], R.SAID["accepted_beside"]),
        ("accept-reminder", "t:1", "accept:{rem}", None, lambda F: [("1:r", t("add_reminder", {"fire_at": "2026-11-20T09:00", "label": None, "repeat": None}, "remind tomorrow"), None),
                                                                    (f"e:{F['rem']}", judge(F["rem"], 1, "applied", "suggestion accepted"), None)], R.SAID["accepted_beside"]),
        ("accept-link", "t:1", "accept:{link}", None, lambda F: [("rel:1:waits_on:2", t("link", {"kind": "waits_on", "other": 2}, "now waits on Task-2"), None),
                                                                 (f"e:{F['link']}", judge(F["link"], 1, "applied", "suggestion accepted"), None)], R.SAID["accepted_beside"]),
        ("accept-note", "t:1", "accept:{note}", None, lambda F: [(f"1:a{F['note']}", t("add_note", {"text": "Max agreed"}, "note “Max agreed”"), None),
                                                                 (f"e:{F['note']}", judge(F["note"], 1, "applied", "suggestion accepted"), None)], R.SAID["accepted_beside"]),
        ("accept-set", "t:1", "accept:{set}", None, lambda F: [(f"1:c{F['set']}", dict(t("set", {"context": "Draft sent to Max"}, "where it stands"), expect={"context": "Draft in the folder"}), None),
                                                               (f"e:{F['set']}", judge(F["set"], 1, "applied", "suggestion accepted"), None)], R.SAID["accepted_beside"]),
        ("dismiss", "t:1", "dismiss:{pin}", None, lambda F: [(f"e:{F['pin']}", judge(F["pin"], 1, "dismissed", "suggestion dismissed"), None)], ""),
        ("follow", "t:1", "follow:{mail}", None, lambda F: [(f"1:f{F['mail']}", dict(t("follow_thread", MAIL, "follows “The numbers”"), ev=F["mail"]), None),
                                                           (f"e:{F['mail']}", judge(F["mail"], 1, "applied", "mail card accepted"), None)], R.SAID["follow"]),
        ("not_about", "t:1", "not_about:{mail}", None, lambda F: [(f"e:{F['mail']}", judge(F["mail"], 1, "dismissed", "not about this mail"), None)], ""),
        ("link-out", "t:1", "link:{s_out}", None, lambda F: [("rel:1:waits_on:2", dict(t("link", {"kind": "waits_on", "other": 2}, "now waits on Task-2"), ev=F["s_out"]), None)], R.SAID["link"]),
        ("link-in", "t:1", "link:{s_in}", None, lambda F: [("rel:3:part_of:1", {"task_id": 3, "op": "link", "fields": {"kind": "part_of", "other": 1}, "label": "is now part of Task-1", "ev": F["s_in"]}, None)], R.SAID["link"]),
        ("step", "t:1", "step:{s_any}", None, lambda F: [("rel:1:part_of:5", dict(t("link", {"kind": "part_of", "other": 5}, "is now part of Task-5"), ev=F["s_any"]), None)], R.SAID["link"]),
        ("waits", "t:1", "waits:{s_any}", None, lambda F: [("rel:1:waits_on:5", dict(t("link", {"kind": "waits_on", "other": 5}, "now waits on Task-5"), ev=F["s_any"]), None)], R.SAID["link"]),
        ("merge", "t:1", "merge:{s_same}", None, lambda F: [("rel:1:same_as:4", dict(t("merge", {"into": 4}, "merged into Task-4"), ev=F["s_same"]), None)], R.SAID["link"]),
        # "As suggested" on a same_as suggestion is its merge (the page's one primary button there, Merge into <keep>).
        ("link-same", "t:1", "link:{s_same}", None, lambda F: [("rel:1:same_as:4", dict(t("merge", {"into": 4}, "merged into Task-4"), ev=F["s_same"]), None)], R.SAID["link"]),
        ("same", "t:1", "same:{s_any}", None, lambda F: [("rel:5:same_as:1", {"task_id": 5, "op": "merge", "fields": {"into": 1}, "label": "merged into Task-1", "ev": F["s_any"]}, None)], R.SAID["link"]),
        ("not_related", "t:1", "not_related:{s_any}", None, lambda F: [(f"e:{F['s_any']}", judge(F["s_any"], 1, "dismissed", "not related to Task-5"), None)], ""),
        ("not_same", "t:1", "not_related:{s_same}", None, lambda F: [(f"e:{F['s_same']}", judge(F["s_same"], 1, "dismissed", "not the same as Task-4"), None)], ""),
        ("pin", "t:1", "pin", None, lambda F: [("1:top", t("pin_top", {}, "pinned"), None)], ""),
        ("longterm", "t:1", "longterm", None, lambda F: [("1:lt", t("set", {"longterm": 1}, "long-term"), None)], ""),
        ("due", "t:1", "due", {"due": "2026-11-25"}, lambda F: [("1:due", t("set", {"due": "2026-11-25"}, "due Wed Nov 25"), None)], R.SAID["due"]),
        ("due-none", "t:1", "due", {"due": None}, lambda F: [("1:due", t("set", {"due": None}, "due date removed"), None)], R.SAID["due_removed"]),
        ("remind", "t:1", "remind", {"fire_at": "2026-11-20T09:00", "repeat": "daily", "label": " call Max "},
         lambda F: [("1:r", t("add_reminder", {"fire_at": "2026-11-20T09:00", "label": "call Max", "repeat": "daily"}, "remind Fri 9:00 AM · daily · call Max"), None)], R.SAID["remind"]),
        ("create-planner", "s:{c_plan}", "create", None, lambda F: [(f"e:{F['c_plan']}", {"task_id": None, "op": "create_suggestion", "fields": {"evidence_id": F["c_plan"]},
                                                                                          "label": "new task: Go to the dentist"}, f"s:{F['c_plan']}")], R.SAID["create"]),
        ("create-mail", "s:{c_mail}", "create", None, lambda F: [(f"e:{F['c_mail']}", {"task_id": None, "op": "create_suggestion", "fields": {"evidence_id": F["c_mail"]},
                                                                                       "label": "new task: Send the abstract"}, f"s:{F['c_mail']}")], R.SAID["create"]),
        ("create-slack", "s:{c_slack}", "create", None, lambda F: [(f"e:{F['c_slack']}", {"task_id": None, "op": "create_suggestion", "fields": {"evidence_id": F["c_slack"]},
                                                                                         "label": "new task: lunch tomorrow?"}, f"s:{F['c_slack']}")], R.SAID["create"]),
        ("dismiss-card", "s:{c_plan}", "dismiss", None, lambda F: [(f"e:{F['c_plan']}", judge(F["c_plan"], None, "dismissed", "suggestion dismissed"), f"s:{F['c_plan']}")], ""),
    ]


@pytest.mark.parametrize("case", _cases(), ids=[c[0] for c in _cases()])
def test_every_option_on_every_item_kind_stages_what_the_page_staged(conn, cfg, case):
    _, key, option, args, want, said = case
    F = _world(conn)
    before = conn.total_changes
    p = _plan(conn, cfg, key.format(**F), option.format(**F), args)
    assert conn.total_changes == before and not conn.in_transaction          # the rule reads only
    assert _js(p.puts) == _js(want(F))                                        # keys, entries (key order included), decides, in order
    assert p.dels == [] and p.said == said
    for text in [e.get("label") or "" for _, e, _ in p.puts] + [p.said]:
        assert not FORBIDDEN.search(text), text


def test_a_decision_judges_every_pending_evidence_except_what_is_already_staged(conn, cfg, world):
    F = world
    beside = (f"e:{F['thr']}", {"task_id": 1, "op": "judge_evidence", "fields": {"evidence_id": F["thr"], "verdict": "dismissed"}, "label": "x"}, None)
    earlier = (f"e:{F['pin']}", {"task_id": 1, "op": "judge_evidence", "fields": {"evidence_id": F["pin"], "verdict": "reviewed"}, "label": "suggestion seen"}, D)
    p = _plan(conn, cfg, "t:1", "keep", None, tray_of(beside, earlier))
    keys = [k for k, _, _ in p.puts]
    assert f"e:{F['thr']}" not in keys                                         # staged beside: already answered
    assert f"e:{F['pin']}" in keys                                             # an earlier decision's judge is judged again
    assert keys == ["1"] + [k for k, _, _ in J(F, skip=("thr",))]


def test_later_dates_are_the_pages():
    days = lambda d: [c["until"] for c in R.later_choices(d)]                  # noqa: E731
    assert days(date(2026, 11, 19)) == ["2026-11-20", "2026-11-20", "2026-11-23"]      # a Thursday
    assert days(date(2026, 11, 20)) == ["2026-11-21", "2026-11-27", "2026-11-23"]      # a Friday: the next Friday is a week on
    assert days(date(2026, 11, 22)) == ["2026-11-23", "2026-11-27", "2026-11-23"]      # a Sunday
    assert [c["id"] for c in R.later_choices(TODAY)] == ["tomorrow", "fri", "nextweek"]
    assert [c["label"] for c in R.later_choices(TODAY)] == ["tomorrow", "Friday", "next week"]


def test_later_until_today_or_earlier_is_refused(conn, cfg, world):
    for until in ("2026-11-19", "2026-11-18", "2026-11-19T10:00", "someday", None, 5):
        with pytest.raises(R.Refused):
            _plan(conn, cfg, "t:1", "later", {"until": until})
    assert _plan(conn, cfg, "t:1", "later", {"until": "2026-11-20"}).puts[0][1]["fields"]["snoozed_until"] == "2026-11-20"


def test_a_long_note_on_done_goes_to_the_text_and_says_so(conn, cfg, world):
    note = "é" * 300                                                           # 300 characters is an outcome
    p = _plan(conn, cfg, "t:1", "done", {"note": note})
    assert p.puts[0][1]["fields"] == {"outcome": note} and "1:note" not in [k for k, _, _ in p.puts] and p.said == ""
    p = _plan(conn, cfg, "t:1", "done", {"note": note + "é"})
    assert p.puts[0][1]["fields"] == {} and p.puts[1][:1] == ("1:note",) and p.puts[1][1]["fields"] == {"text": note + "é"}
    assert p.puts[1][2] == D and "300 characters" in p.said and "text" in p.said
    with pytest.raises(R.Refused):
        _plan(conn, cfg, "t:1", "done", {"note": "x" * (R.NOTE_MAX + 1)})


def test_deciding_again_replaces_the_decision_and_its_note_and_keeps_what_is_beside(conn, cfg, world):
    F = world
    tray = after(TRAY0, _plan(conn, cfg, "t:1", "done", {"note": LONG}))
    tray = after(tray, _plan(conn, cfg, "t:1", "due", {"due": "2026-11-30"}, tray))
    tray = after(tray, _plan(conn, cfg, "t:1", "pin", None, tray))
    p = _plan(conn, cfg, "t:1", "keep", None, tray)
    assert p.dels == ["1:note"]
    tray = after(tray, p)
    rows = {k: (e, m["decides"]) for k, e, m in tray["entries"]}
    assert rows["1"] == ({"task_id": 1, "op": "keep", "fields": {}, "label": "kept"}, D)
    assert "1:note" not in rows and rows["1:due"][1] is None and rows["1:top"][1] is None
    assert [k for k, _, _ in tray["entries"]][0] == "1"                        # the decision keeps its place
    assert {k for k, (_, d) in rows.items() if d == D} == {"1"} | {k for k, _, _ in J(F)}


def test_keep_after_an_accepted_done_turns_its_verdict_into_reviewed(conn, cfg, world):
    F = world
    tray = after(TRAY0, _plan(conn, cfg, "t:1", f"accept:{F['done']}"))
    assert dict((k, e) for k, e, _ in tray["entries"])[f"e:{F['done']}"]["fields"]["verdict"] == "applied"
    tray = after(tray, _plan(conn, cfg, "t:1", "keep", None, tray))
    rows = {k: e for k, e, _ in tray["entries"]}
    assert rows["1"]["op"] == "keep" and rows[f"e:{F['done']}"]["fields"]["verdict"] == "reviewed"
    assert rows[f"e:{F['done']}"]["label"] == "suggestion seen"


def test_undecide_removes_the_rows_that_decide_the_item_and_nothing_else(conn, cfg, world):
    F = world
    tray = after(TRAY0, _plan(conn, cfg, "t:1", "pin"))
    tray = after(tray, _plan(conn, cfg, "t:1", "keep", {"note": "Ask Max"}, tray))
    tray = after(tray, _plan(conn, cfg, f"s:{F['c_plan']}", "create", None, tray))
    p = _plan(conn, cfg, "t:1", "undecide", None, tray)
    assert p.puts == [] and set(p.dels) == {"1", "1:note"} | {k for k, _, _ in J(F)}
    assert [k for k, _, _ in after(tray, p)["entries"]] == ["1:top", f"e:{F['c_plan']}"]
    assert _plan(conn, cfg, "t:2", "undecide", None, tray).dels == []          # nothing decides Task-2


def test_pin_and_longterm_stage_beside_and_come_off_when_pressed_again(conn, cfg, world):
    tray = after(TRAY0, _plan(conn, cfg, "t:1", "pin"))
    tray = after(tray, _plan(conn, cfg, "t:1", "longterm", None, tray))
    assert [(k, m["decides"]) for k, _, m in tray["entries"]] == [("1:top", None), ("1:lt", None)]
    p = _plan(conn, cfg, "t:1", "pin", None, tray)
    assert p.puts == [] and p.dels == ["1:top"]
    p = _plan(conn, cfg, "t:1", "longterm", None, tray)
    assert p.puts == [] and p.dels == ["1:lt"]
    assert _plan(conn, cfg, "t:1", "pin", {"on": False}).puts == []           # as the task is already: nothing
    conn.execute("UPDATE tasks SET top_rank = 2, longterm = 1 WHERE id = 1")
    conn.commit()
    assert _js(_plan(conn, cfg, "t:1", "pin").puts) == _js([("1:top", {"task_id": 1, "op": "unpin", "fields": {}, "label": "unpinned"}, None)])
    assert _js(_plan(conn, cfg, "t:1", "longterm").puts) == _js([("1:lt", {"task_id": 1, "op": "set", "fields": {"longterm": 0}, "label": "not long-term"}, None)])
    assert _plan(conn, cfg, "t:1", "pin", {"on": True}).puts == []
    with pytest.raises(R.Refused):
        _plan(conn, cfg, "t:1", "pin", {"on": "yes"})


def test_accept_of_an_op_the_card_cannot_stage_is_refused(conn, cfg, world):
    F = world
    for option in (f"accept:{F['reopen']}", f"accept:{F['thr']}", f"accept:{F['mail']}", f"follow:{F['pin']}", f"not_about:{F['done']}",
                   f"link:{F['s_any']}", "dismiss", "create", "remind", "due"):
        with pytest.raises(R.Refused):
            _plan(conn, cfg, "t:1", option, {})
    for option in ("done", "keep", "pin", f"accept:{F['pin']}", f"dismiss:{F['c_plan']}"):
        with pytest.raises(R.Refused):
            _plan(conn, cfg, f"s:{F['c_plan']}", option)


def test_an_answered_suggestion_is_stale_and_a_judged_card_is_gone(conn, cfg, world):
    F = world
    conn.execute("UPDATE evidence SET judged_at = ?, verdict = 'dismissed' WHERE id IN (?, ?, ?)", (now_iso(), F["pin"], F["s_out"], F["c_plan"]))
    conn.commit()
    with pytest.raises(R.Stale):
        _plan(conn, cfg, "t:1", f"accept:{F['pin']}")
    with pytest.raises(R.Stale):
        _plan(conn, cfg, "t:1", f"link:{F['s_out']}")
    with pytest.raises(R.Stale):
        _plan(conn, cfg, "t:1", "dismiss:999999")
    with pytest.raises(R.Gone):
        R.load_item(conn, cfg, TODAY, f"s:{F['c_plan']}")
    with pytest.raises(R.Gone):
        R.load_item(conn, cfg, TODAY, f"s:{F['thr']}")                      # evidence on a task is no card
    mk(conn, 9, status="done")
    for key in ("t:9", "t:999"):
        with pytest.raises(R.Gone):
            R.load_item(conn, cfg, TODAY, key)


def test_merge_keeps_the_suggestions_survivor_else_the_default(conn, cfg, world):
    F = world
    # Tasks 1 and 4 are as old as each other, so the default keeps the lower id; the suggestion says 4 stays.
    assert _plan(conn, cfg, "t:1", f"merge:{F['s_same']}").puts[0][:1] == ("rel:1:same_as:4",)
    assert _plan(conn, cfg, "t:1", f"same:{F['s_same']}").puts[0][:1] == ("rel:4:same_as:1",)
    assert _plan(conn, cfg, "t:1", f"merge:{F['s_any']}").puts[0][:1] == ("rel:5:same_as:1",)    # an `any` suggestion keeps no one
    conn.execute("UPDATE tasks SET source = 'Canvas' WHERE id = 5")
    conn.commit()
    p = _plan(conn, cfg, "t:1", f"same:{F['s_any']}")
    assert p.puts[0][0] == "rel:1:same_as:5" and p.puts[0][1]["fields"] == {"into": 5} and p.puts[0][1]["task_id"] == 1


def test_a_second_answer_to_a_suggested_link_replaces_the_first(conn, cfg, world):
    F = world
    tray = after(TRAY0, _plan(conn, cfg, "t:1", f"link:{F['s_out']}"))
    p = _plan(conn, cfg, "t:1", f"not_related:{F['s_out']}", None, tray)
    assert p.dels == ["rel:1:waits_on:2"] and [k for k, _, _ in p.puts] == [f"e:{F['s_out']}"]


def test_create_on_a_card_is_staged_with_its_cleaned_draft_and_refuses_an_empty_name(conn, cfg, world):
    F = world
    eid = F["c_plan"]
    raw = {"fields": {"name": "  Dentist at 9 ", "project": "", "effort": "XL", "due": "2026-11-26"},
           "links": [{"kind": "waits_on", "other": 2}, {"kind": "bogus", "other": 3}, {"kind": "part_of", "other": 999}]}
    p = _plan(conn, cfg, f"s:{eid}", "create", {"draft": raw})
    (key, entry, decides), = p.puts
    assert key == f"e:{eid}" and decides == f"s:{eid}" and entry["label"] == "new task: Dentist at 9"
    assert entry["fields"] == {"evidence_id": eid, "draft": {"fields": {"name": "Dentist at 9", "project": None, "due": "2026-11-26"},
                                                             "links": [{"kind": "waits_on", "other": 2}]}}
    # Cleaned once here and again by Create: the same changes as the draft the page sent.
    once = review_cards.card_changes(conn, cfg, eid, entry["fields"]["draft"], TODAY).changes
    assert once == review_cards.card_changes(conn, cfg, eid, raw, TODAY).changes
    for bad in ({"fields": {"name": "   "}}, {"fields": {"name": None}}):
        with pytest.raises(R.Refused, match="a task needs a name"):
            _plan(conn, cfg, f"s:{eid}", "create", {"draft": bad})
    for bad in ("x", {"fields": "x"}, {"links": {}}):
        with pytest.raises(R.Refused):
            _plan(conn, cfg, f"s:{eid}", "create", {"draft": bad})
    assert "draft" not in _plan(conn, cfg, f"s:{eid}", "create", {"draft": {}}).puts[0][1]["fields"]


def test_create_and_dismiss_on_a_card_replace_each_other(conn, cfg, world):
    eid = world["c_plan"]
    tray = after(TRAY0, _plan(conn, cfg, f"s:{eid}", "create"))
    p = _plan(conn, cfg, f"s:{eid}", "dismiss", None, tray)
    assert p.dels == [] and [(k, d) for k, _, d in p.puts] == [(f"e:{eid}", f"s:{eid}")]
    tray = after(tray, p)
    assert [(k, e["op"], e["fields"].get("verdict")) for k, e, _ in tray["entries"]] == [(f"e:{eid}", "judge_evidence", "dismissed")]
    tray = after(tray, _plan(conn, cfg, f"s:{eid}", "create", None, tray))
    assert [(k, e["op"]) for k, e, _ in tray["entries"]] == [(f"e:{eid}", "create_suggestion")]
    p = _plan(conn, cfg, f"s:{eid}", "undecide", None, tray)
    assert p.puts == [] and p.dels == [f"e:{eid}"]


def _reply(conn, origin, task_id, proposals, role="assistant"):
    th = conn.execute("INSERT INTO chat_threads (title, created_at, origin, task_id) VALUES ('t', ?, ?, ?)", (now_iso(), origin, task_id)).lastrowid
    mid = conn.execute("INSERT INTO chat_messages (thread_id, role, text, proposals_json, created_at) VALUES (?, ?, 'ok', ?, ?)",
                       (th, role, json.dumps(proposals), now_iso())).lastrowid
    conn.commit()
    return mid


def _bare(puts):
    return _js([(k, {x: v for x, v in e.items() if x != "prop"}, d) for k, e, d in puts])


def test_a_decision_from_the_cards_conversation_is_the_buttons_decision(conn, cfg, world):
    F = world
    props = [{"op": "done", "task_id": 1, "fields": {"outcome": "Sent it"}, "label": "done Task-1", "evidence": [], "applied": False},
             {"op": "set", "task_id": 1, "fields": {"snoozed_until": "2026-11-23", "due": "2026-11-30"}, "label": "later", "evidence": [], "applied": False},
             {"op": "pin_top", "task_id": 1, "fields": {}, "label": "pin", "evidence": [], "applied": False},
             {"op": "longterm", "task_id": 1, "fields": {}, "label": "long-term", "evidence": [], "applied": False},
             {"op": "drop", "task_id": 1, "fields": {}, "label": "drop", "evidence": [], "applied": False},
             {"op": "add_note", "task_id": 1, "fields": {"text": "x"}, "label": "note", "evidence": [], "applied": False},
             {"op": "done", "task_id": 2, "fields": {}, "label": "done Task-2", "evidence": [], "applied": False},
             {"op": "set", "task_id": 1, "fields": {"snoozed_until": "2026-11-19"}, "label": "today", "evidence": [], "applied": False}]
    mid = _reply(conn, "review", 1, props)
    item = R.load_item(conn, cfg, TODAY, "t:1")

    def chat_plan(i):
        p = R.stored_proposal(conn, 1, mid, i)
        opts = R.from_proposal(item, p, mid, i, TODAY)
        if opts is None:
            return None
        (option, args), = opts
        return R.plan(conn, cfg, item, option, args, TRAY0, TODAY)

    buttons = [("done", {"note": "Sent it"}), ("later", {"until": "2026-11-23"}), ("pin", None), ("longterm", None), ("drop", None)]
    for i, (option, args) in enumerate(buttons):
        got, want = chat_plan(i), R.plan(conn, cfg, item, option, args, TRAY0, TODAY)
        rows = [r for r in got.puts if r[0] != f"1:c{mid}-1"]
        assert _bare(rows) == _bare(want.puts)                                  # verdicts and last_reviewed_at included
        assert rows[0][1]["prop"] == f"{mid}-{i}"                               # the decision remembers the proposal
    later = chat_plan(1)
    beside = next(r for r in later.puts if r[0] == f"1:c{mid}-1")
    assert beside == (f"1:c{mid}-1", {"task_id": 1, "op": "set", "fields": {"due": "2026-11-30"}, "label": "due Mon Nov 30", "prop": f"{mid}-1"}, None)
    assert later.puts[0][1]["fields"]["last_reviewed_at"] == "2026-11-19"
    assert [chat_plan(i) for i in (5, 6, 7)] == [None, None, None]              # a note, another task, a Later that is no later: as any proposal


def test_a_proposal_from_another_tasks_thread_is_refused(conn, cfg, world):
    props = [{"op": "done", "task_id": 1, "fields": {}, "label": "done", "evidence": [], "applied": False}]
    assert R.stored_proposal(conn, 1, _reply(conn, "task", 1, props), 0)["op"] == "done"      # an opened task shares the card's thread
    for mid in (_reply(conn, "review", 2, props), _reply(conn, "search", None, props), _reply(conn, None, None, props),
                _reply(conn, "review", 1, props, role="user")):
        with pytest.raises(R.Refused):
            R.stored_proposal(conn, 1, mid, 0)
    good = _reply(conn, "review", 1, props)
    for mid, i in ((good, 1), (good, -1), (str(good), 0), (good, True), (999999, 0)):
        with pytest.raises(R.Refused):
            R.stored_proposal(conn, 1, mid, i)


def test_an_applied_proposal_is_refused(conn, cfg, world):
    """A proposal the server marked applied (a chat create, a followed thread) is never staged again from the reply
    (stage 1 review, note 5: the connector will send from_chat itself)."""
    props = [{"op": "done", "task_id": 1, "fields": {}, "label": "done", "evidence": [], "applied": True},
             {"op": "keep", "task_id": 1, "fields": {}, "label": "keep", "evidence": [], "applied": False}]
    mid = _reply(conn, "review", 1, props)
    with pytest.raises(R.Refused, match="applied already"):
        R.stored_proposal(conn, 1, mid, 0)
    assert R.stored_proposal(conn, 1, mid, 1)["op"] == "keep"


def test_labels_are_the_pages_words():
    assert R.cut60("a" * 60) == "a" * 60 and R.cut60("a" * 61) == "a" * 60 + "…" and R.cut60(None) == ""
    assert R.cut60("😀" * 31) == "😀" * 30 + "…"                                # JavaScript counts UTF-16 units
    assert R.day_words("2026-11-14") == "Sat Nov 14" and R.day_words("2026-11-20T09:00") == "Fri Nov 20" and R.day_words("soon") == "soon"
    assert R.when_words("2026-11-20T09:00") == "Fri 9:00 AM" and R.when_words("2026-11-20T21:05") == "Fri 9:05 PM"
    assert R.when_words("2026-11-21T00:30") == "Sat 12:30 AM" and R.when_words("2026-11-21T12:00") == "Sat 12:00 PM"
    cw = R.change_words
    assert cw({"op": "link", "fields": {"kind": "waits_on", "other": 14}}) == "now waits on Task-14"
    assert cw({"op": "link", "fields": {"kind": "part_of", "other": 14}}) == "is now part of Task-14"
    assert cw({"op": "unlink", "fields": {"kind": "waits_on", "other": 14}}) == "no longer waits on Task-14"
    assert cw({"op": "unlink", "fields": {"kind": "part_of", "other": 14}}) == "is no longer part of Task-14"
    assert cw({"op": "merge", "fields": {"into": 3}}) == "merged into Task-3"
    assert cw({"op": "add_note", "fields": {"text": "hi"}}) == "note “hi”"
    assert cw({"op": "done", "fields": {"outcome": "ok"}}) == "done · “ok”" and cw({"op": "drop", "fields": {"outcome": "no"}}) == "dropped · “no”"
    assert cw({"op": "done", "fields": {}}) == "" and cw({"op": "set", "fields": {"due": "2026-11-20"}}) == ""
    assert R.set_words({"due": "2026-11-20", "name": "N", "context": "c", "project": None, "effort": "S", "hard": 1, "waiting_on": "",
                        "counterpart": "Max", "snoozed_until": "2026-11-23", "why_now": "", "due_is_datetime": 0, "top_rank": 2, "x": None, "y": True}) == (
        "due Fri Nov 20, name “N”, text rewritten, project none, effort S, hard deadline, no longer waiting, for Max, later, back Mon Nov 23, "
        "why now cleared, top_rank 2, x cleared, y true")
    sk = R.stage_key
    assert sk({"task_id": 3, "op": "link", "fields": {"kind": "waits_on", "other": 9}}, "a1") == "rel:3:waits_on:9"
    assert sk({"task_id": 3, "op": "merge", "fields": {"into": 9}}) == "rel:3:same_as:9"
    assert sk({"task_id": 3, "op": "add_note", "fields": {}}) == "3:note" and sk({"task_id": 3, "op": "add_note"}, "a7") == "3:a7"
    assert sk({"task_id": 3, "op": "add_reminder"}) == "3:r" and sk({"task_id": 3, "op": "judge_evidence", "fields": {"evidence_id": 8}}) == "e:8"
    assert sk({"task_id": None, "op": "create"}, "c5-0") == "new:c5-0"
    assert sk({"task_id": 3, "op": "pin_top"}) == "3:top" and sk({"task_id": 3, "op": "unpin"}) == "3:top"
    assert sk({"task_id": 3, "op": "set", "fields": {"top_rank": 1}}) == "3:top" and sk({"task_id": 3, "op": "longterm"}) == "3:lt"
    assert sk({"task_id": 3, "op": "set", "fields": {"longterm": 0}}) == "3:lt" and sk({"task_id": 3, "op": "pin_top"}, "a4") == "3:a4"
    assert sk({"task_id": 3, "op": "done"}) == "3" and sk({"task_id": 3, "op": "set", "fields": {"due": None}}, "due") == "3:due"
    assert sk({"task_id": 3, "op": "set", "fields": {"longterm": 1, "due": None}}) == "3"
    assert R.text_expect({"context": None, "name": "N"}, {"context": "x", "name": "y", "due": 1}) == {"context": "", "name": "N"}
    assert R.text_expect({"context": "c"}, {"due": 1}) is None and R.text_expect(None, {"context": 1}) is None
    for text in list(R.SAID.values()) + list(R.LABELS.values()):
        assert not FORBIDDEN.search(text), text


def test_load_item_builds_what_the_queue_builds(conn, cfg, world):
    F = world
    q = review_queue.items(conn, cfg, TODAY)
    for i in q["tasks"]:
        assert json.dumps(R.load_item(conn, cfg, TODAY, i["key"]), sort_keys=True, default=str) == json.dumps(i, sort_keys=True, default=str)
    one = next(i for i in q["tasks"] if i["key"] == "t:1")
    assert {s["evidence_id"] for s in one["link_suggestions"]} == {F["s_out"], F["s_in"], F["s_same"], F["s_any"]}
    assert [e["id"] for e in one["evidence"]] == [F[n] for n in TASK_EV]
    for c in q["cards"]:
        assert R.load_item(conn, cfg, TODAY, c["key"]) == c
    conn.execute("UPDATE tasks SET snoozed_until = '2099-01-01' WHERE id = 3")      # not on Review today, still an item
    conn.commit()
    assert "t:3" not in {i["key"] for i in review_queue.items(conn, cfg, TODAY)["tasks"]}
    three = R.load_item(conn, cfg, TODAY, "t:3")
    assert three["key"] == "t:3" and three["reasons"] == [] and three["primary"] is None and three["task"]["id"] == 3


def test_parse_option_and_item_key_refuse_what_is_not_theirs():
    assert R.parse_option("accept:190") == ("accept", 190) and R.parse_option("done") == ("done", None)
    assert R.parse_option("dismiss") == ("dismiss", None) and R.parse_option("dismiss:4") == ("dismiss", 4)
    assert R.parse_item_key("t:412") == ("t", 412) and R.parse_item_key("s:186") == ("s", 186)
    assert R.item_key({"task": {"id": 4}}) == "t:4" and R.item_key({"id": 9, "kind": "proposal"}) == "s:9" and R.item_key({"key": "t:2"}) == "t:2"
    for bad in ("accept:x", "accept", "accept:-1", "accept:0", "done:3", "pin:1", "rec", "keep_all", "", None, 3, "accept:1:2"):
        with pytest.raises(R.Refused):
            R.parse_option(bad)
    for bad in ("g:rec:1", "t:-1", "t:0", "t:x", "s:", "x:1", "t:1 ", "412", None, 412, "g:course:COMPE 510"):
        with pytest.raises(R.Refused):
            R.parse_item_key(bad)
    with pytest.raises(R.Refused):
        R.plan(None, {}, {"key": "g:rec:1"}, "keep", None, TRAY0, TODAY)


def test_from_proposal_never_decides_another_task_or_a_card():
    item = {"key": "t:1", "task": {"id": 1}}
    assert R.from_proposal(item, {"op": "done", "task_id": 2}, 1, 0, TODAY) is None
    assert R.from_proposal({"key": "s:4", "id": 4}, {"op": "done", "task_id": 1}, 1, 0, TODAY) is None
    assert R.from_proposal(item, {"op": "done", "task_id": 1, "fields": {"outcome": "  "}}, 7, 2, TODAY) == [("done", {"prop": "7-2"})]
    assert R.from_proposal(item, {"op": "set", "task_id": 1, "fields": {"snoozed_until": "bad"}}, 7, 2, TODAY) is None
    assert R.from_proposal(item, {"op": "unpin", "task_id": 1}, 7, 2, TODAY) is None


# --- stage: load, plan, write the tray (commit 4) -------------------------------------------------------

def test_stage_writes_the_plan_and_does_not_commit(conn, cfg, world, tmp_path):
    from factotum import tray
    from factotum.db import connect
    F = world
    other = connect(tmp_path / "t.db")
    out = R.stage(conn, cfg, "t:1", "done", {"note": "Sent it"}, TODAY, origin="page", device="Phone")
    assert out["keys"] == ["1"] + [f"e:{F[n]}" for n in TASK_EV] and out["removed"] == [] and out["said"] == ""
    assert conn.in_transaction
    assert other.execute("SELECT COUNT(*) FROM tray").fetchone()[0] == 0           # nothing seen before the caller commits
    conn.commit()
    assert other.execute("SELECT COUNT(*) FROM tray").fetchone()[0] == 1 + len(TASK_EV)
    t = tray.read(conn)
    assert t["entries"][0][:2] == ["1", {"task_id": 1, "op": "done", "fields": {"outcome": "Sent it"}, "label": "done · “Sent it”"}]
    assert {m["decides"] for _, _, m in t["entries"]} == {"t:1"} and {m["device"] for _, _, m in t["entries"]} == {"Phone"}
    out = R.stage(conn, cfg, "t:1", "keep", None, TODAY, origin="connector")       # a second decision replaces the first
    conn.commit()
    assert out["keys"][0] == "1" and out["removed"] == []
    assert tray.read(conn)["entries"][0][1]["op"] == "keep"
    out = R.stage(conn, cfg, "t:1", "undecide", None, TODAY, origin="page")
    conn.commit()
    assert tray.read(conn)["entries"] == [] and len(out["removed"]) == 1 + len(TASK_EV)


def test_stage_from_chat_reads_the_stored_proposal_not_the_page(conn, cfg, world):
    from factotum import tray
    props = [{"op": "done", "task_id": 1, "fields": {"outcome": "Sent it"}, "label": "done", "evidence": [], "applied": False},
             {"op": "add_note", "task_id": 1, "fields": {"text": "x"}, "label": "note", "evidence": [], "applied": False}]
    mid = _reply(conn, "review", 1, props)
    # the page's option and args are ignored, and so are prop / also it might send
    R.stage(conn, cfg, "t:1", "keep", {"note": "not this", "prop": "forged", "also": {"name": "x"}}, TODAY, origin="page",
            from_chat={"message_id": mid, "index": 0})
    conn.commit()
    first = tray.read(conn)["entries"][0]
    assert first[:2] == ["1", {"task_id": 1, "op": "done", "fields": {"outcome": "Sent it"}, "label": "done · “Sent it”", "prop": f"{mid}-0"}]
    with pytest.raises(R.Refused):                                                  # a note is not a decision: the page stages it as any proposal
        R.stage(conn, cfg, "t:1", None, None, TODAY, origin="page", from_chat={"message_id": mid, "index": 1})
    with pytest.raises(R.Refused):                                                  # a reply about Task-1 never decides Task-2
        R.stage(conn, cfg, "t:2", None, None, TODAY, origin="page", from_chat={"message_id": mid, "index": 0})
    with pytest.raises(R.Refused):                                                  # a card's conversation is not this
        R.stage(conn, cfg, f"s:{world['c_plan']}", None, None, TODAY, origin="page", from_chat={"message_id": mid, "index": 0})
    conn.rollback()
    # prop and also from a caller are dropped: a client cannot mark a proposal applied through stage
    R.stage(conn, cfg, "t:2", "keep", {"prop": "forged"}, TODAY, origin="page")
    conn.commit()
    two = next(e for k, e, _ in tray.read(conn)["entries"] if k == "2")
    assert "prop" not in two
