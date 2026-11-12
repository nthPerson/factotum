"""The final review's findings on configuration, the deploy check, the documents and the skills (2026-11-11)."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

from factotum import config as C
from factotum.db import connect, init_db

ROOT = C.ROOT


# --- 1. links-check reads only: no init_db, so no migration of a database the old service still runs on

def _old_db_module(tmp_path):
    """`factotum/db.py` as the release before linked tasks had it (main at f95e390)."""
    try:
        text = subprocess.run(["git", "-C", str(ROOT), "show", "f95e390:factotum/db.py"], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git history not available here")
    p = tmp_path / "old_db.py"
    p.write_text(text, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("old_db_f95e390", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _files(d: Path) -> set[str]:
    return {str(p.relative_to(d)) for p in d.rglob("*")}


def test_links_check_on_a_database_from_before_this_release_migrates_nothing_and_says_how_to_preview(tmp_path, monkeypatch, capsys):
    from conftest import FIXTURE
    from factotum.__main__ import main
    old = _old_db_module(tmp_path)
    data = tmp_path / "data"
    db = data / "factotum.db"
    c = old.connect(db)
    old.init_db(c)
    now = "2026-10-01T00:00:00+00:00"
    for i in (1, 2):
        c.execute("INSERT INTO tasks (id, name, status, created_at, edited_at) VALUES (?, ?, 'open', ?, ?)", (i, f"t{i}", now, now))
    c.execute("INSERT INTO links (task_id, kind, ref, created_at) VALUES (2, 'task', '1', ?)", (now,))   # the import's kind the migration rewrites
    c.commit()
    c.close()
    before, listing = db.read_bytes(), _files(data)
    monkeypatch.setenv("FACTOTUM_CONFIG", str(FIXTURE)); monkeypatch.setenv("FACTOTUM_DB", str(db))
    capsys.readouterr()
    assert main(["links-check"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert db.read_bytes() == before and _files(data) == listing                 # no -wal, -shm or backups/ left
    assert len(out) == 1 and "not migrated yet" in out[0]
    assert "s.backup(d)" in out[0] and f"FACTOTUM_DB=" in out[0] and "init-db" in out[0] and out[0].endswith("links-check")


def test_links_check_on_a_migrated_database_reads_it_and_writes_nothing(tmp_path, monkeypatch, capsys):
    from conftest import FIXTURE
    from factotum.__main__ import main
    from stage4_helpers import mk
    data = tmp_path / "data"
    db = data / "factotum.db"
    c = connect(db); init_db(c)
    mk(c, 1, name="Take COMPE 510 Quiz 7", source="Canvas", due="2026-11-10T23:59"); mk(c, 2, name="Quiz 7 prep", source="Plaud", due="2026-11-10")
    c.commit(); c.close()
    before, listing = db.read_bytes(), _files(data)
    monkeypatch.setenv("FACTOTUM_CONFIG", str(FIXTURE)); monkeypatch.setenv("FACTOTUM_DB", str(db))
    capsys.readouterr()
    assert main(["links-check"]) == 0
    out = capsys.readouterr().out
    assert "twin    Task-2 → Task-1  same as" in out and "would write" in out and "twin 1 (1 would write)" in out
    assert db.read_bytes() == before and _files(data) == listing


def test_links_check_never_creates_a_database(tmp_path, monkeypatch, capsys):
    from factotum.__main__ import main
    monkeypatch.setenv("FACTOTUM_DB", str(tmp_path / "none" / "factotum.db"))
    assert main(["links-check"]) == 0
    assert "no database" in capsys.readouterr().out and not (tmp_path / "none").exists()


# --- 2. a create's ref / links beside fields are read as its fields'; any other key a change carries is named

def _skill_ctx(cfg, tmp_path, **kw):
    from factotum import mcp_server as M
    from conftest import TODAY
    return M.Context(connect(tmp_path / "t.db"), cfg, today=TODAY, run_dir=tmp_path / "run", writer=lambda: connect(tmp_path / "t.db"), **kw)


def _tool(ctx, name, **args):
    import json
    from factotum import mcp_server as M
    resp = M.dispatch(ctx, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    text = "".join(c["text"] for c in resp["result"]["content"] if c["type"] == "text")
    assert not resp["result"]["isError"], text
    return json.loads(text)


SPLIT_BESIDE = [{"op": "create", "ref": "s1", "links": [{"kind": "part_of", "other": 1}], "fields": {"name": "Draft section 3.1"}},
                {"op": "create", "ref": "s2", "links": [{"kind": "part_of", "other": 1}, {"kind": "waits_on", "other": "s1"}], "fields": {"name": "Draft section 3.2"}}]


def test_a_split_written_with_ref_and_links_beside_fields_is_staged_linked(conn, cfg, tmp_path):
    import json
    from stage4_helpers import mk
    mk(conn, 1, name="Thesis chapter 3")
    res = _tool(_skill_ctx(cfg, tmp_path), "stage_changes", changes=SPLIT_BESIDE)
    assert (res["staged"], res["rejected"], res["ignored"]) == (2, [], [])
    staged = json.loads(conn.execute("SELECT changes_json FROM proposals WHERE id = ?", (res["proposal_id"],)).fetchone()[0])
    assert [c["fields"].get("ref") for c in staged] == ["s1", "s2"]
    assert staged[1]["fields"]["links"] == [{"kind": "part_of", "other": 1}, {"kind": "waits_on", "other": "s1"}]


def test_a_split_written_beside_fields_is_applied_linked_in_apply_mode(conn, cfg, tmp_path):
    from stage4_helpers import active_rels, mk
    mk(conn, 1, name="Thesis chapter 3")
    C.set_(cfg, "server.mcp_writes", "apply")
    res = _tool(_skill_ctx(cfg, tmp_path, apply_writes=True, source="connector"), "apply_changes", changes=SPLIT_BESIDE)
    assert res["rejected"] == [] and res["ignored"] == [] and res["applied"] >= 2
    new = {r[1]: r[0] for r in conn.execute("SELECT id, name FROM tasks WHERE id > 1")}
    a, b = new["Draft section 3.1"], new["Draft section 3.2"]
    assert active_rels(conn) == {(a, 1, "part_of"), (b, 1, "part_of"), (b, a, "waits_on")}


def test_a_ref_given_twice_differently_rejects_the_change_and_any_other_stray_key_is_named(conn, cfg, tmp_path):
    from factotum import mcp_server as M
    from stage4_helpers import mk
    from conftest import TODAY
    mk(conn, 1, name="Thesis chapter 3")
    ctx = M.Context(connect(tmp_path / "t.db"), cfg, today=TODAY)
    kept, rejected, ignored = M._validate_changes(ctx, [
        {"op": "create", "ref": "s1", "fields": {"name": "Step", "ref": "s9"}},
        {"op": "create", "ref": "s1", "fields": {"name": "Same ref twice, same value", "ref": "s1"}},
        {"op": "done", "task_id": 1, "outcome": "sent", "fields": {}},
        {"op": "set", "task_id": 1, "due": "2026-11-20", "expect": {"due": None}, "fields": {"effort": "S"}}])
    assert len(rejected) == 1 and rejected[0].startswith("#0: ref is given beside fields and in fields")
    assert [k["fields"].get("ref") for k in kept if k["op"] == "create"] == ["s1"]
    assert len(ignored) == 2 and ignored[0].startswith("#2: outcome beside fields is not read") and ignored[1].startswith("#3: due beside fields is not read")
    assert "fields" in ignored[0] and "expect" in ignored[1]                      # it says where the key belongs


def test_chat_reads_a_create_ref_and_links_beside_fields_too(conn, cfg):
    from factotum import chat
    from stage4_helpers import mk
    mk(conn, 1, name="Thesis chapter 3")
    data = {"proposals": [dict(p, label=p["fields"]["name"]) for p in SPLIT_BESIDE] +
                         [{"op": "create", "ref": "s3", "fields": {"name": "Conflicting", "ref": "s4"}, "label": "Conflicting"}]}
    kept, rejected = chat._clean_proposals(conn, cfg, data, {}, tasks_seen=set(), attached=[1])
    assert [p["fields"].get("ref") for p in kept] == ["s1", "s2"]
    assert kept[1]["fields"]["links"] == [{"kind": "part_of", "other": 1}, {"kind": "waits_on", "other": "s1"}]
    assert rejected == ["Conflicting: ref is given beside fields and in fields with different values; give it once, in fields"]


# --- 5. a run kind the config names no budget or timeout for runs on the documented caps, not $1.00 / 600 s

def _flag(cmd, name):
    return cmd[cmd.index(name) + 1]


def test_a_config_without_budgets_runs_each_kind_on_the_examples_caps(tmp_path):
    import yaml
    from factotum.claude import runner
    ex = yaml.safe_load(C.EXAMPLE_CONFIG.read_text(encoding="utf-8"))["claude"]
    assert runner.BUDGETS_USD == ex["budgets_usd"] and runner.TIMEOUTS_S == ex["timeouts_s"]   # one table, pinned to the example
    assert runner.MODELS == dict(ex["models"], ripple=ex["ripple"]["model"])                     # and the models (polish H8), ripple's from its own key
    bare = {"claude": {"binary": "claude"}}                                       # an install's config from before this release names none
    seen = []

    def exec_(cmd, timeout=None, **kw):
        seen.append((cmd, timeout))
        raise runner.subprocess.TimeoutExpired(cmd, timeout)
    for kind in ex["budgets_usd"]:
        runner.run(bare, kind, "p", exec_=exec_)
    by_kind = {k: (float(_flag(c, "--max-budget-usd")), t) for k, (c, t) in zip(ex["budgets_usd"], seen)}
    assert by_kind["filing"] == (0.10, 120) and by_kind["ripple"] == (0.30, 180)
    for kind, usd in ex["budgets_usd"].items():
        assert by_kind[kind] == (usd, ex["timeouts_s"].get(kind, runner.DEFAULT_TIMEOUT_S)), kind
    mine = {"claude": {"budgets_usd": {"filing": 0.07}, "timeouts_s": {"filing": 99}}}   # a config value still wins
    seen.clear()
    runner.run(mine, "filing", "p", exec_=exec_)
    assert (float(_flag(seen[0][0], "--max-budget-usd")), seen[0][1]) == (0.07, 99)
    assert float(_flag(runner.command(bare, "no_such_kind"), "--max-budget-usd")) == runner.DEFAULT_BUDGET_USD


def test_settings_show_the_budgets_the_runner_uses(tmp_path):
    from fastapi.testclient import TestClient
    from factotum.api import create_app
    from factotum.claude import runner
    for key in ("claude.budgets_usd.chat_turn", "claude.budgets_usd.ripple"):
        assert C.DEFAULTS[key] == runner.BUDGETS_USD[key.rsplit(".", 1)[1]], key
    e = TestClient(create_app(C.load(C.STARTER_CONFIG), db_path=str(tmp_path / "s.db"))).get("/api/settings").json()["effective"]
    assert e["claude.budgets_usd.chat_turn"] == float(_flag(runner.command(C.load(C.STARTER_CONFIG), "chat_turn"), "--max-budget-usd")) == 0.60
    assert e["claude.budgets_usd.ripple"] == 0.30


# --- 11. effective's fingerprint of a masked value shows that it changed, and is too short to find it by

def test_effective_fingerprints_a_masked_value_with_two_hex_characters(tmp_path, monkeypatch, capsys):
    import re
    from factotum.__main__ import main
    y = tmp_path / "mine.yaml"
    y.write_text("notify:\n  ntfy: {topic: factotum-7f3a9c2e}\n  morning_brief: {to: me@example.com}\nsources: {calendar: {calendar_id: ''}}\n")
    monkeypatch.setattr(C, "DEFAULT_CONFIG_PATHS", [y])
    monkeypatch.setenv("FACTOTUM_DB", str(tmp_path / "none.db"))
    assert main(["effective", *sorted(C.MASKED)]) == 0
    lines = {l.split()[0]: l.split()[1:] for l in capsys.readouterr().out.splitlines()[2:] if l.strip()}
    for k in ("notify.ntfy.topic", "notify.morning_brief.to"):
        assert lines[k][0] == "set" and re.fullmatch(r"#[0-9a-f]{2}", lines[k][1]), lines[k]
    assert lines["sources.calendar.calendar_id"][0] == "empty"


# --- 10. what factotum/ imports is installed by requirements.txt on its own merits

def _norm(name: str) -> str:
    import re
    return re.sub(r"[-_.]+", "-", name).lower()


def test_every_third_party_import_is_in_requirements_or_a_direct_dependency_of_one_there():
    """A distribution the code imports is named in requirements.txt, or is a direct requirement of one named
    there (pydantic and starlette are fastapi's own API, google-auth is google-auth-oauthlib's). Anything
    further away is installed only by luck: `cryptography` came in through google-auth-oauthlib → google-auth."""
    import ast
    import re
    import sys
    from importlib.metadata import packages_distributions, requires
    named = {_norm(re.split(r"[\[<>=!~; ]", l.strip(), maxsplit=1)[0]) for l in (ROOT / "requirements.txt").read_text().splitlines()
             if l.strip() and not l.startswith("#")}
    direct = set(named)
    for d in named:
        for r in requires(d) or []:
            if "extra ==" not in r:
                direct.add(_norm(re.split(r"[\[<>=!~; (]", r, maxsplit=1)[0]))
    dists = packages_distributions()
    missing = {}
    for path in (ROOT / "factotum").rglob("*.py"):
        for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            mods = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module] if isinstance(n, ast.ImportFrom) and n.level == 0 else []
            for m in mods:
                top = m.split(".")[0]
                if top in sys.stdlib_module_names or top == "factotum":
                    continue
                have = {_norm(d) for d in dists.get(top, [top])}
                if not have & direct:
                    missing.setdefault(top, set()).add(path.name)
    assert missing == {}


# --- 12. Settings refuses a value of the wrong type, or a number below the page's own minimum

def test_settings_refuse_a_wrong_type_or_a_number_below_the_pages_minimum(cfg, tmp_path):
    from fastapi.testclient import TestClient
    from factotum.api import create_app
    client = TestClient(create_app(cfg, db_path=str(tmp_path / "api.db")))
    for values, words in (({"claude.ripple.hourly_cap": 0}, "claude.ripple.hourly_cap must be at least 1"),
                          ({"claude.budgets_usd.ripple": 0}, "claude.budgets_usd.ripple must be at least 0.05"),
                          ({"claude.budgets_usd.chat_turn": "0.5"}, "claude.budgets_usd.chat_turn must be a number"),
                          ({"claude.ripple.enabled": "yes"}, "claude.ripple.enabled must be true or false"),
                          ({"lifecycle.fade_days": "30"}, "lifecycle.fade_days must be a whole number"),
                          ({"lifecycle.fade_days": True}, "lifecycle.fade_days must be a whole number"),
                          ({"notify.morning_brief.to": 5}, "notify.morning_brief.to must be text"),
                          ({"lifecycle.top_cap": 5, "lifecycle.review_cap": 2}, "lifecycle.review_cap must be at least 5")):
        r = client.put("/api/settings", json={"values": values})
        assert r.status_code == 400 and words in r.json()["detail"], (values, r.text)
    assert client.get("/api/settings").json()["overrides"] == {}                 # nothing of a refused request was kept
    ok = {"claude.ripple.hourly_cap": 1, "claude.budgets_usd.ripple": 0.05, "claude.budgets_usd.chat_turn": 2, "links.suggestions_per_review": 0,
          "claude.ripple.enabled": False, "server.mcp_writes": "apply", "lifecycle.fade_days": 30}
    assert client.put("/api/settings", json={"values": ok}).status_code == 200
    assert client.get("/api/settings").json()["overrides"] == ok


def test_the_minimum_settings_holds_is_the_one_the_page_states():
    import re
    page = (ROOT / "factotum" / "web" / "app.js").read_text(encoding="utf-8")
    stated = {k: float(m) for k, m in re.findall(r"num\('([a-z_.]+)', ([0-9.]+), [0-9.]+\)", page)}
    assert stated and {k: float(v) for k, v in C.MINIMA.items()} == stated


def test_the_review_nudge_says_when_nothing_delivered_it(conn, cfg):
    from datetime import date
    from factotum.db import upsert_task
    from factotum.scheduler import Scheduler
    from conftest import task
    upsert_task(conn, task(id=1, due=None, created_at="2026-10-30T00:00:00+00:00", last_mention_at="2026-10-30")); conn.commit()
    C.set_(cfg, "notify.provider", "both")
    quiet = Scheduler(cfg, conn, push=lambda *a, **k: None, device_push=lambda *a, **k: {"sent": 0})._review_push(cfg, date(2026, 11, 1), {})
    assert quiet == "nothing delivered: ntfy is off or has no topic; the app has no device that took it (1 item waiting)"
    half = Scheduler(cfg, conn, push=lambda *a, **k: "m1", device_push=lambda *a, **k: {"sent": 0})._review_push(cfg, date(2026, 11, 1), {})
    assert half.startswith("1 item ") and "the app has no device that took it" in half
