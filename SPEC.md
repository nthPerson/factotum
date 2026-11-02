# Factotum — design spec

Successor to `PLANNER-SPEC.md` in `nthPerson/plaud-sync`. Drafted 2026-11-01 from a brainstorm
with Claude; the clickable mockup is https://claude.ai/artifact/DcutyeKweJaxWYJGBGVr8X.
Status: **approved by Robert 2026-11-01**; phase 1 scaffolded in this repo.

Factotum replaces the three Claude-driven loops in plaud-sync (`plaud_watcher.py`,
`planner.py`, the email command channel) with one service that owns the task list, runs on
**mf-pc (Windows 11, WSL2 with systemd, always on)**, is used through a **web UI behind
Cloudflare Access**, and reminds through **ntfy**. Notion stops being the task store and becomes
an optional records sink. Claude stays in the loop for judgment only; every write is Python.

It is also meant to be **handed to someone else**: everything person-specific lives in
`config/factotum.yaml` and `.env`, every input and every sink can be switched off, and the
Settings tab names the config file so a new user knows where the rest lives.

## 1. Why (measured 2026-11-01 on plaud-sync)

| Signal, 2026-09-04 → 11-01 | Value |
|---|---|
| Open tasks | 26 → 274 |
| Overdue · Inbox (never decided) | 50 · 45 |
| Tasks closed by automation, total | ≈12 (all Canvas) |
| Email commands Robert sent, total | 10 |
| Planner spend | ≈$2.50/day |
| Sync runs failed in October | 14 of 64, mostly the 40 KB transcript spill |

Four causes, none of them the user's discipline:

1. **Extraction has no bar.** One meeting produced ten rows, several of which nothing can ever
   close ("Evaluate Christian as a junior developer after the group project").
2. **Undated tasks never leave.** The only lever is a "Decide or drop" list in an email.
3. **Scoring rewards rot.** A soft date one day overdue scores 85 plus weights, clamped to 100,
   so stale promises own the Must list and the brief became ignorable.
4. **Every action is a headless Claude run** ($0.2–0.5, 30 s), so batching is impossible.

## 2. Principles

- **SQLite is the source of truth.** Notion, calendar and Mailbird are sources and sinks.
- **Claude decides, Python writes.** Claude returns JSON proposals; the service validates and
  writes. Scoring is a formula and lives in Python.
- **Claude proposes, the tray applies.** Every change from Claude, the UI or a command lands in a
  pending batch that the user applies in one click, with one undo per batch. Evidence-based
  automatic closes are the one exception and are logged as such.
- **Outflow must not depend on the user.** Every task has a close signal or an expiry.
- **The evidence rule**: delete or close on good evidence, never on a hunch. Headless runs can
  never send, delete, move or archive mail.
- **Configurable, not forked.** A new user edits two files and the Settings tab; they never
  edit code to change projects, weights, databases, accounts or which integrations are on.
- **Bounded cost.** Per-run budgets in §13; the rolling total is visible in Settings.

## 3. Architecture

```
                 mf-pc (Windows 11, always on)
 ┌──────────────────────────────────────────────────────────────────────────┐
 │  Mailbird Next ──MCP over HTTPS, bearer──▶ 127.0.0.1:52000/mcp           │
 │                                                  ▲ (mirrored networking)  │
 │  WSL2 + systemd                                  │                        │
 │  ┌──────────────────────────────────────────────┼─────────────────────┐  │
 │  │ factotum.service (one Python process, FastAPI + scheduler)         │  │
 │  │   store/      SQLite (tasks, reminders, links, batches, chat, runs) │  │
 │  │   web/        UI + JSON API  ◀── cloudflared ◀── factotum.nthperson.dev (Access)
 │  │   mail/       Mailbird MCP client (Python, deterministic sweeps)    │  │
 │  │   plaud/      IMAP IDLE trigger + `plaud` CLI fetch                 │  │
 │  │   canvas/     Canvas API                                            │  │
 │  │   calendar/   Google Calendar (OAuth desktop client or service acct)│  │
 │  │   claude/     runner for `claude -p` (stream-json, allowlists)      │  │
 │  │   notify/     ntfy publisher + reminder scheduler + morning brief   │  │
 │  │   sinks/      Notion REST (notes; optional task mirror)             │  │
 │  │   mcp/        `factotum mcp` — stdio server exposing tasks_query    │  │
 │  └────────────────────────────────────────────────────────────────────┘  │
 └──────────────────────────────────────────────────────────────────────────┘
          │ ntfy.sh (private topic)            │ Notion API         │ Google Calendar API
          ▼                                    ▼                    ▼
       phone / laptop                      notes DB, project dashboards
```

