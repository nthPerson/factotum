"""The Search tab's engine (factotum/search.py): words, close spellings, dates, filters, counts, order."""
from datetime import date

from fastapi.testclient import TestClient

from factotum import api, search
from factotum.db import connect, init_db, upsert_task
from conftest import FIXTURE, TODAY, task                    # TODAY is Sunday 2026-11-01
from stage4_helpers import link


def _seed(conn):
    rows_ = [
        task(id=1, name="Submit the IRB application", project="BDA 696", due="2026-11-10", hard=1, effort="M", source="Plaud",
             context="Reza wants the consent form attached.\nPilot starts after approval.", counterpart="Reza"),
        task(id=2, name="Driving simulator pilot plan", project="BDA 696", due="2026-11-20", effort="L", source="Plaud",
             context="Twelve billboards in two groups; schedule the pilot subjects.", waiting_on="Molina"),
        task(id=3, name="Email the lab the dataset", project="Hardware Lab", effort="S", source="Manual",
             context="The résumé of the capture session goes with it."),
        task(id=4, name="Quiz 5", project="COMPE 510", status="done", completed_at="2026-10-20T18:00:00+00:00", source="Canvas",
             outcome="Scored 9 of 10 on the simulator questions."),
        task(id=5, name="Prioritize the proposal backlog", project="Personal", status="dropped", completed_at="2026-10-28T03:00:00+00:00",
             source="Planner", closed_by="review"),
        task(id=6, name="Renew the parking permit", project=None, due="2026-12-01", source="Mail", top_rank=1, longterm=1,
             snoozed_until="2026-11-15", created_at="2026-10-30T23:30:00+00:00"),
    ]
    for t in rows_:
        upsert_task(conn, t)
    conn.commit()
    link(conn, 2, 1, "waits_on")
    conn.execute("INSERT INTO reminders (task_id, fire_at, status, created_at) VALUES (3, '2026-11-02T09:00', 'pending', '2026-10-01')")
    conn.commit()


def S(conn, cfg, **kw):
    return search.run(conn, cfg, TODAY, **kw)


def ids(res):
    return [r["id"] for r in res["results"]]


def test_every_word_must_match_and_the_name_outranks_the_text(conn, cfg):
    _seed(conn)
    r = S(conn, cfg, q="pilot")
    assert ids(r) == [2, 1] and r["total"] == 2 and r["mode"] == "match" and not r["fell_back"]
    assert r["results"][0]["fields"] == ["name"] and r["results"][1]["fields"] == ["context"]
    assert ids(S(conn, cfg, q="pilot reza")) == [1] and ids(S(conn, cfg, q="pilot nothing-like-this")) == []
    assert ids(S(conn, cfg, q="SIMULATOR")) == [2, 4]            # case folded; open before closed on the weight of the field
    assert ids(S(conn, cfg, q="")) == [1, 2, 6, 3, 5, 4]         # nothing typed: open by date, undated last, then closed newest first


def test_accents_phrases_exclusions_and_a_task_number(conn, cfg):
    _seed(conn)
    assert ids(S(conn, cfg, q="resume")) == [3]                  # résumé
    assert ids(S(conn, cfg, q='"consent form"')) == [1] and ids(S(conn, cfg, q='"form consent"')) == []
    assert ids(S(conn, cfg, q="pilot -billboards")) == [1]
    assert ids(S(conn, cfg, q="Task-4")) == [4] and ids(S(conn, cfg, q="#5"))[0] == 5
    assert ids(S(conn, cfg, q="510")) == [4]                     # a number is also words: COMPE 510
    assert search.parse('  a "b c" -d  ')["terms"] == [{"text": "a", "neg": False, "phrase": False}, {"text": "b c", "neg": False, "phrase": True},
                                                       {"text": "d", "neg": True, "phrase": False}]


