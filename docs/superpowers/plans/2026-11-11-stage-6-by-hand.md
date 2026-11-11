# Stage 6: In Place, By Hand — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The conversation with Claude lives where the question is asked (a Review card, under the Today box, an opened task) with every proposal listed in place and staged together; a task can be added by hand in one line (or a phone sheet) and exists the moment Enter is pressed; an opened task is its own editor, links are made and removed by hand, and any task, open or closed, can be found.

**Architecture:** Part A (Python) adds one module, `factotum/by_hand.py` (the add preview with database checks, adding at once with an idempotency key, search), a pure grammar `commands.parse_add`, conversation places on `chat_threads` (`origin`, `task_id`) with a per-place lookup and a busy guard on the turn route, proposals that can be staged together (a `link` to a sibling create's `ref`; a chat create staged in the tray as `from_chat`, resolved by the server from the stored proposal), and a `split` canned prompt. Part B (the page, `factotum/web/` only) adds one in-place conversation component used in three places, the New task line and phone sheet, the editor in the opened task, linking by hand, and a finder, all under `// ---------- … (stage 6) ----------` section comments in the single `app.js`.

**Tech Stack:** Python 3 + SQLite + FastAPI, pytest (offline, fake runners); vanilla JS/CSS, no build step, no JS tests; Playwright scenarios from the dev harness for rendered checks.

**Spec:** `docs/superpowers/specs/2026-11-10-linked-tasks-design.md` §12.6, §12.7, §12.8, §12.9 (binding to the word), with §2 (goals 6, 7; anti-goals), §3, §4, §5.2, §6, §11, §13.2, §16, §17, §18. Read `CLAUDE.md` in full. Part B builds on the stage 4b plan `docs/superpowers/plans/2026-11-11-stage-4b-links-page.md` (in `linked-tasks`; read it all: its `stageKey`, `stageChange`, `stageExtra(…, extra)`, `changeWords`, `linkPredicate`, `textExpect`, `suggestionStaged`, `undoBatch`, `undoSaid`, `nameOf`, `goTask`, `openPage`, `detailCache`, `loadDetail`, `swapDetail`, `detailsHtml`, `linkedListHtml`, `linkedGroups`/`linkedHtml`, `defaultKeep`, `taskPageHtml` and its `.pagebar`, `typingNow`). Mockups (read as HTML for wording, colours, layout): `/tmp/claude-1000/-home-robert-factotum/85fa6951-ca9c-416e-9691-1a412f0fadea/scratchpad/canvas/project/` `Review.dc.html` (frame 4), `PhoneReview.dc.html` (frame 10), `Create.dc.html` (frame 6), `PhoneNew.dc.html` (frame 11), `Edit.dc.html` (frame 7), `PhoneToday.dc.html` (frame 8). The mockups say "unblocked": never copy it.

**Two runs.** Part A (Tasks 1–7: Python, prompts, CLI; nothing under `factotum/web/`) is built in `/home/robert/factotum/.worktrees/byhand` (branch `byhand`, from af49c98) while other stages build; it must be complete and green on its own. Part B (Tasks 8–15: everything under `factotum/web/`, the dev seed, the documents) is built in `/home/robert/factotum/.worktrees/linked-tasks` after the controller has merged 4b, stage 5 Part A+B, stage 7 Part A and `byhand`. Part B depends only on stage 4b's page functions and on what Part A ships; it never calls a function stage 5 or 7 added to the page.

## Global Constraints

- Work only in the worktree the controller names (WT) and never touch the main checkout or another worktree. The controller names WT per run (Part A: `/home/robert/factotum/.worktrees/byhand`, branch `byhand`; Part B: `/home/robert/factotum/.worktrees/linked-tasks`, branch `linked-tasks`). Every path below is relative to WT. Never read, write or run git in `/home/robert/factotum` outside `.worktrees/`.
- Python is `/home/robert/factotum/.venv/bin/python`, tests `/home/robert/factotum/.venv/bin/pytest`, run from WT.
- Do not install packages.
- NO real Claude runs by any build agent, no scheduler, no sweeps, no sudo, no `systemctl`. Never run `python -m factotum chat`, `chat-check`, `claude-check`, `planner`, `sync-file`; no test may reach `factotum.claude.runner.run` unpatched. A scenario presses Ask, Send with text that is neither a command nor an add line, a canned button ("Search related emails", "Draft reply", "Ask Claude what else is related", "Split into steps", "Make them tasks", "Let Claude file it") ONLY after `stubTurn` (Task 8) is installed; it never presses "Open in Mailbird" or "Show the message in Mailbird".
- Never open `config/factotum.yaml`, `.env` or `/home/robert/factotum/data`.
- Commit rules from CLAUDE.md: small commits on the branch, pytest green at every commit (the WHOLE suite, `/home/robert/factotum/.venv/bin/pytest -q`), the commit-message trailer exactly as CLAUDE.md gives it (`Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`, added as a second `-m`; the commands below show the subject only), never push. Message style: `area: what changed and why`.
- A new task exists on Enter and its Undo drops it; every other edit stages and applies through the tray.
- Nothing typed is ever silently dropped (an unrecognised word stays in the name; a refused part is shown with its reason).
- Desktop behaviour that exists today does not change except where a task says so.
- Every surface is checked at 1280 px and at 390 px.
- The design's vocabulary (§4): "blocked" and "dependency" never appear in anything the page shows (4b's `test_the_page_never_says_blocked_or_dependency` and 4a's prompt guard stay green).
- `/api` never answers 401, 403 or a redirect; every state-changing `/api` call carries `Content-Type: application/json` (the page's `api()` sets it; tests pass `json=`). The API never runs Claude except a chat turn the user sent.
- Page rules from CLAUDE.md: what is typed lives in `draft`; a new text box needs a `draft` entry, `typing()` wiring (or an `oninput` that writes `draft`) and its id accepted by `isDraftBox` (Task 8) so `render()` restores focus and caret; a folded section renders no rows; on a phone the task page is rendered into `#panel` (`ui.page`); addresses are real paths (`/t/<id>`, `/<tab>`).
- New Python tests: `tests/test_stage6.py` (Tasks 1–3, 7) and `tests/test_stage6_chat.py` (Tasks 4–6), one `# --- Task N: …` comment per task.
- **How a Part B (UI) task is verified** (no JS tests: render and look). DEV = `/tmp/claude-1000/-home-robert-factotum/85fa6951-ca9c-416e-9691-1a412f0fadea/scratchpad/dev`.
  - Dev instance on a COPY of a real database, no scheduler, test fixture config, http://127.0.0.1:8751: `DEV/serve.sh restart` (after any Python change; `factotum/web/` is served straight from WT). `DB=<path> DEV/serve.sh restart` uses another file (a fresh path is an empty database).
  - Data: `DEV/seed_byhand.py` (Task 8) on top of 4b's `DEV/seed_links.py`; every scenario starts with `S = seed6('--fresh')` and ends with `seed6('--fresh')`, and reads ids from `S` (never hard-coded). Every task a scenario adds is named `Seed6 · …`.
  - Browser: `cd DEV && node pw.mjs scenarios/<name>.mjs --device both`. A scenario is `export default async ({ page, context, shot, base, device, log }) => { … }`; `shot('name')` saves `DEV/../shots/<name>-<device>.png`; console errors, failed requests and HTTP ≥ 400 are printed (an expected one is named in the task). Scenarios are `DEV/scenarios/s6-<task>-<name>.mjs` (not committed).
  - A chat turn is STUBBED with `stubTurn` (Task 8): it intercepts `POST /api/chat/turn` and answers with the event stream the server sends (shape below). Nothing else is stubbed.
  - The implementer runs each scenario for both devices, LOOKS at every screenshot (the Read tool shows images), compares it with the named frame, and reports what was seen with the `log` lines. Every phone shot logs `noSideScroll(page)` (must be `true`). A scenario may press Apply / Undo on the dev database.
- **The turn's event stream** (what `POST /api/chat/turn` sends, `text/event-stream`, each event `data: <json>\n\n`, a `: keepalive\n\n` comment every 20 s of silence): `{"type":"meta","thread_id":T,"message_id":<user message id>}`; any number of `{"type":"status","text":"Thinking…"}` / `{"type":"status","text":"Reading a task · 30 · task_get","tool":"task_get"}` / `{"type":"status","text":"Factotum's tools did not connect (failed)","warn":true}` / `{"type":"text","text":"…"}` / `{"type":"result","cost_usd":0.21,"ok":true}`; then exactly one of `{"type":"done","result":R}` or `{"type":"error","error":"…","result"?:R}`. `R` = `{ok, thread_id, message_id, user_message_id, reply_md, proposals, drafts, rejected, cost_usd, duration_ms, session_id}` plus, from Task 4, `read`, `named`, `origin`, `task_id`. A proposal is `{op, task_id, fields, label, evidence, applied}` (a create has `task_id: null`; an applied `link_thread` has `applied: true`); a draft is `{kind, subject, to, account, conversation_id?, draft_message_id?, warn?}`; `rejected` is a list of strings.

## Decisions this plan makes

- **The `add` grammar is a separate function, never a tray command.** `commands.parse` stays as it is (`commands.parse("add …")` is `None`, so the old `/api/commands` path can never stage a create); `commands.parse_add` reads an add line. The verb is `add`, `new` or a leading `+`; the New task line and the phone sheet need no verb.
- **Where parts are read.** `#project`, `~effort` and a lone `!` are read anywhere after the verb. A date phrase, `after N` and `under N` are read only in the trailing run (from the end of the line back to the first word that is not a part), so "Plan monday meeting fri" keeps "monday" in the name and "Call Sun Life" keeps "Sun". A date phrase may be preceded by `by` or `due`, which go with it. When a kind appears twice, the last one wins and the earlier stays in the name.
- **A refused part stays in the name**, and the preview and the response say why ("… ; kept in the name"). Enter still adds the task; the toast repeats the refusal. So nothing typed is lost and nothing is silently left out.
- **The project rule:** compare with every configured project name after lowercasing and removing everything that is not a letter or a digit; an exact match wins; otherwise a unique prefix of at least 2 characters; otherwise refused (`no project matches #x`, or `#x could be A or B`). `#` followed only by digits is not a project (it stays in the name; it is not a task reference either).
- **Date words are an exact list** (today, tomorrow, tmrw, weekday names and their usual short forms, `next <weekday>`, `next week`, `<month> <day>`, ISO `YYYY-MM-DD`), resolved by `dates.resolve_when`; `fri!` is a date and `!`. An impossible date (`feb 30`) is refused.
- **Preview is `GET /api/add/preview?text=`**: the parse plus a few `SELECT`s by primary key, no scoring, no write; the page calls it on a 250 ms typing pause. `/api/commands` is not reused: it answers "is this a tray command", and an add is not one.
- **Adding is `POST /api/add`** with `{text}` (a line) or `{fields}` (the phone sheet, Add a step, Duplicate) and a client `key`. One `batches.apply` with source `ui` (so the task's Source is `Manual`, `batches.created_source`), `source_key = add:<key>`; the same key again answers `already: true` with the first task, under a module lock. Two quick Enters, or a retried request, make one task. Undo is the batch's undo (it drops the row). Duplicate is the `{fields}` path from the page; no other server helper.
- **A conversation's place** is `chat_threads.origin` (`review`, `today`, `task`; NULL for a Chat-tab thread) and `chat_threads.task_id`. A Review card and an opened task share one thread per task (the newest thread with that `task_id` and origin `review` or `task`); the Today box continues the newest `today` thread whose last message is under 12 hours old; `fresh: true` starts a new one. The turn route resolves the thread under a lock and answers 409 while that thread has a turn running.
- **Everything a reply proposes can be staged together.** A `link` may name, as `other`, the `ref` of a `create` in the same reply (so an existing task can wait on a new one). A chat create is staged in the tray as `{op: "create", from_chat: {message_id, index}}`; the server replaces its fields with the stored, validated proposal (the page cannot forge one), keys it `chat:<message>:<index>` (so Create and Stage never make two), labels it Source `Chat`, and marks the proposal applied. A Review decision and the staged proposals are one Apply, one Undo.
- **One canned prompt serves "Split into steps" and "Make them tasks"**: `split`, which turns listed steps into tasks when the text lists them and drafts two to six otherwise, as creates with `ref`s, `part_of` the goal and `waits_on` only where order is real.
- **Search is `GET /api/search?q=&limit=`** over every status: a number (`12`, `Task-12`, `#12`) matches that id first, then ids starting with it; words must all appear in the name or project. Open first, then closed newest first; 12 by default, at most 30.
- **On the page, one conversation component** (`placeHtml`) everywhere; proposals are listed in the order Claude gave them (refs read top-down) with a hint on what kind each is; Review's button reads "Stage all N with this decision", elsewhere "Stage N". A create row also has Create (at once, the existing route) and Edit first (Task 10: it fills the New task line). One turn runs at a time on the page; a follow-up while one runs is refused with the text kept.
- **Typing plain text into the box on Today, Timeline or Projects** starts or continues the `today` conversation, drawn under the box; the tab does not change. The Chat tab's own box keeps today's behaviour. The canned buttons in an opened task now answer in that task (they used to switch to Chat).
- **The editor's "who" field** is one field, as frame 7 draws it, with a choice `is for` / `waiting on`: it writes `counterpart` or `waiting_on`; changing the choice moves the text and clears the other column.
- **"Add a step"** is offered on every open task that is not itself a step (adding the first step makes it a goal). "Make them tasks" shows on a Linked-view goal whose text lists at least two steps (lines starting with `-`, `*`, `•`, or `1.` / `1)`).
- **On a phone, one thing at the bottom:** the tray when changes are staged, the short bar while rows are selected, the task page's pinned bar on a page, Review's pinned decisions on Review, otherwise the round New task button (list tabs only). While a text box in `#panel` or the sheet has focus on a phone, the tray, the pinned bars and the round button are hidden (`body.kb-open`) so nothing covers the box, and `--kb` (the keyboard's height from `visualViewport`) pads the sheet and the Review card.
- **The empty Today tab** says how to add a first task (and no longer tells a newcomer to run the Notion import first).

## Review Focus

1. **Text typed into the reply box or the New task line while the panel re-renders** (a turn's status re-renders every event; a reload on return): the text and caret survive — Task 8 scenario V2 (`#rv-ask` keeps "and the form?" through the live re-renders), Task 13 E6 (an editor field through a return).
2. **An add line that looks like another command or carries a number that is not a task** (`add done 12`, `Pay the 12 invoices after 99999`): it is a task named as typed; the unknown task is refused and kept in the name; `/api/commands` never stages it — Task 1 `test_add_text_that_looks_like_a_command_or_a_number_is_a_name`, Task 2 `test_the_preview_checks_the_tasks_it_names`, Task 10 N2.
3. **Two quick Enters, or a retried request**: one task — Task 3 `test_two_quick_enters_make_one_task`, Task 10 N3.
4. **A follow-up sent while the first turn is still running**: refused (409 on the server; on the page a toast, the text kept, one request) — Task 4 `test_the_turn_route_finds_the_place_and_refuses_a_second_turn_while_one_runs`, Task 8 V2.
5. **A staged edit made stale by a ripple or another device**: Apply skips it with its reason and keeps the newer text — Task 13 E3 (Python side pinned by 4a's `expect`).
6. **The phone keyboard covering the box or the pinned buttons**: focusing a box hides the tray and pinned bars and keeps the box in view at a keyboard-sized viewport — Task 8 V4 (Review reply), Task 12 P3 (the sheet).

---

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `factotum/commands.py` | `ADD_RE`, `parse_add`, `link_refusal` (the add grammar, pure) | 1 |
| `factotum/by_hand.py` (new) | `lookup`, `preview`, `add` (with its lock and key), `search` | 2, 3, 7 |
| `factotum/batches.py` | `_create` honours a Python-only `created_source` | 5 |
| `factotum/db.py` | `MIGRATIONS`: `chat_threads.origin`, `chat_threads.task_id`, `chat_messages.read_json` | 4 |
| `factotum/chat.py` | `ORIGINS`, `PLACE_TEXT`, `new_thread(…, origin, task_id)`, `place_thread`, `named`, `proposal_ids`, `read`/`named` on a turn, ref links, `resolve_staged`, `mark_staged`, `CANNED["split"]` | 4, 5, 6 |
| `factotum/api.py` | `/api/add/preview`, `/api/add`, `/api/search`, `/api/chat/place`; the turn route's place, lock and 409; `apply_batch` resolves `from_chat` | 2, 3, 4, 5, 7 |
| `factotum/__main__.py` | `add TEXT [--dry-run]`, `find Q` | 3, 7 |
| `prompts/chat-split.md` (new), `prompts/chat-turn.md` | the split prompt; a link to a create's ref | 5, 6 |
| `tests/test_stage6.py`, `tests/test_stage6_chat.py` (new) | Part A tests | 1–7 |
| `factotum/web/app.js`, `app.css`, `index.html` | the conversation in place, New task (line and sheet), finder, editor, linking by hand | 8–14 |
| `DEV/seed_byhand.py`, `DEV/scenarios/s6-lib.mjs`, `DEV/scenarios/s6-*.mjs` (outside the repository) | seed, stub and rendered checks | 8–14 |
| `SPEC.md`, `CLAUDE.md` | decisions and gotchas | 15 |

---

# Part A — Python, prompts, CLI (worktree `byhand`)

### Task 1: The add line's grammar

Implementer: **opus**. Part A. UI task: no.

**Files:**
- Modify: `factotum/commands.py`
- Create: `tests/test_stage6.py`

**Interfaces:**
- Consumes: `dates.resolve_when(text, today) -> date | None` (it raises `ValueError` for an impossible day: catch it).
- Produces (in `factotum/commands.py`; `parse` is unchanged):
  - `ADD_RE = re.compile(r"^\s*(?:(?:add|new)\s+|\+\s*)", re.I)`.
  - `link_refusal(kind: str, n: int, task: dict | None, under: int | None = None) -> str | None` — `task` is `{"id", "name", "status", "goal"}` (`goal` = the task's own goal id or None) or None for a missing task. `waits_on`: None → `no Task-N`; status not `open` → `Task-N is already <status>`; `n == under` → `Task-N is its goal; a task cannot wait on its own goal`. `part_of`: None → `no Task-N`; `goal` set → `Task-N is a step of Task-<goal>; a step cannot have steps`. Else None.
  - `parse_add(text: str, today: date, projects: list[str], efforts: list[str], *, verb: bool = True, task: Callable[[int], dict | None] | None = None) -> dict | None`: `None` when `verb` is True and `ADD_RE` does not match; with `verb=False` a leading verb is stripped when present. Returns `{"name": str, "fields": {"name", "due"?, "hard"?, "project"?, "effort"?}, "after": int | None, "under": int | None, "parts": [part…], "refused": [{"words", "why"}]}`; a part is `{"kind", "words", "value", "label", "legend"}` (plus `"other": {"id","name","status"}` for a link part when `task` is given), in the order the words appear. With `task=None` the task numbers are not checked.
  - Part kinds, labels and legends (exact): `due` → label `%a %b <day>` (`Fri Nov 6`), legend `is the due date`, value ISO date; `hard` → label `hard deadline`, legend `makes it a hard deadline`, value `1`; `project` → the configured name, `is the project`; `effort` → the configured effort, `is the effort`; `waits_on` → `waits on Task-N`, `makes it wait on Task-N`, value N; `part_of` → `part of Task-N` (plus ` (<status>)` when the looked-up goal is closed), `makes it a step of Task-N`, value N.
  - Refusal reasons (each followed by `; kept in the name`): `no project matches #x`; `#x could be A or B` (up to three names, `A, B or C`); `~X is not an effort (XS, S, M, L)` (the configured list); `! needs a due date`; `<words> is not a date`; `a second due date` / `a second project` / `a second effort` / `a second after` / `a second under`; any `link_refusal`. An empty name: `name` `""` and refused `[{"words": "", "why": "a task needs a name"}]` (no suffix).

- [ ] **Step 1: Write the failing tests** in `tests/test_stage6.py`

```python
"""Stage 6, in place and by hand: the add line, adding at once, finding any task."""
import json
import threading
from datetime import date

from fastapi.testclient import TestClient

from factotum import api, batches, commands
from factotum.db import connect, init_db, one, rows
from conftest import FIXTURE, TODAY                          # TODAY is Sunday 2026-11-01
from stage4_helpers import active_rels, link, mk

PROJECTS = ["BDA M.S.", "BDA 696", "COMPE 510", "Plaud Sync", "Personal", "Hardware Lab"]
EFFORTS = ["XS", "S", "M", "L"]
P = lambda s, **k: commands.parse_add(s, TODAY, PROJECTS, EFFORTS, **k)      # noqa: E731
K = "; kept in the name"


# --- Task 1: the add line's grammar

def test_the_designs_example_line():
    r = P("add Email the lab the dataset fri #hardware ~S after 120")
    assert r["name"] == "Email the lab the dataset" and r["refused"] == [] and r["after"] == 120 and r["under"] is None
    assert r["fields"] == {"name": "Email the lab the dataset", "due": "2026-11-06", "project": "Hardware Lab", "effort": "S"}
    assert [p["kind"] for p in r["parts"]] == ["due", "project", "effort", "waits_on"]
    assert r["parts"][0] == {"kind": "due", "words": "fri", "value": "2026-11-06", "label": "Fri Nov 6", "legend": "is the due date"}
    assert (r["parts"][3]["label"], r["parts"][3]["legend"]) == ("waits on Task-120", "makes it wait on Task-120")


def test_the_verbs_and_the_line_without_one():
    assert P("new Call mom")["name"] == P("+Call mom")["name"] == P("+ Call mom")["name"] == "Call mom"
    assert P("Call mom") is None and P("addition to the list") is None and P("add") is None
    assert P("Call mom", verb=False)["name"] == "Call mom" and P("add Call mom", verb=False)["name"] == "Call mom"
    r = P("add #personal ~S")
    assert r["name"] == "" and r["refused"] == [{"words": "", "why": "a task needs a name"}]


def test_add_text_that_looks_like_a_command_or_a_number_is_a_name():
    assert P("add done 12")["name"] == "done 12" and commands.parse("add done 12") is None
    assert commands.parse("add Email the lab fri") is None and commands.parse("new 12") is None
    for line, name in (("add Plan monday meeting fri", "Plan monday meeting"), ("add Call Sun Life", "Call Sun Life"),
                       ("add Pay the 12 invoices", "Pay the 12 invoices"), ("add Fix issue #12", "Fix issue #12"),
                       ("add Read chapter 3 after lunch", "Read chapter 3 after lunch")):
        assert P(line)["name"] == name and P(line)["refused"] == [], line
    assert P("add Plan monday meeting fri")["fields"]["due"] == "2026-11-06" and "due" not in P("add Call Sun Life")["fields"]


def test_hard_deadlines_and_date_phrases():
    r = P("add Submit the form fri!")
    assert r["fields"]["due"] == "2026-11-06" and r["fields"]["hard"] == 1 and [p["kind"] for p in r["parts"]] == ["due", "hard"]
    assert P("add Submit the form tomorrow !")["fields"] == {"name": "Submit the form", "due": "2026-11-02", "hard": 1}
    r = P("add Fix it !")
    assert r["name"] == "Fix it !" and "hard" not in r["fields"] and r["refused"] == [{"words": "!", "why": "! needs a due date" + K}]
    r = P("add Send the form by nov 13")
    assert r["name"] == "Send the form" and r["fields"]["due"] == "2026-11-13" and r["parts"][0]["words"] == "by nov 13"
    r = P("add Call the office due next fri")
    assert r["name"] == "Call the office" and r["fields"]["due"] == "2026-11-13" and r["parts"][0]["words"] == "due next fri"
    for bad in ("feb 30", "2026-13-01"):
        r = P(f"add Pay rent {bad}")
        assert r["name"] == f"Pay rent {bad}" and r["refused"] == [{"words": bad, "why": f"{bad} is not a date" + K}]


def test_projects_and_efforts_match_or_say_why():
    assert P("add Read #BDA696")["fields"]["project"] == "BDA 696"
    assert P("add Read #compe-510")["fields"]["project"] == "COMPE 510"
    assert P("add Read #plaud")["fields"]["project"] == "Plaud Sync" and P("add Read #personal")["fields"]["project"] == "Personal"
    assert P("add Read ~xs")["fields"]["effort"] == "XS"
    for line, words, why in (("add Write it #hardwre", "#hardwre", "no project matches #hardwre"), ("add Read #b", "#b", "no project matches #b"),
                             ("add Read #bda", "#bda", "#bda could be BDA M.S. or BDA 696"), ("add Read ~XL", "~XL", "~XL is not an effort (XS, S, M, L)")):
        r = P(line)
        assert r["refused"] == [{"words": words, "why": why + K}] and r["name"].endswith(words) and "project" not in r["fields"] and "effort" not in r["fields"], line


def test_the_last_of_a_kind_wins_and_the_earlier_stays_in_the_name():
    r = P("add Plan #personal trip #compe510")
    assert r["fields"]["project"] == "COMPE 510" and r["name"] == "Plan #personal trip" and r["refused"] == [{"words": "#personal", "why": "a second project" + K}]
    r = P("add Plan trip fri mon")
    assert r["fields"]["due"] == "2026-11-02" and r["name"] == "Plan trip fri" and r["refused"] == [{"words": "fri", "why": "a second due date" + K}]
    r = P("add Draft the intro under 30 after Task-12")
    assert (r["name"], r["under"], r["after"]) == ("Draft the intro", 30, 12) and [p["label"] for p in r["parts"]] == ["part of Task-30", "waits on Task-12"]
    r = P("add X after 3 after 4")
    assert r["after"] == 4 and r["name"] == "X after 3" and r["refused"] == [{"words": "after 3", "why": "a second after" + K}]


def test_task_numbers_are_checked_when_a_lookup_is_given():
    known = {30: {"id": 30, "name": "Goal", "status": "open", "goal": None}, 40: {"id": 40, "name": "Step", "status": "open", "goal": 30},
             7: {"id": 7, "name": "Old", "status": "done", "goal": None}}
    Q = lambda s: P(s, task=known.get)                                                      # noqa: E731
    assert Q("add Read after 99")["refused"] == [{"words": "after 99", "why": "no Task-99" + K}] and Q("add Read after 99")["name"] == "Read after 99"
    assert Q("add Read after 7")["refused"][0]["why"] == "Task-7 is already done" + K
    assert Q("add Read under 40")["refused"][0]["why"] == "Task-40 is a step of Task-30; a step cannot have steps" + K
    r = Q("add Read under 30 after 30")
    assert r["under"] == 30 and r["after"] is None and r["refused"] == [{"words": "after 30", "why": "Task-30 is its goal; a task cannot wait on its own goal" + K}]
    r = Q("add Read under 30")
    assert r["parts"][0]["other"] == {"id": 30, "name": "Goal", "status": "open"}
```

- [ ] **Step 2: Run them**

Run: `/home/robert/factotum/.venv/bin/pytest tests/test_stage6.py -q`
Expected: FAIL with `AttributeError: module 'factotum.commands' has no attribute 'parse_add'`.

- [ ] **Step 3: Implement `ADD_RE`, `link_refusal` and `parse_add`** in `factotum/commands.py` (update the module docstring's examples with one add line). The algorithm (the tests pin the rest):

```
1. Strip the text; apply ADD_RE (required when verb=True, optional otherwise). Split the rest on whitespace into tokens.
2. Anywhere: a token "#<x>" whose <x> is not all digits is a project candidate; "~<x>" an effort candidate; a lone "!" a hard candidate.
3. Trailing run, walking back from the last token, at each position try in order:
   "after"|"under" + N (N = \d{1,7} or Task-\d{1,7}, case-insensitive) → a link candidate;
   a two-token date ("next <weekday>", "next week", "<month> <day>") then a one-token date (today, tomorrow, tmrw,
   mon|monday, tue|tues|tuesday, wed|weds|wednesday, thu|thur|thurs|thursday, fri|friday, sat|saturday, sun|sunday,
   YYYY-MM-DD), a one-token date may end in "!" (a date and a hard candidate); a date takes a preceding "by"/"due";
   a token already a candidate from step 2. Stop at the first token that is none of these.
4. Validate candidates from the right: the first of each kind is checked (project rule, effort list, date resolves,
   link_refusal when `task` is given, `!` needs a valid due); the rest of that kind are refused "a second …".
   Kinds are validated in this order: under, after (with `under` = the valid under's N), due, hard, project, effort.
5. Tokens of a valid part are consumed; everything else, refused parts included, is the name, in order, joined by one space.
```

- [ ] **Step 4: Run them** — `/home/robert/factotum/.venv/bin/pytest tests/test_stage6.py tests/test_stage1.py tests/test_core.py -q`; Expected: PASS (the existing grammar tests unchanged).

- [ ] **Step 5: Run the whole suite, then commit** — `/home/robert/factotum/.venv/bin/pytest -q` (all pass).

```bash
git add factotum/commands.py tests/test_stage6.py
git commit -m "commands: parse_add reads an add line (add / new / +): #project, ~effort and ! anywhere, a date, after N and under N in the trailing run; the last of a kind wins; a refused part stays in the name with its reason; parse() never takes an add line"
```

---

### Task 2: The preview, checked against the database

Implementer: **sonnet**. Part A. UI task: no.

**Files:**
- Create: `factotum/by_hand.py`
- Modify: `factotum/api.py` (one route, beside `/api/commands`)
- Modify: `tests/test_stage6.py`

**Interfaces:**
- Consumes: `commands.parse_add`, `commands.ADD_RE` (Task 1), `config.project_names`, `config.get`.
- Produces (in `factotum/by_hand.py`, module docstring: "Adding, editing and finding tasks by hand (stage 6): the add line's preview and its write, search. Nothing here runs Claude."):
  - `efforts(cfg) -> list[str]` = `C.get(cfg, "taxonomy.efforts", []) or ["XS", "S", "M", "L"]`.
  - `lookup(conn) -> Callable[[int], dict | None]`: `{"id", "name", "status", "goal"}` by primary key, `goal` from an active `part_of` relation whose `src` is the task.
  - `preview(conn, cfg, text: str, today: date, *, verb: bool = False) -> dict`: `{"is_add": False}` when `parse_add` returns None; otherwise the parse (with `task=lookup(conn)`) plus `"is_add": True` and `"change": {"op": "create", "fields": dict(fields, links=[{"kind": "part_of", "other": under}?, {"kind": "waits_on", "other": after}?])}` (`links` only when non-empty; `part_of` first). Text is cut to 500 characters. Writes nothing.
  - `GET /api/add/preview?text=` → `by_hand.preview(conn, eff_cfg(), text, date.today())`.

- [ ] **Step 1: Write the failing tests** (append)

```python
# --- Task 2: the preview, checked against the database

def _client(cfg, tmp_path):
    path = tmp_path / "t.db"
    conn = connect(path)
    init_db(conn)
    return TestClient(api.create_app(cfg, db_path=str(path))), conn


def test_the_preview_checks_the_tasks_it_names(cfg, tmp_path):
    client, conn = _client(cfg, tmp_path)
    mk(conn, 30, name="Assemble the committee"); mk(conn, 12, name="Ask the chair"); mk(conn, 7, name="Old", status="done"); mk(conn, 40)
    link(conn, 40, 30, "part_of")
    n_batches = one(conn, "SELECT COUNT(*) AS n FROM batches")["n"]
    pv = lambda t: client.get("/api/add/preview", params={"text": t}).json()       # noqa: E731
    r = pv("Draft the intro under 30 after 12")
    assert r["is_add"] and r["name"] == "Draft the intro" and r["refused"] == []
    assert r["change"] == {"op": "create", "fields": {"name": "Draft the intro", "links": [{"kind": "part_of", "other": 30}, {"kind": "waits_on", "other": 12}]}}
    assert [p["other"]["name"] for p in r["parts"]] == ["Assemble the committee", "Ask the chair"]
    assert pv("add Read")["name"] == "Read"                                         # a verb is allowed, not needed
    assert pv("Pay the 12 invoices after 99")["refused"] == [{"words": "after 99", "why": "no Task-99" + K}]
    assert pv("Read after 7")["refused"][0]["why"] == "Task-7 is already done" + K
    assert pv("Read under 40")["refused"][0]["why"] == "Task-40 is a step of Task-30; a step cannot have steps" + K
    assert pv("Read #nosuch")["refused"][0]["why"] == "no project matches #nosuch" + K
    assert pv("")["refused"] == [{"words": "", "why": "a task needs a name"}]
    assert one(conn, "SELECT COUNT(*) AS n FROM batches")["n"] == n_batches        # a preview writes nothing
```

- [ ] **Step 2: Run them** — `/home/robert/factotum/.venv/bin/pytest tests/test_stage6.py -q`; Expected: the new test FAILS (404 on `/api/add/preview`).

- [ ] **Step 3: Implement** `factotum/by_hand.py` (`efforts`, `lookup`, `preview`) and the route.

- [ ] **Step 4: Run the whole suite, then commit** — all pass.

```bash
git add factotum/by_hand.py factotum/api.py tests/test_stage6.py
git commit -m "by_hand: GET /api/add/preview shows how an add line will be filed, with the tasks it names checked (missing, closed, a step as a goal, its own goal) and every refused part kept in the name; it writes nothing"
```

---

### Task 3: A new task exists on Enter

Implementer: **sonnet**. Part A. UI task: no.

**Files:**
- Modify: `factotum/by_hand.py`, `factotum/api.py`, `factotum/__main__.py`
- Modify: `tests/test_stage6.py`

**Interfaces:**
- Consumes: `preview`, `lookup`, `efforts` (Task 2); `commands.link_refusal` (Task 1); `batches.apply` (a `create` with `fields.links` and a Python-only `source_key`).
- Produces:
  - `KEY_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")`; `FIELD_KEYS = ("name", "due", "hard", "project", "effort", "context", "counterpart", "waiting_on", "after", "under")`.
  - `add(conn, cfg, *, text: str | None = None, fields: dict | None = None, key: str | None = None, today: date) -> dict` = `{"task_id", "batch_id", "already", "name", "parts", "refused", "skipped"}`. Exactly one of `text` / `fields`, else `ValueError("give text or fields")`; a bad key → `ValueError("key must be 6 to 64 letters, digits, - or _")`; no name → `ValueError("a task needs a name")`. `text` goes through `preview` (`verb=False`). `fields` are checked one by one, a bad one left out and refused WITHOUT the "kept in the name" suffix: `'<v>' is not a date` (not `date.fromisoformat`-able), `hard` only with a due (`! needs a due date`), `'<v>' is not a project` (exact configured name), `'<v>' is not an effort (XS, S, M, L)`, `link_refusal` for `after` / `under` (ints only, else `'<v>' is not a task number`), `<key> cannot be set on a new task` for any key not in `FIELD_KEYS`; text fields are stripped, empty ones omitted, `context` / `counterpart` / `waiting_on` cut to 2000. Under a module `threading.Lock`: when `key` names an existing `source_key` `add:<key>`, the answer is that task with `"already": True, "batch_id": None`; else one `batches.apply(conn, [{"op": "create", "fields": dict(fields, links=…), "source_key": f"add:{key}"}], "ui", today)` (no key → no `source_key`); `skipped` is the batch's.
  - `POST /api/add` with body `AddBody {text: str | None = None, fields: dict | None = None, key: str | None = None}` → `by_hand.add(…, today=date.today())`; `ValueError` → 400 with its message; after a create, `sch = getattr(app.state, "scheduler", None); if sch is not None: sch.wake()` (harmless today; stage 7 queues filing on a create).
  - CLI `python -m factotum add TEXT [--dry-run]` (help: "add a task by hand from one line, as the New task line does: #project ~effort ! a date, after N, under N; --dry-run prints how it would be filed"): `--dry-run` prints `json.dumps(preview, indent=1, ensure_ascii=False)` and writes nothing; otherwise prints `Task-<id> <name>` then one line per refusal `  <words>: <why>`.

- [ ] **Step 1: Write the failing tests** (append)

```python
# --- Task 3: a new task exists on Enter

def test_a_task_made_by_hand_exists_at_once_and_its_undo_drops_it(cfg, tmp_path):
    client, conn = _client(cfg, tmp_path)
    mk(conn, 12, name="Ask the chair")
    r = client.post("/api/add", json={"text": "Email the lab #personal ~S after 12", "key": "k1abcdef"}).json()
    t = one(conn, "SELECT * FROM tasks WHERE id = ?", (r["task_id"],))
    assert not r["already"] and r["refused"] == [] and r["skipped"] == []
    assert (t["name"], t["project"], t["effort"], t["source"], t["status"]) == ("Email the lab", "Personal", "S", "Manual", "open")
    assert (r["task_id"], 12, "waits_on") in active_rels(conn)
    assert one(conn, "SELECT source FROM batches WHERE id = ?", (r["batch_id"],))["source"] == "ui"
    client.post(f"/api/batches/{r['batch_id']}/undo", json={})
    t = one(conn, "SELECT status, closed_by FROM tasks WHERE id = ?", (r["task_id"],))
    assert (t["status"], t["closed_by"]) == ("dropped", "undo") and (r["task_id"], 12, "waits_on") not in active_rels(conn)


def test_two_quick_enters_make_one_task(cfg, tmp_path):
    from factotum import by_hand
    client, conn = _client(cfg, tmp_path)
    a = client.post("/api/add", json={"text": "Water the plants", "key": "samekey1"}).json()
    b = client.post("/api/add", json={"text": "Water the plants", "key": "samekey1"}).json()
    assert b["already"] and b["task_id"] == a["task_id"] and b["batch_id"] is None
    shared = connect(tmp_path / "t.db", shared=True)
    got = []
    ts = [threading.Thread(target=lambda: got.append(by_hand.add(shared, cfg, text="Feed the cat", key="samekey2", today=TODAY))) for _ in range(2)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len({g["task_id"] for g in got}) == 1 and sorted(g["already"] for g in got) == [False, True]
    assert one(conn, "SELECT COUNT(*) AS n FROM tasks WHERE name IN ('Water the plants', 'Feed the cat')")["n"] == 2


def test_refused_words_stay_in_the_name_and_are_reported(cfg, tmp_path):
    client, conn = _client(cfg, tmp_path)
    r = client.post("/api/add", json={"text": "Read after 99", "key": "k2abcdef"}).json()
    assert one(conn, "SELECT name FROM tasks WHERE id = ?", (r["task_id"],))["name"] == "Read after 99"
    assert r["refused"] == [{"words": "after 99", "why": "no Task-99" + K}] and not active_rels(conn)


def test_add_with_fields_for_the_phone_sheet_add_a_step_and_duplicate(cfg, tmp_path):
    client, conn = _client(cfg, tmp_path)
    mk(conn, 30); mk(conn, 12)
    r = client.post("/api/add", json={"key": "k3abcdef", "fields": {"name": "Copy", "due": "2026-11-20", "hard": True, "project": "Personal", "effort": "M",
                                                                   "context": "ctx", "counterpart": "a@b.c", "under": 30, "after": 12}}).json()
    t = one(conn, "SELECT * FROM tasks WHERE id = ?", (r["task_id"],))
    assert (t["due"], t["hard"], t["project"], t["effort"], t["context"], t["counterpart"]) == ("2026-11-20", 1, "Personal", "M", "ctx", "a@b.c")
    assert {(r["task_id"], 30, "part_of"), (r["task_id"], 12, "waits_on")} <= active_rels(conn)
    r = client.post("/api/add", json={"key": "k4abcdef", "fields": {"name": "X", "project": "Nope", "effort": "XXL", "due": "soon", "score": 9, "after": "12"}}).json()
    assert [x["why"] for x in r["refused"]] == ["'soon' is not a date", "'Nope' is not a project", "'XXL' is not an effort (XS, S, M, L)",
                                                "'12' is not a task number", "score cannot be set on a new task"]
    assert one(conn, "SELECT project, effort, due FROM tasks WHERE id = ?", (r["task_id"],)) == {"project": None, "effort": None, "due": None}
    assert client.post("/api/add", json={"fields": {"name": " "}}).status_code == 400
    assert client.post("/api/add", json={"text": "x", "key": "bad key!"}).status_code == 400
    assert client.post("/api/add", json={}).status_code == 400


def test_the_add_command_line(cfg, tmp_path, monkeypatch, capsys):
    from factotum import __main__ as cli
    monkeypatch.setenv("FACTOTUM_CONFIG", str(FIXTURE)); monkeypatch.setenv("FACTOTUM_DB", str(tmp_path / "c.db"))
    cli.main(["add", "Read the paper #personal", "--dry-run"])
    assert json.loads(capsys.readouterr().out)["name"] == "Read the paper"
    c = connect(tmp_path / "c.db")
    assert one(c, "SELECT COUNT(*) AS n FROM tasks")["n"] == 0
    cli.main(["add", "Read the paper #nosuch"])
    out = capsys.readouterr().out
    assert out.startswith("Task-") and "#nosuch: no project matches #nosuch" in out
```

(The order of `refused` in the fields path is the order of `FIELD_KEYS`, then unknown keys in the order given.)

- [ ] **Step 2: Run them** — `/home/robert/factotum/.venv/bin/pytest tests/test_stage6.py -q`; Expected: the new tests FAIL (404 on `/api/add`; `by_hand.add` missing).

- [ ] **Step 3: Implement** `add`, the route and the CLI.

- [ ] **Step 4: Run the whole suite, then commit** — all pass.

```bash
git add factotum/by_hand.py factotum/api.py factotum/__main__.py tests/test_stage6.py
git commit -m "by_hand: POST /api/add makes a task at once (one ui batch, Source Manual, undo drops it) from a line or from fields; a client key makes two quick Enters one task; refused parts are reported, never lost; python -m factotum add"
```

---

### Task 4: A conversation that belongs to a place

Implementer: **opus**. Part A. UI task: no.

**Files:**
- Modify: `factotum/db.py` (`MIGRATIONS`), `factotum/chat.py`, `factotum/api.py` (`TurnBody`, the turn route, `GET /api/chat/place`)
- Create: `tests/test_stage6_chat.py`

**Interfaces:**
- Consumes: `chat.turn`, `chat.build_prompt`, `chat.messages`, `chat.threads`, `mcp_server.tasks_seen`, `app.state.chat_busy`.
- Produces:
  - `MIGRATIONS["chat_threads"] = {"origin": "TEXT", "task_id": "INTEGER"}`; `MIGRATIONS["chat_messages"]` gains `"read_json": "TEXT"`. No index (the table is small, and an index in `SCHEMA` would run before the column exists on an old database).
  - In `factotum/chat.py`: `ORIGINS = ("review", "today", "task")`; `TODAY_THREAD_HOURS = 12`; `PLACE_TEXT` (exact):

```python
PLACE_TEXT = {
    "review": "The user is on the Review card of Task-{id}, deciding what to do with it (done, keep, later or drop). Whatever you propose is listed on that card and staged together with their decision, so propose `done` or `drop` for Task-{id} only when they ask for it.",
    "task": "The user opened Task-{id} and asked from there; your answer and your proposals are shown on that task.",
    "today": "The user typed this into the box on the Today tab. If it describes something they have to do, propose one `create` (a name of at most 80 characters, the project, an effort when you can tell, a due date only when they gave one, `hard` only when something breaks if it is late); read the open tasks with `tasks_query` first, so the create carries the `links` that place it: `part_of` a goal it is a step of, `waits_on` a task it cannot start before. If it is a question, answer it.",
}
```

  - `new_thread(conn, title: str | None = None, origin: str | None = None, task_id: int | None = None) -> int`.
  - `place_thread(conn, origin: str, task_id: int | None, now: datetime | None = None) -> int | None`: `review` / `task` → the thread with that `task_id` and origin in (`review`, `task`) whose last message (else `created_at`) is newest; `today` → the newest `today` thread whose last message is less than `TODAY_THREAD_HOURS` old (`now` defaults to `datetime.now(timezone.utc)`).
  - `threads()` rows gain `origin` and `task_id`; `messages()` entries gain `"read": [ids]` (from `read_json`, `[]` when NULL).
  - `named(conn, ids: set[int]) -> dict[str, dict]` = `{str(id): {"id", "name", "status"}}` for the ids that exist; `proposal_ids(proposals: list[dict]) -> set[int]` = every int `task_id`, `fields.other`, `fields.into` and `fields.links[].other`.
  - `build_prompt(…, place: tuple[str, int | None] | None = None)`: when `place` names an origin, a section `## Where the user asked\n\n` + `PLACE_TEXT[origin].format(id=task_id)` right after `## Today` (first and continued turns alike).
  - `turn()`: reads the thread's `origin` / `task_id`; for `review` and `task` the place's task is added to `attached` when missing; passes `place`; the assistant row stores `read_json` = `sorted(set(attached) | tasks_seen)`; the result gains `"read"`, `"named"` (= `named(conn, read ∪ proposal_ids(kept) ∪ attached)`), `"origin"`, `"task_id"`.
  - API: `TurnBody` gains `origin: str | None = None`, `task_id: int | None = None`, `fresh: bool = False`. The route, under a `threading.Lock` made in `create_app`: `thread_id` given → that thread (404 if missing); else `origin` not None and not in `ORIGINS` → 400 `unknown origin`; `review` / `task` need an existing task (404 `no such task`) and use `place_thread` unless `fresh`, else `new_thread(conn, None, origin, task_id)`; `today` likewise without a task; no origin → `new_thread(conn)` as today. A thread in `app.state.chat_busy` → 409 `Claude is still answering in this conversation`; the id joins `chat_busy` inside the lock.
  - `GET /api/chat/place?origin=&task_id=` → `{"thread": {id, title, origin, task_id, created_at} | None, "messages": [...], "named": {...}, "busy": bool}` (`named` over every message's `read`, `attached` and proposal ids); 400 for an unknown origin.

- [ ] **Step 1: Write the failing tests** in `tests/test_stage6_chat.py`

```python
"""Stage 6, the conversation in place: threads that belong to a place, proposals staged together, Split into steps."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from factotum import api, batches, chat
from factotum.claude.runner import RunResult
from factotum.db import one, rows
from conftest import TODAY
from stage4_helpers import active_rels, mk


def _sse(resp) -> list[dict]:
    return [json.loads(l[5:]) for l in resp.iter_lines() if l.startswith("data:")]


def _runner(data, seen=None, calls=None):
    def run(cfg, kind, prompt, **kw):
        if calls is not None:
            calls.append(dict(kw, prompt=prompt))
        if seen:
            (Path(kw["workdir"]) / "tasks_seen.jsonl").write_text(json.dumps({"ids": seen}) + "\n")
        return RunResult(True, data, json.dumps(data), 0.21, 2400, session_id="S1")
    return run


# --- Task 4: a conversation that belongs to a place

def test_a_card_and_an_opened_task_share_one_thread_and_today_has_its_own(conn):
    a = chat.new_thread(conn, None, "review", 5)
    assert chat.place_thread(conn, "review", 5) == a == chat.place_thread(conn, "task", 5)
    assert chat.place_thread(conn, "review", 6) is None
    chat.new_thread(conn, "from the Chat tab")                                        # no origin: never found in place
    assert chat.place_thread(conn, "task", 5) == a
    t = chat.new_thread(conn, None, "today")
    conn.execute("INSERT INTO chat_messages (thread_id, role, text, created_at) VALUES (?, 'user', 'hi', ?)", (t, datetime.now(timezone.utc).isoformat())); conn.commit()
    assert chat.place_thread(conn, "today", None) == t
    assert chat.place_thread(conn, "today", None, now=datetime.now(timezone.utc) + timedelta(hours=13)) is None
    assert [(r["origin"], r["task_id"]) for r in chat.threads(conn) if r["id"] == a] == [("review", 5)]


def test_a_turn_from_a_card_attaches_the_card_says_where_and_names_what_it_read(conn, cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(chat, "RUNS_DIR", tmp_path)
    mk(conn, 5, name="The goal"); mk(conn, 7, name="Read by a tool")
    calls = []
    tid = chat.new_thread(conn, None, "review", 5)
    res = chat.turn(conn, cfg, tid, "what is left?", [], today=TODAY, runner=_runner({"reply_md": "ok", "proposals": []}, seen=[7], calls=calls))
    assert "## Where the user asked" in calls[0]["prompt"] and "Review card of Task-5" in calls[0]["prompt"] and '"Task-5"' in calls[0]["prompt"]
    assert res["read"] == [5, 7] and res["named"]["7"] == {"id": 7, "name": "Read by a tool", "status": "open"}
    assert (res["origin"], res["task_id"]) == ("review", 5) and chat.messages(conn, tid)[-1]["read"] == [5, 7]


def test_the_turn_route_finds_the_place_and_refuses_a_second_turn_while_one_runs(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(chat, "RUNS_DIR", tmp_path / "runs")
    app = api.create_app(cfg, db_path=str(tmp_path / "t.db"), chat_runner=_runner({"reply_md": "ok", "proposals": []}))
    mk(app.state.conn, 5)
    c = TestClient(app)
    turn = lambda **b: c.post("/api/chat/turn", json=dict({"text": "hi", "attached": []}, **b))      # noqa: E731
    with c.stream("POST", "/api/chat/turn", json={"text": "hi", "attached": [], "origin": "review", "task_id": 5}) as r:
        first = _sse(r)[-1]["result"]
    with c.stream("POST", "/api/chat/turn", json={"text": "and?", "attached": [], "origin": "task", "task_id": 5}) as r:
        again = _sse(r)[-1]["result"]
    assert again["thread_id"] == first["thread_id"] and len(chat.messages(app.state.conn, first["thread_id"])) == 4
    app.state.chat_busy.add(first["thread_id"])
    r = turn(origin="review", task_id=5)
    assert r.status_code == 409 and r.json()["detail"] == "Claude is still answering in this conversation"
    assert turn(thread_id=first["thread_id"]).status_code == 409
    app.state.chat_busy.discard(first["thread_id"])
    assert turn(origin="elsewhere").status_code == 400 and turn(origin="review", task_id=999).status_code == 404
    with c.stream("POST", "/api/chat/turn", json={"text": "new", "attached": [], "origin": "review", "task_id": 5, "fresh": True}) as r:
        fresh = _sse(r)[-1]["result"]
    assert fresh["thread_id"] != first["thread_id"]
    p = c.get("/api/chat/place", params={"origin": "review", "task_id": 5}).json()
    assert p["thread"]["id"] == fresh["thread_id"] and p["thread"]["origin"] == "review" and len(p["messages"]) == 2 and p["busy"] is False
    assert c.get("/api/chat/place", params={"origin": "today"}).json()["thread"] is None
    assert c.get("/api/chat/place", params={"origin": "nope"}).status_code == 400
```

- [ ] **Step 2: Run them** — `/home/robert/factotum/.venv/bin/pytest tests/test_stage6_chat.py -q`; Expected: FAIL (`new_thread() takes … positional arguments`, then missing `place_thread`).

- [ ] **Step 3: Implement** the columns, the chat functions, the prompt section and the routes. Keep `chat.turn`'s existing return keys and order; add the new ones at the end.

- [ ] **Step 4: Run** `/home/robert/factotum/.venv/bin/pytest tests/test_stage6_chat.py tests/test_phase5.py tests/test_stage4_agent.py -q`, then the whole suite; Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add factotum/db.py factotum/chat.py factotum/api.py tests/test_stage6_chat.py
git commit -m "chat: a conversation can belong to a place (a Review card or an opened task share one thread per task; the Today box keeps today's); the turn says where it was asked, attaches the place's task, records what it read and names every task it mentions; a second turn in a busy thread is a 409; GET /api/chat/place"
```

---

### Task 5: Everything a reply proposes can be staged together

Implementer: **opus**. Part A. UI task: no.

**Files:**
- Modify: `factotum/chat.py` (`_clean_proposals`, new `resolve_staged`, `mark_staged`), `factotum/batches.py` (`_create`), `factotum/api.py` (`apply_batch`), `prompts/chat-turn.md`
- Modify: `tests/test_stage6_chat.py`

**Interfaces:**
- Consumes: `chat._clean_proposals` (its `ref_owner` map), `chat._loads`, `batches.apply`.
- Produces:
  - `_clean_proposals`: a `link` whose `fields.other` is a string is kept when it is the `ref` of a `create` elsewhere in the same reply (`{"kind", "other": "<ref>", "why"?}`); `task_id` must still be an int the turn attached or read, and `kind` `part_of` or `waits_on`; no `relations.link_problem` check for that end. Otherwise rejected `<label>: no create in this reply has ref '<v>'`. `unlink` and `merge` with a string end are rejected `<label>: both tasks must be named by their integer ids` (today's message).
  - `batches._create`: `source=ch.get("created_source") or created_source(source)` (a Python-only key, like `source_key`).
  - `chat.resolve_staged(conn, cfg, changes: list[dict]) -> list[dict]`: each change with `from_chat: {message_id, index}` must name a stored `create` proposal (else `ValueError("from_chat names no create proposal")`); it becomes `{"op": "create", "fields": <the stored fields>, "source_key": f"chat:{m}:{i}", "created_source": "Chat", "from_chat": {…}}` (the page's fields are ignored). When a task with that `source_key` already exists (Create was pressed), the create is left out and, in every other change of the list, a `task_id`, `fields.other` or `fields.links[].other` equal to its `ref` becomes that task's id. Other changes pass unchanged.
  - `chat.mark_staged(conn, changes: list[dict], res: dict) -> None`: for each `from_chat` create, the task with its `source_key` (if any) marks the stored proposal `applied: True, task_id, batch_id = res["batch_id"]`; commits.
  - `api.apply_batch`: strips `source_key` AND `created_source` from what the page sent, then `changes = chat.resolve_staged(conn, eff_cfg(), changes)`, `res = batches.apply(…)`, `chat.mark_staged(conn, changes, res)`; a `ValueError` is a 400 as today.
  - `prompts/chat-turn.md`: the line `- \`link\` / \`unlink\` fields: …` becomes exactly: `` - `link` / `unlink` fields: `kind` (`part_of` | `waits_on`), `other` (a task id; for `link`, also the `ref` of a `create` in this reply, so an existing task can wait on a new one), `why` (one line). ``

- [ ] **Step 1: Write the failing tests** (append)

```python
# --- Task 5: everything a reply proposes can be staged together

STEPS = {"reply_md": "Two steps.", "proposals": [
    {"op": "create", "fields": {"name": "Fill the form", "project": "Personal", "ref": "s1", "links": [{"kind": "part_of", "other": 5}]}, "label": "New: Fill the form"},
    {"op": "link", "task_id": 6, "fields": {"kind": "waits_on", "other": "s1", "why": "needs the form"}, "label": "6 waits on the form"},
    {"op": "link", "task_id": 6, "fields": {"kind": "waits_on", "other": "s9"}, "label": "bad ref"},
    {"op": "unlink", "task_id": 6, "fields": {"kind": "waits_on", "other": "s1"}, "label": "unlink by ref"}]}


def _steps(conn, cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(chat, "RUNS_DIR", tmp_path / "runs")
    mk(conn, 5, name="Goal"); mk(conn, 6, name="Send it")
    tid = chat.new_thread(conn, None, "review", 5)
    return chat.turn(conn, cfg, tid, "steps?", [6], today=TODAY, runner=_runner(STEPS))


def test_a_link_may_point_at_a_create_in_the_same_reply(conn, cfg, tmp_path, monkeypatch):
    res = _steps(conn, cfg, tmp_path, monkeypatch)
    assert [(p["op"], p["task_id"], p["fields"].get("other")) for p in res["proposals"]] == [("create", None, None), ("link", 6, "s1")]
    assert res["rejected"] == ["bad ref: no create in this reply has ref 's9'", "unlink by ref: both tasks must be named by their integer ids"]


def test_staged_creates_and_links_apply_as_one_batch_labelled_chat(cfg, tmp_path, monkeypatch):
    app = api.create_app(cfg, db_path=str(tmp_path / "t.db"))
    c, conn = TestClient(app), app.state.conn
    res = _steps(conn, cfg, tmp_path, monkeypatch)
    m = res["message_id"]
    out = c.post("/api/batches/apply", json={"source": "review", "changes": [
        {"op": "keep", "task_id": 5, "fields": {}},
        {"op": "create", "task_id": None, "fields": {"name": "forged"}, "from_chat": {"message_id": m, "index": 0}},
        {"op": "link", "task_id": 6, "fields": {"kind": "waits_on", "other": "s1"}}]}).json()
    new = out["created"][0]
    t = one(conn, "SELECT name, source, source_key FROM tasks WHERE id = ?", (new,))
    assert out["skipped"] == [] and (t["name"], t["source"], t["source_key"]) == ("Fill the form", "Chat", f"chat:{m}:0")
    assert {(new, 5, "part_of"), (6, new, "waits_on")} <= active_rels(conn)
    stored = chat.messages(conn, res["thread_id"])[-1]["proposals"][0]
    assert stored["applied"] and stored["task_id"] == new and stored["batch_id"] == out["batch_id"]
    c.post(f"/api/batches/{out['batch_id']}/undo", json={})
    assert one(conn, "SELECT status FROM tasks WHERE id = ?", (new,))["status"] == "dropped" and (6, new, "waits_on") not in active_rels(conn)
    assert one(conn, "SELECT last_reviewed_at FROM tasks WHERE id = 5")["last_reviewed_at"] is None


def test_a_staged_create_already_made_by_its_create_button_is_not_made_twice(cfg, tmp_path, monkeypatch):
    app = api.create_app(cfg, db_path=str(tmp_path / "t.db"))
    c, conn = TestClient(app), app.state.conn
    res = _steps(conn, cfg, tmp_path, monkeypatch)
    made = chat.create_from_proposal(conn, cfg, res["message_id"], 0, TODAY)
    out = c.post("/api/batches/apply", json={"source": "ui", "changes": [
        {"op": "create", "task_id": None, "fields": {}, "from_chat": {"message_id": res["message_id"], "index": 0}},
        {"op": "link", "task_id": 6, "fields": {"kind": "waits_on", "other": "s1"}}]}).json()
    assert out["created"] == [] and out["skipped"] == [] and (6, made, "waits_on") in active_rels(conn)
    assert c.post("/api/batches/apply", json={"changes": [{"op": "create", "fields": {}, "from_chat": {"message_id": res["message_id"], "index": 1}}]}).status_code == 400
    assert c.post("/api/batches/apply", json={"changes": [{"op": "create", "fields": {}, "from_chat": {"message_id": 999999, "index": 0}}]}).status_code == 400


def test_the_page_cannot_choose_a_created_source(cfg, tmp_path):
    app = api.create_app(cfg, db_path=str(tmp_path / "t.db"))
    out = TestClient(app).post("/api/batches/apply", json={"changes": [{"op": "create", "fields": {"name": "Mine"}, "created_source": "Chat", "source_key": "x"}]}).json()
    assert one(app.state.conn, "SELECT source, source_key FROM tasks WHERE id = ?", (out["created"][0],))["source"] == "Manual"
```

- [ ] **Step 2: Run them** — `/home/robert/factotum/.venv/bin/pytest tests/test_stage6_chat.py -q`; Expected: the four new tests FAIL.

- [ ] **Step 3: Implement** the cleaner branch, `_create`'s override, `resolve_staged`, `mark_staged`, the route and the prompt line.

- [ ] **Step 4: Run** `/home/robert/factotum/.venv/bin/pytest tests/test_stage6_chat.py tests/test_phase5.py tests/test_stage4_agent.py tests/test_stage4_store.py -q`, then the whole suite; Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add factotum/chat.py factotum/batches.py factotum/api.py prompts/chat-turn.md tests/test_stage6_chat.py
git commit -m "chat: a reply's creates, its links to them (a link may name a sibling create's ref) and a Review decision apply as one batch: a create staged from_chat is resolved from the stored proposal (Source Chat, keyed like Create, never made twice) and marked applied; the page cannot pick a created source"
```

---

### Task 6: Split into steps

Implementer: **sonnet**. Part A. UI task: no.

**Files:**
- Create: `prompts/chat-split.md`
- Modify: `factotum/chat.py` (`CANNED`)
- Modify: `tests/test_stage6_chat.py`

**Interfaces:**
- Consumes: `chat.CANNED`, `chat.build_prompt`, the API's canned check.
- Produces: `CANNED["split"] = ("chat-split.md", "Split into steps")`; the page's "Make them tasks" sends `canned: "split"` with the text `Make them tasks` (the stored user message is the text when there is one).

- [ ] **Step 1: Write `prompts/chat-split.md`** (exact text):

```markdown
## Task: split the attached task into steps

The attached task is a goal. Read it with `task_get` and its existing steps with `goal_tree`. If its text
already lists steps, make one task for each listed step that has no task yet, in the order the text
gives; otherwise draft between two and six steps that together finish it, in order. Propose each as a
`create` with a short name (at most 80 characters), the goal's project, an effort when you can tell, a
`ref` (`s1`, `s2`, …) and `links: [{"kind": "part_of", "other": <the goal's id>}]`; add
`{"kind": "waits_on", "other": "<the ref of an earlier step>"}` only where a step truly cannot start
before that one. Never a due date unless the text gives one. A step that already exists as a task is
not proposed again; if it is not linked to the goal, propose `link` with `kind` `part_of` instead. A goal
that is itself a step of another task cannot have steps: say so and propose nothing. Reply with the
steps as a short numbered list.
```

- [ ] **Step 2: Write the failing test** (append)

```python
# --- Task 6: Split into steps

def test_split_into_steps_is_a_canned_prompt(conn, cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(chat, "RUNS_DIR", tmp_path / "runs")
    mk(conn, 5, name="Goal")
    p = chat.build_prompt(cfg, conn, "", [5], TODAY, canned="split")
    assert "## Task: split the attached task into steps" in p and '"kind": "part_of"' in p
    data = {"reply_md": "1. a\n2. b", "proposals": [
        {"op": "create", "fields": {"name": "a", "ref": "s1", "links": [{"kind": "part_of", "other": 5}]}, "label": "a"},
        {"op": "create", "fields": {"name": "b", "ref": "s2", "links": [{"kind": "part_of", "other": 5}, {"kind": "waits_on", "other": "s1"}]}, "label": "b"}]}
    tid = chat.new_thread(conn, None, "task", 5)
    res = chat.turn(conn, cfg, tid, "Make them tasks", [], today=TODAY, canned="split", runner=_runner(data))
    assert res["rejected"] == [] and res["proposals"][1]["fields"]["links"] == [{"kind": "part_of", "other": 5}, {"kind": "waits_on", "other": "s1"}]
    assert chat.messages(conn, tid)[0]["text"] == "Make them tasks"
    assert chat.messages(conn, chat.new_thread(conn)) == [] and chat.CANNED["split"][1] == "Split into steps"
```

- [ ] **Step 3: Run it** — `/home/robert/factotum/.venv/bin/pytest tests/test_stage6_chat.py -q`; Expected: FAIL (`KeyError` / the canned section missing), then add the `CANNED` entry.

- [ ] **Step 4: Run the whole suite** (`test_nothing_the_user_or_the_agent_reads_says_blocked_or_dependency` reads the new prompt); all pass.

- [ ] **Step 5: Commit**

```bash
git add prompts/chat-split.md factotum/chat.py tests/test_stage6_chat.py
git commit -m "chat: Split into steps (canned split): listed steps become tasks, or two to six are drafted, each a create with a ref, part of the goal, waiting on an earlier step only where order is real; Make them tasks uses the same prompt"
```

---

### Task 7: Finding any task

Implementer: **sonnet**. Part A. UI task: no.

**Files:**
- Modify: `factotum/by_hand.py`, `factotum/api.py`, `factotum/__main__.py`
- Modify: `tests/test_stage6.py`

**Interfaces:**
- Consumes: the `tasks` and `relations` tables.
- Produces:
  - `search(conn, q: str, limit: int = 12) -> dict` = `{"results": [row…], "more": bool}`, `limit` clamped to 1..30. A row is `{"id", "name", "status", "project", "due", "completed_at", "closed_by", "outcome", "merged_into"}` (`merged_into` = the `dst` of the task's active `same_as` relation, else None). `q` stripped; empty → no results. `^(?:task[-\s]?|#)?(\d{1,7})$` (case-insensitive) → that id first, then ids whose decimal form starts with the digits; otherwise every word (at most 6) must appear in `name` or `project` (`LIKE` with `%` / `_` / `\` escaped, `ESCAPE '\'`). Order after an exact id: open (due ascending, undated last, then id descending), then closed (`completed_at` descending, then id descending). Read-only.
  - `GET /api/search?q=&limit=12` → `by_hand.search(...)`.
  - CLI `python -m factotum find Q` (help: "find any task, open or closed, by number or words"): one line per row `Task-<id>  <status>[ <completed_at[:10]>][ merged into Task-N]  <name>`.

- [ ] **Step 1: Write the failing tests** (append)

```python
# --- Task 7: finding any task

def _found(cfg, tmp_path):
    client, conn = _client(cfg, tmp_path)
    mk(conn, 12, name="Ask the chair"); mk(conn, 120, name="Firmware tests"); mk(conn, 121, name="Firmware box")
    batches.apply(conn, [{"op": "done", "task_id": 120, "fields": {"outcome": "passed"}}], "ui", date(2026, 11, 10))
    batches.apply(conn, [{"op": "merge", "task_id": 121, "fields": {"into": 12}}], "ui", TODAY)
    return client, conn


def test_any_task_can_be_found_by_number_or_words(cfg, tmp_path):
    from factotum import by_hand
    client, conn = _found(cfg, tmp_path)
    ids = lambda q, **k: [r["id"] for r in by_hand.search(conn, q, **k)["results"]]     # noqa: E731
    assert ids("12") == [12, 120, 121] and ids("Task-120")[0] == 120 and ids("#121")[0] == 121
    assert ids("firmware") == [120, 121] and ids("firm tests") == [120] and ids("") == [] and ids("100%") == []
    r = client.get("/api/search", params={"q": "firmware"}).json()["results"]
    assert r[0] == {"id": 120, "name": "Firmware tests", "status": "done", "project": "COMPE 510", "due": None, "completed_at": "2026-11-10",
                    "closed_by": "ui", "outcome": "passed", "merged_into": None}
    assert r[1]["merged_into"] == 12 and r[1]["status"] == "dropped"


def test_search_is_capped_and_says_so(conn):
    from factotum import by_hand
    for i in range(1, 41):
        mk(conn, i, name=f"Note {i}")
    r = by_hand.search(conn, "note")
    assert len(r["results"]) == 12 and r["more"] is True and len(by_hand.search(conn, "note", limit=50)["results"]) == 30


def test_the_find_command_line(cfg, tmp_path, monkeypatch, capsys):
    from factotum import __main__ as cli
    monkeypatch.setenv("FACTOTUM_CONFIG", str(FIXTURE)); monkeypatch.setenv("FACTOTUM_DB", str(tmp_path / "t.db"))
    _found(cfg, tmp_path)
    cli.main(["find", "firmware"])
    assert capsys.readouterr().out.splitlines() == ["Task-120  done 2026-11-10  Firmware tests", "Task-121  dropped 2026-11-01 merged into Task-12  Firmware box"]
```

- [ ] **Step 2: Run them** — `/home/robert/factotum/.venv/bin/pytest tests/test_stage6.py -q`; Expected: the new tests FAIL.

- [ ] **Step 3: Implement** `search`, the route and the CLI.

- [ ] **Step 4: Run the whole suite, then commit** — all pass.

```bash
git add factotum/by_hand.py factotum/api.py factotum/__main__.py tests/test_stage6.py
git commit -m "by_hand: GET /api/search finds any task, open or closed, by number or by every word of its name, with how and when it closed and where a merge sent it; python -m factotum find"
```

---

# Part B — the page (worktree `linked-tasks`, after the merges)

Before Task 8: run `/home/robert/factotum/.venv/bin/pytest -q` (green), read the current `app.js` whole (4b and stage 5 Part B are in it; stage 5 may have extended `toast`, `typingNow`, `draft` and `reviewAct`: extend what is there, never revert it). Name functions; never rely on line numbers.

### Task 8: The seed, the stub, and the conversation on a Review card

Implementer: **opus**. Part B. UI task: yes.

**Files:**
- Create (outside the repository): `DEV/seed_byhand.py`, `DEV/scenarios/s6-lib.mjs`, `DEV/scenarios/s6-8-review.mjs`
- Modify: `factotum/web/app.js` — new section `// ---------- in place (stage 6): the conversation ----------`; changes in `draft`, `DRAFT_BOXES` / `typingNow` / `render()` (focus restore through `isDraftBox`), `sendChat` (stream reading moved out), `stageKey`, `stageProposal`, `applyAll`, `renderReview` (the card's Ask section), `wire()`; delete `reviewAsk`, `ui.rvReply` and `sendChat`'s `from.review` branch.
- Modify: `factotum/web/app.css`

**Interfaces:**
- Consumes: Part A's routes and result keys; 4b's `stageChange`, `stageExtra(key, task_id, op, fields, label, extra)`, `changeWords`, `linkPredicate`, `textExpect`, `md`, `goTask`, `nameOf`.
- Produces:
  - `DEV/seed_byhand.py [--fresh | --remove]` (conventions of `DEV/seed_links.py`): runs `seed_links.py --fresh` (subprocess, the venv python) and reads its JSON as `S`; on `DEV/factotum-dev.db`, with `P1` the first of `config.project_names(cfg)` and `today = date.today()`: (1) one `ui` batch setting `goal`'s context to `"Steps:\n- ask the chair\n- send the form\n- register with the office"`; (2) a review thread `chat.new_thread(conn, "Seed6 · committee steps", "review", goal)` with a user message `Which steps are left, and can you make tasks for them?` and an assistant message (`text` `Two of the steps have a task. I drafted the rest below, in order, and marked what waits on what.`, `cost_usd` 0.21, `duration_ms` 24000, `read_json` `[goal, step_done, step_freed]`) whose `proposals_json` is, in order: create `{name: "Seed · Get the chair's sign-off", project: P1, ref: "s1", links: [{kind: "part_of", other: goal}]}`; create `{name: "Seed · Fill out the committee form", project: P1, ref: "s2", links: [{part_of goal}, {kind: "waits_on", other: "s1"}]}`; link `{task_id: step_freed, fields: {kind: "waits_on", other: "s2", why: "the form must be filled first"}}`; set `{task_id: goal, fields: {due: today+9}}`; add_note `{task_id: goal, fields: {text: "Steps drafted with Claude"}}` (each with a `label`, `evidence: []`, `applied: false`), `drafts_json` `[{"kind": "reply", "subject": "Seed6 · Committee form", "to": ["office@example.edu"], "account": "you@example.edu", "conversation_id": "seed6-conv", "draft_message_id": 1}]`, `rejected_json` `["Seed6 · drop Task-99999999: no Task-99999999"]`; (3) a today thread `chat.new_thread(conn, "Seed6 · chair signature", "today")` with user `I need the chair's signature on the committee form before I can send it` and assistant `That reads like a missing step of Task-<goal>, and Task-<step_freed> cannot go out before it.` with proposals create `{name: "Seed · Get the chair's signature on the committee form", project: P1, ref: "s1", links: [{part_of goal}]}` and link `{task_id: step_freed, fields: {kind: "waits_on", other: "s1"}}`. Output (printed, and `DEV/seed_byhand.json`): `S` plus `review_thread`, `review_message`, `today_thread`, `today_message`, `batches`, and `turns: {review: R, today: R}`, each `R` the turn's `done` result as the server would send it (`ok: true`, the ids, `reply_md`, `proposals`, `drafts`, `rejected`, `cost_usd`, `duration_ms`, `session_id: null`, `read`, `named` via `chat.named`, `origin`, `task_id`). `--remove`: undo the recorded batches newest first; one `ui` `drop` batch for every open task named `Seed6 · %` or with a `source_key` of `chat:<a seeded message id>:%`; delete the seeded threads and their messages; `seed_links.py --remove`; delete the JSON. `--fresh`: `--remove` when the JSON exists, then seed.
  - `DEV/scenarios/s6-lib.mjs`: `export function seed6(...flags)` (as 4b's `seed`); re-exports `noSideScroll`, `DEV` from `./s4b-lib.mjs`; and the stub (load-bearing, exact):

```js
// Answers POST /api/chat/turn with the stream the server sends; returns the list of request bodies it saw.
export async function stubTurn(context, result, { delayMs = 0 } = {}) {
  const posted = [];
  await context.route('**/api/chat/turn', async route => {
    posted.push(JSON.parse(route.request().postData() || '{}'));
    if (delayMs) await new Promise(r => setTimeout(r, delayMs));
    const evs = [{ type: 'meta', thread_id: result.thread_id, message_id: result.user_message_id }, { type: 'status', text: 'Thinking…' },
      { type: 'status', text: `Reading a task · ${result.task_id || ''} · task_get`, tool: 'task_get' }, { type: 'text', text: String(result.reply_md).slice(0, 120) },
      { type: 'result', cost_usd: result.cost_usd, ok: true }, { type: 'done', result }];
    await route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' }, body: evs.map(e => 'data: ' + JSON.stringify(e) + '\n\n').join('') });
  });
  return posted;
}
export const unstubTurn = context => context.unroute('**/api/chat/turn');
```

  - In `app.js` (the new section):
    - `isDraftBox(id) -> boolean` = `DRAFT_BOXES.includes(id)` or `id` matches `/^(ed-(name|context|who)|step-line)-\d+$/` (Tasks 13, 14 use the second form); `render()`'s focus restore and `typingNow()` use it instead of `DRAFT_BOXES.includes`.
    - `const places = new Map()`; `placeOf(origin: 'review'|'today'|'task', taskId: number|null) -> place`, `place = {key, origin, task_id, thread_id: null, messages: [], named: {}, live: null, loaded: false, off: new Set(), more: false}`.
    - `loadPlace(pl) -> Promise<void>`: `GET /api/chat/place?origin=&task_id=` → `thread_id`, `messages`, `named`, `loaded = true`; then `render()` unless `typingNow()` (then only the place's `[data-place="<key>"]` container is swapped and re-wired: `swapPlace(pl)`).
    - `readTurn(resp, onEvent) -> Promise<void>`: today's SSE loop of `sendChat`, moved out; `sendChat` uses it.
    - `sendTurn(pl, text, canned, draftKey) -> Promise<void>`: the caller has already cleared `draft[draftKey]`. Refused (puts the text back, ahead of anything typed since, and toasts) when `chat.busy` (`Claude is still answering · your text is kept`), on a 409 (`Claude is still answering in this conversation · your text is kept`), or when the request fails. Otherwise `chat.busy = true`, `chat.place = pl.key`, `pl.live = {status: 'Starting…', texts: []}`, POST `{thread_id: pl.thread_id, origin, task_id, text, attached: pl.task_id ? [pl.task_id] : attachedIds(), canned}`; `status` / `text` events update `pl.live` and `render()`; on `done` the user message `{role: 'user', text: text || <canned label>}` and `{id: R.message_id, role: 'assistant', text: R.reply_md, proposals, drafts, rejected, read, cost_usd, duration_ms}` are appended, `pl.thread_id = R.thread_id`, `Object.assign(pl.named, R.named)`, `chat.threads = null`, toast `Answered · $<cost>`; on `error` an assistant message `{role: 'assistant', error}` is appended and toast `Claude failed: <error>`; finally `chat.busy = false`, `chat.place = null`, `pl.live = null`, `render()`. Canned labels: `search_mail` `Search related emails`, `draft_reply` `Draft a reply`, `related` `What else is related?`, `split` `Split into steps`.
    - `proposalText(p, m, pl) -> {main: string, sub: string}`: create → main `New task: <name>`, sub from its links: `part of Task-N`, `waits on Task-N`, a ref → `waits on “<that sibling's name cut 40>”`, joined `, `; link / unlink / merge → main `Task-<task_id> ` + `changeWords` (a ref `other` → `now waits on: <sibling name>` / `is now part of: <sibling name>`); set → main `Task-N: ` + its fields in words (`due Fri Nov 20`, `due date removed`, `name “…”`, `text rewritten`, `project X`, `effort S`, `hard deadline`, `soft date`, `waiting on X`, `for X`, `later, back <date>`), others → `changeWords(p) || p.label`; sub gains `a change to another task, staged from here` when `p.task_id` is not `pl.task_id` (and not null), and `Task-N` names are `<a href="/t/N" data-open-task="N">`.
    - `placeHtml(pl, opts: {decision: boolean, title: string, hint: string, box: {id: string, key: string, first: string, next: string}}) -> string` (inside `<div class="place" data-place="<key>">`): a head (`title`, a spacer, `hint`); per message: user bubble (`.pmsg.user`), assistant bubble (`.pmsg.ai`, `md(text)`; an error as `The run failed: …`), `Read Task-a, Task-b and Task-c.` (`.pread`, links) when `read` is non-empty; for an assistant message with proposals the list (`.pprops`: head `Proposals` · `untick any you do not want`; one `<label class="pprop">` per proposal with a tick box `data-pprop="<key>|<mid>|<i>"`, checked unless in `pl.off`, the main text and sub; a staged one shows the chip `staged` instead of the box; an applied one `created Task-N` / `linked`; a create row also has `<button class="btn small" data-place-create="<key>|<mid>|<i>">Create</button>`); on a phone only the first two rows, then `and <n> more · <button data-place-more="<key>">Show</button>` until `pl.more`; under the newest assistant message's list the footer: `Stage all <n> with this decision` (`opts.decision`) or `Stage <n>` (`data-place-stage="<key>|<mid>"`, n = ticked, unstaged, unapplied) and the hint `One Apply, one Undo.`; drafts (`.pdrafts`: `✉ draft <subject> → <to>` and `Open in Mailbird`, the existing `/api/chat/drafts/open` call); rejected as `<details><summary><n> proposal(s) rejected by Factotum</summary>…</details>`; the live line (`.plive`: spinner, `pl.live.status`, the last text cut to 300) while running; the reply box `<textarea id="<box.id>" class="grow" rows="1" placeholder="<box.first or box.next>">` + `Ask` (`data-place-ask="<key>"`, disabled while `chat.busy`); chips `$0.21`, `24 s` of the newest assistant message and the link `Also kept as a thread in Chat` (`data-goto-chat="<thread_id>"`).
    - `stageProposal(p, mid, i)` (4b) gains `create` → `stageExtra('new:c' + mid + '-' + i, null, 'create', p.fields, 'new task: ' + cut60(name), {from_chat: {message_id: mid, index: i}})` and returns true; a `link` with a string `other` is labelled `now waits on “<sibling name>”` / `is now part of “<sibling name>”` (the sibling found in the same message by `fields.ref`). `stageKey` gains `create` → `'new:' + (suffix || Date.now())`. `applyAll` sends `from_chat` when the entry has it (still never `label` or `ev`).
    - `stagePlace(pl, mid, decision: boolean)`: stages every ticked, unstaged, unapplied proposal of that message through `stageProposal`; toast `<n> staged with this decision · decide on the card, then Apply` (Review) or `<n> staged · press Apply in the tray`.
    - Review: `renderReview` puts, after the card's extras (frame 4: at the bottom of the card), `placeHtml(placeOf('review', x.id), {decision: true, title: 'Ask Claude about this task', hint: 'the conversation stays on this card', box: {id: 'rv-ask', key: 'ask', first: 'Ask Claude about this task, e.g. “which steps are left?”; what it proposes stages with your decision', next: 'Ask a follow-up, or say what to change'}})`, and calls `loadPlace` once for a card's place that is not `loaded`. `draft.ask` / `draft.askFor` keep today's reset rule (a new card starts empty).
    - Phone keyboard: `fitKeyboard()` sets `--kb` on `document.documentElement` to `max(0, innerHeight − visualViewport.height − visualViewport.offsetTop)` px on `visualViewport` resize and scroll; on a phone a `focusin` on an `input`/`textarea` in `#panel` or `#sheet` adds `body.kb-open` and scrolls the box to the centre after 300 ms; `focusout` removes it.
  - CSS (tokens; frame 4 for the look): `.place` (top border, padding-top 12px, column, gap 10px), `.place .phead` (11px uppercase muted 600; the hint 12px normal), `.pmsg.user` (end-aligned, accent-soft, radius 12px, 13px, max 86%), `.pmsg.ai` (surface, line border, radius 12px), `.pread` (12px muted), `.pprops` (surface, line border, radius 10px, padding 8px 12px), `.pprop` (grid `20px minmax(0,1fr) auto`, padding 8px 0, top line, 13px; the main 500, the sub muted), `.pfoot` (flex, wrap, gap 8px), `.plive`, `.preply` (flex, end-aligned; the textarea 13px, `min-height: 44px`, background `var(--bg)`), `.pchips .chip` (mono, surface-2). Phone (`max-width: 700px`): `.review .card .big` fixed at the bottom (`left/right: 0`, safe-area padding, surface, top border, a four-column grid, buttons `min-height: 48px`), its `bottom` set in `wire()` to the tray's height when the tray shows (as the bulk bar's); `.review .card` `padding-bottom: calc(88px + var(--kb, 0px))`; the textarea and buttons 16px font and `min-height: 44px`; `body.kb-open .tray, body.kb-open .review .card .big, body.kb-open .pagebar, body.kb-open .fab { display: none }`.

- [ ] **Step 1: Write the seed and the lib**; run `DEV/serve.sh restart`, then `/home/robert/factotum/.venv/bin/python DEV/seed_byhand.py --fresh` twice, then `curl -s 'http://127.0.0.1:8751/api/chat/place?origin=review&task_id=<goal>'` (expect the seeded thread, two messages, five proposals, `named` with the goal and step_freed). Report the JSON's keys and ids.

- [ ] **Step 2: Implement** the section, the hooks, the CSS.

- [ ] **Step 3: Run the suite** — `/home/robert/factotum/.venv/bin/pytest -q`, all pass.

- [ ] **Step 4: Write and run `DEV/scenarios/s6-8-review.mjs`** (both devices; `S = seed6('--fresh')`; this scenario loads Review).
  - V1: `/review`; walk with Keep (`[data-rv="keep"]`) until the card's `data-id` is `S.goal`; then `Discard all` and walk again with Keep up to it (as 4b's R2: the Keeps are not wanted). Shot `s6-8-card`; compare frame 4 (desktop) / frame 10 (phone). Expected: the section `Ask Claude about this task` at the bottom of the card; the user bubble; the reply; `Read Task-<goal>, Task-<step_done> and Task-<step_freed>.`; five ticked rows: `New task: Seed · Get the chair's sign-off` / `part of Task-<goal>`; `New task: Seed · Fill out the committee form` / `part of Task-<goal>, waits on “Seed · Get the chair's sign-off”`; `Task-<step_freed> now waits on: Seed · Fill out the committee form` / `a change to another task, staged from here`; `Task-<goal>: due <Mon D>`; `Task-<goal>: note “Steps drafted with Claude”`; `Stage all 5 with this decision` · `One Apply, one Undo.`; the draft row with `Open in Mailbird` (NOT pressed); `1 proposal rejected by Factotum`; chips `$0.21`, `24 s`. Phone: the decisions pinned at the bottom (log the `.big` box: `bottom` within 2 px of the viewport's), two rows then `and 3 more · Show`.
  - V2 (Review Focus 1 and 4): `const posted = await stubTurn(context, S.turns.review, {delayMs: 3000})`; type `Which of these can wait?` into `#rv-ask`, press `[data-place-ask]`; within 1 s shot `s6-8-live` (the live line in place, Ask disabled); type `and the form?` into `#rv-ask`; on the desktop press Enter in the box (the button is disabled; Enter reaches `sendTurn`): the toast reads `Claude is still answering · your text is kept`; on a phone log that Ask is disabled. After the turn ends (wait 4 s): log `posted.length` (1), `posted[0]` (`origin` `review`, `task_id` `S.goal`, `thread_id` `S.review_thread`), and `#rv-ask`'s value (`and the form?`: it survived every re-render); the new user bubble and answer are on the card. `unstubTurn(context)`.
  - V3: untick the `note` row; press `Stage all 4 with this decision`: the toast; open the tray (`#tray-toggle`) → shot `s6-8-tray`: `new task: Seed · Get the chair's sign-off`, `new task: Seed · Fill out the committee form`, `Task-<step_freed> now waits on “Seed · Fill out the committee form”`, `Task-<goal> …due…`; the rows read `staged`. Press Keep on the card (the card advances). In the tray untick every entry whose task is not `S.goal`, `S.step_freed` or a `new:` key, then `Apply selected`; log the `/api/batches/apply` request (two creates carry `from_chat`) and response (`skipped` empty, two `created`); `GET /api/tasks/<step_freed>` has a `waits_on` relation to the second new task; the new tasks' `source` is `Chat` (via `/api/search`). Press the toast's Undo: log the undo response (`skipped` empty) and that `/api/search?q=Seed · Fill out` shows the task `dropped`. Shot `s6-8-applied`.
  - V4 (Review Focus 6, phone only): `S = seed6('--fresh')`, back to `S.goal`'s card; focus `#rv-ask`: log `getComputedStyle` of the pinned `.big` and of `#tray` (`display: none`) and that `#rv-ask`'s box lies inside the viewport; `page.setViewportSize({width: 390, height: 480})`, wait 400 ms, log the box again (inside); blur (click the card's title): the pinned bar is back (log). Restore 390x844. Shot `s6-8-typing`.
  - `noSideScroll` true on phone shots. End with `seed6('--fresh')`.

- [ ] **Step 5: Look at every screenshot (frames 4 and 10), then commit**

```bash
git add factotum/web/app.js factotum/web/app.css
git commit -m "web: the conversation lives on the Review card: a follow-up continues the card's thread, Claude's status and text show in place, every proposal (this task, other tasks, new tasks, links) is listed with a tick box and staged with the decision as one batch, drafts and rejections in place; on a phone the decisions are pinned and nothing covers the reply box"
```

---

### Task 9: The conversation under the Today box and in an opened task

Implementer: **opus**. Part B. UI task: yes.

**Files:**
- Modify: `factotum/web/app.js` — the stage 6 conversation section, `sendPrompt`, `renderToday`, `renderTimeline`, `renderProjects`, `detailsHtml` (actions), `taskPageHtml` (only if the place must be placed there explicitly), the `[data-chat-canned]` handler, `renderChat` (thread tags), `sendChat` (a 409), `draft`, `DRAFT_BOXES`, `typingNow`.
- Modify: `factotum/web/app.css`
- Create (outside the repository): `DEV/scenarios/s6-9-today.mjs`

**Interfaces:**
- Consumes: `placeOf`, `loadPlace`, `sendTurn`, `placeHtml`, `stubTurn` (Task 8).
- Produces:
  - `sendPrompt(text)`: a command stages as today; otherwise, on the Chat tab `sendChat(...)` as today; on any other tab `sendTurn(placeOf('today', null), text, null, 'cmd')` (the tab does not change).
  - `todayPlaceHtml() -> string`: when the today place has messages or a live turn: `placeHtml(today, {decision: false, title: 'Claude', hint: 'also kept as a thread in Chat', box: {id: 'today-ask', key: 'askToday', first: 'Ask a follow-up', next: 'Ask a follow-up'}})` plus `<button class="btn small" data-place-close="today:">Close</button>` (clears the place: `thread_id = null`, `messages = []`, so the next sentence starts a new conversation). Rendered right after `composer()` on Today, Timeline and Projects. The today place is loaded once per page load (`loadPlace`), so a conversation of the last 12 hours is still there after a reload.
  - The opened task: 4b's actions gain `<button class="btn small" data-ask-task="<id>">Ask Claude</button>`; `ui.askTask` (one task at a time) shows, at the end of `detailsHtml(x)` (desktop row and phone page alike), `placeHtml(placeOf('task', id), {decision: false, title: 'Ask Claude about Task-<id>', hint: 'the conversation stays on this task', box: {id: 'task-ask', key: 'askTask', first: 'Ask about this task', next: 'Ask a follow-up'}})`, loading it once. `draft.askTask` resets when `draft.askTaskFor` changes (like `draft.askFor`).
  - `[data-chat-canned]` inside an opened task: `ui.askTask = id; sendTurn(placeOf('task', id), '', canned, 'askTask')` (it used to switch to Chat).
  - Chat's thread list shows a tag before the date: `Review · Task-N`, `Task-N` or `Today` from the thread's `origin` / `task_id`.
  - `sendChat` reads a 409 as `Claude is still answering in this thread` and keeps the prompt.
  - `DRAFT_BOXES` gains `today-ask`, `task-ask`; `draft` gains `askToday`, `askTask`, `askTaskFor`; `typingNow` counts them.

- [ ] **Step 1: Implement.**
- [ ] **Step 2: Run the suite** (all pass).
- [ ] **Step 3: Write and run `DEV/scenarios/s6-9-today.mjs`** (both devices; `S = seed6('--fresh')`).
  - T1: `stubTurn(context, S.turns.today, {delayMs: 1500})`; on `/` type `I need the chair's signature on the committee form before I can send it` into `#cmd` and press `#cmd-go`. Log the path (still `/`) and, within 1 s, that `[data-place="today:"] .plive` exists. After the turn: shot `s6-9-today` (frame 6's rich path): under the box the bubble, the answer, `New task: Seed · Get the chair's signature on the committee form` / `part of Task-<goal>` with `Create`, `Task-<step_freed> now waits on: Seed · Get the chair's signature…`, `Stage 2`.
  - T2: press `Create` on the create row: toast `Created Task-<n>`; the row reads `created Task-<n>`; log that task's `source` (`Chat`, via `/api/search`).
  - T3: type `thanks` into `#today-ask`, press its Ask (the stub answers again): log the second request's `thread_id` (= the first answer's `thread_id`). `unstubTurn`.
  - T4: `/chat`: the thread list shows `Today` on `Seed6 · chair signature` and `Review · Task-<goal>` on `Seed6 · committee steps`; shot `s6-9-chat`.
  - T5: open `step_freed` (desktop: double-click its name; phone: tap it); with `stubTurn(context, Object.assign({}, S.turns.review, {origin: 'task', task_id: S.step_freed}))` press `Ask Claude what else is related`: log the request (`canned` `related`, `origin` `task`, `task_id` `S.step_freed`) and the path (unchanged: the row stays open / the page stays); the answer and its proposals show inside the opened task. Shot `s6-9-task`.
  - T6: Close the Today conversation: it disappears; reload `/`: it is back (the 12-hour rule) — log both.
  - `noSideScroll`. End with `seed6('--fresh')`.
- [ ] **Step 4: Look at every screenshot, then commit**

```bash
git add factotum/web/app.js factotum/web/app.css
git commit -m "web: plain text in the Today box is answered under the box (no more jump to Chat) and continues there; an opened task has Ask Claude and its canned buttons answer in place; Chat's thread list says where each thread was started"
```

---

### Task 10: New task on a desktop

Implementer: **opus**. Part B. UI task: yes.

**Files:**
- Modify: `factotum/web/app.js` — new section `// ---------- by hand (stage 6): New task ----------`; changes in `composer`, `sendPrompt`, `toast`, `noTasksHtml`, the keydown handler, `placeHtml` (the Edit first button), `draft`, `DRAFT_BOXES`, `typingNow`, `wire()`.
- Modify: `factotum/web/app.css`
- Create (outside the repository): `DEV/scenarios/s6-10-new.mjs`

**Interfaces:**
- Consumes: `GET /api/add/preview`, `POST /api/add` (Tasks 2, 3); `placeHtml`'s rows (Task 8).
- Produces:
  - `const ADD_RE = /^\s*(?:(?:add|new)\s+|\+\s*)/i`; `newKey() -> string` (12 base-36 characters from `crypto.getRandomValues`); `draft.newLine`, `draft.newKey`; `ui.newOpen`.
  - `toast(msg, undo, acts = [])`: `acts` are `{label, run}` buttons after Undo (keep any parameter stage 5 added; add this one last).
  - `handBarHtml() -> string` (list tabs, at the top of `composer()`; Task 11 adds Find to it): desktop `<button class="btn small" id="new-open">+ New task <kbd>n</kbd></button>`; nothing on a phone (Task 12's round button).
  - `newLineHtml() -> string` (while `ui.newOpen`): label `New task`, `<input id="new-line" autocomplete="off" placeholder="Email the lab the dataset fri #project ~S after 120">`, `<div data-add-prev="new">`, buttons `Add` (`#new-add`, primary) and `Add and open` (`#new-add-open`), hint `The task exists the moment you press Enter. Undo removes it.`
  - `addPreviewHtml(slot: 'new'|'cmd') -> string` from `addPrev[slot] = {text, res}` (frame 6): `Will be added as`; a `new` tag and the name; a chip per part (`label`; a link part's Task-N a link); the legend, one line per part `<span class="code">words</span> legend`; refused lines in crit `<span class="code">words</span> why`; `''` for no text.
  - `previewAdd(slot, text)`: 250 ms after the last keystroke, `GET /api/add/preview?text=`; an answer for older text is dropped; only `[data-add-prev="<slot>"]` is swapped.
  - `draft.newKey` is made by `newKey()` at start, again on every `input` event that changes the text of `#new-line`, `#cmd` (while it holds an add line), `#new-name` or a step line, and after every success: two Enters with nothing typed between them carry the same key (one task), an edited line carries a new one.
  - `addTask(input: {text}|{fields}, opts: {open?: boolean, clear?: () => void}) -> Promise<number|null>`: a module flag `adding` ignores a call while one is in flight; POST `/api/add` with `key: draft.newKey`; on success `opts.clear()` (the caller's own draft), `draft.newKey = newKey()`, `lastBatch = res.batch_id`, `await load()`, then `opts.open ? openTask(id) : (PHONE() ? null : jumpTo(id, false))`, and `toast('Added Task-<id>' + (res.already ? ' (already added)' : '') + (res.refused.length ? ' · kept in the name: ' + res.refused.map(r => r.words).join(', ') : '') + (res.skipped.length ? ' · ' + res.skipped[0].why : ''), true, [{label: 'Open', run: () => goTask(id)}])`; on failure the text stays and the toast reads `Not added: <detail or message>`. Returns the id.
  - `sendPrompt`: `ADD_RE` first → `addTask({text}, {clear: () => { draft.cmd = ''; }})` (no `/api/commands` call). While `draft.cmd` matches `ADD_RE`, the composer shows `<div data-add-prev="cmd">` under the box and typing in `#cmd` calls `previewAdd('cmd', …)`.
  - Keys: `n` (no box focused, no task page, desktop) → leave Review / Chat / Log / Settings for Today, `ui.newOpen = true`, render, focus `#new-line`. In `#new-line`: Enter → `addTask({text: draft.newLine}, {clear: () => { draft.newLine = ''; }})`; Escape → `ui.newOpen = false` (the text is kept in `draft`).
  - Edit first: a create row in `placeHtml` gains `<button class="btn small" data-edit-first="<key>|<mid>|<i>">Edit first</button>`; `addLineOf(fields) -> string` = the name, then `YYYY-MM-DD` (with `!` when hard), `#` + the project without spaces or punctuation, `~effort`, `under N` for an int `part_of`, `after N` for an int `waits_on`; a ref link is left out with the toast `A link to another new task is left out`; the line goes into `draft.newLine`, `ui.newOpen = true` (Today when the tab is not a list), focus.
  - `noTasksHtml()` (exact): desktop `<div class="empty"><b>No open tasks yet.</b> Press <kbd>n</kbd> or <b>New task</b> and type one line, for example <code>Email the lab the dataset fri ~S</code>, then Enter. Or write a sentence in the box above and Claude files it for you. Tasks also arrive from the inputs you switch on in Settings and from suggestions in Review. Coming from a Notion tasks database? See docs/SETUP.md §5.</div>`; on a phone `Press <kbd>n</kbd> or <b>New task</b>` becomes `Tap <b>+</b> at the bottom`.
  - `DRAFT_BOXES` gains `new-line`; `typingNow` counts `draft.newLine`.
  - CSS (frame 6): `.handbar` (flex, gap 8px, margin-bottom 10px); `.newline` (surface, line border, radius 8px, padding 12px; its input full width, 15px); `.addprev` (bg, line border, radius 8px, padding 10px 12px; `.lbl` 11px uppercase muted; the `new` tag accent-soft/accent mono; chips; `.legend` 12px muted with `.code` tokens; `.refused` crit); the buttons row with the hint.

- [ ] **Step 1: Implement.**
- [ ] **Step 2: Run the suite** (all pass).
- [ ] **Step 3: Write and run `DEV/scenarios/s6-10-new.mjs`** (desktop for N1–N4 and N7; both devices for N5 and N6). `S = seed6('--fresh')`; `P2` = the second project in `/api/tasks`'s `projects`, written compact (spaces and punctuation removed).
  - N1: `/`, press `n`: the line opens focused. Type `Email the lab the dataset fri #<P2 compact> ~S after <S.blocker>`; wait 500 ms; shot `s6-10-preview` (frame 6): `Will be added as`, `new` `Email the lab the dataset`, chips `Fri <Mon D>`, `<P2>`, `S`, `waits on Task-<blocker>`; the legend `fri is the due date`, `#… is the project`, `~S is the effort`, `after <blocker> makes it wait on Task-<blocker>`.
  - N2 (Review Focus 2): select all, type `Pay the 12 invoices after 99999999 #nosuchproject`: the preview's name is the whole line; refused `after 99999999 no Task-99999999; kept in the name` and `#nosuchproject no project matches #nosuchproject; kept in the name`; shot `s6-10-refused`. Replace with `done 12`: the preview's name is `done 12` (log). Clear the line.
  - N3 (Review Focus 3): type `Seed6 · Two quick Enters`, press Enter twice with no wait. Log the number of `POST /api/add` requests and their `key`s (one, or two with the same key), the toast (`Added Task-<n>` with Undo and Open), and `/api/search?q=Seed6 · Two quick Enters` (exactly one result). Shot `s6-10-added` (the new row focused and flashing). Press Undo: toast `Undone`; the search shows it `dropped`.
  - N4: type `Seed6 · Add and open`, press `Add and open`: the row is expanded (log `.row.open[data-id=<n>]`).
  - N5 (both devices): in `#cmd` type `add Seed6 · From the box tomorrow ~XS`: the preview under the box; press Send; log the task's `due` (tomorrow) and `effort` (`XS`) and that no tray entry appeared.
  - N6 (both devices): `DB=/tmp/claude-1000/-home-robert-factotum/85fa6951-ca9c-416e-9691-1a412f0fadea/scratchpad/dev/empty-s6.db DEV/serve.sh restart` (run by the scenario through `child_process`, or by the implementer before a separate run), load `/`: shot `s6-10-empty` (the new empty text; phone: `Tap + at the bottom`). Then `DEV/serve.sh restart` back to the dev database and delete the empty file.
  - N7: `stubTurn(context, S.turns.today)`; send the sentence of Task 9's T1 from `#cmd`; press `Edit first` on the create row: `#new-line` holds `Seed · Get the chair's signature on the committee form #<P1 compact> under <S.goal>` (log); shot `s6-10-editfirst`.
  - Every task this scenario added is undone at its end (keep each response's `batch_id`, `page.request.post('/api/batches/<id>/undo', {data: {}, headers: {'Content-Type': 'application/json'}})`). End with `seed6('--fresh')`.
- [ ] **Step 4: Look at every screenshot (frame 6), then commit**

```bash
git add factotum/web/app.js factotum/web/app.css
git commit -m "web: New task (n, or the button): one line with a live 'Will be added as' preview and the legend of what each word means; Enter makes the task at once with Undo and Open in the toast, two quick Enters make one; add … works in the box too; Edit first turns Claude's proposed task into that line; the empty Today tab says how to add a first task"
```

---

### Task 11: Finding any task, and the task picker it shares

Implementer: **sonnet**. Part B. UI task: yes.

**Files:**
- Modify: `factotum/web/app.js` — new section `// ---------- by hand (stage 6): finding any task ----------`; changes in `pickMatches` (now a caller of `matchOpen`), `handBarHtml`, the keydown handler, `draft`, `DRAFT_BOXES`, `typingNow`, `wire()`.
- Modify: `factotum/web/app.css`
- Create (outside the repository): `DEV/scenarios/s6-11-find.mjs`

**Interfaces:**
- Consumes: `GET /api/search` (Task 7); 4b's `goTask`, `openPage`, `act(id, 'reopen')`, `nameOf`.
- Produces:
  - `matchOpen(q: string, exclude: Set<number>) -> rows` (the body of today's `pickMatches`, which now calls it with `ui.sel`).
  - `searchTasks(q) -> Promise<{results, more}>` with a per-query cache cleared by every `load()`.
  - `findHtml() -> string` (while `ui.findOpen`, at the top of the list tabs under the hand bar): `<input id="find-q" placeholder="A task number, or words from its name, open or closed" autocomplete="off">` (value `draft.findQ`), then the rows from `found` (250 ms after the last keystroke): `Task-N` (mono), the name (struck through when closed), a chip `open` (plus the due label) / `done <Mon D>` / `dropped <Mon D>` / `faded <Mon D>` / `lapsed <Mon D>` / `merged into Task-M`, `Outcome: <outcome>` under a closed one, and the buttons `Open` (`data-find-open="<id>"`: open → `goTask`, closed → `openPage(id)`) and, for a closed task, `Reopen` (`data-find-reopen="<id>"` → `act(id, 'reopen')`; a staged one shows the chip `reopen staged`); `No task matches.`; `More match; add a word.` when `more`.
  - `handBarHtml()` gains `<button class="btn small" id="find-open">Find any task <kbd>/</kbd></button>` (on a phone it is the only button, full width, `min-height: 44px`).
  - The picker other tasks use: `pickerHtml(slot: 'link'|'merge'|'sheet', opts: {placeholder: string, closed: boolean, exclude: Set<number>}) -> string` = `<input id="pick-<slot>">` (value `draft.picks[slot]`) and at most 8 hits: `matchOpen` first, then (when `closed`) the closed rows from `searchTasks`, each `<button data-picker="<slot>" data-hit="<id>">` with `Task-N`, the name and its status chip; `pickers[slot] = (id) => …` is set by the caller and run on a hit or on Enter (the first hit).
  - Keys: `/` (no box focused, list tab) opens and focuses the finder; Escape closes it.
  - `DRAFT_BOXES` gains `find-q`, `pick-link`, `pick-merge`, `pick-sheet`; `draft.findQ`, `draft.picks = {}`; `typingNow` counts them.
  - CSS (frame 7, "Find any task"): `.find` (surface, line border, radius 8px); `.find .hit` (grid: code, name, chip, buttons; closed name struck through, muted); phone: rows wrap, buttons `min-height: 44px`; `.picker` like Chat's `.pick .hits`.

- [ ] **Step 1: Implement.**
- [ ] **Step 2: Run the suite** (all pass).
- [ ] **Step 3: Write and run `DEV/scenarios/s6-11-find.mjs`** (both devices; `S = seed6('--fresh')`).
  - F1: `/`, open the finder (`/` on desktop; `#find-open` on a phone), type `Seed ·`: open and closed seed tasks are listed; `step_done` reads `done <Mon D>` with `Outcome: The chair can serve remotely`, `dropped_blocker` `dropped <Mon D>`, `merged_away` `merged into Task-<merged_into>`. Shot `s6-11-find` (frame 7's search row).
  - F2: type `Task-<S.step_done>`: it is the first row; press `Open`: the read-only page `This task is done.`; Back returns to the list with the finder and its text still there (log `#find-q`'s value).
  - F3: press `Reopen` on `step_done`: the row shows `reopen staged` and the tray `Task-<step_done> reopened`; Discard all.
  - F4: type `zzzz-no-such-task`: `No task matches.`
  - F5: Chat's `+ Attach a task` still finds open tasks only (type `Seed ·` there: no closed ones) — log.
  - `noSideScroll`. End with `seed6('--fresh')`.
- [ ] **Step 4: Look at every screenshot, then commit**

```bash
git add factotum/web/app.js factotum/web/app.css
git commit -m "web: Find any task (/, or the button) searches open and closed tasks by number or words, says how and when each closed, opens it or stages Reopen; the task picker is shared and can reach closed tasks"
```

---

### Task 12: New task on a phone

Implementer: **opus**. Part B. UI task: yes.

**Files:**
- Modify: `factotum/web/index.html` (two elements after `#toast`), `factotum/web/app.js` — new section `// ---------- by hand (stage 6): New task on a phone ----------`; changes in `render()` (the bottom rule), the keydown handler, `draft`, `DRAFT_BOXES`, `typingNow`, `wire()`.
- Modify: `factotum/web/app.css`
- Create (outside the repository): `DEV/scenarios/s6-12-sheet.mjs`

**Interfaces:**
- Consumes: `addTask` (Task 10), `pickerHtml` / `pickers` (Task 11), `sendTurn` / `placeOf` (Task 8), `fitKeyboard` and `body.kb-open` (Task 8).
- Produces:
  - `index.html`: `<button class="fab" id="fab-new" aria-label="New task">` with an inline plus SVG, and `<div class="sheet" id="sheet" role="dialog" aria-modal="true" aria-label="New task" hidden></div>` plus `<div class="sheet-back" id="sheet-back" hidden></div>`.
  - The bottom rule in `render()`: `body.fab-on` when `PHONE()`, the tab is Today, Timeline or Projects, `ui.page === null`, nothing is staged, nothing is selected and the sheet is closed.
  - `ui.sheet = null | {due: string|null, pick: boolean, project: string, effort: string, links: [{kind, other}], hit: number|null}`; `draft.newName`; `openSheet()`, `closeSheet()`.
  - `sheetHtml() -> string` (frame 11, exact labels): a handle; `New task` with `Cancel`; `What needs doing` + `<input id="new-name">`; `Due` with `Today`, `Tomorrow`, `Fri`, `Pick a date` (a native date input) as toggles (`aria-pressed`); `Project` (a native select: `No project` + `data.projects`); `Effort` with `XS`, `S`, `M`, `L` toggles; `Linked to`: a chip per link (`waits on Task-N` / `part of Task-N` with a remove ×) and `Link a task` → `pickerHtml('sheet', {closed: true, …})`, then for the hit two buttons `waits on it` (disabled for a closed task) and `is a step of it` (one link of each kind; a second replaces the first); `Add task` (primary, full width) → `addTask({fields: {name, due, project, effort, after, under}}, {clear: () => { draft.newName = ''; }})`, closing the sheet on success; `Let Claude file it` with the hint `Type or dictate a whole sentence. Claude names it, files it and links it.` → the name text goes to `sendTurn(placeOf('today', null), text, null, 'newName')` after the sheet closes and the tab becomes Today.
  - `n` on a phone (a hardware keyboard) opens the sheet; the round button opens it.
  - `DRAFT_BOXES` gains `new-name`; `typingNow` counts `draft.newName`.
  - CSS (frame 11; frame 8 for the button): `.fab` (display none; `body.fab-on .fab` a 56px accent circle with the on-accent plus, fixed at `right: max(16px, safe-area)`, `bottom: calc(16px + env(safe-area-inset-bottom))`, shadow); `.sheet` (fixed, `left/right: 0`, `bottom: var(--kb, 0px)`, `max-height: calc(100dvh - 48px)`, scrolls, surface, top radius 16px, padding 16px with safe-area bottom); toggles 44px tall; the name input and select 16px; `.sheet-back` a dim full-screen backdrop that closes the sheet.

- [ ] **Step 1: Implement.**
- [ ] **Step 2: Run the suite** (all pass).
- [ ] **Step 3: Write and run `DEV/scenarios/s6-12-sheet.mjs`** (phone; on the desktop log only that `#fab-new` is not displayed). `S = seed6('--fresh')`.
  - P1: `/`: the round button at the bottom right (log its box: within 24 px of the right and bottom edges); shot `s6-12-fab` (frame 8). Tick a row: the button is hidden and the short bar shows (log); untick. Type `snooze <S.blocker>` into `#cmd` and Send: the tray shows and the button is hidden (log); Discard all. Open a task page: hidden (log); Back.
  - P2: tap the button: the sheet. Type `Seed6 · From the phone`, tap `Tomorrow`, choose the second project, tap `S`, tap `Link a task`, type `Get the dataset`, tap `Task-<S.chain_1>`, tap `waits on it`: the chip `waits on Task-<chain_1>`; shot `s6-12-sheet` (frame 11). Tap `Add task`: the toast `Added Task-<n>`; log that task's `due` (tomorrow), project, effort `S` and its `waits_on` relation to `chain_1` (`GET /api/tasks/<n>`).
  - P3 (Review Focus 6): open the sheet; focus `#new-name`: log that `#new-name` is inside the viewport and `#tray` / `#fab-new` are not displayed; `page.setViewportSize({width: 390, height: 480})`, wait 400 ms: `#new-name` still inside the viewport; scroll `Add task` into view inside the sheet and log it inside the viewport. Restore 390x844; Cancel.
  - P4: `stubTurn(context, S.turns.today)`; open the sheet, type `I need the chair's signature on the committee form before I can send it`, tap `Let Claude file it`: the sheet closes, the path is `/`, the conversation is under the box with its proposals; log the request (`origin` `today`). Shot `s6-12-claude`.
  - Undo every add of this scenario (as Task 10). `noSideScroll`. End with `seed6('--fresh')`.
- [ ] **Step 4: Look at every screenshot (frames 8 and 11), then commit**

```bash
git add factotum/web/index.html factotum/web/app.js factotum/web/app.css
git commit -m "web: on a phone a round New task button sits at the bottom when nothing else does; its sheet takes a name and taps for the date, project, effort and a link, adds at once, or lets Claude file a typed or dictated sentence; the keyboard never covers the name"
```

---

### Task 13: The opened task is the editor

Implementer: **opus**. Part B. UI task: yes.

**Files:**
- Modify: `factotum/web/app.js` — new section `// ---------- by hand (stage 6): editing in place ----------`; changes in `view`, `chipsHtml`, `detailsHtml` (4b's left column), `draft`, `typingNow`, `wire()`.
- Modify: `factotum/web/app.css`
- Create (outside the repository): `DEV/scenarios/s6-13-edit.mjs`

**Interfaces:**
- Consumes: 4b's `stageExtra`, `stageDue`, `textExpect`, `discardStaged`, `applyAll`, `whereHtml` (replaced for an open task), `isDraftBox` (Task 8).
- Produces:
  - `editKey(id, f) -> string` = `` `${id}:edit-${f}` `` for `f` in `name`, `context`, `project`, `effort`, `hard`, `who`.
  - `view(t)` also lays every staged `${id}:edit-*` entry's fields over the row (after the due entry, before the decision), so a staged edit shows on the row before Apply.
  - `keysFor(id) -> Set<string>`: every staged key that names the task (the bare id, `${id}:*`, a `rel:` key with it at either end, an `e:` entry whose `task_id` is it); `chipsHtml` adds `<span class="chip staged">N changes staged</span>` when the task has a staged entry other than its decision (N = all of them).
  - `editorHtml(x) -> string` (an open task, opened row or task page; frame 7's left column): `Name` `<input id="ed-name-<id>">`; `Where this stands` `<textarea id="ed-context-<id>" class="grow" rows="4">`; `Due` (a native date input) and `Project` (a native select, `No project` + `data.projects`); `Effort` `XS S M L` toggles; `Hard deadline: something breaks if this is late` (a checkbox, disabled without a due date); `Who is this for, or who are you waiting on` `<input id="ed-who-<id>">` with a select `is for` / `waiting on` (initially `waiting on` when the task has `waiting_on`, else `is for`). A field shows `draft.edit[id][f]` while typed, else the overlaid value. Then, when `keysFor(id)` is not empty, a line `Staged: <labels joined “, ”>.` with `Discard` (`discardStaged(keysFor(id))`) and `Apply` (`applyAll(keysFor(id))`).
  - Staging (each its own key, so edits sit beside the decision and each other): name on Enter or `change` → `{name}` with `expect: textExpect(id, {name})`, label `name “<cut60>”`; context on `change` or Ctrl+Enter → `{context}` with `expect`, label `text rewritten`; due → 4b's `stageDue`; hard → `{hard: 1|0}`, label `hard deadline` / `soft date`; project → `{project}` (`project X`, `null` → `no project`); effort → `{effort}` (`effort M`); who → `{waiting_on: v}` (`waiting on v`) or `{counterpart: v}` (`for v`), and changing the select with text in the field writes the text to the chosen column and `null` to the other. A value equal to the server's removes that key instead. The typed text then leaves `draft.edit[id]`.
  - `detailsHtml(x)`: for an open task the left column starts with `editorHtml(x)` instead of `whereHtml(x)` (4b's rewritten line and Linked tasks stay below it); a closed task is unchanged (read-only).
  - `draft.edit = {}` (id → `{name?, context?, who?}`); `typingNow` counts an entry that differs from the server's value.
  - CSS (frame 7): `.editor` grid of labelled fields (11px muted labels, inputs 13px, `.row2` two columns for Due / Project), effort toggles (`aria-pressed` accent-soft), `.edstaged` (accent-soft band, `Staged:` 600, buttons on the right); phone: one column, inputs 16px and `min-height: 44px`.

- [ ] **Step 1: Implement.**
- [ ] **Step 2: Run the suite** (all pass).
- [ ] **Step 3: Write and run `DEV/scenarios/s6-13-edit.mjs`** (both devices; `S = seed6('--fresh')`; `C = S.chain_1`).
  - E1: open `C` (desktop: double-click its name; phone: tap it): shot `s6-13-editor` (frame 7's left column): the fields with the task's values.
  - E2: name → `Seed · Get the cleaned dataset from the lab` + Enter; text → `The lab promised the cleaned set.` then blur; due → today + 3; tick hard; project → the first project; effort `M`; who → `the lab manager` with `waiting on`, then blur. Log the staged keys: `<C>:edit-name` (with `expect.name`), `<C>:edit-context` (with `expect.context`), `<C>:due`, `<C>:edit-hard`, `<C>:edit-project`, `<C>:edit-effort`, `<C>:edit-who`. Close the row (desktop) / go Back (phone): the row shows the new name, the due chip, `M` and `7 changes staged` before Apply; shot `s6-13-staged`.
  - E3 (Review Focus 5): another device writes: `page.request.post('/api/batches/apply', {data: {source: 'ui', changes: [{task_id: C, op: 'add_note', fields: {text: 'from another device'}}]}, headers: {'Content-Type': 'application/json'}})`. Reopen `C`, press the editor's `Apply`: the toast names the skip (`Task-<C> context changed since this edit was staged; not applied`); log `GET /api/tasks/<C>`: the new name, `effort` `M`, `waiting_on` `the lab manager`, a context ending `from another device` and not containing `The lab promised`. Shot `s6-13-stale`.
  - E4: press the toast's Undo: log the undo response.
  - E5: open `S.step_done` (closed): no `#ed-name-<id>` (log); the read-only page as 4b drew it.
  - E6 (Review Focus 1): open `S.chain_2`, type `half a sentence` into its text box without leaving it; `document.dispatchEvent(new Event('visibilitychange'))` (a return to the app); wait 1 s; log the box's value (unchanged) and that it still has focus.
  - Discard all. `noSideScroll`. End with `seed6('--fresh')`.
- [ ] **Step 4: Look at every screenshot (frame 7), then commit**

```bash
git add factotum/web/app.js factotum/web/app.css
git commit -m "web: an opened task is its editor (name, where it stands, due and hard deadline, project, effort, who it is for or who is awaited); each edit stages under its own key and shows on the row before Apply, a text edit carries what it started from, and the task's own staged line applies or discards just its changes"
```

---

### Task 14: Linking by hand, Add a step, and More

Implementer: **opus**. Part B. UI task: yes.

**Files:**
- Modify: `factotum/web/app.js` — new section `// ---------- by hand (stage 6): linking and More ----------`; changes in 4b's `linkedListHtml`, `linkedHtml` (a goal group's head), `detailsHtml` (actions), `composer` / `shortBar` (the bulk bar's Link), `draft`, `typingNow`, `wire()`.
- Modify: `factotum/web/app.css`
- Create (outside the repository): `DEV/scenarios/s6-14-link.mjs`

**Interfaces:**
- Consumes: `pickerHtml` / `pickers` (Task 11), `addTask` (Task 10), `sendTurn` / `placeOf` (Tasks 8, 9), 4b's `stageChange`, `defaultKeep(a, b)`, `goTask`, `linkedGroups`.
- Produces:
  - In `linkedListHtml(d)`: each `part_of` / `waits_on` entry of an open task gets `<button class="btn small" data-unlink="<src>|<kind>|<dst>">Remove</button>` (src/dst from `dir`), staging `stageChange({task_id: src, op: 'unlink', fields: {kind, other: dst}}, '')`; a staged removal draws the entry struck through with the chip `removal staged`. A merged (`same_as`) entry has no Remove.
  - Link a task (under Linked tasks, open tasks): `Link a task` (`data-link-open="<id>"`, `ui.linkFor`) → `pickerHtml('link', {placeholder: 'Link a task: a number, or a few words', closed: true, exclude: {id}})`; on a hit (`ui.linkHit`) the line `This task…` and three buttons: `is a step of it` → `{task_id: id, op: 'link', fields: {kind: 'part_of', other: N}}`; `waits on it` → `kind: 'waits_on'` (disabled, with the title `Task-N is <status>; nothing to wait on`, when the hit is closed); `is the same task` → keep = `N` when the hit is closed, else `defaultKeep(id, N)`; `{task_id: <the other one>, op: 'merge', fields: {into: keep}}`. Each through `stageChange(change, '')`; toast `Link staged · press Apply in the tray`.
  - Add a step (an open task with no goal): `<input id="step-line-<id>" placeholder="Add a step">` and `Add step`, with the hint `The step is created already linked, in the goal's project.`; Enter or the button → `addTask({fields: {name, project: x.project, under: x.id}}, {clear: () => { delete draft.step[x.id]; }})`; toast `Added Task-<n> as a step of Task-<id>`. `draft.step = {}` (id → text); `typingNow` counts it.
  - More (`data-more="<id>"`, `ui.moreFor`, in the actions; on a phone inside 4b's More): `Split into steps` → `ui.askTask = id; sendTurn(placeOf('task', id), '', 'split', 'askTask')`; `Duplicate` → `addTask({fields: {name, project, effort, due, hard, context, counterpart, waiting_on, under: rel.goal_id}}, {open: true})` (null fields left out); `Merge into another` → `pickerHtml('merge', {placeholder: 'Merge into: a number, or a few words', closed: true, exclude: {id}})`, a hit stages `{task_id: id, op: 'merge', fields: {into: N}}` with the toast `Merge staged: Task-<id> goes, Task-<N> stays`.
  - Two rows selected: the desktop bulk bar shows `Link` (`data-bulk="link"`); the phone short bar shows `Link` before `More` when exactly two are selected. `Link` opens (`ui.bulkLink`, `ui.bulkSwap`) the line `Task-A [is a step of] [waits on] [is the same task as] Task-B [⇄]` (A, B = the selection in tick order, swapped by ⇄); a choice stages `part_of` / `waits_on` from A to B or a merge with `defaultKeep(A, B)`, then clears the selection.
  - Make them tasks: in the Linked view, a goal group whose goal's context has at least two lines matching `/^\s*(?:[-*•]|\d+[.)])\s+\S/m` shows `Make them tasks` (`data-make-steps="<goal>"`) → `goTask(goal); ui.askTask = goal; sendTurn(placeOf('task', goal), 'Make them tasks', 'split', 'askTask')`.
  - CSS (frame 7, the right column): `.lk .rm` (the Remove button), `.lk.removing` (struck through, muted), `.linkpick` (picker plus `This task…` and the three buttons in a row; phone: stacked, full width, `min-height: 44px`), `.stepline`, `.more-row`, `.bulk .linkline`.

- [ ] **Step 1: Implement.**
- [ ] **Step 2: Run the suite** (all pass).
- [ ] **Step 3: Write and run `DEV/scenarios/s6-14-link.mjs`** (both devices; `S = seed6('--fresh')`).
  - L1: open `chain_3`: Linked tasks shows `waits on Task-<chain_2>` with `Remove`; press it: the entry is struck through, `removal staged`, the tray `Task-<chain_3> no longer waits on Task-<chain_2>`; shot `s6-14-remove`. Discard all.
  - L2: open `pair_a`; `Link a task`, type `Plan the lab tour`: the hit `Task-<goal_done>` reads `done …`; log that `waits on it` is disabled; press `is a step of it`: the tray `Task-<pair_a> is now part of Task-<goal_done>`. Discard all. `Link a task`, type `Task-<pair_b>`, press `is the same task`: the tray reads a merge of the newer into the older of the two (log which); shot `s6-14-link`. Discard all.
  - L3: open `goal`; type `Seed6 · Email the office to register` into `#step-line-<goal>`, Enter: the toast `Added Task-<n> as a step of Task-<goal>`; after the History reload Linked tasks lists it as a step (log `GET /api/tasks/<n>` relations: `part_of` `goal`); press the toast's Undo.
  - L4: open `step_open`; More → `Duplicate`: the toast `Added Task-<n>`; the copy is opened in the editor with the same name; its Linked tasks says `part of Task-<goal>` (log); Undo.
  - L5: open `twin_goes`; More → `Merge into another`, type `Task-<twin_kept>`, pick it: the tray `Task-<twin_goes> merged into Task-<twin_kept>`; the toast says who goes and who stays. Discard all.
  - L6: open `goal`; `stubTurn(context, Object.assign({}, S.turns.review, {origin: 'task'}))`; More → `Split into steps`: log the request (`canned` `split`, `origin` `task`, `task_id` `S.goal`); the proposals show in the opened task. Shot `s6-14-split`. `unstubTurn`.
  - L7: on `/timeline` tick `pair_a` then `pair_b`: the bar shows `Link` (on a phone in the short bar; log); press it, then `waits on`: the tray `Task-<pair_a> now waits on Task-<pair_b>`; Discard all; tick both again, `Link`, `⇄`, `is a step of`: the tray `Task-<pair_b> is now part of Task-<pair_a>`. Shot `s6-14-bulk`. Discard all.
  - L8: the Linked view: the `goal` group (its text lists three steps, from the seed) shows `Make them tasks`; with the stub, press it: log the request (`canned` `split`, `text` `Make them tasks`) and that `goal` is opened with the conversation.
  - Undo every add of this scenario. `noSideScroll`. End with `seed6('--fresh')`.
- [ ] **Step 4: Look at every screenshot (frame 7), then commit**

```bash
git add factotum/web/app.js factotum/web/app.css
git commit -m "web: link by hand from the opened task (find any task, then is a step of it / waits on it / is the same task), Remove a link, Add a step under a task, More: Split into steps, Duplicate, Merge into another; two selected rows offer Link; a goal whose text lists steps offers Make them tasks"
```

---

### Task 15: The stage's documents

Implementer: **sonnet**. Part B. UI task: no.

**Files:**
- Modify: `SPEC.md` (§2, §6 "Commands", §8.3, §9), `CLAUDE.md`

**Interfaces:**
- Consumes: the names every earlier task produced.

- [ ] **Step 1: `SPEC.md`.** §2: the sentence on pending batches (as stage 5 left it) also names, as an exception, a task added by hand: it exists the moment Enter is pressed, as its own `ui` batch, and its Undo drops it; edits to a task still stage. §6 "Commands": list the verbs the grammar really takes with their aliases — `done` (`finished`, `complete`, `completed`), `drop` (`remove`, `delete`, `cancel`), `snooze`, `push` (`defer`, `postpone`, `move`), `nodue` (`undate`, `undue`), `top` (`pin`), `unpin`, `longterm` (`long-term`, `lt`), `start`, `reopen` — and the add line (`add` / `new` / `+`: `#project` with its matching rule, `~effort`, `!`, a date, `after N`, `under N`; where each is read; a refused part stays in the name; `GET /api/add/preview`, `POST /api/add` with its key; `python -m factotum add`). §8.3: places (`origin`, `task_id`, the thread rules, the 409), what a turn result now carries (`read`, `named`), a link to a sibling create's ref, `from_chat` creates in the tray (resolved from the stored proposal, Source `Chat`, keyed like Create), the `split` canned prompt. §9: a paragraph **In place, by hand (stage 6, 2026-11-11)**: the conversation component and its three places; New task (line, preview, legend, Enter, the toast, Edit first; the phone sheet and its round button, the bottom rule); the editor and its keys; linking by hand, Add a step, More, the bulk bar's Link, Make them tasks; Find any task (`GET /api/search`, `python -m factotum find`); the empty Today text.
- [ ] **Step 2: `CLAUDE.md`.** Phase status: one sentence for stage 6 (2026-11-11). "Where things live" rows: "the add line, adding at once, search" → design §12.7, §12.9 → `factotum/commands.py` (`ADD_RE`, `parse_add`, `link_refusal`), `factotum/by_hand.py` (`preview`, `add`, `search`), `api.py` (`/api/add*`, `/api/search`); "the conversation in place" → design §12.6 → `chat.py` (`place_thread`, `PLACE_TEXT`, `resolve_staged`, `mark_staged`), `api.py` (`/api/chat/turn`, `/api/chat/place`), `web/app.js` (`placeOf`, `loadPlace`, `sendTurn`, `placeHtml`, `stagePlace`); "New task, the sheet, the editor, linking by hand, Find" → design §12.7–§12.9 → `web/app.js` (`addTask`, `previewAdd`, `addPreviewHtml`, `sheetHtml`, `editorHtml`, `editKey`, `keysFor`, `pickerHtml`, `searchTasks`, `findHtml`). Conventions, each dated 2026-11-11:
  - **A task added by hand exists on Enter** (`POST /api/add`, one `ui` batch, Source `Manual`); everything else a person edits stages. Its `key` makes a retry or a second Enter the same task (`source_key` `add:<key>`); a new line needs a new key (`newKey()`).
  - **An add line is never a tray command**: `commands.parse` returns None for it; `parse_add` reads it, a refused part stays in the name, and the page sends add lines to `/api/add` before `/api/commands`.
  - **A chat create in the tray is `from_chat`**: the server takes its fields from the stored proposal and sets `source_key` and `created_source`; the API strips both from what the page sends. A link may name a sibling create's `ref` only inside one batch with that create.
  - **One thread per task for a Review card and an opened task; one turn per thread** (409); the page runs one turn at a time and gives the text back when it cannot send.
  - **A new text box in the page needs `isDraftBox`** (`DRAFT_BOXES` or the `ed-…-<id>` / `step-line-<id>` forms), a `draft` entry and its `typingNow` count.
  - **On a phone one thing at the bottom** (tray, short bar, pinned bars, the round button; `body.fab-on`), and `body.kb-open` hides them all while a box has focus; a new bottom bar must join both rules.
  - **The `who` field writes `counterpart` or `waiting_on`**, never both.
- [ ] **Step 3: Run the suite** (all pass) and check the words: `grep -niE "\bblocked\b|\bunblocked\b|dependenc" SPEC.md CLAUDE.md` shows nothing this stage added. `docs/SETUP.md` is unchanged (no setup step changed); say so in the report.
- [ ] **Step 4: Commit**

```bash
git add SPEC.md CLAUDE.md
git commit -m "docs: stage 6, in place and by hand (the conversation on the card, under the box and in a task; New task on Enter with its preview and the phone sheet; the editor; linking by hand; Find any task), the real command verbs, and the gotchas learned"
```

---

## Stage check (by the controller)

After Task 7: the Part A checklist in the report. After Task 15: every `s6-*` scenario again for both devices on a fresh seed, plus `s4b-2-tray.mjs`, `s4b-6-detail.mjs`, `s4b-7-page.mjs`, `s4b-8-review.mjs` (4b regressions: the tray, the opened task, the page, Review) and stage 5's `s5-11-outcome.mjs` and `s5-12-review.mjs`; `pytest -q`; then `seed_byhand.py --remove` and confirm Today, Timeline, Review and Chat look as before at both sizes apart from the hand bar and, on a phone, the round button.
