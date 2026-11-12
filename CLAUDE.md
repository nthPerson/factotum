# CLAUDE.md

Guidance for Claude Code working in this repository. Read `SPEC.md` for the design and
`REPO_RETIREMENT_AND_NEW_REPO_INITIALIZATION.md` for how this repo takes over from
`nthPerson/plaud-sync`. This file is the short version plus the things that bite.

## The three facts that matter

1. **SQLite is the source of truth.** Notion, Canvas, Mailbird and the calendar are inputs or
   write-only sinks. Nothing is ever read back from Notion after the one-time import.
2. **Claude decides, Python writes.** `factotum/batches.py::apply` is the only write path for
   task state. Claude runs return JSON proposals; the UI, the command grammar, Review, Chat and
   the automations all turn them into a batch. Every batch is undoable.
3. **Person-specific facts live in config, never in code.** A Notion id, a course code, an
   email address or a project weight in a `.py` file is a bug; it belongs in the user's
   `config/factotum.yaml` (`config/factotum.example.yaml` documents the structure with
   placeholders) or `.env` (secrets). The tool is meant to be handed to other people.

## Phase status

All six phases (SPEC.md §14) are built as of 2026-11-02: store, scoring, lifecycle, Notion import,
batches, command grammar, API, the Today / Timeline / Projects / Review / Chat / Log / Settings
tabs; the automations (`factotum/scheduler.py`: ntfy reminders with Snooze/Done/Open actions at
`/r/<token>`, the daily review nudge, the expire pass, the morning brief over SMTP, the calendar,
Canvas, mail and Notion-mirror sweeps); the Plaud port (`factotum/plaud_trigger.py` IMAP IDLE →
`plaud` CLI → one headless Claude run via `factotum/claude/runner.py` and `prompts/sync-file.md` →
`factotum/sync_file.py`); the mail sweep (`factotum/mail_sweep.py` over the read-only
`factotum/mailbird.py`, findings in `evidence`); the Canvas sweep (`factotum/canvas.py`); the morning
planner (`factotum/planner.py`); Chat (`factotum mcp` in `factotum/mcp_server.py`, `factotum/chat.py`,
`prompts/chat-*.md`, the `/api/chat/*` routes); and phase 6: the Notion tasks mirror
(`factotum/notion_mirror.py`), the skills `skills/factotum-planner` and `skills/factotum-ops` on the
same MCP server with `stage_changes` → `proposals` → the tray, `POST /mcp` behind a bearer token,
Slack and Discord read tools (`factotum/slack.py`, `factotum/discord.py`), evidence verdicts as
batch changes, planner suggestions on Review, the phone layout. Wave 7 (2026-11-02, after the
restart): the Slack mention sweep (`factotum/slack_sweep.py`: @-mentions → evidence, "Mentioned in
Slack" on Review, suggestion cards), Discord forums and threads (`discord_threads`), the claude.ai
connector (`factotum/oauth.py`: Factotum as the OAuth server behind `/mcp`, consent behind Access
with the JWT verified, arming from Settings → Connections), `mirror --task`. Wave 8 (2026-11-04):
Claude Client ID Metadata Documents; writing from a connector or skill (`create`, `reopen`,
`watch_sent` as batch ops; `apply_changes` beside `stage_changes` when `server.mcp_writes` is
`apply`; the mail sweep closes a watched task when the mail is sent). Wave 9 (2026-11-04): the Today
summary (`factotum/summary.py`: the planner's verdict in `state.summary`, staleness decided by
Python, a light rewrite run, Refresh = sweeps + planner through the scheduler), removing a due date
(`due: null`, `nodue`), foldable sections, the Top rail's tiles and jump-to-task. Wave 10
(2026-11-05): tasks suggested from mail (`mail_sweep.py` keeps `mail_new` evidence, `factotum/mail_triage.py`
and `prompts/mail-triage.md` turn it into Review cards), growing prompt boxes, Chat attachments and
Delete thread, the Log's Timeline / Changes / Runs / All views. Stage 1 of the linked-tasks design (2026-11-10,
`docs/superpowers/specs/2026-11-10-linked-tasks-design.md`): the installable app, task and tab
addresses with Back, the phone task page, the short selection bar, Sign in again, fresh on return,
self-update by build stamp; the command leftover fault and the brief retry fault. Stage 3 (2026-11-10): a
neutral start: without `config/factotum.yaml` the app runs on `config/factotum.starter.yaml` and says so (one
stderr line, a banner on every tab); absent means off for every paid or mailing switch, with one table of
code defaults (`config.DEFAULTS`, read through `C.value`); `factotum effective` prints each switch's value and
origin; accounts are seeded once (`state.accounts_seeded`); Settings shows effective values and saves only the
control that changed; the example is a reference with placeholders, and the suite loads
`tests/fixtures/factotum.fixture.yaml` instead. Stage 2 (2026-11-10): notifications from the app
(`factotum/webpush.py`, `factotum/app_push.py`, `web/sw.js`, Settings → Notifications, `notify.provider`:
ntfy, the app or both). Stage 4a (2026-11-10): links between
tasks (`part_of`, `waits_on`, `same_as` in the `relations` table; `link` / `unlink` / `merge` / `add_note` as
batch ops; undo that skips what changed since; suggestions as `link_suggestion` evidence; one derived-facts
step, `relations.annotate`, in front of scoring, lifecycle, the brief, reminders and the summary; payloads
and the agent's tools; `PRAGMA user_version` migrations with backups; `scoring.links` in config). Stage 4b
(2026-11-11): links on the page (badges, the All / Linked / Ready now filter and the Linked view, Today's
link-aware order, the opened task with History, the task page and its trail, Review's link reasons and
suggestions, the Log in words, "Free now" after a reminder's Done). Stage 5 (2026-11-11): ripples (`factotum/ripple.py`,
`prompts/ripple.md`: a closed, changed or merged task queues its linked tasks, one light run with no tools
rewrites their text as an undoable `ripple` batch and stages everything else on Review), the outcome
question on close, "Task-N is free now", the Review block and Settings controls. Stage 6 (2026-11-11): in place and by hand (the conversation on a Review card,
under the Today box and in an opened task; New task on Enter with its preview and the phone sheet; the editor; linking
by hand; Find any task). Retiring the old Notion
pages and plaud-sync is the user's checklist (SETUP §15). Update this paragraph when something lands.

## Where things live

| Touching… | Read first | Code |
|---|---|---|
| tiers, fading, last call, review queue, import buckets | SPEC §5, §12 | `factotum/lifecycle.py` |
| score / priority | SPEC §5 table | `factotum/scoring.py` (parameters under `scoring:` in config) |
| what a change may set, undo | SPEC §2, §4 | `factotum/batches.py` (`SETTABLE`, `OPS`) |
| `done 389 392` and friends | SPEC §6 "Commands" | `factotum/commands.py`, `factotum/dates.py` |
| Settings-tab keys, config precedence | SPEC §9 | `factotum/config.py` (`SETTINGS_KEYS`) |
| schema | SPEC §4 | `factotum/db.py` (`SCHEMA`, `TASK_FIELDS`) |
| API routes, static UI | SPEC §9 | `factotum/api.py`, `factotum/web/` |
| the one-time import and the interim `--new-only` sync | SPEC §12 | `factotum/importers/notion_tasks.py` |
| Notion property names, sink toggles | SPEC §7 | `config/factotum.example.yaml` `sinks:` |
| the scheduler tick, daily jobs, run logging | SPEC §10 | `factotum/scheduler.py` (`Scheduler.tick`), `db.log_run` |
| reminders firing, repeats, action tokens | SPEC §10 | `factotum/reminders.py`, `factotum/notify.py` (ntfy) |
| the morning brief, SMTP | SPEC §7 | `factotum/brief.py` (`build`, `send`, `smtp_settings`) |
| Google Calendar auth, events, sweep | SPEC §6, SETUP §6 | `factotum/gcal.py` |
| headless Claude runs: command, budget, envelope, auth failure | SPEC §8 | `factotum/claude/runner.py` (`run`, `extract_json`) |
| the Plaud trigger mail, sender authentication, seen uids | SPEC §6 | `factotum/plaud_trigger.py` (`authenticated`, `process_mailbox`) |
| locating a recording, the two files | SPEC §6 | `factotum/plaud.py` (`locate`, `fetch_files`) |
| the sync-file prompt, applying its JSON, retry, alert | SPEC §8.1 | `factotum/sync_file.py` (`build_prompt`, `apply_result`, `run`), `prompts/sync-file.md` |
| project descriptions, tie-breakers, note types | SPEC §8.1 | `config/factotum.example.yaml` `taxonomy:` |
| the Notion note page, markdown → blocks | SPEC §7 | `factotum/notion_notes.py` (`upsert`, `md_to_blocks`) |
| Mailbird MCP (read-only), the mail sweep, evidence, Review flags | SPEC §2, §6 | `factotum/mailbird.py` (`READ_TOOLS`), `factotum/mail_sweep.py` (`sweep`, `review_flags`) |
| Canvas items → tasks, closes, announcements | SPEC §6 | `factotum/canvas.py` (`normalize`, `sweep`) |
| the morning planner: bundle, prompt, what gets applied | SPEC §8.2 | `factotum/planner.py` (`bundle`, `apply`, `run`), `prompts/planner-morning.md` |
| the MCP server a chat turn talks to, its tools, result caps, draft policy | SPEC §8.3 | `factotum/mcp_server.py` (`TOOLS`, `dispatch`, `known_recipients`) |
| chat turns, proposal validation, session resume, canned prompts | SPEC §8.3 | `factotum/chat.py` (`build_prompt`, `turn`, `_clean_proposals`), `prompts/chat-*.md` |
| streaming a headless run, per-run MCP config, isolation flags | SPEC §8 | `factotum/claude/runner.py` (`command`, `run`, `_run_stream`) |
| the Notion tasks mirror: schema-typed properties, which rows, `factotum mirror` | SPEC §7 | `factotum/notion_mirror.py` (`properties`, `sweep`), `tasks.mirrored_at` |
| a skill staging a batch, the tray banner, the HTTP MCP route and its allowlist | SPEC §15 | `factotum/mcp_server.py` (`stage_changes`, `Context.allowed`), `api.py` (`/api/proposals`, `/mcp`), `skills/` |
| Slack / Discord read tools, workspaces and servers, tokens | SPEC §6 | `factotum/slack.py`, `factotum/discord.py`, `mcp_server.py` (`slack_*`, `discord_*`) |
| verdicts on evidence (dismiss, accept, reviewed), planner suggestions on Review | SPEC §9 | `factotum/batches.py` (`judge_evidence`), `api.py` (`/api/review`, `/api/review/suggestions`, `SUGGESTION_KINDS`) |
| what a connector or skill may write: validation, staging vs direct apply, create, the sent-mail watch | SPEC §15 "Writing from a connector" | `factotum/mcp_server.py` (`_validate_changes`, `stage_changes`, `apply_changes`), `batches.py` (`_create`, `watch_fields`), `mail_sweep.py` (`watched`), `mcp_server.known_recipients` |
| the Slack mention sweep, "Mentioned in Slack", a mention as a suggestion card | SPEC §6 "Slack" | `factotum/slack_sweep.py` (`sweep`), `lifecycle.REVIEW_ORDER`, `mail_sweep.review_flags`, `planner.NON_PROVING` |
| tasks suggested from mail: what the sweep keeps, the triage run, the card, Create | SPEC §6 "Tasks suggested from mail", §8.5 | `factotum/mail_sweep.py` (`candidate`, `clean_body`, `_person`), `factotum/mail_triage.py` (`pending`, `apply`, `run`), `prompts/mail-triage.md`, `scheduler.py` (`_mail_triage`), `api.py` (`_suggestion_mail`, `create_suggestion`, `open_suggestion`), `web/app.js` (`suggestionCard`) |
| prompt boxes, Chat attachments and the task picker, the Log's views | SPEC §9 "Wave 10" | `web/app.js` (`draft`, `typing`, `grow`, `chatAttach`, `pickMatches`, `renderLog`, `timelineHtml`, `batchLine`, `batchDigest`) |
| the Today summary: what it is about, when it is stale, the light rewrite, Refresh | SPEC §8.4 | `factotum/summary.py` (`store`, `stale`, `run`, `refresh_state`), `scheduler.py` (`_refresh`, `_auto_summary`), `api.py` (`/api/summary`), `prompts/today-summary.md`, `web/app.js` (`summaryHtml`, `pollSummary`) |
| folded sections, the Top rail's tiles, jump to a task's row | SPEC §9 "Wave 9" | `web/app.js` (`folds`, `group`, `todayGroups`, `timelineGroups`, `renderRail`, `jumpTo`) |
| links between tasks, merge, suggestions, migrations | SPEC §2, §4, §5, §15; design `docs/superpowers/specs/2026-11-10-linked-tasks-design.md` | `factotum/relations.py`, `batches.py` (`_link_op`, `_merge`), `db.py` (`VERSIONED`, `migrate`) |
| derived neighbour facts, the scoring wrapper | SPEC §5 | `relations.annotate`, `relations.scored`, `relations.score_one` |
| a task's History, relations, nearby | SPEC §9 | `api.get_one` |
| link badges, the link filter and the Linked view, Today's order | design §12.1, §12.2, §7.4 | `web/app.js` (`badgesFor`, `linkChipsHtml`, `overlayRel`, `linkView`, `readyRow`, `linkedGroups`, `arrangeToday`) |
| staging links, notes and outcomes; Undo that skips | design §11.2, §6 | `web/app.js` (`stageKey`, `stageChange`, `changeWords`, `textExpect`, `suggestionStaged`, `undoBatch`, `undoSaid`) |
| the opened task: linked tasks, provenance, History, the task page and its trail | design §9, §12.3 | `web/app.js` (`detailCache`, `loadDetail`, `ensureDetails`, `swapDetail`, `historyHtml`, `historyWords`, `taskPageHtml`, `openPage`), `api.get_one` |
| "Free now" after a reminder's Done | design §14 | `api._page`, `relations.freed_by` |
| ripples: what queues, the run, what applies, the guard, undo following the cause | design §8, SPEC §8.6 | `factotum/ripple.py` (`enqueue`, `next_group`, `bundle`, `parse`, `guard`, `clean_proposal`, `apply`, `run`, `since`, `status`), `batches.py` (`apply`, `undo`), `scheduler.py` (`_ripples`), `prompts/ripple.md` |
| the outcome question, Free now | design §8.5, §14 | `api._page`, `reminders.save_outcome`, `reminders.free_now`, `web/app.js` (`askOutcome`, `saveOutcome`, `watchRipple`, `sinceHtml`) |
| the claude.ai connector: metadata, registration, consent gate, arming, tokens, `/mcp` auth | SPEC §15, SETUP §16 | `factotum/oauth.py` (`consent_identity`, `verify_access_jwt`, `armed`, `token`, `check_access`), `api.py` (`/oauth/*`, `/api/connectors`) |
| the add line, adding at once, search | design §12.7, §12.9, SPEC §6 "The add line" | `factotum/commands.py` (`ADD_RE`, `parse_add`, `link_refusal`), `factotum/by_hand.py` (`preview`, `add`, `search`), `api.py` (`/api/add*`, `/api/search`) |
| the conversation in place | design §12.6, SPEC §8.3 | `chat.py` (`place_thread`, `PLACE_TEXT`, `resolve_staged`, `mark_staged`), `api.py` (`/api/chat/turn`, `/api/chat/place`), `web/app.js` (`placeOf`, `loadPlace`, `sendTurn`, `placeHtml`, `stagePlace`) |
| New task, the sheet, the editor, linking by hand, Find | design §12.7–§12.9 | `web/app.js` (`addTask`, `previewAdd`, `addPreviewHtml`, `sheetHtml`, `editorHtml`, `editKey`, `keysFor`, `pickerHtml`, `searchTasks`, `findHtml`) |
| the installable app: manifest, icons, the page head | design §13, SPEC §9 | `factotum/web/manifest.webmanifest`, `index.html`, `tools/make_icons.py` |
| addresses, Back, the phone task page, the short bar, signed out, return and update | SPEC §9 | `web/app.js` (`parseAddress`, `setAddress`, `go`, `applyAddress`, `openTask`, `closeTask`, `taskPageHtml`, `signedOut`, `typingNow`, `onReturn`), `api.py` (`PAGE_TABS`, `STAMPED`, `build_stamp`, `BuildStamp`, `_index_html`) |
| the starter, code defaults, absent means off, `effective` | SPEC §9 "Configuration", SETUP §18 | `factotum/config.py` (`STARTER_CONFIG`, `DEFAULTS`, `PAID_SWITCHES`, `value`, `explain`), `config/factotum.starter.yaml`, `__main__.py` (`_effective`), `web/app.js` (`starterHtml`, `settingsDirty`), `tests/test_stage3.py` (`_code_defaults`) |
| notifications from the app: the key pair, devices, payload, sending, the worker, Settings → Notifications | design §14, SPEC §10 | `factotum/webpush.py` (`encrypt`, `vapid_header`, `send`), `factotum/app_push.py` (`keys`, `subscribe`, `payload`, `send_all`, `send_test`), `notify.py` (`providers`, `ready`), `reminders.py` (`notice`), `api.py` (`/api/push*`, `/sw.js`), `web/sw.js` (`shape`, `handlePush`, `handleClick`, `openApp`), `web/app.js` (`pushView`, `pushOn`, `followPath`, `reviewPushOn`) |

## Commands

```bash
.venv/bin/pytest                                   # offline, must stay green
.venv/bin/python -m factotum init-db
.venv/bin/python -m factotum import-notion --dry-run          # bucket report, writes nothing
.venv/bin/python -m factotum import-notion --apply            # the one-time migration
.venv/bin/python -m factotum import-notion --new-only --apply # interim: pull rows plaud-sync created since
.venv/bin/python -m factotum score | review | expire [--apply]
.venv/bin/python -m factotum tick                  # one scheduler tick now; `brief [--send]`, `calendar-check`, `google-auth`
.venv/bin/python -m factotum sync-file <plaud_id> --dry-run   # a real Claude run (~$0.15) that prints the JSON and writes nothing
.venv/bin/python -m factotum claude-check | plaud-check       # the headless login; the Plaud CLI and the trigger mailbox
.venv/bin/python -m factotum mail-check | canvas-check        # Mailbird's accounts vs the monitored ones; Canvas items vs rows
.venv/bin/python -m factotum sweep mail|canvas|calendar|slack # one sweep now
.venv/bin/python -m factotum mirror [--all|--dry-run|--task ID…]  # the Notion tasks mirror: rows edited since the last pass, every row, or named rows
.venv/bin/python -m factotum connectors [--arm|--revoke ID]   # the OAuth connectors behind /mcp (claude.ai, Claude Desktop)
.venv/bin/python -m factotum slack-check | discord-check      # tokens, workspaces / servers, what each can read
.venv/bin/python -m factotum planner --dry-run [--send]       # the morning planner (~$0.30): JSON only, or applied and mailed
.venv/bin/python -m factotum chat-check                       # the MCP server over a pipe, Mailbird, one haiku run through it
.venv/bin/python -m factotum chat "text" --attach 388         # one chat turn from the terminal (~$0.20), stored like a UI turn
.venv/bin/python -m factotum mcp                              # the stdio MCP server itself (the runner starts it; rarely by hand)
.venv/bin/python -m factotum serve --port 8751 --no-scheduler   # a second instance for UI checks; never two schedulers on one DB
.venv/bin/python -m factotum serve                 # http://127.0.0.1:8750
.venv/bin/python -m factotum ripple <task_id> [--dry-run]   # one ripple now from a task (a real run, ~$0.03 measured 2026-11-11); --dry-run writes nothing
.venv/bin/python -m factotum effective [KEY…] [--as-before]   # each switch's value and origin, read-only; diff the two before a deploy (SETUP §18)
# every sweep and the planner at once: Refresh on the Today tab (the scheduler runs it; a `refresh` row in the Log)
sudo systemctl restart factotum                    # after code changes; see the restart rule below
journalctl -u factotum -n 20
```
The service runs `.venv/bin/python`, never system Python (`deploy/factotum.service`).

## Conventions that break silently

- **Settings-tab keys are a whitelist** (`config.SETTINGS_KEYS`). A new toggle needs the key
  there, its code default in `config.DEFAULTS`, a control in `web/app.js` `renderSettings`, and a
  default in the example YAML.
- **Absent means off for anything that costs money or sends mail** (2026-11-10): a paid Claude run, a
  mail, a write to an outside service happens only when its switch is explicitly on. The default lives
  in `config.DEFAULTS` as False and the code reads it with `C.value(cfg, key)`, never
  `C.get(cfg, key, True)`; a new Settings key needs its `DEFAULTS` entry too (with `SETTINGS_KEYS`, the
  control and the example). New runs (ripples, filing) follow this from the start.
  `tests/test_stage3.py::_code_defaults` fails when a literal default in the code disagrees with the table.
  `DEFAULTS` holds the Settings keys only; a file-only key (`scoring.links.*`, 2026-11-11) keeps its literal
  default in the code, and the example, `tests/fixtures/factotum.fixture.yaml` and the starter carry the same value.
- **Settable task fields are a whitelist** (`batches.SETTABLE`). `score`, `priority`, `status`
  and `mentions` are derived or owned by automations; a change that names them is rejected
  with a reason, not applied.
- **Notion property names come from config** (`sinks.notion_tasks_mirror.properties`). A renamed
  Notion column is a YAML edit. The importer and the mirror both read that map.
- **Task ids continue Notion's numbering.** Never renumber; `Task-<n>` is what the user types
  and says.
- **`mentions` is append-only evidence.** Bump it through `db.add_mention`, which also moves
  `last_mention_at`; that timestamp is the fade clock.
- **Filters in the Notion REST import**: `fetch_all` tries the 2025-09 data-source endpoint,
  then the older database endpoint. Keep both until the older one is gone.
- **Daily jobs are "at or after HH:MM, once per day"**, keyed by `state.last_run.<job>` in the
  settings table. A fresh deploy after the brief time sends the brief immediately; seed the key
  for today if that is not wanted. Never run two schedulers against one database.
- **`db.log_run` and `set_setting` do not commit; the caller does.** A scheduler branch that logs
  and returns without `conn.commit()` leaves the thread's transaction open: the row is invisible to
  the API's connection and, worse, the thread holds SQLite's write lock, so every UI Apply fails
  with "database is locked" after the 5 s busy timeout (bit on 2026-11-01).
- **`set_setting` JSON-encodes what it is given.** Pass the list or dict itself and read it back
  through `get_settings`; passing `json.dumps(...)` stores a string of JSON and the reader gets a
  string (bit the Canvas announcements on 2026-11-02).
- **Headless runs pass `--setting-sources "" --disable-slash-commands`** (2026-11-02): without them
  the user's own SessionStart hooks, plugins and skills load into every `claude -p` run (~5 KB of hook
  output and the user's permission mode showed up in a probe). The runner adds both to every command.
- **A chat run whose MCP server fails to start still "succeeds"** (2026-11-02): `system/init` reports
  `{"name": "factotum", "status": "failed"}` and the model answers from thin air (haiku invented task
  ids). The chat prompt says to report a failed tool; the UI shows the init warning; `chat-check` is
  the test. The server is `python -m factotum mcp` with `PYTHONPATH` set — a missing subcommand or a
  stdout print before the protocol starts breaks it silently.
- **`factotum mcp` must never write to stdout except JSON-RPC lines**; logs go to stderr. Its DB
  connection is `query_only`, so a tool that tries to write raises instead of bypassing batches.
- **Every state-changing `/api` call must carry `Content-Type: application/json`** (2026-11-02): a
  middleware answers 415 otherwise, so a body-less POST (undo, apply a proposal) is never a browser
  "simple request" that rides on the Access cookie. `fetch` with no body still needs the header; the
  UI's `api()` helper sets it, tests pass `json={}`.
- **`mcp_server.Context` sets `PRAGMA query_only` on the connection it is given** (2026-11-02): never
  hand it the API's shared connection; the `/mcp` route opens one per request, and `stage_changes`
  writes through `ctx.writer`, a separate connection.
- **Notion's Task ID is Notion's counter** (2026-11-02): the mirror cannot set a `unique_id`; it only
  matches Factotum's id because creates are pushed in id order and nothing else creates rows there.
- **A reminder push has no `Click` header** (2026-11-02): with one, the ntfy app on the phone opened
  the browser before Snooze / Done could be tapped. `notify.ntfy.tap_opens_app` restores it.
- **A `plaud_note` link is not always a Notion page id** (2026-11-02): the import stored the note
  page's id under that kind, but sync-file stores the Plaud recording id (`of_<hex>`) there. The
  mirror put one in the Notes relation and Notion refused the whole row (404 "Could not find page"
  on Task-395..397). `notion_mirror.note_ids` only takes a bare 32-hex id; `mirror --task` re-pushes.
- **`/oauth/authorize` must never be in an Access bypass policy** (2026-11-02): the consent page's
  only login is Access. It verifies the `Cf-Access-Jwt-Assertion` JWT (needs `server.oauth.access_team`
  and `access_aud`) and never a bare identity header; offline tests monkeypatch `oauth._fetch_certs`.
- **A `slack_mention` never proves a planner proposal** (2026-11-02): anyone in a workspace can
  write "Task-42 is done". `planner.NON_PROVING` stages such a proposal for Review; and its link is
  `slack_message`, never `mail_thread` (the mail sweep polls those through Mailbird).
- **`create` is a batch op and its undo drops the row** (2026-11-04): never a DELETE — task ids are
  forever and the mirror may already have a Notion page. Imports, sync-file, Canvas and the Review
  suggestion cards still create outside a batch (upsert by `source_key`); connector / skill creates
  ride in one, and since 2026-11-10 so does Chat's Create (`chat.create_from_proposal`, source `Chat`);
  a batch create's Source follows the batch source (`batches.created_source`).
- **`apply_changes` exists only when `server.mcp_writes` is `apply`** (2026-11-04): hidden from
  `tools/list` and refused on call otherwise, on both the HTTP route and the stdio skill server
  (which re-reads the setting on every call). A chat turn never has it (no writer). Whatever the
  mode, the write is `batches.apply`.
- **Nobody taps Apply on a direct write, so the validator is the check** (review 2026-11-04):
  `_validate_changes` rejects a value the cleaner would drop (a bad due, an unknown effort or
  project, a name over 80) instead of losing it; `task_id` must be `type(...) is int` (True is
  Task-1); direct changes are capped per hour; and an address a direct batch put on a task is not
  in `known_recipients`. A new settable field needs a line in that validator, not only in `SETTABLE`.
- **The watch columns are not `SETTABLE`** (2026-11-04): `close_on_sent_since` / `close_on_sent_to`
  are written by `watch_sent` / `unwatch_sent` only, and the watched address never goes into
  `counterpart`. The sweep ends the watch when it fires, so a reopened task is not closed again.
- **The mail sweep keeps one watermark per folder** (2026-11-04): `state.mail_sweep.last_at.<address>.inbox|sent`
  (the bare key is the old shared one, still read as a starting point); with one shared mark a sent
  message that reached Mailbird after a newer Inbox conversation was swept was skipped for good.
- **A Settings-tab override hides a YAML edit** (2026-11-02): every `SETTINGS_KEYS` control auto-saves,
  so once a box has been seen unticked the database holds `false` and `C.effective` prefers it to the
  file. `server.oauth.enabled: true` in the YAML did nothing until the toggle was ticked in Settings.
  Change such keys in Settings; the YAML is their default for a fresh database. Since 2026-11-10 only
  the control that changed is saved (`settingsDirty`); the overrides saved before that stay until removed
  from the `settings` table, and `factotum effective` shows them with the origin `settings`.
- **The API's connection is opened with `connect(path, shared=True)`** (2026-11-04): sync routes run
  on a thread pool over one connection, and Python's sqlite3 hands two threads running the same SQL
  the same cached statement, so a read came back short or raised (a 500 on about one request in
  twenty when two arrived together; worse, `get_settings` silently returning half the rows).
  `shared=True` is `cached_statements=0`. Any new connection used from more than one thread needs it.
- **Mailbird writes people as `{email, displayName}`** (2026-11-05), never `{address, name}`. The
  mail sweep read the second shape for a week and saw no sender on any real message: no waiting
  flip, no close on a sent mail, no sent-mail watch and no deny rule had ever fired, and nothing
  failed (the live `evidence` table simply had none of those kinds). `mail_sweep._person` takes both;
  `tests/test_phase4.py::_msg` builds the real one. Check a new integration's fake against one live
  response (keys only) before trusting a green suite.
- **A sent mail closes a task only as a reply in the thread the task follows** (2026-11-05):
  `mail_sweep.REPLY_RE` (reply / respond / answer / get back) AND `(task, conversation)` in the links
  that existed before the pass. Everything else to a counterpart is `sent_to_counterpart` evidence
  with the text, for the planner. Do not widen the verb list or link threads by address alone: that
  is what would have closed three tasks on one unrelated mail. A `watch_sent` is the explicit version.
- **A waiting task flips only on a NEW message from the address** (2026-11-05): `_waiting_match` gets
  the messages after the watermark that the owner did not write. A display-name match is whole words,
  and only a hint (no link, no `matched`).
- **The mail sweep commits before every Mailbird call** (`mb()` inside `sweep`): never call
  `client.call` there directly, or the write lock is held across the network again.
- **When the candidate cap leaves mail behind, the Inbox watermark stays** (2026-11-05): `candidate`
  returns False only for that; a new early `return` in it must return True, or the watermark freezes.
- **Mailbird: an attachment's name is `fileName`, a conversation row's `lastMessageId` is not always
  its last message** (checked live 2026-11-05). Fetch a message by its own `id`.
- **A mail suggestion is a card, never a task** (2026-11-05): `mail_triage.apply` only inserts
  `proposal` evidence and judges `mail_new` rows. The model supplies no address (`counterpart` is
  Mailbird's sender) and can name only evidence from the batch it was shown. The planner must keep
  excluding `mail_new` (two readers would judge the same mail twice).
- **Text written by strangers goes through bounded patterns** (2026-11-05): `mail_sweep.clean_body`
  caps what it reads and bounds every quantifier; an exception there is a note, never a failed sweep.
- **What is typed lives in `draft`, not in the DOM** (2026-11-05): `render()` rebuilds the panel
  (every second while a chat turn runs) and restores focus and caret for `#cmd`, `#rv-ask` and
  `#chat-pick`. A new text box needs an entry in `draft`, `typing()` wiring and its id in that list,
  or it loses its text mid-sentence.
- **In Chat the selection is the attachment list** (2026-11-05): `attachedIds()` has no focus
  fallback there and Esc does not clear it; the bulk bar is not rendered on that tab.
- **The API never sweeps or runs the planner itself** (2026-11-04): the Today tab's Refresh stores
  `state.refresh.requested` and wakes the scheduler (`app.state.scheduler.wake()`), which is the one
  thread that sweeps. `serve --no-scheduler` has no `app.state.scheduler`; the route answers 409.
- **The planner can run more than once a day** (2026-11-04): a create's `source_key` is
  `planner:e<evidence id>`, never date + counter (a second run overwrote the first run's task through
  `upsert_task`), and the prompt lists unjudged suggestions so they are not proposed again.
- **"Stale" for the Today summary is Python's call** (`summary.stale`), on a fingerprint of the rows
  the summary is about plus the urgent set; a new field that should invalidate a summary goes in
  `summary.FP`. The automatic rewrite (`summary.run`) must never write anything but `state.summary`.
  `summary.store` is given the bundle's snapshot (`b["snap"]`), never the live rows: a run takes
  seconds to minutes, and a row closed meanwhile would otherwise be forgotten, not flagged.
- **An empty `due` from a recording is "not said"** (2026-11-04): `sync_file.apply_result` drops it
  (with `hard`) on an update and on a re-synced create; only the UI, a command, Chat or a connector
  removes a date. `_clean_task_fields` is shared with those paths, so the rule lives in apply_result.
- **Removing a due date clears `hard` and `due_is_datetime` in `batches._apply_one`** (2026-11-04),
  and `""` means `None`. The connector's hourly cap counts those rows (`mcp_server._rows_written`).
- **A folded section renders no rows** (2026-11-04): `jumpTo` must unfold (`setFold`) before
  `render()`, and anything that looks rows up in the DOM only sees unfolded sections.
- **`scoring.score` is called only by `relations.scored` / `relations.score_one`** (2026-11-10), and
  lifecycle callers pass `relations.annotate`d rows (`test_only_relations_calls_scoring_score`,
  `test_every_surface_reads_annotated_rows`). A new reader that skips it ranks and fades without links,
  silently.
- **Undo skips any field whose current value is not what that batch wrote and says so** (2026-11-10);
  undo entries written before 2026-11-10 carry no `new` and are restored unchecked.
- **A staged text edit carries `expect`; a note is `add_note`** (2026-11-10), appended at apply time.
  Never send a whole new `context` built from an old read.
- **`relations` direction** (2026-11-10): `src part_of dst` (dst is the goal), `src waits_on dst`,
  `src same_as dst` (src was merged into dst). Soft-deleted, never DELETE.
- **What lands on a merged duplicate goes to its survivor in three ways only** (2026-11-10): a
  sync-file mention, a sync-file instruction, a Canvas close or due move. Fields are never copied, and a
  merge writes no mention.
- **A reopened duplicate is its own task again** (review 2026-11-11): `reopen` removes its outgoing
  `same_as` in the batch (`batches._unmerge`), and `relations.survivor` hops only from a closed row; with
  the row left active, a recording saying "Task-1 is done" closed Task-2. A re-synced merged create
  lends the survivor no `calendar_event`.
- **`link_suggestion` evidence is not the planner's** (2026-11-10): excluded like `mail_new`, and not a
  card's `evidence`. A judged pair is never suggested again.
- **The summary fingerprint grew a sixth field** (2026-11-10, `waits_on_open`); `stale` compares a
  stored fingerprint on its own length, so old summaries are not all stale at once.
- **Migrations are `db.VERSIONED`, keyed by `PRAGMA user_version`** (2026-11-10); one that changes data
  declares `needs_backup` and gets a backup-API copy in `data/backups/` first.
- **The words "blocked" and "dependency" appear in nothing a user or the agent reads** (2026-11-10):
  say part of, steps, waits on, frees, same as
  (`test_nothing_the_user_or_the_agent_reads_says_blocked_or_dependency`).
- **A staged change's tray key is `stageKey`** (2026-11-11): a link is `rel:<a>:<kind>:<b>`, never the bare id (that is the task's decision and would be overwritten); a new op needs a branch in `stageKey`, `changeWords` and `stageProposal`, or the tray drops or mislabels it.
- **Opening a task stamps `neighbours_seen_at`** (2026-11-11; `GET /api/tasks/{id}`, every call, paging included): the page fetches it only for an opened row or the task page (`ensureDetails`), never for every row, and clears the row's "updated by" marker itself when it arrives.
- **History arrives while things are typed** (2026-11-11): `loadDetail` swaps the two async containers in place (`swapDetail`) when `typingNow()`; a new async part of the opened task goes in `[data-dlinks]` / `[data-dhist]` or it is lost on arrival.
- **`.details` is built only for open rows** (2026-11-11): code that reads a row's details from the DOM sees only opened rows.
- **Today may draw a task in a group it has no date for** (2026-11-11; `arrangeToday` pulls a task that frees a hard-dated one into that group) and a filter may hide a row: `jumpTo` resets the link filter to All before giving up, and opens the task page when no list holds the id.
- **The phone trail is `history.state.from`** (2026-11-11): a page-to-page jump pushes `{page: true, from}`; nothing keeps a trail in memory.
- **Badges are `<a class="lb" href="/t/N">`** (2026-11-11): the row's click handler ignores `a`; a new control inside `.main` must be an `a`, `button` or `input`, or it opens / selects the row.
- **With no links nothing is drawn** (2026-11-11): the link bar, the legend and the Linked view appear only when `hasLinks()`.
- **A ripple writes only `context`, through `batches.apply` with source `ripple`** (2026-11-11); anything
  else it proposes is `proposal` evidence with `account = 'ripple'`. A new thing a ripple may do goes
  through `clean_proposal`, never into the batch.
- **`ripple_queue` rows are deleted only with their `last_batch_id`** (2026-11-11): a change made while a
  run is in flight must stay queued.
- **A ripple batch's `cause.batches` is how undo finds it** (2026-11-11); a new automatic batch that should
  be undone with its cause needs the same key.
- **`batches.apply` returns `ripple_queued` and wakes the scheduler for an outcome** (2026-11-11) through
  `ripple.set_waker`; a second process (the stdio skill server) is picked up by the next tick.
- **The ripple reply is `{"ripples": [...]}` and a wrong shape applies nothing** (2026-11-11).
- **`claude.ripple.enabled` is not in `PAID_SWITCHES`** (2026-11-11): that tuple is also what
  `effective --as-before` flips. It is off when missing like every paid run.
- **A ripple may drop words the owner wrote** (2026-11-11): the first real dry-run removed "in person" from
  a sibling's sentence while adding where things stand. The prompt says to keep the owner's sentences and
  never soften a requirement (`test_the_prompt_says_to_keep_what_the_owner_wrote`); the guard checks only
  links, `Task-N` and addresses, so History's Undo is the safety net.
- **Never pipe the dev or service start script's output through `tail` / `head` when it leaves a server
  running** (2026-11-11): the pipe never closes and the command hangs. And `GET /api/review` is loaded once
  per page (the in-flight flag in `app.js`) because its `last_offered_at` stamp commits on the shared
  connection; two at once answered 500.
- **`expire` only touches rows Review has shown** (`last_offered_at`, stamped by `GET /api/review`).
  A row that was never on a Review page stays open however old it is; that is by design.
- **The manifest must be requested with `crossorigin="use-credentials"`** (2026-11-10): otherwise Access
  answers it with its login page. The iPhone fetches the touch icon without the cookie, so it is inlined
  in `index.html` (a file behind Access drew a letter).
- **`/api` must never answer 401, 403 or a redirect** (2026-11-10): the page reads each as an expired
  sign-in and shows "Sign in again".
- **The build stamp covers `api.STAMPED` only** (2026-11-10): a new file the page loads joins that list
  and gets the `?v=` stamp; a Python-only change reloads no page.
- **Tab and task addresses are real paths** (2026-11-10), because a fragment may not survive the sign-in
  redirect: a new link into the app names `/t/<id>` or `/<tab>`, never `/#…`.
- **On a phone the task page is rendered into `#panel` (`ui.page`)** (2026-11-10): anything `wire()`
  installs on the panel works there too, and nothing may assume the panel holds a list.
- **The test suite never reads `.env`** (2026-11-10, `tests/conftest.py::_no_real_env`): a new secret is
  named in `.env.example`, and a test that needs one sets it with `monkeypatch.setenv`.
- **`.env.example`'s `NAME=` lines are the list `_no_real_env` clears** (2026-11-10): `FACTOTUM_CONFIG` and
  `FACTOTUM_DB` stay there as `NAME=` lines for that reason, even though both have defaults.
- **The tests read `tests/fixtures/factotum.fixture.yaml`, never the example** (2026-11-10): the example
  holds placeholders (`you@example.com`, `YOUR_DATABASE_ID`); a test that asserts on projects or
  accounts uses `conftest.FIXTURE`.
- **No `config/factotum.yaml` means the starter** (2026-11-10): `config.load` falls back to
  `config/factotum.starter.yaml`, marks the dict `_starter`, prints one stderr line per process, and the
  page shows "Starter settings in use." on every tab. A `FACTOTUM_CONFIG` naming a missing file falls
  through to `config/factotum.yaml`, then the starter, never to the example.
- **`owner.timezone` empty or not a zone means the machine's zone** (2026-11-10): `config.load` fills it
  from `TZ`, `/etc/timezone`, the `/etc/localtime` link, else `UTC` (a misspelt name prints one stderr
  line); `factotum effective` shows that origin as `machine`.
- **Accounts are seeded once** (2026-11-10): `db.seed_accounts` records `state.accounts_seeded`, so a
  table emptied on purpose stays empty; a table that already has rows is marked without inserting, an
  empty seed list does not use the seeding up, and the starter is never seeded from.
- **The service worker has no fetch handler, ever** (2026-11-10): it would let the worker store or
  serve the app and make it stale. `/sw.js` is served from its own route with no-cache and is not in
  `api.STAMPED` (the browser re-checks a worker on each navigation; a worker change must not reload
  open pages).
- **The push key pair is `push-keys.json` beside the database** (2026-11-10): 0600, never in git,
  never in an `/api` answer but its public half. Only `GET /api/push` and subscribe make one, and a
  new pair empties `push_subscriptions` (every device was bound to the old key). The scheduler never
  makes one: missing or unreadable at send time is a `KeyFileError`, nothing sent, the reminder
  waits, the Log says why. An unreadable file is never overwritten; fix or delete it by hand.
- **A notification's look is defined twice** (2026-11-10): Safari shows the declarative payload
  (`app_push.payload`) without running the worker; other browsers run `sw.js` `shape()` on the same
  JSON. Change both together. No `mutable` member: with it Safari would run the worker and wait on it.
- **`app_push.send_all` makes every network call before its first write, then commits the device
  rows itself** (2026-11-10): a 410 removal must survive the caller's rollback. It raises
  `NothingSent` when no device took it.
- **The payload is at most 3993 bytes of plaintext** (2026-11-10, `webpush.MAX_PLAINTEXT`, one
  4096-byte record): `payload()` trims the body, then the title, then drops the buttons last; it never
  cuts `navigate`, the tag or `data`, so a new notice must keep those short (one too large is refused
  by `encrypt` and recorded as a failure).
- **The server posts only to known push services** (2026-11-11, the stage review): `app_push.endpoint_problem`
  checks a subscription's host at subscribe AND before every send (Google, Mozilla, Apple, Microsoft,
  `.invalid`, plus `notify.app.push_hosts`; never an IP, `localhost` or a numeric host). Without it,
  anyone who reaches `/api/push/subscribe` makes the server POST wherever they like. Never repeat a
  push service's response body in a route, a row or the Log: status and host only.