**Who calls what.**

| Job | Runs | Uses Claude? | Uses Mailbird? |
|---|---|---|---|
| Plaud recording arrives | IMAP IDLE on the trigger account → `plaud` CLI → one Claude run | yes (file + extract) | no |
| Mail sweep | every 20 min | no | yes, Python client |
| Canvas sweep | every 60 min | no | no |
| Calendar sweep | every 60 min | no | no |
| Planner run | 06:30 daily (+ on demand) | yes (judgment + brief) | no (Python pre-built the bundle) |
| Reminders / review push | scheduler | no | no |
| UI batch apply | on click | no | no |
| Command grammar (`done 389 392`) | on submit | no | no |
| Chat turn, email search, draft reply | on demand | yes | yes, via MCP in the run |

**Mailbird's role, explicitly.** The server is streamable HTTP with a bearer token on the
Windows host, so Python can call it directly. Three consumers with fixed boundaries:
1. *The mail sweep* (Python, no Claude): lists new conversations in each enabled account's Inbox,
   closes "reply to X" tasks on a sent message to X, flips Waiting tasks on a reply, watches
   linked threads, collects `.ics` and confirmation-shaped mail as candidate evidence. Reads only.
2. *The planner run* (Claude): judges the candidates Python collected.
3. *On-demand runs* (Claude, from the UI): "Search related emails", "Draft reply", Chat. Read
   tools plus the draft tools. **Never** `send_message_now`, trash, delete, move, archive, spam,
   block, unsubscribe or mark read.

## 4. Data model (SQLite)

See `factotum/db.py` for the executable schema. Summary:

```
tasks(id, name, project, area, status,            -- open|done|dropped|faded|lapsed
      legacy_status, top_rank, longterm, due, due_is_datetime, hard, effort,
      source, source_key UNIQUE, source_link, account, counterpart, waiting_on, waiting_since,
      context, why_now, score, priority, manual_priority, mentions, last_mention_at,
      created_at, edited_at, completed_at, closed_by,     -- user|canvas|mail-evidence|fade|lapse|voice|chat|import
      snoozed_until, last_reviewed_at, calendar_event, notion_page_id, canvas_id)
reminders(id, task_id, fire_at, label, repeat, status, ntfy_seq, token, created_by)
          -- repeat: NULL|daily|weekdays|weekly|monthly; status: pending|sent|snoozed|done|cancelled
links(id, task_id, kind, ref, label, account, created_at, last_seen_at)
          -- kind: plaud_note|notion_page|canvas|mail_thread|mail_message|calendar|task
mentions(id, task_id, source_kind, source_ref, at)       -- append-only; resets the fade clock
batches(id, created_at, applied_at, undone_at, source, undo_json)
changes(id, batch_id, task_id, field, old, new)
chat_threads / chat_messages / runs / accounts / settings
```

Task ids continue the Notion numbering (next id after import: 395), so `Task-<n>` stays the
code the user types and says.

## 5. Task lifecycle

**Tiers.**
- **Top**: `top_rank` set by the user only (UI drag, `t`, Review star, voice "put X at the top").
  Soft cap 7. Never fades, never lapses, always first in Today and the brief.
- **Dated**: has `due`. Hard deadlines auto-close on evidence (Canvas submission, a sent reply to
  `counterpart`, a calendar event that passed with an accepted invite, an explicit Done). Past
  `due + last_call_days` (default **7**) with no evidence → one *Last call* in Review → then
  `lapsed` unless kept.
