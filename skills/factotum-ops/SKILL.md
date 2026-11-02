---
name: factotum-ops
description: Operate and troubleshoot the Factotum service (the task system that replaced plaud-sync) — the systemd unit, the scheduler and its sweeps (mail, Canvas, calendar, Notion mirror), the morning brief and planner, ntfy reminders, the Plaud trigger, headless Claude runs and their cost, config keys and secrets, the Log tab. Use for "is Factotum running", "why didn't the brief arrive", "the reminder didn't fire", "what did the planner do", restarting after a deploy, checking a connector, and adjusting intervals or budgets.
---

<!-- skill-version: 2026-11-02 -->

# Factotum — ops

One service on the always-on machine: `factotum.service` runs the API, the UI and the scheduler
thread from `/home/robert/factotum` with `.venv/bin/python` (never system Python). The repo's
`CLAUDE.md` is the authoritative short reference; `docs/SETUP.md` is the host setup; `SPEC.md`
the design. Read those before changing anything.

## First look

```bash
systemctl status factotum --no-pager | head -5
journalctl -u factotum -n 30 --no-pager
```

Then the Log tab (http://127.0.0.1:8750/#log or the public hostname): every automation that did
something, and every Claude run with its cost, is a row in `runs`; batches with every field change
and an Undo are above it. Through the `factotum` MCP server, `task_get` shows one task's history.

## Checks, in the order they usually fail

```bash
cd /home/robert/factotum
.venv/bin/python -m factotum claude-check     # the headless login (a dead refresh token dies in 400 ms, exit 1)
.venv/bin/python -m factotum plaud-check      # the plaud CLI and the trigger mailbox
.venv/bin/python -m factotum mail-check       # Mailbird reachable (Windows side, mirrored networking), accounts vs monitored
.venv/bin/python -m factotum canvas-check     # Canvas items vs rows
.venv/bin/python -m factotum calendar-check   # Google token
.venv/bin/python -m factotum chat-check       # the MCP server over a pipe + one haiku run through it (~$0.002)
.venv/bin/python -m factotum slack-check | discord-check   # when those sources are configured
.venv/bin/python -m factotum mirror --dry-run # rows the Notion mirror would push
.venv/bin/python -m factotum tick             # one scheduler tick — ONLY with factotum.service stopped: two schedulers on one DB send the brief twice and fire reminders twice
```

## Things that bite (dates are when they were learned)

- **Restart only when no Claude run is in flight** (`journalctl -u factotum -n 5`): a restart
  mid-run loses that recording or chat turn.
- **The brief is "at or after HH:MM, once a day"**: a deploy after 06:30 sends it immediately.
  Two briefs in a morning means two schedulers (or the old lark-server timers) were alive.
- **"database is locked" on Apply** (2026-11-01) means a scheduler branch left a transaction open;
  the fix is always a missing `conn.commit()` in `scheduler.py`, never a longer timeout.
- **Headless runs see no hooks or skills** (`--setting-sources ""`): a run that behaves as if it
  loaded your settings is being started some other way.
- **A chat run whose MCP server failed still "succeeds"**: `system/init` says
  `{"name": "factotum", "status": "failed"}` and the model answers from nothing. `chat-check`
  reproduces it; the cause is usually `PYTHONPATH` or a print to stdout before the protocol starts.
- **`cp data/factotum.db` is stale** (WAL): copy with `sqlite3.Connection.backup` or `VACUUM INTO`.
- **ntfy caps a delay at 3 days** and 250 messages a day; the service publishes at fire time.
- **A reminder tap shows the message in ntfy** (no Click header since 2026-11-02); "Open" is one of
  its three buttons. `notify.ntfy.tap_opens_app: true` restores the old jump.

## Where a setting lives

| Changing… | Where |
|---|---|
| review time, fade days, sweep intervals, planner on/off, chat budget, sources and sinks on/off | Settings tab (stored in the DB; saves on change) |
| projects, weights, Canvas course map, mail allow/deny, Notion property names, Slack workspaces, Discord servers | `config/factotum.yaml` |
| tokens and passwords (Notion, Mailbird, ntfy, Canvas, SMTP, IMAP, Slack, Discord, the MCP route) | `.env` — never print its values |
| models, budgets, timeouts per run kind | `config/factotum.yaml` `claude:` (the chat budget also in Settings) |

Person-specific facts never go in a `.py` file; that is a bug, not a shortcut.

## Costs

sync-file ≈ $0.15 per recording, planner ≈ $0.30 a morning, a chat turn $0.05–0.20, chat-check
$0.002. `GET /api/stats` reports the last 7 days; the Log tab shows each run.

## What this skill may do

Read anything above and explain it. Edit `config/factotum.yaml` or the Settings tab when the user
asks. Restart the service when the user asks and no run is in flight. Never edit `.env` values
into a message, never run two `serve` processes against one database, and never bypass
`batches.apply` to change a task (the `factotum` MCP server's `stage_changes` stages a batch for
the user's tray; `sqlite3` by hand is not a write path).