- **The permission is asked first thing in the tap handler** (2026-11-10, `pushOn`): an `await`
  before `Notification.requestPermission()` makes iOS refuse it.
- **Notification buttons use the reminder's token links, which stay behind the sign-in**
  (2026-11-10): never add `/r/*` or `/sw.js` to an Access bypass; a button that gets a redirect, an
  error or no network opens the task page.
- **A device's links are built on its own origin** (2026-11-10, `push_subscriptions.origin`), so the
  dev instance's test notifications open the dev instance.
- **A task added by hand exists on Enter** (2026-11-11; `POST /api/add`, one `ui` batch, Source `Manual`);
  everything else a person edits stages. Its `key` makes a retry or a second Enter the same task
  (`source_key` `add:<key>`); a new line needs a new key, and each box has its own (`addKeyOf(box)`, `renewKey`):
  with one shared key a lost response let another box's line come back `already: true` and be erased (review 2026-11-11).
- **An add line is never a tray command** (2026-11-11): `commands.parse` returns None for it; `parse_add`
  reads it, a refused part stays in the name, and the page sends add lines to `/api/add` before `/api/commands`.
- **A chat create in the tray is `from_chat`** (2026-11-11): the server takes its fields from the stored
  proposal and sets `source_key` and `created_source`; the API strips both from what the page sends. A link may
  name a sibling create's `ref` only inside one batch with that create (or once Create made it); in the tray
  that ref is `m<message>-<index>`, never the model's `s1`, which another message may use too.
