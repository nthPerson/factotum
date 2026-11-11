# Stage 4b: Links, the Page — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the links that stage 4a stores visible and usable in the web page: staged link changes in the tray, badges on rows, the All / Linked / Ready now filter and the Linked view, Today's link-aware order, an opened task with Linked tasks, provenance and History (desktop and phone), the link reasons and suggestions on Review, links in words in the Log, and "Free now" on the page after a reminder's Done.

**Architecture:** Everything is in the existing single-file page `factotum/web/app.js` (one IIFE, closure state) plus `app.css`, under new `// ---------- … (stage 4b) ----------` section comments. The page reads only what the merged 4a API already ships (`rel`, `neighbours`, the opened task's `relations` / `nearby` / `history`, Review's `texts` / `link_suggestions`, the Log's `cause` / `note`); one field is added server-side (a suggestion's `dir`, Task 1). Python changes are limited to that field and the server-rendered page after Done (Task 10), both test-first.

**Tech Stack:** Vanilla JS (no build step, no JS tests), CSS custom properties, FastAPI + SQLite (Python 3, pytest), Playwright scenarios run by the dev harness for rendered checks.

**Spec:** `docs/superpowers/specs/2026-11-10-linked-tasks-design.md` — §4, §7.3, §7.4, §9, §11.2 (last bullet), §12.1–§12.3, the Review-card parts of §12.4 that concern the new reasons and link suggestions, §14 (page after Done), §16, §17. Payloads: `docs/superpowers/plans/2026-11-10-stage-4a-links-backend.md` "API payloads" as corrected by "Payload notes" below. Approved mockups (read as HTML, they are the reference for wording, colours and layout): `/tmp/claude-1000/-home-robert-factotum/85fa6951-ca9c-416e-9691-1a412f0fadea/scratchpad/canvas/project/` `Main.dc.html` (frame 1), `Linked.dc.html` (frame 2), `Detail.dc.html` (frame 3), `Review.dc.html` (frame 4, reason line and progress chip only), `PhoneToday.dc.html` (frame 8), `PhoneTask.dc.html` (frame 9), `PhoneReview.dc.html` (frame 10, progress chip only).

## Global Constraints

