# Stage 3: a neutral starting configuration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A fresh install starts neutral (no owner's file, no addresses, every integration and paid run off) and says so, while the owner's running setup behaves exactly as before.

**Architecture:** `config.load` falls back to a committed `config/factotum.starter.yaml` instead of the owner-shaped example, records `_starter` in the loaded dict and prints one line to stderr. Every Settings key gets one code default in `config.DEFAULTS` (the four paid / mailing switches flip to off there), read through `C.value`; an AST test keeps every literal default in the code equal to it, `/api/settings` reports those values, and a read-only `python -m factotum effective` command prints each value with its origin so the owner can diff before and after. The Settings page saves only the control that changed; accounts are seeded once; the page shows a starter banner and truthful empty states.

**Tech Stack:** Python 3.12, FastAPI/Starlette, SQLite, PyYAML, pytest (offline); plain JavaScript and CSS with no build step; Playwright (outside the repository) for UI checks.

**Spec:** `docs/superpowers/specs/2026-11-10-linked-tasks-design.md` — §15 (all of it except "A failed brief must not re-run the planner", which stage 1 Task 3 builds), §2 goal 10 and the non-goals (the wider packaging work is out), §3, §16 (checks every stage passes). Read `CLAUDE.md` in full.

## Global Constraints

- The repository working copy for this work is the git worktree `/home/robert/factotum/.worktrees/neutral-start` (branch `neutral-start`), called WT. `cd` there first in every command and use paths under it. Never touch the main checkout or any other worktree: NEVER read from, write to, or run git in `/home/robert/factotum` outside `.worktrees/neutral-start`. The main checkout serves a live app straight from its files.
- Never open `/home/robert/factotum/data`, `config/factotum.yaml` (the owner's private file) or `.env`. Never create `WT/config/factotum.yaml` either: its absence is what makes the dev instance run on the starter.
- Python and tests: `/home/robert/factotum/.venv/bin/python` and `/home/robert/factotum/.venv/bin/pytest`, run from WT. Do not install packages.
- No real Claude runs, no scheduler, no sweeps, no sudo, no `systemctl`.
- Small commits on branch `neutral-start`, the whole `pytest` suite green at every commit, never push. Every commit message ends with the line `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` (CLAUDE.md's trailer, exactly). Message style is the repository's: `area: what changed and why`.
- Person-specific values never appear in code, in `config/factotum.starter.yaml` or in `config/factotum.example.yaml`: no real name, address, course, Notion id, host or workspace. Placeholders are `you@example.com`, `you@example.edu`, `example.edu`, `YOUR_DATABASE_ID`. The fixture `tests/fixtures/factotum.fixture.yaml` keeps the owner-shaped content for now (removing it is out of scope).
- New tests go in `tests/test_stage3.py`, one section comment per task (`# --- Task N: …`). It imports what it needs from `conftest` (`TODAY`, `task`, `FIXTURE` after Task 1) like `tests/test_stage1.py` does. `tests/conftest.py` already has the autouse fixture `_no_real_env` (stage 1 Task 1): it stubs `C.load_dotenv` and deletes every variable named on a `NAME=` line of `WT/.env.example`, so `FACTOTUM_CONFIG` and `FACTOTUM_DB` are unset in every test unless the test sets them.
- `db.set_setting` JSON-encodes what it is given and does not commit; the caller commits. Every state-changing `/api` call carries `Content-Type: application/json` (tests pass `json=...`; the page uses its `api()` helper).
- What is typed lives in `draft` (app.js); `render()` rebuilds `#panel`. `app.js` stays one file; new functions go beside the code they belong with, under the existing `// ---------- … ----------` comments.
- User-visible strings this plan gives are used exactly as written.
- Stage 1 is being built in parallel on branch `linked-tasks` and rewrites the tab handling, `render()` and `wire()` in `app.js` and `Scheduler._brief`. Do not anticipate its code. To switch tabs from a new control, click the tab button (`document.querySelector('#tabs button[data-tab="settings"]').click()`) so whatever the tab handler becomes still runs.

**How a UI task is verified** (there are no JS tests; a UI task is not done on a clean pytest run alone):

- Dev instance for this worktree, no scheduler, at http://127.0.0.1:8752:
  `/tmp/claude-1000/-home-robert-factotum/85fa6951-ca9c-416e-9691-1a412f0fadea/scratchpad/dev/serve-neutral.sh restart`
  Two optional environment variables: `DB=<path>` (a fresh path under `/tmp/claude-1000/-home-robert-factotum/85fa6951-ca9c-416e-9691-1a412f0fadea/scratchpad/dev/` gives an EMPTY database; `rm -f` it first so it really is empty) and `CONFIG=<path>` (sets `FACTOTUM_CONFIG`; unset means no config file, which after Task 2 means the starter). Without `DB` it serves a copy of the owner's database. Restart after any Python or config change; files under `factotum/web/` are served from disk, so a reload is enough for those.
- A real browser at desktop (1280x900) and phone (390x844, touch) size:
  `cd /tmp/claude-1000/-home-robert-factotum/85fa6951-ca9c-416e-9691-1a412f0fadea/scratchpad/dev && node pw.mjs scenarios/<name>.mjs --device both --base http://127.0.0.1:8752`
  A scenario is an ES module, `export default async ({ page, context, shot, base, device, log }) => { ... }` (Playwright `page` API; `page.request.get(base + '/api/settings')` reads the API); `shot('name')` saves `.../scratchpad/shots/<name>-<device>.png`; console errors, failed requests and HTTP >= 400 are printed at the end. `scenarios/baseline.mjs` is an example.
- Scenarios for this stage are `scenarios/s3-<task number>-<name>.mjs` (not committed). The implementer runs each for both devices, LOOKS at every screenshot it names (the Read tool shows images), and reports what was seen in each.
- Do not load the Review tab in a scenario unless the task is about Review's empty state; Task 9 loads it only on an EMPTY database, where stamping rows as offered is harmless. Never press Apply in a scenario.

## Decisions this plan makes

- **File names.** `config/factotum.starter.yaml` (committed, the fallback), `config/factotum.example.yaml` (the reference, never loaded unless named), `tests/fixtures/factotum.fixture.yaml` (today's example, byte for byte). `config.py` gets `USER_CONFIG`, `STARTER_CONFIG`, `EXAMPLE_CONFIG` and `DEFAULT_CONFIG_PATHS = [USER_CONFIG, STARTER_CONFIG]`.
- **The starter is marked by its path:** `cfg["_starter"]` is True whenever the file loaded resolves to `STARTER_CONFIG`, however it was chosen (a chat turn's MCP server gets it through `FACTOTUM_CONFIG`). A `FACTOTUM_CONFIG` naming a missing file still falls through to `USER_CONFIG`, then the starter (`.env.example` sets `FACTOTUM_CONFIG=config/factotum.yaml`, so a newcomer's copied `.env` names a file that is not there yet).
- **"One line in the log"** is one line on stderr, once per process, printed by `config.load` (stderr, because `factotum mcp` owns stdout; the service journal captures both).
- **The machine's time zone** is filled in by `config.load` when `owner.timezone` is empty or not a zone `zoneinfo` knows: `TZ`, then `/etc/timezone`, then the `/etc/localtime` link, else `UTC`. A wrong name also prints one stderr line. The example and the starter both leave it empty.
- **One table of code defaults:** `config.DEFAULTS` holds the default for every `SETTINGS_KEYS` key; `C.value(cfg, key)` reads with it. The four switches that were on when absent (`notify.morning_brief.enabled`, `claude.planner.enabled`, `claude.summary.auto`, `sinks.notion_notes.enabled`) are False there and are read only through `C.value`. Other call sites keep their literals; an AST test fails if any literal default for a `DEFAULTS` key disagrees with the table.
- **`notify.ntfy.enabled` stays True when absent.** A push needs a topic too, and the topic's default is empty; a push costs nothing and sends no mail. The starter switches it off explicitly.
- **`/api/settings` `effective` carries every key's value with its default filled in**, and a new `starter` flag. No `defaults` or `origin` field: the page needs the value, and origins are the `effective` command's job.
- **`python -m factotum effective [KEY …] [--as-before]`** is read-only (the database is opened with `mode=ro`, and not created when missing), prints `config file:` and `database:` lines then one line per key: key, value (JSON), origin (`settings`, `file`, `machine`, `default`, `unset`). `--as-before` evaluates absent keys with the pre-stage-3 defaults (`config.OLD_DEFAULTS`), so `diff <(effective --as-before) <(effective)` printing nothing is the design's "identical before and after". It never prints `notify.ntfy.topic`, `notify.morning_brief.to` or `sources.calendar.calendar_id` (shown as `set #<8 hex of a SHA-256>` or `empty`, so a diff still sees a change) and never reads `.env` values.
- **Accounts are seeded once:** the settings row `state.accounts_seeded` records it. A table that already has rows marks it without inserting; an empty seed list does not use the seeding up; the starter is never seeded from.
- **Settings saves what changed:** each control's value is captured when it changes, in a map of dirty keys; a save sends only those keys, and the accounts table only when an account control changed or a row was removed. "Save now" saves what is dirty. The ~30 overrides already frozen in the owner's database are left alone: removing an override has no UI in this stage, and `effective` shows which keys come from Settings.
- **The banner says only what is always true** ("Starter settings in use.") plus where Factotum is running from, never "nothing is on": a database that already has Settings overrides can switch things on under the starter.
- **`FACTOTUM_SECRET` is removed from `.env.example`:** no code reads it (reminder tokens are random and stored in the database). `FACTOTUM_CONFIG` and `FACTOTUM_DB` stay as `NAME=` lines because `_no_real_env` takes its list from that file.
- **For whoever merges stage 1 and stage 3:** in `Scheduler._brief` and `_refresh` keep stage 1's planner-result logic and stage 3's `C.value(ecfg, "claude.planner.enabled")`; in `app.js` keep stage 1's tab and render structure and re-apply stage 3's banner call, Settings save and empty strings.

## Review Focus

1. The owner deploys this and a paid run silently stops because their own `config/factotum.yaml` lacks a key that used to default to on — Task 4's test pins that `--as-before` differs from the normal output only on those four keys, and the SETUP note makes the diff a deploy step.
2. A newcomer's `.env` copied from `.env.example` names `config/factotum.yaml` before that file exists — Task 2's test sets `FACTOTUM_CONFIG` to a missing file and expects the starter, not the example and not a crash.
3. A switch changed and the tab left within the 300 ms debounce must still save that one key, and nothing else — Task 7's scenario changes a switch, switches tab at once, and reads `/api/settings` `overrides`.
4. Deleting every account and restarting must not bring them back; a file without a seed list that gains one later must still seed once — Task 5's tests.
5. A config file whose `owner:` is empty or whose time zone is misspelt must load and run on the machine's zone, not crash the first tick — Task 2's time-zone test.

---

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `tests/fixtures/factotum.fixture.yaml` (new) | today's example, the suite's owner-shaped fixture | 1 |
| `tests/conftest.py`, `tests/test_core.py`, `tests/test_stage1.py` | load the fixture instead of the example | 1 |
| `tests/test_stage3.py` (new) | every new test of this stage | 1–6 |
| `config/factotum.starter.yaml` (new) | the neutral fallback | 2 |
| `factotum/config.py` | paths, starter fallback and note, machine time zone, `DEFAULTS`, `OLD_DEFAULTS`, `value`, `explain`, `REPORT_KEYS`, `MASKED` | 2, 3, 4 |
| `factotum/api.py` | `starter` in `/api/settings`, effective values with defaults, the starter's 400 text, `/api/summary` `auto`, seeding only from the user's file | 2, 3, 5 |
| `factotum/scheduler.py`, `factotum/sync_file.py` | the paid switches read through `C.value` | 3 |
| `factotum/__main__.py` | the `effective` command | 4 |
| `factotum/db.py` | `seed_accounts` once | 5 |
| `config/factotum.example.yaml` | the reference with placeholders | 6 |
| `factotum/web/app.js`, `factotum/web/app.css` | Settings truth and one-key saves, the banner, empty states | 7, 8, 9 |
| `docs/SETUP.md` | the `effective` deploy note (Task 4); starter and secrets (Task 10) | 4, 10 |
| `SPEC.md`, `CLAUDE.md`, `README.md`, `.env.example` | the stage's rule, gotchas, quick start, secrets list | 10 |

---

### Task 1: The suite loads a fixture of its own

Implementer: **sonnet**. UI task: **no**.

**Files:**
- Create: `tests/fixtures/factotum.fixture.yaml` (a byte-for-byte copy of `config/factotum.example.yaml`)
- Modify: `tests/conftest.py` (the `cfg` fixture), `tests/test_core.py` (its own `cfg` fixture), `tests/test_stage1.py` (`test_the_suite_never_sees_the_owners_environment` loads the example by path)
- Create: `tests/test_stage3.py`

**Interfaces:**
- Produces: `FIXTURE = Path(__file__).parent / "fixtures" / "factotum.fixture.yaml"` in `tests/conftest.py`; `tests/test_stage3.py` starting with `import re, json` · `from datetime import datetime` · `from pathlib import Path` · `from types import SimpleNamespace` · `import pytest` · `from fastapi.testclient import TestClient` · `from factotum import config as C, notify` · `from factotum.api import create_app` · `from factotum.db import connect, get_settings, init_db, rows, seed_accounts, set_setting, upsert_task` · `from conftest import FIXTURE, TODAY, task`.

This task changes nothing but where the tests read their configuration. The example itself is not touched.

- [ ] **Step 1: Copy the file:** `cp config/factotum.example.yaml tests/fixtures/factotum.fixture.yaml`, then `cmp config/factotum.example.yaml tests/fixtures/factotum.fixture.yaml` prints nothing.
- [ ] **Step 2: Write the test** (section `# --- Task 1: the suite's own fixture`)

```python
def test_the_suite_reads_its_fixture_not_the_example():
    assert FIXTURE.exists()
    hits = [p.name for p in Path(__file__).parent.glob("*.py") if "factotum.example" in p.read_text(encoding="utf-8") and p.name != "test_stage3.py"]
    assert hits == []
```

- [ ] **Step 3: Run it to verify it fails:** `/home/robert/factotum/.venv/bin/pytest tests/test_stage3.py -v` — FAIL, `hits` names `conftest.py`, `test_core.py`, `test_stage1.py`.
- [ ] **Step 4: Point every reference at the fixture.** `conftest.cfg` returns `C.load(FIXTURE)`; `test_core.cfg` returns `C.load(Path(__file__).parent / "fixtures" / "factotum.fixture.yaml")`; the stage-1 test loads `FIXTURE` (import it from `conftest`). Then `grep -rn "factotum.example" tests` prints only `tests/test_stage3.py`.
- [ ] **Step 5: Run the whole suite:** `cd /home/robert/factotum/.worktrees/neutral-start && /home/robert/factotum/.venv/bin/pytest -q` — all pass.
- [ ] **Step 6: Commit** (`tests: the suite loads tests/fixtures/factotum.fixture.yaml, a copy of today's example, so the example can become a reference with placeholders without touching a test` + trailer).

---

### Task 2: No silent fallback: the starter

Implementer: **sonnet**. UI task: **no**.

**Files:**
- Create: `config/factotum.starter.yaml`
- Modify: `factotum/config.py` (paths, `load`, new `machine_timezone`), `factotum/api.py` (`settings()`, the 400 text in `put_settings`)
- Test: `tests/test_stage3.py`

**Interfaces:**
- Consumes: `C.load(path=None) -> dict` (signature unchanged), `create_app(cfg, db_path)`.
- Produces: `C.USER_CONFIG`, `C.STARTER_CONFIG`, `C.EXAMPLE_CONFIG` (Paths under `ROOT / "config"`); `C.DEFAULT_CONFIG_PATHS = [USER_CONFIG, STARTER_CONFIG]` (read at call time, so tests monkeypatch it); `C.STARTER_NOTE: str`; module flag `C._starter_told: bool`; `C.machine_timezone() -> str`; keys in the loaded dict: `_path` (as now), `_starter: bool`, `_tz_from_machine: bool` (present and True only when filled in); `GET /api/settings` gains `"starter": bool`. Test helper `_flags(cfg) -> dict[str, Any]` in `tests/test_stage3.py` (Task 6 reuses it).

- [ ] **Step 1: Write the failing tests** (section `# --- Task 2: the starter`)

```python
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
    assert C.machine_timezone()                                                  # never raises; falls through to the system's or UTC

def test_settings_say_when_the_starter_is_in_use(tmp_path, cfg):
    starter = TestClient(create_app(C.load(C.STARTER_CONFIG), db_path=str(tmp_path / "s.db")))
    assert starter.get("/api/settings").json()["starter"] is True
    assert TestClient(create_app(cfg, db_path=str(tmp_path / "o.db"))).get("/api/settings").json()["starter"] is False
    r = starter.put("/api/settings", json={"values": {"owner.name": "x"}})
    assert r.status_code == 400 and "copy config/factotum.example.yaml to config/factotum.yaml" in r.json()["detail"]
```

- [ ] **Step 2: Run them to verify they fail:** `/home/robert/factotum/.venv/bin/pytest tests/test_stage3.py -k "starter or time_zone" -v` — FAIL (`AttributeError: STARTER_CONFIG`).
- [ ] **Step 3: Write `config/factotum.starter.yaml`** with exactly this content, then append the `lifecycle:` and `scoring:` blocks copied verbatim from today's `config/factotum.example.yaml` (generic numbers, no comments needed) where the two placeholders say so:

```yaml
# Factotum starter settings.
#
# Factotum runs on this file when config/factotum.yaml does not exist, and says so: one line in the
# service log and a banner in the app. Nothing here belongs to anyone: no name, no addresses, this
# machine's time zone, three projects of equal weight, and every input, output, notification and
# paid Claude run switched off. To make Factotum your own, copy config/factotum.example.yaml (the
# reference: every key, explained) to config/factotum.yaml, edit it, and restart Factotum.
# Do not set Factotum up by editing this file; an update replaces it.

owner:
  name: ""
  timezone: ""                  # empty = this machine's time zone
  emails: []

taxonomy:
  areas: [Work, Personal]
  projects:
    - { name: Work,     area: Work,     weight: medium, description: "Work and study: anything done for a job, a course or a team." }
    - { name: Personal, area: Personal, weight: medium, description: "Personal life: home, health, errands, money, plans." }
    - { name: Other,    area: Personal, weight: medium, description: "Anything that fits neither of the others." }
  efforts: [XS, S, M, L]
  tie_breakers: ""
  note_types:
    - { name: Meeting, description: a conversation with more than one participant }
    - { name: Note,    description: a memo, an idea or a note to self }
  note_tags:
    - { name: Task,      description: contains actionable to-dos }
    - { name: Follow-up, description: contains items to circle back on }

# lifecycle: (copied from the example)

# scoring: (copied from the example)

sources:
  plaud:    { enabled: false }
  canvas:   { enabled: false }
  mail:     { enabled: false, suggest: { enabled: false } }
  slack:    { enabled: false }
  discord:  { enabled: false }
  calendar: { enabled: false }

sinks:
  notion_notes:        { enabled: false }
  notion_tasks_mirror: { enabled: false }

notify:
  ntfy: { enabled: false, topic: "" }
  review_time: "19:30"
  snooze_presets: ["30m", "1h", "tomorrow 9am"]
  morning_brief: { enabled: false, time: "06:30", to: "" }

claude:
  planner: { enabled: false }
  summary: { auto: false, auto_minutes: 10 }

server:
  host: 127.0.0.1
  port: 8750
  public_url: ""
  mcp_writes: stage
  oauth: { enabled: false }
```

- [ ] **Step 4: Implement in `factotum/config.py`.**
  - Paths: `USER_CONFIG`, `STARTER_CONFIG = ROOT / "config" / "factotum.starter.yaml"`, `EXAMPLE_CONFIG`; `DEFAULT_CONFIG_PATHS = [USER_CONFIG, STARTER_CONFIG]`. Module docstring: one line saying the starter replaced the example as the fallback (2026-11-10) and why (the example was the owner's own setup).
  - `STARTER_NOTE = "factotum: no config/factotum.yaml, so starter settings in use (config/factotum.starter.yaml): every input, output and paid Claude run is off. Copy config/factotum.example.yaml to config/factotum.yaml to make it your own."`
  - `load`: candidate order unchanged except the defaults; after reading, `cfg["_starter"] = p.resolve() == STARTER_CONFIG.resolve()`; when `_starter` and not `_starter_told`, print `STARTER_NOTE` to `sys.stderr` (flush) and set the flag. Never print to stdout. A missing `owner` block or `owner: null` becomes `{}`. When `owner.timezone` is empty, or `zoneinfo.ZoneInfo(name)` raises anything, set it to `machine_timezone()` and `cfg["_tz_from_machine"] = True`; for a non-empty wrong name also print `f"factotum: owner.timezone {name!r} is not a time zone; using {tz}"` to stderr. The `FileNotFoundError` text stays.
  - `machine_timezone() -> str`: the first of `os.environ["TZ"]` (a leading `:` stripped), the first line of `/etc/timezone`, the part of `os.path.realpath("/etc/localtime")` after `zoneinfo/` that `ZoneInfo` accepts (any exception means "not this one"), else `"UTC"`.
- [ ] **Step 5: Implement in `factotum/api.py`.** `settings()` adds `"starter": bool(cfg.get("_starter"))`. In `put_settings`, the 400 for a non-Settings key says, when `cfg.get("_starter")`: `f"{k} is not a Settings key; it belongs in config/factotum.yaml, which does not exist yet: copy config/factotum.example.yaml to config/factotum.yaml, edit it and restart"`; otherwise the current text.
- [ ] **Step 6: Run the tests:** `/home/robert/factotum/.venv/bin/pytest -q` — all pass.
- [ ] **Step 7: Commit** (`config: with no config/factotum.yaml Factotum runs on config/factotum.starter.yaml (no name, no addresses, the machine's time zone, three equal projects, every input, output, notification and paid run off) and says so on stderr and in /api/settings; a missing or wrong time zone becomes the machine's` + trailer).

---

### Task 3: Absent means off, and one table of defaults

Implementer: **sonnet**. UI task: **no**.

**Files:**
- Modify: `factotum/config.py` (`DEFAULTS`, `value`), `factotum/scheduler.py` (`tick`, `_brief`, `_refresh`, `_auto_summary`), `factotum/sync_file.py` (`run`, the notes-sink gate), `factotum/api.py` (`today_summary`, `settings`)
- Test: `tests/test_stage3.py`

**Interfaces:**
- Consumes: `C.SETTINGS_KEYS`, `C.get`.
- Produces: `C.DEFAULTS: dict[str, Any]` (exactly the `SETTINGS_KEYS` keys), `C.value(cfg: dict, key: str) -> Any` (`get(cfg, key, DEFAULTS[key])`; a key not in `DEFAULTS` raises `KeyError` on purpose), `C.PAID_SWITCHES = ("notify.morning_brief.enabled", "claude.planner.enabled", "claude.summary.auto", "sinks.notion_notes.enabled")`. `GET /api/settings` `effective[k] == C.value(ecfg, k)` for every Settings key.

- [ ] **Step 1: Write the failing tests** (section `# --- Task 3: absent means off`)

```python
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
```

- [ ] **Step 2: Run them to verify they fail:** `/home/robert/factotum/.venv/bin/pytest tests/test_stage3.py -k "default or paid or notes_switch or will_use" -v` — FAIL (`AttributeError: DEFAULTS`).
- [ ] **Step 3: Add `DEFAULTS`, `PAID_SWITCHES` and `value` to `factotum/config.py`** under `SETTINGS_KEYS`, with a comment saying absent means off for anything that costs money or sends mail, and that the AST test in `tests/test_stage3.py` holds every literal default in the code to this table. The values (the code's defaults today, except the four flips):

| Key | Default | Key | Default |
|---|---|---|---|
| `lifecycle.fade_days` | 21 | `sources.calendar.calendar_id` | `"primary"` |
| `lifecycle.fade_warn_days` | 3 | `sources.canvas.sweep_minutes` | 60 |
| `lifecycle.last_call_days` | 7 | `sources.mail.sweep_minutes` | 20 |
| `lifecycle.top_cap` | 7 | `sources.calendar.sweep_minutes` | 30 |
| `lifecycle.review_cap` | 15 | `sources.slack.enabled`, `sources.discord.enabled` | False |
| `notify.review_time` | `"19:30"` | `sources.slack.sweep_minutes` | 20 |
| `notify.snooze_presets` | `[]` | `sources.mail.suggest.enabled` | False |
| `notify.ntfy.enabled` | True | `sources.mail.suggest.minutes` | 30 |
| `notify.ntfy.topic` | `""` | `sinks.notion_notes.enabled` | **False** (was True) |
| `notify.morning_brief.enabled` | **False** (was True) | `sinks.notion_tasks_mirror.enabled` | False |
| `notify.morning_brief.time` | `"06:30"` | `sinks.notion_tasks_mirror.sweep_minutes` | 10 |
| `notify.morning_brief.to` | `""` | `scoring.project_weights` | `{}` |
| `sources.plaud.enabled`, `sources.canvas.enabled`, `sources.mail.enabled`, `sources.calendar.enabled` | False | `claude.models` | `{}` |
| `claude.planner.enabled` | **False** (was True) | `claude.budgets_usd.chat_turn` | 1.0 |
| `claude.summary.auto` | **False** (was True) | `claude.summary.auto_minutes` | 10 |
| `server.oauth.enabled` | False | `server.mcp_writes` | `"stage"` |

- [ ] **Step 4: Read the four switches through `C.value`:** `scheduler.py` (`tick`'s morning-brief condition, `_brief`, `_refresh`, `_auto_summary`), `sync_file.run` (`sinks.notion_notes.enabled`), `api.today_summary` (`claude.summary.auto`). In `api.settings()`, `effective` becomes `{k: C.value(ecfg, k) for k in C.SETTINGS_KEYS}` and the `sources` / `sinks` blocks read their `enabled` with `C.value`. Update the scheduler's module docstring lines for `morning_brief` and `today_summary` to say "when switched on (off when the key is absent)".
- [ ] **Step 5: Run the tests:** `/home/robert/factotum/.venv/bin/pytest -q` — all pass. If the AST test names a call site, fix the literal at that site to the table's value only when the table is right; if the code's literal is the real behaviour, stop and report it.
- [ ] **Step 6: Commit** (`config: absent means off for the morning brief, the planner, the automatic summary and the notes sink; one table of defaults (config.DEFAULTS, read with C.value) that /api/settings reports and an AST test holds every literal default in the code to` + trailer).

---

### Task 4: `factotum effective`, and the deploy check

Implementer: **sonnet**. UI task: **no**.

**Files:**
- Modify: `factotum/config.py` (`OLD_DEFAULTS`, `REPORT_KEYS`, `MASKED`, `explain`), `factotum/__main__.py` (subcommand, `_effective`, module docstring), `docs/SETUP.md` (new §17)
- Test: `tests/test_stage3.py`

**Interfaces:**
- Consumes: `C.DEFAULTS`, `C.PAID_SWITCHES` (Task 3); `C.load`, `C.db_path`.
- Produces: `C.OLD_DEFAULTS = {**DEFAULTS, **{k: True for k in PAID_SWITCHES}}` (commented: the code's defaults before 2026-11-10, for `effective --as-before` only); `C.REPORT_KEYS = ["owner.timezone", *SETTINGS_KEYS]`; `C.MASKED = {"notify.ntfy.topic", "notify.morning_brief.to", "sources.calendar.calendar_id"}`; `C.explain(cfg: dict, overrides: dict, key: str, defaults: dict | None = None) -> tuple[Any, str]` returning origin `"settings"` (key in `SETTINGS_KEYS` and in `overrides`), `"machine"` (`owner.timezone` with `_tz_from_machine`), `"file"`, `"default"` (in `defaults`, which is `DEFAULTS` when None), or `"unset"` (value None); `__main__._effective(keys: list[str], as_before: bool) -> int`.

- [ ] **Step 1: Write the failing tests** (section `# --- Task 4: the effective command`)

```python
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
```

- [ ] **Step 2: Run them to verify they fail:** `/home/robert/factotum/.venv/bin/pytest tests/test_stage3.py -k effective -v` — FAIL (argparse: invalid choice `effective`).
- [ ] **Step 3: Implement `explain`, `OLD_DEFAULTS`, `REPORT_KEYS`, `MASKED` in `factotum/config.py`** as the Interfaces block says (a module-level `_MISSING = object()` sentinel for "not in the file").
- [ ] **Step 4: Implement the command in `factotum/__main__.py`.** Parser: `p = sub.add_parser("effective", help="what each switch is and where it comes from (Settings, the config file, the code's default); read-only")`, `p.add_argument("keys", nargs="*")`, `p.add_argument("--as-before", action="store_true", help="absent keys as the code before 2026-11-10 read them; diff against the plain output when deploying")`. In `main`, right after `parse_args` and BEFORE `connect`/`init_db`: `if a.cmd == "effective": return _effective(a.keys, a.as_before)`. `_effective`:
  - `cfg = C.load()`; overrides from `C.db_path()` opened with `sqlite3.connect(f"file:{path}?mode=ro", uri=True)` (`SELECT key, value_json FROM settings`, `json.loads` each) when the file exists, else `{}`.
  - Line 1: `f"config file: {cfg['_path']}" + ("  (starter settings)" if cfg.get("_starter") else "")`. Line 2: `f"database:    {path}" + ("" if exists else "  (none yet)")`. Both identical with and without `--as-before`.
  - Then one line per key of `keys or C.REPORT_KEYS`: `f"{key:<42} {shown:<26} {origin}"`, where `shown` is `json.dumps(value, ensure_ascii=False)`, or for a key in `MASKED`: `"set #" + sha256(json.dumps(value).encode()).hexdigest()[:8]` when truthy, else `"empty"`. `defaults=C.OLD_DEFAULTS` with `--as-before`.
  - Add `python -m factotum effective [KEY …] [--as-before]` to the module docstring.
- [ ] **Step 5: Write `docs/SETUP.md` §17 "Deploying a change to defaults: `factotum effective`".** Content: what the command prints and that it only reads (config file, database opened read-only, never `.env`; three values shown as a fingerprint). The deploy check for stage 3, in the main checkout after the merge and BEFORE restarting the service (the running service is not affected by files changing, except `factotum/web/`):
  ```bash
  cd /home/robert/factotum
  .venv/bin/python -m factotum effective --as-before > /tmp/effective-before.txt
  .venv/bin/python -m factotum effective > /tmp/effective-after.txt
  diff /tmp/effective-before.txt /tmp/effective-after.txt && echo identical
  ```
  `identical` and a first line naming `config/factotum.yaml` (not `(starter settings)`) mean nothing changes for this install. Each line `diff` prints is a switch that was on only because its key was missing: add the key to `config/factotum.yaml` with the value from the before file (or switch it in Settings), run the two commands again, then restart (check `journalctl -u factotum -n 5` for a Claude run in flight first, CLAUDE.md). Lines with origin `settings` are values the Settings tab froze into the database; they win over the file.
- [ ] **Step 6: Run the tests:** `/home/robert/factotum/.venv/bin/pytest -q` — all pass. Also by hand from WT (no config file, no database there): `/home/robert/factotum/.venv/bin/python -m factotum effective | head -5` shows `(starter settings)` and `(none yet)`, and `ls WT/data` shows no new database.
- [ ] **Step 7: Commit** (`cli: factotum effective [KEY…] [--as-before] prints each switch's value and origin (settings, file, machine, default) without secrets, reading the database read-only; SETUP §17 makes diffing the two outputs a deploy step` + trailer).

---

### Task 5: Accounts are seeded once

Implementer: **sonnet**. UI task: **no**.

**Files:**
- Modify: `factotum/db.py` (`seed_accounts`), `factotum/api.py` (`create_app`)
- Test: `tests/test_stage3.py`

**Interfaces:**
- Consumes: `cfg["_starter"]` (Task 2).
- Produces: `db.seed_accounts(conn, seed: list[dict]) -> int` (rows inserted; commits); the settings row `state.accounts_seeded` (an ISO date-time string). The callers in `tests/test_phase4.py`, `test_phase8.py`, `test_phase10.py` keep working unchanged.

- [ ] **Step 1: Write the failing tests** (section `# --- Task 5: accounts seeded once`)

```python
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
```

- [ ] **Step 2: Run them to verify they fail:** `/home/robert/factotum/.venv/bin/pytest tests/test_stage3.py -k "seed" -v` — FAIL (re-seeded rows; `None == 0`).
- [ ] **Step 3: Implement.** `seed_accounts`: return 0 if `state.accounts_seeded` is set; if the table has rows, set the mark, commit, return 0; insert the seed rows as now; set the mark only when at least one row was inserted; commit; return the count. Docstring: once per database, so a table emptied on purpose stays empty. `create_app`: call it only when `not cfg.get("_starter")`.
- [ ] **Step 4: Run the tests:** `/home/robert/factotum/.venv/bin/pytest -q` — all pass.
- [ ] **Step 5: Commit** (`accounts: seeded once per database from the user's own file (state.accounts_seeded), never from the starter; deleting every account no longer brings the owner's back on the next start` + trailer).

---

### Task 6: The example becomes a reference with placeholders

Implementer: **opus**. UI task: **no**.

**Files:**
- Modify: `config/factotum.example.yaml`
- Test: `tests/test_stage3.py`

**Interfaces:**
- Consumes: `FIXTURE` (Task 1), `C.EXAMPLE_CONFIG` and `_flags` (Task 2).
- Produces: nothing code reads; `config/factotum.example.yaml` stays loadable.

Write the file for a stranger: every key Factotum reads, each with a comment saying what it does, where it is changed (the file or Settings → which section), and which docs/SETUP.md section sets up its integration. Keep today's structure and key order. Rules:
- Header comment: a reference, not loaded unless named; copy to `config/factotum.yaml`; without that file Factotum runs on `config/factotum.starter.yaml`; secrets go in `.env`; a Settings-tab change is stored in the database and wins over this file; values marked `# taste` are preferences with no right answer.
- `# taste` on the lines `fade_days:`, `fade_warn_days:`, `last_call_days:`, `top_cap:`, `review_cap:`, `scoring:` (with "every number in this block"), `review_time:` and the morning brief's `time:`.
- Every `enabled:` switch false, `sources.mail.suggest.enabled` false, `claude.planner.enabled` false, `claude.summary.auto` false, `server.oauth.enabled` false, `server.mcp_writes: stage`; each switch's comment says what turning it on costs or sends.
- Placeholders: `owner.name: "Your Name"`, `owner.timezone: ""` (empty = this machine's), `owner.emails: [you@example.com, you@example.edu]`; projects `Course 101` (Academics, high), `Thesis` (Research, high, never_fade), `Lab work` (Research, medium), `Side project` (Work, low), `Personal` (Personal, high), `Unknown` (Work, medium), each with a one-line generic description that shows how descriptions steer filing; generic `tie_breakers` between those; `sources.plaud.trigger_account: you@example.com`; Canvas `base_url: https://canvas.example.edu`, `course_map: { COURSE101: Course 101 }`, `skip_context_patterns: ["*Example Department*"]`; mail `seed_accounts` `you@example.edu` (School, `[monitor]`) and `you@example.com` (Personal, `[monitor, brief_recipient, plaud_trigger]`); `allow: ["*@example.edu", "*@*.example.edu", "*@instructure.com", "*@*.instructure.com", "*"]`; `deny: ["newsletter@example.edu", "*Example Athletics*"]`; Slack `workspaces: [{ name: Example Lab, token_env: SLACK_TOKEN_EXAMPLE, project: Lab work }]`; Discord `{ name: Example server, id: "000000000000000000" }`; Notion ids `YOUR_DATABASE_ID`, `YOUR_DATA_SOURCE_ID` (notes) and `YOUR_TASKS_DATABASE_ID`, `YOUR_TASKS_DATA_SOURCE_ID` (mirror); `notify.morning_brief.to: you@example.com`; `server.public_url: https://factotum.example.com`; empty `access_team` / `access_aud`. Vendor values that are not personal stay (`no-reply@plaud.ai`, `mx.google.com`, the claude.ai redirect URIs, the property-name map, the skip-sender patterns).

- [ ] **Step 1: Write the failing test** (section `# --- Task 6: the example`)

```python
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
```

- [ ] **Step 2: Run it to verify it fails:** `/home/robert/factotum/.venv/bin/pytest tests/test_stage3.py -k example -v` — FAIL on the address check.
- [ ] **Step 3: Rewrite `config/factotum.example.yaml`** by the rules above.
- [ ] **Step 4: Run the tests:** `/home/robert/factotum/.venv/bin/pytest -q` — all pass; `/home/robert/factotum/.venv/bin/python -c "from factotum import config as C; print(len(C.load(C.EXAMPLE_CONFIG)))"` prints a number.
- [ ] **Step 5: Commit** (`config: the example is a reference with placeholders only (example.edu, you@example.com, YOUR_DATABASE_ID): every key explained, every switch off, the values that are taste marked; the owner's setup lives in their own config/factotum.yaml and in the test fixture` + trailer).

---

### Task 7: Settings shows the truth and saves one key

Implementer: **opus**. UI task: **yes**.

**Files:**
- Modify: `factotum/web/app.js` (`renderSettings`, `wire`'s Settings handlers, `saveSettings`)

**Interfaces:**
- Consumes: `GET /api/settings` `effective` (every key filled in, Task 3); `PUT /api/settings` with `{values}` and optionally `accounts` (an omitted `accounts` leaves the table alone, as today).
- Produces: `settingsDirty: Map<string, any>` (a Settings key → the value captured when its control changed; the key `'#accounts'` → true when the accounts table changed); `controlValue(el) -> any | undefined`; `saveSettings()` sends only what is dirty.

The two faults, verified in today's code: `bool(key, dflt)` shows a switch unchecked when its key is absent from the file (the code then used True for the brief, the planner and the summary), and number boxes show empty; and `saveSettings` reads every `[data-key]` control in the panel and sends all of them, so the first change freezes about thirty file values into the database.

- [ ] **Step 1: Make the controls show `effective` as given.** `bool(key)` loses its second parameter (checked iff `e[key]` is truthy); remove the `false` / `true` arguments at its two call sites. Nothing else in `renderSettings` guesses a default.
- [ ] **Step 2: Save what changed.** `controlValue(el)`: checkbox → `checked`; number → `undefined` when empty or not a number, else `Number(el.value)`; anything else → `el.value`. In `wire()` (Settings tab only): a `[data-key]` change puts `controlValue(el)` into `settingsDirty` (skipped when undefined) and schedules `saveSettings` in 300 ms (the existing `saveSettings.t` timer); a `[data-acc]` change writes the field into `settings.accounts[i]` at once (roles split on commas and trimmed, `enabled` from `checked`) and sets `'#accounts'`; the `×` button removes the row, sets `'#accounts'` and schedules the save. `saveSettings()`: when `settingsDirty` is empty, toast `Nothing to save` and return; otherwise snapshot and clear the map, send `values` = every entry except `'#accounts'`, and `accounts` only when `'#accounts'` was in it (if a row has no address yet, leave `accounts` out, put `'#accounts'` back, and toast `Settings saved · the new account row needs an address`); on failure put the snapshot back and toast `Save failed: <message>`. The success toast stays `Settings saved`. "Save now" calls `saveSettings`. Keep the focus restore.
- [ ] **Step 3: Check the suite is untouched:** `/home/robert/factotum/.venv/bin/pytest -q` — all pass.
- [ ] **Step 4: Verify by rendering.** Make `/tmp/claude-1000/-home-robert-factotum/85fa6951-ca9c-416e-9691-1a412f0fadea/scratchpad/dev/s3-trimmed.yaml`: a copy of `WT/config/factotum.starter.yaml` without its `lifecycle:` block and without its `claude:` block. Start: `rm -f <dev>/s3-7.db && DB=<dev>/s3-7.db CONFIG=<dev>/s3-trimmed.yaml <dev>/serve-neutral.sh restart` (`<dev>` = `/tmp/claude-1000/-home-robert-factotum/85fa6951-ca9c-416e-9691-1a412f0fadea/scratchpad/dev`). Scenario `scenarios/s3-7-settings.mjs`, run per device on a fresh database (restart with `rm -f` between the two devices, or run `--device desktop` and `--device phone` separately with a restart between):
  1. Open `/#settings`, wait for `[id="s-lifecycle.fade_days"]` (ids contain dots: select them by attribute, not `#…`); log the value of the fade-days box and whether `[id="s-claude.planner.enabled"]`, `[id="s-claude.summary.auto"]` and `[id="s-notify.morning_brief.enabled"]` are checked; `shot('s3-7-settings')`. Expected: fade days `21`; all three unchecked.
  2. Tick `[id="s-sources.discord.enabled"]`, then at once click the Today tab button; wait 1 s; `page.request.get(base + '/api/settings')` → log `Object.keys(overrides)`. Expected: exactly `["sources.discord.enabled"]`.
  3. Back to Settings; set the fade-days box to 30 and blur; wait 1 s; overrides keys are exactly the two keys.
  4. Click "Save now"; the toast reads `Nothing to save`; `shot('s3-7-nothing')`.
  5. Click "+ account", type `test@example.com` in the new address box and blur; wait 1 s; the API's `accounts` has one row and `overrides` still has two keys.
  Look at both screenshots on both devices: the switches and boxes show those values, nothing scrolls sideways at 390 px, the toast is readable.
- [ ] **Step 5: Commit** (`web: Settings shows the value the code will use (the server fills defaults in) and a change saves that one key, captured when it changed; the accounts table only when an account changed (the first change used to save every control shown and freeze ~30 file values)` + trailer).

---

### Task 8: The starter banner

Implementer: **sonnet**. UI task: **yes**.

**Files:**
- Modify: `factotum/web/app.js` (new `starterHtml()` beside `pendingHtml()`, `render()`, `renderSettings`'s note, `wire()`), `factotum/web/app.css`

**Interfaces:**
- Consumes: `settings.starter` (Task 2).
- Produces: `starterHtml() -> string` ('' unless `settings && settings.starter`), rendered first in `#panel` on every tab.

- [ ] **Step 1: Implement.** `render()` puts `starterHtml()` before `pendingHtml()`. The banner, exactly:
  `<div class="starter" role="status"><b>Starter settings in use.</b> <span>Factotum is running on config/factotum.starter.yaml, not a file of your own.</span> <button class="btn small" data-starter-help>How to make it your own</button></div>` — the button only when `ui.tab !== 'settings'`; in `wire()` it clicks `#tabs button[data-tab="settings"]`. On the Settings tab, when `settings.starter`, the top note is replaced by, exactly:
  `<div class="note starter-note"><b>These are the starter settings</b> (<code>config/factotum.starter.yaml</code>): no name, no addresses, every input and output off, no brief, no planner, no automatic summary, no notifications. To make Factotum your own, copy <code>config/factotum.example.yaml</code> to <code>config/factotum.yaml</code>, fill in your projects, your addresses and the integrations you use (docs/SETUP.md), and restart Factotum. A switch changed here is stored in the database and wins over the file.</div>`
  CSS `.starter`: the shape of `.pending` in amber (`var(--warn-soft)` background, `var(--warn)` border), flex with wrapping, `margin-bottom: 14px`; one line at 1280 px, wraps at 390 px without sideways scrolling.
- [ ] **Step 2: Check the suite:** `/home/robert/factotum/.venv/bin/pytest -q` — all pass.
- [ ] **Step 3: Verify by rendering.** (a) `rm -f <dev>/s3-8.db && DB=<dev>/s3-8.db <dev>/serve-neutral.sh restart` (no `CONFIG`: the starter). Scenario `scenarios/s3-8-starter.mjs`: open `/`, wait for `.starter`, `shot('s3-8-today')`; click `[data-starter-help]`, wait for `.starter-note`, `shot('s3-8-settings')`. (b) `DB=<dev>/s3-8.db CONFIG=/home/robert/factotum/.worktrees/neutral-start/tests/fixtures/factotum.fixture.yaml <dev>/serve-neutral.sh restart`; scenario `scenarios/s3-8-own.mjs`: open `/`, wait 1 s, log `await page.locator('.starter').count()` (expected 0), `shot('s3-8-own')`. Run both for both devices and look: the banner sits at the top of Today on both sizes, its button lands on Settings with the starter note and no banner button; with the fixture there is no banner and Settings shows the usual note.
- [ ] **Step 4: Commit** (`web: a banner on every tab while the starter settings are in use, and on Settings how to make them your own` + trailer).

---

### Task 9: Empty and first-run states

Implementer: **sonnet**. UI task: **yes**.

**Files:**
- Modify: `factotum/web/app.js` (`renderToday`, `renderTimeline`, `renderProjects`, `summaryHtml`, `renderReview`'s finished page, `renderChat`'s intro, `render()`'s Review-at pill), `factotum/web/app.css` only if a string needs it

**Interfaces:**
- Consumes: `openOnes()`, `todayGroups()`, `settings.effective` (may still be null while loading: then keep today's behaviour).
- Produces: `noTasksHtml() -> string`.

How tasks arrive today, which these strings must not contradict: the inputs (Plaud recordings, Canvas) create them; Review's suggestion cards (from mail, Slack, the planner) create one on Create; Chat proposes a task with a Create button; a connector or skill may create (staged or direct per Settings); the one-time Notion import. There is no hand-made "New task" yet; do not promise one.

- [ ] **Step 1: Implement, with these exact strings.**
  - `noTasksHtml()`: `<div class="empty"><b>No open tasks.</b> Tasks arrive from the inputs you switch on in Settings (Plaud recordings, Canvas), from suggestions you accept in Review (mail, Slack), and from Chat, where Claude can propose a task for you to create. Coming from a Notion tasks database? Run <code>python -m factotum import-notion --dry-run</code>, then <code>--apply</code> (docs/SETUP.md §5).</div>` — replaces Today's current empty text, and is what Timeline and Projects render when `openOnes()` is empty.
  - Today with open tasks but every group empty: `<div class="empty">Nothing is due today or this week. Every open task is on Timeline.</div>`.
  - `summaryHtml()` renders the hidden section (as when `summary` is null) when there is no summary and no open task, so a first run shows no Summary box or Refresh button.
  - Review's finished page, when nothing is staged and nothing is left (the "Nothing to review." case): add `<p class="hint">Review gathers what needs a decision: soft dates that slipped, new tasks, backlog about to fade, and suggestions from the inputs you switch on.</p>` under the heading.
  - Chat's intro, when `settings` is loaded and `sources.mail.enabled` is off: drop the mail starter question and the sentence about Mailbird drafts, and say "Ask about your tasks" plus ", your calendar" only when `sources.calendar.enabled` is on. With mail on, today's text unchanged.
  - The header's `Review at HH:MM` pill shows only when `notify.ntfy.enabled` is on and `notify.ntfy.topic` is set (the review push needs both).
- [ ] **Step 2: Check the suite:** `/home/robert/factotum/.venv/bin/pytest -q` — all pass.
- [ ] **Step 3: Verify by rendering on an EMPTY database with the starter.** `rm -f <dev>/s3-9.db && DB=<dev>/s3-9.db <dev>/serve-neutral.sh restart`. Scenario `scenarios/s3-9-empty.mjs` (it may load Review: the database is empty): open `/`, `shot('s3-9-today')`; click each tab button in turn (`timeline`, `projects`, `review`, `chat`, `log`) and `shot('s3-9-<tab>')` after 500 ms. Look: Today shows the no-tasks text, no Summary box, no Review-at pill; Timeline and Projects show the same text; Review says "Nothing to review." with the hint; Chat's intro mentions neither mail nor Mailbird and offers two starters; Log says nothing has happened. Then the owner-shaped check: `DB` unset (the copy of the owner's database) and `CONFIG=` the fixture path, restart, scenario `scenarios/s3-9-own.mjs` opens `/` and Chat and shoots `s3-9-own-today` and `s3-9-own-chat` (do NOT open Review): Today shows the rows and the Summary box as before, the Review-at pill is there, Chat's intro is today's. Both devices, every screenshot looked at, nothing scrolls sideways at 390 px.
- [ ] **Step 4: Commit** (`web: empty states that are true for someone with nothing set up (how tasks arrive today, no Summary box before there is anything to sum up, Review says what it gathers, Chat offers mail only when mail is on, no review-push time without ntfy)` + trailer).

---

### Task 10: The stage's documents

Implementer: **opus**. UI task: **no**.

**Files:**
- Modify: `CLAUDE.md`, `SPEC.md`, `docs/SETUP.md`, `README.md`, `.env.example`

**Interfaces:**
- Consumes: every name above (`STARTER_CONFIG`, `DEFAULTS`, `C.value`, `PAID_SWITCHES`, `explain`, `effective`, `state.accounts_seeded`, `settingsDirty`, `starterHtml`).

- [ ] **Step 1: `.env.example`.** Remove `FACTOTUM_SECRET` and its comment (no code reads it: `grep -rn FACTOTUM_SECRET factotum` is empty; reminder tokens are random and stored in the database). The `FACTOTUM_CONFIG` comment says: default `config/factotum.yaml`; without that file Factotum runs on `config/factotum.starter.yaml`. Keep `FACTOTUM_CONFIG=` and `FACTOTUM_DB=` as `NAME=` lines (the test fixture `_no_real_env` takes its list from them). Header: only the secrets of the integrations you switch on are needed.
- [ ] **Step 2: `CLAUDE.md`.**
  - Fact 3: person-specific facts belong in the user's `config/factotum.yaml` (the example documents the structure with placeholders) or `.env`.
  - Phase status: a sentence for stage 3 (2026-11-10): the starter fallback and banner, absent means off, `config.DEFAULTS`, `factotum effective`, accounts seeded once, Settings saving one key, the example as a placeholder reference, the test fixture.
  - Where things live: a row "the starter, code defaults, absent means off, `effective`" → SPEC §9 "Configuration" → `factotum/config.py` (`STARTER_CONFIG`, `DEFAULTS`, `value`, `explain`), `config/factotum.starter.yaml`, `__main__.py` (`_effective`), `tests/test_stage3.py` (`_code_defaults`).
  - Commands: `.venv/bin/python -m factotum effective [KEY…] [--as-before]   # each switch's value and origin, read-only; diff the two before a deploy (SETUP §17)`.
  - Conventions, the rule: **Absent means off for anything that costs money or sends mail** (2026-11-10): a paid Claude run, a mail, a write to an outside service happens only when its switch is explicitly on. The default lives in `config.DEFAULTS` as False and the code reads it with `C.value(cfg, key)`, never `C.get(cfg, key, True)`; a new Settings key needs its `DEFAULTS` entry too (with `SETTINGS_KEYS`, the control and the example). New runs (ripples, filing) follow this from the start. `tests/test_stage3.py::_code_defaults` fails when a literal default in the code disagrees with the table.
  - The "Settings-tab keys are a whitelist" bullet adds the `DEFAULTS` entry. The "A Settings-tab override hides a YAML edit" bullet: since 2026-11-10 only the control that changed is saved; the overrides saved before stay until removed from the `settings` table, and `factotum effective` shows them as `settings`.
  - Gotchas dated 2026-11-10: the tests read `tests/fixtures/factotum.fixture.yaml`, never the example (it holds placeholders); no `config/factotum.yaml` means the starter, marked `_starter`, one stderr line, the banner; `owner.timezone` empty or wrong means the machine's zone; accounts are seeded once (`state.accounts_seeded`), never from the starter; `.env.example`'s `NAME=` lines are the list `_no_real_env` clears.
- [ ] **Step 3: `SPEC.md`.** The opening "handed to someone else" paragraph: a fresh install runs on the starter and says so. §2 "Configurable, not forked": the example is the reference, the starter the fallback. A new §9 paragraph "Configuration" (beside the Settings sentence): precedence Settings override > the user's file > `config.DEFAULTS`; the starter; the banner; Settings shows effective values with defaults and saves only the control that changed; absent means off for paid and mailing switches; accounts seeded once; `factotum effective`. §13 "Cost and safety": one line, every paid run is off unless switched on.
- [ ] **Step 4: `docs/SETUP.md`.** §8: remove `FACTOTUM_SECRET` from the `.env` line; say that without `config/factotum.yaml` the service runs on the starter (banner in the app, one line in `journalctl`), and that `python -m factotum effective` shows what is in force. Link §17 (Task 4).
- [ ] **Step 5: `README.md`.** Quick start, replacing the block and "fill in NOTION_TOKEN at least":
  ```bash
  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
  .venv/bin/python -m factotum serve                     # http://127.0.0.1:8750, on the starter settings: nothing switched on
  cp config/factotum.example.yaml config/factotum.yaml   # make it yours: projects, addresses, the integrations you use
  cp .env.example .env                                   # only the secrets of the integrations you switch on (docs/SETUP.md)
  .venv/bin/python -m factotum effective                 # what each switch is and where it comes from
  .venv/bin/pytest                                       # offline
  ```
  then one line: moving from a Notion tasks database is `import-notion --dry-run`, then `--apply` (docs/SETUP.md §5). "Adapting it to your own workflow" mentions the starter and the example as the reference. The Layout block lists `config/  factotum.starter.yaml (the fallback), factotum.example.yaml (the reference)`.
- [ ] **Step 6: Check:** `/home/robert/factotum/.venv/bin/pytest -q` — all pass (the stage-1 env test still finds `NOTION_TOKEN`, `MAILBIRD_TOKEN`, `SMTP_PASSWORD`, `FACTOTUM_DB` in `.env.example`); `grep -rn "FACTOTUM_SECRET" . --include=*.md --include=.env.example --exclude-dir=.git` prints nothing outside `docs/superpowers/`.
- [ ] **Step 7: Commit** (`docs: stage 3, a neutral start: the starter and its banner, absent means off (a CLAUDE.md rule for every new paid run), config.DEFAULTS, factotum effective as a deploy check, accounts seeded once, Settings saving one key; README quick start without a token; FACTOTUM_SECRET dropped (nothing read it)` + trailer).