- **One thread per task for a Review card and an opened task; one turn per thread** (2026-11-11; 409): the
  page runs one turn at a time and gives the text back when it cannot send.
- **A new text box in the page needs `isDraftBox`** (2026-11-11; `DRAFT_BOXES` or the `ed-…-<id>` /
  `step-line-<id>` forms), a `draft` entry and its `typingNow` count; a box that can close with its text kept
  counts only while drawn, or a closed one keeps every return from reloading the lists.
- **On a phone one thing at the bottom** (2026-11-11: tray, short bar, pinned bars, the round button;
  `body.fab-on`), and `body.kb-open` hides them all while a box has focus; a new bottom bar must join both rules.
- **The `who` field writes `counterpart` or `waiting_on`** (2026-11-11), never both.

## Gotchas carried over from plaud-sync (dates are when they were learned)

- **A tool result over ~40 KB never reaches Claude** (2026-09-15); it spills to a file and the
  run burns minutes rebuilding it. That is why transcripts come through the `plaud` CLI as
  files and why nothing reads whole Notion views. Keep every tool result small by design.
- **MCP write tools in a project `ask` list stall headless runs** (2026-07-22). The run exits 0
  with a prose apology and nothing written. Keep Notion, Calendar and Mailbird write tools out
  of `ask`; headless allowlists are explicit per run kind (SPEC §8).
