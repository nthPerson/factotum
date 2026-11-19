# A spoken Review, Stage 1: the decision rule in Python, the tray on the server — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One Python rule says what a decision on a Review item stages, and the tray lives in SQLite so every device sees one tray; the page keeps working as it does, with Pin and Long-term no longer ending a Review card.

**Architecture:** Review's queue and its suggestion cards move out of `api.create_app` into `factotum/review_cards.py` and `review_queue.items`; a card's Create becomes data (`review_cards.card_changes`) that both the old route and the tray apply; `factotum/review_decide.py` is the rule (`plan`, pure) and `factotum/tray.py` the store (`read`, `write`, `carry`, `apply`), every write starting with `db.begin_write`. The page's `staged` Map stays and becomes a mirror of the server's tray.

**Tech Stack:** Python 3.12, FastAPI, SQLite (WAL, legacy transaction control), pytest offline; one `app.js` with no build step; playwright-core for the rendered checks.

**Spec:** `docs/superpowers/specs/2026-11-19-spoken-review-design.md`, sections 4 (the contract: use its names exactly), 5 (this stage), 9 to 12. This plan does not repeat the design's tables, signatures or test names; it adds what a reading of the code at `38b7170` gives. The code has not changed since `75d2c77` (`git diff --stat 75d2c77 38b7170` touches only docs), so the design's line numbers hold at HEAD; the few that are off are corrected below.

**Starting point:** `.venv/bin/pytest -q` → **1277 passed** (90 s) on `38b7170`.

## Global Constraints

