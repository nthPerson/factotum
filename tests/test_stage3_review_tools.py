"""Spoken Review, stage 3 (design §7): the review tools on Factotum's MCP server, so that the Claude app's voice mode can
walk the owner through Review over the claude.ai connector or the stdio skill server. Each tool is a thin wrapper over
the pass, the decision rule and the tray; what is said is the View's `spoken`, through `review_mcp.speakable`; a spoken
day is resolved in Python (`dates.resolve_spoken`)."""
import re
from datetime import date

import pytest

from factotum import dates, review_mcp as RM

D = date(2026, 11, 19)                   # Review's day: a Thursday


# --- commit 1: a spoken day, what may be spoken, a day for a confirmation ------------------------------------

@pytest.mark.parametrize("said, want", [
    ("friday", "2026-11-20"), ("this Friday", "2026-11-20"), ("on Friday.", "2026-11-20"), ("tomorrow", "2026-11-20"),
    ("next week", "2026-11-23"), ("tuesday next week", "2026-11-24"), ("in two weeks", "2026-12-03"), ("in 3 days", "2026-11-22"),
    ("in a week", "2026-11-26"), ("two weeks from now", "2026-12-03"), ("the day after tomorrow", "2026-11-21"),
    ("the 3rd", "2026-12-03"), ("the third", "2026-12-03"), ("the thirtieth", "2026-11-30"), ("the twenty-first", "2026-11-21"),
    ("the 31st", "2026-12-31"), ("december 1st", "2026-12-01"), ("December first", "2026-12-01"), ("the first of december", "2026-12-01"),
    ("1 december", "2026-12-01"), ("dec 1", "2026-12-01"), ("the 3rd of january", "2027-01-03"), ("end of the week", "2026-11-20"),
    ("the weekend", "2026-11-21"), ("next weekend", "2026-11-28"), ("next month", "2026-12-01"), ("2026-12-01", "2026-12-01"),
    ("today", "2026-11-19"), ("the 19th", "2026-11-19"),
])
def test_resolve_spoken_reads_the_days_a_person_says(said, want):
    assert dates.resolve_spoken(said, D) == date.fromisoformat(want)


@pytest.mark.parametrize("said", ["whenever", "someday", "sunshine", "novel 6", "in fifteen days", "2026-02-30", "nov 31", "the fortieth",
                                  "", "   ", "later", "soon", "next", None, 7, "in a while"])
def test_resolve_spoken_gives_none_for_what_is_not_clear(said):
    assert dates.resolve_spoken(said, D) is None


def test_dates_resolve_spoken_leaves_resolve_when_alone():
    """The add line reads `resolve_when`, which stays as it was: the spoken forms are resolve_spoken's alone, and every
    form resolve_when reads gives the same day through both."""
    for s in ("in two weeks", "the 3rd", "this friday", "december 1st", "end of the week", "the weekend"):
        assert dates.resolve_when(s, D) is None and dates.resolve_spoken(s, D) is not None
    for s in ("today", "tomorrow", "fri", "friday", "next fri", "next week", "nextweek", "2026-12-24", "nov 6", "dec 25"):
        assert dates.resolve_when(s, D) == dates.resolve_spoken(s, D)
    assert dates.resolve_when("sunshine", D) == date(2026, 11, 22)        # resolve_when's own reading, untouched


def test_day_said_names_the_weekday_and_the_date():
    assert RM.day_said(date(2026, 11, 20), D) == "Friday, November twentieth"
    assert RM.day_said("2026-12-03", D) == "Thursday, December third"
    assert RM.day_said("2027-01-01", D) == "Friday, January first, 2027"
    assert RM.day_said(None, D) == "" and RM.day_said("soon", D) == ""