def test_close_spellings(conn, cfg):
    _seed(conn)
    assert ids(S(conn, cfg, q="simulater", mode="fuzzy")) == [2, 4]
    assert ids(S(conn, cfg, q="aplication irb", mode="fuzzy")) == [1]
    assert ids(S(conn, cfg, q="prioritization", mode="fuzzy")) == [5]        # a shared stem
    back = S(conn, cfg, q="simulater")                           # match found nothing: the close ones, and it says so
    assert ids(back) == [2, 4] and back["fell_back"] and back["mode"] == "fuzzy" and "close spellings" in back["said"]
    assert ids(S(conn, cfg, q="zzzzqq")) == [] and not S(conn, cfg, q="zzzzqq")["fell_back"]
    assert search.closeness("quiz", "quit") == 0.75 and search.closeness("cat", "car") == 0 and search.closeness("transcript", "transfer") == 0
    assert search.closeness("recieve", "receive") > 0.8 and search.closeness("pilot", "pilot") == 1.0


def test_within(conn, cfg):
    _seed(conn)
    assert ids(S(conn, cfg, q="reza", within="people")) == [1] and ids(S(conn, cfg, q="reza", within="name")) == []
    assert ids(S(conn, cfg, q="simulator", within="text")) == [4] and ids(S(conn, cfg, q="molina")) == [2]


def test_filters_and_their_counts(conn, cfg):
    _seed(conn)
    r = S(conn, cfg, status=["open"])
    assert ids(r) == [1, 2, 6, 3] and r["facets"]["status"] == {"open": 4, "done": 1, "dropped": 1}      # its own filter is left out of its counts
    assert r["facets"]["project"] == {"BDA 696": 2, "Hardware Lab": 1, "": 1}
    assert ids(S(conn, cfg, status="done,dropped")) == [5, 4]
    assert ids(S(conn, cfg, project=["BDA 696", "(none)"])) == [1, 2, 6]
    assert ids(S(conn, cfg, source=["Canvas"])) == [4] and ids(S(conn, cfg, effort=["S", "L"])) == [2, 3]
    assert ids(S(conn, cfg, status=["nonsense"])) == [1, 2, 6, 3, 5, 4]      # an unknown value is no filter
    for flag, want in (("hard", [1]), ("undated", [3, 5, 4]), ("waiting", [2]), ("top", [6]), ("longterm", [6]), ("linked", [1, 2]), ("later", [6]), ("reminder", [3])):
        assert ids(S(conn, cfg, flags=[flag])) == want, flag
    assert ids(S(conn, cfg, flags="hard,linked")) == [1]


def test_date_ranges_are_days_where_the_owner_is(conn, cfg):
    _seed(conn)
    cfg["owner"]["timezone"] = "America/Los_Angeles"
    assert ids(S(conn, cfg, date_from="2026-11-10", date_to="2026-11-20")) == [1, 2]
    assert ids(S(conn, cfg, date_to="2026-11-10")) == [1] and ids(S(conn, cfg, date_from="2026-11-21")) == [6]
    assert ids(S(conn, cfg, date_from="2026-11-20", date_to="2026-11-10")) == [1, 2]       # the two ends swapped
    # Task-5 was closed at 03:00 UTC on the 28th: the evening of the 27th in Los Angeles
    assert ids(S(conn, cfg, date_field="closed", date_from="2026-10-27", date_to="2026-10-27")) == [5]
    assert ids(S(conn, cfg, date_field="closed", date_from="2026-10-28")) == []
    assert ids(S(conn, cfg, date_field="created", date_from="2026-10-30")) == [6]
    assert "closed 2026-10-27 to 2026-10-27" in S(conn, cfg, date_field="closed", date_from="2026-10-27", date_to="2026-10-27")["said"]


def test_order_pages_and_what_a_row_carries(conn, cfg):
    _seed(conn)
    assert ids(S(conn, cfg, sort="due")) == [1, 2, 6, 3, 5, 4]
    assert ids(S(conn, cfg, sort="closed"))[:2] == [5, 4] and ids(S(conn, cfg, sort="created"))[0] == 6
    one = S(conn, cfg, limit=2)
    two = S(conn, cfg, limit=2, offset=2)
    assert ids(one) == [1, 2] and one["more"] and ids(two) == [6, 3] and two["total"] == 6
    r = S(conn, cfg, q="consent")["results"][0]
    sn = r["snippet"]
    assert sn["field"] == "context" and "\n" not in sn["text"] and [sn["text"][a:b] for a, b in sn["spans"]] == ["consent"]
    assert "context" not in r and r["name_spans"] == [] and r["merged_into"] is None
    n = S(conn, cfg, q="irb")["results"][0]
    assert [n["name"][a:b] for a, b in n["name_spans"]] == ["IRB"] and n["snippet"] is None