- Work on `main` in `/home/robert/factotum`; small commits in the order of design §5.8 (Tasks 1 to 8 below); the WHOLE suite green before every commit (`.venv/bin/pytest -q`, about 95 s). Never skip, delete or weaken a test; a test that pins behaviour a commit changes is changed in that commit and named in the report.
- Tests first: write the design's named tests (§5.7) and this plan's, watch them fail, then make them pass.
- Commit message: one long plain sentence (what changed and why, in the manner of `git log`), a blank line, then `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. `git add` named files only, never `-A`; never push; leave the tree clean.
- Never touch the service or its data: no `systemctl`, no killing a process you did not start, no write to `data/factotum.db`, no scheduler, no paid Claude run, no write tool of Mailbird, Notion, Slack, Gmail or Calendar.
- Rendered checks (Tasks 6, 7): a backup-API copy of the database (`sqlite3.connect(src).backup(dst)`, never `cp`) under `/tmp/claude-1000/-home-robert-factotum/<session>/scratchpad/stage1`; `FACTOTUM_DB=<copy> .venv/bin/python -m factotum serve --port 8751 --no-scheduler` in the background (never piped through `tail`/`head`; note its PID; end only that process); playwright-core at `/home/robert/factotum/.worktrees/_scratch/dev/node_modules/playwright-core` with chromium at `/home/robert/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome`; 1280 × 900 and 390 × 844 (`hasTouch`, `deviceScaleFactor: 2`). Port 8751 busy means another builder's server: say so, use 8752. Look at every screenshot.
- After any edit of `factotum/web/app.js`: `node --check factotum/web/app.js` AND load the page (a `const` used above its line throws only at load).
- Every state-changing route is a plain `def` in `api.py`, JSON only, never 401/403/redirect, and takes `commit_lock` (last in the lock order `turn_lock` → `chat._CREATE_LOCK` → `by_hand._ADD_LOCK` → `commit_lock`).
- `batches.apply` stays the only write path for task state. No person-specific value in code. The words "blocked" and "dependency" appear in no label, `said` or toast.
- Review's day is `review_queue.today(cfg)` in every Review route, the rule and the tray's finish write; never `date.today()`.

## Review Focus

The five inputs the design implies that no named test covers and that would hit the owner first, each with the test this plan adds to the task that owns the code:

1. **An old browser tray carried with a bare-key Pin.** Every tray saved before Task 6 holds `[412, {op: "pin_top"}]` under the bare id; carried as is, `decides_of` makes Task-412 "decided" and it vanishes from Review, the very bug §5.6 item 1 fixes. Expected: the carry re-keys a bare `pin_top` / `unpin` / `set {top_rank}` to `<id>:top` and a bare `longterm` / `set {longterm}` to `<id>:lt`, and they decide nothing. Test (Task 4): `test_a_carried_old_pin_or_longterm_moves_beside_and_decides_nothing`.
2. **A card answered on another device after its Create was staged here.** Expected: Apply makes no task, writes no verdict twice, removes the row and says why in `skipped`. Test (Task 4): `test_a_staged_create_of_a_card_answered_since_is_skipped_with_its_reason`.
3. **One bad row in the tray** (a `from_chat` naming a message that no longer holds a create: `chat.resolve_staged` raises `ValueError`). Expected: Apply of the whole tray answers 400 naming the reason and leaves the tray as it was; Apply of the other keys still works. Test (Task 5): `test_a_bad_row_refuses_the_apply_it_is_in_and_not_the_others`.
4. **The evening hours where the machine's date and the owner's differ** (another deployment). Expected: `GET /api/review`, the count, the finish stamp and the rule all use the owner's date. Test (Task 1): `test_reviews_day_is_the_owners_not_the_machines` with `owner.timezone: Pacific/Kiritimati` (UTC+14, never the same date as a UTC-11 or a UTC-8 machine for most of the day; assert against `datetime.now(ZoneInfo(...)).date()`).
5. **Several quick changes on the page while a tray request is in flight.** Expected: each reaches the server once, in order, and nothing staged in between is lost or sent twice. The diff must be computed when the queued send runs, not when it is queued. Check (Task 6, rendered): stage three changes within 200 ms on a throttled connection (`page.route` delaying `/api/tray/ops` by 800 ms), then read `GET /api/tray`: exactly those three keys.

---

## Traps found in the code (read before any task)

1. **`review_decide.stage` needs `tray` and `db.begin_write`, which arrive in commit 4.** Commit 3 builds everything in `review_decide` except `stage`; `plan` takes the tray as the shape `tray.read` returns (`{"rev", "entries": [[key, entry, meta], ...]}`) so its tests build one by hand. `stage` lands in Task 4.
2. **`tray.apply` must not import `review_pass`** (Stage 2's). Leave no call and no try-import; Stage 2 adds the line in step 6 of §5.3.
3. **The lock test only sees module-qualified calls with `conn` as the first argument** (`tests/test_review_fixes.py:92-101`): import the new modules in `api.py` as `tray`, `review_decide`, `review_cards` (never `as T`), call them as `tray.apply(conn, …)`, and never name a local variable `tray` in `create_app`. A module function that commits and is called from a route joins `_WRITERS` (`tests/test_review_fixes.py:77-80`).
4. **`api.SUGGESTION_KINDS` must survive the move** (`api.py:156`): `tests/test_stage8.py:42` asserts `api.SUGGESTION_KINDS is review_queue.SUGGESTION_KINDS`.
5. **The day's link-suggestion session must be written before `items` reads.** `api.py:547-548` writes `suggestions.session` and commits; `relations.annotate` (inside `decorate`) reads `shown_ids` from it. `items` never writes, so `GET /api/review` keeps the session write and its commit *before* calling `items`, inside its `commit_lock`.
6. **Excluding after `items` is the same as excluding before.** Today `_review` drops `exclude` ids before scoring (`api.py:549`); `items` never excludes. `lifecycle.build_review_queue` decides and sorts each task alone (`lifecycle.py:183-187`), so dropping excluded items from `items`' answer gives the same list. `total` is counted after the drop.
7. **`texts` and `link_suggestions` are built from the full `rel` before `ship_rel` compacts it** (`api.py:553-561`). Keep that order inside `items`.
8. **Python's sqlite3 here uses legacy transaction control** (`isolation_level` default, Python 3.12.3): no BEGIN for a SELECT, an implicit BEGIN before the first DML. `begin_write` is `if not conn.in_transaction: conn.execute("BEGIN IMMEDIATE")`. Never call `executescript` inside a write (it commits first). On the API's shared connection, `in_transaction` while holding `commit_lock` means a writer before forgot to commit; do not try to repair it there.
9. **`batches.apply` commits, also when everything was skipped** (`batches.py:250-262`), and rolls back on any exception (`:246-248`), which also rolls back the tray's DELETE (that is the design's "both or neither"). After its commit it runs `suggestions.after_create`, which commits again (`suggestions.py:397-407`).
10. **The decide route's error answers carry the tray beside `detail`.** `HTTPException(detail=…)` cannot add a sibling key, and the page's `api()` reads `j.detail` as a string (`app.js:85-90`): answer `JSONResponse({"detail": "<words>", "tray": tray.read(conn)}, status_code=…)`. Roll back before reading the tray.
11. **`stageProposal` mutates a staged entry in place** (`app.js:2230`: `e.prop = …` then `persistStaged()`). `trayDiff` compares by value, so `tray.base` must hold its own copies (build `staged` from a separate `JSON.parse`, or keep `base` as a Map of JSON strings).
12. **`reorderTop` stages `set {top_rank}` under the bare id** (`app.js:4191-4197`), and `stageProposalAs` stages a chat `pin_top` / `longterm` under the bare id (`app.js:2258`, keyed by `propKey`, `app.js:2223`). Both must move to `<id>:top` / `<id>:lt` in Task 6, or `decides_of` marks those tasks decided.
13. **The source pin for "no `localStorage.setItem('factotum.staged'`" would match the backup key `factotum.staged.backup`.** Pin the exact `setItem('factotum.staged',` (with the comma) and `removeItem('factotum.staged')`.
14. **`afterPress` and `pressing` are declared at `app.js:3699-3702`**: anything the boot path calls synchronously at the top of the script must not reach them. Declare `const tray = …` next to `staged` (`app.js:15`) and call `trayBoot()` only from `load()` (async).
15. **The page's own staging never sends `decides`.** Between Tasks 6 and Stage 2 the four buttons still stage in the page: their `"<id>"` row decides through `decides_of`, but the `e:` judges and `"<id>:note"` they stage beside carry none. An `undecide` or a re-decision through the rule after a button decision therefore leaves those beside it. Harmless in Stage 1 (only the conversation decides through the rule); Stage 2 moves the buttons. Say so in the report; do not widen the route to trust a page-sent `decides`.
16. **The design's `api.py:1300-1301` for the connector's writer is `api.py:1302`** today. Not used in Stage 1.
17. **`reviewShown` counts a card whose Create or Dismiss is staged** (`app.js:364-368`): after Task 7 filter the cards with `!staged.has('e:' + x.id)` too, or the number beside Review is one too high after a staged Create.

---

### Task 1: `review_cards`, `review_queue.items`, `review_queue.today` (a pure move; the Review routes use Review's day)

**Files:**
- Create: `factotum/review_cards.py`, `tests/test_review_cards.py`
- Modify: `factotum/review_queue.py`, `factotum/relations.py`, `factotum/api.py:304-328, 342, 353, 361, 402, 536-636, 696, 713, 745-749, 952, 973-977`

**Interfaces:**
- Produces: `review_queue.today(cfg) -> date`; `review_queue.items(conn, cfg, today) -> {"tasks": [...], "cards": [...]}` (every task item has `key: "t:<id>"`, every card `key: "s:<eid>"`; the items' other keys exactly those `GET /api/review` ships today); `review_cards.evidence_by_task(conn, ids=None)`, `cards(conn, cfg)`, `card(conn, cfg, evidence_id) -> dict | None` (None: not a card, or judged), `row(conn, evidence_id) -> dict | None` (today's `suggestion_row`, judged or not), `suggestion_fields(cfg, e)`, `suggestion_mail(e)`, `suggestion_links(e)`; `relations.decorate(db, ts, today, cfg)`, `relations.ship(t)`, `relations.briefs(db, ids)`.

**Where each piece is now and how it gets out:**

| Piece | Now | Closes over | Leaves as |
|---|---|---|---|
| `decorate_all` | `api.py:304-315`; called at `:342` (`list_tasks`), `:402` (`get_one`), `:549` (`_review`) | `conn` | `relations.decorate(db, ts, today, cfg)`, importing `lifecycle` and `parse_date` inside the function (lifecycle imports relations lazily at `lifecycle.py:147`; keep both lazy). It calls `relations.scored`, never `scoring.score` (`test_only_relations_calls_scoring_score`), and `tests/test_stage4_derived.py:93-114` monkeypatches `scoring.score` and `lifecycle.review_reasons` by module attribute: call them as `lifecycle.…` |
| `ship_rel` | `api.py:317-321`; `:353`, `:561` | nothing | `relations.ship(t)` |
| `task_briefs` | `api.py:323-328`; `:361`, `:553`, `:580` | `conn` | `relations.briefs(db, ids)` |
| the read part of `_review` | `api.py:549-582` | `conn`, `eff_cfg`, the three above, the suggestion helpers | `review_queue.items`; `review_flags` and `ripple.since` imported inside it (ripple imports planner, sync_file, suggestions: keep `review_queue`'s top imports as they are, since `scheduler` and `app_push` import it) |
| the evidence query | `api.py:562-575` | `conn` | `review_cards.evidence_by_task(conn, ids=None)` |
| the cards | `api.py:578-582` | `conn`, `ecfg` | `review_cards.cards(conn, cfg)`; `card(…)` is the same for one id |
| `suggestion_row` | `api.py:595-598`; used by `chat_turn` at `:952`, `:973` | `conn` | `review_cards.row(conn, evidence_id)` |
| `_suggestion_mail` / `_links` / `_fields` | `api.py:600-636`; used at `:578-579`, `:662`, `:666`, `:701`, `:731`, `:976-977` | `C` only | `review_cards.suggestion_mail(e)`, `suggestion_links(e)`, `suggestion_fields(cfg, e)` |

What stays in the route `GET /api/review` (`api.py:526-534, 542-593`): `commit_lock`; `suggestions.session(conn, today, …)` + commit; `items(conn, ecfg, today)`; drop items whose task id is in `exclude_ids(exclude)`; `cap`; the `last_offered_at` stamp + commit on the page; the answer's keys as today (`today`, `last_review_at`, `cap`, `total`, `labels`, `items`, `suggestions`).

- [ ] **Step 1: Write the failing tests** in `tests/test_review_cards.py` (module docstring `"""Spoken Review, stage 1: Review's queue and cards outside the API, and Review's day."""`):
  - `test_items_reads_only_and_holds_the_whole_queue`: 20 new tasks (`mk`), a card (`_card` as in `tests/test_stage8.py:22-24`), a task with a `thread_updated` evidence row; `before = conn.total_changes`; `q = review_queue.items(conn, cfg, date.today())`; assert `conn.total_changes == before`, `len(q["tasks"]) == 21` (no cap), every task item has `key == f"t:{id}"` and the keys `task, reasons, primary, texts, link_suggestions, flags, evidence`; `q["cards"][0]["key"] == f"s:{eid}"`.
  - `test_get_review_is_items_with_its_cap_its_exclude_and_its_stamp`: on the same setup through `TestClient`, `GET /api/review?exclude=1,2` → `[i["task"]["id"] for i in items]` equals the first 15 of `items(...)` with 1 and 2 left out; `total == 19`; exactly those 15 rows have `last_offered_at` set.
  - `test_reviews_day_is_the_owners_not_the_machines`: `C.set_(cfg, "owner.timezone", "Pacific/Kiritimati")`; `want = datetime.now(ZoneInfo("Pacific/Kiritimati")).date()`; `review_queue.today(cfg) == want`; `GET /api/review` answers `today == want.isoformat()`; `POST /api/review/finish` then `get_settings(conn)["state.last_review_at"] == want.isoformat()`.
- [ ] **Step 2: Run them**: `.venv/bin/pytest tests/test_review_cards.py -q` → FAIL (`review_queue` has no `items` / `today`).
- [ ] **Step 3: Move the closures** to `relations.decorate`, `relations.ship`, `relations.briefs` and change their six call sites in `api.py`. Run `.venv/bin/pytest -q tests/test_stage4_derived.py tests/test_core.py` → PASS.
- [ ] **Step 4: Create `review_cards.py`** with the functions of the Interfaces block (bodies moved verbatim from `api.py:562-636`; `SUGGESTION_KINDS` from `review_queue`) and switch `chat_turn` (`api.py:952, 973-977`) and `open_suggestion` (`:731`) to them.
- [ ] **Step 5: Add `review_queue.today` and `review_queue.items`**; `today` imports `reminders` inside the function and returns `reminders.local_now(cfg).date()`.
- [ ] **Step 6: Rewrite `_review` on `items`** (the "stays in the route" list above), and give `GET /api/review`, `GET /api/review/count` (`:540`), `POST /api/review/finish` (`:747`) and the create route's two `batches.apply` dates (`:696`, `:713`) `review_queue.today(eff_cfg())`. Keep `SUGGESTION_KINDS = review_queue.SUGGESTION_KINDS` at `api.py:156`.
- [ ] **Step 7: Run the new tests, then the whole suite**: `.venv/bin/pytest -q` → 1280 passed. The 30 `GET /api/review` calls in 13 files (`test_core.py:361,363,383,396,400,475`, `test_stage5.py:340,348,357,495,525`, `test_phase6.py:585,595,598`, `test_phase10.py:167,177,429`, `test_polish.py:71,84,278`, `test_stage7_runs.py:173,181`, `test_stage8.py:38`, `test_stage4_api.py:80`, `test_stage4b.py:27`, `test_stage4_derived.py:114`, `test_stage7_filing.py:64`, `test_stage7_detect.py:256`, `test_stage4_merge.py:314`) must pass unchanged: a failure there means the move changed behaviour; fix the move, not the test.
- [ ] **Step 8: Commit** `factotum/review_cards.py factotum/review_queue.py factotum/relations.py factotum/api.py tests/test_review_cards.py`.

### Task 2: `review_cards.card_changes`; the Create route built on it

**Files:**
- Modify: `factotum/review_cards.py`, `factotum/api.py:638-723`, `tests/test_review_cards.py`

**Interfaces:**
- Consumes: Task 1's `row`, `suggestion_fields`, `suggestion_mail`, `suggestion_links`.
- Produces: `review_cards.card_changes(conn, cfg, evidence_id, draft, today) -> CardPlan` with `CardPlan = NamedTuple(changes: list[dict], skipped: list[str], post: list[dict], summary: dict)`. `draft` is `None` or `{"fields": dict | None, "links": list | None}` (the route's `SuggestionCreateBody`). `summary = {"task_id": int | None, "reopened": bool, "already": bool, "ref": "card<eid>"}`. `post` is the follow-up rows: for a Slack card `[{"task_id": None, "kind": "slack_message", "ref": e["ref"], "label": …[:120], "account": …}]` (`task_id` None means the task the create makes). Raises `LookupError("no such suggestion")` (not a card), `LookupError("this suggestion was already judged")` (judged, no task made from it) and `ValueError("a task needs a name")`; the route maps them to 404 / 404 / 400 with those words.

**Where it is now:** `api._create_suggestion` (`api.py:655-723`) closes over `conn`, `eff_cfg`, the suggestion helpers, `ripple.ACCOUNT`, `SUGGESTION_KINDS`, `chat.clean_draft`, `sync_file._clean_task_fields`, `batches.apply`, `db.add_link` and `HTTPException`; its nested `thread(tid)` / `follow(tid)` (`:677-682`) become local helpers of `card_changes` taking the ref. Everything up to each `batches.apply` call (`:656-695` and `:698-712`) is the build; `:696-697` and `:713-723` are the route's. The create's ref changes from `"card"` (`:709`, `:713`) to `f"card{evidence_id}"` (no test reads it: `grep -rn '"card"' tests/` is empty).

What stays in the route (`create_suggestion`, `:638-653`): `commit_lock`; `card_changes`; with no changes, the old `already` answer; else `batches.apply(conn, plan.changes, "review", review_queue.today(ecfg))`; the made-meanwhile fallback of `:714-718` (a `source_key` lookup); the post (`add_link` + `conn.commit()`, both already in `_WRITERS`); the old answer `{task_id, batch_id, skipped, reopened, already}`. Keep it inside `create_app` so the lock test sees `batches.apply(conn, …)` and `add_link(conn, …)` under the lock.

- [ ] **Step 1: Write the failing tests** in `tests/test_review_cards.py`:
  - `test_card_changes_writes_nothing_and_is_what_the_route_applies`: four cards (a planner create with fields, a mail-triage proposal with `mail` and `links`, a `slack_mention` with a workspace project in cfg, a ripple proposal with `account = 'ripple'`) on two databases built the same way; on A, `card_changes` (assert `total_changes` unchanged) then `batches.apply(conn, plan.changes, "review", D)`; on B, `POST /api/review/suggestions/{id}/create`; the `changes` table rows `(task_id, field, old, new)` are equal, and so are the created tasks' `source, source_key, source_link, account, name, project, due, context`.
  - `test_two_cards_in_one_batch_get_their_own_refs`: `batches.apply(conn, p1.changes + p2.changes, …)` makes two tasks; each mail card's `mail_thread` link is on its own task.
  - `test_card_changes_refuses_an_empty_name_and_a_judged_card`: `draft={"fields": {"name": "  "}}` → `ValueError`; a judged card with no task → `LookupError`; a judged card whose task exists → `CardPlan(changes=[], …, summary.already=True)`.
- [ ] **Step 2: Run them** → FAIL (`card_changes` missing).
- [ ] **Step 3: Implement `card_changes`** by moving the build out of `_create_suggestion`; then rewrite the route on it.
- [ ] **Step 4: Run** `.venv/bin/pytest -q tests/test_review_cards.py tests/test_core.py tests/test_phase10.py tests/test_polish.py tests/test_search_edit_chat.py tests/test_stage5.py tests/test_stage7_runs.py tests/test_phase6.py` (the 19 calls of the Create route pin its answers: 404 for a judged card at `test_core.py:395`, idempotence at `:394`, the draft, Create after Undo) → PASS; then the whole suite.
- [ ] **Step 5: Commit** `factotum/review_cards.py factotum/api.py tests/test_review_cards.py`.

### Task 3: `factotum/review_decide.py` with its table tests (no route, no `stage`)

**Files:**
- Create: `factotum/review_decide.py`, `tests/test_review_decide.py`
- Modify: `tests/test_stage4_agent.py:462-466`

**Interfaces:**
- Consumes: `review_queue.items` / `review_cards.evidence_by_task`, `review_cards.card` (Tasks 1-2), `chat.clean_draft` (`chat.py:205`), `batches._reminder_fields` (`batches.py:64`), `relations.default_survivor` (`relations.py:131`, returns `(keep, goes)`).
- Produces: everything of design §5.2 except `stage` (Trap 1): `OPTIONS`, `LATER_WORDS`, `OUTCOME_MAX`, `NOTE_MAX`, `Gone`, `Refused`, `Stale`, `Plan`, `item_key`, `parse_item_key`, `parse_option`, `load_item`, `later_choices`, `plan`, `from_proposal`, `stage_key`, `text_expect`, `day_words`, `when_words`, `change_words`, `set_words`, `cut60`. `plan`'s `tray` argument is `tray.read`'s shape.

**What the page does today, and so what the table must reproduce** (all in `factotum/web/app.js`):

| Option | The page's code |
|---|---|
| done / drop / keep / later, the note rule, the judges | `reviewAct`, `:4198-4229` (labels: `'done'`, `'dropped'`, `'kept'`, `'later · back ' + label`; judges `'suggestion seen'` for a `proposal`, else `'thread seen'`) |
| `later` days | `isoPlus(1)`, `nextFri()` (`:117`: `(5 - getDay() + 7) % 7 || 7` days), next week `isoPlus(7 - ((getDay() + 6) % 7))` (`:4220`); a picked date's label is the ISO date itself (`:4221`) |
| accept / dismiss | `[data-rv-accept]`, `:3975-3990` |
| follow / not_about | `:3992-4000` (labels `follows “<subject>”` or `follows the mail thread`, `mail card accepted`, `not about this mail`) |
| link / step / waits / merge / same / not_related | `linkSuggestionAct`, `:1901-1913`; `suggestionKeep` / `defaultKeep` `:1877-1883` (Python: `s["keep"]` else `relations.default_survivor(conn, card, other)[0]`) |
| keys | `stageKey` `:861-870`, `propKey` `:2223` |
| words | `cut60` `:871`, `changeWords` `:881-888`, `setWords` `:2490-2500`, `dayWords` `:2489` (`"Fri Nov 14"`), `fmtWhen` `:132` (`"Fri 9:00 AM"`) |
| `textExpect` | `:897-902` (`context`, `name`: the server value or `""`) |

`stage_key` follows the page as it will be after Task 6: `pin_top` / `unpin` / a `set` holding only `top_rank` → `"<id>:top"`; `longterm` / a `set` holding only `longterm` → `"<id>:lt"`; the rest as `stageKey`.

- [ ] **Step 1: Write the failing tests** in `tests/test_review_decide.py`, every name of design §5.7's first list, on the fixture it describes, with `TODAY = date(2026, 11, 19)` passed as `today`. `test_later_dates_are_the_pages` pins (`tomorrow`, `fri`, `nextweek`): Thursday 2026-11-19 → 11-20, 11-20, 11-23; Friday 11-20 → 11-21, 11-27, 11-23; Sunday 11-22 → 11-23, 11-27, 11-23. `test_a_decision_from_the_cards_conversation_is_the_buttons_decision` writes a `chat_threads` row (`origin='review'`, `task_id`) and a `chat_messages` row with `proposals_json` (`chat.py` stores them; see `place_thread`, `chat.py:87`) and compares `from_proposal` + `plan` against `plan` with the button's option. Add `test_parse_option_and_item_key_refuse_what_is_not_theirs` (`"accept:x"`, `"g:rec:1"`, `"t:-1"` → `Refused`).
- [ ] **Step 2: Extend the forbidden-words test** (`tests/test_stage4_agent.py:462`): add the labels and `said` texts `plan` produces over the fixture of `test_every_option_on_every_item_kind_stages_what_the_page_staged` (import the fixture builder from `test_review_decide`, or list the module's fixed strings). This is an extension, not a weakening; name it in the report.
- [ ] **Step 3: Run** `.venv/bin/pytest -q tests/test_review_decide.py` → FAIL (no module).
- [ ] **Step 4: Implement** `review_decide.py`. `load_item("t:<id>")`: `get_task`; not open → `Gone`; the item is built as `items` builds one (call a shared helper in `review_queue` for one task, or `items` filtered to it; an open task with no reason gets `reasons: []`, not `Gone`, so a conversation decision on a card whose reason has just gone still works). Reads only.
- [ ] **Step 5: Run** the new tests, then the suite → green.
- [ ] **Step 6: Commit** `factotum/review_decide.py tests/test_review_decide.py tests/test_stage4_agent.py`.

### Task 4: `db.begin_write`, the tray tables, `factotum/tray.py`, and `review_decide.stage`

**Files:**
- Create: `factotum/tray.py`, `tests/test_tray.py`
- Modify: `factotum/db.py` (`SCHEMA`, new `begin_write` after `connect`, `:169-182`), `factotum/review_decide.py` (`stage`)

**Interfaces:**
- Consumes: `review_cards.card_changes` (Task 2), `review_decide.plan` / `load_item` / `from_proposal` (Task 3), `chat.resolve_staged` / `mark_staged` (`chat.py:660, 706`), `batches.apply`, `batches.OPS` (`batches.py:19`), `db.set_setting`, `db.add_link`.
- Produces: design §5.3 exactly (`STRIP`, `SENT`, `META`, `OPS`, the limits, `KEY_RE`, `Stale`, every function), `db.begin_write(conn)`, `review_decide.stage(...)`.

**Decisions this task makes:**
- `decides_of` (deviation from the letter of §5.3, kept to its intent): a key of ASCII digits decides `t:<key>` only when its op is `done`, `drop`, `keep`, `reopen` or a `set` that holds `snoozed_until`; any other op under a digit key decides nothing. This is what makes Review Focus 1 safe for trays from older pages and for any page code that still stages under the bare id.
- `carry` re-keys an old bare-key `pin_top` / `unpin` / `set {top_rank}` to `"<id>:top"` and `longterm` / `set {longterm}` to `"<id>:lt"` before anything else, applies the `app.js:64` fix (a missing `task_id` on anything but a `create` is the key's number), and fingerprints the entry as received (so the same old entry carried again is skipped).
- `changes_for`: a `create_suggestion` row whose `card_changes` raises `LookupError`, or returns no changes, goes to the answer's `skipped` with the reason (`{"task_id": None, "why": "suggestion <eid>: <words>"}`), and its row is deleted with the others.
- `apply`'s answer `cards` row: `task_id` = `res["refs"].get(plan.summary["ref"])` or `plan.summary["task_id"]`.
- No `review_pass` call (Trap 2).

- [ ] **Step 1: Write the failing tests** in `tests/test_tray.py`: every name of design §5.7's second list, plus Review Focus 1 and 2 (`test_a_carried_old_pin_or_longterm_moves_beside_and_decides_nothing`, `test_a_staged_create_of_a_card_answered_since_is_skipped_with_its_reason`) and `test_begin_write_takes_the_lock_and_leaves_an_open_transaction_alone`. Two-connection tests use `db.connect(path)` twice on one `tmp_path` file and two `threading.Thread`s started together on a `threading.Barrier(2)`. `test_a_staged_card_create_is_the_routes_batch` compares the `changes` table rows with those of `POST /api/review/suggestions/{id}/create` on a twin database, as in Task 2. `test_decides_of` adds a digit key holding `pin_top` → None.
- [ ] **Step 2: Add `stage` tests** to `tests/test_review_decide.py`: `test_stage_writes_the_plan_and_does_not_commit` (another connection sees nothing until the caller commits) and `test_stage_from_chat_reads_the_stored_proposal_not_the_page`.
- [ ] **Step 3: Run** `.venv/bin/pytest -q tests/test_tray.py tests/test_review_decide.py` → FAIL.
- [ ] **Step 4: Add the three tables and the index to `db.SCHEMA`** (after `push_subscriptions`, `db.py:145`), as design §5.3 writes them; no `VERSIONED` entry (no data changes). `tests/test_stage4_store.py:78` and `tests/test_merge_byhand.py:39-50` compare fresh against migrated schemas and need no change.
- [ ] **Step 5: Implement `db.begin_write`, `tray.py`, `review_decide.stage`.** `tray.apply` steps 3 to 5 in one `try … except: conn.rollback(); raise`; `tray.Stale` raised after a rollback.
- [ ] **Step 6: Run** the new tests, then the suite → green.
- [ ] **Step 7: Commit** `factotum/db.py factotum/tray.py factotum/review_decide.py tests/test_tray.py tests/test_review_decide.py`.

### Task 5: the routes; `review_queue.undecided_count`

**Files:**
- Create: `tests/test_tray_api.py`
- Modify: `factotum/api.py` (new body models beside `ApplyBody`, `api.py:92`; routes after `finish_review`, `:745-749`; `review` `:526-534`; `review_count` `:536-540`), `factotum/review_queue.py`, `tests/test_review_fixes.py:77-80`

**Interfaces:**
- Consumes: Task 4's `tray` and `review_decide.stage`.
- Produces: design §5.4's six routes; `review_queue.undecided_count(conn, cfg, today) -> int` (reads `SELECT DISTINCT decides FROM tray WHERE decides IS NOT NULL` itself, or imports `tray` inside the function; never a top-level import, since `tray` imports `chat` and `batches`).

**Route details the design leaves to the code:**
- Bodies: `TrayOpsBody(ops: list[dict], device: str | None = None)`, `TrayCarryBody(carry_id: str, entries: list, device: str | None = None)`, `TrayApplyBody(keys: list[str] | None = None, seen_rev: int | None = None, source: str = "review", finish: bool = False, whole_tray: bool = True)` (`source` other than `review` / `ui` → 400, as `api.py:460-461` refuses `ripple`), `DecideBody(key: str, option: str | None = None, args: dict | None = None, note: str | None = None, from_chat: dict | None = None, device: str | None = None)` (`note` is laid into `args`).
- `POST /api/tray/ops` and `/api/tray/carry` use `origin = "page"` / `"carry"`; a `ValueError` from `tray.write` → rollback, 400 `{detail: "<key>: <why>"}`.
- `POST /api/tray/apply`: `with chat._CREATE_LOCK, commit_lock:` (`api.py:464`'s order); `tray.Stale` → 409 `{detail, changed, tray}`; `ValueError` → 400 `{detail}`; after the lock, `app.state.scheduler.wake()` when `res["created"]` (`api.py:474-477`).
- `POST /api/review/decide`: `commit_lock`; `review_decide.stage(conn, ecfg, …, review_queue.today(ecfg), origin="page", device=…)`; commit; answer `{tray: tray.read(conn), keys, removed, said}`. `Refused` → 400, `Gone` → 404, `Stale` → 409, each after `conn.rollback()` with `tray` beside `detail` (Trap 10). Catch `Refused` before `ValueError` (it is one).
- `GET /api/review` and `GET /api/review/count` union `exclude_ids(exclude)` with the task ids of `tray.decided(conn)` (`t:` keys); the count route answers `undecided_count` minus nothing else (its own query already leaves out decided items) with the extra `exclude` ids left out too. `GET /api/review/count` takes no lock.

- [ ] **Step 1: Write the failing tests** in `tests/test_tray_api.py`: every name of design §5.7's third list, plus `test_a_bad_row_refuses_the_apply_it_is_in_and_not_the_others` (Review Focus 3: a tray row `{"op": "create", "from_chat": {"message_id": 999, "index": 0}}` put through `tray.write`; `POST /api/tray/apply {}` → 400 naming it, `GET /api/tray` unchanged; `POST /api/tray/apply {"keys": [<the other key>]}` → 200). JSON-only checks post with `headers={"Content-Type": "text/plain"}` → 415, as `tests/test_phase6.py:259-262`.
- [ ] **Step 2: Add to `_WRITERS`** (`tests/test_review_fixes.py:77-80`): `("tray", "write")`, `("tray", "carry")`, `("tray", "apply")`, `("review_decide", "stage")`.
- [ ] **Step 3: Run** `.venv/bin/pytest -q tests/test_tray_api.py tests/test_review_fixes.py` → the new tests FAIL; the lock test passes (no route calls them yet).
- [ ] **Step 4: Implement** the routes and `undecided_count`.
- [ ] **Step 5: Run** the suite. `tests/test_stage8.py:26-42` (`/api/review/count`, with and without `exclude`, and `review_queue.count`) and `tests/test_polish.py:304` (F3, `exclude` parsed safely) must pass unchanged: with an empty tray `undecided_count` equals `count`.
- [ ] **Step 6: Commit** `factotum/api.py factotum/review_queue.py tests/test_tray_api.py tests/test_review_fixes.py`.

### Task 6: the page's tray on the server; Pin and Long-term beside on every tab; the Later-menu fix

**Files:**
- Modify: `factotum/web/app.js`, `factotum/web/app.css` (the "not saved yet" line and "· from <device>" only), `tests/test_search_edit_page.py:63`

**Interfaces:**
- Consumes: Task 5's routes.
- Produces (Stage 2 calls them): `tray` state, `trayFrom`, `trayDiff`, `syncTray`, `trayAnswer`, `trayFail`, `trayPoll`, `trayUnsent`, `trayApply`, `trayBoot`, `carryOldTray` (design §5.6); `api()` errors carry `e.body`.

**Every write site of the tray, at HEAD:**

| Site | Line | Change |
|---|---|---|
| boot read of `factotum.staged` and the old fix | `:63-64` | removed; `trayBoot` draws from `localStorage['factotum.tray']`; the old fix is `tray.carry`'s (Task 4) |
| `stageExtra` | `:529-538` (setItem `:536`) | `syncTray()`; still renders unless `quiet` |
| `stageDue` | `:540-543` | through `stageExtra` |
| `wireDue` `[data-due-clear]` | `:555` | `staged.delete(…); syncTray(); render();` |
| `stageReminder` | `:557-566` (set `:562`, setItem `:563`) | `syncTray()` |
| `wireReminders` `[data-rem-clear]` | `:571` | as `wireDue` |
| `persistStaged` | `:575`; callers `:588`, `:639`, `:2230`, `:3618`, `:3659`, `:3660` | body becomes `syncTray()` (the callers stay) |
| `chipLeft` | `:579-584` | mutates `staged`; its callers sync after it (unchanged) |
| `discardStaged` | `:585-589`; callers `:2945`, `:3725`, `:4138` | ends in `persistStaged()` as now |
| `stage` | `:590-594` (setItem `:592`) | `syncTray()` |
| `act` top / longterm | `:605-606` | `stageExtra(id + ':top', …)` / `stageExtra(id + ':lt', …)`; the wanted state is the opposite of `view`'s, and when it equals the server row (`byId(id)`) the key is deleted instead |
| `applyAll` | `:614-668` (removeItem `:639`, `editsOnly` `:646`, `/api/review/finish` `:651`) | `trayApply(keys, {finish: ui.tab === 'review', whole_tray: !keys, source: …})`; `res.finished` replaces `editsOnly`; no finish call; the "Say how it went" and toast logic stays |
| `stageChange` | `:890-894` | through `stageExtra` |
| `stageProposal` | `:2228-2232` (in-place `e.prop`, Trap 11) | unchanged; `persistStaged` syncs |
| `stageProposalAs` pin_top / longterm | `:2258` | under `<id>:top` / `<id>:lt` |
| `propKey` | `:2223` | `pin_top` and `longterm` leave the bare-key list; `stageKey` gives them `:top` / `:lt` |
| `stageKey` | `:861-870` | `pin_top` / `unpin` → `` `${id}:top` ``, `longterm` → `` `${id}:lt` `` (no suffix given) |
| `stageEdit` | `:3615-3621` (delete + persist `:3618`) | unchanged (persist syncs) |
| `edDue` | `:3653-3662` (`:3659-3660`) | unchanged (persist syncs) |
| `reorderTop` | `:4191-4197` (`:4196`, Trap 12) | `stageExtra(id + ':top', id, 'set', {top_rank: i + 1}, 'Top ' + (i + 1))` |
| `reviewAct` | `:4198-4229` | `:4203` unchanged (`act` now stages beside); `ui.laterOpen = ui.datePick = false` moves to just before the decision is staged (from `:4228`) |
| the Later date box | `:1966` | `min="${isoPlus(1)}"` |
| `renderTray` | `:2928-2947` | "not saved yet" inside the tray bar when `trayUnsent()`; "· from <device>" on a row whose `tray.meta` device is not `deviceLabel()` |
| `onReturn` | `:432-454` (`:435`) | a new build reloads only when `!trayUnsent()` and nothing is typed; `trayPoll()` on every return; the comments at `:455` and `:839` say the tray is on the server |
| `api()` | `:82-93` | `e.body = j` on an error |

**Every reader that tests `typeof k === 'number'`:** `:64` (removed), `:383` (`loadCount`'s `exclude`), `:646` (`editsOnly`, removed), `:1838` (`loadReview`'s `exclude`). `trayFrom` makes ASCII-digit keys numbers, so `:383` and `:1838` keep working; so do the bare-id readers `staged.get(t.id)` / `staged.has(i.task.id)` at `:98`, `:1640`, `:1718`, `:1933`, `:2988`, `:3122`, `:4199`.

**Readers that must learn the two new keys:** `view` (`:97-112`) lays `:top` (`pin_top` → `top_rank || 99`, `unpin` → `null`, `set` → its fields) and `:lt` (its fields, or `longterm: 1`) over the row; `chipsHtml`'s staged chip (`:1655`) still shows only the bare key, and `stagedCountHtml` (`:3552-3556`, via `keysFor` `:3541-3550`) counts the rest; `fab-on` (`:1480`) and the head line on Review (`:1940`, `staged.size`) are unchanged.

- [ ] **Step 1: Change the source pins first** in `tests/test_search_edit_page.py`: remove the `editsOnly` pin at `:63` and pin `res.finished` in its place; add `test_the_tray_is_the_servers`: `"setItem('factotum.staged',"` and `"removeItem('factotum.staged')"` are not in `JS`; `JS.count("getItem('factotum.staged')") == 1` and that one occurrence lies inside `function carryOldTray`; `"'/api/tray/ops'"` appears once, in `syncTray`; `"id + ':top'"` and `"id + ':lt'"` are in `act`'s body; `min="${isoPlus(1)}"` is in `JS`. Run → FAIL.
- [ ] **Step 2: Add the tray state and functions** (Interfaces), declared beside `staged` (Trap 14). `syncTray` queues on `tray.busy` and computes the diff when its turn comes (Review Focus 5). An entry with `local` is never sent. The cache is `{rev, entries, unsent, unsentAt}` in `localStorage['factotum.tray']`, read and written in `try`.
- [ ] **Step 3: Replace the write sites** of the table, one by one; `node --check factotum/web/app.js` after each group.
- [ ] **Step 4: Move Pin and Long-term** (`act`, `stageKey`, `propKey`, `stageProposalAs`, `reorderTop`, `view`), the Later-menu fix and the date box `min`.
- [ ] **Step 5: `trayBoot` / `carryOldTray`** in `load()`: draw the cache; send `unsent` under an hour old (older: toast that it was dropped); `GET /api/tray`; if `factotum.staged` exists, post it with a saved `carry_id` (`localStorage['factotum.carryId']`, made once with `crypto.randomUUID()` and a fallback); on success move it to `factotum.staged.backup` as `{at, entries}` and toast "N staged changes from this browser are in the tray on every device now"; remove a backup older than 30 days; on failure draw the old entries `local: true` and disable Apply with a line saying why.
- [ ] **Step 6: Run** `node --check factotum/web/app.js`, then `.venv/bin/pytest -q tests/test_search_edit_page.py`, then the suite → green.
- [ ] **Step 7: Render and look** (design §5.7 "Rendered", the Task 6 parts) at 1280 × 900 and 390 × 844 on the copy: the page loads with no console error; each Review decision moves to the next card at the click with no flicker when the answer comes; Pin to Top on a Review card leaves the card in front and the tray lists "pinned"; the Later menu is closed on the next card; the Later date box refuses today; two pages on one server share the tray (stage on one, `trayPoll` on the other by a `visibilitychange`); offline (`context.setOffline(true)`) a change shows "not saved yet" inside the tray bar and is sent once when back; Review Focus 5 with a delayed `/api/tray/ops`; the carry (seed `factotum.staged` with `[[412, {task_id: 412, op: "keep", fields: {}, label: "kept"}], ["e:<eid>", …], ["rel:3:waits_on:9", …]]` using ids that exist in the copy; reload: three carried; reload again: none); on the phone one thing at the bottom, hidden by `kb-open`; Today, Timeline, Search and Chat stage and apply as before. Never press Apply on the copy except for the Apply checks, and stop the server you started.
- [ ] **Step 8: Commit** `factotum/web/app.js factotum/web/app.css tests/test_search_edit_page.py`.

### Task 7: `decideOn`: the conversation's decisions, Create and Dismiss on a suggestion card

**Files:**
- Modify: `factotum/web/app.js`, `factotum/web/app.css` (the staged card's Take back), `tests/test_search_edit_page.py:55`

**Interfaces:**
- Consumes: `POST /api/review/decide` (Task 5), Task 6's `tray`, `trayAnswer`, `trayFail`, `trayApply`.
- Produces: `decideOn(key, option, args, opts)` and `sgStagedHtml(card, entry)` (Stage 2 calls both).

**Where it is now:**
- `stagePlace` (`:2601-2613`) stages every ticked proposal through `stageProposal`. For `pl.origin === 'review'` and a proposal with `p.task_id === pl.task_id` whose op is `done`, `drop`, `pin_top`, `longterm`, or `set` holding `snoozed_until`, call `decideOn('t:' + pl.task_id, null, null, {from_chat: {message_id: mid, index: i}})` instead and count it in `n`; everything else stays on `stageProposal`. `placeOpen` (`:2537`) keeps finding the staged entry because the rule's keys are the page's (`"<id>"`, `"<id>:top"`, `"<id>:lt"`, `"<id>:c<mid>-<i>"`).
- Dismiss on a card: `[data-sugg-dismiss]` (`:4016`) → `decideOn('s:' + id, 'dismiss')`.
- Create on a card: `[data-sugg-create]` (`:4017-4033`, the direct `POST /api/review/suggestions/${id}/create` at `:4023`) → `decideOn('s:' + id, 'create', edited ? { draft: sgBody(sgDraft(card)).create } : {})`. The old route stays for old pages. After an Apply (`trayApply`'s answer), each `res.cards` row gives the toast `Created Task-N` (or `Reopened Task-N`, or `Task-N was already created`) with Open, `cardMade.set(res.batch_id, task_id)` (`cardMade`, `:3283`; read by `undoBatch`, `:922`), and `delete draft.sg[id]`.
- The card list on Review (`:1936`) already hides a card whose `e:<id>` is staged; `sgStagedHtml` draws such a card (its name and "new task: …" or "dismissed") with Take back (`decideOn('s:' + id, 'undecide')`) where the card would have been, after the undecided ones.
- `reviewShown` (`:364-368`, Trap 17) filters staged cards.

- [ ] **Step 1: Change the source pins first** in `tests/test_search_edit_page.py`: `:55`'s `"JSON.stringify(edited ? sgBody(sgDraft(card)).create : {})"` becomes `"decideOn('s:' + id, 'create', edited ? { draft: sgBody(sgDraft(card)).create } : {})"`; add `JS.count("'/api/review/decide'") == 1` and that one inside `function decideOn`; `"decideOn('t:' + pl.task_id"` inside `stagePlace`; `"decideOn('s:' + id, 'dismiss')"` in `JS`. Run → FAIL.
- [ ] **Step 2: Implement `decideOn`**: put a provisional `local: true` entry (the card moves at the click), queue on `tray.busy` behind any earlier staged change, `POST /api/review/decide` with `device: deviceLabel()`, the answer's `tray` through `trayAnswer`, a 4xx through `trayFail` using `e.body.tray`, `said` as a toast when not empty.
- [ ] **Step 3: Wire** `stagePlace`, Dismiss, Create, `sgStagedHtml`, `reviewShown`, the toasts after Apply.
- [ ] **Step 4: Run** `node --check factotum/web/app.js`, `.venv/bin/pytest -q tests/test_search_edit_page.py`, then the suite → green.
- [ ] **Step 5: Render and look** (design §5.7 "Rendered", the Task 7 parts): a suggestion card's Create leaves at the click; the tray says "new task: …"; Apply toasts "Created Task-N" with Open; History's Undo brings the card back; Create and Apply again says "Reopened"; a card's Dismiss and Take back; a conversation reply proposing `done` on a Review card stages the verdicts too (stub `POST /api/chat/turn` with `page.route` returning a stored message; never run a real chat turn); the number beside Review drops by one on a staged Create. Both sizes; stop your server.
- [ ] **Step 6: Commit** `factotum/web/app.js factotum/web/app.css tests/test_search_edit_page.py`.

### Task 8: SPEC and CLAUDE.md

**Files:**
- Modify: `SPEC.md` (§4 data model: `tray`, `tray_rev`, `tray_carried`; §5 lifecycle: "decided" is a tray row's `decides`, Review's day; §9 UI: the tray on the server and its carry, Pin and Long-term beside, Create staged on a card, the conversation's decisions through the rule), `CLAUDE.md`

- [ ] **Step 1: SPEC** sections as above, each with the date 2026-11-19; one paragraph each, no tables copied from the design.
- [ ] **Step 2: CLAUDE.md**: the phase-status paragraph gains a "Spoken Review stage 1 (2026-11-19)" sentence; "Where things live" gains rows for the queue and cards (`review_queue.items`, `today`, `undecided_count`; `factotum/review_cards.py`), the rule (`factotum/review_decide.py`: `plan`, `stage`, `from_proposal`) and the tray (`factotum/tray.py`, the routes, `web/app.js` `syncTray`, `trayAnswer`, `trayApply`, `decideOn`, `carryOldTray`); "Conventions that break silently" gains the ten new ones of design §11 that are true after Stage 1 (leave out the pass, the offered stamp, `spoken`, recommendations and the connector's source, which are Stage 2-3), each dated 2026-11-19, plus this plan's `decides_of` op rule and Traps 3, 8, 10 and 15.
- [ ] **Step 3: Run** the suite → green.
- [ ] **Step 4: Commit** `SPEC.md CLAUDE.md`.

---

## What each later builder needs to know

- Stage 2 rewrites `reviewAct` and the card buttons on `decideOn`; until then they stage in the page (Trap 15).
- `tray.apply` has no `review_pass.after_apply` call yet (Trap 2).
- Deploy order (the lead's, not a builder's): Task 5 before or with Task 6, and a backup-API copy before Task 6.