def test_speakable_removes_what_must_not_be_said():
    s = RM.speakable("## Ask **Max** about Task-412 by 2026-11-20T14:00 — see [the doc](https://x.example.com/a?b=1), "
                     "write max@example.edu or https://lavin.sdsu.edu/book `now`; id 3f2a9c1b7d4e5f60a1b2 and 123456789012\n\n- next", today=D)
    assert "Task-" not in s and "412" not in s and "2026" not in s and "@" not in s and "http" not in s
    assert not re.search(r"[*_`#\[\]<>|]", s) and "\n" not in s and "  " not in s
    assert "another task" in s and "tomorrow at 2 PM" in s and "the doc" in s and "an address" in s and "a link" in s
    assert "3f2a9c" not in s and "123456789012" not in s
    assert RM.speakable("2026-12-04") == "December fourth"                  # no Review's day: the date itself
    long = RM.speakable("word " * 400, cap=50)
    assert len(long) <= 50 and not long.endswith(" ") and long.split()[-1] == "word"
    assert RM.speakable(None) == "" and RM.speakable("Keep it, or drop?") == "Keep it, or drop?"


# --- commit 2: Context.wake --------------------------------------------------------------------------------

def _rpc(ctx, id_, method, params=None):
    from factotum import mcp_server as M
    msg = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        msg["params"] = params
    return M.dispatch(ctx, msg)


def _text(resp) -> str:
    return "".join(c["text"] for c in resp["result"]["content"] if c["type"] == "text")


def _call(ctx, name, args=None, id_=1):
    r = _rpc(ctx, id_, "tools/call", {"name": name, "arguments": args or {}})["result"]
    return (r["isError"], _text({"result": r}) if r["isError"] else __import__("json").loads(_text({"result": r})))


def test_apply_changes_wakes_the_scheduler_after_a_create_and_only_then(cfg, tmp_path):
    """A connector's direct create is a task filing may place, as after the page's Apply (design §7.2, call 17): the
    context's `wake` runs after a batch that created a task; a batch that created none does not wake it."""
    from factotum import mcp_server as M
    from factotum.db import connect, init_db
    db = tmp_path / "w.db"
    init_db(connect(db))
    woke = []
    ctx = M.Context(connect(db), cfg, writer=lambda: connect(db), apply_writes=True, source="connector", wake=lambda: woke.append(1))
    err, res = _call(ctx, "apply_changes", {"changes": [{"op": "create", "fields": {"name": "Book the dentist", "project": "Personal"}}]})
    assert not err and len(res["created"]) == 1 and woke == [1]
    tid = res["created"][0]["task_id"]
    err, res = _call(ctx, "apply_changes", {"changes": [{"task_id": tid, "op": "set", "fields": {"due": "2026-11-27"}}]}, 2)
    assert not err and res["applied"] == 1 and woke == [1]
    ctx2 = M.Context(connect(db), cfg, writer=lambda: connect(db), apply_writes=True, source="skill")          # no scheduler here: nothing to wake
    err, res = _call(ctx2, "apply_changes", {"changes": [{"op": "create", "fields": {"name": "Call the bank", "project": "Personal"}}]}, 3)
    assert not err and len(res["created"]) == 1


def test_the_http_route_gives_the_context_the_schedulers_wake(cfg, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from factotum.api import create_app
    monkeypatch.setenv("FACTOTUM_MCP_TOKEN", "s3cret")
    app = create_app(cfg, db_path=str(tmp_path / "api.db"))
    client = TestClient(app)
    h = {"Authorization": "Bearer s3cret"}
    client.put("/api/settings", json={"values": {"server.mcp_writes": "apply"}})
    call = lambda i, name: client.post("/mcp", json={"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": "apply_changes", "arguments": {
        "changes": [{"op": "create", "fields": {"name": name, "project": "Personal"}}]}}}, headers=h).json()
    r = call(1, "No scheduler here")                                          # serve --no-scheduler: nothing to wake, and no error
    assert not r["result"]["isError"], r
    woke = []
    app.state.scheduler = SimpleNamespace(wake=lambda: woke.append(1))
    r = call(2, "Book the dentist")
    assert not r["result"]["isError"] and woke == [1], r
