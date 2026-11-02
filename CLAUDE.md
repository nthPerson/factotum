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
   email address or a project weight in a `.py` file is a bug; it belongs in
   `config/factotum.example.yaml` (structure) or `.env` (secrets). The tool is meant to be
   handed to other people.

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
batch changes, planner suggestions on Review, the phone layout. Retiring the old Notion pages and
plaud-sync is the user's checklist (SETUP §15). Update this paragraph when something lands.

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
| verdicts on evidence (dismiss, accept, reviewed), planner suggestions on Review | SPEC §9 | `factotum/batches.py` (`judge_evidence`), `api.py` (`/api/review`, `/api/review/suggestions`) |

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
.venv/bin/python -m factotum sweep mail|canvas|calendar       # one sweep now
.venv/bin/python -m factotum mirror [--all|--dry-run]         # the Notion tasks mirror: rows edited since the last pass, or every row
.venv/bin/python -m factotum slack-check | discord-check      # tokens, workspaces / servers, what each can read
.venv/bin/python -m factotum planner --dry-run [--send]       # the morning planner (~$0.30): JSON only, or applied and mailed
.venv/bin/python -m factotum chat-check                       # the MCP server over a pipe, Mailbird, one haiku run through it
.venv/bin/python -m factotum chat "text" --attach 388         # one chat turn from the terminal (~$0.20), stored like a UI turn
.venv/bin/python -m factotum mcp                              # the stdio MCP server itself (the runner starts it; rarely by hand)
.venv/bin/python -m factotum serve --port 8751 --no-scheduler   # a second instance for UI checks; never two schedulers on one DB
.venv/bin/python -m factotum serve                 # http://127.0.0.1:8750
sudo systemctl restart factotum                    # after code changes; see the restart rule below
journalctl -u factotum -n 20
```
The service runs `.venv/bin/python`, never system Python (`deploy/factotum.service`).

## Conventions that break silently

- **Settings-tab keys are a whitelist** (`config.SETTINGS_KEYS`). A new toggle needs the key
  there, a control in `web/app.js` `renderSettings`, and a default in the example YAML.
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
- **`expire` only touches rows Review has shown** (`last_offered_at`, stamped by `GET /api/review`).
  A row that was never on a Review page stays open however old it is; that is by design.

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