def test_a_long_text_is_cut_around_the_match(conn, cfg):
    upsert_task(conn, task(id=9, name="Long", context=("word " * 60) + "needle in here " + ("tail " * 60)))
    conn.commit()
    sn = S(conn, cfg, q="needle")["results"][0]["snippet"]
    assert sn["text"].startswith("…") and sn["text"].endswith("…") and len(sn["text"]) < 200
    assert [sn["text"][a:b] for a, b in sn["spans"]] == ["needle"]


def test_said_and_the_route(cfg, tmp_path):
    path = tmp_path / "f.db"
    c = connect(path); init_db(c); _seed(c); c.close()
    client = TestClient(api.create_app(cfg, db_path=str(path)))
    r = client.get("/api/search/advanced", params={"q": "pilot", "status": ["open", "done"], "project": ["BDA 696"], "flags": ["linked"], "sort": "due"}).json()
    assert [x["id"] for x in r["results"]] == [1, 2] and r["query"]["status"] == ["open", "done"] and r["query"]["flags"] == ["linked"]
    assert r["said"] == "“pilot” · open or done · project BDA 696 · only with linked to another task"
    assert client.get("/api/search/advanced").json()["said"] == "every task"
    assert client.get("/search").status_code == 200              # the tab's own address
    assert client.get("/api/search", params={"q": "pilot"}).json()["results"][0]["id"] in (1, 2)     # the picker's search is as it was


def test_the_agents_tool_searches_the_same_way_and_marks_what_it_saw(conn, cfg, tmp_path):
    import json
    from factotum import chat, mcp_server as M
    _seed(conn)
    ctx = M.Context(connect(tmp_path / "t.db"), cfg, today=TODAY, run_dir=tmp_path)
    call = lambda **a: json.loads(M.dispatch(ctx, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "tasks_search", "arguments": a}})["result"]["content"][0]["text"])   # noqa: E731
    r = call(q="simulater")
    assert [t["id"] for t in r["tasks"]] == [2, 4] and r["fell_back"] and r["total"] == 2 and r["cut"] == 0
    assert r["tasks"][1]["closed"] == "2026-10-20" and "outcome" in r["tasks"][1] and "snippet" in r["tasks"][1] and "closed" not in r["tasks"][0]
    assert [t["id"] for t in call(status=["done", "dropped"], date_field="closed", date_from="2026-10-25")["tasks"]] == [5]
    assert M.tasks_seen(tmp_path) >= {2, 4, 5}
    t = next(t for t in M.TOOLS if t["name"] == "tasks_search")
    assert t["annotations"] == M.READ_ONLY and not t["skill_only"] and "tasks_search" in chat.FRIENDLY and "mcp__factotum__tasks_search" in chat.allowed_tools()
    assert not {"blocked", "dependency", "dependencies"} & set(t["description"].lower().replace(",", " ").replace(".", " ").split())
    bad = M.dispatch(ctx, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "tasks_search", "arguments": {"status": 3}}})
    assert bad["result"]["isError"]


def test_the_search_command(conn, cfg, tmp_path, capsys, monkeypatch):
    from factotum import __main__ as cli
    _seed(conn)
    monkeypatch.setenv("FACTOTUM_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("FACTOTUM_CONFIG", str(FIXTURE))
    monkeypatch.setattr("sys.argv", ["factotum", "search", "simulater", "--status", "open", "done"])
    cli.main()
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("2 matches: “simulater” (close spellings too) · open or done") and "nothing as written" in out.splitlines()[0]
    assert "Task-2  open due 2026-11-20  Driving simulator pilot plan" in out and "Task-4  done 2026-10-20  Quiz 5" in out and "Scored 9 of 10" in out
