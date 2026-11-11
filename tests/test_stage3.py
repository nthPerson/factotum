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
