# Stage 7: Suggestions (links that are proposed, not typed) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Links between tasks are mostly proposed by Factotum and confirmed with one tap: three free Python detectors (twins, a shared thread or event, "Task-N" in text) run after the sweeps and at every create, the planner, sync-file and mail-triage may propose links, a task made by hand with only a name is filed by one small run, and Review shows at most a set number of link suggestions a day.

**Architecture:** One new module, `factotum/suggestions.py`, owns every rule about a suggested link (one card per unordered pair, ever; the detectors; the daily cap) and is the only writer of `link_suggestion` evidence besides the import. The three existing runs gain a `links` part in their JSON that Python validates against the rows the run was shown; only a link the owner stated in a recording is written without a tap. A second new module, `factotum/filing.py`, is a no-tools run kind (`filing`) queued at create and run by the scheduler. The page (Part B) draws filing proposals as dashed chips, a "This mail is about Task-N" card and two Settings controls, on top of stage 4b.

**Tech Stack:** Python 3 + SQLite (stdlib `sqlite3`, `re`), FastAPI, pytest (offline, fake runners); vanilla JS in `factotum/web/app.js` (no build step); Playwright scenarios run by the dev harness.

**Spec:** `docs/superpowers/specs/2026-11-10-linked-tasks-design.md` §10 (binding to the word), with §1, §2, §3, §4, §5.4, §6 (merge), §11, §15, §16, §17, §18. Part B also reads the stage 4b plan, `/tmp/claude-1000/-home-robert-factotum/85fa6951-ca9c-416e-9691-1a412f0fadea/scratchpad/plans/2026-11-11-stage-4b-links-page.md` (its function names and tray keys).

## Global Constraints

- Work only in the worktree the controller names (WT) and never touch the main checkout or another worktree. The controller names WT per run: Part A is built in `/home/robert/factotum/.worktrees/suggestions` (branch `suggestions`), Part B in `/home/robert/factotum/.worktrees/linked-tasks` (branch `linked-tasks`). Every path below is relative to WT.
- python is `/home/robert/factotum/.venv/bin/python`, tests `/home/robert/factotum/.venv/bin/pytest`, run from WT.
- Do not install packages.
- NO real Claude runs by any build agent, no scheduler, no sweeps, no sudo. Nothing in a test or a scenario may reach `claude`, Mailbird, Canvas, Slack or Notion; every run is a fake runner. (`python -m factotum file-task … --dry-run` is a real run: only the controller runs it, by hand.)
- Never open `config/factotum.yaml`, `.env` or `/home/robert/factotum/data`.
- Commit rules from CLAUDE.md: small commits on the branch, pytest green at every commit (the WHOLE suite, `/home/robert/factotum/.venv/bin/pytest -q`), never push. Every commit message ends with the line `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` exactly as CLAUDE.md gives it. Message style: `suggestions: what changed and why` (or `planner:`, `sync_file:`, `mail_triage:`, `filing:`, `web:`, `docs:`).
- A suggestion is never applied without a tap, except a link the speaker stated in a recording.
- Nothing from a Slack mention or a stranger's mail may create a link except as a card.
- A dismissed pair is never suggested again.
- A shared recording or a shared person is provenance, never a suggestion.
- Every paid run is off when its key is missing.
- The design's vocabulary (§4): "blocked" and "dependency" never appear in anything the app shows or tells the agent. Words the app shows are "part of", "steps", "waits on", "frees", "same as". The word "blocked" may appear in the owner's text and in the text matcher's patterns (Task 2), never in a `why`, a label, a prompt or the page.
- The detectors never write `relations`; they write `link_suggestion` evidence through `suggestions.suggest_pair` only. `relations.suggest` (stage 4a) stays as it is for the import.
- `db.log_run` and `set_setting` do not commit; a function documented "the caller commits" never commits, and a scheduler branch never returns with a transaction open.
- Text written by strangers goes through bounded patterns: every regex over task text has bounded quantifiers and reads a capped slice.
- `factotum mcp`'s connection is `query_only`: anything `relations.annotate` calls must only read.
- Stage 5 (ripples) is built in parallel and also edits `scheduler.py`, `config.py` and the three YAML files. Keep to this plan's edits there: one method `_suggestions` and one call site in `Scheduler.tick`; config keys added on their own lines with a `# stage 7` comment. Never reformat neighbouring lines.
- Part A touches nothing under `factotum/web/`. Part A is complete and green without Part B.
- Tests: new Python tests go in `tests/test_stage7_detect.py` (Tasks 1–4), `tests/test_stage7_runs.py` (Tasks 5–7) and `tests/test_stage7_filing.py` (Tasks 8–9) (the single `tests/test_stage7.py` the brief names, split up front because it would pass 600 lines). Use `conftest` fixtures `conn`, `cfg` and `stage4_helpers` (`TODAY`, `mk`, `link`, `active_rels`). If an existing test fails only because a create now also writes a `link_suggestion` row, stop and report it; do not weaken a detector to pass it.
- **How UI tasks (Part B) are verified** (there are no JS tests). DEV = `/tmp/claude-1000/-home-robert-factotum/85fa6951-ca9c-416e-9691-1a412f0fadea/scratchpad/dev`.
  - Dev instance: `DEV/serve.sh restart` (http://127.0.0.1:8751, a copy of a real database, no scheduler, the test fixture config). Restart after any Python change; files under `factotum/web/` are served straight from WT.
  - Data: `DEV/seed_suggestions.py` (Task 10) beside stage 4b's `DEV/seed_links.py`; every scenario of this stage starts with `seed7('--fresh')` from `DEV/scenarios/s7-lib.mjs` and reads the ids it returns (never hard-coded ids).
  - Browser: `cd DEV && node pw.mjs scenarios/<name>.mjs --device both`; a scenario is `export default async ({ page, context, shot, base, device, log }) => { ... }`; `shot('name')` saves `DEV/../shots/<name>-<device>.png`. Scenarios are named `s7-<task>-<name>.mjs`. Desktop is 1280x900, phone 390x844 (touch).
  - The implementer runs each scenario for both devices, LOOKS at every screenshot (the Read tool shows images), and reports what was seen, including the `log` lines and the "problems" list. Every phone shot is checked with stage 4b's `noSideScroll(page)` (must log `true`).
  - A scenario never presses Send in Chat, Ask on a Review card, a starter prompt, Refresh, "Search related emails", "Draft reply" or "Ask Claude what else is related" (each starts a real Claude run). It may press Apply / Undo on the dev database and ends with `seed7('--fresh')`.

## Review Focus

Six lines, because the brief named six; each has its test in the task that owns the code.

1. **A course series flooding Review with false twins** (Quiz 5, Discussion 5 and Reading 5 of one week, all Canvas, all due the same night; a "Week 5 stuff" recording): no twin between two Canvas items of different names, and a row with several twin candidates keeps the one it shares a title word with, or none. — Task 1, `test_a_course_series_makes_no_twins_and_an_ambiguous_row_makes_none`.
2. **The same pair suggested by two detectors, or by a model after a detector, or in the other order** (a twin whose text also says "Same as Task-1"): one card per unordered pair, ever. — Task 1, `test_one_pair_gets_one_card_whichever_signal_finds_it_and_never_again_once_judged`; Task 3, `test_the_sweep_writes_each_pair_once_and_a_second_pass_writes_nothing`.
3. **A recording synced again** (the trigger fires twice, or `sync-file` is re-run by hand) after the owner removed its stated link and dismissed its inferred one: nothing comes back. — Task 6, `test_a_recording_synced_again_neither_relinks_what_the_owner_removed_nor_suggests_again`.
4. **A model naming a task it was not shown** (an id from memory, a closed task given as reference only, an invented id): dropped with a reason, never a card or a link. — Task 5 (`…unseen…` assertions), Task 6, Task 7, Task 8.
5. **A hand-made task whose filing run comes back after the owner already set a project**: the project proposal is dropped, the rest is kept, and the task row is byte-for-byte what the owner made. — Task 8, `test_filing_names_only_tasks_it_was_shown_and_never_a_field_set_meanwhile`.
6. **The cap starving a real suggestion forever** (five suggestions the owner keeps skipping): each day offers the never-offered first, then the longest unseen, so every pending suggestion is offered within a few days; a suggestion whose task closed gives its slot up. — Task 4, `test_an_ignored_suggestion_never_keeps_another_out_for_good`.

---

## Decisions this plan makes

- **One card per unordered pair, ever.** `suggestions.why_not` refuses a pair that has any `link_suggestion` row (pending or judged, any kind, either order) or any `relations` row (active, or removed: the owner unlinked it). Design §5.4: "never suggested again, whichever signal finds it next". 4a's `relations.suggest` is kind-level; it stays for the import, and every stage 7 writer goes through `suggestions.suggest_pair`.
- **Twins, tightened.** `relations.twin_pairs` keeps the design's rule and adds: two rows that are BOTH from Canvas pair only when their names are equal after normalising numbers (Canvas re-posted one item), because two Canvas items with two Canvas ids are two items. In `suggestions.twins`, a row with more than one candidate keeps only the candidate it shares a title word with (project words and Canvas verbs left out); still more than one, or none: no card for that row.
- **Shared thread or event:** two to four open tasks with the same `links` row of kind `mail_thread` or `calendar`; five or more on one thread is a mailing list, not a pair. `plaud_note` and `counterpart` are never read by a detector.
- **"Task-N" in text:** `\bTask-(\d{1,7})\b`, case-insensitive, in the task's name and context, first 4000 characters, at most 10 references per task; the kind is guessed from the 60 characters before the reference on the same line, or from a list label (`Sub-tasks:`, `Steps:`) earlier on that line. `#N` and `task 12` are not the form. The `why` is generated ("Task-10's text names Task-20"); the text is never quoted.
- **The cap is per day, in one place.** `links.suggestions_per_review` (Settings, default 5) is the number of link suggestions offered per day. `suggestions.session(conn, today, n)` picks them (the never-offered first, then the longest unseen, then the oldest) and records them in `state.links.session`; the scheduler's detector pass and `GET /api/review` call it. `relations.annotate` filters `rel.suggestions` to that set, so Review, the dashed badges, the Linked view, the brief's counts and the agent's `task_get` agree. Judged ones are not replaced the same day. Suggestions with signal `filing` are exempt (at most two, about a task the owner just made). With no session recorded yet, everything pending shows (a fresh database, and stage 4a/4b's tests).
- **A model's link always stages**, as a `link_suggestion` with its signal (`planner`, `recording`, `filing`); an `unlink` from the planner is a `proposal` card. The one exception: a `part_of` / `waits_on` link the owner stated in a recording rides in the recording's batch, with the sentence as its `why` and the change's note. A stated `same_as` stages (a merge drops a task; it always waits for a tap, as for connectors).
- **sync-file's reference-only list** is the done or dropped tasks of the last 30 days (not merged away), at most 25. Their ids may be the `to` of a `part_of` or `same_as` link and nothing else; `waits_on` a finished task is refused ("a finished task holds nothing up").
- **A re-synced recording** (its `plaud_note` link existed before this pass) never adds a stated link for a pair that has any `relations` row, active or removed.
- **"This mail is about Task-N"** is a `proposal` card on Task-N whose change is a new batch op `follow_thread` (adds the `mail_thread` link, sets `last_seen_at` to the mail's time; undo deletes that `links` row). A mail-suggested task's link is stored on its card and written by the Create endpoint in a `review` batch after the create.
- **Filing** is queued for a create whose Source is "Manual" (`batches.created_source`) with no project, no effort and no task links; it is stored in `state.filing.pending` (at most 50, kept for the day it was made and the next), run by the scheduler at most 3 per tick when `claude.filing.enabled` is on, and dequeued before it runs (a failed run is logged and not retried). Project and effort become `filing` evidence on the task; the link and the twin become `link_suggestion`s with signal `filing`. The API wakes the scheduler after a create.
- **`claude.filing.enabled` is not added to `config.PAID_SWITCHES`**: that tuple also builds `OLD_DEFAULTS` for `factotum effective --as-before`, which would then show a difference that never existed. Its default is False in `config.DEFAULTS` and a test holds it.
- **Detector pass interval:** every 30 minutes (`suggestions.SWEEP_MINUTES`, a constant: it is free and not a matter of taste).

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `factotum/suggestions.py` (new) | pair rules (`why_not`, `suggest_pair`, `live`), detectors (`twins`, `threads`, `refs_in`, `text_refs`, `detect`), writing (`sweep`, `sweep_job`, `at_create`, `after_create`), `pending`, the daily cap (`session`) | 1–4, 9 |
| `factotum/relations.py` | `twin_pairs` both-Canvas rule (1); `shown_ids` and the filter in `annotate` (4) | 1, 4 |
| `factotum/batches.py` | the post-create hook (3); `follow_thread` op and its undo (7) | 3, 7 |
| `factotum/canvas.py`, `factotum/sync_file.py`, `factotum/api.py` | `at_create` after their creates | 3 |
| `factotum/scheduler.py` | `_suggestions` and its one call in `tick` | 3, 9 |
| `factotum/__main__.py` | `sweep links`, `links-check` (3); `file-task` (8) | 3, 8 |
| `factotum/api.py` | session before Review (4); card links, Create links (7); `filing` left out of Review's evidence (8); `filing` chips, wake on create (9) | 3, 4, 7, 8, 9 |
| `factotum/planner.py`, `prompts/planner-morning.md` | link candidates in, `link` / `unlink` / `merge` out (5); `filing` excluded (8) | 5, 8 |
| `factotum/sync_file.py`, `prompts/sync-file.md` | the closed list, `links` in the JSON (6) | 6 |
| `factotum/mail_triage.py`, `prompts/mail-triage.md` | `link` on a suggestion, `about` cards (7) | 7 |
| `factotum/filing.py` (new), `prompts/filing.md` (new) | the filing run (8), its queue and chips (9) | 8, 9 |
| `factotum/config.py`, `config/factotum.example.yaml`, `config/factotum.starter.yaml`, `tests/fixtures/factotum.fixture.yaml` | `links.suggestions_per_review` (4); `claude.filing.enabled` (9); the `filing` model, budget, timeout (8) | 4, 8, 9 |
| `tests/test_stage7_detect.py`, `tests/test_stage7_runs.py`, `tests/test_stage7_filing.py` (new) | Part A tests | 1–9 |
| `factotum/web/app.js`, `factotum/web/app.css` | filing chips (10), the mail cards (11), Settings (12) | 10–12 |
| `SPEC.md`, `CLAUDE.md`, `docs/SETUP.md` | the stage's documents | 13 |
| `DEV/seed_suggestions.py`, `DEV/scenarios/s7-*.mjs` (outside the repository) | seed and rendered checks | 10–12 |

---

# Part A — Python, prompts, CLI (built in the `suggestions` worktree)

### Task 1: One card per pair, and twins that a course series cannot flood

Implementer: **opus**. UI task: no. Part A.

**Files:**
- Create: `factotum/suggestions.py`
- Modify: `factotum/relations.py` (`twin_pairs`, plus a helper `_twin_name`)
- Create: `tests/test_stage7_detect.py`

**Interfaces:**
- Consumes: `relations.suggest`, `relations.check`, `relations.merge_target_problem`, `relations.twin_pairs`, `relations.default_survivor`, `relations.EVIDENCE_KIND`, `relations.Db`.
- Produces (in `factotum/suggestions.py`):
  - `why_not(db, kind: str, a: int, b: int) -> str | None`: None when a suggestion `a kind b` may be written; otherwise, checked in this order and worded exactly: `"the same task"` (a == b); `"no Task-<x>"` (a missing task, a first); `"already linked"` (an active `relations` row between them, either direction, any kind); `"linked before"` (a removed one); `"suggested before"` (any `link_suggestion` row for the unordered pair, pending or judged, any kind); then the rule for the kind: `same_as` → `"Task-<a> is not open"` or `relations.merge_target_problem(db, b)`; `part_of` / `waits_on` → `"Task-<a> is not open"` or `relations.check(db, a, b, kind)`; `any` → `"Task-<x> is not open"` for the first end that is not open; an unknown kind → `"kind must be one of same_as, part_of, waits_on, any"`.
  - `suggest_pair(db, kind: str, a: int, b: int, why: str, signal: str) -> int | None`: `relations.suggest(...)` when `why_not` is None, else None. For `same_as`, `a` goes and `b` is kept. The caller commits.
  - `live(db, ref: dict) -> bool`: a pending suggestion (`ref` with `kind`, `a`, `b`) is still worth showing: both tasks exist, no active relation joins them, and the kind's rule above passes now.
  - `twins(db, ids: set[int] | None = None) -> list[dict]`: `[{"kind": "same_as", "a": goes, "b": keep, "why": <twin_pairs' why>, "signal": "twin"}]` over the open rows, only pairs touching `ids` when given, ordered by (min id, max id).
  - `TITLE_STOP: frozenset[str]` (the generic words below).
- Produces (in `relations.py`): `twin_pairs` unchanged in signature; two rows both from Canvas pair only when `_twin_name(x) == _twin_name(y)`.

- [ ] **Step 1: Write the failing tests** in `tests/test_stage7_detect.py`

```python
"""Stage 7, suggestions (design §10.1): one card per pair, the detectors, the scheduler job, the Review cap."""
import json
import time
from datetime import date, datetime

from factotum import batches, canvas, relations, suggestions, sync_file
from factotum.db import add_link, connect, get_settings, init_db, one, rows
from stage4_helpers import TODAY, active_rels, link, mk


def _sugg(conn):
    """Every link_suggestion row, oldest first: its ref plus evidence_id and judged."""
    return [dict(json.loads(r["ref"]), evidence_id=r["id"], judged=r["judged_at"] is not None)
            for r in rows(conn, "SELECT id, ref, judged_at FROM evidence WHERE kind = 'link_suggestion' ORDER BY id")]


def test_twins_need_the_same_number_a_canvas_row_and_36_hours(conn):
    mk(conn, 1, name="Submit COMPE 510 Assignment 03", source="Canvas", due="2026-11-10T23:59")
    mk(conn, 2, name="Assignment 3 writeup", source="Plaud", due="2026-11-11T08:00", created_at="2026-10-20T00:00:00+00:00")
    mk(conn, 3, name="Assignment 4 writeup", source="Plaud", due="2026-11-10")                      # a sibling: another number
    mk(conn, 4, name="Assignment 3 slides", source="Plaud", due="2026-11-14")                       # three days later
    mk(conn, 5, name="Assignment 3 notes", source="Plaud", due="2026-11-10", project="Personal")    # another project
    assert [(c["kind"], c["a"], c["b"], c["signal"]) for c in suggestions.twins(conn)] == [("same_as", 2, 1, "twin")]
    assert suggestions.twins(conn, {3}) == []


def test_a_course_series_makes_no_twins_and_an_ambiguous_row_makes_none(conn):
    for i, name in enumerate(["Take COMPE 510 Quiz 5", "Post in COMPE 510 Discussion 5", "Submit COMPE 510 Reading Response 5",
                              "Take COMPE 510 Quiz 4", "Take COMPE 510 Quiz 6"], start=1):
        mk(conn, i, name=name, source="Canvas", due="2026-11-10T23:59")
    assert suggestions.twins(conn) == []                                        # three Canvas items of one week are three items
    mk(conn, 10, name="Finish the discussion post 5", source="Plaud", due="2026-11-10")
    mk(conn, 11, name="Week 5 stuff", source="Plaud", due="2026-11-10")
    assert [(c["a"], c["b"]) for c in suggestions.twins(conn)] == [(10, 2)]     # the one it shares a word with; 11 is ambiguous: none
    mk(conn, 12, name="Take COMPE 510 Quiz 05", source="Canvas", due="2026-11-11T09:00", created_at="2026-10-02T00:00:00+00:00")
    assert (12, 1) in [(c["a"], c["b"]) for c in suggestions.twins(conn)]       # Canvas posted Quiz 5 twice: the older is kept


def test_one_pair_gets_one_card_whichever_signal_finds_it_and_never_again_once_judged(conn):
    mk(conn, 1); mk(conn, 2); mk(conn, 3)
    e = suggestions.suggest_pair(conn, "waits_on", 1, 2, "x", "text")
    assert e and suggestions.suggest_pair(conn, "any", 2, 1, "y", "thread") is None           # pending: any kind, either order
    assert suggestions.why_not(conn, "same_as", 1, 2) == "suggested before"
    batches.apply(conn, [{"task_id": 1, "op": "judge_evidence", "fields": {"evidence_id": e, "verdict": "dismissed"}}], "review", TODAY)
    assert suggestions.suggest_pair(conn, "same_as", 2, 1, "z", "twin") is None               # dismissed: whichever signal finds it next
    link(conn, 1, 3, "part_of")
    assert suggestions.why_not(conn, "waits_on", 3, 1) == "already linked"
    batches.apply(conn, [{"op": "unlink", "task_id": 1, "fields": {"kind": "part_of", "other": 3}}], "ui", TODAY)
    assert suggestions.why_not(conn, "any", 1, 3) == "linked before"                          # the owner unlinked it: decided
    assert suggestions.why_not(conn, "any", 1, 1) == "the same task" and suggestions.why_not(conn, "any", 1, 99) == "no Task-99"
    mk(conn, 4, status="done", completed_at="2026-10-30"); mk(conn, 5)
    assert suggestions.why_not(conn, "waits_on", 5, 4) == "Task-4 is already done"
    assert suggestions.why_not(conn, "any", 5, 4) == "Task-4 is not open"
    assert suggestions.why_not(conn, "same_as", 5, 4) is None                                 # a duplicate may be merged into a finished task
    assert suggestions.live(conn, {"kind": "any", "a": 1, "b": 2}) and not suggestions.live(conn, {"kind": "any", "a": 5, "b": 4})
```

- [ ] **Step 2: Run them**

Run: `/home/robert/factotum/.venv/bin/pytest tests/test_stage7_detect.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'factotum.suggestions'`).

