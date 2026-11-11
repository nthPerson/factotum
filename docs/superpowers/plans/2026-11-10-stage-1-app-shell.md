# Stage 1: the app shell — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Factotum an installable app with real addresses, a working Back, a phone task page, a "Sign in again" screen, fresh lists on return and self-update, and fix the two faults the design names for stage 1 (leftover words in commands, the brief retry re-running the planner).

**Architecture:** The server answers `/t/<id>` and one path per tab with the same page, stamps every `/api` response with a build stamp made from the web files' modification times, and sends a reminder's Open to `/t/<id>`. The page (still one `app.js`, no build step) reads its address on load and on `popstate`, keeps tab changes as history entries, and on a phone renders a full-screen task page into `#panel`. `api()` recognises an expired sign-in (a redirect, 401 or 403 on `/api`), and a `visibilitychange` handler reloads the lists or the whole page. Python faults are fixed test-first; UI work is verified by rendering in a real browser at 1280 px and 390 px.

**Tech Stack:** Python 3.12, FastAPI/Starlette, SQLite, pytest (offline); plain JavaScript and CSS with no build step; Playwright (outside the repository) for UI checks.

**Spec:** `docs/superpowers/specs/2026-11-10-linked-tasks-design.md` — §13 (all), §12.3 (the phone task page, as far as stage 1 goes), §12.7 last paragraph (the command fault), §15 "A failed brief must not re-run the planner", §16 (checks every stage passes), §18 (builder's decisions, made below). Read §2 and §3 too.

## Global Constraints

- The repository working copy for this work is the git worktree `/home/robert/factotum/.worktrees/linked-tasks` (branch `linked-tasks`), called WT. `cd` there first in every command and use paths under it.
- NEVER read from, write to, or run git in `/home/robert/factotum` outside `.worktrees/linked-tasks`: the main checkout serves a live app straight from its files. Never open `/home/robert/factotum/data`, `config/factotum.yaml` or `.env`. Never run `sudo`, `systemctl`, the scheduler, a sweep, the planner, or `claude`.
- Python and tests: `/home/robert/factotum/.venv/bin/python` and `/home/robert/factotum/.venv/bin/pytest`, run from WT.
- Do not install packages.
- No real Claude runs, no scheduler, no sweeps, no sudo.
- Small commits on branch `linked-tasks`, `pytest` green at every commit (the whole suite: 159 tests at the start, more as tasks add them), never push. Every commit message ends with the line `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` (CLAUDE.md's trailer, exactly). Message style is the repository's: `area: what changed and why`.
- Desktop behaviour must not change except where a task says so. Every surface is checked at 1280 px and at 390 px.
- Read `WT/CLAUDE.md` before touching code. These of its rules bite here: what is typed lives in `draft` and is restored by `render()` (a new text box needs a `draft` entry, `typing()` wiring and its id in render()'s focus list); a folded section renders no rows (`jumpTo` unfolds first); every state-changing `/api` call carries `Content-Type: application/json` (use the page's `api()` helper); `set_setting` JSON-encodes what it is given and does not commit.
- `app.js` stays one file (no modules in stage 1). New functions go beside the code they belong with, under the existing `// ---------- … ----------` section comments.
- User-visible words follow the design's vocabulary (§4). Exact strings this plan gives are to be used as written.

**How a UI task is verified** (there are no JS tests; a UI task is not done on a clean pytest run alone):

- Dev instance on a COPY of the database, no scheduler, at http://127.0.0.1:8751:
  `/tmp/claude-1000/-home-robert-factotum/85fa6951-ca9c-416e-9691-1a412f0fadea/scratchpad/dev/serve.sh restart`
  Restart after any Python change; files under `factotum/web/` are served straight from WT, so a page reload is enough for those.
- A real browser at desktop (1280x900, mouse) and phone (390x844, touch, hover none) size:
  `cd /tmp/claude-1000/-home-robert-factotum/85fa6951-ca9c-416e-9691-1a412f0fadea/scratchpad/dev && node pw.mjs scenarios/<name>.mjs --device both`
  A scenario is an ES module, `export default async ({ page, context, shot, base, device, log }) => { ... }`, using Playwright's `page` API (and `context` for `route` / `setOffline`); `shot('name')` saves `.../scratchpad/shots/<name>-<device>.png`; console errors, uncaught exceptions, failed requests and HTTP >= 400 are printed at the end. `scenarios/baseline.mjs` there is an example, and `shots/baseline-*-{desktop,phone}.png` show the app before stage 1.
- Each UI task writes its scenario under that `scenarios/` folder as `s1-<task number>-<name>.mjs`, runs it for both devices, LOOKS at every screenshot it names (the Read tool shows images), and says in its report what was seen in each. Scenarios are not committed (they live outside the repository).
- Do not load the Review tab in a scenario unless the task is about Review (loading it stamps rows as offered). No stage-1 task is about Review.
- The dev database is a copy, but still never press Apply in a scenario; staging and Discard are fine.

## Decisions this plan makes (design §18 and the points it leaves open)

- **No split of `app.js`** into modules in stage 1. Every file the page loads keeps the `?v=<mtime>` stamp `api.py::index` gives it.
- **No automated browser checks in the repository.** UI tasks are checked with the scratchpad Playwright scenarios above; the repository gains no Node dependency.
- **The icon tool belongs in the repository** as `tools/make_icons.py` (dependency-free, regenerates `factotum/web/icon-{180,192,512}.png` and prints the 180 px icon as the data URI `index.html` inlines). A test pins that the inlined icon is `icon-180.png`.
- **Addresses.** Today is `/`; every other tab is `/<tab>` (`/timeline`, `/projects`, `/review`, `/chat`, `/log`, `/settings`); `/today` is accepted and rewritten to `/`. A task is `/t/<id>`. Old `/#<tab>` links keep working: the page rewrites them to the path in place. Tabs become paths too because a real path survives the sign-in redirect and a fragment may not, and "Sign in again" reloads at the same address.
- **History, address by address:**

  | What happens | Address afterwards | History |
  |---|---|---|
  | App opened at `/`, `/<tab>` | that address | — |
  | App opened at `/today` or an old `/#<tab>` link | `/` or `/<tab>` | replaced |
  | A tab is clicked | `/` or `/<tab>` | pushed (nothing if it is the current tab) |
  | A jump to a row moves to another tab (Top tile, summary link) | the new tab's address | pushed |
  | A prompt sent from a list tab moves to Chat, or "Open in Chat" | `/chat` | pushed |
  | Desktop: a row is expanded or collapsed | unchanged | none |
  | Desktop: app opened at `/t/<id>` | the tab that lists the task (Today, else Timeline), row unfolded, expanded, flashed | replaced |
  | Phone: a row is tapped (or its ▸, or a Top tile double-tapped) | `/t/<id>` | pushed |
  | Phone: app opened at `/t/<id>` | `/t/<id>` | `/` put beneath it (replace, then push), so Back lands on Today, not outside the app |
  | Phone: ‹ Back on the task page, the phone's Back, or Esc | the address beneath | popped; the list is redrawn and its scroll position restored |
  | Browser Back / Forward between tabs | the entry's address | `popstate` redraws that tab (or opens / closes the task page) |
- **The phone task page is rendered into `#panel`** in place of the tab's content when `ui.page` is set (`render()` already rebuilds `#panel`, and every handler `wire()` installs is scoped to it). `body.page-on` hides the header and the Top rail on phones; the list's scroll position is saved on open and restored on close. "Phone" means `PHONE()` (`max-width: 700px`), the same breakpoint as the CSS phone layout; a wider touch screen keeps desktop behaviour.
- **What the task page shows in stage 1:** what the expanded row shows today (score, why now, context, source, links, lifecycle, reminders, the actions and the two Claude buttons), under a ‹ Back bar, the task code, the name and the row's chips. Actions stay in the page, not pinned to the bottom (pinning Done / Later / More arrives with stage 4's page). A task not in the open list is fetched from `GET /api/tasks/{id}` and shown read-only ("This task is done." / "This task is dropped."); an unknown id says "Task-N does not exist."
- **Signed-out detection:** `api()` fetches with `redirect: 'manual'`. An expired Access session answers an `/api` request with a redirect to another origin, which the page then sees as `response.type === 'opaqueredirect'`; a 401 or 403 is treated the same (Factotum's `/api` never sends either). A restarting server (502 / 530 / a refused connection) or a dead network (a `TypeError` from `fetch`) is not a redirect and never shows the screen. The rule "`/api` never answers 401, 403 or a redirect" becomes a CLAUDE.md gotcha.
- **The build stamp** is the first 12 hex digits of SHA-1 over the `st_mtime_ns` of `index.html`, `app.js`, `app.css` and `manifest.webmanifest` (`api.STAMPED`), computed per request (the web files are served from disk, so a JS-only deploy needs no restart and must still be noticed). A Python-only change does not change it: the page it would reload is the same page. Header: `X-Factotum-Build` on every `/api/` response; the page carries the stamp it loaded with in `<meta name="factotum-build" content="…">`.
- **"Something is being typed"** (`typingNow()`): a non-empty `draft.cmd`, `draft.ask`, `draft.pick` or Review note; an open reminder or due-date form (their values live only in the DOM); a chat turn running; or focus in an input, textarea or select inside `#panel` (covers Settings).
- **The brief keeps its planner result for the day** in the setting `state.brief.planner` = `{"day": "<ISO date>", "result": {...}}`, committed before the mail is sent; a retry the same day reuses it. A planner that raised is recorded too, so one brief day costs at most one planner run.
- **The command fault:** a command whose verb takes no date (`done`, `drop`, `top`, `unpin`, `longterm`, `start`, `reopen`, `nodue`) is unrecognised when anything is left after the task numbers, including a `to <when>` tail. The word `and` and commas between numbers are separators, not leftovers (`done 12 and 14` stays a command). `snooze` with words it cannot read as a date is unrecognised too instead of silently meaning "tomorrow" (`snooze 12 duplicate of 14` was the same fault).
- **The test suite never reads `.env`:** an autouse fixture in `tests/conftest.py` replaces `config.load_dotenv` with a no-op and removes every variable named in `.env.example` (and any `SLACK_TOKEN_*`) from the environment for each test.

## Review Focus

1. Text typed into the command box, a Review note or an open reminder form when the app comes back to the front, or when a new build is noticed, must still be there afterwards — Task 10's scenario types, returns, and checks the box.
2. A server restart (502) or a phone with no network must not show "Sign in again"; only a redirect, 401 or 403 does — Task 9's scenario drives all three.
3. A reminder for a task that is already done, or a stale `/t/<id>` link, must open a page that says so, not a blank screen — Task 7's scenario opens `/t/<done id>` and `/t/999999`.
4. The phone's Back from a task page opened straight from a reminder must land on Today, not leave the app — Task 7's scenario loads `/t/<id>` and goes back.
5. Changes staged in the tray must survive selecting rows on a phone (the tray hides while the short bar shows and comes back when the selection is cleared) and must survive an update reload — Task 8's and Task 10's scenarios.

---

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `tests/conftest.py` | shared fixtures; the autouse fixture that keeps real secrets out of tests | 1 |
| `tests/test_stage1.py` (new) | every new Python test of this stage, in sections per task | 1–6 |
| `tests/test_phase3.py` | the notes-sink test sets its own `NOTION_TOKEN` | 1 |
| `tests/test_core.py` | the `tap_opens_app` assertion follows the new click address | 5 |
| `factotum/commands.py` | `parse`: leftovers make a dateless command unrecognised | 2 |
| `factotum/scheduler.py` | `_brief` keeps the day's planner result; the review push links `/review` | 3, 6 |
| `factotum/api.py` | static manifest type; page routes, build stamp, reminder Open, `_page` links, consent link | 4, 5, 6 |
| `factotum/reminders.py` | the optional push click goes to `/t/<id>` | 5 |
| `factotum/brief.py` | "Open Review" links `/review` | 6 |
| `factotum/web/index.html` | install lines (comments), build meta, signed-out screen, Update ready button | 4, 5, 9, 10 |
| `factotum/web/app.js` | addresses and Back, the task page, the short bar, signed out, return and update | 6–10 |
| `factotum/web/app.css` | task page, short bar, signed-out screen, Update ready | 7–10 |
| `tools/make_icons.py` (new) | regenerates the app icons | 4 |
| `SPEC.md`, `CLAUDE.md`, `docs/SETUP.md` | the stage's decisions, gotchas and install steps | 11 |

---

### Task 1: A test suite that never reads real secrets

Implementer: **sonnet**. UI task: **no**.

**Files:**
- Modify: `tests/conftest.py`
- Modify: `tests/test_phase3.py:238` (`test_notes_sink_creates_then_updates_on_source_link`)
- Create: `tests/test_stage1.py`

**Interfaces:**
- Consumes: `factotum.config.load`, `factotum.config.load_dotenv` (the name `config.py` imports from `dotenv`), `WT/.env.example`.
- Produces: the autouse fixture `_no_real_env` in `tests/conftest.py` (every later test runs under it); `tests/test_stage1.py` with the imports later tasks extend:
  `import base64, json, os, struct` · `from datetime import datetime` · `from types import SimpleNamespace` · `from fastapi.testclient import TestClient` · `from factotum import api, batches, commands, config as C, reminders` · `from factotum.db import get_settings, upsert_task` · `from conftest import TODAY, _example_env_names, task`.

- [ ] **Step 1: Write the failing test** in `tests/test_stage1.py` (section comment `# --- Task 1: tests never read real secrets`)

```python
def test_the_suite_never_sees_the_owners_environment():
    names = _example_env_names()
    assert {"NOTION_TOKEN", "MAILBIRD_TOKEN", "SMTP_PASSWORD", "FACTOTUM_DB"} <= names
    C.load(C.ROOT / "config" / "factotum.example.yaml")          # would call load_dotenv(ROOT / ".env")
    assert not any(os.environ.get(n) for n in names)
    assert not any(k.startswith("SLACK_TOKEN_") for k in os.environ)
```
`_example_env_names()` is imported from `conftest` (it is defined there in Step 3: the set of names on lines of `.env.example` matching `^([A-Z][A-Z0-9_]*)=`).

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /home/robert/factotum/.worktrees/linked-tasks && NOTION_TOKEN=from-shell /home/robert/factotum/.venv/bin/pytest tests/test_stage1.py -v`
Expected: FAIL (ImportError for `_example_env_names`, or the `NOTION_TOKEN` assertion).

- [ ] **Step 3: Implement in `tests/conftest.py`**
  - `_example_env_names() -> set[str]`: parse `C.ROOT / ".env.example"` as above.
  - `@pytest.fixture(autouse=True) def _no_real_env(monkeypatch)`: `monkeypatch.setattr(C, "load_dotenv", lambda *a, **k: False)`; `monkeypatch.delenv(name, raising=False)` for every name from `_example_env_names()`; the same for every variable in `os.environ` starting with `SLACK_TOKEN_`. Add one docstring line: the owner's `.env` sits in the main checkout's root, and a test that passes only because a real token is present proves nothing (and may reach the real service).

- [ ] **Step 4: Give the notes-sink test its own token.** In `tests/test_phase3.py::test_notes_sink_creates_then_updates_on_source_link` add the `monkeypatch` fixture and `monkeypatch.setenv("NOTION_TOKEN", "t")` at its top, as `tests/test_phase6.py` already does for its Notion tests.

- [ ] **Step 5: Run the whole suite, with and without a token in the shell**

Run: `cd /home/robert/factotum/.worktrees/linked-tasks && /home/robert/factotum/.venv/bin/pytest -q && NOTION_TOKEN=from-shell SLACK_TOKEN_X=y /home/robert/factotum/.venv/bin/pytest -q`
Expected: `160 passed` both times.

- [ ] **Step 6: Commit**

```bash
git add tests/conftest.py tests/test_phase3.py tests/test_stage1.py
git commit -m "tests: never read the owner's .env (load_dotenv stubbed, every variable .env.example names removed per test); the notes-sink test sets its own NOTION_TOKEN, so the suite is green in a checkout without secrets

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Leftover words make a command unrecognised

Implementer: **sonnet**. UI task: **no**.

**Files:**
- Modify: `factotum/commands.py` (`parse`, module docstring)
- Test: `tests/test_stage1.py`

**Interfaces:**
- Consumes: `commands.parse(text: str, today: date) -> list[dict] | None` (unchanged signature); `dates.resolve_when`.
- Produces: a module constant `DATELESS = {"done", "drop", "top", "unpin", "longterm", "start", "reopen", "nodue"}` (canonical ops from `VERBS`).

- [ ] **Step 1: Write the failing test** (section `# --- Task 2: the command fault`; `TODAY` is 2026-11-01, a Sunday)

```python
def test_a_dateless_command_with_words_left_over_is_not_a_command():
    P = lambda s: commands.parse(s, TODAY)           # noqa: E731
    assert P("drop 12 duplicate of 14") is None      # the design's case: it used to drop both
    assert P("done 389 because it shipped") is None
    assert P("top 23 please") is None
    assert P("reopen 5 to friday") is None           # a date tail on a verb that takes none
    assert P("nodue 364 tomorrow") is None
    assert P("snooze 12 duplicate of 14") is None    # unreadable words never silently mean "tomorrow"
    # what keeps working
    assert [c["task_id"] for c in P("done 389 392")] == [389, 392]
    assert [c["task_id"] for c in P("done 12 and 14")] == [12, 14]
    assert [c["task_id"] for c in P("drop Task-12, Task-14")] == [12, 14]
    assert P("snooze #23")[0]["fields"]["snoozed_until"] == "2026-11-02"
    assert P("snooze 23 fri")[0]["fields"]["snoozed_until"] == "2026-11-06"
    assert P("push Task-364 fri")[0]["fields"]["due"] == "2026-11-06"
    assert P("defer 388 to next week")[0]["fields"]["due"] == "2026-11-02"
    assert P("nodue 364 Task-365") == [{"task_id": 364, "op": "set", "fields": {"due": None}}, {"task_id": 365, "op": "set", "fields": {"due": None}}]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `/home/robert/factotum/.venv/bin/pytest tests/test_stage1.py -k leftover -v`
Expected: FAIL on the first assertion (a list of two drops instead of None).

- [ ] **Step 3: Implement in `factotum/commands.py::parse`.** After the ids are found, the leftover is the rest with the ids removed, commas and standalone `and` treated as spaces. If the op is in `DATELESS` and there is a leftover or a `to|until|till` tail, return None. For `snooze`, a `when_text` that `resolve_when` cannot read returns None (only no `when_text` at all means tomorrow). Add `drop 12 duplicate of 14 -> not a command (words left over)` to the docstring's examples.

- [ ] **Step 4: Run the tests**

Run: `/home/robert/factotum/.venv/bin/pytest -q`
Expected: all pass (161), including `tests/test_core.py` and `tests/test_phase9.py` command tests.

- [ ] **Step 5: Commit** (`commands: words left over after the task numbers make a command that takes no date unrecognised, so it goes to Chat instead of staging every number it saw (\`drop 12 duplicate of 14\` staged two drops); snooze no longer reads unknown words as tomorrow` + trailer).

---

### Task 3: A failed brief does not run the planner again

Implementer: **sonnet**. UI task: **no**.

**Files:**
- Modify: `factotum/scheduler.py` (`Scheduler._brief`)
- Test: `tests/test_stage1.py`

**Interfaces:**
- Consumes: `Scheduler(cfg, conn, push=..., mailer=..., planner=...)`, `tick(now_local)`, `brief.send(conn, cfg, today, mailer, calendar, planner=...)`, `db.set_setting`, `db.get_settings`.
- Produces: the setting `state.brief.planner` = `{"day": "YYYY-MM-DD", "result": {"ok": bool, "verdict": str|None, "applied": [str], "staged": [str], "error": str|None, "line": str|None}}`.

- [ ] **Step 1: Write the failing tests** (section `# --- Task 3: the brief retry`)

```python
def _brief_sched(cfg, conn, mailer, planner):
    from factotum.scheduler import Scheduler
    cfg["notify"]["ntfy"]["topic"] = "t"
    upsert_task(conn, task(id=1, name="x", top_rank=1)); conn.commit()
    return Scheduler(cfg, conn, push=lambda *a, **k: None, mailer=mailer, planner=planner)

def test_a_failed_brief_send_does_not_run_the_planner_again(conn, cfg):
    runs, sent, fail = [], [], [True]
    def mailer(cfg, to, subject, text, html):
        if fail[0]:
            fail[0] = False
            raise OSError("smtp down")
        sent.append(text)
    pl = SimpleNamespace(run=lambda conn, cfg, today, calendar=None: runs.append(today) or {"ok": True, "verdict": "Verdict here.", "applied": [], "staged": [], "cost_usd": 0.3})
    s = _brief_sched(cfg, conn, mailer, pl)
    s.tick(datetime(2026, 11, 2, 6, 31))
    assert len(runs) == 1 and sent == [] and get_settings(conn).get("state.last_fail.morning_brief")
    s.tick(datetime(2026, 11, 2, 7, 40))          # the hourly retry
    assert len(runs) == 1 and len(sent) == 1 and "Verdict here." in sent[0]
    s.tick(datetime(2026, 11, 3, 6, 31))          # a new day plans again
    assert len(runs) == 2 and len(sent) == 2

def test_a_planner_that_failed_is_not_retried_with_the_brief(conn, cfg):
    runs, sent, fail = [], [], [True]
    def mailer(cfg, to, subject, text, html):
        if fail[0]:
            fail[0] = False
            raise OSError("smtp down")
        sent.append(text)
    def boom(conn, cfg, today, calendar=None):
        runs.append(today)
        raise RuntimeError("claude down")
    s = _brief_sched(cfg, conn, mailer, SimpleNamespace(run=boom))
    s.tick(datetime(2026, 11, 2, 6, 31)); s.tick(datetime(2026, 11, 2, 7, 40))
    assert len(runs) == 1 and len(sent) == 1 and "claude down" in sent[0]
```

- [ ] **Step 2: Run to verify they fail**

Run: `/home/robert/factotum/.venv/bin/pytest tests/test_stage1.py -k brief -v`
Expected: FAIL (`len(runs) == 2` after the retry).

- [ ] **Step 3: Implement in `Scheduler._brief`.** Read `state.brief.planner` from `get_settings(self.conn)`; when its `day` is today, use its `result` and do not call the planner. Otherwise run the planner as today (an exception becomes `{"ok": False, "error": ...}` as now), then `set_setting(self.conn, "state.brief.planner", {"day": ..., "result": <the six keys above>})` — the dict itself, never `json.dumps` — and `self.conn.commit()` BEFORE `brief.send`, so `_run`'s rollback on a failed send keeps it. The returned summary line is unchanged. Docstring: one line saying why (the planner is a paid run; the send retries hourly).

- [ ] **Step 4: Run the tests**

Run: `/home/robert/factotum/.venv/bin/pytest -q`
Expected: all pass, including `tests/test_phase4.py::test_scheduler_runs_the_planner_before_the_brief_and_sends_the_plain_brief_when_it_fails`.

- [ ] **Step 5: Commit** (`scheduler: the brief keeps the day's planner result (state.brief.planner, committed before the mail goes out), so a failed send retried hourly no longer pays for the planner again each time` + trailer).

---

### Task 4: The installable app, pinned

Implementer: **sonnet**. UI task: **no** (the page head is pinned by tests; nothing visible changes).

**Files:**
- Modify: `factotum/web/index.html:8-15` (comments only)
- Modify: `factotum/api.py` (`NoCacheStatic.get_response`)
- Create: `tools/make_icons.py`
- Test: `tests/test_stage1.py`

**Interfaces:**
- Consumes: `api.create_app(cfg, db_path=...)`, `api.WEB`.
- Produces: `tools/make_icons.py` with `png(size: int, dot: float = 0.26) -> bytes` and a CLI `python tools/make_icons.py [OUT_DIR] [--data-uri]` (default OUT_DIR `factotum/web`; writes `icon-180.png`, `icon-192.png`, `icon-512.png`; `--data-uri` also prints `data:image/png;base64,…` of the 180 px icon for `index.html`). A test helper `_png_size(b: bytes) -> tuple[int, int]` in `tests/test_stage1.py` (asserts the PNG signature, returns the IHDR width and height).

- [ ] **Step 1: Write the failing tests** (section `# --- Task 4: the installable app`)
  - `test_the_page_head_installs_the_app(cfg, tmp_path)`: `GET /` with a `TestClient(api.create_app(cfg, db_path=str(tmp_path / "a.db")))`; the text contains `<link rel="manifest" href="/static/manifest.webmanifest" crossorigin="use-credentials">`, `<link rel="apple-touch-icon" sizes="180x180" href="data:image/png;base64,`, `<meta name="apple-mobile-web-app-title" content="Factotum">`, two `name="theme-color"` metas; `"SPIKE" not in r.text`; the inlined data URI decodes to bytes equal to `(api.WEB / "icon-180.png").read_bytes()`, and `_png_size` of them is `(180, 180)`.
  - `test_the_manifest_and_its_icons_are_served(cfg, tmp_path)`: `GET /static/manifest.webmanifest` is 200, `content-type` starts with `application/manifest+json`, the body parses as JSON with `name == "Factotum"`, `start_url == "/"`, `scope == "/"`, `display == "standalone"`; for every icon, `GET icon["src"]` is 200 with `content-type` `image/png` and `_png_size(body)` equals the numbers in `icon["sizes"]`; the purposes include `any` and `maskable`.
  - `test_the_icon_tool_draws_valid_icons()`: load `C.ROOT / "tools" / "make_icons.py"` with `importlib.util.spec_from_file_location`; for 180, 192 and 512, `_png_size(mod.png(n)) == (n, n)`.

- [ ] **Step 2: Run to verify they fail**

Run: `/home/robert/factotum/.venv/bin/pytest tests/test_stage1.py -k "installs or manifest or icon_tool" -v`
Expected: FAIL (`SPIKE` is in the page; `tools/make_icons.py` does not exist).

- [ ] **Step 3: Implement.**
  - `index.html`: replace the two `<!-- SPIKE … -->` comments with plain ones: the manifest is requested with the sign-in cookie (`use-credentials`), or the sign-in in front of the app answers it with its login page; the iPhone fetches the touch icon without the cookie, so it is inlined (`tools/make_icons.py --data-uri` regenerates it). Nothing else in the head changes.
  - `tools/make_icons.py`: the scratchpad script `/tmp/claude-1000/-home-robert-factotum/85fa6951-ca9c-416e-9691-1a412f0fadea/scratchpad/make_icons.py`, with the "Spike:" wording removed, the default output dir and `--data-uri` added. Standard library only.
  - `NoCacheStatic.get_response`: set `content-type: application/manifest+json` for a path ending in `.webmanifest` (the host's mime table decides otherwise).

- [ ] **Step 4: Check the tool reproduces the committed icons' size and run the suite**

Run: `cd /home/robert/factotum/.worktrees/linked-tasks && /home/robert/factotum/.venv/bin/python tools/make_icons.py /tmp/claude-1000/-home-robert-factotum/85fa6951-ca9c-416e-9691-1a412f0fadea/scratchpad/icons-check --data-uri | head -c 120 && /home/robert/factotum/.venv/bin/pytest -q`
Expected: three file names and the start of a data URI (create the folder first with `mkdir -p`); all tests pass. Do not overwrite the committed icons.

- [ ] **Step 5: Commit** (`web: the installable app adopted properly: plain comments instead of SPIKE ones, the manifest served as application/manifest+json whatever the host's mime table says, tools/make_icons.py to redraw the icons, tests that pin the head, the manifest and every icon it names` + trailer).

---

### Task 5: Task and tab addresses, the build stamp, a reminder's Open

Implementer: **sonnet**. UI task: **no** (server only; the page reads the new addresses in Task 6).

**Files:**
- Modify: `factotum/api.py` (`index`, new `PAGE_TABS`, `STAMPED`, `build_stamp`, `_index_html`, `BuildStamp`, `reminder_action`, `_page`)
- Modify: `factotum/web/index.html` (one meta line)
- Modify: `factotum/reminders.py:90` (the optional click)
- Modify: `tests/test_core.py:538`
- Test: `tests/test_stage1.py`

**Interfaces:**
- Consumes: `reminders.act(...)` returning `{"action": "open", "task_id": int, ...}`.
- Produces (module level in `factotum/api.py`):
  - `PAGE_TABS = ("today", "timeline", "projects", "review", "chat", "log", "settings")`
  - `STAMPED = ("index.html", "app.js", "app.css", "manifest.webmanifest")`
  - `build_stamp() -> str`: `hashlib.sha1("|".join(str((WEB / f).stat().st_mtime_ns) for f in STAMPED).encode()).hexdigest()[:12]`, read per call (reads the module global `WEB`, so a test can point it elsewhere).
  - `_index_html() -> str`: today's `index()` body (the `?v=<mtime>` stamps) plus `<meta name="factotum-build" content="">` filled with `build_stamp()`.
  - `class BuildStamp`: pure ASGI middleware (like `JsonOnlyMutations`, never `BaseHTTPMiddleware`: the chat stream must stay untouched) that adds the header `x-factotum-build: <build_stamp()>` to the `http.response.start` message of every request whose path starts with `/api/`. Added after `JsonOnlyMutations`, so it is outermost and a 415 carries it too.
  - `_page(title, text, base, undo=False, task_id: int | None = None)`: with `task_id`, the link is `{base}/t/{task_id}` labelled `Open Task-{task_id}`; without, `{base}/` labelled `Open Factotum`.
  - Routes: `GET /` and `GET /<tab>` for each of `PAGE_TABS` and `GET /t/{task_id:int}` all answer `_index_html()` with `Cache-Control: no-cache, must-revalidate`. Register the tab routes one explicit path each (`app.add_api_route(f"/{tab}", …, include_in_schema=False)`), never a `/{tab}` pattern, which would shadow `/mcp`. A non-numeric `/t/abc` is 404.
  - `index.html` head gains `<meta name="factotum-build" content="">` (after the theme-color metas).

- [ ] **Step 1: Write the failing tests** (section `# --- Task 5: addresses and the build stamp`)
  - `test_task_and_tab_addresses_answer_with_the_app(cfg, tmp_path)`: for `/`, `/today`, `/timeline`, `/projects`, `/review`, `/chat`, `/log`, `/settings`, `/t/42`: status 200, `'id="panel"' in r.text`, `"/static/app.js?v=" in r.text`, `r.headers["cache-control"] == "no-cache, must-revalidate"`; `/t/abc` and `/nowhere` are 404; `GET /mcp` is still 405.
  - `test_every_api_response_carries_the_build_stamp(cfg, tmp_path)`: `s = api.build_stamp()`; `len(s) == 12`; `GET /api/tasks` has `headers["x-factotum-build"] == s`; `POST /api/batches/apply` with `content=b"{}"` and no JSON content type is 415 and carries the header too; `GET /` contains `<meta name="factotum-build" content="{s}">`.
  - `test_the_build_stamp_follows_the_web_files(tmp_path, monkeypatch)`: copy the four `STAMPED` files into `tmp_path`, `monkeypatch.setattr(api, "WEB", tmp_path)`; `a = api.build_stamp()`; `api.build_stamp() == a`; `os.utime(tmp_path / "app.js", ns=(…+10**9, …+10**9))` changes it.
  - `test_a_reminders_open_goes_to_its_task(cfg, tmp_path)`: set up one task with a reminder fired as `tests/test_core.py::test_reminder_action_links_are_single_use_and_expire` does (`batches.apply(... "add_reminder" ...)`, `reminders.fire(c, cfg, datetime(2026, 11, 3, 9, 0, 30), lambda *a, **k: "m1")`, two tasks with one reminder each); `client.get(f"/r/{tok}", params={"a": "open"}, follow_redirects=False)` is a redirect whose `location` ends with `/t/1`; `?a=done` on a second reminder's token returns a page containing `/t/<its task id>"` and `Open Task-<id>`.

- [ ] **Step 2: Run to verify they fail**

Run: `/home/robert/factotum/.venv/bin/pytest tests/test_stage1.py -k "addresses or build_stamp or reminders_open" -v`
Expected: FAIL (404 on `/t/42`, no header, `/#today` location).

- [ ] **Step 3: Implement** the Interfaces above. `reminder_action`: Open redirects to `{base}/t/{task_id}` (or `/t/{task_id}` with no base); Done and Snooze pages pass `task_id=res["task_id"]`; a `TokenError` page links `{base}/`. `reminders.fire`: the optional click (`notify.ntfy.tap_opens_app`) becomes `f"{base}/t/{r['task_id']}"`; update `tests/test_core.py:538` to `.endswith("/t/1")`.

- [ ] **Step 4: Run the suite**

Run: `/home/robert/factotum/.venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit** (`api: a task has an address (/t/<id>) and so does every tab, all answered with the app; every /api response carries X-Factotum-Build (a hash of the web files' mtimes) and the page the stamp it loaded with; a reminder's Open and the page after Done or Snooze go to the task, not Today` + trailer).

---

### Task 6: The page reads its address; Back works between tabs

Implementer: **opus**. UI task: **yes**.

**Files:**
- Modify: `factotum/web/app.js` (startup at line ~34, `jumpTo`, `sendChat`, the `[data-goto-chat]` handler, `reviewAsk`'s "Open in Chat" link, the `#tabs` click handler, the final `load().then(...)`)
- Modify: `factotum/scheduler.py:162` (`_review_push` click), `factotum/brief.py:125` ("Open Review"), `factotum/api.py:106` (consent page's Settings link)
- Test: `tests/test_stage1.py`; scenario `scenarios/s1-6-addresses.mjs`

**Interfaces:**
- Consumes: the server routes of Task 5 (`/`, `/<tab>`, `/t/<id>` all serve the app).
- Produces (in `app.js`, section `// ---------- addresses (stage 1) ----------` placed before `// ---------- loading ----------`):
  - `addressOf(tab: string) -> string` — `'/'` for `today`, else `'/' + tab`.
  - `parseAddress() -> { tab: string, task: number | null, legacy: boolean }` — from `location.pathname` and, only when the path is `/`, a `location.hash` naming a tab (`legacy: true`); `/today` → `today`; `/t/<digits>` → `{ tab: 'today', task: n }`; anything else → `today`.
  - `setAddress(path: string, replace?: boolean)` — `history.pushState(null, '', path)` (or `replaceState`) unless `location.pathname + location.hash === path` already.
  - `go(tab: string)` — `ui.tab = tab; ui.laterOpen = false; setAddress(addressOf(tab)); render();`.
  - `applyAddress(ev?: PopStateEvent)` — called once with no argument after the first `load()`, and as the `popstate` listener (with the event, so Task 7 can tell a startup address from a pop). A tab address sets `ui.tab` and renders (a `legacy` or `/today` address is rewritten with `replace`). A task address runs `jumpTo(id, true)` and then `setAddress(addressOf(ui.tab), true)`; Task 7 changes this branch on phones.
- Every `location.hash = …` assignment in `app.js` goes: the tabs click handler calls `go(b.dataset.tab)`; `jumpTo` calls `setAddress(addressOf(hit.tab))` when it changes tab; `sendChat` and the `[data-goto-chat]` handler use `go('chat')` (the latter after setting `chat.current` and `chat.threads = null`). `reviewAsk`'s link becomes `href="/chat"` (still `preventDefault`ed). The initial `ui.tab` comes from `parseAddress().tab`.
- Server links name the real addresses: review push click `f"{base}/review"`, brief `{base}/review`, consent page `{base}/settings`.

- [ ] **Step 1: Write the failing Python test** (section `# --- Task 6: server links name real addresses`):

```python
def test_server_links_name_real_addresses(conn, cfg):
    from datetime import date
    from factotum import brief
    from factotum.scheduler import Scheduler
    upsert_task(conn, task(id=1, due=None, created_at="2026-10-30T00:00:00+00:00", last_mention_at="2026-10-30")); conn.commit()
    html = brief.build(conn, cfg, TODAY)["html"]
    assert 'https://factotum.nthperson.dev/review"' in html and "/#review" not in html
    clicks = []
    Scheduler(cfg, conn, push=lambda cfg, title, body, click=None, **k: clicks.append(click))._review_push(cfg, date(2026, 11, 1), {})
    assert clicks and clicks[0].endswith("/review")
```
(The task row is the one `tests/test_core.py::test_review_push_fires_once_a_day_after_its_time_and_only_with_a_queue` uses: it puts one item in the queue.)

- [ ] **Step 2: Run it to verify it fails**, then change the three Python links, then run `/home/robert/factotum/.venv/bin/pytest -q` (all pass).

- [ ] **Step 3: Implement the address functions and replace every `location.hash` use** as in Interfaces. `window.addEventListener('popstate', applyAddress)`. The final line becomes `load().then(() => { applyAddress(); return loadSettings(); }).catch(…)` (the catch is unchanged).

- [ ] **Step 4: Write `scenarios/s1-6-addresses.mjs` and run it for both devices** (`serve.sh restart` first: Python changed). It drives:
  1. `goto(base + '/')`, wait for `#panel .row`; shot `s1-6-today`.
  2. Click `#tabs button[data-tab="timeline"]`; log `location.pathname` (must be `/timeline`); click `log`; log pathname (`/log`).
  3. `page.goBack()`; log pathname (`/timeline`) and which tab has `aria-selected="true"` (`timeline`); shot `s1-6-back-timeline`. `goBack()` again: `/`, Today selected.
  4. `goto(base + '/#settings')`: pathname `/settings`, `location.hash === ''`, Settings selected; shot `s1-6-legacy-hash`.
  5. Find an open task id: `await page.evaluate(() => fetch('/api/tasks').then(r => r.json()).then(d => d.tasks[0].id))`. `goto(base + '/t/' + id)`; wait 500 ms; log pathname (`/` or `/timeline`) and whether `#panel .row.open[data-id="<id>"]` exists (it must); shot `s1-6-task-address`.
  Expected in the screenshots: `s1-6-back-timeline-*` shows the Timeline tab selected; `s1-6-legacy-hash-*` shows Settings; `s1-6-task-address-*` shows the task's row expanded in place (on the phone too, until Task 7). The "problems" list must be empty apart from what the baseline already shows.

- [ ] **Step 5: Commit** (`web: the page reads its address and follows it: tabs are real paths (/ is Today), each tab change is a history entry so Back and Forward move between tabs, old /#tab links are rewritten in place, /t/<id> opens the task's row; the review push, the brief and the consent page link real addresses` + trailer).

---

### Task 7: The phone task page

Implementer: **opus**. UI task: **yes**.

**Files:**
- Modify: `factotum/web/app.js` (`ui` state, `row`, new `chipsHtml`, `detailsHtml`, `taskPageHtml`, `openTask`, `closeTask`; `render`, `wire`, `composer`, `jumpTo`, `applyAddress`, `sendChat`, the keydown handler)
- Modify: `factotum/web/app.css` (task page, `body.page-on`)
- Scenario: `scenarios/s1-7-task-page.mjs`

**Interfaces:**
- Consumes: `parseAddress`, `setAddress`, `addressOf`, `applyAddress`, `go` (Task 6); `GET /api/tasks/{id}` (existing: a decorated task with `links`, `reminders`, or 404).
- Produces:
  - `ui.page: number | null` (the task shown full-screen), `ui.pageTask: object | null` (the fetched row when the id is not in `data.tasks`; `{ missing: true }` after a 404), `ui.pageScroll: number`.
  - `chipsHtml(x, extraChip) -> string` and `detailsHtml(x) -> string`: the two pieces of today's `row()` markup (the `.meta` chips; the two `<dl>`s and `.actions`), moved out unchanged so `row()` renders byte-for-byte what it renders today. `detailsHtml` omits `.actions` when `x.status !== 'open'`.
  - `taskPageHtml() -> string`: `<div class="taskpage" data-id="…">` with a bar (`<button class="btn" id="page-back">‹ Back</button>` and `<span class="code">Task-N</span>`), `<h2>` name, the chips, then `<div class="details">` + `detailsHtml(x)`. For a closed task a line `This task is done.` / `This task is dropped.` (from its status) above the details; for `{missing: true}` only `<div class="empty">Task-N does not exist.</div>` under the bar; while the fetch runs, `<div class="empty">Loading Task-N…</div>`.
  - `openTask(id: number, push = true)`: not `PHONE()` → `jumpTo(id, true)` and return. Else save `ui.pageScroll = window.scrollY`, set `ui.page = id`, `ui.pageTask = null`, `push` → `setAddress('/t/' + id)`, `render()`, `scrollTo(0, 0)`; when `byId(id)` is missing, fetch `/api/tasks/${id}` through `api()` into `ui.pageTask` (a 404 → `{ missing: true }`) and render again if `ui.page` is still `id`.
  - `closeTask()`: `ui.page = null`, `render()`, `scrollTo(0, ui.pageScroll)`.
- Behaviour:
  - `render()`: when `ui.page !== null` the panel is `taskPageHtml()` alone (no pending banners, no composer); `document.body.classList.toggle('page-on', ui.page !== null)`.
  - `wire()`, per row, on `PHONE()` only: a click on `.main` that is not on `input, button, .chip.due` calls `openTask(id)`; `[data-chev]` calls `openTask(id)`; the checkbox still toggles selection. Desktop keeps the 400 ms double-click logic and the chevron expanding in place, unchanged.
  - `#page-back` → `history.back()`. Esc while `ui.page !== null` → `history.back()`.
  - `applyAddress(ev)`: a task address on `PHONE()` at startup (`ev` undefined) replaces the entry with `/` and pushes `/t/<id>`, then `openTask(id, false)`; on a later `popstate` to `/t/<id>`, `openTask(id, false)`; on a `popstate` to a tab address while `ui.page !== null`, `closeTask()` after setting the tab. Desktop is as in Task 6.
  - `jumpTo(id, true)` on `PHONE()` calls `openTask(id)` (a Top tile's double-tap opens the page). `sendChat` (not from Review) sets `ui.page = null` before `go('chat')`.
  - `composer()` on `PHONE()`: the touch hint reads `Tap a row to open it · tick the box to select it`; the empty-attachment line reads `Nothing attached · tick a row to attach it`. Elsewhere the texts are today's.
  - CSS, inside `@media (max-width: 700px)`: `body.page-on header.bar, body.page-on aside.rail { display: none; }`; the main grid has one column; `.taskpage` has top padding of `max(12px, env(safe-area-inset-top))`, its bar is a row with the Back button at a 44 px touch height, `h2` 20 px, and `.taskpage .details` is always shown as one column (`display: grid`), on the surface colour with the card border and radius. Nothing horizontal scrolls at 390 px.

- [ ] **Step 1: Refactor `row()` into `chipsHtml` + `detailsHtml` without changing its output.** Check: on desktop, `s1-7` step 6 below must match `shots/baseline-row-open-desktop.png`.
- [ ] **Step 2: Add the page state, `taskPageHtml`, `openTask`, `closeTask`, the `render` and `wire` branches, the CSS.**
- [ ] **Step 3: Change `applyAddress`, `jumpTo`, `sendChat`, the composer texts and the Esc branch** as above.
- [ ] **Step 4: Run the suite** (`/home/robert/factotum/.venv/bin/pytest -q`, all pass: nothing Python changed, but it must stay green).
- [ ] **Step 5: Write `scenarios/s1-7-task-page.mjs` and run it for both devices.** It drives, on the phone:
  1. `goto('/')`; scroll the window 300 px; tap the third row's `.name`; log pathname (`/t/<id>`) and whether `header.bar` is hidden; shot `s1-7-page`.
  2. Tap `#page-back`: pathname `/`, `window.scrollY` back near 300 (log it); shot `s1-7-back`.
  3. Tap a row's `.name` again, then `page.goBack()` (the phone's Back): pathname `/`, the list shows.
  4. Tap a row's checkbox: no page, pathname `/`, the row has class `sel`, the bulk bar is up; shot `s1-7-tick`. Clear it (`[data-bulk="clear"]`).
  5. Open a page and tap `[data-act="snooze"]` on it: the staged chip shows on the page and the tray is at the bottom; shot `s1-7-page-staged`; tap `#tray-discard`.
  6. `goto('/t/<open id>')` directly: the page shows; `page.goBack()` lands on `/` with Today (not `about:blank`); shot `s1-7-direct-back`.
  7. `goto('/t/999999')`: "Task-999999 does not exist."; shot `s1-7-missing`. Find a closed id with `fetch('/api/tasks?status=done')` (first row's id; skip with a log line if none) and `goto('/t/<it>')`: "This task is done." and no action buttons; shot `s1-7-closed`.
  On the desktop: `goto('/')`; click a row's `.name` once (it is selected, pathname stays `/`, no page); click `.name` twice within 400 ms (the row expands in place); shot `s1-7-desktop`; `goto('/t/<open id>')` expands the row in place as in Task 6.
  Expected: `s1-7-page-phone` shows a full-screen page (no tab strip) with ‹ Back, the code, the name, chips, Score / Why now / Context / Source / Links / Lifecycle / Reminders and the action buttons, all within 390 px; `s1-7-back-phone` shows the list where it was; `s1-7-tick-phone` shows a selected row and no page; `s1-7-desktop-desktop` looks like `baseline-row-open-desktop`. Say in the report what each screenshot shows.
- [ ] **Step 6: Commit** (`web: on a phone a tap opens a task as a full-screen page (rendered into the panel; the list keeps its scroll) and the tick box selects; ‹ Back, the phone's Back and Esc close it; /t/<id> opened straight from a reminder puts Today beneath it; a closed or unknown task says so; desktop unchanged` + trailer).

---

### Task 8: One thing at the bottom of a phone screen

Implementer: **opus**. UI task: **yes**.

**Files:**
- Modify: `factotum/web/app.js` (`composer`'s bulk bar, the bulk wiring in `wire`, `ui.bulkMore`)
- Modify: `factotum/web/app.css`
- Scenario: `scenarios/s1-8-bottom.mjs`

**Interfaces:**
- Consumes: `PHONE()`, `act(id, kind, extra)`, the `[data-bulk]` handler (unchanged semantics: `done`, `drop`, `snooze`, `setdue` with `#bulk-date`, `cleardue`, `top`, `longterm`, `clear`).
- Produces: `ui.bulkMore: boolean` (reset to `false` whenever the selection becomes empty); on `PHONE()` the bulk bar markup is `<div class="bulk short" id="bulk">`: `<b id="bulk-n"></b>`, buttons `Done` (`data-bulk="done"`), `Drop` (`drop`), `Snooze` (`snooze`), `More` (`id="bulk-more"`, `aria-expanded`), `✕` (`data-bulk="clear"`, `aria-label="Clear selection"`); with `ui.bulkMore`, a second line holds the date input `#bulk-date` with `Set due`, `Remove due date`, `Pin to Top`, `Long-term`. Desktop's seven-action bar is unchanged.
- Behaviour on phones: while rows are selected the short bar is the only thing at the bottom — the tray slides away (`@media (max-width: 700px) { body.bulk-on .tray { transform: translateY(110%); } }`) and the bar sits at the bottom edge (in `wire()`, the `bulk.style.bottom` offset above the tray applies only when not `PHONE()`); clearing the selection brings the tray back with everything it held. The short bar fits on one line at 390 px (buttons keep the 36 px touch height).

- [ ] **Step 1: Implement** the markup branch, `#bulk-more` toggle (re-render), the reset of `ui.bulkMore`, the CSS.
- [ ] **Step 2: Run the suite** (all pass).
- [ ] **Step 3: Write `scenarios/s1-8-bottom.mjs` and run it for both devices.** Phone:
  1. Tick two rows' checkboxes; log `#bulk`'s bounding-box height (one line: under 60 px) and whether `#tray` is visible; shot `s1-8-short-bar`.
  2. Tap `#bulk-more`; shot `s1-8-more`. Tap `[data-bulk="clear"]`: the bar is gone.
  3. Tick one row and tap `Done` in the bar: the tray shows alone at the bottom; shot `s1-8-tray`.
  4. Tick another row: the short bar shows and the tray is hidden; shot `s1-8-bar-over-tray`. Clear: the tray is back with `1 change`; shot `s1-8-tray-back`. Tap `#tray-discard`.
  Desktop: tick two rows: the full bar (Done, Drop, Snooze to tomorrow, date, Set due, Remove due date, Pin to Top, Long-term, Clear) floats above the tray exactly as before; shot `s1-8-desktop`.
  Expected: on the phone screenshots exactly one of {short bar, tray} is at the bottom, never both, and nothing scrolls sideways; `s1-8-desktop` matches today's bar.
- [ ] **Step 4: Commit** (`web: on a phone the bottom of the screen holds one thing at a time: the tray when changes are staged, a short bar (Done, Drop, Snooze, More) while rows are selected instead of the seven-action bar that wrapped onto several lines; desktop unchanged` + trailer).

---

### Task 9: Signed out

Implementer: **opus**. UI task: **yes**.

**Files:**
- Modify: `factotum/web/index.html` (the screen, beside `#toast`)
- Modify: `factotum/web/app.js` (`api`, `sendChat`'s fetch, new `signedOut`)
- Modify: `factotum/web/app.css`
- Scenario: `scenarios/s1-9-signed-out.mjs`

**Interfaces:**
- Consumes: nothing from earlier tasks beyond the real addresses (Task 6), which make a reload land where the user was.
- Produces:
  - `index.html`: `<div class="signedout" id="signedout" role="alertdialog" aria-modal="true" aria-labelledby="so-title" hidden><div class="box"><h2 id="so-title">Signed out</h2><p>Your sign-in has expired. Changes waiting in the tray are kept.</p><button class="btn primary" id="signin">Sign in again</button></div></div>`
  - `signedOut()`: shows `#signedout` (once), focuses `#signin`; `#signin` click → `location.reload()`.
  - `api(path, opts)`: fetches with `redirect: 'manual'` (in the defaults that `opts` may override); a response with `r.type === 'opaqueredirect'` or status 401 or 403 calls `signedOut()` and throws `new Error('signed out')`; otherwise as today. `sendChat`'s own `fetch('/api/chat/turn', …)` gets `redirect: 'manual'` and the same check before reading the stream.
  - CSS: `.signedout { position: fixed; inset: 0; z-index: 20; display: grid; place-items: center; background: var(--bg); padding: max(24px, env(safe-area-inset-top)) 16px; }` and `.signedout[hidden] { display: none; }` (without it the `display: grid` beats the `hidden` attribute); the box centred, max-width 420 px, the button full width on a phone.

- [ ] **Step 1: Implement.**
- [ ] **Step 2: Run the suite** (all pass).
- [ ] **Step 3: Write `scenarios/s1-9-signed-out.mjs` and run it for both devices.** The trigger is a command: fill `#cmd` with `top 1` and click `#cmd-go` (it calls `POST /api/commands`; with the route below nothing reaches the server, so nothing is staged).
  1. `goto(base + '/log')`, wait for the Log. `context.route('**/api/**', r => r.fulfill({ status: 302, headers: { location: 'https://team.cloudflareaccess.com/cdn-cgi/access/login/factotum' } }))`. Trigger. `#signedout` is visible; shot `s1-9-signed-out`.
  2. `context.unroute('**/api/**')`; click `#signin`: the page reloads at `/log` and the screen is gone; shot `s1-9-after-signin`.
  3. Route `**/api/**` to `fulfill({ status: 502, body: 'Bad gateway' })`; trigger: no `#signedout` (log its `hidden`); unroute.
  4. `context.setOffline(true)`; trigger: no `#signedout`; `context.setOffline(false)`.
  5. Route to `fulfill({ status: 401 })`; trigger: `#signedout` shows; shot `s1-9-401`; unroute.
  If Playwright cannot deliver the fulfilled 302 as an `opaqueredirect` (step 1 shows no screen), say so in the report, check with `page.evaluate` what `fetch('/api/tasks', { redirect: 'manual' })` returns under that route, and do not weaken the detection to cover failed fetches.
  Expected: `s1-9-signed-out-*` is a full-screen "Signed out" with one "Sign in again" button and nothing of the app reachable around it; `s1-9-after-signin-*` is the Log tab; steps 3 and 4 log the screen as hidden. The problems list will show the intercepted requests; name them in the report.
- [ ] **Step 4: Commit** (`web: an expired sign-in (an /api request answered with a redirect, 401 or 403) shows a full-screen "Sign in again" that reloads at the same address; a restarting server or a dead network does not` + trailer).

---

### Task 10: Fresh on return, and self-update

Implementer: **opus**. UI task: **yes**.

**Files:**
- Modify: `factotum/web/index.html` (the Update ready button in `header.bar`)
- Modify: `factotum/web/app.js` (`api`, new `BUILD`, `newBuild`, `noteBuild`, `typingNow`, `onReturn`; the `visibilitychange` listener)
- Modify: `factotum/web/app.css`
- Scenario: `scenarios/s1-10-return.mjs`

**Interfaces:**
- Consumes: the `<meta name="factotum-build">` and `X-Factotum-Build` of Task 5; `api()` and `signedOut()` of Task 9; `ui.page` of Task 7.
- Produces:
  - `index.html`: `<button class="btn small update" id="update" hidden title="A newer version of Factotum is on the server">Update ready</button>` right after `<div class="spacer"></div>` in `header.bar` (on a phone it sits at the right of the first header line; `.remind` is hidden there).
  - `const BUILD = (document.querySelector('meta[name="factotum-build"]') || {}).content || ''`; `let newBuild = false`; `noteBuild(r: Response)`: when `BUILD` and the response's `X-Factotum-Build` are both non-empty and differ, `newBuild = true`. `api()` calls it on every response before anything else.
  - `typingNow() -> boolean`: as in "Decisions" (`draft.cmd`, `draft.ask`, `draft.pick`, `ui.rvNote.text` trimmed non-empty; `ui.remOpen !== null`; `ui.dueOpen !== null`; `chat.busy`; `document.activeElement` an `input, textarea, select` inside `#panel`).
  - `onReturn()`, the only `visibilitychange` handler (it replaces today's, which only re-polled the summary): when the page became visible and `#signedout` is hidden —
    - `newBuild`: if `typingNow() || staged.size` show `#update`; else `location.reload()`.
    - else if `typingNow()`: only what today's handler did (`ui.tab === 'today'` → `clearTimeout(sumTimer); pollSummary()`), so a box being typed in is not re-rendered.
    - else: `logData = null` when on the Log tab, then `load()` (tasks, staged proposals, summary; redraws the open tab or task page). A Review session in progress keeps its page (`review` is not reset).
  - `#update` click → `location.reload()` (the tray survives: it is kept in `localStorage`).

- [ ] **Step 1: Implement.**
- [ ] **Step 2: Run the suite** (all pass).
- [ ] **Step 3: Write `scenarios/s1-10-return.mjs` and run it for both devices.** A helper brings the page "back to the front": `const ret = () => page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, get: () => false }); document.dispatchEvent(new Event('visibilitychange')); })`. Count `GET /api/tasks` requests with `page.on('request', …)`.
  1. `goto('/')`; `ret()`; wait 500 ms: one more `/api/tasks` request was made (log the count).
  2. Type `hello there` into `#cmd`; `ret()`: no new `/api/tasks` request; `#cmd` still holds `hello there` and keeps focus; shot `s1-10-typing-kept`. Clear the box (select all, Delete).
  3. `context.route('**/api/**', async r => { const res = await r.fetch(); await r.fulfill({ response: res, headers: { ...res.headers(), 'x-factotum-build': 'changed' } }); })`. `ret()` (this load notices the stamp), then `ret()` again: the page reloads (wait for `page.waitForEvent('load')`; log it).
  4. Still routed: tick a row and tap Done in the bulk bar (the tray holds 1 change). `ret()`, `ret()`: no reload; `#update` is visible; shot `s1-10-update-ready`.
  5. `context.unroute('**/api/**')`; click `#update`: the page reloads and the tray still shows `1 change`; shot `s1-10-after-update`. Tap `#tray-discard`.
  Expected: `s1-10-typing-kept-*` shows the typed text in the box; `s1-10-update-ready-*` shows "Update ready" in the header (on the phone, at the right of the first header line, not wrapping the tab strip) with the tray at the bottom; `s1-10-after-update-*` shows the tray still holding the change.
- [ ] **Step 4: Commit** (`web: bringing the app back to the front reloads the lists unless something is being typed; when the server's build stamp differs from the page's, the app reloads itself on the next return, or shows "Update ready" while something is typed or staged` + trailer).

---

### Task 11: The stage's documents

Implementer: **sonnet**. UI task: **no**.

**Files:**
- Modify: `SPEC.md`, `CLAUDE.md`, `docs/SETUP.md`

**Interfaces:**
- Consumes: everything above; the decisions section of this plan; the design §13.1 (what the spike showed).

- [ ] **Step 1: `SPEC.md`.**
  - §9: a paragraph "Stage 1 of the linked-tasks design (2026-11-10): the app shell" — installable (manifest with `use-credentials`, the iPhone icon inlined, `tools/make_icons.py`); addresses (`/`, `/<tab>`, `/t/<id>`, old `/#tab` links rewritten) with the history table from this plan; on a phone a tap opens a full-screen task page and the tick box selects, ‹ Back / the phone's Back / Esc close it, a closed or unknown task says so; one thing at the bottom of a phone screen (the tray, or the short bar while rows are selected); "Sign in again" on a redirect, 401 or 403 from `/api`; fresh lists on return unless something is typed; the build stamp (`X-Factotum-Build`, `<meta name="factotum-build">`, what it hashes) and "Update ready". Also correct the paragraph that says a tap on a row selects and a double-tap opens: that is now desktop only.
  - §6 "Commands" (or wherever the grammar is described): a command that takes no date with words left over is not a command and goes to Chat; `and` and commas separate numbers; `snooze` with unreadable words is not a command.
  - §10: a reminder's Open (and the optional tap) goes to `/t/<id>`; the page after Done or Snooze links the task. The brief: the planner runs at most once per brief day; its result is kept in `state.brief.planner` and reused when the send is retried.
- [ ] **Step 2: `CLAUDE.md`.**
  - Phase-status paragraph: append "Stage 1 of the linked-tasks design (2026-11-10, `docs/superpowers/specs/2026-11-10-linked-tasks-design.md`): the installable app, task and tab addresses with Back, the phone task page, the short selection bar, Sign in again, fresh on return, self-update by build stamp; the command leftover fault and the brief retry fault."
  - Table rows: "the installable app: manifest, icons, the page head" → design §13, SPEC §9 → `factotum/web/manifest.webmanifest`, `index.html`, `tools/make_icons.py`; "addresses, Back, the phone task page, the short bar, signed out, return and update" → SPEC §9 → `web/app.js` (`parseAddress`, `setAddress`, `go`, `applyAddress`, `openTask`, `closeTask`, `taskPageHtml`, `signedOut`, `typingNow`, `onReturn`), `api.py` (`PAGE_TABS`, `STAMPED`, `build_stamp`, `BuildStamp`, `_index_html`).
  - Gotchas, each dated 2026-11-10: the manifest must be requested with `crossorigin="use-credentials"` or Access answers it with its login page; the iPhone fetches the touch icon without the cookie, so it is inlined (a file behind Access drew a letter); `/api` must never answer 401, 403 or a redirect, because the page reads each as an expired sign-in; the build stamp covers `api.STAMPED` only — a new file the page loads joins that list and gets the `?v=` stamp, and a Python-only change reloads no page; tab and task addresses are real paths because a fragment may not survive the sign-in redirect, so a new link into the app names `/t/<id>` or `/<tab>`, never `/#…`; on a phone the task page is rendered into `#panel` (`ui.page`), so anything `wire()` installs on the panel works there too and nothing may assume the panel holds a list; the test suite never reads `.env` (`tests/conftest.py::_no_real_env`), so a new secret is named in `.env.example` and a test that needs one sets it with `monkeypatch.setenv`.
- [ ] **Step 3: `docs/SETUP.md`**: a new section "## 17. Installing the app on a phone and a desktop": iPhone (Safari → Share → Add to Home Screen; it opens full-screen; notifications come in stage 2), Android (Chrome → menu → Install app; not tested yet), desktop Edge or Chrome (the install icon in the address bar, or menu → Apps → Install). What was learned behind Cloudflare Access (§13.1 of the design): no second sign-in in the installed app; the manifest needs the cookie; the iPhone icon is inlined; when the Access session ends the app shows "Sign in again", so the Access session duration (§7) is how often that happens; the new paths `/t/*` and `/<tab>` sit under the same Access application and must not get a bypass policy. Updating needs nothing: the app reloads itself when it is next brought to the front. In §7's "Check:" paragraph add that the reminder's Open now opens the task.
- [ ] **Step 4: Run the suite** (all pass) and `git diff --stat` (only the three documents).
- [ ] **Step 5: Commit** (`docs (stage 1): SPEC §6, §9, §10; CLAUDE.md phase status, table rows and the 2026-11-10 gotchas; SETUP §17 installing the app` + trailer).
