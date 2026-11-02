# Factotum

A self-hosted task triage service. Recordings (Plaud), course deadlines (Canvas), email
(Mailbird) and your calendar go in; a short, honest task list comes out, kept short by rules
rather than by willpower. Claude does the judgment work (filing a recording, extracting real
commitments, reading email context, drafting replies); Python does every write.

- **SPEC.md** — the full design: why, principles, architecture, data model, lifecycle rules,
  inputs, outputs, Claude runs, UI, reminders, deployment, migration, build order.
- **docs/SETUP.md** — getting a machine ready: WSL2 + systemd, Plaud CLI, Mailbird MCP,
  Cloudflare tunnel + Access, ntfy, Notion integration, Google Calendar credentials.
- **REPO_RETIREMENT_AND_NEW_REPO_INITIALIZATION.md** — how this repo takes over from
  `nthPerson/plaud-sync`, phase by phase.

## Status

Phases 1 to 4 of 6 (see SPEC.md §14): store, scoring, lifecycle, the one-time Notion import with
its dry-run report, undoable batches, the command grammar, the API and the tabs; the automations
(ntfy reminders with action buttons, the daily review push, the expire pass, the morning brief
over SMTP, the Google Calendar sweep); the Plaud port (IMAP trigger, `plaud` CLI fetch, one
headless Claude run per recording whose JSON Python applies); the mail sweep over Mailbird's MCP
(read-only: replies flip Waiting tasks, sent mail closes reply tasks, invitations and
confirmations become evidence); the Canvas sweep; and the morning planner, whose verdict and
proposals lead the brief. Chat and the Notion tasks mirror are phases 5 and 6.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                       # fill in NOTION_TOKEN at least
cp config/factotum.example.yaml config/factotum.yaml   # edit projects, accounts, times
.venv/bin/python -m factotum init-db
.venv/bin/python -m factotum import-notion --dry-run   # bucket report, writes nothing
.venv/bin/python -m factotum import-notion --apply
.venv/bin/python -m factotum import-notion --new-only --apply   # hourly, until the old system stops writing
.venv/bin/python -m factotum serve                     # http://127.0.0.1:8750
.venv/bin/pytest                                       # 77 tests, no network
```

## Adapting it to your own workflow

Everything person-specific is in two files: `config/factotum.yaml` (projects and weights,
which inputs and outputs are on, reminder times, Notion database ids and property names,
Canvas course map, email allow/deny) and `.env` (every secret). The Settings tab overrides the
lifecycle and notification values and manages email accounts; it names the config file so you
know where the rest lives. Each input and each sink can be turned off; with Notion off, Factotum
is a standalone SQLite task list with a web UI.

## Layout

```
factotum/            the service
  config.py          yaml + .env + Settings-table overrides
  db.py              schema (SPEC.md §4) and helpers
  scoring.py         deterministic score / priority
  lifecycle.py       tiers, fade, last call, review queue, import classifier
  commands.py        the `done 389 392` grammar
  batches.py         the one write path, with undo
  api.py             FastAPI routes + static UI + /r/<token> reminder actions
  scheduler.py       the 30 s tick: reminders, review push, expire, brief, calendar sweep
  reminders.py       firing, repeats, action tokens        notify.py  ntfy publisher
  brief.py           the morning brief (SMTP)              gcal.py    Google Calendar auth, events, sweep
  importers/         notion_tasks.py (one-time migration)
  sinks/             notion_mirror.py (one-way task mirror)
  web/               index.html, app.css, app.js
config/              factotum.example.yaml
deploy/              factotum.service (systemd, WSL2)
docs/                SETUP.md
tests/               pytest, offline
```
