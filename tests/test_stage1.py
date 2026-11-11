import base64, json, os, struct
from datetime import datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from factotum import api, batches, commands, config as C, reminders
from factotum.db import get_settings, upsert_task
from conftest import FIXTURE, TODAY, _example_env_names, task


# --- Task 1: tests never read real secrets

def test_the_suite_never_sees_the_owners_environment():
    names = _example_env_names()
    assert {"NOTION_TOKEN", "MAILBIRD_TOKEN", "SMTP_PASSWORD", "FACTOTUM_DB"} <= names
    C.load(FIXTURE)          # would call load_dotenv(ROOT / ".env")
    assert not any(os.environ.get(n) for n in names)
    assert not any(k.startswith("SLACK_TOKEN_") for k in os.environ)


# --- Task 2: the command fault

def test_a_dateless_command_with_words_left_over_is_not_a_command():
    P = lambda s: commands.parse(s, TODAY)           # noqa: E731
    assert P("drop 12 duplicate of 14") is None      # the design's case: it used to drop both
    assert P("done 389 because it shipped") is None
    assert P("top 23 please") is None
    assert P("reopen 5 to friday") is None           # a date tail on a verb that takes none
    assert P("nodue 364 tomorrow") is None
    assert P("snooze 12 duplicate of 14") is None    # unreadable words never silently mean "tomorrow"
    # what keeps working (the plain forms, `done 389 392`, `snooze #23`, `push Task-364 fri`, `defer 388 to next week`,
    # `nodue 364 Task-365`, are already pinned in test_core.py and test_phase9.py; not repeated here)
    assert [c["task_id"] for c in P("done 12 and 14")] == [12, 14]
    assert [c["task_id"] for c in P("drop Task-12, Task-14")] == [12, 14]
    assert P("snooze 23 fri")[0]["fields"]["snoozed_until"] == "2026-11-06"


# --- Task 3: the brief retry

def _brief_sched(cfg, conn, mailer, planner):
    from factotum.scheduler import Scheduler
    cfg["notify"]["ntfy"]["topic"] = "t"
    upsert_task(conn, task(id=1, name="x", top_rank=1)); conn.commit()
    return Scheduler(cfg, conn, push=lambda *a, **k: None, mailer=mailer, planner=planner)


def _flaky_mailer(sent):
    """The first send fails (SMTP down); later ones are recorded in `sent`."""
    fail = [True]
    def mailer(cfg, to, subject, text, html):
        if fail[0]:
            fail[0] = False
            raise OSError("smtp down")
        sent.append(text)
    return mailer


def test_a_failed_brief_send_does_not_run_the_planner_again(conn, cfg):
    runs, sent = [], []
    mailer = _flaky_mailer(sent)
    pl = SimpleNamespace(run=lambda conn, cfg, today, calendar=None: runs.append(today) or {"ok": True, "verdict": "Verdict here.", "applied": [], "staged": [], "cost_usd": 0.3})
    s = _brief_sched(cfg, conn, mailer, pl)
    s.tick(datetime(2026, 11, 2, 6, 31))
    assert len(runs) == 1 and sent == [] and get_settings(conn).get("state.last_fail.morning_brief")
    s.tick(datetime(2026, 11, 2, 7, 40))          # the hourly retry
    assert len(runs) == 1 and len(sent) == 1 and "Verdict here." in sent[0]
    s.tick(datetime(2026, 11, 3, 6, 31))          # a new day plans again
    assert len(runs) == 2 and len(sent) == 2


def test_a_planner_that_failed_is_not_retried_with_the_brief(conn, cfg):
    runs, sent = [], []
    mailer = _flaky_mailer(sent)
    def boom(conn, cfg, today, calendar=None):
        runs.append(today)
        raise RuntimeError("claude down")
    s = _brief_sched(cfg, conn, mailer, SimpleNamespace(run=boom))
    s.tick(datetime(2026, 11, 2, 6, 31)); s.tick(datetime(2026, 11, 2, 7, 40))
    assert len(runs) == 1 and len(sent) == 1 and "claude down" in sent[0]


# --- Task 4: the installable app