- Work only in `/home/robert/factotum/.worktrees/linked-tasks` (branch `linked-tasks`), called WT; never touch the main checkout `/home/robert/factotum` or another worktree. Every path below is relative to WT.
- Python is `/home/robert/factotum/.venv/bin/python`, tests are `/home/robert/factotum/.venv/bin/pytest`, run from WT.
- Do not install packages.
- No real Claude runs, no scheduler, no sweeps, no sudo. A scenario never presses Send in Chat, Ask on a Review card, a starter prompt, "Search related emails", "Draft reply" or "Ask Claude what else is related" (each starts a real Claude run).
- Never open `config/factotum.yaml`, `.env` or `/home/robert/factotum/data`.
- Commits: small commits on branch `linked-tasks`, the WHOLE suite green at every commit (`/home/robert/factotum/.venv/bin/pytest -q` from WT), never push. Every commit message ends with the line `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` (CLAUDE.md's trailer, exactly). Message style is the repository's: `web: what changed and why` (or `api:`, `relations:`, `docs:`).
- Vocabulary (design §4): the page says "part of", "steps", "waits on", "frees", "same as". The words "blocked" and "dependency" never appear in anything the page shows (Task 1 adds a test over `factotum/web/`).
- Desktop behaviour that exists today does not change except where a task says so.
- Every surface is checked at 1280 px and at 390 px.
- With no links in the database every list looks exactly as it does today: no link bar, no badges, no legend, no new classes on rows.
- `app.js` stays one file (decision below). New code goes under the section comment the task names. What is typed lives in `draft` (CLAUDE.md); this stage adds no text box. A folded section renders no rows; `jumpTo` unfolds first. On a phone the task page is rendered into `#panel` (`ui.page`); nothing may assume the panel holds a list. `/api` never answers 401/403/redirect. Every state-changing `/api` call goes through `api()` (it sets `Content-Type: application/json`).
- `stage 2` (notifications) is being built in WT at the same time and edits `app.js` in the Settings area and `render()`'s Review-at pill: name functions, never rely on line numbers, and re-read the file before editing.
- **How UI tasks are verified** (there are no JS tests; a UI task is verified by rendering and looking). DEV = `/tmp/claude-1000/-home-robert-factotum/85fa6951-ca9c-416e-9691-1a412f0fadea/scratchpad/dev`.
  - Dev instance on a COPY of a real database, no scheduler, at http://127.0.0.1:8751, loading the owner-shaped test fixture config: `DEV/serve.sh restart` (restart after any Python change; files under `factotum/web/` are served straight from WT).
  - Data: `DEV/seed_links.py` (Task 1) makes a known set of linked tasks on `DEV/factotum-dev.db` through `batches.apply`; every scenario of this stage starts with `seed('--fresh')` from `DEV/scenarios/s4b-lib.mjs` and reads the ids it returns (never hard-coded ids).
  - A real browser at desktop (1280x900) and phone (390x844, touch): `cd DEV && node pw.mjs scenarios/<name>.mjs --device both`. A scenario is an ES module `export default async ({ page, context, shot, base, device, log }) => { ... }` (Playwright API); `shot('name')` saves `DEV/../shots/<name>-<device>.png`; console errors, failed requests and HTTP >= 400 are printed (an expected 404 is named in the task). Scenarios of this stage are named `s4b-<task>-<name>.mjs`.
  - The implementer runs each scenario for both devices, LOOKS at every screenshot (the Read tool shows images), compares it with the named mockup frame, and reports what was seen, including the `log` lines and the "problems" list.
  - A scenario MAY press Apply / Undo on the dev database when the task needs it; it ends by calling `seed('--fresh')` again so the next task starts from the known set.
  - Loading the Review tab is allowed in Task 8's scenario only.
  - Every phone screenshot is checked for sideways scroll with `noSideScroll(page)` from `s4b-lib.mjs` (logged; must be `true`).

## Decisions this plan makes

- **`app.js` stays one file.** Every piece of 4b reads and writes the IIFE's closure state (`data`, `staged`, `ui`, `view`, `render`, `jumpTo`); a second plain script would need a global namespace exposing all of it, and native modules would mean restructuring the whole file while stage 2 edits it concurrently. Design §18 leaves this to the builder. Findability comes from section comments: `links (stage 4b): words and staging keys` (Task 2), `links (stage 4b): badges` (Task 3), `links (stage 4b): filters and the Linked view` (Task 4), `links (stage 4b): Today's order` (Task 5), `the opened task (stage 4b): linked tasks, provenance, History` (Task 6). `api.STAMPED` and `index.html` do not change.
- **A staged change's tray key is `stageKey(change, suffix)`**: `link` / `unlink` → `rel:<task_id>:<kind>:<other>`; `merge` → `rel:<task_id>:same_as:<into>`; `add_note` → `<id>:<suffix or "note">`; `add_reminder` → `<id>:r`; `judge_evidence` → `e:<evidence_id>`; anything else → `<id>:<suffix>` when a suffix is given, else the bare id (today's decision key). A staged entry may carry `expect`, `note` (sent) and `ev` (the suggestion's evidence id; never sent).
- **Accepting a link suggestion stages only the `link` / `merge` change**, with `ev` set: the server judges every pending suggestion for the pair in the same batch (`batches._judge_suggestions`), so discarding the staged link from the tray brings the card back. "Not related" / "Not the same" stage `judge_evidence` `dismissed` under `e:<id>`. `suggestionStaged(eid)` is true for either.
- **A Review note becomes `add_note`** staged under `<id>:note` beside the decision (today the page sends a whole new `context` built from an old read, which CLAUDE.md now forbids). What the card shows does not change.
- **A chat or planner `set` that names `context` or `name` carries `expect`** = the server's current value of those fields (`byId(id)`, never the overlaid `view`).
- **Badge colours follow frame 1**: "waits on" is amber (`.lb.wait`); "do first / frees", "steps", "part of" are teal (`.lb.ok`); "updated by" purple (`.lb.chg`); a suggestion is dashed amber (`.lb.sus`); a link staged in the tray uses the staged look (`.lb.staged`). One badge per kind; several tasks of one kind read "waits on Task-12 +1".
- **The link bar's choice is one remembered value** (`linkView`, localStorage `factotum.linkview`), shared by Today and Timeline, like the Log's view. The bar, the Linked view and the legend are drawn only when some open task has a link, a suggestion or a change marker (`hasLinks()`); otherwise the choice reads as All.
- **"Ready now" keeps hard-dated waiting tasks** (dimmed, with their "waits on" badge): design §7.4 says a hard-deadline task is never hidden because it is waiting.
- **The Linked view is the same on Today and Timeline**: every open task that has a link or a suggestion, regrouped (goals with their steps, chains of waiting tasks from their first blocker, twin cards). Only `same_as` suggestions get a card there; other suggestions are dashed badges and Review cards. "Make them tasks" is left out entirely.
- **Today's order (`arrangeToday`)**: a task that frees a hard-dated task in an EARLIER Today group is moved into that group (never pushed later); within a group, a waiting task is drawn under the task it waits on (one indent level, chains included). Timeline's order does not change (only dimming and badges).
- **Details render only for opened rows.** `row()` leaves `.details` empty unless the row is open; the opened task's payload (`GET /api/tasks/{id}`) is fetched only for an open row on screen or the page's task, cached in `detailCache`, marked stale (not dropped) by every `load()`, and on arrival the page re-renders, or, while something is typed (`typingNow()`), only the two async containers are swapped in place (`swapDetail`).
- **A closed task opens as the task page on both devices** (`openPage`): `jumpTo` of a task no list holds opens the page instead of a toast; on a desktop the page is the same `taskPageHtml`, styled for a wide screen.
- **The phone trail lives in `history.state.from`**: a jump from one task page to another pushes `/t/<N>` with `{page: true, from: <current id>}`; the back button reads "‹ Task-<from>" from `history.state`, so it survives a reload. Back is the browser's Back.
- **History groups a batch's rows**: one entry per batch (one who, one Undo), a line per visible field; the quiet fields (`completed_at`, `closed_by`, `due_is_datetime`, `last_reviewed_at`, `close_on_sent_to`) are not shown.
- **The opened task gains "Ask Claude what else is related"** (the `related` canned prompt 4a added), beside "Search related emails". "Link a task", Remove, "Add a step", Edit, Split and Merge from More are a later stage.

## Payload notes (what was actually built in 4a, read from the code)

- `rel.suggestions[]` entries carry `evidence_id, kind, other, keep, why, signal` and **no direction**: for a `part_of` / `waits_on` suggestion the page cannot tell which end is the step or the waiting one. Task 1 adds `"dir": "out" | "in"` ("out" when the listed task is the suggestion's `a`), in `relations.pending_suggestions`, so the list, the opened task and Review all carry it.
- The compact `rel` has no `freed_at`, `waits_on_closed` or `neighbour_activity_at`; a closed neighbour's `completed_at` and `outcome` come from `neighbours` (list) or `relations[].other` (opened task). `rel.goal_id` may name a closed goal; `steps_total` counts closed steps.
- `GET /api/tasks/{id}` stamps `neighbours_seen_at` on EVERY call, paging with `before` included, and still returns the old `changes` list beside `history` (the page reads `history` only).
- `history.entries[].other` is `{id, name, status}` (no `completed_at`); `relations[].other` has `due, hard, completed_at, outcome` but no `rel` (a goal's step progress comes from the list row when the goal is open).
- `rel.changed.by` may be null (a cause without `from`).
- `GET /api/review` stamps `last_offered_at` on the page it returns (the reason scenarios load Review only in Task 8).
- `POST /api/batches/{id}/undo` returns `{batch_id, restored, skipped: [{task_id, why}], already}`; apply returns `{batch_id, applied, skipped, created, refs}` as planned.
- Merge hazard for the controller (not a task here): `links-backend`'s `api.py` lacks stage 3's `/api/summary` `planner` key and the starter's no-seeding branch; the merge must keep `linked-tasks`' versions of those.

## Review Focus

1. **A linked task that is closed or missing from the open list** (a done step, a merged duplicate, a goal closed meanwhile): its name comes from `neighbours` / `relations`, and a tap opens it read-only with Reopen on both devices; an id that does not exist says so. — Task 7, scenario step P5.
2. **A staged link overwriting a staged decision on the same task**: Done staged on a task, then a chat proposal links that task: the tray holds both entries under different keys. — Task 2, scenario step T2.
3. **A jump to a row that is folded, filtered out, or drawn twice**: a badge naming a task in a folded section while "Ready now" hides it resets the filter to All, unfolds the section and flashes the first copy of the row. — Task 4, scenario step L6.
4. **The phone back trail after a reload at `/t/<id>`**: jump task → linked task, reload, the bar still reads "‹ Task-<first>" and Back lands there, then on the list. — Task 7, scenario step P3.
5. **History arriving while text is typed, and the cost of History on every row**: with the History response delayed, text typed into an open row's reminder label survives its arrival; only open rows have non-empty `.details`; one request per opened task. — Task 6, scenario step D4.

---

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `factotum/relations.py` | `pending_suggestions` adds `dir`; new `freed_by` | 1, 10 |
| `factotum/api.py` | `_page(..., free=)` and the `/r/{token}` Done branch say what is now free | 10 |
| `factotum/web/app.js` | staging keys and words, badges, link filter and Linked view, Today's order, the opened task and History, the task page and trail, Review reasons and suggestions, the Log in words | 2–9 |
| `factotum/web/app.css` | `.lb` badges, `.linkbar`, `.lgroup` Linked view, `.row.waiting` / `.row.under`, `.dgrid` / History, `.pagebar`, desktop `.taskpage`, Review `.why.ok/.chg`, `.lsug` | 3–8 |
| `tests/test_stage4b.py` (new) | suggestion `dir`; no "blocked" in the page; Free now after Done | 1, 10 |
| `tests/test_stage4_merge.py`, `tests/test_stage4_api.py` | two suggestion-shape assertions gain `dir` | 1 |
| `SPEC.md`, `CLAUDE.md` | §9 "Links on the page", phase status, where things live, gotchas | 11 |
| `DEV/seed_links.py`, `DEV/scenarios/s4b-lib.mjs` (outside the repository) | the known set of linked tasks; scenario helpers | 1 |
| `DEV/scenarios/s4b-*.mjs` (outside the repository) | one rendered check per UI task | 2–9 |

---

### Task 1: A suggestion's direction, a vocabulary guard, and the seed

Implementer: **sonnet**. UI task: no (the seed is checked by its JSON and the API).

**Files:**
- Modify: `factotum/relations.py` (`pending_suggestions`)
- Modify: `tests/test_stage4_merge.py` (the `pending_suggestions` equality, near `p[1] == [...]`), `tests/test_stage4_api.py` (`test_review_items_carry_texts_and_link_suggestions`'s `link_suggestions` equality)
- Create: `tests/test_stage4b.py`
- Create (outside the repository): `DEV/seed_links.py`, `DEV/scenarios/s4b-lib.mjs`

**Interfaces:**
- Consumes: `relations.suggest`, `batches.apply` (with `cause=`), `batches.undo`, `db.connect`, `db.init_db`, `config.load`, `config.project_names`.
- Produces:
  - Every entry of `relations.pending_suggestions(...)` gains `"dir": "out"` when the task it is listed under is the suggestion's `a`, else `"in"` (so `rel.suggestions[]`, the opened task's `rel`, and Review's `link_suggestions[]` all carry it).
  - `DEV/seed_links.py [--fresh | --remove | --unsee]` prints, and writes to `DEV/seed_links.json`, the object of named ids below.
  - `DEV/scenarios/s4b-lib.mjs`: `export function seed(...flags): object` (runs the seed with `child_process.execFileSync` and the venv python, returns the parsed JSON), `export async function noSideScroll(page): Promise<boolean>`, `export const DEV: string`.

- [ ] **Step 1: Write the failing tests** in `tests/test_stage4b.py`

```python
"""Stage 4b, the page: the few server pieces it needs (suggestion direction, Free now after Done) and
a guard on the words it shows."""
import re
from datetime import datetime

from fastapi.testclient import TestClient

from factotum import api, batches, relations, reminders
from factotum.db import connect, init_db
from stage4_helpers import TODAY, link, mk


def _client(cfg, tmp_path):
    path = tmp_path / "t.db"
    conn = connect(path)
    init_db(conn)
    return TestClient(api.create_app(cfg, db_path=str(path))), conn


def test_a_suggestion_says_which_end_the_task_is(cfg, tmp_path):
    client, conn = _client(cfg, tmp_path)
    mk(conn, 1); mk(conn, 2)
    eid = relations.suggest(conn, "waits_on", 1, 2, "after", "text"); conn.commit()
    by = {t["id"]: t for t in client.get("/api/tasks").json()["tasks"]}
    assert by[1]["rel"]["suggestions"] == [{"evidence_id": eid, "kind": "waits_on", "other": 2, "keep": None, "why": "after", "signal": "text", "dir": "out"}]
    assert by[2]["rel"]["suggestions"][0]["other"] == 1 and by[2]["rel"]["suggestions"][0]["dir"] == "in"
    items = {i["task"]["id"]: i for i in client.get("/api/review").json()["items"]}
    assert items[2]["link_suggestions"][0]["dir"] == "in" and items[1]["link_suggestions"][0]["dir"] == "out"


def test_the_page_never_says_blocked_or_dependency():
    for f in ("app.js", "app.css", "index.html"):
        assert not re.search(r"\bblocked\b|\bdependenc", (api.WEB / f).read_text(encoding="utf-8"), re.I), f
```

- [ ] **Step 2: Run them**

Run: `/home/robert/factotum/.venv/bin/pytest tests/test_stage4b.py -q`
Expected: the first FAILS (no `dir` key); the second passes (a guard from now on).

- [ ] **Step 3: Add `"dir"` in `relations.pending_suggestions`** (one key in the dict it appends, `"out" if tid == a else "in"`), and add `"dir": "out"` to each suggestion in the two existing equality assertions named under Files (each lists suggestions under their `a`; if one does not, use `"in"` there).

- [ ] **Step 4: Run the whole suite**

Run: `/home/robert/factotum/.venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add factotum/relations.py tests/test_stage4b.py tests/test_stage4_merge.py tests/test_stage4_api.py
git commit -m "relations: a pending suggestion says which end the listed task is (dir), so the page can stage a part_of / waits_on suggestion the right way round; a test keeps blocked and dependency out of the page"
```

- [ ] **Step 6: Write `DEV/seed_links.py`** (not committed; it lives beside `serve.sh`).

Behaviour:
- Puts WT on `sys.path`, sets `FACTOTUM_CONFIG` to `WT/tests/fixtures/factotum.fixture.yaml` when unset, opens `DEV/factotum-dev.db` with `db.connect`, runs `db.init_db` (which also migrates), takes `P1, P2` = the first two of `config.project_names(cfg)`. `today = date.today()`; every name starts `"Seed · "`.
- Every task change goes through `batches.apply` (source `"ui"` unless named), one batch per group, in this order; record every batch id:
  1. goal `goal` "Seed · Assemble the thesis committee" (P1, due today+5); steps created in the same batch with `fields.links`: `step_done` "Seed · Ask the chair about spring availability" (part_of goal), `step_freed` "Seed · Send the director the committee form" (due today+4, context "The committee list on the form depends on the chair's answer.", links part_of goal AND waits_on step_done), `step_open` "Seed · Email the graduate office" (part_of goal, undated).
  2. `blocker` "Seed · Finish the firmware tests" (P2, undated); `waiting_hard` "Seed · Deliver the units to the team" (P2, due today-3, hard 1, waits_on blocker).
  3. chain: `chain_1` "Seed · Get the dataset from the lab" (undated); `chain_2` "Seed · Clean the dataset" (undated, waits_on chain_1); `chain_3` "Seed · Run the analysis" (due today+6, hard 1, waits_on chain_2).
  4. twins: `twin_kept` "Seed · Submit Assignment 10" (P1, due today+3); `twin_goes` "Seed · Final project proposal (Assignment 10)" (P1, due today+3).
  5. pair: `pair_a` "Seed · Book the field site"; `pair_b` "Seed · Plan the field trip budget" (both undated, P2).
  6. `merged_into` "Seed · Reply to the lab manager" and `merged_away` "Seed · Reply to the lab manager (again)"; then a separate batch `{"op": "merge", "task_id": merged_away, "fields": {"into": merged_into}}`.
  7. `goal_done` "Seed · Plan the lab tour" with `goal_done_step` "Seed · Pick a date for the tour" (part_of goal_done).
  8. `waits_dropped` "Seed · Book the room for the defence" (waits_on `dropped_blocker` "Seed · Ask facilities about rooms").
  9. Closes, one batch each: `done` step_done with `fields.outcome` "The chair can serve remotely"; `done` goal_done_step; `drop` dropped_blocker.
  10. The ripple: `batches.apply(conn, [{"op": "set", "task_id": step_freed, "fields": {"context": "The chair can serve remotely, so the committee list is settled. Next: send the form."}, "note": f"Task-{step_done} recorded that the chair can serve remotely"}], "ripple", today, cause={"kind": "ripple", "from": [step_done], "run": 0})` → `ripple_batch`.
- Not batches (they are not task state): `relations.suggest(conn, "same_as", twin_goes, twin_kept, "same number 10, same due time", "twin")` → `twin_evidence`; `relations.suggest(conn, "any", pair_a, pair_b, "both follow one mail thread", "thread")` → `pair_evidence`; a planner proposal on pair_a: `INSERT INTO evidence (task_id, kind, ref, label, excerpt, at) VALUES (pair_a, 'proposal', json {"op": "link", "task_id": pair_a, "fields": {"kind": "waits_on", "other": chain_1}}, "Planner: Task-<pair_a> waits on Task-<chain_1>", "the site needs the dataset first", now)` → `planner_evidence`; a chat thread (`chat_threads` title "Seed · links") with one assistant message (`role 'assistant'`, text "Proposals to try the tray with.", `proposals_json`) holding, in order: `{"op": "link", "task_id": pair_a, "fields": {"kind": "waits_on", "other": chain_1}, "label": "Book the site after the dataset", "evidence": [], "applied": false}`, `{"op": "add_note", "task_id": chain_1, "fields": {"text": "Asked the lab on Tuesday"}, ...}`, `{"op": "set", "task_id": chain_1, "fields": {"context": "The lab promised the dataset by Friday."}, ...}`, `{"op": "done", "task_id": chain_1, "fields": {"outcome": "Dataset received"}, ...}`, `{"op": "merge", "task_id": twin_goes, "fields": {"into": twin_kept}, ...}` → `chat_thread`, `chat_message`; a skill proposal (`proposals` row, source `skill`, label "Seed · tidy the chain", `changes_json` = `[{"op": "unlink", "task_id": chain_3, "fields": {"kind": "waits_on", "other": chain_2}}, {"op": "link", "task_id": step_open, "fields": {"kind": "waits_on", "other": step_freed}}, {"op": "add_note", "task_id": blocker, "fields": {"text": "Bench is free on Thursday"}}, {"op": "merge", "task_id": pair_b, "fields": {"into": pair_a}}]`) → `skill_proposal`. Commit after each.
- Output: `{"goal", "step_done", "step_freed", "step_open", "blocker", "waiting_hard", "chain_1", "chain_2", "chain_3", "twin_kept", "twin_goes", "pair_a", "pair_b", "merged_into", "merged_away", "goal_done", "goal_done_step", "waits_dropped", "dropped_blocker", "twin_evidence", "pair_evidence", "planner_evidence", "chat_thread", "chat_message", "skill_proposal", "ripple_batch", "batches": [...], "today": "YYYY-MM-DD"}`.
- No flag: if `seed_links.json` exists and its `goal` is still open, print it and exit 0; otherwise seed. `--remove`: `batches.undo` every recorded batch, newest first (print each result, never raise), stage a `judge_evidence` `dismissed` batch for the three recorded evidence ids that are still unjudged, delete the chat thread and its messages, set `dismissed_at` on the skill proposal, delete the JSON. `--fresh`: `--remove` (when the JSON exists), then seed. `--unsee`: `UPDATE tasks SET neighbours_seen_at = NULL` for the seeded ids (the purple marker comes back), print the JSON.

- [ ] **Step 7: Write `DEV/scenarios/s4b-lib.mjs`** with the three exports named under Interfaces (`noSideScroll` = `document.documentElement.scrollWidth <= window.innerWidth`).

- [ ] **Step 8: Check the seed against the API**

Run: `DEV/serve.sh restart`, then `/home/robert/factotum/.venv/bin/python DEV/seed_links.py --fresh` twice, then
`curl -s http://127.0.0.1:8751/api/tasks | /home/robert/factotum/.venv/bin/python -c "import json,sys; d=json.load(sys.stdin); print(sum(1 for t in d['tasks'] if t.get('rel')), sorted(d['neighbours']))"` and `curl -s http://127.0.0.1:8751/api/tasks/<step_freed>` (look at `relations`, `history.entries[*].source`, the ripple entry's `cause` and `note`).
Expected: the second `--fresh` removes the first set and makes a new one (new ids); at least 16 listed tasks carry `rel`; `neighbours` names `step_done`, `goal_done_step`, `dropped_blocker`, `merged_away`; `step_freed` has `rel.changed.by == step_done`, `blocker` has `rel.needed_by` = today-3 with `needed_by_via` `"frees"`, `chain_1` has `needed_by_via` `"chain"`, twin and pair suggestions carry `dir`. Report the JSON. Then `--remove` and check that no `Seed ·` task is open; then `--fresh` again for the next task.

---

### Task 2: Staging links, notes and outcomes; the tray in words; Undo that says what it skipped

Implementer: **opus**. UI task: yes.

**Files:**
- Modify: `factotum/web/app.js` — new section `// ---------- links (stage 4b): words and staging keys ----------` after the `// ---------- staging ----------` section; changes in `stageExtra`, `applyAll`, `toast`, `pendingHtml`, `renderTray` (no change needed beyond labels), `propKey`, `stageProposal`, `proposalsHtml`, the `[data-rv-accept]` handler in `wire()`, the `[data-undo]` handler in `wire()`, `reviewAct`, `sendPrompt`.

**Interfaces:**
- Consumes: the payloads in "Payload notes".
- Produces (all in the new section):
  - `relKey(a: number, kind: string, b: number) -> string` = `` `rel:${a}:${kind}:${b}` ``.
  - `stageKey(c: {task_id, op, fields}, suffix?: string) -> string|number` (rules in "Decisions").
  - `linkPredicate(kind: 'part_of'|'waits_on'|'same_as', other: number, removed: boolean) -> string`: `waits_on` → `now waits on Task-N` / `no longer waits on Task-N`; `part_of` → `is now part of Task-N` / `is no longer part of Task-N`; `same_as` → `merged into Task-N` / `is no longer merged into Task-N`. Read with the source task in front ("Task-12 now waits on Task-14").
  - `changeWords(c) -> string`: `link` → `linkPredicate(kind, other, false)`; `unlink` → `linkPredicate(kind, other, true)`; `merge` → `linkPredicate('same_as', into, false)`; `add_note` → `note “<text cut to 60 with …>”`; `done` / `drop` with `fields.outcome` → `done · “<outcome cut to 60>”` / `dropped · “…”`; otherwise `''` (callers keep their own label).
  - `stageChange(c, label: string, extra?: {expect?, note?, ev?}, suffix?: string) -> string|number`: stages under `stageKey(c, suffix)` through `stageExtra`, with `label = changeWords(c) || label`; returns the key.
  - `stageExtra(key, task_id, op, fields, label, extra = {})` — gains the optional sixth argument; the entry is `{task_id, op, fields, label, ...extra}`.
  - `textExpect(id: number, fields: object) -> object|undefined`: `{context?, name?}` = the current value in `byId(id)` (`''` when null) of each text field `fields` names; undefined when it names neither or the task is not in the list.
  - `suggestionStaged(eid: number) -> boolean`: a staged `e:<eid>` entry exists, or a staged entry has `ev === eid`.
  - `undoBatch(id: number) -> Promise<object>` (POST `/api/batches/<id>/undo`, returns the response) and `undoSaid(res) -> string`: `res.already` → `Already undone`; no skips → `Undone`; else `` `Undone · ${k} left as ${k === 1 ? 'it is' : 'they are'}: ${res.skipped[0].why}` ``.

- [ ] **Step 1: Add the section** with the functions above. `stageKey`'s `add_note` uses `` `${id}:${suffix || 'note'}` ``.

- [ ] **Step 2: Send what the server accepts.** `applyAll`: each change is `{task_id, op, fields}` plus `expect` and `note` when the entry has them (never `ev`, never `label`). Its toast adds the first reason when something was skipped: `` `${n} change(s) applied · ${k} skipped: ${res.skipped[0].why}` ``. `toast`'s Undo button and the Log's `[data-undo]` handler call `undoBatch`, then `toast(undoSaid(res))` (the Log one also clears `logData` and reloads as today).

- [ ] **Step 3: The three places that turned unknown ops into `set`.**
  - `propKey(p, mid, i)` = `stageKey(p, ['done', 'drop', 'pin_top', 'longterm'].includes(p.op) ? undefined : 'c' + mid + '-' + i)`; `proposalsHtml`'s "staged" test becomes `staged.has(key)`.
  - `stageProposal(p, mid, i)`: `create`, `link_thread` → false (unchanged); `done` / `drop` → `stage(p.task_id, p.op, p.fields || {}, label)` (the outcome rides along); `pin_top` / `longterm` → as today; `add_reminder` → as today; `link`, `unlink`, `merge`, `add_note` → `stageChange(p, label, {}, 'c' + mid + '-' + i)`; `set` → `stageExtra(propKey(...), p.task_id, 'set', p.fields, label, {expect: textExpect(p.task_id, p.fields)})` (omit `expect` when undefined); any other op → return false (not staged; the toast's count says so).
  - The `[data-rv-accept]` handler: `c = Object.assign({task_id: tid}, e.change)`; `done` / `drop` → `stage(tid, c.op, c.fields || {}, e.label)` (rest unchanged); `pin_top` / `longterm` / `add_reminder` unchanged; `link`, `unlink`, `merge`, `add_note` → `stageChange(c, e.label, {}, 'a' + eid)`; `set` → as today plus `expect: textExpect(tid, c.fields)`; any other op → `toast('This suggestion cannot be staged here')` and return before judging. Judging the planner's evidence (`stageJudge(eid, tid, 'applied', …)`) stays for every staged kind.
  - `sendPrompt`: `res.changes.forEach(c => stageChange(c, <today's label expression>))`.

- [ ] **Step 4: The Review note is appended by the server.** In `reviewAct`, remove the `extra` context; every decision keeps its fields without `context`; after the decision is staged, when the note is non-empty, `stageChange({task_id: id, op: 'add_note', fields: {text}}, '')`. The note box, its placeholder and the card do not change.

- [ ] **Step 5: Words in the skill banner.** `pendingHtml`'s per-change chip uses `changeWords(c)` for `link`, `unlink`, `merge`, `add_note` and for `done` / `drop` with an outcome, producing e.g. `Task-31 no longer waits on Task-30`, `Task-44 merged into Task-43`.

- [ ] **Step 6: Run the suite**

Run: `/home/robert/factotum/.venv/bin/pytest -q`
Expected: all pass (no Python changed).

- [ ] **Step 7: Write and run `DEV/scenarios/s4b-2-tray.mjs`** (both devices). `S = seed('--fresh')` first; Chat's thread list on the phone opens with `#chat-threads-toggle`.
  - T1: go to `/` → shot `s4b-2-banner`: the "Staged by Claude Code" banner lists `Task-<chain_3> … no longer waits on Task-<chain_2>`, `Task-<step_open> … now waits on Task-<step_freed>`, `Task-<blocker> … note “Bench is free on Thursday”`, `Task-<pair_b> … merged into Task-<pair_a>` (no raw `link` / `merge` / `add_note`). Press its Dismiss.
  - T2 (Review Focus 2): type `done <pair_a>` into `#cmd` and press `#cmd-go` (a command: staged, no Claude run); go to `/chat`, open thread `S.chat_thread`, keep every proposal ticked, press `[data-chat-stage]`. Log `JSON.parse(localStorage['factotum.staged']).map(([k]) => k)`. Expected: 6 keys, among them `<pair_a>` (the Done), `rel:<pair_a>:waits_on:<chain_1>`, `<chain_1>:c<m>-1`, `<chain_1>:c<m>-2`, `<chain_1>` and `rel:<twin_goes>:same_as:<twin_kept>`; the `<chain_1>:c<m>-2` entry carries `expect.context`. Open the tray (`#tray-toggle`) → shot `s4b-2-tray`: rows read `Task-<pair_a> … done`, `Task-<pair_a> … now waits on Task-<chain_1>`, `Task-<chain_1> … note “Asked the lab on Tuesday”`, `Task-<chain_1> … done · “Dataset received”`, `Task-<twin_goes> … merged into Task-<twin_kept>`. Each proposal in Chat now shows "staged".
  - T3: in the tray untick everything but the `add_note` row; Apply selected; log the `/api/batches/apply` response (`applied: 1`, no skips). Then Apply selected with only the `set` (context) row ticked: the response skips it with `Task-<chain_1> context changed since this edit was staged; not applied`, and the toast shows that reason → shot `s4b-2-skip`. Discard all.
  - T4: `S = seed('--fresh')`, reload `/chat`, open the new `S.chat_thread`, untick every proposal but the `set`, Stage selected, Apply all (batch A; keep its `batch_id` from the response). Back in Chat untick every proposal but the `add_note`, Stage selected, Apply all (batch B). In the Log (`/log`, Changes view) press Undo on batch A: the toast reads `Undone · 1 left as it is: Task-<chain_1> context was changed after this batch; left as it is` → shot `s4b-2-undo`.
  - Every phone shot: `noSideScroll` true. Expected problems: none.
  - End with `seed('--fresh')`.

- [ ] **Step 8: Look at every screenshot, then commit**

```bash
git add factotum/web/app.js
git commit -m "web: link, unlink, merge, add_note and outcomes stage under their own tray keys (rel:<a>:<kind>:<b> for a link, never the bare id) and read in words in the tray and the skill banner; a staged text edit carries expect, a Review note is add_note; Apply names the first skip, Undo says what it left alone"
```

---

### Task 3: Badges on rows

Implementer: **opus**. UI task: yes.

**Files:**
- Modify: `factotum/web/app.js` — new section `// ---------- links (stage 4b): badges ----------`; changes in `view`, `row`, `chipsHtml`, `renderToday`, `renderTimeline`, `wire()` (badge clicks; the row-click guard).
- Modify: `factotum/web/app.css`.

**Interfaces:**
- Consumes: `staged` entries keyed by `stageKey` (Task 2), `suggestionStaged` (Task 2), `data.neighbours`.
- Produces:
  - `nameOf(id: number) -> {id, name, status, due?, completed_at?, outcome?} | null` = `byId(id) || (data.neighbours || {})[String(id)] || null`.
  - `overlayRel(t) -> rel|undefined` and its use in `view(t)`: when a staged `link` / `unlink` / `merge` entry names `t.id` at either end, or a staged entry makes `suggestionStaged` true for one of `t.rel.suggestions`, `view` returns a copy whose `rel` (an empty compact rel when `t.rel` is absent: `links: [], waits_on_open: [], frees_open: [], goal_id: null, steps_open: 0, steps_total: 0, needed_by: null, needed_by_id: null, needed_by_via: null, changed: null, suggestions: []`) shows it: a staged `link` adds `{kind, dir, other, staged: true}` to both ends' `links`, `waits_on_open` / `frees_open` (when the other end is open), `goal_id` / `steps_total` / `steps_open`; `unlink` removes them; `merge` sets the duplicate's `status` to `'dropped'` and removes the pair's suggestions; a staged suggestion verdict removes that suggestion. Never mutates `data`.
  - `isWaiting(x) -> boolean` = `!!(x.rel && x.rel.waits_on_open && x.rel.waits_on_open.length)`.
  - `badgesFor(x) -> Array<{kind, look, text, jump?, open?, title}>`, in this order (design §12.1), each present only when its condition holds:
    1. `frees` — `rel.frees_open.length`: text `do first: frees Task-N` when `rel.needed_by`, else `frees Task-N`; N = `rel.needed_by_id` when `needed_by_via === 'frees'`, else `rel.frees_open[0]`; look `ok`; `jump` N.
    2. `waits` — `rel.waits_on_open.length`: `waits on Task-N`; look `wait`; `jump` N.
    3. `steps` — `rel.steps_total > 0`: `<done> of <total> steps done` (done = total − open) with a bar; look `ok`; `open` x.id.
    4. `partof` — `rel.goal_id != null`: `part of Task-N`; look `ok`; `jump` N.
    5. `changed` — `rel.changed`: `updated by Task-N` (`by`), or `updated by a linked task` when `by` is null; look `chg`; `open` x.id.
    6. `suggest` — `rel.suggestions.length`: kind `same_as` → `same as Task-N?`; `part_of` → `part of Task-N?` (dir out) / `step: Task-N?` (in); `waits_on` → `waits on Task-N?` (out) / `frees Task-N?` (in); `any` → `linked to Task-N?`; look `sus`; `jump` N.
    When several tasks fit one kind, the text ends ` +k`. A badge whose named link is `staged` has look `staged`. `title` = `Task-N · <nameOf(N).name>` plus ` (done)` / ` (dropped)` for a closed one.
  - `badgeHtml(b) -> string`: `<a class="lb <look>" href="/t/<N>" data-lb="<N>" title="…">…</a>` for a jump, `data-lb-open="<id>"` for an open; the steps badge holds `<span class="bar"><span style="width:P%"></span></span>`; `frees` an arrow icon, `waits` a clock icon, `changed` a dot (frame 1's inline SVGs).
  - `linkChipsHtml(x) -> string`: desktop: every badge, then `needed for <Mon D>` (`<span class="chip need">`) when `rel.needed_by`; phone (`PHONE()`): the first badge, then `<span class="chip morelinks">1 more link</span>` / `k more links`, then the need chip reading `for <Mon D>`. `''` when `x.rel` is absent.
  - `stepsChip(rel) -> string`: the steps badge alone, not a link (`<span class="lb ok steps">`), for Review (Task 8).
  - `goTask(id)`: phone → `openTask(id)`; desktop → `jumpTo(id, false)`.
  - `legendHtml() -> string`: frame 1's three entries, desktop only, and only when `hasLinks()`: `a link you or Claude confirmed`, `a link the app suspects, one tap to confirm`, `changed by a linked task since you last looked`.
  - `hasLinks() -> boolean` = some `openOnes()` row with `rel` whose `links` or `suggestions` is non-empty or `changed` is set (Task 4 uses it too).

- [ ] **Step 1: Implement the section**, the `view` overlay, and the hooks: `chipsHtml` puts `linkChipsHtml(x)` first; `row()` adds class `waiting` when `isWaiting(x)`; `renderToday` and `renderTimeline` append `legendHtml()` after their groups; in `wire()`, `[data-lb]` → `preventDefault`, `stopPropagation`, `goTask(+id)`; `[data-lb-open]` → `preventDefault`, `stopPropagation`, `openTask(+id)`; the `.main` click guard becomes `e.target.closest('input, button, a, .chip.due')`.

- [ ] **Step 2: CSS** (colours from frame 1, as tokens): `.lb` (inline-flex, gap 5px, 11px, padding 3px 9px, radius 999px, weight 500, no underline, nowrap, cursor pointer); `.lb.ok` accent-soft / accent; `.lb.wait` warn-soft / warn; `.lb.chg` top-soft / top (its dot `background: var(--top)`); `.lb.sus` surface background, `1px dashed var(--warn)`, warn text; `.lb.staged` accent / on-accent; `.lb .bar` 28x5px surface track with an accent fill; `.lb .code`-like Task-N in `var(--mono)`; `.chip.need` crit-soft / crit, mono; `.chip.morelinks`; `.row.waiting .main` `background: var(--surface-2)` and `.row.waiting .name` `color: var(--muted)`; `.lblegend` (frame 1's legend row, 12px muted, three swatches). Phone (`max-width: 700px`): `.lb` 12px, `padding: 6px 10px`, `min-height: 32px` (a full tap target); `.lblegend` hidden.

- [ ] **Step 3: Run the suite** (`/home/robert/factotum/.venv/bin/pytest -q`, all pass).

- [ ] **Step 4: Write and run `DEV/scenarios/s4b-3-badges.mjs`** (both devices; `S = seed('--fresh')`).
  - B1: `/timeline`, unfold every section (click each `[data-fold][aria-expanded=false]`), full-page shot `s4b-3-timeline`. Compare with frames 1 and 8. Desktop expected: `waiting_hard` dimmed with amber `waits on Task-<blocker>`; `blocker` with teal `do first: frees Task-<waiting_hard>` and red `needed for <date of waiting_hard>`; `chain_2` `do first: frees Task-<chain_3>`; `chain_1` `do first: frees Task-<chain_2>` and `needed for <chain_3's date>`; `goal` `1 of 3 steps done` with a one-third bar; `step_freed` purple `updated by Task-<step_done>` then teal `part of Task-<goal>`; `twin_kept` dashed `same as Task-<twin_goes>?`; `pair_a` dashed `linked to Task-<pair_b>?`; the legend at the bottom. Phone expected: one badge per row; `step_freed` shows `updated by Task-<step_done>` and `1 more link`; the need chip reads `for <date>`; badges at least 32 px tall (log the bounding box height of one).
  - B2: log the `title` of `step_freed`'s purple badge: it names `step_done`'s name with ` (done)` (a closed task, named from `neighbours`).
  - B3: click `waiting_hard`'s `waits on` badge. Desktop: path stays `/timeline`, `blocker`'s row has class `flash` and `focus`. Phone: path is `/t/<blocker>` and `.taskpage` shows. Shot `s4b-3-jump`. Back.
  - B4: click `step_freed`'s `updated by` badge. Desktop: the row opens (class `open`), a `GET /api/tasks/<step_freed>` is seen (log it via `page.waitForResponse`). Phone: its task page opens. Shot `s4b-3-changed`.
  - B5 (staged overlay): go to `/chat`, open `S.chat_thread`, untick every proposal but the first (pair_a waits on chain_1), Stage selected; go to `/timeline`: `pair_a` shows `waits on Task-<chain_1>` in the staged look and is dimmed; shot `s4b-3-staged`. Discard all.
  - B6 (no links): `seed('--remove')`, reload `/` and `/timeline`: log `document.querySelectorAll('.lb, .lblegend, .row.waiting, .chip.need, .chip.morelinks').length` → `0`; shot `s4b-3-nolinks` (rows look as they did before this stage).
  - `noSideScroll` true on every phone shot. End with `seed('--fresh')`.

- [ ] **Step 5: Look at every screenshot, then commit**

```bash
git add factotum/web/app.js factotum/web/app.css
git commit -m "web: rows carry link badges (do first / frees, waits on, steps done, part of, updated by, a dashed suggestion), each naming the task and jumping to it; one badge and 'N more links' on a phone; a waiting row is dimmed; staged links show before Apply; nothing changes without links"
```

---

### Task 4: All / Linked / Ready now, and the Linked view

Implementer: **opus**. UI task: yes.

**Files:**
- Modify: `factotum/web/app.js` — new section `// ---------- links (stage 4b): filters and the Linked view ----------`; changes in `todayGroups`, `timelineGroups`, `groupsOf`, `jumpTo`, `renderToday`, `renderTimeline`, `wire()`.
- Modify: `factotum/web/app.css`.

**Interfaces:**
- Consumes: `hasLinks`, `isWaiting`, `nameOf`, `badgesFor`, `goTask`, `stageChange`, `suggestionStaged`, `stageJudge`, `changeWords`.
- Produces:
  - `let linkView = 'all'` (one of `all`, `linked`, `ready`), read from and written to localStorage `factotum.linkview` like `logView`; `setLinkView(v)`; `effectiveLinkView()` = `hasLinks() ? linkView : 'all'`.
  - `readyRow(x) -> boolean` = `!isWaiting(x) || !!(x.hard && x.due)` (a hard-dated waiting task stays).
  - `todayGroups()` and `timelineGroups()` drop rows failing `readyRow` when `effectiveLinkView() === 'ready'` (same keys, same titles).
  - `linkedGroups() -> Array<{key, kind: 'goal'|'waits'|'twin', rows, ...}>` built from `openOnes()`:
    - `goal` (key `linked:g<id>`): every open task with `rel.steps_total > 0`, ordered by due then score; `rows` = the goal, then its open steps (`rel.links` with `kind 'part_of'`, `dir 'in'`); `closed` = its closed steps from `nameOf`; header `Steps of one goal`, a progress bar and `<done> of <total> done`.
    - `waits` (key `linked:w<id>`): every open task that frees an open task and waits on none; `rows` = it, then everything that waits on it, depth first through `rel.frees_open` (a seen set guards a loop); header `One task is holding up another` (one waiting) or `One task is holding up <k> others`; on the right, when a waiting task is hard-dated and overdue, `the waiting task is <fmtDue label>` in crit.
    - `twin` (key `linked:t<evidence_id>`): one per unstaged `same_as` suggestion (deduplicated by `evidence_id`), both ends open: `goes` = the end that is not `keep`, `keep` = `s.keep`; header `Possibly the same task, twice`, the suggestion's `why` on the right; two columns, each `Task-N name` over `from <source>, due <fmtDue label>` (`Canvas` → `from Canvas`, `Plaud` → `from a recording`, `Mail` → `from mail`, `Manual` → `made by hand`, else `from <source>`; `no date` when undated); buttons `Merge into <keep>` (`data-twin-merge="<goes>:<keep>:<eid>"`) and `Not the same` (`data-twin-not="<eid>:<goes>:<keep>"`).
    A task may appear in more than one group.
  - `linkedHtml() -> string`: the groups as `<section class="lgroup [twin]">`; open rows through `row(x)` (steps and waiting rows get class `under`); a closed step is `<a class="lstep closed" href="/t/N" data-lb="N">` with a tick, the struck-through name, `Outcome: <outcome>` when set, and a chip `done <Mon D>` / `dropped <Mon D>` from `completed_at`. When nothing qualifies: `<div class="empty">No linked tasks.</div>`.
  - `linkBarHtml() -> string` (only when `hasLinks()`): `<div class="linkbar"><div class="seg" role="tablist" aria-label="Show">` with `All <n>`, `Linked <n>`, `Ready now <n>` (`data-linkview`; counts: open tasks, distinct rows `linkedGroups` shows, open tasks passing `readyRow`), then the hint `A badge names the linked task. Click it to jump there.` (`Tasks that belong together, shown together.` while Linked is chosen).
  - `groupsOf('today'|'timeline')` returns `linkedGroups()` while Linked is chosen.
  - `jumpTo`: when no group holds the id and `linkView !== 'all'`, `setLinkView('all')` and search again (then unfold as today).

- [ ] **Step 1: Implement**: `renderToday` = summary + composer + `linkBarHtml()` + (Linked ? `linkedHtml()` : today's groups and empty notes) + legend + today's footer note; `renderTimeline` likewise. Clicks: `[data-linkview]` → `setLinkView` and `render()`; `[data-twin-merge]` → `stageChange({task_id: goes, op: 'merge', fields: {into: keep}}, '', {ev: eid})` and toast `Merge staged · press Apply in the tray`; `[data-twin-not]` → `stageJudge(eid, goes, 'dismissed', label)` with the label "not the same as Task-<keep>". A twin card whose `suggestionStaged(eid)` is true is not drawn.

- [ ] **Step 2: CSS**: `.logbar .seg` rules also apply to `.linkbar .seg` (one selector list); `.linkbar` flex row, margin-bottom 14px; phone: `.linkbar .seg` a three-column grid, full width, buttons `min-height: 44px`, the hint hidden. `.lgroup` (surface, line border, radius 8px, margin-bottom 16px); `.lgroup .lhead` (surface-2 band, 12px uppercase muted, the bar 90x6px); `.lgroup.twin` `border-style: dashed; border-color: var(--warn)` with a warn-soft head; `.lgroup .tw` two columns + buttons (phone: one column, rows stacked above full-width buttons, `min-height: 44px`); `.row.under .title` `padding-left: 18px`; `.lstep.closed` grid like frame 2's done step.

- [ ] **Step 3: Run the suite** (all pass).

- [ ] **Step 4: Write and run `DEV/scenarios/s4b-4-linked.mjs`** (both devices; `S = seed('--fresh')`).
  - L1: `/` → shot `s4b-4-all`: the bar with three counts above the groups (phone: full-width three buttons, compare frame 8).
  - L2: click `Linked` → full-page shot `s4b-4-linked`; compare frame 2: a goal section for `goal` (`1 of 3 done`, `step_done` struck through with `Outcome: The chair can serve remotely` and `done <today>`, `step_freed` and `step_open` as indented rows), a goal section for `goal_done` (`1 of 1 done`), a holding-up section for `blocker` → `waiting_hard` with `the waiting task is 3d overdue`, one for `chain_1` → `chain_2` → `chain_3`, a dashed twin card `twin_goes` / `twin_kept` with `Merge into <twin_kept>` and `Not the same`; no "Make them tasks". Phone: one column; the twin card's rows stack above full-width buttons.
  - L3: reload: Linked is still chosen (log `localStorage['factotum.linkview']`). Go to `/timeline`: Linked view there too.
  - L4: on `/timeline` choose `Ready now` and log the ids of `#panel .row`: `chain_2` (waiting, undated) is absent; `waiting_hard` and `chain_3` (waiting, hard-dated) are present and dimmed; `blocker`, `chain_1` present. Shot `s4b-4-ready`.
  - L5: Linked; press `Merge into <twin_kept>`: the card disappears, the tray reads `Task-<twin_goes> merged into Task-<twin_kept>`; Apply; log the response (`applied >= 1`, no skips) and that the `twin_goes` row is gone and the card is not drawn again. Shot `s4b-4-merged`.
  - L6 (Review Focus 3): Discard all, `seed('--fresh')`, reload `/timeline`, choose `Ready now`, fold `Undated · backlog`, then click `chain_3`'s `waits on Task-<chain_2>` badge (`chain_2` is hidden by Ready now AND would sit in the folded section). Desktop expected: `linkView` is back to `all` (log localStorage), `Undated · backlog` is unfolded, `#panel .row.flash` has `data-id` `<chain_2>`, and it is the first `.row[data-id="<chain_2>"]` in the DOM (Timeline also draws it under "Recently added": log the count of rows with that id, 2). Phone: the badge opens `/t/<chain_2>`; log the path. Shot `s4b-4-jump`.
  - `noSideScroll` true on phone shots. End with `seed('--fresh')`.

- [ ] **Step 5: Look at every screenshot, then commit**

```bash
git add factotum/web/app.js factotum/web/app.css
git commit -m "web: All / Linked / Ready now on Today and Timeline, remembered; Ready now hides waiting tasks but never a hard-dated one; the Linked view groups a goal with its steps (done ones struck through with their outcome), a chain from its first blocker, and a suspected twin with Merge into N / Not the same; a jump to a filtered-out row resets the filter"
```

---

### Task 5: Today's order

Implementer: **opus**. UI task: yes.

**Files:**
- Modify: `factotum/web/app.js` — new section `// ---------- links (stage 4b): Today's order ----------`; `todayGroups` calls it; `row()` reads `x.under`.
- Modify: `factotum/web/app.css` (only if `.row.under` from Task 4 needs a Today variant).

**Interfaces:**
- Consumes: `isWaiting`, `readyRow`, `effectiveLinkView`.
- Produces: `arrangeToday(groups: Array<{key, rows}>) -> same groups with new row arrays`, applied by `todayGroups()` after the Ready filter. Rows are the `view()` copies `openOnes()` returns, so it may set `x.under = true` on them; `row()` adds class `under` when `x.under`.

- [ ] **Step 1: Implement `arrangeToday`** (nothing happens to a group without links, so a list without links is unchanged, order included):

```
1. pull forward: for every open task x (openOnes(), passing the Ready filter) with rel.needed_by and
   needed_by_via in ('frees', 'chain'), let H = rel.needed_by_id and h = index of the first group whose
   rows hold H. If h exists and no group at index <= h holds x: remove x from every group after h and
   insert it into group h just before H.
2. under: in each group, parent(w) = the first id of w.rel.waits_on_open that is a row of the same group
   (never w itself). Rebuild the group as a depth-first walk: roots in their current order, each root
   followed by its children (current order), recursively, with a seen set (a loop in old data draws
   each row once). Every row that has a parent gets under = true.
A hard-dated waiting task is never moved between groups (rule 2 only reorders inside a group); rule 1
only moves a task to an earlier group.
```

- [ ] **Step 2: Run the suite** (all pass).

- [ ] **Step 3: Write and run `DEV/scenarios/s4b-5-order.mjs`** (both devices; `S = seed('--fresh')`).
  - O1: `/` (All) → full-page shot `s4b-5-today`; compare frame 1 and frame 8. Log the row ids per section (`section.group` → its `.row` ids). Expected: `Overdue · hard deadlines` = … `blocker`, `waiting_hard` (indented, dimmed) in that order; `Rest of the week` holds `chain_1`, `chain_2` (indented), `chain_3` (indented) consecutively, and `goal`, `step_freed`, `twin_kept`, `twin_goes` as before; `blocker` shows `do first: frees Task-<waiting_hard>` and `needed for <date>` (phone: `for <date>`).
  - O2: `Ready now`: `waiting_hard` and `chain_3` are still in their groups (hard-dated), `chain_2` is gone; log the ids again.
  - O3: `seed('--remove')`, reload: log the ids per section and compare them with the order of `data.tasks` filtered as `todayGroups` filters (evaluate in the page: no row has class `under`, and each section's order equals the API's order). Shot `s4b-5-nolinks`.
  - `noSideScroll` true. End with `seed('--fresh')`.

- [ ] **Step 4: Look at every screenshot, then commit**

```bash
git add factotum/web/app.js factotum/web/app.css
git commit -m "web: on Today a task that frees a hard-dated task is drawn in that task's (earlier) group, and a waiting task is drawn under the task it waits on in its own group, chains included; a hard-dated task never leaves its group"
```

---

### Task 6: A task opened on a desktop: Where this stands, Linked tasks, Came from the same place, History

Implementer: **opus**. UI task: yes.

**Files:**
- Modify: `factotum/web/app.js` — new section `// ---------- the opened task (stage 4b): linked tasks, provenance, History ----------`; changes in `row`, `detailsHtml`, `render`, `load`, `wire()`, `sendChat` (the canned bubble text), `batchDigest` (its `say` moves out, unchanged, as `fieldSaid`).
- Modify: `factotum/web/app.css`.

**Interfaces:**
- Consumes: `nameOf`, `goTask`, `undoBatch`, `undoSaid`, `fmtDue`, `typingNow`, `stepsChip`.
- Produces:
  - `const detailCache = new Map()` (id → the `GET /api/tasks/{id}` payload, plus `stale: true` after a `load()`, or `{id, missing: true}` on 404, or `{id, failed: <message>}`); `const detailBusy = new Set()`.
  - `loadDetail(id: number, before?: number) -> Promise<void>`: one request per id at a time; with `before`, appends `history.entries` and replaces `more` / `next_before`; on success clears `rel.changed` on the list's row (`byId(id)`, the server just stamped it); then `typingNow() ? swapDetail(id) : render()`.
  - `ensureDetails()`, called at the end of `render()`: on a desktop, for each id in `ui.open` whose `.row.open` is on screen, `loadDetail(id)` when it is not cached, is stale, and is not in flight.
  - `load()` marks every cached entry `stale` (what is shown stays until the new answer arrives).
  - `fieldSaid(c) -> string`: today's `say` from `batchDigest`, moved to module level (Task 9 and History use it).
  - `whereHtml(x) -> string`: `<section class="dsec"><h4>Where this stands</h4><p class="ctx">` the context, or `Nothing written yet.` `</p></section>`.
  - `detailAsyncHtml(id) -> string` (the content of `[data-dlinks="<id>"]`): from the cache: the rewritten line, `linkedListHtml`, `nearbyHtml`; `''` before the payload arrives.
  - `rewrittenHtml(d) -> string`: when the newest `context` entry in the loaded History has `cause.from`: `Rewritten <Mon D> after Task-N. Earlier versions are in History.` (Task-N a link).
  - `linkedListHtml(d) -> string`: `<section class="dsec"><h4>Linked tasks</h4>` one `<a class="lk" href="/t/N" data-open-task="N">` per `d.relations` entry: a kind word (`part of`, `step`, `waits on` / `waited on` when the other is closed, `frees`, `merged into`, `merged from`), `Task-N` and the name (struck through when closed), and a state line: open → `open` plus `, <done> of <total> steps done` when the other is a goal in the list, plus `, due <fmtDue label>` when dated; closed → `<status> <Mon D>` plus `: <outcome>` when set. `''` when there are none.
  - `nearbyHtml(d) -> string`: one line per (via, label): `Same <via>` + ` “<label>”` when labelled + ` as Task-30, Task-31.` (links), then ` Shown for context, not a link.`; under `<h4>Came from the same place</h4>`; `''` when `d.nearby` is empty.
  - `historyHtml(id) -> string` (the content of `[data-dhist="<id>"]`): `<h4>History</h4>` with the hint `how this task got to where it is`, then one `.hitem` per batch (consecutive entries with the same `batch_id`): a `when` (`Nov 10 14:12`, mono), a `who` chip, `Undo` / `Undo all <batch_changes> changes made together` (when `batch_tasks > 1`; `data-hist-undo="<batch_id>"`) or an `undone` chip, then one line per visible field (`historyWords(e, id)`), each followed by `Why: <note>` (muted, Task-N linked) when the entry has a note, and a before / after block for a `context`, `name` or `why_now` change that is not an append; `Reviewed, kept as it is.` when every field is quiet; then `<button class="btn small" data-hist-more="<id>">Earlier</button>` when `history.more`. `Loading History…` before the payload; `Could not load History (<message>).` on failure.
  - `whoText(entry) -> {text, cls}`: `ui`, `review`, `command` → `you`; `ripple` → `ripple from Task-N` (cls `chg`; N = `cause.from[0]`, else `ripple`); `automation` → `automatic`; `planner` → `planner`; `plaud` → `a recording`; `mail` → `mail`; `canvas` → `Canvas`; `connector` → `a connector`; `skill` → `Claude Code`; `chat` → `chat`; `ntfy` → `a reminder`; `import` → `import`; anything else as it is.
  - `historyWords(e, selfId) -> string`: a `link` entry (added when `new` is set, removed when only `old` is): dir `out` + `waits_on` → `Now waits on Task-N.` / `No longer waits on Task-N.`; `in` + `waits_on` → `Now frees Task-N.` / `No longer frees Task-N.`; `out` + `part_of` → `Linked as part of Task-N.` / `No longer part of Task-N.`; `in` + `part_of` → `Task-N linked as a step.` / `Task-N is no longer a step.`; `out` + `same_as` → `Merged into Task-N.` / `No longer merged into Task-N.`; `in` + `same_as` → `Task-N merged into this task.` / `Task-N is no longer merged into this task.` (N a link). `context`: an append (new starts with old) → `Note added: “<added>”`, else `Text rewritten.`; `name` → `Renamed.`; `outcome` → `Outcome: “<v>”`; `evidence` → `Suggestion accepted.` / `Suggestion dismissed.` / `Suggestion seen.` / `Suggestion staged.` by verdict; `created` → `Created.`; anything else → `fieldSaid(e)` with a capital first letter and a full stop.
  - `swapDetail(id)`: replaces the inner HTML of `#panel [data-dlinks="<id>"]` and `#panel [data-dhist="<id>"]` (every copy) and wires them with `wireDetail(root)`.
  - `wireDetail(root)`: `[data-open-task]` → `preventDefault`, `goTask(+id)` (Task 7 extends it for the page); `[data-hist-undo]` → `undoBatch`, `toast(undoSaid(res))`, mark the cache stale, `await load()`; `[data-hist-more]` → `loadDetail(id, d.history.next_before)`. `wire()` calls it on the panel.
  - `row(x)` renders `detailsHtml(x)` only when `ui.open.has(x.id)`; otherwise `.details` is empty.
  - `detailsHtml(x)` = `<div class="dgrid"><div class="dcol">` `whereHtml(x)`, `<div data-dlinks="<id>">detailAsyncHtml</div>`, today's two `<dl>`s without the Context entry `</div><div class="dcol" data-dhist="<id>">historyHtml</div></div>` + today's actions (open tasks only), which gain `<button class="btn small" data-chat-canned="related" data-id="<id>" title="A chat turn: finds the tasks this one belongs with and offers to link them">Ask Claude what else is related</button>` after `✉ Draft reply`.
  - `sendChat`'s user bubble for a canned turn: `search_mail` → `Search related emails`, `draft_reply` → `Draft a reply`, `related` → `What else is related?`.

- [ ] **Step 1: Implement** the section and hooks above.

- [ ] **Step 2: CSS**: `.row.open .details` stays a two-column grid holding `.dgrid` across both columns (`grid-column: 1 / -1`); `.dgrid` two columns `minmax(0, 1fr) minmax(0, 1.15fr)`, gap 24px (frame 3); `.dsec h4` (11px uppercase muted, weight 600); `.lk` (frame 3's linked-task card: 74px kind column, surface, line border, radius 6px; kind colour accent, `waited on` / closed ok-green); `.lk .s` 12px muted; `.hitem` (surface, a line between items), `.hitem .who` chip (surface-2 / muted; `.who.you` accent-soft / accent; `.who.chg` top-soft / top), `.hitem .hdiff` (frame 3's before / after grid: 48px label column, `before` crit, `after` ok, bg `var(--bg)`, radius 6px), `.hitem.undone` muted. Phone rules wait for Task 7 (the phone shows details on the task page only).

- [ ] **Step 3: Run the suite** (all pass).

- [ ] **Step 4: Write and run `DEV/scenarios/s4b-6-detail.mjs`** (desktop; on the phone log only that a tap still opens the page and no `.row .details` has content: `[...document.querySelectorAll('.row .details')].filter(d => d.innerHTML.trim()).length` → `0`). `S = seed('--fresh')`.
  - D1: `/timeline`, double-click `step_freed`'s name → shot `s4b-6-open`; compare frame 3: left column `Where this stands` (the rewritten text), `Rewritten <today> after Task-<step_done>. Earlier versions are in History.`, `Linked tasks` (`part of` Task-<goal> `open, 1 of 3 steps done, due …`; `waited on` Task-<step_done> struck through `done <today>: The chair can serve remotely`), Score / Why now / Source / Links / Lifecycle / Reminders; right column `History`: a `ripple from Task-<step_done>` entry with `Text rewritten.`, `Why: Task-<step_done> recorded that the chair can serve remotely`, before / after; a `you` entry `Now waits on Task-<step_done>.` / `Linked as part of Task-<goal>.` with `Undo all <n> changes made together`; actions with `Ask Claude what else is related` (do not click it).
  - D2: the purple `updated by` badge on `step_freed`'s row is gone after the History arrives (the server stamped it); log it.
  - D3: open `merged_into`: History shows `Task-<merged_away> merged into this task.`; open `goal`: Linked tasks lists three steps (`step`).
  - D4 (Review Focus 5): `page.route('**/api/tasks/<chain_1>', r => setTimeout(() => r.continue(), 2000))`; on `/timeline` double-click `chain_1`'s name, immediately click its `⏰ Set reminder`, type `remember the cable` into the label box; wait 3 s; log the label box's value (`remember the cable`), that History is shown (`[data-dhist="<chain_1>"] .hitem` count ≥ 1), and the number of requests to `/api/tasks/<chain_1>` (exactly 1). Log `document.querySelectorAll('.row .details')` with content = only the open rows. Shot `s4b-6-typing`.
  - D5: on `step_freed`'s History, press Undo on the ripple entry: toast `Undone`; the text is back to "The committee list on the form depends on the chair's answer."; the entry shows `undone`. Shot `s4b-6-undo`.
  - D6: open `chain_3` (Rest of the week); press `Earlier` only if shown (log whether it is: a fresh seed has fewer than 30 entries per task, so it is expected absent).
  - End with `seed('--fresh')`.

- [ ] **Step 5: Look at every screenshot, then commit**

```bash
git add factotum/web/app.js factotum/web/app.css
git commit -m "web: an opened row shows Where this stands, Linked tasks (kind, state, outcome, a jump), Came from the same place and its History (who, what in words, why, before and after, Undo per batch, Earlier), fetched only for the opened task and swapped in place while something is typed; rows no longer build details they do not show"
```

---

### Task 7: The task page: sections, Done / Later / More pinned on a phone, the trail, and a closed task on both devices

Implementer: **opus**. UI task: yes.

**Files:**
- Modify: `factotum/web/app.js` — the `the phone task page (stage 1)` section (`taskPageHtml`, `openTask`, `closeTask`; `fetchPageTask` and `ui.pageTask` are replaced by the detail cache), `setAddress`, `applyAddress`, `jumpTo`, `act`, `render`, `ensureDetails`, `wireDetail`, `wire()`.
- Modify: `factotum/web/app.css`.

**Interfaces:**
- Consumes: `detailCache`, `loadDetail`, `ensureDetails`, `whereHtml`, `detailAsyncHtml`, `historyHtml`, `chipsHtml`, `detailsHtml`'s facts and actions.
- Produces:
  - `setAddress(path, replace, from = null)`: a task entry's state is `{page: true, from}` (`from` a task id or null).
  - `openTask(id, push = true, from = null)`: on a phone as today (with `from` in the pushed state); on a desktop `jumpTo(id, true)` as today.
  - `openPage(id, push = true, from = null)`: the page on either device (`ui.page = id`, `ui.pageMore = ui.pageLater = false`, scroll position kept as today, `/t/<id>` pushed unless `push` is false).
  - `jumpTo(id, expand, replace)` returns `'row'` or `'page'`: when no list holds the id it calls `openPage(id, !replace)` and returns `'page'` (no more "is not in a list" toast); when it finds the row while `ui.page` is set on a desktop, it clears `ui.page` and pushes the tab's address.
  - `applyAddress`: desktop branch keeps `/t/<id>` when `jumpTo(...)` returned `'page'` (replaces it with the tab's address only for `'row'`).
  - `ensureDetails()` also loads `ui.page`'s payload (both devices).
  - `taskPageHtml()`: the bar `‹ Task-<from>` when `history.state && history.state.from`, else `‹ Back`, then `Task-<id>`; the title; `chipsHtml`; for a closed task `This task is <status>.` and `<button class="btn" data-act="reopen" data-id="<id>">Reopen</button>` (or the staged chip when a reopen is staged); `whereHtml`, `[data-dlinks]`, `[data-dhist]`, the facts (`<dl>`s); for an open task the actions block only while `ui.pageMore` (heading `More`), and on a phone `<div class="pagebar"><button data-page="done">Done</button><button data-page="later" aria-expanded>Later</button><button data-page="more" aria-expanded>More</button></div>` with, while `ui.pageLater`, a line `Comes back on:` `Tomorrow` / `Friday` / `Next week` (`data-page-later="tomorrow|fri|nextweek"`, staging `set snoozed_until` like the Review card's Later, label `later · back <when>`). On a desktop the actions are always shown (no pinned bar). The task comes from `byId(id)` or the cache; `Loading Task-<id>…`, `Task-<id> does not exist.`, `Could not load Task-<id> (<message>).` as today.
  - `act(id, 'reopen')` → `stage(id, 'reopen', {}, 'reopened')`.
  - In `wireDetail`, `[data-open-task]`: when `ui.page !== null` and (phone, or the target is in no list) → `openTask`/`openPage(N, true, ui.page)` (the trail); otherwise `goTask(N)`.

- [ ] **Step 1: Implement**; delete `fetchPageTask`, `pageFetch` and `ui.pageTask` (their job is the cache's); `Escape` on a page still goes Back.

- [ ] **Step 2: CSS**: desktop `.taskpage` (outside the phone media query): `max-width: 980px`, the bar a flex row, `h2` 20px, `.dgrid` as in rows; phone: `.taskpage .dgrid` one column, sections in frame 9's order, `.lk` cards 56px tall with a chevron, `padding-bottom: 96px` when the page has a `.pagebar`; `.pagebar` fixed at the bottom (`left/right: 0`, safe-area padding, surface, top border), a three-column grid of 48px buttons: Done ok-soft / ok, Later warn-soft / warn, More surface-2 (frame 9); `.pagebar` sits above the tray when the tray shows (set its `bottom` in `wire()` like the bulk bar); `.page-later` line above the bar.

- [ ] **Step 3: Run the suite** (all pass).

- [ ] **Step 4: Write and run `DEV/scenarios/s4b-7-page.mjs`** (both devices; `S = seed('--fresh')`).
  - P1 (phone): `/`, tap `step_freed`'s name → shot `s4b-7-page`; compare frame 9: `‹ Back`, `Task-<step_freed>`, title, chips, Where this stands, Linked tasks (two cards), History (ripple entry with before / after and Undo), the pinned Done / Later / More. Press `More`: the actions appear (Drop, Snooze to tomorrow, Pin to Top, Mark long-term, due, reminder, the chat buttons — not clicked). Press `Later`: `Tomorrow` / `Friday` / `Next week`; press `Tomorrow`: the tray shows `later · back tomorrow` and the bar moves above it; shot `s4b-7-later`. Discard all.
  - P2 (phone): on that page tap the `part of` card → `/t/<goal>` with the bar `‹ Task-<step_freed>`; shot `s4b-7-trail`.
  - P3 (Review Focus 4, phone): reload at `/t/<goal>`: the bar still reads `‹ Task-<step_freed>`; press it: `/t/<step_freed>`, bar `‹ Back`; press Back: `/`, the list, scrolled where it was. Log each path and bar text.
  - P4 (desktop): `/t/<step_freed>` expands the row in place on Timeline/Today as before (path back to the tab's address); log it.
  - P5 (Review Focus 1, both): open `goal`'s Linked tasks (desktop: expand the row; phone: its page) and click the struck-through `step_done` → its page (desktop too): `This task is done.`, `Reopen`, Linked tasks `part of Task-<goal>`, History with `Created.` / `done`; no pinned bar; shot `s4b-7-closed`. Press `Reopen`: the tray shows `Task-<step_done> reopened`; Discard. Then `/t/<merged_away>`: `This task is dropped.`, Linked tasks `merged into Task-<merged_into>`. Then `/t/99999999`: `Task-99999999 does not exist.` (an expected 404 on `/api/tasks/99999999`).
  - P6 (desktop): from the closed page click `part of Task-<goal>` (an open task): the page closes, `goal`'s row is open and flashing on its tab, the address is the tab's.
  - `noSideScroll` true on phone shots. End with `seed('--fresh')`.

- [ ] **Step 5: Look at every screenshot, then commit**

```bash
git add factotum/web/app.js factotum/web/app.css
git commit -m "web: the task page gains the opened task's sections, Done / Later / More pinned at the bottom on a phone, and a trail ('‹ Task-N', kept in history.state so it survives a reload); a closed or merged task opens read-only with Reopen on a desktop too, where it used to be a toast"
```

---

### Task 8: Review: link reasons in words, the steps chip, link suggestions

Implementer: **opus**. UI task: yes (the only task whose scenario loads Review).

**Files:**
- Modify: `factotum/web/app.js` — `renderReview`, `wire()`.
- Modify: `factotum/web/app.css`.

**Interfaces:**
- Consumes: `review.items[].texts`, `review.items[].link_suggestions` (with `dir`, Task 1), `stepsChip`, `stageChange`, `stageJudge`, `suggestionStaged`, `nameOf`, `changeWords`.
- Produces:
  - `REASON_CLS = {freed: 'ok', steps_done: 'ok', blocker_gone: 'warn', linked_changed: 'chg', link_suggested: 'new'}`; the card's `.why` class is `REASON_CLS[primary]` when present, else today's rule.
  - `reasonText(item) -> string` (HTML): `item.texts[item.primary]` when present (escaped, every `Task-N` a `<a href="/t/N" data-open-task="N">`), else `review.labels[primary]` as today; the `· also …` labels and the `planner` flag unchanged.
  - The card's `.sub` gains `stepsChip(x.rel)` after the effort chip when `x.rel && x.rel.steps_total`.
  - `linkSuggestionsHtml(item) -> string`: for each `item.link_suggestions` entry not `suggestionStaged(evidence_id)`, a `<div class="extra lsug">`: `<span class="chip">Suggested link</span>`, the question, the buttons, then `<span class="hint">— <why></span>`:
    - `same_as`: `Same task as Task-N <other_name>?`, `Merge into <keep>` and `Not the same`.
    - `part_of` out: `Part of Task-N <other_name>?`; in: `Task-N <other_name> is a step of this?`; `waits_on` out: `Waits on Task-N <other_name>?`; in: `Task-N <other_name> waits on this?` — each with `Link` and `Not related`.
    - `any`: `Related to Task-N <other_name>?` with `is a step of it`, `waits on it`, `is the same task`, `Not related`.
  - The changes the buttons stage (all `stageChange(change, '', {ev: evidence_id})`, card = the card's task id, N = `other`): `Link` → `{task_id: dir === 'out' ? card : N, op: 'link', fields: {kind, other: dir === 'out' ? N : card}}`; `is a step of it` → `{task_id: card, op: 'link', fields: {kind: 'part_of', other: N}}`; `waits on it` → `{task_id: card, op: 'link', fields: {kind: 'waits_on', other: N}}`; `Merge into K` → `{task_id: <the end that is not K>, op: 'merge', fields: {into: K}}`; `is the same task` → merge with K = `defaultKeep(card, N)` (the Canvas row, else the older `created_at`, else the lower id; from `nameOf` / `byId`). `Not related` / `Not the same` → `stageJudge(evidence_id, card, 'dismissed', label)` with the label "not related to Task-<N>" / "not the same as Task-<N>". Toast: `Link staged · decide on the card, then Apply`. Attributes: `data-lsug="<evidence_id>"` with `data-lsug-act="link|step|waits|same|merge|not"`.
  - Nothing else on the card changes (no "Since you last looked", no ripple proposals, no conversation).

- [ ] **Step 1: Implement**; place `linkSuggestionsHtml(items[0])` after the planner `extra` lines.

- [ ] **Step 2: CSS**: `.card .why.ok { color: var(--ok) }`, `.card .why.chg { color: var(--top) }`; `.card .lsug` wraps; phone: its buttons full width, `min-height: 44px`.

- [ ] **Step 3: Run the suite** (all pass).

- [ ] **Step 4: Write and run `DEV/scenarios/s4b-8-review.mjs`** (both devices; `S = seed('--fresh')`; this scenario loads Review).
  - R1: `/review`; walk the cards with Keep (`[data-rv="keep"]`) until the card's `data-id` is one of the seed ids, taking a shot of each seed card: `step_freed` (`Free now: Task-<step_done> is done`, green), `goal` (`1 of 3 steps done` chip in `.sub`), `goal_done` (`All steps are done. Close this, or is there more?`, chip `1 of 1 steps done`), `waits_dropped` (`Task-<dropped_blocker> was dropped. Is this still needed?`), `pair_a` and `pair_b` (a `Suggested link` line `Related to Task-… ?` with the four buttons), `twin_goes` / `twin_kept` (`Same task as …?`, `Merge into <twin_kept>`). Log each card's `.why` text and class. Compare with frame 4's reason line and chip (frame 10 on the phone). Shots `s4b-8-<key>`.
  - R2: on `pair_a`'s card press `waits on it`: the line disappears, the tray reads `Task-<pair_a> now waits on Task-<pair_b>`; press `Discard all`: the line is back.
  - R3: on `pair_a`'s card the planner line (`Planner suggests` … `Accept`) is there; press Accept: the tray holds `rel:<pair_a>:waits_on:<chain_1>` (`Task-<pair_a> now waits on Task-<chain_1>`) and `e:<planner_evidence>` (`suggestion accepted`) — Task 2's Accept branch; log the keys. Press `Not related` on the pair suggestion: `e:<pair_evidence>` staged. Press Keep. Open the tray, untick every entry whose key does not name `pair_a`, `planner_evidence` or `pair_evidence` (the walk staged Keeps on other cards), Apply selected; log the response (no skips) and that `GET /api/tasks/<pair_a>` now has a `waits_on` relation to `chain_1`. Discard all.
  - R4: Review's `Task-N` in a reason text is a link: click it on `step_freed`'s card → `/t/<step_done>` read-only page (phone) / page (desktop); Back returns to Review.
  - `noSideScroll` true. End with `seed('--fresh')`.

- [ ] **Step 5: Look at every screenshot, then commit**

```bash
git add factotum/web/app.js factotum/web/app.css
git commit -m "web: a Review card says its link reason in the server's words (Free now, a task it waited on closed, all steps done, a linked task changed), shows a goal's steps done, and offers a suggested link with one tap (is a step of it / waits on it / is the same task) or Not related"
```

---

### Task 9: The Log in words

Implementer: **sonnet**. UI task: yes.

**Files:**
- Modify: `factotum/web/app.js` — `SOURCE_NAMES`, `changeLines`, `batchLine`, `batchDigest`, `timelineHtml` (the line text).

**Interfaces:**
- Consumes: `linkPredicate` (Task 2), `fieldSaid` (Task 6), the Log's `b.cause` and `c.note`.
- Produces:
  - `logLinkWords(c) -> string|null`: for a `link` change, `Task-<task_id> ` + `linkPredicate(v.kind, v.other, removed)` where `v` is `c.new` (added) or `c.old` (removed); `null` for a row whose `dir` is `in` (the other row of the same relation says it).
  - `SOURCE_NAMES` gains `ripple: 'ripple'`, `ntfy: 'reminder'`, `command: 'you'`, `import: 'import'`.
  - `batchLine(b)`: `link` rows read `now waits on Task-14` etc. (rank 4; `in` rows skipped); when `b.cause && b.cause.kind === 'ripple'`: `Ripple from Task-N: <k> task(s) updated` (k = distinct task ids, N = `cause.from[0]`); another cause with `from`: `after Task-N · ` + the usual line.
  - `batchDigest(b)`: a link row → `logLinkWords(c)`; a change with `note` → its words followed by ` — <note>`.
  - `changeLines(b)` (Changes view): a link row reads `logLinkWords(c)` instead of JSON; `in` rows are left out; a note is shown after the row.

- [ ] **Step 1: Implement.**

- [ ] **Step 2: Run the suite** (all pass).

- [ ] **Step 3: Write and run `DEV/scenarios/s4b-9-log.mjs`** (both devices; `S = seed('--fresh')`).
  - G1: `/log`, Timeline view → shot `s4b-9-timeline`: the seed's batches read `Task-<step_freed> is now part of Task-<goal>` / `now waits on …`, the merge `Task-<merged_away> merged into Task-<merged_into>`, the ripple `Ripple from Task-<step_done>: 1 task updated` with chip `ripple`; no raw `link` or JSON. Open the ripple line: the digest shows `context: …` followed by ` — Task-<step_done> recorded that the chair can serve remotely`.
  - G2: Changes view → shot `s4b-9-changes`: link rows in words, one per relation.
  - `noSideScroll` true. End with `seed('--fresh')`.

- [ ] **Step 4: Look at every screenshot, then commit**

```bash
git add factotum/web/app.js
git commit -m "web: the Log says a link change in words (Task-12 now waits on Task-14), a merge as merged into, a ripple as one line with its cause, and a change's note"
```

---

### Task 10: The page after Done from a reminder says what is now free

Implementer: **sonnet**. UI task: no (server-rendered; tested in pytest).

**Files:**
- Modify: `factotum/relations.py` (new `freed_by`), `factotum/api.py` (`_page`, the `/r/{token}` route's Done branch)
- Test: `tests/test_stage4b.py`

**Interfaces:**
- Consumes: `relations.annotate`, `reminders.act`, `reminders.fire`, `test_core._rem`.
- Produces:
  - `relations.freed_by(db, task_id: int) -> list[dict]`: the open tasks with an active `waits_on` link to `task_id` that now wait on no open task, `[{"id", "name"}]` by id (read through `annotate`).
  - `api._page(title, text, base, undo=False, task_id=None, free: list[dict] | None = None) -> str`: when `free` is non-empty, after the text: `<p style="margin:16px 0 4px;font-weight:600">Free now:</p><ul style="margin:0;padding-left:20px">` and one `<li><a href="{base}/t/{id}" style="color:#2a6">Task-{id}</a> {escaped name}</li>` each, `</ul>`. Without `free` the page is byte-for-byte today's.
  - The Done branch of `/r/{token}` passes `free=relations.freed_by(conn, res["task_id"])`; Snooze and the error pages pass nothing.

- [ ] **Step 1: Write the failing tests** in `tests/test_stage4b.py`

```python
def _done_page(cfg, tmp_path, setup):
    from test_core import _rem
    app = api.create_app(cfg, db_path=str(tmp_path / "a.db"))
    c = app.state.conn
    setup(c)
    batches.apply(c, [{"task_id": 1, "op": "add_reminder", "fields": {"fire_at": "2026-11-03T09:00"}}], "ui", TODAY)
    reminders.fire(c, cfg, datetime(2026, 11, 3, 9, 0, 30), lambda *a, **k: "m1")
    tok = [r["token"] for r in _rem(c, 1) if r["status"] == "sent"][0]
    return TestClient(app).get(f"/r/{tok}", params={"a": "done"})


def test_the_page_after_done_names_what_is_now_free(cfg, tmp_path):
    def setup(c):
        for i, name in ((1, "Get the data"), (2, "Run <the> analysis"), (3, "Write it up"), (4, "Other"), (5, "Closed one")):
            mk(c, i, name=name)
        link(c, 2, 1, "waits_on"); link(c, 3, 1, "waits_on"); link(c, 3, 4, "waits_on"); link(c, 5, 1, "waits_on")
        batches.apply(c, [{"op": "done", "task_id": 5}], "ui", TODAY)
    r = _done_page(cfg, tmp_path, setup)
    assert r.status_code == 200 and "Free now:" in r.text
    assert '/t/2"' in r.text and "Run &lt;the&gt; analysis" in r.text
    assert '/t/3"' not in r.text and '/t/5"' not in r.text and "Open Task-1" in r.text
    assert not re.search(r"\bblocked\b|\bdependenc", r.text, re.I)


def test_the_page_after_done_with_nothing_waiting_is_unchanged(cfg, tmp_path):
    r = _done_page(cfg, tmp_path, lambda c: mk(c, 1, name="Alone"))
    assert r.status_code == 200 and "Free now" not in r.text and "Open Task-1" in r.text


def test_freed_by_lists_only_open_tasks_with_nothing_else_open_to_wait_on(conn):
    for i in range(1, 6):
        mk(conn, i)
    link(conn, 2, 1, "waits_on"); link(conn, 3, 1, "waits_on"); link(conn, 3, 4, "waits_on"); link(conn, 5, 1, "waits_on")
    batches.apply(conn, [{"op": "done", "task_id": 5}], "ui", TODAY)
    batches.apply(conn, [{"op": "done", "task_id": 1}], "ui", TODAY)
    assert relations.freed_by(conn, 1) == [{"id": 2, "name": "Task 2"}]
```

(`conn` is the suite's database fixture from `tests/conftest.py`, the one `tests/test_core.py` uses.)

- [ ] **Step 2: Run them**

Run: `/home/robert/factotum/.venv/bin/pytest tests/test_stage4b.py -q`
Expected: the three new tests FAIL (`freed_by` missing, no "Free now:").

- [ ] **Step 3: Implement** `relations.freed_by`, the `free` parameter of `_page`, and the Done branch.

- [ ] **Step 4: Run the whole suite**

Run: `/home/robert/factotum/.venv/bin/pytest -q`
Expected: all pass (stage 1's `test_a_reminders_open_goes_to_its_task` unchanged).

- [ ] **Step 5: Commit**

```bash
git add factotum/relations.py factotum/api.py tests/test_stage4b.py
git commit -m "api: the page after a reminder's Done lists the tasks that were waiting only on that one as Free now, each a link to its address"
```

---

### Task 11: The stage's documents

Implementer: **sonnet**. UI task: no.

**Files:**
- Modify: `SPEC.md` (§9), `CLAUDE.md`

**Interfaces:**
- Consumes: the names every earlier task produced.

- [ ] **Step 1: `SPEC.md` §9.** Replace "the page follows in stage 4b" (in "Links in the API") with a pointer, and add a paragraph **Links on the page (stage 4b, 2026-11-11)**: badges and their order and looks, one per row on a phone; All / Linked / Ready now (Ready keeps hard-dated waiting tasks); the Linked view's three group kinds; Today's order (pull forward into an earlier group, draw under the blocker, a hard task never moves); the opened task's sections and History (who, what, why, before / after, Undo per batch with "Undo all N changes made together", Earlier), fetched only when opened, opening stamps `neighbours_seen_at`; the task page's pinned Done / Later / More and its trail; a closed task read-only with Reopen on both devices; Review's reason texts, steps chip and link suggestions; tray keys (`rel:<a>:<kind>:<b>`), `expect` on a staged text edit, a Review note as `add_note`; the Log in words; the page after a reminder's Done lists what is free now; a suggestion carries `dir`.

- [ ] **Step 2: `CLAUDE.md`.** Phase status: one sentence for stage 4b (2026-11-11). Where things live, rows: "link badges, the link filter and the Linked view, Today's order" → design §12.1, §12.2, §7.4 → `web/app.js` (`badgesFor`, `linkChipsHtml`, `overlayRel`, `linkView`, `readyRow`, `linkedGroups`, `arrangeToday`); "staging links, notes and outcomes; Undo that skips" → design §11.2, §6 → `web/app.js` (`stageKey`, `stageChange`, `changeWords`, `textExpect`, `suggestionStaged`, `undoBatch`, `undoSaid`); "the opened task: linked tasks, provenance, History, the task page and its trail" → design §9, §12.3 → `web/app.js` (`detailCache`, `loadDetail`, `ensureDetails`, `swapDetail`, `historyHtml`, `historyWords`, `taskPageHtml`, `openPage`), `api.get_one`; "Free now after a reminder's Done" → design §14 → `api._page`, `relations.freed_by`. Conventions, each dated 2026-11-11:
  - **A staged change's tray key is `stageKey`**: a link is `rel:<a>:<kind>:<b>`, never the bare id (that is the task's decision and would be overwritten); a new op needs a branch in `stageKey`, `changeWords` and `stageProposal`, or the tray drops or mislabels it.
  - **Opening a task stamps `neighbours_seen_at`** (`GET /api/tasks/{id}`, every call, paging included): the page fetches it only for an opened row or the task page (`ensureDetails`), never for every row, and clears the row's "updated by" marker itself when it arrives.
  - **History arrives while things are typed**: `loadDetail` swaps the two async containers in place (`swapDetail`) when `typingNow()`; a new async part of the opened task goes in `[data-dlinks]` / `[data-dhist]` or it is lost on arrival.
  - **`.details` is built only for open rows**: code that reads a row's details from the DOM sees only opened rows.
  - **Today may draw a task in a group it has no date for** (`arrangeToday` pulls a task that frees a hard-dated one into that group) and a filter may hide a row: `jumpTo` resets the link filter to All before giving up, and opens the task page when no list holds the id.
  - **The phone trail is `history.state.from`**: a page-to-page jump pushes `{page: true, from}`; nothing keeps a trail in memory.
  - **Badges are `<a class="lb" href="/t/N">`**: the row's click handler ignores `a`; a new control inside `.main` must be an `a`, `button` or `input`, or it opens / selects the row.
  - **With no links nothing is drawn**: the link bar, the legend and the Linked view appear only when `hasLinks()`.

- [ ] **Step 3: Run the suite** (all pass) and check the words: `grep -niE "\bblocked\b|dependenc" SPEC.md CLAUDE.md` shows nothing added by this stage.

- [ ] **Step 4: Commit**

```bash
git add SPEC.md CLAUDE.md
git commit -m "docs: stage 4b, links on the page (badges, the link filter and the Linked view, Today's order, the opened task with History, the task page and its trail, Review's link reasons and suggestions, the Log in words, Free now after Done) and the gotchas learned"
```

---

## Stage check (after Task 11, by the controller)

Run every `s4b-*` scenario again for both devices on a fresh seed, plus `s1-7-task-page.mjs` and `s3-9-own.mjs` (stage 1 and 3 regressions), and `pytest -q`. Then, with `seed_links.py --remove`, open Today, Timeline and Projects at both sizes and confirm they look as before this stage (no bar, no badges, no legend).