- **Backlog**: undated, not long-term. `fade_days` (default **21**) after `last_mention_at` with
  no edit → *Fading* in Review (`fade_warn_days` before) → then `faded`.
- **Long-term** flag: exempt from fading. Per task, by voice, or by default for projects marked
  `never_fade` in config (seed: BDA M.S., SCIBER-CT, Evidential Deep Learning).

**Resurrection.** A new mention of a faded or lapsed task reopens it, bumps `mentions`, resets
the clock, and flags it *Back* in Review.

**Status Inbox is retired.** Uncertain extractions either fail the bar (and become a
"Mentioned follow-ups" list on the note page) or are created open and show as *New* in Review.

**Extraction bar** (replaces sync prompt Step 3b): create a task only when at least two of
(a) a first-person commitment, (b) a named person expecting it, (c) a date or time, (d) a
deliverable verb hold. Cap `max_undated_per_recording` (3). Lectures yield only announced
deliverables.

**Scoring** is `factotum/scoring.py`, parameters under `scoring:` in config. Soft overdue decays
(70 − 5/day, floor 25) instead of clamping. Claude writes `why_now` for the rows the morning
brief shows (≤ 15); it never writes scores.

**Review queue** (`lifecycle.build_review_queue`), ordered: last call · thread updated · back from
Later · new · soft slipped · fading soon · back · reminder missed. *New* means never decided on
(`last_reviewed_at` is null), not "created since the last session": an item the user did not get to
stays in the queue. The queue is served in pages of `review_cap` (15); ids already staged in the tray
are excluded from the next page, and after a page the UI offers "Review another 15 of the remaining
N" rather than loading it unasked. Decisions: **Done · Keep · Later · Drop**, plus ★ Top and
Long-term. Every decision sets `last_reviewed_at`; *Keep* changes nothing else and suppresses the
item until something changes; *Later* sets `snoozed_until` to tomorrow, Friday, next week or a
picked date, and never touches `due`. A decision may carry a free-text note, appended dated to
`context` in the same change. Applying a Review batch marks the day reviewed (the phase 2 push
stays quiet). Phase 5 adds a prompt field on the card: a chat turn with the task attached whose
proposal is staged into the same batch (e.g. "add that the first pass is done and ~30 PDFs still
need fetching by hand").

## 6. Inputs (each with an `enabled` flag)

**Plaud.** IMAP IDLE on the account with the `plaud_trigger` role (instant and reliable; Mailbird
has no push). On a Plaud mail: `plaud search` → id; `plaud transcript <id> -o` and
`plaud summary <id> -o`; one Claude run (§8.1) reads the two files; Python writes the note page,
tasks, mentions, reminders and calendar links. No large payload ever passes through a tool
result, so the 40 KB MCP spill is gone. One automatic retry and a failure alert stay.

**Mailbird.** Accounts and roles live in the `accounts` table, managed in Settings → Email
accounts (add, edit, remove, enable). Allow/deny globs in config. Sweep every `sweep_minutes`.

**Canvas.** `/api/v1/planner/items` with submission state; course map, effort by type and skip
patterns in config. Auto-close on submitted/graded/excused/complete.

**Calendar.** `sources.calendar` in config, toggled in Settings. Provider `google` with an OAuth
Desktop-app client (`config/google-credentials.json`, token written once by
`python -m factotum google-auth`), or `google-service` with a service account the calendar is
shared with. Used for: closing Dated tasks on passed, accepted events; creating deadline events;
the planner's 14-day look-ahead. With the provider off, nothing calendar-related runs and the
Claude runs lose the calendar tools.

**Commands.** The email command channel is retired. Voice stays (§8.1 applies spoken
instructions, including reminder and tier verbs). The UI grammar handles
`done|drop|snooze|push|defer|top|longterm <ids…> [to <when>]` with no Claude run.

## 7. Outputs (each with an `enabled` flag)

- **Notion notes** (`sinks.notion_notes`): one page per recording in the Plaud notes database
  with Summary, Mind Map, Transcript and "Mentioned follow-ups". Database id, data source id and
  the `Source Link` prefix are config.
- **Notion tasks mirror** (`sinks.notion_tasks_mirror`): one-way upsert of every task row so
  relations and dashboards keep showing history. Never read back. The Notion property names are
  config (`properties:`), so a user with a different layout edits YAML, not code. Default on
  for Robert; the Settings tab shows the toggle and the config file name. With it off, Factotum
  is a standalone SQLite task list.
- **Morning brief** (email at `notify.morning_brief.time`): verdict, Top, due today, reminders
  firing today, hard deadlines this week, closed on evidence since yesterday, waiting on. No
  evening email.
- **ntfy** (§10).

## 8. Claude runs

All runs: `claude -p` as the service user, `--output-format stream-json`, explicit
`--allowedTools` built from which sources are enabled, a budget guard in the runner, JSON-only
final message, logged to `runs`. Model and budget per run kind are config.

### 8.1 `sync-file` (one per recording)
Inputs: subject, received time, the two files from the Plaud CLI, the store's slice for the
same project (≤ 50 rows, inline), the taxonomy from config. Tools: Read (those two files only),
calendar tools if enabled, Notion read (dedup against an existing page). Returns:
```json
{ "note": { "action": "create|update", "properties": {…}, "body_md": "…", "followups": ["…"] },
  "tasks": [ { "op": "create|update", "id": null, "name": "…", "project": "…", "due": "…", "hard": true,
               "effort": "S", "counterpart": "max@…", "context": "…", "source_key": "plaud:<id>:1" } ],
  "instructions": [ { "op": "done|defer|drop|waiting|priority|start|top|longterm|remind|correct",
                      "task_id": 364, "fields": {…}, "evidence": "spoken sentence" } ],
  "reminders": [ { "task_id": null, "task_ref": "tasks[0]", "fire_at": "2026-11-02T14:00", "label": "Get started" } ],
  "calendar": [ … ], "unresolved": [ "…" ] }
```

### 8.2 `planner-morning`
Inputs: a bundle Python built: scored open rows (top 60 by score plus every Top row), the mail
sweep's candidate asks and evidence, Canvas deltas, reminders due today, calendar for 7 days.
Tools: calendar read, Mailbird read (to inspect a candidate). Returns proposals, `why_now` for
≤ 15 rows, the verdict, the brief HTML. Python auto-applies proposals that cite evidence, stages
the rest for Review, sends the brief.

### 8.3 `chat-turn` (Chat tab, "Search related emails", "Draft reply")
Inputs: the prompt, the attached tasks as JSON, thread history via `--resume`. Tools:
`factotum mcp` (`tasks_query`, `task_get`, `reminders_list`; read-only), Mailbird read + draft
tools, Plaud read, calendar read, Notion read. Returns, streamed, then final:
```json
{ "reply_md": "…",
  "proposals": [ { "op": "set|done|drop|merge_into|create|link_thread|add_reminder|pin_top|longterm",
                   "task_id": 388, "fields": {…}, "label": "Task-388 → due Thu Nov 5",
                   "evidence": [ { "kind": "mail_message", "ref": "<id>", "account": "…", "label": "Oct 29 15:03 from Max" } ] } ],
  "drafts": [ { "account": "…", "message_id": "…", "to": "…", "subject": "…" } ] }
```
The UI renders proposals as checkboxes; "Stage selected" puts them in the tray. Drafts are
created in the run (reversible, never sent) and listed with "Open in Mailbird"
(`show_notification`). "Search related emails" is a chat turn with a canned prompt and the task
attached; the optional prompt field is appended. Found threads become `mail_thread` links, which
the sweep then watches for free, so a task linked once never goes stale silently.

## 9. The UI

One page, phone-friendly, served by the service. Tabs: **Today · Timeline · Projects · Review ·
Chat · Log · Settings**. The mockup is the reference. Rows show only: checkbox, code, name, tier and
fade chips, next reminder, project, due, effort, staged-change chip; the chevron opens details
(score reasons, why now, context, source and account, links, lifecycle, reminders, actions, the
Claude buttons). Top rail with drag. Composer on every tab with attached chips; grammar commands
stage instantly, anything else opens Chat. A tap on a row or its checkbox selects it (no modifier,
so it works on a phone); a double-tap opens its details; Esc clears the selection. The bulk bar
floats at the bottom of the screen with any selection (it never shifts the list, so the second
tap of a double-tap lands on the same row) and offers Done, Drop, Snooze, a due date to set or
clear, Top and Long-term. Due dates can also be set or cleared from a row's
details and from a Review card, staged beside the decision rather than replacing it. The Log tab
lists recent batches with every field change (old → new, task named) and an Undo per batch, and
the Claude runs with their cost. Assets are served with `no-cache` and version-stamped URLs so a
deploy reaches the phone without a hard refresh. Tray with one Apply and
one Undo. Review as a card stack. Settings: lifecycle values, inputs and outputs on/off, email
accounts table, ntfy topic, brief time and recipient, with a note naming the config file.
Keyboard: `j k d x s t l e`, Esc deselects; Review `d k s x`.

## 10. Reminders and push

- The public ntfy.sh server caps delay at 3 days and 250 messages/day, so **the service owns the
  schedule** and publishes at fire time.
- Task reminder: title = task name, body = label + due, `Click` = the task URL behind Access,
  up to three `view` actions (*Snooze 1 h*, *Done*, *Open*) pointing at `/r/<token>?…`, since
  `http` actions cannot carry an Access session. Tokens are single use, 24 h.
- Review push: daily at `notify.review_time`; skipped when reviewed today or the queue is empty.
- **Setting one.** "Set reminder" on a Review card and in a row's details: date-time, optional
  label, once or daily / weekdays / weekly / monthly. It is a staged change (`add_reminder`) under
  its own key, so it rides in the same batch as the decision without replacing it, Keep, Later or
  even Drop; undoing the batch cancels it. A reminder fires whatever the task's status is: a
  dropped task can still carry a "check anyway" nudge. When a repeating reminder fires, the
  scheduler marks it sent and inserts the next occurrence as a new pending row (phase 2). `fire_at`
  is naive local wall time (`YYYY-MM-DDTHH:MM`) in `owner.timezone`; the scheduler converts.
- Voice: "add X due next week, remind me tomorrow afternoon" → a task plus one reminder.
- The morning brief lists reminders firing today, so email and push agree.

## 11. Deployment

See `docs/SETUP.md` (WSL2 systemd, Plaud CLI, Mailbird check, ntfy, Notion integration, Google
credentials, Cloudflare tunnel and Access, the unit). Secrets only in `.env`. A new repo
(`nthPerson/factotum`) because the tool is meant to be adopted by others; `plaud-sync` is
archived after cutover (see `REPO_RETIREMENT_AND_NEW_REPO_INITIALIZATION.md`).

## 12. Migrating the existing backlog (Robert: 274 open rows)

Do not review them one by one. `python -m factotum import-notion --dry-run` imports every row
(ids preserved, all statuses) and prints the bucket counts; `--apply` writes them with the
classified statuses (`lifecycle.classify_import`):

| Bucket | Rule | Result |
|---|---|---|
| canvas_future | Canvas-sourced, due in the future | keep open; Canvas closes it |
| hard_lapsed | hard, past due > 7 d, < 3 mentions, not calendar-sourced | lapsed |
| hard_last_call | hard, past due > 7 d, ≥ 3 mentions or calendar-sourced | keep; Last call in Review |
| soft_faded | soft, past due > 14 d | faded |
| undated_faded | undated, 1 mention, created ≥ 21 d ago | faded |
| undated_keep | undated, ≥ 2 mentions or created < 21 d ago, or long-term | keep; New in Review |
| waiting_keep | Status Waiting | keep; the first mail sweep flips any that got replies |
| dated_keep | dated, not yet due, or recently past | keep |
| closed | Done/Dropped | history |

Two Notion columns are deliberately not signals here, because plaud-sync wrote them itself: it put
every dated extraction on the calendar, so `Calendar Event` only counts when the task *came from*
the calendar; and its planner rewrote `Why now` / `Score` on every open row, so `last_edited_time`
says nothing about the user. A row mentioned once was mentioned when it was created, and that is
where its fade clock starts (measured 2026-11-01: 274 open rows → 108 open, 71 in Review, versus
137 / 100 with the naive rules).

Rows outside `hard_last_call` and `undated_keep` are marked reviewed on import day, so only
those two buckets surface as New. Then Review sessions at the usual cap until the New bucket is
empty (estimate from the sample: 30 to 40 rows, three evenings).

**The interim window.** Between import day and the phase 3 cutover, plaud-sync keeps creating
task rows in Notion. `import-notion --new-only --apply` pulls only rows whose Task ID is above
the store's highest and never touches existing rows, so Review decisions survive; run it hourly
from cron until phase 3, then delete the cron entry. Any edit made to the Notion Tasks DB by
hand or by the old `plaud-sync-planner` skill after import day is invisible to Factotum: from
that day the skill is read-only (see §15). Top starts empty; the first brief suggests five. Faded and
lapsed rows stay searchable and come back on any new mention, so a wrong fade costs nothing.
The old Command Log relations are not imported; task history lives in `changes` from now on.

## 13. Cost and safety

| Run | Budget | Expected/day |
|---|---|---|
| sync-file | $1.00 | 2–4 recordings → $1–3 |
| planner-morning | $0.75 | $0.75 |
| chat-turn / search / draft | $0.60 | on demand |
| sweeps, UI, reminders, brief | $0 | $0 |

Target under $4/day all in, versus ≈$9/day measured in September. Settings shows the rolling
7-day total from `runs`. Headless allowlists exclude every destructive Mailbird tool; Python
validates every proposal before writing (`batches.SETTABLE`); automatic closes always cite a
link; every batch is undoable; sinks are write-only.

## 14. Build order

0. **Prep (mf-pc, mostly the user):** `docs/SETUP.md` §1–§8.
1. **Core (this commit):** store, scoring, lifecycle, import with dry-run report, Today /
   Timeline / Projects / Review / Settings, tray with undo, grammar commands, accounts table.
   *Gate: the user's first Review session on real rows.*
2. **Push + brief:** scheduler, ntfy reminders and the review push, `/r/<token>` routes,
   morning brief email, `google-auth` and the calendar sweep.
3. **Plaud port:** IMAP trigger, CLI fetch, `sync-file` run with the new bar, Notion notes sink,
   voice verbs incl. reminders and tiers. One week in parallel with plaud-sync, then stop it.
4. **Mail + Canvas + planner:** Mailbird client and sweep, Canvas sweep, `planner-morning`.
   Retire forwarding and the old timers.
5. **Chat:** `factotum mcp`, chat turns, email search, drafts, proposals UI.
6. **Mirror and cleanup:** Notion tasks mirror live, retire the Today page and Command Log,
   archive plaud-sync, point the two Claude Code skills at the API.

## 15. Claude Code skills

`plaud-sync-planner` and `plaud-sync-ops` (installed in Claude Code and claude.ai) keep working
unchanged until import day. From import day the planner skill is **read-only** (the Notion
Tasks DB is a mirror; writes there are lost); task edits go through the Factotum UI. In phase 5
both are replaced by `factotum-planner` and `factotum-ops` under `skills/` here: the planner
skill uses the same `factotum mcp` server Chat uses, extended with write tools that *stage a
batch* rather than write directly, so the tray rule holds from a skill too. For Claude Code on
mf-pc that server is local stdio; for claude.ai it is exposed as a streamable-HTTP route behind
the tunnel with a Cloudflare Access service token and added as a custom connector.

## 16. Open questions

- Hostname: `factotum.nthperson.dev` (recommended) or `plaud-sync.nthperson.dev`.
- Review cap 15 and the 3-day fade warning: right sizes to start?
- Google Calendar: OAuth desktop client (one-time browser consent) or service account?
