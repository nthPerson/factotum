import re, json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from factotum import config as C, notify
from factotum.api import create_app
from factotum.db import connect, get_settings, init_db, rows, seed_accounts, set_setting, upsert_task
from conftest import FIXTURE, TODAY, task


# --- Task 1: the suite's own fixture

def test_the_suite_reads_its_fixture_not_the_example():
    assert FIXTURE.exists()
    hits = [p.name for p in Path(__file__).parent.glob("*.py") if "factotum.example" in p.read_text(encoding="utf-8") and p.name != "test_stage3.py"]
    assert hits == []


# --- Task 2: the starter

def _flags(cfg):
    """Every `enabled` switch under sources and sinks, plus the mail suggestions."""
    out = {f"{g}.{n}.enabled": C.get(cfg, f"{g}.{n}.enabled") for g in ("sources", "sinks") for n in (cfg.get(g) or {})}
    out["sources.mail.suggest.enabled"] = C.get(cfg, "sources.mail.suggest.enabled")
    return out

def test_without_a_config_of_your_own_the_starter_loads_and_says_so_once(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(C, "DEFAULT_CONFIG_PATHS", [tmp_path / "factotum.yaml", C.STARTER_CONFIG])
    monkeypatch.setattr(C, "_starter_told", False)
    cfg = C.load()
    assert cfg["_starter"] is True and Path(cfg["_path"]) == C.STARTER_CONFIG
    assert capsys.readouterr().err.count("starter settings in use") == 1
    C.load()
    assert "starter settings in use" not in capsys.readouterr().err
    monkeypatch.setenv("FACTOTUM_CONFIG", str(tmp_path / "missing.yaml"))       # a copied .env names a file not made yet
    assert C.load()["_starter"] is True
    (tmp_path / "factotum.yaml").write_text("owner: {name: Ann, timezone: Europe/Paris}\n")
    own = C.load()
    assert own["_starter"] is False and own["owner"]["name"] == "Ann"
    assert C.load(C.STARTER_CONFIG)["_starter"] is True                         # named outright (a chat turn's MCP server)

def test_the_starter_is_neutral_and_switches_everything_off():
    text = C.STARTER_CONFIG.read_text(encoding="utf-8")
    s = C.load(C.STARTER_CONFIG)
    assert "@" not in text and not re.search(r"[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-", text)
    assert not C.get(s, "owner.name") and C.get(s, "owner.emails") == [] and not C.get(s, "sources.mail.seed_accounts")
    projects = C.get(s, "taxonomy.projects")
    assert len(projects) == 3 and len({p["weight"] for p in projects}) == 1
    assert set(s["sources"]) >= {"plaud", "canvas", "mail", "slack", "discord", "calendar"} and set(s["sinks"]) >= {"notion_notes", "notion_tasks_mirror"}
    assert all(v is False for v in _flags(s).values())
    for k in ("notify.ntfy.enabled", "notify.morning_brief.enabled", "claude.planner.enabled", "claude.summary.auto", "server.oauth.enabled"):
        assert C.get(s, k, "absent") is False, k
    assert C.get(s, "server.public_url") == "" and C.get(s, "server.mcp_writes") == "stage"
    assert not C.get(s, "notify.ntfy.topic") and not C.get(s, "notify.morning_brief.to") and s["_tz_from_machine"] is True

def test_a_day_on_the_starter_spends_nothing_and_sends_nothing(conn):
    from factotum.scheduler import Scheduler
    upsert_task(conn, task(id=1, name="x", project="Work", due="2026-11-02", hard=1)); conn.commit()
    calls = []
    rec = lambda name: (lambda *a, **k: calls.append(name) or {"ok": True})            # noqa: E731
    mod = lambda name: SimpleNamespace(sweep_job=rec(name), run=rec(name), pending=lambda *a, **k: True, get=lambda *a, **k: None)   # noqa: E731
    s = Scheduler(C.load(C.STARTER_CONFIG), conn, push=lambda cfg, *a, **k: calls.append("push") if notify.enabled(cfg) else None,
                  mailer=rec("mail"), calendar=mod("calendar"), canvas=mod("canvas"), mail=mod("mail_sweep"), planner=mod("planner"),
                  mirror=mod("mirror"), slack=mod("slack"), summary=mod("summary"), triage=mod("triage"))
    for h, m in ((6, 0), (7, 0), (12, 0), (19, 45)):
        s.tick(datetime(2026, 11, 2, h, m))
    assert calls == []

def test_a_missing_or_wrong_time_zone_becomes_the_machines(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    assert C.machine_timezone() == "Asia/Tokyo"
    y = tmp_path / "a.yaml"
    y.write_text("owner:\n")                                                     # an empty block loads as None
    cfg = C.load(y)
    assert cfg["owner"]["timezone"] == "Asia/Tokyo" and cfg["_tz_from_machine"] is True
    y.write_text("owner: {timezone: Mars/Olympus}\n")
    assert C.load(y)["owner"]["timezone"] == "Asia/Tokyo" and "Mars/Olympus" in capsys.readouterr().err
    y.write_text("owner: {timezone: Europe/Paris}\n")
    cfg = C.load(y)
    assert cfg["owner"]["timezone"] == "Europe/Paris" and not cfg.get("_tz_from_machine")
    monkeypatch.setenv("TZ", "../../etc/passwd")
    tz = C.machine_timezone()                                                    # never raises; falls through to the system's or UTC
    assert tz != "../../etc/passwd" and ZoneInfo(tz)

def test_settings_say_when_the_starter_is_in_use(tmp_path, cfg):
    starter = TestClient(create_app(C.load(C.STARTER_CONFIG), db_path=str(tmp_path / "s.db")))
    assert starter.get("/api/settings").json()["starter"] is True
    assert TestClient(create_app(cfg, db_path=str(tmp_path / "o.db"))).get("/api/settings").json()["starter"] is False
    r = starter.put("/api/settings", json={"values": {"owner.name": "x"}})
    assert r.status_code == 400 and "copy config/factotum.example.yaml to config/factotum.yaml" in r.json()["detail"]


# --- Task 3: absent means off

def _drop(cfg, *dotted):
    for k in dotted:
        *head, last = k.split(".")
        d = cfg
        for h in head:
            d = d[h]
        d.pop(last, None)

def _code_defaults():
    """Every `C.get(x, "<a DEFAULTS key>", <literal>)` in factotum/, as (file, line, key, default); None when no default is given."""
    import ast
    out = []
    for path in (C.ROOT / "factotum").rglob("*.py"):
        for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get" and isinstance(n.func.value, ast.Name)
                    and n.func.value.id == "C" and len(n.args) >= 2 and isinstance(n.args[1], ast.Constant) and n.args[1].value in C.DEFAULTS):
                if len(n.args) == 2:
                    out.append((path.name, n.lineno, n.args[1].value, None))
                elif isinstance(n.args[2], (ast.Constant, ast.List, ast.Dict, ast.Tuple)):
                    out.append((path.name, n.lineno, n.args[1].value, ast.literal_eval(n.args[2])))
    return out

def test_every_settings_key_has_one_default_and_the_paid_ones_are_off():
    assert set(C.DEFAULTS) == set(C.SETTINGS_KEYS)
    for k in C.PAID_SWITCHES + ("sources.mail.suggest.enabled",):
        assert C.DEFAULTS[k] is False and C.value({}, k) is False, k

def test_every_default_in_the_code_is_the_one_settings_shows():
    found = _code_defaults()
    assert len(found) > 20
    bad = [f for f in found if not (f[3] == C.DEFAULTS[f[2]] or (not f[3] and not C.DEFAULTS[f[2]]))]
    assert bad == []
    assert not [f for f in found if f[2] in C.PAID_SWITCHES]                    # those are read through C.value only

def test_a_config_without_the_paid_switches_runs_no_brief_planner_or_summary(cfg, conn, tmp_path):
    from factotum.scheduler import Scheduler
    def day(c, conf):
        upsert_task(c, task(id=1, name="x")); c.commit()
        calls = []
        s = Scheduler(conf, c, push=lambda *a, **k: None, mailer=lambda *a, **k: calls.append("mail"),
                      planner=SimpleNamespace(run=lambda *a, **k: calls.append("planner") or {"ok": True, "verdict": "V."}),
                      summary=SimpleNamespace(get=lambda *a, **k: None, run=lambda *a, **k: calls.append("summary") or {"ok": True}))
        s.tick(datetime(2026, 11, 2, 7, 0)); s.tick(datetime(2026, 11, 2, 12, 0))
        return set(calls)
    assert day(conn, cfg) >= {"mail", "planner", "summary"}                      # the fixture switches them on
    _drop(cfg, *C.PAID_SWITCHES)
    c2 = connect(tmp_path / "t2.db"); init_db(c2)
    assert day(c2, cfg) == set()

def test_a_recording_writes_no_notion_note_when_the_notes_switch_is_absent(cfg, conn, tmp_path, monkeypatch):
    from factotum import sync_file
    from test_phase3 import GOOD, _fake_plaud, _runner_seq
    _drop(cfg, "sinks.notion_notes.enabled")
    plaud_run, _ = _fake_plaud(tmp_path)
    runner, _ = _runner_seq(GOOD)
    monkeypatch.setattr(sync_file, "RUNS_DIR", tmp_path / "runs")
    res = sync_file.run(conn, cfg, subject="[Plaud-AutoFlow] 11-01 Action Plan: Molina Healthcare Duplicate Payment and Auto-Pay Alignment",
                        received="2026-11-02T05:42:09+00:00", today=TODAY, runner=runner, plaud_run=plaud_run,
                        notes=lambda *a, **k: pytest.fail("a note was written"), calendar=None)
    assert res["ok"] and not res.get("note_url")

def test_settings_report_the_value_the_code_will_use(cfg, tmp_path):
    _drop(cfg, "lifecycle.fade_days", "claude.planner.enabled", "notify.morning_brief.enabled", "claude.summary.auto")
    client = TestClient(create_app(cfg, db_path=str(tmp_path / "api.db")))
    e = client.get("/api/settings").json()["effective"]
    assert set(e) == set(C.SETTINGS_KEYS)
    assert e["lifecycle.fade_days"] == 21 and e["claude.planner.enabled"] is False and e["notify.morning_brief.enabled"] is False
    assert e["lifecycle.review_cap"] == cfg["lifecycle"]["review_cap"]
    assert client.get("/api/summary").json()["auto"] is False
    client.put("/api/settings", json={"values": {"sources.discord.enabled": True}})
    assert client.get("/api/settings").json()["overrides"] == {"sources.discord.enabled": True}


# --- Task 4: the effective command

def _eff(*argv):
    from factotum.__main__ import main
    assert main(["effective", *argv]) == 0

def test_effective_prints_each_value_and_where_it_comes_from_and_no_secret(tmp_path, monkeypatch, capsys):
    y = tmp_path / "mine.yaml"
    y.write_text("owner: {timezone: Europe/Berlin}\nlifecycle: {fade_days: 30}\nnotify:\n  ntfy: {topic: secret-topic-123}\n  morning_brief: {to: me@example.com}\n")
    monkeypatch.setattr(C, "DEFAULT_CONFIG_PATHS", [y])
    db = tmp_path / "f.db"
    c = connect(db); init_db(c); set_setting(c, "lifecycle.top_cap", 5); c.commit(); c.close()
    monkeypatch.setenv("FACTOTUM_DB", str(db))
    _eff()
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0].startswith("config file: ") and "mine.yaml" in lines[0] and lines[1].startswith("database: ")
    f = {l.split()[0]: l.split() for l in lines[2:] if l.strip()}
    assert set(f) == set(C.REPORT_KEYS)
    assert f["lifecycle.top_cap"][1:] == ["5", "settings"] and f["lifecycle.fade_days"][1:] == ["30", "file"]
    assert f["lifecycle.review_cap"][1:] == ["15", "default"] and f["claude.planner.enabled"][1:] == ["false", "default"]
    assert f["owner.timezone"][1:] == ['"Europe/Berlin"', "file"]
    assert "secret-topic-123" not in out and "me@example.com" not in out and f["notify.ntfy.topic"][1] == "set"
    _eff("--as-before")
    before = capsys.readouterr().out
    changed = {l.split()[0] for l in set(before.splitlines()) ^ set(lines)}
    assert changed == set(C.PAID_SWITCHES)                                       # the whole difference stage 3 makes
    _eff("lifecycle.fade_days", "no.such.key")
    tail = capsys.readouterr().out.splitlines()[2:]
    assert [l.split()[0] for l in tail if l.strip()] == ["lifecycle.fade_days", "no.such.key"] and tail[-1].split()[-1] == "unset"

