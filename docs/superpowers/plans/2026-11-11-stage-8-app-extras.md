# Stage 8: App extras — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The installed app's icon shows how many items wait on Review, kept current by the open app and carried by the notifications Factotum sends anyway; a long press on the icon offers "New task" and "Review".

**Architecture:** One read-only definition of "the Review count" in a new module, `factotum/review_queue.py` (`queue`, `card_count`, `count`), used by `GET /api/review` (unchanged answer), a new `GET /api/review/count`, the evening review nudge, and `app_push.send_all` / `send_test`. The page shows that count beside "Review" from its first load and mirrors it onto the icon with `navigator.setAppBadge` / `clearAppBadge`. A push that is being sent anyway gets the count as the declarative payload's top-level `app_badge`; Safari applies it without running the worker, and `sw.js` sets it where the worker runs. A Settings switch, `notify.app.badge` (on when absent), turns both off. The manifest gains `shortcuts` (New task → `/new`, Review → `/review`) with two 96 px icons drawn by `tools/make_icons.py`; the server answers `/new` with the page, which opens Today with New task (stage 6's opener) and replaces `/new` with `/`.

**Tech Stack:** Python 3.12, FastAPI, SQLite, pytest (offline, `httpx.MockTransport` for push services); vanilla JS/CSS with no build step; the notifications-only service worker; Playwright scenarios from the dev harness for rendered checks.

**Spec:** `docs/superpowers/specs/2026-11-10-linked-tasks-design.md` — §16 (the stage 8 line, binding: "a count of Review items on the app's icon; long-press shortcuts to New task and Review", and the checks for every stage with a surface), §13 (the installable app: the manifest with `use-credentials`, the inlined iPhone icon, real paths, Sign in again, self-update), §14 (the worker has no fetch handler; every push shows a notification; the declarative payload, defined twice), §15 (absent means off for what costs money or sends mail), §4 (words). Read `CLAUDE.md` in full. Stage 2's plan (`docs/superpowers/plans/2026-11-10-stage-2-notifications.md`) built the files Tasks 2 and 3 change.

**Builds on stage 6 Part B** (`docs/superpowers/plans/2026-11-11-stage-6-by-hand.md`, Tasks 10 and 12, being built while this plan is written). Its plan names: on a desktop the `n` key "leave Review / Chat / Log / Settings for Today, `ui.newOpen = true`, render, focus `#new-line`" and a button `#new-open` ("+ New task"); on a phone `openSheet()` / `closeSheet()`, the round button `#fab-new` and the sheet `#sheet`. Task 4 below opens New task through whatever code the `n` key runs, never a second way.

**Out of this stage:** a push sent only to change the number; offline caching of any kind (the worker still has no fetch handler); `launch_handler` or other manifest members; shortcuts beyond the two; a per-device badge switch; any change to `GET /api/review`'s answer.

## Global Constraints