- [ ] **Step 3: `relations.twin_pairs` and `_twin_name(name) -> str`**: `_twin_name` is the name (first 300 characters) casefolded, every digit run replaced by its integer value (`"Quiz 05"` → `"quiz 5"`), whitespace collapsed. In `twin_pairs`, after the Canvas check, skip a pair when both sources are `"Canvas"` and the two `_twin_name`s differ. Update the docstring ("two Canvas rows pair only when their names are the same item's"). 4a's `test_twin_pairs_counts_a_date_only_due_as_midnight_and_needs_canvas` still passes (its both-Canvas pair has equal names).

- [ ] **Step 4: Write `factotum/suggestions.py`** with the module docstring (what it owns, that it never writes `relations`, that nothing here commits except `sweep_job` and `after_create`), the functions under Interfaces, and the twin disambiguation:

```
title_words(t) = { w for w in re.findall(r"[a-z]{3,}", casefold(name[:200])) }
                 − { words of t["project"], same pattern }  − TITLE_STOP
TITLE_STOP = {"take", "post", "submit", "the", "and", "for", "due", "with", "from", "your",
              "finish", "complete", "prep", "work", "task", "this", "that"}
pairs = relations.twin_pairs(open rows with id, name, project, status, due, source, created_at)
cands[row] = the pairs that row is in
a pair (x, y) is kept when, for each of x and y that has more than one pair in cands, it is that
row's ONLY pair whose other row shares a title word with it.
```

- [ ] **Step 5: Run the tests**

Run: `/home/robert/factotum/.venv/bin/pytest tests/test_stage7_detect.py tests/test_stage4_agent.py -q`
Expected: PASS.

- [ ] **Step 6: Run the whole suite** — `/home/robert/factotum/.venv/bin/pytest -q`, all pass.

- [ ] **Step 7: Commit**

```bash
git add factotum/suggestions.py factotum/relations.py tests/test_stage7_detect.py
git commit -m "suggestions: one card per unordered pair, ever (pending, judged, linked or unlinked before: never again, whichever signal finds it); twins as the design says, except that two Canvas items pair only when their names are the same item's, and a row with several candidates keeps the one it shares a title word with, or none (a course series cannot flood Review)" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: A shared thread or event, and "Task-N" in text

Implementer: **opus**. UI task: no. Part A.

**Files:**
- Modify: `factotum/suggestions.py`
- Modify: `tests/test_stage7_detect.py`

**Interfaces:**
- Consumes: Task 1's `why_not`, `twins`.
- Produces:
  - `threads(db) -> list[dict]`: `[{"kind": "any", "a": min, "b": max, "why": W, "signal": "thread"}]`, W = `"both follow the same mail thread"` (kind `mail_thread`) or `"both are on the same calendar event"` (kind `calendar`); groups of 2 to `THREAD_GROUP_MAX = 4` open tasks sharing one `(kind, ref)`; every pair of a group; ordered by (a, b).
  - `refs_in(text: str, self_id: int) -> list[tuple[str, int]]`: pure; `(word, N)` per reference in order of appearance, word one of `"same_as"` (this task goes into N), `"part_of"` (this is a step of N), `"goal_of"` (N is a step of this), `"waits_on"` (this waits on N), `"frees"` (N waits on this), `"any"`. Reads `text[:TEXT_CAP]` (`TEXT_CAP = 4000`), at most `MAX_REFS = 10`, skips `self_id` and repeats of one N (the first wins).
  - `text_refs(db, ids: set[int] | None = None) -> list[dict]`: over open tasks (only `ids` when given), the text is `name + "\n" + context`; each `(word, N)` becomes `{"kind", "a", "b", "why": "Task-<T>'s text names Task-<N>", "signal": "text"}` with `same_as` → (same_as, T, N), `part_of` → (part_of, T, N), `goal_of` → (part_of, N, T), `waits_on` → (waits_on, T, N), `frees` → (waits_on, N, T), `any` → (any, min, max); kept only when `why_not`'s rule part passes (a missing task, a closed one where the kind needs it open, a rule refusal: dropped; never turned into `any`).
  - `detect(db, ids: set[int] | None = None) -> list[dict]` = `twins(db, ids) + text_refs(db, ids)` + (`threads(db)` only when `ids` is None), in that order.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_stage7_detect.py`)

```python
def test_two_tasks_on_one_thread_or_event_are_a_pair_but_a_recording_or_a_person_is_not(conn):
    for i in range(1, 10):
        mk(conn, i)
    add_link(conn, 1, "mail_thread", "conv-a"); add_link(conn, 2, "mail_thread", "conv-a")
    add_link(conn, 3, "calendar", "ev-1"); add_link(conn, 4, "calendar", "ev-1")
    add_link(conn, 5, "plaud_note", "of_abc"); add_link(conn, 6, "plaud_note", "of_abc")              # a shared recording: provenance
    conn.execute("UPDATE tasks SET counterpart = 'prof@example.edu' WHERE id IN (7, 8)")                  # a shared person: provenance
    add_link(conn, 9, "mail_thread", "conv-a"); conn.execute("UPDATE tasks SET status = 'done' WHERE id = 9")   # closed: not a pair
    conn.commit()
    assert [(c["a"], c["b"], c["kind"], c["why"]) for c in suggestions.threads(conn)] == [
        (1, 2, "any", "both follow the same mail thread"), (3, 4, "any", "both are on the same calendar event")]
    for i in range(20, 25):
        mk(conn, i); add_link(conn, i, "mail_thread", "conv-list")
    conn.commit()
    assert all(c["a"] < 20 for c in suggestions.threads(conn))                                           # five on one thread: a list, not a pair


CASES = [
    ("Merged into Task-20 on Oct 3", [("same_as", 20)]),
    ("Duplicate of Task-20.", [("same_as", 20)]),
    ("Do this after Task-20 is in.", [("waits_on", 20)]),
    ("Blocked on Task-20 (the chair).", [("waits_on", 20)]),
    ("Waiting on Task-20 first", [("waits_on", 20)]),
    ("This unlocks Task-20.", [("frees", 20)]),
    ("Needed before Task-20.", [("frees", 20)]),
    ("Sub-task of Task-20", [("part_of", 20)]),
    ("Step 2 of Task-20", [("part_of", 20)]),
    ("Part of task-20", [("part_of", 20)]),
    ("Sub-tasks: Task-20, Task-21 (the form)", [("goal_of", 20), ("goal_of", 21)]),
    ("See Task-20 for the numbers", [("any", 20)]),
    ("Like #20, and task 20, and Task-10", []),                                                      # "#N" is noise, "task 20" is not the form, Task-10 is itself
    ("After Task-20. See Task-20 again.", [("waits_on", 20)]),                                       # one entry per task, the first wins
]


def test_task_n_in_text_is_read_with_its_kind_guessed_from_the_words_nearby():
    for text, want in CASES:
        assert suggestions.refs_in(text, 10) == want, text


def test_text_references_become_cards_only_for_tasks_that_exist_and_obey_the_link_rules(conn):
    mk(conn, 10, context="After Task-20.\nMerged into Task-30.\nSub-task of Task-40.\nSee Task-99.\nBefore Task-50.")
    mk(conn, 20); mk(conn, 30, status="done", completed_at="2026-10-30"); mk(conn, 40); mk(conn, 41)
    mk(conn, 50, status="dropped", completed_at="2026-10-30")
    link(conn, 40, 41, "part_of")                                                                    # 40 is a step: it cannot have steps
    got = [(c["kind"], c["a"], c["b"], c["why"], c["signal"]) for c in suggestions.text_refs(conn)]
    assert got == [("waits_on", 10, 20, "Task-10's text names Task-20", "text"), ("same_as", 10, 30, "Task-10's text names Task-30", "text")]
    assert not [c for c in suggestions.text_refs(conn) if "blocked" in c["why"].lower()]


def test_hostile_text_is_read_within_bounds():
    huge = ("after " * 50000) + ("Task-20 " * 50000) + ("Sub-tasks: " + "Task-21, " * 20000)
    t0 = time.monotonic()
    refs = suggestions.refs_in(huge, 10)
    assert time.monotonic() - t0 < 1.0 and len(refs) <= suggestions.MAX_REFS
```

- [ ] **Step 2: Run them** — `/home/robert/factotum/.venv/bin/pytest tests/test_stage7_detect.py -q`; Expected: the four new tests FAIL (`AttributeError`).

- [ ] **Step 3: Implement `threads`, `refs_in`, `text_refs`, `detect`.** `refs_in` algorithm (every quantifier bounded; all matching on the casefolded line):

```
text = text[:TEXT_CAP]; out = []; seen = {self_id}
for line in text.split("\n")[:200]:
    line = line[:600]
    label_end = end of the first match of r"\b(?:sub-?tasks?|steps)\s{0,2}:" in line, or None
    for m in re.finditer(r"\btask-(\d{1,7})\b", line, re.I):
        n = int(m.group(1)); if n in seen: continue; seen.add(n)
        before = line[max(0, m.start() - 60): m.start()]
        word = the cue in CUES whose LAST match in `before` ends nearest to m.start(); if none:
               "goal_of" when label_end is not None and label_end <= m.start(), else "any"
        out.append((word, n)); stop at MAX_REFS
CUES (patterns end at the reference, \s{0,3} between words):
  same_as : merged into | merged with | duplicate of | dupe of | same as
  part_of : part of | sub-?task of | step \d{0,3} ?(of|in|for) | step (of|in|for) | under
  waits_on: after | waits? on | waiting (on|for) | blocked (on|by) | depends on | needs | requires | until | once
  frees   : before | unlocks? | frees | unblocks? | enables?
```

  Each cue regex is anchored to the end of `before` allowing only `[\s:,(-]{0,6}` between the cue and the reference, so "after the meeting, see Task-20" reads as `any`. Order of checking is irrelevant: the nearest cue wins; "blocked on" is checked as a whole phrase so "blocks" never wins over it.

- [ ] **Step 4: Run the tests** — `/home/robert/factotum/.venv/bin/pytest tests/test_stage7_detect.py -q`; Expected: PASS.

- [ ] **Step 5: Run the whole suite**, all pass.

- [ ] **Step 6: Commit**