def test_effective_never_creates_or_writes_the_database(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(C, "DEFAULT_CONFIG_PATHS", [C.STARTER_CONFIG])
    monkeypatch.setenv("FACTOTUM_DB", str(tmp_path / "none.db"))
    _eff()
    out = capsys.readouterr().out
    assert not (tmp_path / "none.db").exists() and "(starter settings)" in out.splitlines()[0] and "(none yet)" in out.splitlines()[1]


# --- Task 5: accounts seeded once

def test_deleted_accounts_are_not_seeded_again(tmp_path, cfg):
    db = str(tmp_path / "a.db")
    c = TestClient(create_app(cfg, db_path=db))
    assert len(c.get("/api/settings").json()["accounts"]) == len(cfg["sources"]["mail"]["seed_accounts"])
    c.put("/api/settings", json={"accounts": []})
    assert TestClient(create_app(cfg, db_path=db)).get("/api/settings").json()["accounts"] == []


def test_the_starter_never_seeds_and_an_empty_seed_list_does_not_use_the_seeding_up(tmp_path, cfg, conn):
    c = TestClient(create_app(dict(cfg, _starter=True), db_path=str(tmp_path / "s.db")))
    assert c.get("/api/settings").json()["accounts"] == []
    assert seed_accounts(conn, []) == 0 and "state.accounts_seeded" not in get_settings(conn)
    n = len(cfg["sources"]["mail"]["seed_accounts"])
    assert seed_accounts(conn, cfg["sources"]["mail"]["seed_accounts"]) == n and get_settings(conn)["state.accounts_seeded"]
    assert seed_accounts(conn, [{"address": "z@example.com"}]) == 0


def test_an_install_that_already_has_accounts_is_marked_and_left_alone(conn):
    conn.execute("INSERT INTO accounts (address, display, enabled, roles_json) VALUES ('a@example.com', 'A', 1, '[]')"); conn.commit()
    assert seed_accounts(conn, [{"address": "b@example.com"}]) == 0
    assert [r["address"] for r in rows(conn, "SELECT address FROM accounts")] == ["a@example.com"]
    assert get_settings(conn)["state.accounts_seeded"]


# --- Task 6: the example

def _dotted(d, prefix=""):
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict) and v and key != "sources.canvas.course_map":     # the course codes there are the user's own
            yield from _dotted(v, key + ".")
        elif not key.startswith("_"):
            yield key