- Work only in `/home/robert/factotum/.worktrees/linked-tasks` (branch `linked-tasks`), called WT, and never touch the main checkout (`/home/robert/factotum`, the live service on port 8750, serves from its files) or another worktree: never read from, write to, or run git in `/home/robert/factotum` outside `.worktrees/linked-tasks`. Every path below is relative to WT.
- Never open `config/factotum.yaml`, `.env` or `/home/robert/factotum/data`.
- Python is `/home/robert/factotum/.venv/bin/python`, tests `/home/robert/factotum/.venv/bin/pytest -q`, run from WT. Do not install packages (the icons are drawn with the standard library by `tools/make_icons.py`).
- No `sudo`, no `systemctl`, no pushes to a remote, no scheduler, no sweeps, no real Claude run (never `planner`, `sync-file`, `chat`, `chat-check`, `ripple`, `claude-check`).
- No network call to a real push service from a test or a scenario: tests pass an `httpx.MockTransport`; scenarios call the worker's handlers through `worker.evaluate` and never subscribe for real.
- Commit rules from CLAUDE.md: small commits on `linked-tasks`, the WHOLE suite green at every commit, test first for Python. Every commit message ends with the line `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` (CLAUDE.md's trailer, exactly; the commands below show the subject only, so add the trailer as a second `-m`). Message style: `area: what changed and why`.
- `/api` never answers 401, 403 or a redirect (the page reads each as an expired sign-in). Every state-changing `/api` call carries `Content-Type: application/json`.
- The service worker has NO fetch handler, ever. Every push shows a notification, none is silent; nothing about the badge may stop a notification from showing.
- A notification's look is defined twice (`app_push.payload` for Safari, `sw.js` for the rest): a change to what a push carries goes into both. Never add a `mutable` member.
- The dev copy's Settings hold the owner's real notification topic (and the fixture holds a real address): never copy a Settings value into a report, a commit, a scenario's log or a screenshot you describe; a Settings screenshot is an element screenshot of the one field this stage adds (Task 3 says how).
- The words "blocked" and "dependency" never appear on the page, in a notification or in a manifest string (`test_nothing_the_user_or_the_agent_reads_says_blocked_or_dependency` stays green).
- New Python tests go in `tests/test_stage8.py` (module docstring `"""Stage 8, app extras: the Review count on the icon, the shortcuts."""`), one `# --- Task N: …` comment per task, importing `TODAY`, `task`, `FIXTURE` from `conftest`.
- `app.js` stays one file; this stage's code goes under a new `// ---------- app extras (stage 8) ----------` section after the notifications (stage 2) section, plus the small hooks each task names.
- **How a UI task is verified** (there are no JS tests; a UI task is verified by rendering and looking). DEV = `/tmp/claude-1000/-home-robert-factotum/85fa6951-ca9c-416e-9691-1a412f0fadea/scratchpad/dev`.
  - Dev instance at http://127.0.0.1:8751, from WT, on a COPY of a real database (`DEV/factotum-dev.db`), with the test fixture config and NO scheduler. Restart after any Python change: `DEV/serve.sh restart > DEV/serve-status.txt 2>&1 < /dev/null`, then read `DEV/serve-status.txt`. Never pipe that script's output (`| tail`, `| head`): the pipe never closes and the command hangs. `factotum/web/` is served straight from WT (no restart for JS, CSS, the manifest or the icons; `/sw.js` is read from disk per request).
  - A real browser at desktop (1280x900, mouse) and phone (390x844, touch, an iPhone user agent on Chromium): `cd DEV && node pw.mjs scenarios/<name>.mjs --device both`. A scenario is `export default async ({ page, context, shot, base, device, log }) => { … }` (Playwright API: `page.addInitScript`, `context.route`, `context.grantPermissions`, `context.serviceWorkers()` / `context.waitForEvent('serviceworker')`, `worker.evaluate`, `context.newCDPSession(page)`); `shot('name')` saves `DEV/../shots/<name>-<device>.png`; console errors, failed requests and HTTP >= 400 are printed at the end (a stubbed one is named in the task as expected). Scenarios of this stage are `DEV/scenarios/s8-<task>-<name>.mjs` (not committed).
  - A headless browser has no app icon. The page's badge calls are stubbed and logged as an ARRAY, so a repeated call is visible:
    `await page.addInitScript(() => { window.__badge = []; navigator.setAppBadge = n => { window.__badge.push(n); return Promise.resolve(); }; navigator.clearAppBadge = () => { window.__badge.push(0); return Promise.resolve(); }; });` and read with `page.evaluate(() => window.__badge)`. The worker's are stubbed the same way inside `worker.evaluate` (Task 2 gives the code).
  - The implementer runs each scenario for both devices, LOOKS at every screenshot (the Read tool shows images) and reports what was seen, which responses were stubbed, and `noSideScroll` (from `DEV/scenarios/s4b-lib.mjs`) for every phone shot, which must be `true`.
  - Leave the dev database as found: a scenario that changes a Settings switch sets it back AND deletes the override row it created (Task 3 gives the command). Never press Apply; never add a task.

## Decisions this plan makes

- **The Review count is defined once, read-only, in `factotum/review_queue.py`.** The number beside "Review" today comes only from `GET /api/review`, which the page loads only on the Review tab, and that route WRITES: it records the day's link-suggestion session (`suggestions.session`) and stamps `last_offered_at` on the page it shows, which is what `expire` acts on. Calling it just for a number would mark rows offered. So `review_queue.count(conn, cfg, today, exclude=())` = the length of Review's whole eligible queue (the same `lifecycle.build_review_queue` call with the same flags and `state.last_review_at`) + the waiting suggestion cards (unjudged evidence with no task, kind in `SUGGESTION_KINDS`), and `GET /api/review` builds its queue and its card list through the same module, so `count == total + len(suggestions)` for the same database (pinned by a test). `SUGGESTION_KINDS` moves to `review_queue` (`api.SUGGESTION_KINDS` stays as an alias). Cost if wrong: none for the data; one more annotate pass per page load (milliseconds, `/api/tasks` already does one).
- **One known difference, stated, not hidden:** `GET /api/review` records the day's link-suggestion session before it builds the queue; `count` does not write, so before Review was opened on a given day the count can differ from Review's own by the link suggestions that session adds. It agrees again once Review has been opened that day.
- **The review nudge counts what Review shows.** `Scheduler._review_push` builds its queue without the mail, Slack, planner and ripple flags, a second definition today ("Review: 3 items" when Review shows 4). It now calls `review_queue.queue(conn, ecfg, today)`; its text keeps its shape. Cost if wrong: a nudge on a day when only flagged items wait, which is what Review would show anyway.
- **The page shows the count beside "Review" from the first load** (`GET /api/review/count` inside `load()`, which runs at start, after every Apply, on return to the front and after Refresh), and the loaded Review page's own number (which leaves out what the tray already decided) while Review is loaded, as today. Before this stage the tab showed no number until Review was opened. The icon always shows exactly the number the tab shows. `load()` passes the same `exclude` (the tray's decided task ids) that `loadReview` passes.
- **`notify.app.badge`, a Settings key, `True` in `config.DEFAULTS`** ("Count on the app icon" in Settings → Notifications). It does not break the stage 3 rule "absent means off for anything that costs money or sends mail": the badge makes no Claude run, sends no mail, writes to no outside service and never causes a push; it only adds an integer to pushes the owner already chose to receive (inside their RFC 8291 encryption, so the push service cannot read it) and to the icon of the owner's own device. It is not in `PAID_SWITCHES` (that tuple is also what `effective --as-before` flips). Precedent for a `True` default: `notify.ntfy.enabled`. The starter sends nothing with it on, because the starter has no provider and no device. Off: the open page clears the icon; pushes carry no `app_badge` (a closed app keeps the last number it was given until it is opened).
- **Which pushes carry it:** every push that goes through `app_push.send_all` (a reminder, "Task-N is free now", the evening review nudge) and Settings' "Send a test" (`send_test`), which also makes the owner's real-device check possible. They get it in ONE place, `app_push.with_badge(conn, cfg, notice)`, called by `send_all` (after its no-device early return) and `send_test`; no caller computes a count. ntfy messages carry nothing new. No push is ever sent because the number changed (iOS requires every push to show a notification, and a silent one would cost the subscription).
- **The count a push carries** is `review_queue.count(conn, cfg, date.today())` (the API's day, no `exclude`: the server does not know the tray). A count that raises is left out of that push, never a reason not to send it.
- **The declarative member is top-level `app_badge`, a JSON integer >= 0.** WebKit first read it inside `notification` (the 2025 "Meet Declarative Web Push" example: `"app_badge": "1"` nested, a string), then moved it to the top level (WebKit bug 293457, 2025-05; bug 296770, 2025-08, reads it from the outer object and keeps the nested place only for migration; bug 296773 reported a crash in the badge update path for a nested value). The W3C draft that adds it (push-api PR #402) is still open and its text checks for an integer. So: top level, integer, never nested, never a string. If a Safari build rejects it, the message is not declarative for that build and is handed to the worker as an ordinary `push` (Push API "If declarativeResult is not failure", else fire a push event), whose handler shows the notification and sets the badge itself: the cost if wrong is that Safari runs the worker for every push, never a lost notification. Which iOS version first applies the top-level member is not known; the owner's check in SETUP says what to look for.
- **The worker** (`sw.js`): `badgeOf(json)` reads `json.app_badge` (an integer >= 0, else `null`); `setBadge(n)` calls `self.navigator.setAppBadge(n)` (or `clearAppBadge()` for 0) where they exist and NEVER throws or rejects; `handlePush` runs it beside `showNotification`, so an exception in the badge path cannot stop the notification. `shape()` is unchanged (the badge is not part of what the notification looks like).
- **Shortcuts:** `"shortcuts"` in `manifest.webmanifest`: "New task" → `/new`, "Review" → `/review`, each with one 96x96 PNG (`/static/icon-new-96.png`: a light plus; `/static/icon-review-96.png`: a light check mark; teal ground like the app icon, the glyph inside the centre 60% so a launcher's mask never cuts it). They are drawn by a new `shortcut(kind, size=96)` in `tools/make_icons.py` (the same supersampled, standard-library PNG writer as `png()`, which stays byte-for-byte unchanged), and the tool writes them with the three app icons.
- **`/new` is a page address, not a tab.** `api.SHORTCUT_PATHS = ("new",)` beside `PAGE_TABS`, each answered by `index()`. On the page, `parseAddress` returns `{tab: 'today', task: null, legacy: false, add: true}` for `/new`; `applyAddress` then sets Today, REPLACES the address with `/` (no history entry: Back from there leaves the app, as from `/`), renders, and calls stage 6's New task opener. A reload therefore opens Today plainly. Signed out: the start-up `load()` fails before `applyAddress` runs, the existing "Sign in again" screen shows and the address stays `/new`, so the reload after signing in opens New task.
- **Limits stated in the documents, not worked around:** iOS ignores manifest `shortcuts`; an iPhone shows a badge only for the installed app, and only after notifications were allowed for it; Chromium (desktop and Android) re-reads an installed app's manifest lazily, so new shortcuts may need a reinstall; Firefox has no Badging API; Chrome on Android does not show a number from the Badging API (Android's own dot for unread notifications is not Factotum's); on a desktop the number shows only for the installed app, not a browser tab; the number is as fresh as the last time the app was open on that device or the last push it received.

## Review Focus

1. **Two definitions of the Review count** (the tab, the icon and a push disagree): Task 1 `test_one_review_count_for_the_tab_the_icon_and_the_pushes`, `test_the_review_nudge_counts_what_review_shows`; Task 2 `test_send_all_carries_the_count_unless_switched_off`.
2. **Counting writes** (stamping `last_offered_at` or recording the suggestion session would let `expire` lapse rows nobody saw): Task 1 `test_the_count_writes_nothing`.
3. **A push sent only to move the number**: Task 2 `test_the_count_rides_only_on_pushes_that_are_sent_anyway`, and a reading of every caller of `send_all` / `send_test` (no new caller).
4. **A badge failure that loses a notification** (the count raises on the server; `setAppBadge` throws or rejects in the worker; the payload grows past 3993 bytes): Task 2 `test_a_count_that_fails_never_stops_a_push`, `test_the_badge_is_a_top_level_integer_and_only_a_good_one`, scenario `s8-2-worker` cases W5–W6.
5. **`/new` reopening New task on a reload, or lost behind a sign-in**: Task 4 scenario cases N2–N4.
6. **The switch off but a number stays on the open app**: Task 3 scenario case B3.

---

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `factotum/review_queue.py` (new) | the Review count, defined once and read-only: `SUGGESTION_KINDS`, `queue`, `is_card`, `card_count`, `count` | 1 |
| `factotum/api.py` | `_review` reads through `review_queue`; `GET /api/review/count`; `SHORTCUT_PATHS` and the `/new` route | 1, 4 |
| `factotum/scheduler.py` | `_review_push` counts with `review_queue.queue` | 1 |
| `factotum/config.py`, `config/factotum.example.yaml`, `config/factotum.starter.yaml`, `tests/fixtures/factotum.fixture.yaml` | `notify.app.badge` | 1 |
| `factotum/app_push.py` | `payload` adds `app_badge`; `with_badge`; `send_all` and `send_test` use it | 2 |
| `factotum/web/sw.js` | `badgeOf`, `setBadge`; `handlePush` sets the badge | 2 |
| `factotum/web/app.js`, `factotum/web/app.css` | the count beside Review from the first load, `syncBadge`, the Settings switch, signed out; `/new` | 3, 4 |
| `factotum/web/manifest.webmanifest`, `factotum/web/icon-new-96.png`, `factotum/web/icon-review-96.png` (new), `tools/make_icons.py` | the shortcuts and their icons | 4 |
| `tests/test_stage8.py` (new) | every Python test of this stage | 1, 2, 4 |
| `DEV/scenarios/s8-*.mjs` (outside the repository) | the rendered checks | 2, 3, 4 |
| `SPEC.md`, `CLAUDE.md`, `docs/SETUP.md` | the stage's decisions, gotchas, owner checks | 5 |

| Task | Title | Implementer | UI |
|---|---|---|---|
| 1 | One Review count, and the switch | sonnet | no |
| 2 | The count in pushes that are sent anyway, and the worker | sonnet | no (one worker scenario) |
| 3 | The count on the open app's icon, and Settings | opus | yes |
| 4 | Long-press shortcuts, their icons, and `/new` | opus | yes |
| 5 | The stage's documents | sonnet | no |

The split differs from the controller's notes on one point: the notes put the switch and the page's badge first, but the page needs a count it can read without writing, and the push needs the same one, so the server's count (and the switch's Python side) come first as Task 1, and the page follows as Task 3.

---

### Task 1: One Review count, and the switch

Implementer: **sonnet**. UI task: no.

**Files:**
- Create: `factotum/review_queue.py`, `tests/test_stage8.py`
- Modify: `factotum/api.py` (`SUGGESTION_KINDS`, `_review`, a new route), `factotum/scheduler.py` (`_review_push`), `factotum/config.py` (`SETTINGS_KEYS`, `DEFAULTS`), `config/factotum.example.yaml`, `config/factotum.starter.yaml`, `tests/fixtures/factotum.fixture.yaml`

**What exists today (quoted):**
- `api.py`: `SUGGESTION_KINDS = ("proposal", "slack_mention")` at module level; inside `create_app`, `review_lock` and `def _review(exclude: str | None)`, which computes `last = parse_date(st.get("state.last_review_at"))`, `flags = review_flags(conn)` (`from .mail_sweep import review_flags`), `full = lifecycle.build_review_queue(open_tasks, today, ecfg, last, flags_by_id=flags, cap=10**9)` over `open_tasks = decorate_all([...], today, ecfg)`, and the cards as `[... for e in pending if e["task_id"] is None and e["kind"] in SUGGESTION_KINDS]`; it answers `{"today", "last_review_at", "cap", "total": len(full), "labels", "items", "suggestions"}`.
- `scheduler.py`: `def _review_push(self, ecfg: dict, today: date, st: dict) -> str | None:` with `queue = lifecycle.build_review_queue(self._open(), today, ecfg, date.fromisoformat(last) if last else None, cap=10**9)` (no flags).
- `lifecycle.build_review_queue(tasks, today, cfg, last_review_at, flags_by_id=None, cap=None) -> list[dict]` (pure); `relations.annotate(db, tasks) -> list[dict]` (never writes); `mail_sweep.review_flags(conn) -> dict[int, dict]` (reads).

**Interfaces (produces):**

```python
# factotum/review_queue.py
"""Review's count, defined once (stage 8): the number beside "Review", the number on the app's icon and
the number a push carries all come from here, and GET /api/review builds its queue and its cards through
the same functions. Reads only. GET /api/review also writes (the day's link-suggestion session, the
offered stamp `expire` acts on), so it is never called just for the number."""
SUGGESTION_KINDS = ("proposal", "slack_mention")      # evidence with no task that Review offers as a Create / Dismiss card

def queue(conn, cfg: dict, today: date, open_tasks: list[dict] | None = None, flags: dict | None = None) -> list[dict]:
    """Review's whole eligible queue, in Review's order. `open_tasks`: the open rows, already annotated
    (relations.annotate, or the API's scored rows); None reads and annotates them. `flags`: review_flags(conn)
    when the caller has it; None reads it."""

def is_card(e: dict) -> bool:          # e["task_id"] is None and e["kind"] in SUGGESTION_KINDS

def card_count(conn) -> int:           # unjudged evidence rows that are cards

def count(conn, cfg: dict, today: date, exclude=frozenset()) -> int:
    """len(queue(...)) over the open rows not in `exclude` (the page's tray), plus card_count(conn)."""
```

- `api.py`: `SUGGESTION_KINDS = review_queue.SUGGESTION_KINDS`; `_review` uses `review_queue.queue(conn, ecfg, today, open_tasks, flags)` for `full` and `review_queue.is_card(e)` for the cards (its answer is unchanged); new route

```python
    @app.get("/api/review/count")
    def review_count(exclude: str | None = None):
        """The number beside Review and on the app's icon (review_queue.count). Reads only; no lock needed."""
        skip = {int(x) for x in (exclude or "").split(",") if x.strip().lstrip("-").isdigit()}
        return {"count": review_queue.count(conn, eff_cfg(), date.today(), exclude=skip)}
```

- `scheduler._review_push`: `queue = review_queue.queue(self.conn, ecfg, today)`; the rest unchanged.
- `notify.app.badge`: in `SETTINGS_KEYS` (after `"notify.provider"`) and `DEFAULTS` (`True`, with a comment `# stage 8: costs nothing, sends nothing (never a push of its own), so on when absent`); not in `PAID_SWITCHES`.
- Example, under `notify: app:` after `push_hosts`: `badge: true                 # Settings → Notifications ("Count on the app icon"). The number of Review items on the installed app's icon: kept by the open app and carried by notifications that are sent anyway, never a notification of its own. Costs nothing and sends nothing, so on when absent`. Starter: `notify: app: { badge: true }` (a line under `notify:`). Fixture: `app: { badge: true }` under `notify:`.
- No Settings control yet: Task 3 adds it (the CLAUDE.md "control in renderSettings" rule is met at Task 3's commit).

- [ ] **Step 1: Write the failing tests** in `tests/test_stage8.py` under `# --- Task 1: one Review count, and the switch`:

```python
"""Stage 8, app extras: the Review count on the icon, the shortcuts."""
import copy
import json
import os
from datetime import date, datetime

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from conftest import FIXTURE, TODAY, task
from factotum import api, app_push as AP, config as C, review_queue, webpush as W
from factotum.api import create_app
from factotum.db import get_settings, get_task, now_iso, upsert_task
from factotum.scheduler import Scheduler


# --- Task 1: one Review count, and the switch

def mk(c, i, **kw):
    upsert_task(c, task(id=i, name=f"T{i}", **kw))

def _card(c, name):
    c.execute("INSERT INTO evidence (task_id, kind, ref, account, label, excerpt, at) VALUES (NULL, 'proposal', ?, NULL, ?, 'r', ?)",
              (json.dumps({"task_id": None, "op": "create", "fields": {"name": name}}), f"create: {name}", now_iso()))

def test_one_review_count_for_the_tab_the_icon_and_the_pushes(cfg, tmp_path):
    client = TestClient(create_app(cfg, db_path=str(tmp_path / "a.db")))
    c = client.app.state.conn
    for i in (1, 2, 3):
        mk(c, i)                                   # never decided on: "new" on Review
    mk(c, 4, snoozed_until="2099-01-01")           # snoozed: not on Review
    mk(c, 5, status="done")
    _card(c, "Order the reagent")
    c.commit()
    n = client.get("/api/review/count").json()["count"]          # asked first: it must not depend on Review having been loaded
    assert n == 4
    assert review_queue.count(c, C.effective(cfg, get_settings(c)), date.today()) == n
    rv = client.get("/api/review").json()
    assert rv["total"] + len(rv["suggestions"]) == n
    assert client.get("/api/review/count").json()["count"] == n
    assert client.get("/api/review/count?exclude=1,2").json()["count"] == 2
    assert api.SUGGESTION_KINDS is review_queue.SUGGESTION_KINDS

def test_the_count_writes_nothing(cfg, tmp_path):
    client = TestClient(create_app(cfg, db_path=str(tmp_path / "a.db")))
    c = client.app.state.conn
    mk(c, 1); c.commit()
    before = c.total_changes
    assert client.get("/api/review/count").status_code == 200
    assert c.total_changes == before and get_task(c, 1)["last_offered_at"] is None

def test_the_review_nudge_counts_what_review_shows(conn, cfg):
    # decided on today, so not "new"; only the mail sweep's flag puts it on Review
    mk(conn, 1, last_reviewed_at=TODAY.isoformat(), last_mention_at=TODAY.isoformat())
    conn.execute("INSERT INTO evidence (task_id, kind, label, at) VALUES (1, 'thread_updated', 'Re: data', ?)", (now_iso(),))
    conn.commit()
    titles = []
    Scheduler(cfg, conn, push=lambda cfg, title, body, **k: titles.append(title))._review_push(cfg, TODAY, {})
    assert titles == ["Review: 1 item"]

def test_the_badge_switch_is_a_settings_key_and_on_when_absent():
    assert "notify.app.badge" in C.SETTINGS_KEYS and C.DEFAULTS["notify.app.badge"] is True
    assert C.value({}, "notify.app.badge") is True and "notify.app.badge" not in C.PAID_SWITCHES
    for path in (C.EXAMPLE_CONFIG, FIXTURE, C.STARTER_CONFIG):
        assert C.get(C.load(path), "notify.app.badge", "absent") is True, path.name
```

- [ ] **Step 2: Run them** — `/home/robert/factotum/.venv/bin/pytest tests/test_stage8.py -q`. Expected: FAIL (no module `review_queue`; the nudge sends nothing; the key is missing).
- [ ] **Step 3: Implement** `review_queue.py` (`queue` imports `review_flags` inside the function, as `_review` does, so importing the module stays cheap; it reads `state.last_review_at` with `get_settings` and `dates.parse_date`), the `_review` change, the route (next to `/api/review`), the nudge change, the key in the four places. `count` annotates only the rows it counts (`relations.annotate(conn, [t for t in rows(conn, "SELECT * FROM tasks WHERE status = 'open'") if t["id"] not in exclude])`), exactly as `_review` filters before `decorate_all`.
- [ ] **Step 4: Run the whole suite** — `/home/robert/factotum/.venv/bin/pytest -q`. Expected: all pass (stage 3's `test_every_settings_key_has_one_default_and_the_paid_ones_are_off`, the example check, `_code_defaults`; stage 4's `"Review: 3 items"` nudge test; stage 5's Review tests).
- [ ] **Step 5: Commit**

```bash
git add factotum/review_queue.py factotum/api.py factotum/scheduler.py factotum/config.py config/factotum.example.yaml config/factotum.starter.yaml tests/fixtures/factotum.fixture.yaml tests/test_stage8.py
git commit -m "review count: one read-only definition (review_queue) for the number beside Review, on the app's icon and in pushes, and GET /api/review/count (GET /api/review writes, so it is never called for the number); the review nudge counts what Review shows, flags included; notify.app.badge, on when absent because it costs nothing and sends nothing"
```

---

### Task 2: The count in pushes that are sent anyway, and the worker

Implementer: **sonnet**. UI task: no (pytest, plus one worker scenario that renders nothing).

**Files:**
- Modify: `factotum/app_push.py` (`payload`, `send_all`, `send_test`, new `with_badge`), `factotum/web/sw.js` (`handlePush`, new `badgeOf`, `setBadge`, the header comment), `tests/test_stage8.py`
- Create (outside the repository): `DEV/scenarios/s8-2-worker.mjs`

**What exists today (quoted):** `app_push.payload(notice: dict, origin: str) -> bytes` builds `message = {"web_push": 8030, "notification": n}` then trims to `W.MAX_PLAINTEXT` (body, then title, then the buttons dropped; `navigate`, the tag and `data` never cut). `send_all(conn, cfg, notice, *, transport=None) -> dict` returns early with no devices, then `k = keys(conn)`, then `deliver(d, notice, k, cfg, transport)` per device. `send_test(conn, cfg, endpoint, *, transport=None) -> dict | None` calls `deliver(device, TEST_NOTICE, k, cfg, transport)`. `sw.js`: `function handlePush(json)` returns `self.registration.showNotification(s.title, s.options).catch(() => self.registration.showNotification('Factotum', {...}))`; handlers are global functions so a test browser can run them through `worker.evaluate`.

**Interfaces (produces):**
- A notice (app_push's shape, module docstring) may carry `"badge": int`. `payload` adds `message["app_badge"] = notice["badge"]` BEFORE the first `_encode` (so trimming counts it, and it is never cut) when `type(b) is int and b >= 0` (so `True`, `2.0`, `"3"`, `-1`, `None` add nothing). Never inside `notification`. The docstring gains one sentence on it and on why it is top-level.
- `with_badge(conn, cfg: dict, notice: dict) -> dict`: the notice unchanged when it already has `"badge"` or `C.value(cfg, "notify.app.badge")` is false; else `dict(notice, badge=review_queue.count(conn, cfg, date.today()))`; any exception from the count → the notice unchanged (a comment: the number is never a reason not to send). `from . import review_queue` inside the function.
- `send_all`: `notice = with_badge(conn, cfg, notice)` right after `if not devs: return res` and before `keys(conn)` (the count reads only, so the rule "every network call before the first write" holds). `send_test`: `deliver(device, with_badge(conn, cfg, TEST_NOTICE), k, cfg, transport)`.
- `sw.js`:

```js
// The Review count for the app's icon (stage 8): the payload's top-level app_badge, an integer >= 0; anything else is no number.
function badgeOf(json) {
  const n = json && typeof json === 'object' ? json.app_badge : undefined;
  return Number.isInteger(n) && n >= 0 ? n : null;
}
// Sets (0: clears) the icon's number where the browser can; never throws, never rejects, so a notification never waits on it.
function setBadge(n) {
  try {
    const nav = self.navigator;
    if (n === null || !nav) return Promise.resolve();
    const p = n === 0 ? (typeof nav.clearAppBadge === 'function' ? nav.clearAppBadge() : null)
                      : (typeof nav.setAppBadge === 'function' ? nav.setAppBadge(n) : null);
    return Promise.resolve(p).catch(() => {});
  } catch (_) { return Promise.resolve(); }
}
```

  `handlePush(json)` becomes `Promise.all([setBadge(badgeOf(json)), <today's showNotification chain>]).then(r => r[1])` (the notification chain unchanged, still with its "Factotum" fallback). The header comment gains: the payload's top-level `app_badge` is the Review count; Safari applies it without running this script; here it is set by `setBadge`; no push is ever sent for the number alone. Still no fetch handler.

- [ ] **Step 1: Write the failing tests** under `# --- Task 2: the count in pushes that are sent anyway`:

```python
NOTICE = {"title": "Call Reza", "body": "before the meeting · due Nov 5", "path": "/t/12", "tag": "task-12",
          "actions": [{"action": "done", "title": "Done", "path": "/r/tok?a=done"}],
          "urgency": "high", "ttl": 86400, "data": {"task_id": 12}}

def _device(conn, endpoint="https://push.invalid/d1", origin="https://f.example"):
    ua = ec.generate_private_key(ec.SECP256R1()); auth = os.urandom(16)
    AP.subscribe(conn, endpoint, W.b64u(W.public_bytes(ua)), W.b64u(auth), "Phone", origin); conn.commit()
    return ua, auth

def _service():
    got = []
    return got, httpx.MockTransport(lambda req: got.append((str(req.url), req)) or httpx.Response(201))

def _sent(got, ua, auth, i=-1):
    return json.loads(W.decrypt(got[i][1].content, ua, auth))

def test_the_badge_is_a_top_level_integer_and_only_a_good_one():
    plain = json.loads(AP.payload(NOTICE, "https://f.example"))
    p = json.loads(AP.payload({**NOTICE, "badge": 7}, "https://f.example"))
    assert p["app_badge"] == 7 and p["notification"] == plain["notification"] and "app_badge" not in plain
    assert json.loads(AP.payload({**NOTICE, "badge": 0}, "https://f.example"))["app_badge"] == 0
    for bad in (True, -1, "3", 2.0, None):
        assert "app_badge" not in json.loads(AP.payload({**NOTICE, "badge": bad}, "https://f.example")), bad
    raw = AP.payload({**NOTICE, "badge": 12, "title": "T" * 5000, "body": "é" * 5000}, "https://f.example")
    assert len(raw) <= W.MAX_PLAINTEXT and json.loads(raw)["app_badge"] == 12

def test_send_all_carries_the_count_unless_switched_off(conn, cfg):
    AP.keys(conn, create=True); ua, auth = _device(conn)
    mk(conn, 1); mk(conn, 2); conn.commit()
    got, t = _service()
    AP.send_all(conn, cfg, NOTICE, transport=t)
    assert _sent(got, ua, auth)["app_badge"] == review_queue.count(conn, cfg, date.today()) == 2
    off = copy.deepcopy(cfg); off["notify"].setdefault("app", {})["badge"] = False
    AP.send_all(conn, off, NOTICE, transport=t)
    assert "app_badge" not in _sent(got, ua, auth)

def test_the_test_push_carries_the_count_too(conn, cfg):
    AP.keys(conn, create=True); ua, auth = _device(conn, "https://push.invalid/me")
    mk(conn, 1); conn.commit()
    got, t = _service()
    assert AP.send_test(conn, cfg, "https://push.invalid/me", transport=t)["ok"]
    assert _sent(got, ua, auth, 0)["app_badge"] == 1

def test_a_count_that_fails_never_stops_a_push(conn, cfg, monkeypatch):
    AP.keys(conn, create=True); ua, auth = _device(conn)
    monkeypatch.setattr(review_queue, "count", lambda *a, **k: 1 / 0)
    got, t = _service()
    assert AP.send_all(conn, cfg, NOTICE, transport=t)["sent"] == 1
    assert "app_badge" not in _sent(got, ua, auth, 0)

def test_the_count_rides_only_on_pushes_that_are_sent_anyway(conn, cfg):
    ecfg = copy.deepcopy(cfg); ecfg["notify"]["provider"] = "app"
    AP.keys(conn, create=True); ua, auth = _device(conn)
    mk(conn, 1); mk(conn, 2); conn.commit()
    got, t = _service()
    sch = Scheduler(ecfg, conn, push=lambda *a, **k: None, device_push=lambda c, cf, n: AP.send_all(c, cf, n, transport=t))
    sch.tick(datetime(2026, 11, 1, 10, 0))
    assert got == []                       # two items wait on Review and nothing is due: no push
    sch.tick(datetime(2026, 11, 1, 19, 31))
    assert len(got) == 1                   # the evening nudge, which is sent anyway, carries the count
    body = _sent(got, ua, auth)
    assert body["notification"]["tag"] == "review" and body["app_badge"] == review_queue.count(conn, ecfg, date.today())

def test_the_worker_sets_the_badge_and_still_has_no_fetch_handler():
    js = (api.WEB / "sw.js").read_text()
    assert "function badgeOf(" in js and "function setBadge(" in js and "app_badge" in js
    assert "addEventListener('fetch'" not in js and 'addEventListener("fetch"' not in js and "onfetch" not in js
```

- [ ] **Step 2: Run them** — expected FAIL (no `app_badge`, no `with_badge`, no worker functions).
- [ ] **Step 3: Implement** `payload`, `with_badge`, the two call sites, the worker. The fixture config must have `notify.provider` absent or `ntfy` for the existing stage 2 tests; this task changes no config.
- [ ] **Step 4: Run the whole suite** (all pass; stage 2's exact-payload test still passes because a notice without `badge` makes no `app_badge`).
- [ ] **Step 5: Write and run `DEV/scenarios/s8-2-worker.mjs`** (both devices; the worker is registered by the page after its first load):
  - Before `page.goto`: `await context.grantPermissions(['notifications'], { origin: base })`. `await page.goto(base + '/')`; `const sw = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker')`. Log `await sw.evaluate(() => typeof self.navigator.setAppBadge)` (informational: whether headless Chromium has it in a worker).
  - Stub inside the worker: `await sw.evaluate(() => { self.__badge = []; const def = (k, f) => Object.defineProperty(self.navigator, k, { configurable: true, value: f }); def('setAppBadge', n => { self.__badge.push(n); return Promise.resolve(); }); def('clearAppBadge', () => { self.__badge.push(0); return Promise.resolve(); }); })`.
  - `const push = j => sw.evaluate(j => handlePush(j).then(() => 'shown', e => 'rejected: ' + e), j)`; `const msg = (extra) => Object.assign({ web_push: 8030, notification: { title: 'S8 worker check', body: 'b', navigate: base + '/review', tag: 's8' } }, extra)`.
  - W1: `push(msg({ app_badge: 4 }))` → `self.__badge` `[4]`. W2: `{ app_badge: 0 }` → `[4, 0]`. W3: no `app_badge`, then `'4'`, `-1`, `2.5`, `true`, and `app_badge` inside `notification` → `__badge` unchanged after each. W4: `push(null)` → unchanged, and it resolves.
  - W5: `def('setAppBadge', () => { throw new Error('x'); })`, push `{ app_badge: 5 }` → resolves; then `def('setAppBadge', () => Promise.reject(new Error('y')))` → resolves. W6: `def('setAppBadge', undefined); def('clearAppBadge', undefined)`, push `{ app_badge: 6 }` and `{ app_badge: 0 }` → resolve. Log each result string: every one is `shown` (if headless refuses `showNotification` itself, every case says so the same way and the report says the badge cases still resolved).
  - Log `await sw.evaluate(async () => (await self.registration.getNotifications()).map(n => n.title))` (may be empty headless: say so), then close them all: `sw.evaluate(async () => (await self.registration.getNotifications()).forEach(n => n.close()))`.
  - Expected problems: none. No screenshot is needed (nothing is drawn); take `shot('s8-2-page')` once to show the page was up.
- [ ] **Step 6: Commit**

```bash
git add factotum/app_push.py factotum/web/sw.js tests/test_stage8.py
git commit -m "app push: a push that is sent anyway (a reminder, free now, the review nudge, a test) carries the Review count as the declarative payload's top-level app_badge, and the worker sets it on the icon where it runs; never a push of its own, and a count that cannot be made is left out, never a reason not to send"
```

---

### Task 3: The count on the open app's icon, and Settings

Implementer: **opus**. UI task: yes.

**Files:**
- Modify: `factotum/web/app.js` — new section `// ---------- app extras (stage 8) ----------` (after the notifications (stage 2) section); hooks in `load()`, `render()`, `signedOut()`, `renderSettings()`; `factotum/web/app.css` only if the field needs it.
- Create (outside the repository): `DEV/scenarios/s8-3-badge.mjs`, `DEV/scenarios/s8-3-nobadge.mjs`

**What exists today (quoted):** in `render()`: `document.getElementById('n-review').textContent = review && (review.total || (review.suggestions || []).length) ? review.total + (review.suggestions || []).filter(x => !x.created).length : '';` — `review` is the `/api/review` payload, loaded only by the Review tab (`loadReview`, which passes `exclude` = `[...staged.keys()].filter(k => typeof k === 'number').join(',')`), and set to `null` by an Apply made off the Review tab. `load()` fetches `/api/tasks?status=open`, `/api/proposals`, `/api/summary`, then `render()`. `signedOut()` shows `#signedout` and makes the rest inert. `loadSettings()` sets `settings` (whose `effective` holds every Settings key with its default filled in) and renders; `renderSettings` builds fields with `f(key, label, sub, control)` and `bool(key)`; a `[data-key]` control saves only itself through `settingsDirty`.

**Interfaces (produces):**

```js
  // ---------- app extras (stage 8) ----------
  // The number beside Review and on the installed app's icon. Before Review is loaded it is the server's
  // count (GET /api/review/count, read in load()); while Review is loaded, that page's own number (it leaves
  // out what the tray already decided). The icon always gets what the tab shows.
  let reviewCount = null;
  let badgeShown;            // what the icon was last given (undefined: nothing yet), so a redraw calls nothing
  function reviewShown() { /* review ? <today's expression, 0 instead of ''> : (reviewCount || 0) */ }
  // Off (notify.app.badge), signed out, or 0: cleared. Before Settings has loaded: nothing (the switch is not known yet).
  function syncBadge() {
    const out = !document.getElementById('signedout').hidden;
    const n = out ? 0 : !settings ? null : !settings.effective['notify.app.badge'] ? 0 : reviewShown();
    if (n === null || n === badgeShown) return;
    badgeShown = n;
    try {
      const p = n > 0 ? (typeof navigator.setAppBadge === 'function' ? navigator.setAppBadge(n) : null)
                      : (typeof navigator.clearAppBadge === 'function' ? navigator.clearAppBadge() : null);
      if (p && typeof p.catch === 'function') p.catch(() => {});
    } catch (_) {}
  }
```

- `load()`: after `/api/summary`, `try { reviewCount = (await api('/api/review/count' + (excl ? '?exclude=' + excl : ''))).count; } catch (_) {}` where `excl` is built exactly as in `loadReview` (a failed request keeps the last count; a sign-out shows its own screen).
- `render()`: the `n-review` line becomes `const rv = reviewShown(); document.getElementById('n-review').textContent = rv || '';` followed by `syncBadge();`. With `review` loaded the number is exactly today's.
- `signedOut()`: `syncBadge()` after the screen is shown (it clears).
- `renderSettings()`, in `<h3>Notifications</h3>`, after the devices list and before the ntfy field:
  `${f('notify.app.badge', 'Count on the app icon', 'The number of Review items on the installed app’s icon. The open app keeps it current, and notifications that are sent anyway carry it, so it also moves while the app is closed; never a notification of its own. An iPhone shows it only after notifications were allowed for the app, Android and Firefox show none.', bool('notify.app.badge'))}`
  (exact words; the saved change re-renders, and `syncBadge` clears or sets the icon).

- [ ] **Step 1: Implement.**
- [ ] **Step 2: Run the suite** (all pass).
- [ ] **Step 3: Write and run `DEV/scenarios/s8-3-badge.mjs`** (both devices; the stub from "How a UI task is verified" via `page.addInitScript` before the first `goto`):
  - B1: register `const countAt = () => page.request.get(base + '/api/review/count').then(r => r.json())` and `const badge = () => page.evaluate(() => window.__badge)`. `goto base + '/'`, wait for the first `/api/review/count` response and 300 ms. Log the `#n-review` text, `countAt()`, `badge()`: the tab shows the count (empty for 0), the log ends with that number (or 0), and holds ONE call. Click Timeline, Projects, Today: log `badge()` again, still one call (a redraw calls nothing). Shot `s8-3-tab` (the tab bar with "Review N" on Today).
  - B2: `context.route('**/api/review/count*', r => r.fulfill({ json: { count: 0 } }))`, then `page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')))` (onReturn reloads the lists), wait 500 ms: the tab number is gone and the log ends with `0`. Route `{ count: 3 }` the same way: the tab says 3 and the log ends with `3`. `unroute`. (Stubbed: `/api/review/count`.)
  - B3 (Review Focus 6): click Settings. Element screenshot of the new field only (the Notifications section also shows the topic, which is never captured): `await page.locator('.field:has([id="s-notify.app.badge"])').screenshot({ path: DEV + '/../shots/s8-3-settings-' + device + '.png' })`. Untick `[id="s-notify.app.badge"]`; wait for the `PUT /api/settings` response; log the request body's `values` KEYS only (exactly `["notify.app.badge"]`) and `badge()` (ends with `0`). Click Today and back to Settings: no new call. Tick it again: the log ends with the count. Then delete the override this created: `execFileSync('/home/robert/factotum/.venv/bin/python', ['-c', "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute(\"DELETE FROM settings WHERE key = 'notify.app.badge'\"); c.commit()", DEV + '/factotum-dev.db'])` (`DEV` from `./s4b-lib.mjs`).
  - B4: click Review (this loads Review on the dev copy and stamps its rows as offered there, as stage 5's scenarios do): wait for `/api/review`; log the tab number and the last badge value: equal.
  - B5 (signed out): `context.route('**/api/tasks*', r => r.fulfill({ status: 401, body: '' }))`; dispatch `visibilitychange`; wait for `#signedout` visible; log `badge()` (ends with `0`). Shot `s8-3-signedout`. `unroute`. Expected problem: `HTTP 401: GET …/api/tasks?status=open` (stubbed).
  - `noSideScroll` on every phone shot. Every problem other than the stubbed 401 is a fault.
- [ ] **Step 4: Write and run `DEV/scenarios/s8-3-nobadge.mjs`** (both devices): `page.addInitScript(() => { delete Navigator.prototype.setAppBadge; delete Navigator.prototype.clearAppBadge; })`; `goto /`, log `'setAppBadge' in navigator` (false) and the tab number; click Settings (no screenshot), Today. Expected problems: none.
- [ ] **Step 5: Look at every screenshot, then commit**

```bash
git add factotum/web/app.js factotum/web/app.css
git commit -m "web: the Review count is on the installed app's icon (setAppBadge, cleared at 0, when signed out and when switched off; one call per change) and beside Review from the first load; Settings → Notifications has 'Count on the app icon'"
```

---

### Task 4: Long-press shortcuts, their icons, and `/new`

Implementer: **opus**. UI task: yes.

**Files:**
- Modify: `factotum/web/manifest.webmanifest`, `tools/make_icons.py`, `factotum/api.py` (`SHORTCUT_PATHS`, the page routes), `factotum/web/app.js` (`parseAddress`, `applyAddress`, and possibly the `n` key handler: see Step 1), `tests/test_stage8.py`
- Create: `factotum/web/icon-new-96.png`, `factotum/web/icon-review-96.png` (written by the tool)
- Create (outside the repository): `DEV/scenarios/s8-4-shortcuts.mjs`

**What exists today (quoted):** `api.PAGE_TABS = ("today", "timeline", "projects", "review", "chat", "log", "settings")`; in `create_app`: `# One explicit path per tab, never a /{tab} pattern (it would shadow /mcp).` then `app.add_api_route("/", index, …)`, a loop `for _tab in PAGE_TABS: app.add_api_route(f"/{_tab}", index, methods=["GET"], include_in_schema=False)`, and `/t/{task_id:int}`. `tools/make_icons.py`: `png(size: int, dot: float = 0.26) -> bytes` (teal `(0x0F, 0x76, 0x6E)` ground, a light `(0xF5, 0xF7, 0xF8)` dot, `SS = 4` supersampling, a hand-written PNG with `chunk()` inside `png`), and `__main__` writing `icon-180/192/512.png` (`--data-uri` prints the 180 px one). `app.js`: `parseAddress()` returns `{tab, task, legacy}` (an unknown name is Today); `applyAddress(ev)` handles a task address, else sets `ui.tab`, `setAddress(addressOf(a.tab), true)` and renders; it runs once after the first `load()` succeeds (`load().then(() => { applyAddress(); registerWorker(); return loadSettings(); })`), so a sign-out during that load shows "Sign in again" before it runs and the address is untouched.

**Interfaces (produces):**
- Manifest (still valid JSON; nothing else in it changes):

```json
  "shortcuts": [
    { "name": "New task", "short_name": "New task", "description": "Add a task in one line", "url": "/new",
      "icons": [{ "src": "/static/icon-new-96.png", "sizes": "96x96", "type": "image/png" }] },
    { "name": "Review", "short_name": "Review", "description": "Decide on what Review asks about", "url": "/review",
      "icons": [{ "src": "/static/icon-review-96.png", "sizes": "96x96", "type": "image/png" }] }
  ]
```

- `tools/make_icons.py`: `shortcut(kind: str, size: int = 96) -> bytes` — the teal ground and a light glyph made of thick strokes (a pixel is light by the share of its `SS x SS` samples within `width/2` of any segment, the same anti-aliasing as `png`): `"new"` a plus, segments `(0.27, 0.5)–(0.73, 0.5)` and `(0.5, 0.27)–(0.5, 0.73)`, width `0.12`; `"review"` a check mark, segments `(0.27, 0.52)–(0.43, 0.68)` and `(0.43, 0.68)–(0.73, 0.36)`, width `0.11` (coordinates are fractions of `size`; everything inside the centre 60%). Factor the PNG writing out of `png` into a module-level `encode(size, pixel_rows) -> bytes` only if `png(n)` stays byte-for-byte identical (check: the three committed app icons and the inlined data URI must not change). `__main__` also writes `icon-new-96.png` and `icon-review-96.png`; the module docstring says so.
- `api.py`: `SHORTCUT_PATHS = ("new",)` beside `PAGE_TABS`, with the comment `# Page addresses that are not tabs: /new opens Today with New task (the app's long-press shortcut, stage 8).`; the route loop covers both tuples. Nothing under `/api` changes.
- `app.js`:
  - `parseAddress()`: `if (p === '/new') return { tab: 'today', task: null, legacy: false, add: true };` (before the generic branch; `TABS` is unchanged, so `/new` never becomes a tab).
  - `applyAddress(ev)`, after the two task branches: `if (a.add) { ui.tab = 'today'; ui.laterOpen = false; ui.page = null; setAddress('/', true); render(); openNew(); return; }` with the comment `// /new (the long-press "New task"): Today with New task open. / REPLACES /new, so a reload opens Today plainly and Back leaves the app as from /. Signed out, this never runs and /new stays for the reload after Sign in again.`
  - `openNew()`: stage 6's New task opener, ONE function (see Step 1).

- [ ] **Step 1: Find the opener stage 6 built.** In `app.js` search for `newOpen`, `openSheet`, `new-open`, `fab-new`, `new-line` and the keydown handler's `'n'` branch. The plan for stage 6 says the `n` key opens the line on a desktop (`ui.newOpen = true`, render, focus `#new-line`, leaving Review / Chat / Log / Settings for Today) and the sheet on a phone (`openSheet()`), and that `#new-open` and `#fab-new` open them. Then:
  - If one named function already does "desktop: the line; phone: the sheet", `openNew` IS that function (call it; do not wrap it in a second one).
  - If the key handler does it inline (or the button handlers do), move exactly that code into `function openNew()` in stage 6's `// ---------- by hand (stage 6): New task ----------` section and make the key, `#new-open` and `#fab-new` call it. Their behaviour must not change: run stage 6's `DEV/scenarios/s6-10-new.mjs` and `s6-12-sheet.mjs` again (both devices) after the move and report that they pass as before.
  - If the names differ from these, use what the code has and report the names. If the opener refuses in some state (say the tray holds changes), `/new` does what the `n` key does in that state; add no conditions of your own.
  - If stage 6's New task is not in `app.js` at all, stop and report: Task 4 depends on it.
- [ ] **Step 2: Write the failing tests** under `# --- Task 4: shortcuts, their icons, /new`:

```python
import struct

def _png_size(b: bytes) -> tuple[int, int]:
    assert b[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", b[16:24])

def _tool():
    import importlib.util
    spec = importlib.util.spec_from_file_location("make_icons", C.ROOT / "tools" / "make_icons.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod

def test_new_answers_with_the_page(cfg, tmp_path):
    c = TestClient(create_app(cfg, db_path=str(tmp_path / "a.db")))
    r = c.get("/new")
    assert r.status_code == 200 and 'id="panel"' in r.text and "/static/app.js?v=" in r.text
    assert r.headers["cache-control"] == "no-cache, must-revalidate"
    assert c.get("/new/1").status_code == 404 and c.get("/news").status_code == 404

def test_the_manifest_has_two_shortcuts_whose_addresses_and_icons_answer(cfg, tmp_path):
    c = TestClient(create_app(cfg, db_path=str(tmp_path / "a.db")))
    r = c.get("/static/manifest.webmanifest")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/manifest+json")
    m = r.json()                                                   # still valid JSON
    sc = {s["name"]: s for s in m["shortcuts"]}
    assert set(sc) == {"New task", "Review"} and sc["New task"]["url"] == "/new" and sc["Review"]["url"] == "/review"
    for s in sc.values():
        assert c.get(s["url"]).status_code == 200
        for icon in s["icons"]:
            g = c.get(icon["src"])
            assert g.status_code == 200 and g.headers["content-type"].startswith("image/png")
            assert _png_size(g.content) == (96, 96) and icon["sizes"] == "96x96"
    assert '<link rel="manifest" href="/static/manifest.webmanifest" crossorigin="use-credentials">' in c.get("/").text

def test_the_icon_tool_draws_the_shortcut_icons_that_are_committed():
    mod = _tool()
    for kind in ("new", "review"):
        b = mod.shortcut(kind)
        assert _png_size(b) == (96, 96) and b == (api.WEB / f"icon-{kind}-96.png").read_bytes()
    assert mod.shortcut("new") != mod.shortcut("review")
    for n in (180, 192, 512):
        assert mod.png(n) == (api.WEB / f"icon-{n}.png").read_bytes()     # the app icons are unchanged
```

- [ ] **Step 3: Run them** — expected FAIL.
- [ ] **Step 4: Implement** the tool function, then run `/home/robert/factotum/.venv/bin/python tools/make_icons.py` from WT and check `git status --short`: only the two new PNGs are new and `icon-180/192/512.png` are NOT modified (if they are, `png` changed: fix the tool, never commit changed app icons). Look at both new icons with the Read tool. Then the manifest, the routes, `parseAddress` / `applyAddress` / `openNew`.
- [ ] **Step 5: Run the suite** (all pass, including stage 1's `test_the_manifest_and_its_icons_are_served`, `test_the_page_head_installs_the_app` and `test_task_and_tab_addresses_answer_with_the_app`).
- [ ] **Step 6: Write and run `DEV/scenarios/s8-4-shortcuts.mjs`** (both devices):
  - M1 (the manifest as Chromium reads it): `goto /`; `const cdp = await context.newCDPSession(page); const am = await cdp.send('Page.getAppManifest');` log `am.errors` (must be `[]`) and the parsed `shortcuts` names and urls (`JSON.parse(am.data).shortcuts`). In the page: `fetch` the manifest link's `href` with `credentials: 'include'` and each shortcut's url and icon: log statuses (all 200) and the link's `crossorigin` (`use-credentials`). Look at the two icons (open them with the Read tool from `factotum/web/`).
  - N1: `goto base + '/new'`; wait for New task to open (desktop: `#new-line` visible and focused; phone: `#sheet` not hidden — or the elements stage 6 actually uses, as found in Step 1). Log `location.pathname` (`/`), `history.length` (1: nothing pushed), the selected tab (`today`). Shot `s8-4-new` (desktop: the line on Today, frame 6; phone: the sheet, frame 11).
  - N2: `page.reload()`; after load log `location.pathname` (`/`) and that New task is NOT open. Shot `s8-4-reload`.
  - N3 (signed out): `context.route('**/api/tasks*', r => r.fulfill({ status: 401, body: '' }))`; `goto base + '/new'`: log `#signedout` visible and `location.pathname` (`/new`). Shot `s8-4-signedout`. `unroute`; click `#signin` (it reloads at the same address): New task opens and the address becomes `/`. Shot `s8-4-after-signin`. Expected problem: `HTTP 401: GET …/api/tasks?status=open` (stubbed).
  - N4: `goto base + '/review'`: the Review tab is selected and the address stays `/review` (it loads Review on the dev copy, as Task 3 does). Then close New task if anything left it open (Escape on a desktop, Cancel on the sheet); type nothing, add nothing.
  - `noSideScroll` on every phone shot. Any other problem is a fault.
- [ ] **Step 7: Look at every screenshot, then commit**

```bash
git add factotum/web/manifest.webmanifest factotum/web/icon-new-96.png factotum/web/icon-review-96.png tools/make_icons.py factotum/api.py factotum/web/app.js tests/test_stage8.py
git commit -m "app: long-press shortcuts New task (/new) and Review, with their own icons drawn by tools/make_icons.py; /new opens Today with New task and leaves / in the address, so a reload does not reopen it, and a sign-in on the way keeps it"
```

(If Step 1 moved stage 6's opener into `openNew`, say so in the message: `…; the n key, + New task and the round button share openNew`.)

---

### Task 5: The stage's documents

Implementer: **sonnet**. UI task: no.

**Files:**
- Modify: `SPEC.md`, `CLAUDE.md`, `docs/SETUP.md`

- [ ] **Step 1: `SPEC.md`.**
  - §9 (The UI), after the stage 1 paragraph's neighbours for the later stages: a paragraph **App extras (stage 8 of the linked-tasks design, 2026-11-11)**: the number beside Review comes from `GET /api/review/count` (`review_queue.count`, read-only) until Review is loaded, then from the Review page (which leaves out the tray's decisions); the icon gets exactly that number (`setAppBadge`, `clearAppBadge` at 0, cleared when signed out or switched off, one call per change); Settings → Notifications "Count on the app icon" (`notify.app.badge`, on when absent, why that is not a paid or mailing switch); the manifest's two shortcuts and their icons (`tools/make_icons.py` `shortcut`); `/new` (Today with New task, the address replaced by `/`, kept through a sign-in).
  - §10 (Reminders and push), in the **Notifications from the app** list: a bullet **The count on the icon**: a push that is sent anyway carries `app_badge` (top level, an integer: the 2025 WebKit change and why never nested), added in one place (`app_push.with_badge`, from `send_all` and `send_test`), absent when the switch is off or the count fails; Safari applies it without the worker, the worker's `setBadge` elsewhere; a message a Safari build cannot read declaratively is shown by the worker instead; never a push for the number alone. The review nudge counts what Review shows (`review_queue.queue`).
  - §13 (Cost and safety): one line: the count costs nothing (no run, no mail, no extra push) and travels encrypted inside pushes already sent.
  - The limits, in §9's paragraph, as plain sentences: iOS ignores manifest shortcuts; an iPhone shows the number only for the installed app with notifications allowed; Chromium re-reads an installed app's manifest lazily, so new shortcuts may need a reinstall; Firefox has no Badging API and Chrome on Android shows no number; on a desktop only the installed app shows it; the number is as fresh as the last open or the last push; before Review is opened on a day the count may differ from Review's by that day's link suggestions.
- [ ] **Step 2: `CLAUDE.md`.**
  - Phase status: one sentence after the stage 5 sentence (or after the last stage sentence present): `Stage 8 (2026-11-11): app extras: the Review count on the installed app's icon (one read-only count, \`review_queue\`, for the tab, the icon and the pushes that are sent anyway; \`notify.app.badge\`) and long-press shortcuts to New task (\`/new\`) and Review.`
  - "Where things live" row: `the Review count, the number on the app's icon, the shortcuts, /new` | design §16, SPEC §9, §10 | `factotum/review_queue.py` (`queue`, `card_count`, `count`), `api.py` (`/api/review/count`, `SHORTCUT_PATHS`), `app_push.py` (`payload`, `with_badge`), `web/sw.js` (`badgeOf`, `setBadge`), `web/app.js` (`reviewShown`, `syncBadge`, `parseAddress`, `applyAddress`, `openNew`), `manifest.webmanifest` `shortcuts`, `tools/make_icons.py` (`shortcut`).
  - Conventions that break silently, each dated 2026-11-11:
    - **The Review count is `review_queue.count`, and it reads only**: `GET /api/review` writes (the day's suggestion session, the `last_offered_at` stamp `expire` acts on), so it is never called for a number; a new place that shows how many items wait on Review uses `review_queue`, never its own `build_review_queue` call.
    - **`app_badge` is a top-level integer in the declarative payload, never inside `notification`** (WebKit moved it out in 2025; a nested one crashed an update path). A wrong type makes Safari hand the push to the worker, which still shows it.
    - **The count only rides on pushes that are sent anyway** (`app_push.with_badge` in `send_all` and `send_test`); never send a push to move the number: every push must show a notification.
    - **`notify.app.badge` is on when absent**: it costs nothing and sends nothing, so it is not a paid or mailing switch and not in `PAID_SWITCHES`.
    - **`/new` is replaced by `/` once New task is open**; a sign-out keeps `/new` for the reload. A new page address that is not a tab goes in `api.SHORTCUT_PATHS` and `parseAddress`.
    - **iOS ignores manifest `shortcuts`, and Chromium re-reads an installed app's manifest lazily**: a changed shortcut may need a reinstall to show.
- [ ] **Step 3: `docs/SETUP.md`.**
  - §17 (Installing the app): a paragraph **Shortcuts**: long-press the icon on Android, or right-click it in the taskbar / dock on a desktop (Edge, Chrome), for "New task" and "Review"; an app installed before this deploy may show them only after the browser re-reads the manifest (up to a day or more) or after a reinstall; iOS has no such menu.
  - §19 (Notifications from the app): a paragraph **The count on the icon** (what it is, the switch, which pushes carry it, that nothing is sent for it) and, appended to "For the owner to check on real devices":
    11. iPhone, installed app with notifications allowed: the icon shows the same number as the Review tab; after a Review session that empties it, the number goes when the app is next opened.
    12. iPhone, app closed: after a reminder or "Send a test", the icon's number is the current Review count (if it does not change until the app is opened, this iOS version does not apply `app_badge` yet: note the iOS version).
    13. Desktop Edge or Chrome, installed app: the taskbar / dock icon shows the number; Settings → Notifications → untick "Count on the app icon": it goes.
    14. Android, installed app: long-press the icon: New task opens Today with New task; Review opens Review (no number on the icon is expected).
    15. Desktop: right-click the installed app's taskbar / dock icon: the same two shortcuts.
    16. Reload after opening New task from a shortcut: Today opens without New task.
  - "What was verified and what was not", one line for stage 8: verified offline (the count's one definition, the payload, the switch, `/new`, the manifest and icons) and in a headless browser (stubbed badge calls, the worker's badge handling, `/new` at both sizes, Chromium's own manifest parse); not verified: any real icon number, any real shortcut menu, Safari's handling of `app_badge`.
- [ ] **Step 4: Run the suite** (all pass) and check the words: `grep -niE "\bblocked\b|dependenc" SPEC.md CLAUDE.md docs/SETUP.md` shows nothing this stage added.
- [ ] **Step 5: Commit**

```bash
git add SPEC.md CLAUDE.md docs/SETUP.md
git commit -m "docs: stage 8, app extras (one read-only Review count for the tab, the icon and the pushes sent anyway; the switch; the shortcuts and /new) with the honest limits: no shortcuts on iOS, a badge there only with notifications allowed, manifests re-read lazily, no Badging API in Firefox or Chrome on Android"
```

---

## Stage check (by the controller)

After Task 5: `pytest -q` from WT; every `s8-*` scenario again for both devices; stage 6's `s6-10-new.mjs` and `s6-12-sheet.mjs` (New task is opened by `/new` through the same opener) and stage 2's worker behaviour through `s8-2-worker.mjs` (every push still shows a notification); confirm the dev database has no `notify.app.badge` row (`SELECT key FROM settings WHERE key = 'notify.app.badge'` on `DEV/factotum-dev.db` returns nothing) and that Today, Review and Settings look as before at both sizes apart from the number beside Review and the new Settings field. The checklist, one checkable sentence each:

```
pytest -q passes from the worktree, with tests/test_stage8.py included.
GET /api/review/count answers {"count": n} where n equals GET /api/review's total plus its suggestion cards on the same database (pytest).
GET /api/review/count writes nothing: no last_offered_at stamp and no change to the connection's total_changes (pytest).
The evening review nudge counts the items Review shows, flagged ones included (pytest).
notify.app.badge is a Settings key, True in config.DEFAULTS, not in PAID_SWITCHES, and present in the example, the starter and the fixture (pytest).
A notice with an integer badge >= 0 makes a payload with a top-level app_badge and an unchanged notification; any other value adds nothing; a trimmed payload keeps app_badge and stays within 3993 bytes (pytest).
send_all and send_test carry app_badge equal to review_queue.count when the switch is on, and none when it is off (pytest).
A count that raises leaves app_badge out and the push is still sent (pytest).
A scheduler tick with items on Review and nothing due sends no push; the evening nudge, which is sent anyway, carries the count (pytest).
sw.js has badgeOf and setBadge and still no fetch handler (pytest).
In the worker, handlePush sets the badge for app_badge 4, clears it for 0, ignores a string, a negative, a fraction, a boolean and a nested value, and resolves when setAppBadge throws, rejects or is missing (s8-2-worker, both devices).
On Today, before Review is opened, the tab says Review with the server's count and the icon stub received that number once; switching tabs makes no further call (s8-3-badge B1, both devices).
When the count becomes 0 the number beside Review disappears and the icon is cleared; when it becomes 3 both show 3 (s8-3-badge B2, both devices).
Settings → Notifications shows "Count on the app icon"; unticking it saves only notify.app.badge and clears the icon; ticking it sets the count again; the override row is removed afterwards (s8-3-badge B3, both devices).
With Review loaded, the icon shows the number the Review tab shows (s8-3-badge B4, both devices).
When the sign-in has expired, "Sign in again" shows and the icon is cleared (s8-3-badge B5, both devices).
Where the Badging API is missing, the page loads, Settings opens and nothing is logged as an error (s8-3-nobadge, both devices).
The manifest is valid JSON fetched with crossorigin="use-credentials", Chromium's own parse reports no errors, and it lists New task at /new and Review at /review whose addresses and 96x96 icons answer 200 (pytest and s8-4-shortcuts M1).
tools/make_icons.py draws the two committed shortcut icons byte for byte and leaves the three app icons unchanged (pytest).
/new answers with the page, and /new/1 and /news answer 404 (pytest).
Opening /new shows Today with New task open (the line on a desktop, the sheet on a phone), the address becomes / and no history entry is added (s8-4-shortcuts N1, both devices).
Reloading after that opens Today without New task (s8-4-shortcuts N2, both devices).
Opening /new while signed out shows "Sign in again" and keeps /new; after Sign in again New task opens and the address becomes / (s8-4-shortcuts N3, both devices).
Opening /review selects the Review tab and keeps /review (s8-4-shortcuts N4, both devices).
The n key, the + New task button and the phone's round button open New task as before (s6-10-new and s6-12-sheet, both devices).
No phone screenshot of this stage scrolls sideways (noSideScroll true).
SPEC.md, CLAUDE.md and docs/SETUP.md describe the stage, its gotchas dated 2026-11-11 and its limits (no shortcuts on iOS, an iPhone badge only with notifications allowed, manifests re-read lazily, no Badging API in Firefox or Chrome on Android), and contain no "blocked" or "dependency" this stage added.
```