def _png_size(b: bytes) -> tuple[int, int]:
    assert b[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", b[16:24])


def test_the_page_head_installs_the_app(cfg, tmp_path):
    r = TestClient(api.create_app(cfg, db_path=str(tmp_path / "a.db"))).get("/")
    t = r.text
    assert '<link rel="manifest" href="/static/manifest.webmanifest" crossorigin="use-credentials">' in t
    assert '<link rel="apple-touch-icon" sizes="180x180" href="data:image/png;base64,' in t
    assert '<meta name="apple-mobile-web-app-title" content="Factotum">' in t
    assert t.count('name="theme-color"') == 2
    assert "SPIKE" not in t
    uri = t.split('rel="apple-touch-icon" sizes="180x180" href="data:image/png;base64,')[1].split('"')[0]
    data = base64.b64decode(uri)
    assert data == (api.WEB / "icon-180.png").read_bytes()
    assert _png_size(data) == (180, 180)


def test_the_manifest_and_its_icons_are_served(cfg, tmp_path):
    c = TestClient(api.create_app(cfg, db_path=str(tmp_path / "a.db")))
    r = c.get("/static/manifest.webmanifest")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/manifest+json")
    m = r.json()
    assert (m["name"], m["start_url"], m["scope"], m["display"]) == ("Factotum", "/", "/", "standalone")
    purposes = set()
    for icon in m["icons"]:
        g = c.get(icon["src"])
        assert g.status_code == 200 and g.headers["content-type"].startswith("image/png")
        w, h = icon["sizes"].split("x")
        assert _png_size(g.content) == (int(w), int(h))
        purposes |= set(icon.get("purpose", "any").split())
    assert {"any", "maskable"} <= purposes


def test_the_icon_tool_draws_valid_icons():
    import importlib.util
    spec = importlib.util.spec_from_file_location("make_icons", C.ROOT / "tools" / "make_icons.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for n in (180, 192, 512):
        assert _png_size(mod.png(n)) == (n, n)


# --- Task 5: addresses and the build stamp

def test_task_and_tab_addresses_answer_with_the_app(cfg, tmp_path):
    c = TestClient(api.create_app(cfg, db_path=str(tmp_path / "a.db")))
    for path in ("/", "/today", "/timeline", "/projects", "/review", "/chat", "/log", "/settings", "/t/42"):
        r = c.get(path)
        assert r.status_code == 200, path
        assert 'id="panel"' in r.text and "/static/app.js?v=" in r.text
        assert r.headers["cache-control"] == "no-cache, must-revalidate"
    assert c.get("/t/abc").status_code == 404
    assert c.get("/nowhere").status_code == 404
    assert c.get("/mcp").status_code == 405


def test_every_api_response_carries_the_build_stamp(cfg, tmp_path):
    c = TestClient(api.create_app(cfg, db_path=str(tmp_path / "a.db")))
    s = api.build_stamp()
    assert len(s) == 12
    assert c.get("/api/tasks").headers["x-factotum-build"] == s
    r = c.post("/api/batches/apply", content=b"{}")
    assert r.status_code == 415 and r.headers["x-factotum-build"] == s
    assert f'<meta name="factotum-build" content="{s}">' in c.get("/").text


def test_the_build_stamp_follows_the_web_files(tmp_path, monkeypatch):
    import shutil
    for f in api.STAMPED:
        shutil.copy(api.WEB / f, tmp_path / f)
    monkeypatch.setattr(api, "WEB", tmp_path)
    a = api.build_stamp()
    assert api.build_stamp() == a
    ns = (tmp_path / "app.js").stat().st_mtime_ns + 10**9
    os.utime(tmp_path / "app.js", ns=(ns, ns))
    assert api.build_stamp() != a


def test_a_reminders_open_goes_to_its_task(cfg, tmp_path):
    from test_core import _rem
    app = api.create_app(cfg, db_path=str(tmp_path / "a.db"))
    c = app.state.conn
    upsert_task(c, task(id=1, name="Call Reza")); upsert_task(c, task(id=2, name="Other")); c.commit()
    batches.apply(c, [{"task_id": 1, "op": "add_reminder", "fields": {"fire_at": "2026-11-03T09:00"}},
                      {"task_id": 2, "op": "add_reminder", "fields": {"fire_at": "2026-11-03T09:00"}}], "ui", TODAY)
    reminders.fire(c, cfg, datetime(2026, 11, 3, 9, 0, 30), lambda *a, **k: "m1")
    tok1, tok2 = [r["token"] for r in _rem(c, 1) + _rem(c, 2) if r["status"] == "sent"]
    client = TestClient(app)
    r = client.get(f"/r/{tok1}", params={"a": "open"}, follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"].endswith("/t/1")
    r = client.get(f"/r/{tok2}", params={"a": "done"})
    assert r.status_code == 200 and '/t/2"' in r.text and "Open Task-2" in r.text