def test_the_example_is_a_reference_with_placeholders_only():
    from urllib.parse import urlparse
    text = C.EXAMPLE_CONFIG.read_text(encoding="utf-8")
    ex, own = C.load(C.EXAMPLE_CONFIG), C.load(FIXTURE)
    emails = re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text)
    assert emails and all(re.search(r"@(example\.(com|edu|org)|plaud\.ai)$", e) for e in emails), emails
    assert not re.search(r"\b[0-9a-f]{32}\b|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-", text)
    personal = [own["owner"]["name"], *own["owner"]["emails"], *(p["name"] for p in own["taxonomy"]["projects"] if p["name"] not in ("Personal", "Unknown")),
                urlparse(own["sources"]["canvas"]["base_url"]).hostname, urlparse(own["server"]["public_url"]).hostname,
                *own["sources"]["canvas"]["course_map"], *(w["name"] for w in own["sources"]["slack"]["workspaces"])]
    assert [p for p in personal if p and p.lower() in text.lower()] == []
    assert set(_dotted(own)) <= set(_dotted(ex))                                  # every key today's file sets is still documented
    assert all(C.get(ex, k, "absent") != "absent" for k in C.SETTINGS_KEYS)
    assert all(v is False for v in _flags(ex).values())
    for k in C.PAID_SWITCHES + ("server.oauth.enabled",):
        assert C.get(ex, k) is False, k
    lines = [l.strip() for l in text.splitlines()]
    for start in ("fade_days:", "fade_warn_days:", "last_call_days:", "top_cap:", "review_cap:", "scoring:", "review_time:", "time:"):
        assert "taste" in next(l for l in lines if l.startswith(start)), start