```bash
git add factotum/suggestions.py tests/test_stage7_detect.py
git commit -m "suggestions: two to four open tasks on one mail thread or calendar event are a pair (never a shared recording or person); Task-N in a task's text is a suggestion whose kind is guessed from the words just before it, read through bounded patterns, never quoting the text" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: The detector pass after the sweeps and at every create; `sweep links` and `links-check`

Implementer: **sonnet**. UI task: no. Part A.

**Files:**
- Modify: `factotum/suggestions.py`, `factotum/batches.py` (`apply`), `factotum/canvas.py` (`sweep`), `factotum/sync_file.py` (`apply_result`), `factotum/api.py` (`create_suggestion`), `factotum/scheduler.py`, `factotum/__main__.py`
- Modify: `tests/test_stage7_detect.py`

**Interfaces:**
- Consumes: `detect`, `suggest_pair` (Tasks 1–2).
- Produces:
  - `SWEEP_MINUTES = 30`.
  - `sweep(db, today: date) -> dict`: writes every `detect(db)` candidate through `suggest_pair`; returns `{"written": {signal: n}}` (only signals that wrote), `"ids": [evidence ids]`; the caller commits.
  - `sweep_job(conn, cfg, now: datetime) -> str | None`: `sweep`, commit, and return `"<n> suggested: twin 1, text 2"` (signals in the order twin, text, thread, only non-zero) or None when nothing was written. (Task 4 adds the session call here.)
  - `at_create(db, ids: list[int]) -> list[int]`: `detect(db, set(ids))` written through `suggest_pair`; returns the evidence ids; the caller commits.
  - `after_create(conn, created: list[int], source: str) -> None`: `at_create`, commit; never raises (any exception: `conn.rollback()`, one line to stderr `factotum: suggestions after create failed: <Type>: <msg>`). Task 9 adds the filing queue here.
  - `Scheduler._suggestions(self, ecfg: dict, now: datetime, st: dict, did: list[str]) -> None`, called once in `tick`, right after the `slack_sweep` block: `self._every("link_suggestions", suggestions.SWEEP_MINUTES, now, st, did, lambda: suggestions.sweep_job(self.conn, ecfg, now))`.
  - CLI: `python -m factotum sweep links` (the `which` choices gain `links`, mapped to the `suggestions` module) and `python -m factotum links-check` (read-only: one line per `detect` candidate, `f"{signal:<7} Task-{a} → Task-{b}  {kind words}  {why}  {why_not or 'would write'}"`, then a total line `twin <n> (<w> would write) · text … · thread …`). Kind words: `same as`, `part of`, `waits on`, `linked to` (for `any`).

- [ ] **Step 1: Write the failing tests** (append)

```python
def _item(key, name, due):
    return {"key": key, "type": "assignment", "name": name, "title": name, "project": "COMPE 510", "area": "Academics", "context": "",
            "due": due, "effort": "M", "state": None, "dismissed": False, "url": None, "points": None}


def test_the_sweep_writes_each_pair_once_and_a_second_pass_writes_nothing(conn):
    mk(conn, 1, name="Take COMPE 510 Quiz 7", source="Canvas", due="2026-11-10T23:59")
    mk(conn, 2, name="Quiz 7 prep", source="Plaud", due="2026-11-10", context="Same as Task-1, the Canvas one.")
    mk(conn, 3); mk(conn, 4); add_link(conn, 3, "mail_thread", "c"); add_link(conn, 4, "mail_thread", "c"); conn.commit()
    out = suggestions.sweep(conn, TODAY)
    assert out["written"] == {"twin": 1, "thread": 1}
    assert [(s["a"], s["b"], s["signal"]) for s in _sugg(conn)] == [(2, 1, "twin"), (3, 4, "thread")]
    assert suggestions.sweep(conn, TODAY)["written"] == {} and active_rels(conn) == set()          # cards only, never a link


def test_a_create_from_a_batch_canvas_or_a_recording_is_checked_at_once(conn, cfg):
    mk(conn, 1, name="Submit COMPE 510 Assignment 5", source="Canvas", due="2026-11-08T23:59")
    res = batches.apply(conn, [{"op": "create", "fields": {"name": "Assignment 5 report", "project": "COMPE 510", "due": "2026-11-08"}}], "chat", TODAY)
    assert [(s["a"], s["b"], s["signal"]) for s in _sugg(conn)] == [(res["created"][0], 1, "twin")]
    mk(conn, 2, name="Assignment 6 notes", source="Plaud", due="2026-11-15")
    canvas.sweep(conn, cfg, [_item("canvas:assignment:66", "Submit COMPE 510 Assignment 6", "2026-11-15T23:59")], TODAY)
    new = one(conn, "SELECT id FROM tasks WHERE source_key = 'canvas:assignment:66'")["id"]
    assert (2, new, "twin") in [(s["a"], s["b"], s["signal"]) for s in _sugg(conn)]
    data = {"tasks": [{"op": "create", "name": "Draft the dataset request", "project": "COMPE 510", "due": None, "context": "Do this after Task-1.",
                       "source_key": "plaud:of_s7:1"}], "instructions": [], "reminders": [], "calendar": [], "unresolved": []}
    r = sync_file.apply_result(conn, cfg, {"id": "of_s7", "name": "Lab sync", "start_utc": "2026-11-02T17:00:00Z"}, data, TODAY, calendar=None)
    assert ("waits_on", r["created"][0], 1, "text") in [(s["kind"], s["a"], s["b"], s["signal"]) for s in _sugg(conn)]


def test_the_scheduler_runs_the_detectors_after_the_sweeps_every_half_hour(conn, cfg):
    from factotum.scheduler import Scheduler
    mk(conn, 1, name="Take COMPE 510 Quiz 7", source="Canvas", due="2026-11-10T23:59"); mk(conn, 2, name="Quiz 7 prep", source="Plaud", due="2026-11-10")
    s = Scheduler(cfg, conn, push=lambda *a, **k: None, mailer=None)
    assert "link_suggestions: 1 suggested: twin 1" in s.tick(datetime(2026, 11, 2, 10, 0))
    assert not [d for d in s.tick(datetime(2026, 11, 2, 10, 10)) if d.startswith("link_suggestions")]
    assert one(conn, "SELECT summary FROM runs WHERE kind = 'link_suggestions'")["summary"] == "1 suggested: twin 1"
    assert not conn.in_transaction


def test_links_check_reads_and_sweep_links_writes(conn, cfg, tmp_path, monkeypatch, capsys):
    from conftest import FIXTURE
    from factotum import __main__ as cli
    path = tmp_path / "cli.db"
    c = connect(path); init_db(c)
    mk(c, 1, name="Take COMPE 510 Quiz 7", source="Canvas", due="2026-11-10T23:59"); mk(c, 2, name="Quiz 7 prep", source="Plaud", due="2026-11-10")
    monkeypatch.setenv("FACTOTUM_CONFIG", str(FIXTURE)); monkeypatch.setenv("FACTOTUM_DB", str(path))
    capsys.readouterr()
    cli.main(["links-check"])
    out = capsys.readouterr().out
    assert "twin    Task-2 → Task-1  same as" in out and "would write" in out and _sugg(c) == []
    cli.main(["sweep", "links"])
    assert [(s["a"], s["b"]) for s in _sugg(c)] == [(2, 1)]
```

- [ ] **Step 2: Run them** — Expected: FAIL (`AttributeError: module 'factotum.suggestions' has no attribute 'sweep'`).

- [ ] **Step 3: Implement** the functions under Interfaces, then the hooks:
  - `batches.apply`: after `conn.commit()` and before `return`, `if created: from . import suggestions; suggestions.after_create(conn, created, source)` (a lazy import: `suggestions` must not be imported at the top of `batches`).
  - `canvas.sweep`: `if created: suggestions.at_create(conn, created)` just before its `conn.commit()`.
  - `sync_file.apply_result`: `if created: suggestions.at_create(conn, created)` just before its final `conn.commit()`.
  - `api.create_suggestion`: `suggestions.at_create(conn, [tid])` just before its `conn.commit()`.
  - `Scheduler._suggestions` with a docstring ("stage 7: the link detectors after the sweeps (free); filing runs join in Task 9") and its call in `tick` right after the `slack_sweep` `if` block.
  - `__main__`: `links` in `sweep`'s choices and the module map; the `links-check` subparser (help: "what the link detectors would suggest now; read-only") and branch. Add both to the module docstring's command list.

- [ ] **Step 4: Run the tests** — `/home/robert/factotum/.venv/bin/pytest tests/test_stage7_detect.py -q`; Expected: PASS.

- [ ] **Step 5: Run the whole suite**, all pass (see Global Constraints for a test that fails only because a create now writes a card).

- [ ] **Step 6: Commit**

```bash
git add factotum/suggestions.py factotum/batches.py factotum/canvas.py factotum/sync_file.py factotum/api.py factotum/scheduler.py factotum/__main__.py tests/test_stage7_detect.py
git commit -m "suggestions: the detectors run every half hour after the sweeps and at once after every create (a batch, Canvas, a recording, a mail card's Create), writing cards only; sweep links writes, links-check says what would be written and why not" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: At most N link suggestions a day, everywhere they show

Implementer: **opus**. UI task: no. Part A.

**Files:**
- Modify: `factotum/suggestions.py` (`session`, `pending`, `sweep_job`), `factotum/relations.py` (`shown_ids`, `annotate`), `factotum/api.py` (`review`), `factotum/config.py`, `config/factotum.example.yaml`, `config/factotum.starter.yaml`, `tests/fixtures/factotum.fixture.yaml`
- Modify: `tests/test_stage7_detect.py`

**Interfaces:**
- Consumes: `live` (Task 1), `sweep_job` (Task 3).
- Produces:
  - `SESSION_KEY = "state.links.session"` holding `{"day": "YYYY-MM-DD", "ids": [evidence ids offered that day, judged ones included]}`; `OFFERED_KEY = "state.links.offered"` holding `{"<evidence id>": "YYYY-MM-DD"}` (the last day each was offered; pruned to pending ids on every call).
  - `pending(db) -> list[dict]`: every unjudged `link_suggestion` as its ref plus `evidence_id`, oldest first (a ref that does not parse is left out).
  - `session(conn, today: date, n: int) -> list[int]`: the ids to show today, in selection order; writes both keys; the caller commits. Candidates: `pending` rows that are `live` and whose signal is not `filing`. Same day as the stored session: the stored ids still pending and live, plus new candidates while `len(stored ids) < n` (judged ones are not replaced). Another day: up to `n` candidates. Candidates are taken by `(offered day or "", evidence_id)`: never offered first, then the longest unseen, then the oldest. `n <= 0` → `[]`.
  - `relations.shown_ids(db) -> set[int] | None`: the stored session's `ids`, or None when no session was ever recorded. Read-only.
  - `relations.annotate`: after `sugg = pending_suggestions(db, set(ids))`, when `shown_ids(db)` is not None keep only entries whose `evidence_id` is in it or whose `signal == "filing"`.
  - `sweep_job` calls `session(conn, now.date(), int(C.value(cfg, "links.suggestions_per_review") or 0))` before its commit, whether or not anything was written.
  - `api.review`: before `decorate_all`, `suggestions.session(conn, today, int(C.value(ecfg, "links.suggestions_per_review") or 0))` and `conn.commit()`.
  - Config: `"links.suggestions_per_review"` joins `SETTINGS_KEYS` (on its own line at the end of the list, `# stage 7`) and `DEFAULTS` (`5`). In the example, a new top-level block just above `claude:`:

```yaml
links:                          # stage 7: suggested links between tasks (design §10)
  suggestions_per_review: 5     # Settings → Review and lifecycle. Taste: at most this many suggested links are offered a day (Review and the dashed badges); the rest wait. 0 = none
```

    The fixture and the starter carry `links: { suggestions_per_review: 5 }` in the same place.

- [ ] **Step 1: Write the failing tests** (append)

```python
def _many(conn, n):
    """n `any` suggestions on n separate pairs (100, 101), (102, 103), …; their ids, oldest first."""
    ids = []
    for i in range(n):
        a, b = 100 + 2 * i, 101 + 2 * i
        mk(conn, a); mk(conn, b)
        ids.append(relations.suggest(conn, "any", a, b, "x", "thread"))
    conn.commit()
    return ids


def test_a_day_offers_at_most_n_link_suggestions_and_keeps_the_rest(conn):
    ids = _many(conn, 8)
    d1 = date(2026, 11, 2)
    assert suggestions.session(conn, d1, 5) == ids[:5] and relations.shown_ids(conn) == set(ids[:5])
    batches.apply(conn, [{"task_id": 100, "op": "judge_evidence", "fields": {"evidence_id": ids[0], "verdict": "dismissed"}}], "review", d1)
    assert suggestions.session(conn, d1, 5) == ids[1:5]                                      # the same day: no top-up
    assert set(suggestions.session(conn, date(2026, 11, 3), 5)) == {ids[5], ids[6], ids[7], ids[1], ids[2]}
    assert suggestions.session(conn, date(2026, 11, 4), 0) == []


def test_an_ignored_suggestion_never_keeps_another_out_for_good(conn):
    ids = _many(conn, 7)
    seen = set()
    for d in (2, 3):
        seen |= set(suggestions.session(conn, date(2026, 11, d), 5))
    assert seen == set(ids)


def test_a_suggestion_whose_task_closed_gives_its_slot_up(conn):
    ids = _many(conn, 2)
    batches.apply(conn, [{"op": "done", "task_id": 100}], "ui", TODAY)
    assert suggestions.session(conn, date(2026, 11, 2), 5) == [ids[1]]


def test_review_the_badges_and_the_agent_see_the_same_days_suggestions(cfg, tmp_path):
    from fastapi.testclient import TestClient
    from factotum import api
    path = tmp_path / "t.db"
    conn = connect(path); init_db(conn)
    ids = _many(conn, 6)
    mk(conn, 300); mk(conn, 301)
    fe = relations.suggest(conn, "part_of", 300, 301, "a piece of it", "filing"); conn.commit()
    client = TestClient(api.create_app(cfg, db_path=str(path)))
    items = client.get("/api/review").json()["items"]
    shown = {s["evidence_id"] for i in items for s in i["link_suggestions"]}
    assert shown == set(ids[:5]) | {fe}                                                      # five a day, filing ones besides
    listed = {s["evidence_id"] for t in client.get("/api/tasks").json()["tasks"] for s in (t.get("rel") or {}).get("suggestions", [])}
    assert listed == shown
    assert not [i for i in items if i["task"]["id"] in (110, 111) and "link_suggested" in i["reasons"]]   # pair six waits for another day


def test_the_suggestions_number_is_a_settings_key_with_one_default_everywhere():
    import yaml
    from factotum import config as C
    from conftest import FIXTURE
    assert "links.suggestions_per_review" in C.SETTINGS_KEYS and C.DEFAULTS["links.suggestions_per_review"] == 5
    for p in (C.EXAMPLE_CONFIG, C.STARTER_CONFIG, FIXTURE):
        assert C.get(yaml.safe_load(p.read_text()), "links.suggestions_per_review") == 5, p
```

- [ ] **Step 2: Run them** — Expected: FAIL (`AttributeError: … 'session'`).

- [ ] **Step 3: Implement** the Interfaces above; extend the Task 3 scheduler test's expectations only by adding `assert get_settings(conn)["state.links.session"]["ids"]` after the first tick (one id).

