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

Phase 1 of 6 is built (SPEC.md §14): store, scoring, lifecycle, Notion import, batches, command
grammar, API, and the Today / Timeline / Projects / Review / Settings tabs. Chat, reminders,
sweeps, the Plaud port and the Claude runner do not exist yet; the UI shows "phase N" where a
control is a placeholder. Update this paragraph when a phase lands.

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
| Claude runs (phase 3+) | SPEC §8 | `factotum/claude/` (not yet), `prompts/` (not yet) |

## Commands

```bash
.venv/bin/pytest                                   # offline, must stay green
.venv/bin/python -m factotum init-db
.venv/bin/python -m factotum import-notion --dry-run          # bucket report, writes nothing
.venv/bin/python -m factotum import-notion --apply            # the one-time migration
.venv/bin/python -m factotum import-notion --new-only --apply # interim: pull rows plaud-sync created since
.venv/bin/python -m factotum score | review | expire [--apply]
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