- **Headless `claude -p` cannot re-authenticate** (2026-09-15). A dead refresh token exits in
  ~50 ms with an empty stderr; the reason is only in the JSON envelope. Port plaud-sync's
  `check_auth()` pre-flight into the runner in phase 3, and always log envelope `result`.
- **`claude -p --bare` reports "Not logged in"** (2026-11-01): bare mode skips the OAuth login, so a
  headless run that passes it dies in 400 ms with exit 1. The runner never passes it; it isolates
  with `--strict-mcp-config` and a run directory instead.
- **The interim `--new-only` cron must be gone once the trigger writes tasks** (2026-11-01): plaud-sync
  still creates Notion task rows for every recording until it is stopped, and the cron would import
  them as twins of what sync-file just wrote. Removed at the phase 3 restart.
- **Restarting mid-run loses that recording** (2026-09-16). Before `systemctl restart factotum`,
  check `journalctl -u factotum -n 5` for a Claude run in flight and wait for it.
- **In the Notion Tasks DB, `Calendar Event` and `last_edited_time` are automation artefacts**
  (2026-11-01): plaud-sync created an event for every dated task and its planner rewrote `Why now`
  and `Score` on every open row, so neither says anything about the user. The import classifier
  uses mentions, source and creation date instead (SPEC §12).