- [ ] **Step 4: Run the tests** — `/home/robert/factotum/.venv/bin/pytest tests/test_stage7_detect.py tests/test_stage3.py tests/test_stage4_api.py -q`; Expected: PASS (stage 3's `_code_defaults` test included: read the key with `C.value`, never with a literal default). A stage 4a test that loads Review and then adds more than five suggestions the same day now meets the cap: report it rather than raising the cap in code.

- [ ] **Step 5: Run the whole suite**, all pass.

- [ ] **Step 6: Commit**

```bash
git add factotum/suggestions.py factotum/relations.py factotum/api.py factotum/config.py config/factotum.example.yaml config/factotum.starter.yaml tests/fixtures/factotum.fixture.yaml tests/test_stage7_detect.py
git commit -m "suggestions: at most links.suggestions_per_review (default 5) suggested links are offered a day, the never-offered first, then the longest unseen, so a skipped card never keeps another out for good; the choice is made once (the detector pass, a Review load) and read in relations.annotate, so Review, the badges, the brief and the agent agree; a filing suggestion always shows" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: The planner proposes links, and they always wait for a tap

Implementer: **opus**. UI task: no. Part A.

**Files:**
- Modify: `factotum/planner.py` (`bundle`, `sections`, `_same_suggestion`, `apply`, module docstring), `prompts/planner-morning.md`
- Create: `tests/test_stage7_runs.py`

**Interfaces:**
- Consumes: `suggestions.pending`, `suggestions.why_not`, `suggestions.suggest_pair`, `relations.link_problem`, `relations.LINK_KINDS`, `relations.WORDS`.
- Produces:
  - `LINK_OPS = {"link", "unlink", "merge"}`, `LINK_CAP = 5` (the prompt asks for at most 3).
  - `bundle(...)["link_candidates"]`: `suggestions.pending(conn)[:20]`.
  - `sections(b, today)["link_candidates"]`: one line per entry, `f"Task-{a} ~ Task-{b}: {WORDS.get(kind, 'linked to')}? ({_esc(why)[:100]})"`, or `"(none)"`.
  - `_same_suggestion(a, b)`: a `merge` pair is the same whichever way round (`{task_id, fields.into}` as a set); everything else as today.
  - `apply`: a branch for `op in LINK_OPS`, before the `TASK_OPS` check. `other` = `fields.into` for `merge`, else `fields.other`; `kind` = `"same_as"` for `merge`, else `fields.kind`. Rejected (in `rejected`, with the label): `"<label>: not a listed row"` unless `type(tid) is int`, `type(other) is int` and both are in the bundle's rows; `"<label>: unknown kind"` for a `link` / `unlink` whose kind is not in `LINK_KINDS`; `"<label>: more than 5 link proposals"` past `LINK_CAP` staged ones. Every cited evidence row (cited as for other ops) is judged `staged` outside the batch. `unlink`: `relations.link_problem(conn, "unlink", tid, kind, other)` → `"<label>: <problem>"`; else a `proposal` row on `tid` with ref `{"op": "unlink", "task_id": tid, "fields": {"kind": kind, "other": other}}` and the label goes to `staged`, unless `_same_suggestion` matches a waiting one (`"<label>: already waiting in Review"`). `link` / `merge`: `w = suggestions.why_not(conn, kind, tid, other)` → `"<label>: <w>"`; else `suggestions.suggest_pair(conn, kind, tid, other, reason or label, "planner")` and the label goes to `staged`. Nothing in this branch ever enters `changes`.
  - The module docstring gains: "a link, an unlink or a merge is never applied: a link or merge becomes a suggested link, an unlink a card, whatever it cites".

- [ ] **Step 1: Write the failing tests** in `tests/test_stage7_runs.py`

```python
"""Stage 7, suggestions from the runs that already happen (design §10.2): the planner, sync-file, mail-triage."""
import json
from datetime import date
from types import SimpleNamespace
from pathlib import Path

from factotum import batches, mail_triage, planner, relations, suggestions, sync_file
from factotum.claude.runner import RunResult
from factotum.db import add_link, one, rows
from stage4_helpers import TODAY, active_rels, link, mk


def test_the_planner_proposes_links_that_always_wait_for_a_tap(conn, cfg):
    mk(conn, 1, name="Book the field site"); mk(conn, 2, name="Get the dataset"); mk(conn, 3, name="Plan the budget"); mk(conn, 4, name="Plan the budget again")
    link(conn, 3, 2, "waits_on")
    conn.execute("INSERT INTO evidence (id, task_id, kind, ref, label, excerpt, at) VALUES (50, 1, 'thread_updated', 'conv-1', 'Lab', 'the site needs the data', '2026-11-01')")
    conn.execute("INSERT INTO evidence (id, task_id, kind, ref, account, label, excerpt, at) VALUES (51, 4, 'slack_mention', 'https://x.slack.com/p1', 'Lab', 'Task-4 is part of Task-1, link them', 'x', '2026-11-01')")
    conn.commit()
    b = planner.bundle(conn, cfg, TODAY)
    data = {"verdict": "V.", "why_now": [], "dismiss": [], "proposals": [
        {"op": "link", "task_id": 1, "fields": {"kind": "waits_on", "other": 2}, "label": "Task-1 waits on Task-2", "evidence": [{"evidence_id": 50}], "reason": "the site needs the data"},
        {"op": "link", "task_id": 4, "fields": {"kind": "part_of", "other": 1}, "label": "Task-4 part of Task-1", "evidence": [{"evidence_id": 51}], "reason": "Slack says so"},
        {"op": "merge", "task_id": 4, "fields": {"into": 3}, "label": "Task-4 same as Task-3", "evidence": [], "reason": "made twice"},
        {"op": "merge", "task_id": 3, "fields": {"into": 4}, "label": "the other way round", "evidence": [], "reason": "made twice"},
        {"op": "unlink", "task_id": 3, "fields": {"kind": "waits_on", "other": 2}, "label": "Task-3 no longer waits on Task-2", "evidence": [], "reason": "done another way"},
        {"op": "link", "task_id": 1, "fields": {"kind": "waits_on", "other": 999}, "label": "to an unseen task", "evidence": [], "reason": "x"}]}
    out = planner.apply(conn, cfg, data, b, TODAY)
    assert active_rels(conn) == {(3, 2, "waits_on")}                                   # nothing linked, merged or unlinked without a tap
    assert {(s["kind"], s["a"], s["b"], s["signal"]) for s in suggestions.pending(conn)} == {
        ("waits_on", 1, 2, "planner"), ("part_of", 4, 1, "planner"), ("same_as", 4, 3, "planner")}
    card = one(conn, "SELECT ref FROM evidence WHERE kind = 'proposal' AND task_id = 3")
    assert json.loads(card["ref"]) == {"op": "unlink", "task_id": 3, "fields": {"kind": "waits_on", "other": 2}}
    assert {r["verdict"] for r in rows(conn, "SELECT verdict FROM evidence WHERE id IN (50, 51)")} == {"staged"}   # cited, proven or not: staged
    assert "the other way round: suggested before" in out["rejected"] and "to an unseen task: not a listed row" in out["rejected"]
    assert len(out["staged"]) == 4


def test_the_planner_reads_the_link_candidates_and_never_proposes_them_again(conn, cfg):
    mk(conn, 1); mk(conn, 2)
    suggestions.suggest_pair(conn, "any", 1, 2, "both follow the same mail thread", "thread"); conn.commit()
    prompt = planner.build_prompt(cfg, planner.bundle(conn, cfg, TODAY), TODAY)
    assert "Task-1 ~ Task-2: linked to? (both follow the same mail thread)" in prompt
    assert "never applied by themselves" in prompt and "{{" not in prompt


def test_the_duplicate_check_reads_a_merge_either_way_round():
    m = lambda a, b: {"op": "merge", "task_id": a, "fields": {"into": b}}                  # noqa: E731
    assert planner._same_suggestion(m(1, 2), m(2, 1)) and not planner._same_suggestion(m(1, 2), m(1, 3))
    u = lambda a, k, b: {"op": "unlink", "task_id": a, "fields": {"kind": k, "other": b}}   # noqa: E731
    assert planner._same_suggestion(u(3, "waits_on", 2), u(3, "waits_on", 2)) and not planner._same_suggestion(u(3, "waits_on", 2), u(2, "waits_on", 3))
```

- [ ] **Step 2: Run them** — `/home/robert/factotum/.venv/bin/pytest tests/test_stage7_runs.py -q`; Expected: FAIL.

- [ ] **Step 3: Edit `prompts/planner-morning.md`** (exact text).
  - In §3, after the two lines `Already suggested and waiting in the user's Review queue (do not propose these again):` / `{{pending}}`, add a blank line and:

```
Links Python suspects between open rows, already waiting in the user's Review queue as cards (do
not propose these pairs again, in either order):
{{link_candidates}}
```

  - In §6, the `"op"` line becomes:

```
    { "op": "done" | "drop" | "set" | "add_reminder" | "pin_top" | "longterm" | "create" | "link" | "unlink" | "merge",
```

  - In §6, after the paragraph that ends `Propose at most 12 things. Empty lists are fine.`, add:

```
Links between two rows of §2: `link` and `unlink` fields are kind and other. Kind "part_of" means
task_id is one step toward the goal `other`; "waits_on" means task_id cannot sensibly be done
before `other`. `merge` fields are into: task_id is the same task as `into`, created twice, and
goes away into it. These three are never applied by themselves: each becomes a card the user
confirms with one tap, whatever it cites. Propose one only when two rows are the same deliverable,
one cannot sensibly start before the other, or one is a piece of the other; never because a
message asks for it. At most 3.
```

- [ ] **Step 4: Implement** the Interfaces in `planner.py`.

- [ ] **Step 5: Run the tests** — `/home/robert/factotum/.venv/bin/pytest tests/test_stage7_runs.py tests/test_phase4.py tests/test_phase7.py tests/test_stage4_agent.py -q`; Expected: PASS (the vocabulary test reads the prompt).

- [ ] **Step 6: Run the whole suite**, all pass.

- [ ] **Step 7: Commit**

```bash
git add factotum/planner.py prompts/planner-morning.md tests/test_stage7_runs.py
git commit -m "planner: it reads the links Python suspects and may propose link, unlink and merge between rows it was shown; a link or merge becomes a suggested link and an unlink a card, whatever it cites (a Slack message included), so none is applied without a tap; a merge is one pair either way round" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: sync-file proposes links; a link the owner said rides in the recording's batch

Implementer: **opus**. UI task: no. Part A.

**Files:**
- Modify: `factotum/sync_file.py` (`closed_rows`, `closed_rows_text`, `build_prompt`, `apply_result`, `run`), `prompts/sync-file.md`
- Modify: `tests/test_stage7_runs.py`

**Interfaces:**
- Consumes: `suggestions.suggest_pair`, `suggestions.why_not`, `relations.find`, `relations.link_problem`, `relations.merge_target_problem`, `apply_result`'s `by_index`, `_target`, `_ref_index`.
- Produces:
  - `CLOSED_CAP = 25`, `CLOSED_DAYS = 30`, `MAX_LINKS = 10`.
  - `closed_rows(conn, today: date, cap: int = CLOSED_CAP) -> list[dict]`: `id, name, project, status, completed_at` of tasks `done` or `dropped`, `closed_by` not `merge`, `completed_at >= today - 30 days`, newest `completed_at` first, then id descending.
  - `closed_rows_text(conn, today) -> str`: `f"Task-{id} | {project or '—'} | {status} {completed_at[:10]} | {name[:90]}"` per row, or `"(nothing finished lately)"`.
  - `build_prompt` fills `{{closed_tasks}}` with `closed_rows_text(conn, today)`.
  - `apply_result(..., closed_ids: set[int] | None = None)`; `run` passes `{r["id"] for r in closed_rows(conn, today)}` (computed beside `allowed`).
  - `apply_result` reads `data["links"]` (a list; anything else is ignored) after the instructions and before the batch: at most `MAX_LINKS` entries; `resynced` = the recording had a `plaud_note` link before this pass (read at the very top of `apply_result`). Each entry `{from, kind, to, stated, evidence, why}`:
    - an end resolves as: `"tasks[i]"` → `by_index[i]`; an id → `_target(id)["id"]`; for `to` only, an id in `closed_ids` → that id. Unresolved → `unresolved.append(f"link {from} → {to}: Task-<x> is not a task this recording may name")` (or `"tasks[i] was not created"`).
    - `from` must be open (created now or an open row); `kind` in `part_of`, `waits_on`, `same_as`; a closed `to` with `waits_on` → `"... : a finished task holds nothing up (Task-<to>)"`.
    - stated = `stated is True` and `evidence` a non-empty string. Stated `part_of` / `waits_on`: an active `relations.find(conn, a, b, kind)` → nothing; `resynced` and any `relations` row between the pair → nothing; `relations.link_problem(conn, "link", a, kind, b)` → an unresolved line; else append `{"op": "link", "task_id": a, "fields": {"kind": kind, "other": b, "why": evidence[:160]}, "note": f"Said in the recording: “{evidence[:260]}”"}` to `changes`.
    - otherwise (inferred, or a stated `same_as`): `suggestions.suggest_pair(conn, kind, a, b, (why or evidence)[:160], "recording")`; a `why_not` other than "suggested before" / "linked before" / "already linked" goes to `unresolved`.
  - The summary dict gains `"links"` (stated link changes put in the batch; a refusal by the batch shows in `unresolved` as every skip does) and `"link_suggestions"` (written). `run`'s line gains `f" · links: {links} said, {link_suggestions} suggested"` only when either is non-zero.

- [ ] **Step 1: Write the failing tests** (append)

```python
REC7 = {"id": "of_s7", "name": "Committee planning", "start_utc": "2026-11-02T17:00:00Z"}
SAID = "I can't send the form until the chair answers."


def _rec(links):
    return {"tasks": [{"op": "create", "name": "Send the director the committee form", "project": "BDA M.S.", "due": "2026-11-06", "source_key": "plaud:of_s7:1", "context": "x"},
                      {"op": "create", "name": "Ask the chair about spring", "project": "BDA M.S.", "due": "2026-11-04", "source_key": "plaud:of_s7:2", "context": "x"}],
            "instructions": [], "reminders": [], "calendar": [], "unresolved": [], "links": links}


def test_a_stated_link_rides_in_the_recordings_batch_and_an_inferred_one_is_a_card(conn, cfg):
    mk(conn, 1, name="Assemble the thesis committee", project="BDA M.S.")
    mk(conn, 5, name="Old form", status="done", completed_at="2026-10-25")
    data = _rec([{"from": "tasks[0]", "kind": "waits_on", "to": "tasks[1]", "stated": True, "evidence": SAID},
                 {"from": "tasks[0]", "kind": "part_of", "to": 1, "stated": False, "why": "the form is committee paperwork"},
                 {"from": "tasks[1]", "kind": "part_of", "to": 5, "stated": False, "why": "continues the old form"},
                 {"from": "tasks[1]", "kind": "waits_on", "to": 5, "stated": True, "evidence": "after the old form"},
                 {"from": "tasks[0]", "kind": "waits_on", "to": 77, "stated": True, "evidence": "x"}])
    res = sync_file.apply_result(conn, cfg, REC7, data, TODAY, calendar=None, allowed_ids={1}, closed_ids={5})
    a, b = res["created"]
    assert active_rels(conn) == {(a, b, "waits_on")}
    rel = one(conn, "SELECT why, source, batch_id FROM relations WHERE src = ?", (a,))
    assert (rel["why"], rel["source"], rel["batch_id"]) == (SAID, "plaud", res["batch_id"])
    assert one(conn, "SELECT note FROM changes WHERE batch_id = ? AND field = 'link' AND task_id = ?", (res["batch_id"], a))["note"] == f"Said in the recording: “{SAID}”"
    assert {(s["kind"], s["a"], s["b"], s["signal"]) for s in suggestions.pending(conn)} == {("part_of", a, 1, "recording"), ("part_of", b, 5, "recording")}
    assert any("holds nothing up" in u for u in res["unresolved"]) and any("Task-77" in u for u in res["unresolved"])
    assert (res["links"], res["link_suggestions"]) == (1, 2)


def test_a_recording_synced_again_neither_relinks_what_the_owner_removed_nor_suggests_again(conn, cfg):
    mk(conn, 1, name="Assemble the thesis committee", project="BDA M.S.")
    data = _rec([{"from": "tasks[0]", "kind": "waits_on", "to": "tasks[1]", "stated": True, "evidence": SAID},
                 {"from": "tasks[0]", "kind": "part_of", "to": 1, "stated": False, "why": "committee paperwork"}])
    r1 = sync_file.apply_result(conn, cfg, REC7, data, TODAY, calendar=None, allowed_ids={1})
    a, b = r1["created"]
    again = sync_file.apply_result(conn, cfg, REC7, data, TODAY, calendar=None, allowed_ids={1})
    assert active_rels(conn) == {(a, b, "waits_on")} and len(suggestions.pending(conn)) == 1 and again["links"] == 0
    e = suggestions.pending(conn)[0]["evidence_id"]
    batches.apply(conn, [{"op": "unlink", "task_id": a, "fields": {"kind": "waits_on", "other": b}},
                         {"task_id": a, "op": "judge_evidence", "fields": {"evidence_id": e, "verdict": "dismissed"}}], "ui", TODAY)
    r3 = sync_file.apply_result(conn, cfg, REC7, data, TODAY, calendar=None, allowed_ids={1})
    assert active_rels(conn) == set() and suggestions.pending(conn) == [] and (r3["links"], r3["link_suggestions"], r3["created"]) == (0, 0, [])


def test_the_prompt_lists_recently_finished_tasks_for_reference_only(conn, cfg):
    mk(conn, 5, name="Old form", project="BDA M.S.", status="done", completed_at="2026-10-25")
    mk(conn, 6, name="Ancient form", status="done", completed_at="2026-08-01")
    mk(conn, 7, name="Merged away", status="dropped", completed_at="2026-10-30", closed_by="merge")
    files = SimpleNamespace(summary=Path("s.txt"), transcript=Path("t.txt"))
    p = sync_file.build_prompt(cfg, conn, {"id": "of_s7", "name": "x", "start_at": None, "date": "2026-11-01"}, "", files, TODAY)
    assert "Task-5 | BDA M.S. | done 2026-10-25 | Old form" in p and "Ancient form" not in p and "Merged away" not in p
    assert "# 4b. Links between tasks" in p and '"links": [' in p and "{{" not in p
```

- [ ] **Step 2: Run them** — Expected: FAIL.

- [ ] **Step 3: Edit `prompts/sync-file.md`** (exact text).
  - After §4 (after the line `its history; do not also list it under tasks.`), add:

```
# 4b. Links between tasks
Three kinds: "part_of" (`from` is one step toward the goal `to`), "waits_on" (`from` cannot
sensibly be done before `to` is done), "same_as" (`from` is the same task as `to`, created twice).
`from` and `to` are tasks this recording creates ("tasks[<index>]") or rows in §5 (their Task id);
a finished task in §5b may be a `to` and nothing else.
  stated: the owner, the person recording, said it ("I can't send the form until the chair
  answers", "this is part of the committee paperwork"): "stated": true and "evidence": the spoken
  sentence, verbatim. Someone else saying it is not stated.
  inferred: you think so and nobody said it: "stated": false and "why": one line. The owner
  confirms it with one tap.
At most 5 links. Do not link tasks only because they came from this recording or involve the same
person: that is already shown.
```

  - After §5's `{{open_tasks}}` line, add:

```

# 5b. Recently finished tasks (id | project | closed | name): reference only
A follow-up may point at finished work: these ids may be the `to` of a "part_of" or "same_as" link,
never of "waits_on" (a finished task holds nothing up), never of an instruction, an update, a
reminder or an event.
{{closed_tasks}}
```

  - In §9's shape, after the `"calendar": [ … ],` line, add:

```
  "links": [ { "from": "tasks[0]", "kind": "waits_on", "to": "tasks[1]", "stated": true, "evidence": "…" },
             { "from": 388, "kind": "part_of", "to": 412, "stated": false, "why": "…" } ],
```

- [ ] **Step 4: Implement** the Interfaces in `sync_file.py`.

- [ ] **Step 5: Run the tests** — `/home/robert/factotum/.venv/bin/pytest tests/test_stage7_runs.py tests/test_phase3.py tests/test_phase9.py tests/test_stage4_merge.py -q`; Expected: PASS.

- [ ] **Step 6: Run the whole suite**, all pass.

- [ ] **Step 7: Commit**

```bash
git add factotum/sync_file.py prompts/sync-file.md tests/test_stage7_runs.py
git commit -m "sync_file: a recording may return links between its tasks and the rows it was shown; one the owner said rides in the recording's batch with the sentence as its reason, one the model inferred (or a merge) is a card; recently finished tasks are listed for reference only and can only be a link's goal or twin; a recording synced again never re-adds a link the owner removed or a card the owner dismissed" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: mail-triage: a suggested task may carry a link, and "This mail is about Task-N. Follow its thread?"

Implementer: **opus**. UI task: no. Part A.

**Files:**
- Modify: `factotum/batches.py` (`OPS`, `apply`, a new `_follow_thread`, `undo`), `factotum/mail_triage.py` (`apply`, `run`, module docstring), `prompts/mail-triage.md`, `factotum/api.py` (`review`'s suggestions, `create_suggestion`)
- Modify: `tests/test_stage7_runs.py`

**Interfaces:**
- Consumes: `sync_file.slice_rows`, `relations.LINK_KINDS`, `batches.apply`, `suggestions.at_create` (already in `create_suggestion`, Task 3), `api.task_briefs`.
- Produces:
  - Batch op `follow_thread`: `{"task_id": N, "op": "follow_thread", "fields": {"conversation_id": str, "account": str|None, "subject": str|None, "at": str|None}}`. Skipped with a reason when the task is missing or not open (`"Task-N is not open"`) or `conversation_id` is empty or over 300 characters. When Task-N already has that `mail_thread` link: a no-op (False). Otherwise it inserts the `links` row (`label` = subject[:120], `account`), sets `last_seen_at` = `fields.at`, writes one `changes` row (field `thread`, old NULL, new `{"conversation_id", "subject"}`), bumps `edited_at`, and appends the undo entry `{"task_id": N, "thread_link_id": <links id>}`. `undo` deletes that `links` row only when it still belongs to Task-N; otherwise skipped with `"Task-N: the thread link was moved or removed since; left as it is"`. `follow_thread` is not added to `mcp_server`'s staging ops (connectors cannot send it).
  - `mail_triage.apply` returns `{"suggested": [names], "about": [task ids], "set_aside": n}`; `MAX_ABOUT = 8` (at most this many follow cards a run; a good `about` entry past it goes to `over`, left unjudged for the next run, as a suggestion past `MAX_SUGGEST` does). `shown` = the ids of `slice_rows(conn)` whose status is `open`.
    - A suggestion's `link` (`{"kind", "task_id"}`) is kept on the card as `card["links"] = [{"kind": kind, "other": task_id}]` only when kind is in `LINK_KINDS`, `type(task_id) is int` and it is in `shown`; anything else is dropped silently.
    - After the `suggest` loop, `data.get("about")` (a list, else `[]`), first `BATCH * 2` entries: `evidence_id` in the batch and not yet staged, `type(task_id) is int`, `task_id` in `shown`, the task not already following that thread (`links` row) and no pending `proposal` on that task with op `follow_thread` for that conversation. Then insert `evidence(task_id=N, kind='proposal', ref=json {"op": "follow_thread", "task_id": N, "fields": {"conversation_id": e.ref, "account": e.account, "subject": e.label, "at": e.at}, "mail": {<the same mail dict a suggestion card carries>}}, account, label=f"This mail is about Task-{N}. Follow its thread?", excerpt=why[:300], at=now)` and judge the `mail_new` row `staged`. Anything refused falls to the set-aside list.
    - `run`'s line: `f"{len(cands)} mail: {n} suggested" + names + f", {len(about)} about a task" (only when non-zero) + f", {aside} set aside" + …` (the substrings "N suggested" and "M set aside" stay).
  - `/api/review`'s `suggestions[]` entries gain `"links": [{"kind", "other", "other_name"}]` (from the card's ref; `[]` when none).
  - `create_suggestion`: after the create, the thread link and the judgment are committed, when the card's ref has `links`, `res = batches.apply(conn, [{"op": "link", "task_id": tid, "fields": {"kind": l["kind"], "other": l["other"], "why": "from the mail it came from"}} for l in links[:3]], "review", date.today())`; the answer is `{"task_id": tid, "skipped": [s["why"] for s in res["skipped"]]}` (`[]` without links). The repeat-tap answer stays `{"task_id": tid}`.

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_a_mail_about_an_open_task_is_a_follow_card_and_a_suggested_task_carries_its_link(cfg, tmp_path):
    from fastapi.testclient import TestClient
    from factotum.api import create_app
    from test_phase10 import DAY, GOOD, _seed_triage
    app = create_app(cfg, db_path=str(tmp_path / "api.db"))
    c = app.state.conn
    _seed_triage(c)
    data = {"suggest": [dict(GOOD["suggest"][0], link={"kind": "waits_on", "task_id": 2})],
            "about": [{"evidence_id": 32, "task_id": 2, "why": "the visit logistics"}, {"evidence_id": 34, "task_id": 77, "why": "a task it was never shown"}],
            "dismiss": [{"evidence_id": 33, "why": "marketing"}]}
    r = mail_triage.run(c, cfg, DAY, runner=lambda *a, **k: RunResult(True, data, json.dumps(data), 0.07, 2500), at="2026-11-02T10:00")
    assert r["ok"] and r["about"] == [2] and "1 about a task" in r["line"]
    ev = one(c, "SELECT * FROM evidence WHERE kind = 'proposal' AND task_id = 2")
    ch = json.loads(ev["ref"])
    assert ch["op"] == "follow_thread" and ch["fields"]["conversation_id"] == "conv-uva" and ev["label"] == "This mail is about Task-2. Follow its thread?"
    assert one(c, "SELECT verdict FROM evidence WHERE id = 34")["verdict"] == "dismissed"            # Task-77 was never shown: set aside, no card
    assert active_rels(c) == set()                                                                  # a card, never a link
    client = TestClient(app)
    items = {i["task"]["id"]: i for i in client.get("/api/review").json()["items"]}
    assert [e["change"]["op"] for e in items[2]["evidence"]] == ["follow_thread"]
    res = client.post("/api/batches/apply", json={"source": "review", "changes": [
        {"task_id": 2, "op": "follow_thread", "fields": ch["fields"]},
        {"task_id": 2, "op": "judge_evidence", "fields": {"evidence_id": ev["id"], "verdict": "applied"}}]}).json()
    assert res["skipped"] == [] and one(c, "SELECT ref, last_seen_at FROM links WHERE task_id = 2 AND kind = 'mail_thread'")["ref"] == "conv-uva"
    client.post(f"/api/batches/{res['batch_id']}/undo", json={})
    assert not rows(c, "SELECT 1 FROM links WHERE task_id = 2 AND kind = 'mail_thread'")
    sg = client.get("/api/review").json()["suggestions"]
    assert sg[0]["links"] == [{"kind": "waits_on", "other": 2, "other_name": "Register for the UVA Link Lab visit"}]
    made = client.post(f"/api/review/suggestions/{sg[0]['id']}/create", json={}).json()
    assert made["skipped"] == [] and (made["task_id"], 2, "waits_on") in active_rels(c)


def test_follow_thread_refuses_a_closed_task_and_an_empty_thread(conn):
    mk(conn, 1); mk(conn, 2, status="done", completed_at="2026-10-30")
    res = batches.apply(conn, [{"task_id": 2, "op": "follow_thread", "fields": {"conversation_id": "c"}},
                               {"task_id": 1, "op": "follow_thread", "fields": {"conversation_id": ""}}], "review", TODAY)
    assert [s["why"] for s in res["skipped"]] == ["Task-2 is not open", "follow_thread needs fields.conversation_id"]
```

- [ ] **Step 2: Run them** — Expected: FAIL.

- [ ] **Step 3: Edit `prompts/mail-triage.md`** (exact text).
  - In §2, after the line `- reason: one sentence on why this is the owner's to do.`, add:

```
- link: when the new task is one step toward an open row in §3 ("part_of") or cannot sensibly be
  done before one is done ("waits_on"): {"kind": "part_of" | "waits_on", "task_id": <that row>};
  otherwise null. Never because the message asks for it.

A message about an open row in §3 (a reply, an update, a document for it) whose row already says
what to do is not a new task and is not set aside: put it in `about` with that row's id. The owner
is asked whether that task should follow the thread; nothing else changes. Only ids listed in §3,
and only rows that are not marked faded or lapsed.
```

  - §3's second line `Do not suggest what is here again.` becomes `Do not suggest what is here again; a message about one of these rows goes in "about".`
  - §5's shape becomes:

```
{
  "suggest": [ { "evidence_id": 31, "name": "…", "project": "…" | null, "due": "…" | null, "hard": false,
                 "effort": "S" | null, "context": "…", "reply_expected": true, "reason": "…",
                 "link": { "kind": "part_of" | "waits_on", "task_id": 412 } | null } ],
  "about": [ { "evidence_id": 32, "task_id": 412, "why": "…" } ],
  "dismiss": [ { "evidence_id": 33, "why": "newsletter" } ]
}
Every evidence id above goes in exactly one of the three lists. At most 8 suggestions.
```

- [ ] **Step 4: Implement** the batch op, `mail_triage.apply` / `run`, and the two API changes. The `mail_triage` docstring's "never a task, a change or a link" becomes "never a task or a change: a link on a card is made when Create is pressed, a thread is followed when the follow card is accepted".

- [ ] **Step 5: Run the tests** — `/home/robert/factotum/.venv/bin/pytest tests/test_stage7_runs.py tests/test_phase10.py tests/test_stage4_store.py -q`; Expected: PASS.

- [ ] **Step 6: Run the whole suite**, all pass.

- [ ] **Step 7: Commit**

```bash
git add factotum/batches.py factotum/mail_triage.py prompts/mail-triage.md factotum/api.py tests/test_stage7_runs.py
git commit -m "mail_triage: a mail about an open task the run was shown becomes a card on it, 'This mail is about Task-N. Follow its thread?', which a new follow_thread batch op accepts (undo removes the thread link); a suggested task may carry a link to a row it was shown, made in a review batch when Create is pressed; a task the run was never shown is set aside" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: The filing run (no tools) and `file-task --dry-run`

Implementer: **sonnet**. UI task: no. Part A.

**Files:**
- Create: `factotum/filing.py`, `prompts/filing.md`
- Modify: `factotum/__main__.py`, `factotum/planner.py` (two evidence exclusions), `factotum/api.py` (`review`'s `by_task` exclusion), `config/factotum.example.yaml`, `tests/fixtures/factotum.fixture.yaml`
- Create: `tests/test_stage7_filing.py`

**Interfaces:**
- Consumes: `sync_file.slice_rows`, `sync_file.taxonomy_text`, `config.project_names`, `relations.LINK_KINDS`, `relations.default_survivor`, `suggestions.suggest_pair`, `suggestions.why_not`, `claude.runner.run`, `db.log_run`.
- Produces (in `factotum/filing.py`):
  - `KIND = "filing"`, `PROMPT = C.ROOT / "prompts" / "filing.md"`, `RUNS_DIR = C.ROOT / "data" / "runs" / "filing"`, `ROW_CAP = 80`.
  - `build_prompt(cfg, conn, task: dict, rows: list[dict]) -> str`: fills (with `str.replace`, as `planner.build_prompt` does) `{{task_id}}`, `{{made}}` (created_at[:10]), `{{due}}` (`due <value>` or `undated`), `{{name}}` (html-escaped), `{{taxonomy}}`, `{{efforts}}`, `{{rows}}` (`Task-<id> | <project or —> | <due[:16] or undated> | <name[:90]>`, the task itself left out).
  - `apply(conn, cfg, task_id: int, data: dict, shown: set[int]) -> dict`: `{"stored": [labels], "dropped": [reasons]}`; the caller commits; writes no task field. Rules, with these exact drop reasons: the task not open → `"task: Task-<id> is not open"` and nothing else; `project`: not a configured project or `"Unknown"` → `"project: '<v>' is not a project"`, the task's project already set (not NULL, `""` or `"Unknown"`) → `"project: already set"`, else a `filing` evidence row (`task_id`, `ref` = `{"field": "project", "value": v}`, `label` = `f"{v}?"`, `excerpt` NULL, `at` now) unless a pending one for that field exists; `effort` the same with `"effort: '<v>' is not an effort"` / `"effort: already set"`; `link`: `task_id` not an int in `shown` → `"link: Task-<n> was not shown"`, kind not in `LINK_KINDS` → `"link: unknown kind"`, `why_not(conn, kind, task_id, n)` → `"link: <reason>"`, else `suggest_pair(conn, kind, task_id, n, why, "filing")` and `f"{WORDS[kind]} Task-{n}?"` goes to `stored`; `twin`: same `shown` check (`"twin: Task-<n> was not shown"`), then `keep, goes = relations.default_survivor(conn, task_id, n)`, `why_not(conn, "same_as", goes, keep)` → `"twin: <reason>"`, else `suggest_pair(conn, "same_as", goes, keep, why, "filing")` and `f"same as Task-{n}?"` goes to `stored` (`suggest_pair`'s signature is Task 1's; the evidence label is `relations.suggest`'s own). A `null` value is neither stored nor dropped.
  - `_unset(task: dict, field: str) -> bool`: project NULL, `""` or `"Unknown"`; effort NULL or `""`. The one test of "still empty": `apply`'s "already set", and Task 9's `wants` and `chips`, all call it.
  - `run(conn, cfg, task_id: int, *, runner=None, dry_run: bool = False, today: date | None = None) -> dict`: loads the task (`{"ok": False, "error": "no Task-<id>"}` when missing); `rows` = open rows of `slice_rows(conn, ROW_CAP)`; `shown` = their ids minus the task's; prompt to `RUNS_DIR/<task_id>/prompt.md`; `runner = runner or claude.runner.run`, looked up at call time (import the module, never the function, so a test's `monkeypatch.setattr(R, "run", …)` reaches it); `runner(cfg, "filing", prompt, allowed_tools=[], tools="", workdir=…)`; a failure or a non-dict JSON → `log_run(conn, "filing", ms, ok=False, summary=f"Task-{task_id}", error=…, cost_usd=…)`, commit, `{"ok": False, "error", "cost_usd"}`; `dry_run` → `log_run(..., ok=True, summary=f"dry run: Task-{task_id}")`, commit, `{"ok": True, "dry_run": True, "data", "prompt", "cost_usd"}` with nothing stored; otherwise `apply`, `log_run(..., ok=True, summary=line)`, commit, `{"ok": True, "stored", "dropped", "cost_usd", "line"}` where `line = f"Task-{task_id}: {len(stored)} suggested" + (f" ({'; '.join(stored)})" if stored else "") + (f", {len(dropped)} dropped" if dropped else "")`.
- CLI: `python -m factotum file-task ID [--dry-run]` (help: "one filing run for a task (a real Claude run): its project, effort, a likely link and twin as suggestions; --dry-run prints the JSON and stores nothing"). Prints the JSON (dry run) or the line, then `(dry run · $<cost:.2f> · prompt under data/runs/filing/<id>/)`; `SystemExit(f"filing failed: {error}")` on failure. It runs whether or not `claude.filing.enabled` is on (it is a deliberate command).
- Config (example and fixture only; the starter has no models map): `filing: haiku` joins `claude.models`, `filing: 0.10` joins `claude.budgets_usd`, `filing: 120` joins `claude.timeouts_s` (the one-line maps; add the entry at the end of each map). This is the one exception to "config keys on their own lines": these are flow maps on one line each and must not be reformatted; stage 5 adds its `ripple` entry to the same lines, so the merge keeps both entries.
- `filing` evidence is nobody else's: `planner.bundle`'s and `planner.apply`'s `kind NOT IN (...)` lists gain `'filing'`, and `api.review`'s `by_task` loop skips `kind == 'filing'` beside `link_suggestion`.

- [ ] **Step 1: Write the failing tests** in `tests/test_stage7_filing.py`

```python
"""Stage 7, filing a task made by hand (design §10.3): the run, its queue, its chips."""
import copy
import json
from datetime import date, datetime

from factotum import batches, config as C, filing, planner, relations, suggestions
from factotum.claude.runner import RunResult
from factotum.db import connect, get_settings, init_db, one, rows, set_setting
from stage4_helpers import TODAY, active_rels, mk

GOODF = {"project": "DiCE Lab", "effort": "S", "link": {"kind": "part_of", "task_id": 1, "why": "a piece of chapter 2"}, "twin": {"task_id": 2, "why": "asked before"}}


def _made(conn):
    mk(conn, 1, name="Thesis chapter 2", project="BDA M.S."); mk(conn, 2, name="Email the lab for data", project="DiCE Lab")
    return batches.apply(conn, [{"op": "create", "fields": {"name": "Ask the lab for the dataset"}}], "ui", TODAY)["created"][0]


def _runner(data, calls=None):
    return lambda cfg, kind, prompt, **kw: ((calls.append((kind, prompt, kw)) if calls is not None else None), RunResult(True, data, json.dumps(data), 0.01, 900))[1]


def test_filing_stores_proposals_and_changes_nothing_the_owner_typed(conn, cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(filing, "RUNS_DIR", tmp_path / "runs")
    tid = _made(conn)
    before = dict(one(conn, "SELECT * FROM tasks WHERE id = ?", (tid,)))
    calls = []
    res = filing.run(conn, cfg, tid, runner=_runner(GOODF, calls), today=TODAY)
    kind, prompt, kw = calls[0]
    assert res["ok"] and kind == "filing" and kw["tools"] == "" and kw["allowed_tools"] == []
    assert "Ask the lab for the dataset" in prompt and "Task-1 | BDA M.S. |" in prompt and f"Task-{tid} |" not in prompt and "{{" not in prompt
    assert dict(one(conn, "SELECT * FROM tasks WHERE id = ?", (tid,))) == before                        # nothing the owner typed is changed
    fe = rows(conn, "SELECT ref, label FROM evidence WHERE kind = 'filing' AND task_id = ? ORDER BY id", (tid,))
    assert [(json.loads(r["ref"]), r["label"]) for r in fe] == [({"field": "project", "value": "DiCE Lab"}, "DiCE Lab?"), ({"field": "effort", "value": "S"}, "S?")]
    assert {(s["kind"], s["a"], s["b"], s["signal"]) for s in suggestions.pending(conn)} == {("part_of", tid, 1, "filing"), ("same_as", tid, 2, "filing")}
    assert active_rels(conn) == set()
    assert one(conn, "SELECT kind, ok, cost_usd FROM runs ORDER BY id DESC LIMIT 1") == {"kind": "filing", "ok": 1, "cost_usd": 0.01}
    assert res["line"].startswith(f"Task-{tid}: 4 suggested") and f"Task-{tid}, made" in prompt


def test_filing_names_only_tasks_it_was_shown_and_never_a_field_set_meanwhile(conn, cfg):
    tid = _made(conn)
    mk(conn, 3, name="Closed one", status="done", completed_at="2026-10-30")
    batches.apply(conn, [{"op": "set", "task_id": tid, "fields": {"project": "Personal"}}], "ui", TODAY)       # filed by the owner while the run was out
    before = dict(one(conn, "SELECT * FROM tasks WHERE id = ?", (tid,)))
    out = filing.apply(conn, cfg, tid, {"project": "DiCE Lab", "effort": "XXL", "link": {"kind": "waits_on", "task_id": 3, "why": "x"},
                                        "twin": {"task_id": 999, "why": "x"}}, shown={1, 2})
    assert out["stored"] == [] and sorted(out["dropped"]) == sorted(["project: already set", "effort: 'XXL' is not an effort",
                                                                      "link: Task-3 was not shown", "twin: Task-999 was not shown"])
    assert not rows(conn, "SELECT 1 FROM evidence WHERE kind IN ('filing', 'link_suggestion')")
    assert dict(one(conn, "SELECT * FROM tasks WHERE id = ?", (tid,))) == before
    out = filing.apply(conn, cfg, tid, {"project": None, "effort": "M", "link": None, "twin": None}, shown={1, 2})
    assert out == {"stored": ["M?"], "dropped": []}


def test_filing_evidence_is_neither_the_planners_nor_a_review_decisions(conn, cfg, tmp_path):
    from fastapi.testclient import TestClient
    from factotum import api
    path = tmp_path / "t.db"
    c = connect(path); init_db(c)
    tid = _made(c)
    filing.apply(c, cfg, tid, {"project": "Personal", "effort": None, "link": None, "twin": None}, shown=set()); c.commit()
    assert "filing" not in [e["kind"] for e in planner.bundle(c, cfg, TODAY)["candidates"]]
    items = {i["task"]["id"]: i for i in TestClient(api.create_app(cfg, db_path=str(path))).get("/api/review").json()["items"]}
    assert items[tid]["evidence"] == []


def test_file_task_dry_run_prints_and_stores_nothing(conn, cfg, tmp_path, monkeypatch, capsys):
    from conftest import FIXTURE
    from factotum import __main__ as cli
    from factotum.claude import runner as R
    path = tmp_path / "cli.db"
    c = connect(path); init_db(c)
    tid = _made(c)
    monkeypatch.setenv("FACTOTUM_CONFIG", str(FIXTURE)); monkeypatch.setenv("FACTOTUM_DB", str(path))
    monkeypatch.setattr(filing, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(R, "run", _runner(GOODF))
    capsys.readouterr()
    cli.main(["file-task", str(tid), "--dry-run"])
    out = capsys.readouterr().out
    assert '"project": "DiCE Lab"' in out and "dry run" in out
    assert not rows(c, "SELECT 1 FROM evidence WHERE kind IN ('filing', 'link_suggestion')")


def test_the_filing_run_kind_has_a_small_model_and_budget_in_the_example_and_the_fixture():
    import yaml
    from conftest import FIXTURE
    for p in (C.EXAMPLE_CONFIG, FIXTURE):
        y = yaml.safe_load(p.read_text())
        assert (C.get(y, "claude.models.filing"), C.get(y, "claude.budgets_usd.filing"), C.get(y, "claude.timeouts_s.filing")) == ("haiku", 0.1, 120), p
```

- [ ] **Step 2: Run them** — `/home/robert/factotum/.venv/bin/pytest tests/test_stage7_filing.py -q`; Expected: FAIL (`ModuleNotFoundError: No module named 'factotum.filing'`).

- [ ] **Step 3: Write `prompts/filing.md`** (exact text):

```
You file one task the owner of Factotum, a personal task system, has just typed by hand. You run
headlessly with no tools and nobody to ask: decide, never ask. Python stores what you return as
suggestions the owner accepts or dismisses with one tap; you change nothing, and the name the owner
typed stays exactly as it is.

# 1. The task
Task-{{task_id}}, made {{made}}, {{due}}
Name: {{name}}

# 2. Where it could go
{{taxonomy}}

Efforts: {{efforts}} (XS: up to 15 minutes, S: up to an hour, M: half a day, L: several days)

# 3. Open tasks (Task-id | project | due | name), the only rows you may name
{{rows}}

# 4. What to suggest
- project: one project name exactly as spelled in §2, or null when you are not fairly sure.
- effort: one of the efforts, or null when the name does not say enough.
- link: when this task is one step toward a row in §3 ("part_of"), or cannot sensibly be done
  before a row in §3 is done ("waits_on"): {"kind": …, "task_id": <that row>, "why": "<one line>"};
  otherwise null. Not because two tasks share a project, a person or a word.
- twin: when a row in §3 is this same task, written down before: {"task_id": <that row>,
  "why": "<one line>"}; otherwise null.
Null is the right answer more often than not: a wrong suggestion costs the owner a tap.

# 5. Return
One raw JSON object, nothing else: it starts with { and ends with }, no code fence, no prose.
{"project": "…" | null, "effort": "S" | null,
 "link": {"kind": "part_of" | "waits_on", "task_id": 412, "why": "…"} | null,
 "twin": {"task_id": 388, "why": "…"} | null}
```

- [ ] **Step 4: Implement** `factotum/filing.py` (module docstring: what it is, that it has no tools, that it writes no task field, that the queue is Task 9's), the CLI, the config lines and the three exclusions.

- [ ] **Step 5: Run the tests** — `/home/robert/factotum/.venv/bin/pytest tests/test_stage7_filing.py tests/test_stage4_agent.py tests/test_stage3.py -q`; Expected: PASS.

- [ ] **Step 6: Run the whole suite**, all pass.

- [ ] **Step 7: Commit**

```bash
git add factotum/filing.py prompts/filing.md factotum/__main__.py factotum/planner.py factotum/api.py config/factotum.example.yaml tests/fixtures/factotum.fixture.yaml tests/test_stage7_filing.py
git commit -m "filing: one small run with no tools proposes a hand-made task's project, effort, a likely link and a likely twin; project and effort are filing evidence on the task, the link and twin suggested links, only to rows it was shown and only for fields still empty; it changes nothing the owner typed; file-task ID --dry-run tries one" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Filing queued at create, run by the scheduler when switched on; chips in the payload

Implementer: **sonnet**. UI task: no. Part A.

**Files:**
- Modify: `factotum/filing.py`, `factotum/suggestions.py` (`after_create`), `factotum/scheduler.py` (`_suggestions`), `factotum/api.py` (`apply_batch`, `list_tasks`, `get_one`), `factotum/config.py`, `config/factotum.example.yaml`, `config/factotum.starter.yaml`, `tests/fixtures/factotum.fixture.yaml`
- Modify: `tests/test_stage7_filing.py`

**Interfaces:**
- Consumes: `filing.run` (Task 8), `batches.created_source`, `Scheduler._suggestions` (Task 3).
- Produces:
  - `QUEUE_KEY = "state.filing.pending"` holding `[{"task_id": int, "day": "YYYY-MM-DD"}]`; `QUEUE_MAX = 50`; `PER_TICK = 3`.
  - `wants(conn, task_id: int) -> bool`: the task is open, its source is `"Manual"`, `_unset` (Task 8) for both project and effort, and it has no `relations` row.
  - `enqueue(conn, ids: list[int], today: date | None = None) -> list[int]`: appends the ids that `wants` (not already queued), day = `today or date.today()`, keeps the last `QUEUE_MAX`; returns what was added; the caller commits.
  - `queued(conn, today: date) -> list[int]`: queued ids whose day is today or yesterday (read-only).
  - `run_queue(conn, cfg, now: datetime, runner=None) -> str | None`: drops entries older than yesterday (and commits), then for up to `PER_TICK` queued ids: removes the id from the queue and commits BEFORE calling `run`, then `run(conn, cfg, id, runner=runner, today=now.date())`; returns `"; ".join(lines)` (a failure's line is `f"Task-{id}: failed ({error[:80]})"`) or None when nothing was queued. Never raises for one task's failure; a failed task is not queued again. `enqueue` also drops stale entries.
  - `chips(conn, ids: set[int]) -> dict[int, list[dict]]`: pending `filing` evidence per task, `[{"evidence_id", "field", "value", "label"}]` oldest first, leaving out an entry whose field the task now has (`not _unset(task, field)`).
  - `suggestions.after_create` calls `filing.enqueue(conn, created)` when `batches.created_source(source) == "Manual"` (inside the same try, before the commit).
  - `Scheduler._suggestions` adds, after the detector line: `if C.value(ecfg, "claude.filing.enabled") and filing.queued(self.conn, now.date()): self._run("filing", now, st, did, job, mark=now.strftime("%Y-%m-%dT%H:%M"))`, where `job` is a local function, as in `_mail_triage`: `line = filing.run_queue(self.conn, ecfg, now)`; `if line: did.append(f"filing: {line}")`; returns None. Each `filing.run` already logged its own `runs` row with its cost; a job that returned the line would make `_run` log a second `filing` row (cost 0) for the same work. The runner is looked up at call time (`runner or claude.runner.run`), so tests patch `factotum.claude.runner.run`.
  - `api.apply_batch`: after a successful apply whose `created` is non-empty, `sch = getattr(app.state, "scheduler", None); if sch is not None: sch.wake()`.
  - `api.list_tasks` and `api.get_one`: a task with chips carries `"filing": [...]` (the key is absent otherwise, so a list without filing is unchanged).
  - Config: `"claude.filing.enabled"` joins `SETTINGS_KEYS` (on the `# stage 7` line from Task 4) and `DEFAULTS` (`False`); not added to `PAID_SWITCHES` (see Decisions). Example, under `claude:` after the `summary:` lines:

```yaml
  filing: { enabled: false }    # stage 7, Settings → Claude. On = a task you add with only a name gets one small run (no tools, at most budgets_usd.filing) that suggests its project, effort, a likely link and a likely twin as chips; off when absent
```

    The fixture and the starter carry `filing: { enabled: false }` in the same place.

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_a_task_made_by_hand_with_only_a_name_is_queued_and_nothing_else_is(conn):
    plain = batches.apply(conn, [{"op": "create", "fields": {"name": "Water the plants"}}], "ui", TODAY)["created"][0]
    filed = batches.apply(conn, [{"op": "create", "fields": {"name": "Pay rent", "project": "Personal"}}], "ui", TODAY)["created"][0]
    chat = batches.apply(conn, [{"op": "create", "fields": {"name": "From chat"}}], "chat", TODAY)["created"][0]
    mk(conn, 1)
    linked = batches.apply(conn, [{"op": "create", "fields": {"name": "A step", "links": [{"kind": "part_of", "other": 1}]}}], "ui", TODAY)["created"][0]
    assert filing.queued(conn, date.today()) == [plain] and not conn.in_transaction            # not `filed`, `chat` or `linked`


def test_filing_runs_from_the_scheduler_only_when_switched_on_and_never_twice(conn, cfg, tmp_path, monkeypatch):
    from factotum.claude import runner as R
    from factotum.scheduler import Scheduler
    monkeypatch.setattr(filing, "RUNS_DIR", tmp_path / "runs")
    calls = []
    monkeypatch.setattr(R, "run", _runner({"project": None, "effort": "S", "link": None, "twin": None}, calls))
    tid = batches.apply(conn, [{"op": "create", "fields": {"name": "Water the plants"}}], "ui", TODAY)["created"][0]
    now = datetime.now().replace(second=0, microsecond=0)
    Scheduler(cfg, conn, push=lambda *a, **k: None, mailer=None).tick(now)                    # the fixture leaves filing off
    assert calls == [] and filing.queued(conn, now.date()) == [tid]
    on = copy.deepcopy(cfg); C.set_(on, "claude.filing.enabled", True)
    did = Scheduler(on, conn, push=lambda *a, **k: None, mailer=None).tick(now)
    assert [k for k, *_ in calls] == ["filing"] and filing.queued(conn, now.date()) == [] and any(d.startswith("filing: ") for d in did)
    assert [f["value"] for f in filing.chips(conn, {tid})[tid]] == ["S"]
    Scheduler(on, conn, push=lambda *a, **k: None, mailer=None).tick(now)
    assert len(calls) == 1                                                                       # dequeued: never filed twice
    set_setting(conn, filing.QUEUE_KEY, [{"task_id": tid, "day": "2020-01-01"}]); conn.commit()
    assert filing.queued(conn, now.date()) == []
    assert filing.run_queue(conn, on, now) is None and len(calls) == 1 and get_settings(conn)[filing.QUEUE_KEY] == []   # a stale entry is dropped unrun


def test_the_list_carries_filing_chips_until_the_field_is_set_and_a_create_wakes_the_scheduler(cfg, tmp_path):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from factotum import api
    app = api.create_app(cfg, db_path=str(tmp_path / "t.db"))
    c = app.state.conn
    woke = []
    app.state.scheduler = SimpleNamespace(wake=lambda: woke.append(1))
    client = TestClient(app)
    tid = client.post("/api/batches/apply", json={"source": "ui", "changes": [{"op": "create", "fields": {"name": "Water the plants"}}]}).json()["created"][0]
    assert woke == [1]
    filing.apply(c, cfg, tid, {"project": "Personal", "effort": "XS", "link": None, "twin": None}, shown=set()); c.commit()
    t = {x["id"]: x for x in client.get("/api/tasks").json()["tasks"]}[tid]
    assert [(f["field"], f["value"], f["label"]) for f in t["filing"]] == [("project", "Personal", "Personal?"), ("effort", "XS", "XS?")]
    client.post("/api/batches/apply", json={"source": "ui", "changes": [{"task_id": tid, "op": "set", "fields": {"effort": "M"}}]})
    assert woke == [1] and [f["field"] for f in client.get(f"/api/tasks/{tid}").json()["filing"]] == ["project"]


def test_the_filing_switch_is_off_when_its_key_is_missing():
    import yaml
    from conftest import FIXTURE
    assert "claude.filing.enabled" in C.SETTINGS_KEYS and C.DEFAULTS["claude.filing.enabled"] is False and C.value({}, "claude.filing.enabled") is False
    assert "claude.filing.enabled" not in C.PAID_SWITCHES                                       # OLD_DEFAULTS would invent a difference for `effective --as-before`
    for p in (C.EXAMPLE_CONFIG, C.STARTER_CONFIG, FIXTURE):
        assert C.get(yaml.safe_load(p.read_text()), "claude.filing.enabled") is False, p
```

- [ ] **Step 2: Run them** — Expected: FAIL.

- [ ] **Step 3: Implement** the Interfaces above.

- [ ] **Step 4: Run the tests** — `/home/robert/factotum/.venv/bin/pytest tests/test_stage7_filing.py tests/test_stage7_detect.py tests/test_stage3.py -q`; Expected: PASS.

- [ ] **Step 5: Run the whole suite**, all pass.

- [ ] **Step 6: Commit**

```bash
git add factotum/filing.py factotum/suggestions.py factotum/scheduler.py factotum/api.py factotum/config.py config/factotum.example.yaml config/factotum.starter.yaml tests/fixtures/factotum.fixture.yaml tests/test_stage7_filing.py
git commit -m "filing: a task made by hand with only a name is queued and filed by the scheduler when claude.filing.enabled is on (off when the key is missing, kept out of PAID_SWITCHES so effective --as-before invents no difference), at most three a tick, dequeued before it runs, a day's grace; the list and the opened task carry its chips until the field is set; a create wakes the scheduler" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

# Part B — the page and the documents (built in `linked-tasks`, after stage 4b and the in-place-by-hand stage are merged)

Part B uses only stage 4b's page functions: `stageChange(c, label, extra, suffix)`, `stageKey`, `stageJudge(eid, tid, verdict, label)`, `suggestionStaged(eid)`, `nameOf(id)`, `chipsHtml(x)`, `badgesFor(x)`, `linkSuggestionsHtml(item)`, `renderReview`, `suggestionCard(sg, n)`, `detailsHtml(x)`, `taskPageHtml()`, `wire()`, `noSideScroll` / `seed` from `DEV/scenarios/s4b-lib.mjs`. Read the current `app.js` before editing: name functions, never rely on line numbers.

### Task 10: The seed, and filing proposals as dashed chips on the row and the opened task

Implementer: **opus**. UI task: yes. Part B.

**Files:**
- Modify: `factotum/web/app.js` — new section `// ---------- suggestions (stage 7): filing chips ----------`; changes in `chipsHtml`, `badgesFor`, `wire()`.
- Modify: `factotum/web/app.css`.
- Create (outside the repository): `DEV/seed_suggestions.py`, `DEV/scenarios/s7-lib.mjs`, `DEV/scenarios/s7-10-filing.mjs`.

**Interfaces:**
- Consumes: the list / opened-task `filing` key and `rel.suggestions[].signal === 'filing'` (Tasks 4, 9); stage 4b's staging functions.
- Produces:
  - `filingChipsHtml(x) -> string`: for an open task, one chip per `x.filing` entry whose `e:<evidence_id>` is not staged (`suggestionStaged`), then one per `x.rel.suggestions` entry with `signal === 'filing'` not staged. A chip is `<span class="fsug"><button class="chip fsug-ok" data-fsug="<evidence_id>" data-id="<x.id>" …>LABEL</button><button class="fsug-no" data-fsug-no="<evidence_id>" data-id="<x.id>" aria-label="Not this">×</button></span>`. LABEL: a field chip uses its `label` (`Personal?`, `XS?`); a link chip says `part of Task-N?` (`part_of`, dir out), `step: Task-N?` (part_of, dir in), `waits on Task-N?` (waits_on, out), `frees Task-N?` (waits_on, in), `same as Task-N?` (same_as). `title` = `Suggested when you made it · tap to accept` (plus ` · Task-N <name>` for a link). `''` when there are none.
  - `chipsHtml(x)` puts `filingChipsHtml(x)` right after stage 4b's `linkChipsHtml(x)`; so the row (Today, Timeline), the opened row and the phone task page all show them.
  - `badgesFor(x)`'s `suggest` badge leaves out suggestions whose `signal === 'filing'` (they are chips here, not a second dashed badge).
  - Clicks (in `wire()`, `preventDefault`, `stopPropagation`): `[data-fsug]` on a field chip → `stageChange({task_id: id, op: 'set', fields: {[field]: value}}, \`filed: ${field} ${value}\`, {}, 'file-' + field)` and `stageJudge(eid, id, 'applied', 'filing suggestion accepted')`; on a link chip → stage 4b's suggestion change for that kind with `{ev: eid}` (`link`: `{task_id: dir === 'out' ? id : other, op: 'link', fields: {kind, other: dir === 'out' ? other : id}}`; `same_as`: `{task_id: <the end that is not keep>, op: 'merge', fields: {into: keep}}`); toast `Staged · press Apply in the tray`. `[data-fsug-no]` → `stageJudge(eid, id, 'dismissed', 'filing suggestion dismissed')`.
- CSS: `.fsug` inline-flex, gap 2px; `.fsug-ok` uses the dashed suggestion look of stage 4b's `.lb.sus` (surface background, `1px dashed var(--warn)`, warn text, radius 999px, 11px); `.fsug-no` a 20px round muted button. Phone (`max-width: 700px`): `.fsug-ok` and `.fsug-no` `min-height: 32px`, `.fsug-no` 32px wide; chips wrap.

- [ ] **Step 1: Write `DEV/seed_suggestions.py`** (not committed; beside `serve.sh`). Same conventions as `DEV/seed_links.py` (WT on `sys.path`, `FACTOTUM_CONFIG` = `WT/tests/fixtures/factotum.fixture.yaml` when unset, `DEV/factotum-dev.db`, `db.init_db`, `P1, P2` = the first two `config.project_names(cfg)`, names start `"Seed7 · "`, every task change through `batches.apply` source `"ui"`, one commit per step). It makes, in order:
  1. `goal` "Seed7 · Assemble the lab handbook" (P1); `followed` "Seed7 · Send the lab the signed data agreement" (P2, due today+4); `manual` "Seed7 · Ask the lab for the cleaned dataset" (name only: a Manual task with no project and no effort).
  2. Filing proposals with no Claude run: `filing.apply(conn, cfg, manual, {"project": P2, "effort": "S", "link": {"kind": "part_of", "task_id": goal, "why": "the dataset is a piece of the handbook"}, "twin": None}, shown={goal, followed})` → `filing_evidence` (the two `filing` ids) and `filing_link_evidence`.
  3. Two `mail_new` rows (`task_id` NULL, `ref` `seed7-conv-1` / `seed7-conv-2`, `account` = the first account's address or `you@example.edu`, `label` `Seed7 · Dataset format?` / `Seed7 · Re: the data agreement`, `excerpt` `from Lab Manager <lab@example.edu> · to you · unread\n<one sentence>`, `at` now), then `mail_triage.apply(conn, cfg, {"suggest": [{"evidence_id": <first>, "name": "Seed7 · Tell the lab which dataset format", "project": P2, "due": None, "hard": False, "effort": "XS", "context": "The lab manager asked which format to export.", "reply_expected": False, "reason": "A direct question to you.", "link": {"kind": "part_of", "task_id": goal}}], "about": [{"evidence_id": <second>, "task_id": followed, "why": "the lab's reply about the agreement"}], "dismiss": []}, <the two rows>)` → `card_evidence` (the suggestion card's id) and `about_evidence`.
  - Output (printed and written to `DEV/seed_suggestions.json`): `{"goal", "followed", "manual", "filing_evidence": [..], "filing_link_evidence", "card_evidence", "about_evidence", "batches": [..], "today"}`. No flag: print the JSON if it exists and `manual` is open, else seed. `--remove`: `batches.undo` every recorded batch, newest first; a `review` batch judging every recorded evidence id still unjudged as `dismissed`; drop any task whose name starts `Seed7 · ` and is still open through one `drop` batch (the Create of a scenario); delete the JSON. `--fresh`: `--remove` when the JSON exists, then seed.

- [ ] **Step 2: Write `DEV/scenarios/s7-lib.mjs`**: `export function seed7(...flags): object` (runs the seed with the venv python, returns the parsed JSON) and `export { noSideScroll, DEV } from './s4b-lib.mjs'`.

- [ ] **Step 3: Implement** the section, the hooks and the CSS.

- [ ] **Step 4: Run the suite** — `/home/robert/factotum/.venv/bin/pytest -q`, all pass (stage 4b's guard against "blocked" / "dependency" in `factotum/web/` included).

- [ ] **Step 5: Write and run `DEV/scenarios/s7-10-filing.mjs`** (both devices). `DEV/serve.sh restart`, then `S = seed7('--fresh')`.
  - F1: `/` (Today: `manual` is undated, so use `/timeline` and unfold `Undated · backlog` if folded) → shot `s7-10-row`. Expected on BOTH devices: `manual`'s row shows three dashed chips `<P2>?`, `S?` and `part of Task-<goal>?`, each with an ×; no second dashed `part of Task-<goal>?` badge from stage 4b. Phone: the chips wrap inside the row; log the bounding-box height of one chip (≥ 32) and `noSideScroll` (true).
  - F2: press `S?`: the chip disappears; the tray reads `Task-<manual> … filed: effort S` and `… filing suggestion accepted`. Press × on `<P2>?`: it disappears, the tray adds `… filing suggestion dismissed`. Apply; log the `/api/batches/apply` response (no skips). Reload: the row shows `S` as its effort chip and only `part of Task-<goal>?` is left → shot `s7-10-applied`.
  - F3: press `part of Task-<goal>?`; the tray reads `Task-<manual> … is now part of Task-<goal>`; Apply; reload: `manual` carries stage 4b's teal `part of Task-<goal>` badge and no dashed chip → shot `s7-10-linked`.
  - F4: `S = seed7('--fresh')`; open `manual` (desktop: double-click its name, the row expands; phone: tap, the task page) → shot `s7-10-opened`: the chips are on the opened task too (desktop row header; phone page under the title).
  - End with `seed7('--fresh')`.

- [ ] **Step 6: Look at every screenshot, then commit**

```bash
git add factotum/web/app.js factotum/web/app.css
git commit -m "web: what the filing run suggested for a task made by hand shows as dashed chips on its row and the opened task (Personal?, S?, part of Task-N?), one tap stages it with its verdict, x dismisses it; a filing link is a chip, not a second badge" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: Review: "This mail is about Task-N. Follow its thread?", and a mail suggestion's link before Create

Implementer: **opus**. UI task: yes. Part B.

**Files:**
- Modify: `factotum/web/app.js` — `renderReview` (the evidence lines of the card), `suggestionCard`, `fieldSaid`, `wire()`.
- Modify: `factotum/web/app.css`.
- Create (outside the repository): `DEV/scenarios/s7-11-review.mjs`.

**Interfaces:**
- Consumes: Review items' `evidence[]` whose `change.op === 'follow_thread'` (`change.fields`, `change.mail.sender`, `change.mail.subject`), `suggestions[].links` (Task 7); `stageChange`, `stageJudge`, `suggestionStaged`, `nameOf`.
- Produces:
  - `mailAboutHtml(e, tid) -> string`, drawn INSTEAD of stage 4b's "Planner suggests … Accept" line for an evidence row whose `change.op === 'follow_thread'` and not `suggestionStaged(e.id)`: `<div class="extra mailabout"><span class="chip">From your mail</span><span>This mail is about Task-<tid>. Follow its thread? <span class="hint">— <sender> · “<subject>”<, why></span></span><button class="btn small primary" data-follow="<e.id>">Follow its thread</button><button class="btn small" data-notabout="<e.id>">Not about this</button></div>`.
  - `[data-follow]` → `stageChange({task_id: tid, op: 'follow_thread', fields: e.change.fields}, \`follows “${subject}”\`, {ev: e.id}, 'f' + e.id)` and `stageJudge(e.id, tid, 'applied', 'mail card accepted')`; toast `Staged · it follows the thread once you Apply`. `[data-notabout]` → `stageJudge(e.id, tid, 'dismissed', 'not about this mail')`.
  - stage 4b's `fieldSaid(c)` gains a branch for field `thread` (the `changes` row `follow_thread` writes; `new` is `{conversation_id, subject}`): `follows the mail thread “<subject>”`, so the Log and History say it in words (design §16), never as JSON.
  - `suggestionCard(sg, n)`: when `sg.links.length`, a line under the fields, `<div class="sg-link">Will be <word> Task-N <name></div>` with word `a step of` (`part_of`) / `waiting on` (`waits_on`), Task-N a link `<a href="/t/N" data-open-task="N">` (name from `l.other_name`); nothing else on the card changes (Create still calls the existing endpoint; the server links).
- CSS: `.mailabout` like stage 4b's `.extra` with the chip in the accent-soft colour; `.sg-link` 13px, accent text. Phone: both buttons full width, `min-height: 44px`, stacked.

- [ ] **Step 1: Implement** the above.

- [ ] **Step 2: Run the suite** — all pass.

- [ ] **Step 3: Write and run `DEV/scenarios/s7-11-review.mjs`** (both devices; this scenario loads Review). `S = seed7('--fresh')`. Before loading Review, narrow it to the seed with a route (no data is changed): `await page.route('**/api/review**', async r => { const res = await r.fetch(); const j = await res.json(); j.items = j.items.filter(i => i.task.id === S.followed); j.total = j.items.length; j.suggestions = j.suggestions.filter(s => s.id === S.card_evidence); await r.fulfill({ response: res, json: j }); })`.
  - V1: `/review` → shot `s7-11-about`. Expected: `followed`'s card with a line `From your mail` · `This mail is about Task-<followed>. Follow its thread?` · `— Lab Manager <lab@example.edu> · “Seed7 · Re: the data agreement”, the lab's reply about the agreement`, buttons `Follow its thread` and `Not about this`; NO `Planner suggests … Accept` line for it. Phone: the buttons are full width and at least 44 px tall (log one bounding box); `noSideScroll` true.
  - V2: press `Follow its thread`: the line disappears; the tray reads `Task-<followed> … follows “Seed7 · Re: the data agreement”` and `… mail card accepted`. Apply only those two (untick anything else); log the response (no skips) and that `GET /api/tasks/<followed>` lists a `mail_thread` link with ref `seed7-conv-2`. Then Undo from the toast: the link is gone (log it).
  - V3: `seed7('--fresh')`, reload `/review`; press `Not about this`: the tray holds `e:<about_evidence>` (log the keys). Discard all.
  - V4: decide on `followed`'s card with Keep so the queue empties and the suggestion card shows (`From your mail`, `Seed7 · Tell the lab which dataset format`) → shot `s7-11-card`. Expected: a line `Will be a step of Task-<goal> Seed7 · Assemble the lab handbook`. Press Create; log the response and that `GET /api/tasks/<new id>` has a `part_of` relation to `goal`. Discard the staged Keep.
  - End with `seed7('--fresh')`.

- [ ] **Step 4: Look at every screenshot, then commit**

```bash
git add factotum/web/app.js factotum/web/app.css
git commit -m "web: a mail about an open task shows on its Review card as 'This mail is about Task-N. Follow its thread?' with the sender and subject, one tap stages follow_thread with its verdict, Not about this dismisses it; a mail suggestion says which task it will be linked to before Create" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: Settings: the filing switch and the number of suggested links a day

Implementer: **sonnet**. UI task: yes. Part B.

**Files:**
- Modify: `factotum/web/app.js` — `renderSettings` only.
- Create (outside the repository): `DEV/scenarios/s7-12-settings.mjs`.

**Interfaces:**
- Consumes: `renderSettings`'s helpers `f(key, label, hint, control)`, `bool(key)`, `num(key, min, max)`; `GET /api/settings`'s `effective` (both keys are in `SETTINGS_KEYS` since Tasks 4 and 9).
- Produces two controls, wording exact:
  - In `<h3>Review and lifecycle</h3>`, after the `lifecycle.review_cap` line: `${f('links.suggestions_per_review', 'Suggested links', 'At most this many suggested links a day, in Review and as dashed badges; the rest wait for another day. 0 shows none.', num('links.suggestions_per_review', 0, 20) + ' a day')}`.
  - In `<h3>Claude</h3>`, after the `claude.summary.auto` line: `${f('claude.filing.enabled', 'Filing a task you add', 'When you add a task with only a name, one small Claude run with no tools suggests its project, effort, a likely link and a likely twin, as dashed chips you accept with one tap. It never changes what you typed. Its cost is in the Log.', bool('claude.filing.enabled'))}`.

- [ ] **Step 1: Implement** the two lines.

- [ ] **Step 2: Run the suite** — all pass.

- [ ] **Step 3: Write and run `DEV/scenarios/s7-12-settings.mjs`** (both devices).
  - S1: `/settings` → shots `s7-12-review` (scrolled to `#s-links\.suggestions_per_review`) and `s7-12-claude` (scrolled to `#s-claude\.filing\.enabled`). Expected: `Suggested links` reads `5 a day`; `Filing a task you add` is unticked (the fixture's value). Phone: no sideways scroll; the hint wraps under the label.
  - S2: tick the filing box; log the `PUT /api/settings` body (only `claude.filing.enabled: true`); reload: still ticked; untick it (log the second body). Set `Suggested links` to `3`, reload, it reads 3; set it back to 5.
  - S3: log `GET /api/settings` → `effective['claude.filing.enabled'] === false`, `effective['links.suggestions_per_review'] === 5` at the end.

- [ ] **Step 4: Look at every screenshot, then commit**

```bash
git add factotum/web/app.js
git commit -m "web: Settings has the filing switch (Claude) and the number of suggested links offered a day (Review and lifecycle)" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 13: The stage's documents

Implementer: **sonnet**. UI task: no. Part B.

**Files:**
- Modify: `SPEC.md`, `CLAUDE.md`, `docs/SETUP.md`

- [ ] **Step 1: `SPEC.md`.**
  - §4 (data model): the evidence kinds `link_suggestion` (ref `{kind, a, b, why, signal}`; signals `twin`, `thread`, `text`, `planner`, `recording`, `filing`, `import`) and `filing` (ref `{field, value}`); the settings rows `state.links.session`, `state.links.offered`, `state.filing.pending`; the batch op `follow_thread`.
  - §6 Plaud: a recording's `links` (stated by the owner → in the batch with the sentence; inferred or a merge → a card; the finished-task reference list; a re-sync never re-adds what the owner removed). §6 "Tasks suggested from mail": `link` on a suggestion (made at Create), `about` cards and `follow_thread`.
  - §8.1, §8.2, §8.5: the contracts as built (the JSON added, what Python enforces, the planner's link ops always staged, `LINK_CAP`). A new §8.6 `filing` (`factotum/filing.py`, `prompts/filing.md`): when it is queued, no tools, what it stores, `claude.filing.enabled` off when missing, `file-task --dry-run`, the measured cost (the controller's figure from the real dry run, or "not yet measured" until then).
  - §9: the filing chips, the Review mail card, the two Settings controls; a paragraph **Suggested links (stage 7, 2026-11-11)**: the three detectors and their exact rules (twins, the both-Canvas rule and word disambiguation; threads 2–4; Task-N in text), one card per pair ever, the daily cap and its rotation, the filing exemption.
  - §10 (scheduler): the `link_suggestions` job (every 30 min) and the `filing` job.
  - §13 (cost): filing.

- [ ] **Step 2: `CLAUDE.md`.** Phase status: one sentence for stage 7 (2026-11-11). Where things live, rows: "suggested links: the detectors, one card per pair, the daily cap" → design §10.1, SPEC §9 → `factotum/suggestions.py` (`why_not`, `suggest_pair`, `twins`, `threads`, `refs_in`, `text_refs`, `sweep`, `at_create`, `session`), `relations.twin_pairs`, `relations.shown_ids`; "links proposed by the planner, a recording, mail" → design §10.2 → `planner.apply` (`LINK_OPS`), `sync_file.apply_result` (`links`, `closed_rows`), `mail_triage.apply` (`about`), `batches` (`follow_thread`); "filing a task made by hand" → design §10.3, SPEC §8.6 → `factotum/filing.py`, `prompts/filing.md`, `scheduler._suggestions`; "filing chips, the mail card, Settings" → `web/app.js` (`filingChipsHtml`, `mailAboutHtml`). Commands: `links-check`, `sweep links`, `file-task ID [--dry-run]`. Conventions, each dated 2026-11-11:
  - **One card per unordered pair, ever**: every stage 7 writer goes through `suggestions.suggest_pair` (`why_not`); `relations.suggest` is kind-level and is for the import only. A pair the owner dismissed, linked or unlinked is never suggested again.
  - **Two Canvas rows are twins only when their names are one item's**, and a row with several twin candidates keeps the one it shares a title word with, or none; a course week (Quiz 5, Discussion 5) is three items.
  - **The daily cap lives in `relations.annotate`** (`shown_ids`): Review, badges, the brief and `task_get` read the same day's set; only `suggestions.session` writes it; a `filing` suggestion is exempt. A new reader of suggestions goes through `annotate`, never `pending_suggestions`, unless it judges them.
  - **A suggestion's `why` never quotes task text** (the owner's text may say "blocked").
  - **A recording's stated link rides in its batch; a re-synced recording never re-adds a link for a pair with any `relations` row**, and its inferred links are cards.
  - **`filing` evidence is not the planner's and not a Review decision's**, like `mail_new` and `link_suggestion`.
  - **`follow_thread` is a batch op whose undo deletes the `links` row it added**; connectors cannot send it.
  - **`claude.filing.enabled` is not in `PAID_SWITCHES`** (that tuple builds `OLD_DEFAULTS`); a new paid switch that did not exist before 2026-11-10 stays out of it and gets its own default test.
  - **A filing run is dequeued before it runs**: a failed one is logged and not retried; `file-task` runs it again by hand.

- [ ] **Step 3: `docs/SETUP.md`.** A new section **§19 Suggested links and filing (stage 7)**: what appears and where; `links.suggestions_per_review` (Settings → Review and lifecycle); `claude.filing.enabled` off until switched on in Settings → Claude, its model and budget (`claude.models.filing`, `claude.budgets_usd.filing`), the cost; trying one filing run on a copy of the database with `file-task ID --dry-run` (copy with the backup API, never `cp`); `links-check` before the first `sweep links` on an existing database (the first pass seeds the existing "Task-N" references as cards, a day's worth at a time).

- [ ] **Step 4: Run the suite** — all pass.

- [ ] **Step 5: Commit**

```bash
git add SPEC.md CLAUDE.md docs/SETUP.md
git commit -m "docs: stage 7, suggestions (the three detectors and their rules, one card per pair, the daily cap, links from the planner, recordings and mail, the follow card, filing a task made by hand) and the gotchas learned" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Stage check (by the controller)

**Part A, without a browser (in `.worktrees/suggestions`, after Task 9):**

1. `/home/robert/factotum/.venv/bin/pytest -q` → all pass; `grep -rniE "\bblocked\b|dependenc" prompts/ skills/` → nothing.
2. Copy the real database (it is in WAL mode, so the backup API): `DEV=/tmp/claude-1000/-home-robert-factotum/85fa6951-ca9c-416e-9691-1a412f0fadea/scratchpad/dev; /home/robert/factotum/.venv/bin/python -c "import sqlite3; s = sqlite3.connect('$DEV/links-source.db'); d = sqlite3.connect('$DEV/s7-detect.db'); s.backup(d); d.close(); s.close()"`.
3. `cd /home/robert/factotum/.worktrees/suggestions && export FACTOTUM_DB=$DEV/s7-detect.db FACTOTUM_CONFIG=$PWD/tests/fixtures/factotum.fixture.yaml`, then `/home/robert/factotum/.venv/bin/python -m factotum links-check > $DEV/s7-check-1.txt`. Read it: count per signal; every twin is a Canvas row against a non-Canvas row of the same number (or two identical Canvas names); no twin between two different Canvas items of one week; every text line names a Task-N that appears in that task's text in the "Task-N" form; no thread group over 4; no `why` containing "blocked". Note how many would be written.
4. `python -c "import sqlite3; c = sqlite3.connect('$DEV/s7-detect.db'); print(c.execute('SELECT COUNT(*) FROM relations').fetchone(), c.execute(\"SELECT COUNT(*) FROM evidence WHERE kind = 'link_suggestion'\").fetchone())"` → before.
5. `python -m factotum sweep links` (prints `<n> suggested: …`), then step 4 again: `relations` unchanged, `link_suggestion` grew by the "would write" count. `python -m factotum links-check` again: no line says `would write`.
6. `python -c "from datetime import date; from factotum import suggestions; from factotum.db import connect; c = connect('$DEV/s7-detect.db'); print(suggestions.session(c, date.today(), 5)); c.commit()"` → at most 5 ids. Then `python -c "from factotum import relations; from factotum.db import connect, rows; c = connect('$DEV/s7-detect.db'); ts = relations.annotate(c, rows(c, \"SELECT * FROM tasks WHERE status = 'open'\")); print(len({s['evidence_id'] for t in ts for s in t['rel']['suggestions']}))"` → the same number (5 or fewer): the badges and Review read the day's set only.
7. `python -m factotum effective claude.filing.enabled links.suggestions_per_review` → `false … file` and `5 … file` (both in the fixture); with `--as-before` the same values and origins.

**Part B, rendered (in `.worktrees/linked-tasks`, after Task 12):**

1. `DEV/serve.sh restart`; `/home/robert/factotum/.venv/bin/python DEV/seed_suggestions.py --fresh` twice (new ids the second time), then `--remove` (no `Seed7 ·` task open), then `--fresh`.
2. Run `s7-10-filing`, `s7-11-review`, `s7-12-settings` with `--device both`; open every PNG under `DEV/../shots/s7-*` at 1280x900 and 390x844: dashed filing chips with × on the row (Timeline) and the opened task / task page, no duplicate dashed badge, chips at least 32 px tall on the phone; the Review mail card with sender and subject and the two buttons full width on the phone; the mail suggestion card's `Will be a step of Task-N …` line; the two Settings controls; `noSideScroll` true on every phone shot; no console errors.
3. Re-run stage 4b's `s4b-3-badges` and `s4b-8-review` scenarios: unchanged results (stage 7 must not disturb them).

**One real filing run on a database copy (the controller only, by hand; about a cent with haiku):**

```bash
DEV=/tmp/claude-1000/-home-robert-factotum/85fa6951-ca9c-416e-9691-1a412f0fadea/scratchpad/dev
cd /home/robert/factotum/.worktrees/suggestions
/home/robert/factotum/.venv/bin/python -c "import sqlite3; s = sqlite3.connect('$DEV/links-source.db'); d = sqlite3.connect('$DEV/s7-filing.db'); s.backup(d); d.close(); s.close()"
export FACTOTUM_DB=$DEV/s7-filing.db FACTOTUM_CONFIG=$PWD/tests/fixtures/factotum.fixture.yaml
/home/robert/factotum/.venv/bin/python -c "from datetime import date; from factotum import batches; from factotum.db import connect, init_db; c = connect('$DEV/s7-filing.db'); init_db(c); print(batches.apply(c, [{'op': 'create', 'fields': {'name': 'Ask the lab for the cleaned dataset'}}], 'ui', date.today())['created'])"
/home/robert/factotum/.venv/bin/python -m factotum file-task <the id printed above> --dry-run
/home/robert/factotum/.venv/bin/python -m factotum file-task <the same id>
/home/robert/factotum/.venv/bin/python -c "import sqlite3; c = sqlite3.connect('$DEV/s7-filing.db'); print(c.execute(\"SELECT kind, label FROM evidence WHERE task_id = <id> AND kind IN ('filing', 'link_suggestion')\").fetchall(), c.execute(\"SELECT cost_usd, duration_ms, summary FROM runs WHERE kind = 'filing' ORDER BY id DESC LIMIT 2\").fetchall())"
```

The dry run prints the JSON and the cost; the second run stores the proposals; the last line shows them and the measured cost, which goes into SPEC §8.6 and §13.