- **Notion SQL queries are metered; the REST API is not.** Nothing here uses the MCP query
  tool.
- **Mailbird's MCP is on the Windows host** at `https://127.0.0.1:52000/mcp`, self-signed TLS,
  bearer token in `.env`. From WSL it is reachable only with mirrored networking. TLS
  verification is off for that loopback URL only, never globally.
- **A `cp` of `data/factotum.db` is stale** (2026-11-01): the DB runs in WAL mode and the service
  holds the log open, so the main file can lag hours behind `factotum.db-wal`. Copy it with
  `sqlite3.Connection.backup` (or `VACUUM INTO`), never `cp`; a copy made the wrong way shows
  "No open tasks" and sends you chasing a UI bug.
- **ntfy.sh caps delay at 3 days and 250 messages/day.** The service owns the reminder
  schedule and publishes at fire time; never hand ntfy a far-future delay.

## Rules

- `main` only, small commits, `pytest` green at every commit. Commit messages end with
  `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- No secrets, runtime data or per-person config in git; `.gitignore` already covers
  `.env`, `config/factotum.yaml`, `data/`, Google credential files.
- When a decision changes, update `SPEC.md`; when a setup step changes, update `docs/SETUP.md`;
  when a gotcha is learned, add it above with the date.
- Prefer a config key over a code branch when the difference is between users.
