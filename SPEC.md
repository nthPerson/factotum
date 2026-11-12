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
Settings tab names the config file so a new user knows where the rest lives. A fresh install has
no `config/factotum.yaml`: it runs on the neutral starter (`config/factotum.starter.yaml`, nothing
switched on) and says so (§9 "Configuration").

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
  pending batch that the user applies in one click, with one undo per batch. Two exceptions are
  logged as such: evidence-based automatic closes, and a ripple's rewrite of a linked task's text (§8.6),
  applied as its own batch with source `ripple` and its cause, shown in History, undone with one tap, and
  undone with its cause while the text is untouched. Everything else a ripple proposes waits on Review.
  A third exception (stage 6, 2026-11-11): a task added by hand exists the moment Enter is pressed, as its
  own `ui` batch (§9), and its Undo drops it; edits to a task still stage.
- **Links between tasks are task state, so they ride in batches** (stage 4a, 2026-11-10): `link`,
  `unlink`, `merge`, `add_note` and a create that carries links are ops of `batches.apply`, each undoable.
  Chat's Create is a batch too, and so is Create on a Review suggestion card (2026-11-11): one `review` batch
  with the create (Source Planner, Slack, Mail or Ripple, keyed by the card), the links the card showed, a mail
  card's thread followed and the card's verdict; its Undo drops the task and brings the card back, and Create
  after that Undo reopens the same task in one batch. A link proposed in a chat turn is never applied at once (it lands in the
  tray), and a merge always waits for a tap: it drops a task, so no direct write path (a connector's
  `apply_changes` included) may make one. Closing never cascades along a link.
- **Outflow must not depend on the user.** Every task has a close signal or an expiry.
- **The evidence rule**: delete or close on good evidence, never on a hunch. Headless runs can
  never send, delete, move or archive mail.
- **Configurable, not forked.** A new user edits two files and the Settings tab; they never
  edit code to change projects, weights, databases, accounts or which integrations are on.
  `config/factotum.example.yaml` is the reference (every key, placeholders only) they copy to
  `config/factotum.yaml`; `config/factotum.starter.yaml` is the fallback until they do.
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
      snoozed_until, last_reviewed_at, calendar_event, notion_page_id, canvas_id,
      outcome,                     -- one line, what came of it (done / drop may carry it; ≤ 300; reopen clears it)
      neighbours_seen_at)          -- stamped when the task is opened or a Review decision is made; "a linked task changed" is measured from it
reminders(id, task_id, fire_at, label, repeat, status, ntfy_seq, token, created_by)
          -- repeat: NULL|daily|weekdays|weekly|monthly; status: pending|sent|snoozed|done|cancelled
links(id, task_id, kind, ref, label, account, created_at, last_seen_at)
          -- kind: plaud_note|notion_page|canvas|mail_thread|mail_message|calendar|slack_message|...
          -- (outside things only: `task` is no longer a kind; migration 1 converted those rows)
relations(id, src, dst, kind, why, source, batch_id, created_at, removed_at, removed_batch_id)
          -- kind: part_of|waits_on|same_as. Direction: `src part_of dst` (dst is the goal), `src waits_on dst`,
          -- `src same_as dst` (src was merged into dst). Soft-deleted (removed_at), never DELETE: History and
          -- undo need the row. A partial unique index allows one ACTIVE row per (src, dst, kind).
mentions(id, task_id, source_kind, source_ref, at)       -- append-only; resets the fade clock
batches(id, created_at, applied_at, undone_at, source, undo_json,
        cause_json)                -- why the batch exists, e.g. {"kind": "ripple", "from": [428], "run": 77}, or NULL
          -- a batch whose every change is skipped leaves no row: `batches.apply` answers `batch_id: null` with the reasons
changes(id, batch_id, task_id, field, old, new,
        note)                      -- an optional one-line reason (≤ 300), on every row the change wrote
          -- a link is two changes rows (field `link`, one per end); `evidence.kind` gains `link_suggestion`
          -- (a suggested pair, `ref` {a, b, kind, why, signal}; judged through the batch that makes the link or merge)
          -- stage 7: `signal` is one of `twin`, `thread`, `text` (the detectors), `planner`, `recording`, `filing`
          -- (a run proposed it) or `import`; `evidence.kind` also gains `filing` (`ref` {field, value}: a project or
          -- an effort the filing run (§8.7) suggests for a task made by hand; `label` "Personal?")
chat_threads / chat_messages / runs / accounts / settings
push_subscriptions(                               -- stage 2: one row per device with the app's notifications on
      id,
      endpoint UNIQUE,                            -- the push service's address for this device
      p256dh,                                     -- the device's public key, base64url as the browser gives it
      auth,                                       -- the device's 16-byte secret, base64url
      label,                                      -- "iPhone", "Chrome on Windows"
      origin,                                     -- the app's origin on that device; a notification's links are built on it
      created_at,
      last_success_at,                            -- the last push the service accepted
      last_failure_at,                            -- the last push it did not
      last_error)                                 -- why, as Settings shows it
```

Stage 7 adds three `settings` rows and one batch op. `state.links.session` is `{day, ids}`: the link
suggestions (evidence ids) offered that day, judged ones included, which `relations.annotate` reads (the daily
cap, §9). `state.links.offered` is `{"<evidence id>": "YYYY-MM-DD"}`, the last day each was offered, which
rotates the cap. `state.filing.pending` is the filing queue, `[{task_id, day}]`, at most 50, kept for the day
a task was made and the next (§8.7). The batch op `follow_thread` (`fields.conversation_id`, optional account
and time) makes an open task follow a mail thread: it adds a `mail_thread` `links` row and sets `last_seen_at`
to the mail's time; its undo deletes the `links` row it added. A connector or skill cannot send it.

The server's push key pair is not a table: it is the file `push-keys.json` beside the database (the
directory of the file the connection has open), mode 0600, never in git (§10, §13).

Task ids continue the Notion numbering (next id after import: 395), so `Task-<n>` stays the
code the user types and says.

**Migrations.** The schema version is `PRAGMA user_version`; `db.VERSIONED` lists the migrations in
order and `db.migrate` runs those above it. A migration that changes data declares `needs_backup(conn)`
and gets a copy made with the SQLite backup API in `data/backups/` (next to the database,
`<stem>-<UTC stamp>-before-v<N>.db`) before it runs. Migration 1 turns the importer's old `links` rows of
kind `task` into `same_as` rows or merge suggestions and deletes them.

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

**Links** (stage 4a; rules in `factotum/relations.py`, enforced again when a batch applies). Kinds:
*part of* (a step and its goal), *waits on*, *same as* (made only by merge). Rules: no link to itself; both
tasks exist; a step has one goal, a step cannot have steps, a goal cannot be a step; a task cannot wait on
its own goal; a task waits only on an open task and a `waits_on` loop is refused (checked again at apply,
so two staged links that make a loop together skip the second); at most 20 links of a kind per task.
A reopened duplicate is a task of its own again: `reopen` removes its `same_as` in the same batch (undo
restores it unless the row was merged elsewhere since), the redirect (`relations.survivor`) hops only from
a closed row, and a merge refuses a target a merge dropped (`relations.merge_target_problem`, shared with
the MCP validator). A merge gives the survivor the duplicate's due date and its sent-mail watch
(`close_on_sent_*`) only when it has none of its own (a watch not carried is said in `skipped`; the mail sweep
reads watches on open tasks only, so one left on the dropped duplicate would never fire); the duplicate's
`calendar_event` field stays with it (the event itself is a `calendar` link, which moves).
**Closing never cascades**: closing a goal does not close its steps, closing the last step does not close
the goal, closing a task does not close what waits on it; the effect is on Review and on scores.
*Scoring* (`scoring.links`, `enabled` false is exactly the old arithmetic): a task that waits on an open
task is capped at `waiting_cap` unless its own hard due date is within `waiting_exempt_days` or past; a
task that frees a hard-dated task, or is a step of a hard-dated goal, is scored as if due then (never
written to `due`); freeing others earns `frees_bonus` each up to `frees_bonus_cap`. *Fading*: a waiting
task does not fade, and its clock starts when it is freed; a step's activity (a mention, a close) keeps its
goal alive. All of it reads `task["rel"]`, derived once by `relations.annotate`.
**Review queue** (`lifecycle.build_review_queue`), ordered: last call · thread updated · mentioned in
Slack · planner · freed (a task it waited on is closed or done and it waits on nothing open) · waited-on
task gone (closed without being done) · all steps done · a linked task changed · link suggested · back from
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
has no push), a thread in the service next to the scheduler. A mail counts only when Gmail's own
`Authentication-Results` header (authserv-id `sources.plaud.auth_server`) shows DKIM or DMARC pass
for the sender's domain and the parsed `From` equals `sources.plaud.sender`; the subject only
locates the recording (`plaud search`, same title within a day of the mail) and never enters the
prompt. Then `plaud summary|transcript <id> -o` write the two files under `data/runs/<id>/`, one
Claude run (§8.1) reads them, and Python writes the note page, tasks, mentions, reminders and
calendar events. The queue is a UID watermark per account, not the unread flag: another reader
(plaud-sync, a phone) marks a mail read within seconds, so every Plaud mail above the watermark is
examined once, read or not, handled uids go to `trigger_seen`, and a first start begins at the top
of the mailbox so nothing old is replayed. One automatic retry two minutes later; a failure alerts over ntfy (else the brief's
mailbox) and writes nothing. `python -m factotum sync-file <id> [--dry-run]` runs the same thing
by hand. No large payload ever passes through a tool result, so the 40 KB MCP spill is gone.

*A recording's links (stage 7, design §10.2).* The run's JSON may carry `links`. A `part_of` or `waits_on`
the speaker stated, with the spoken sentence as `evidence`, is written in the recording's batch (source
`plaud`) as a `link` change whose note is "Said in the recording: “…”"; so undoing the recording undoes it.
Anything inferred, and any `same_as` (a merge drops a task, so it always waits for a tap), is a
`link_suggestion` card with signal `recording`. Both ends must be a task the run created (`tasks[i]`), a row
the prompt listed, or, for the goal (`to`) only, a task finished in the last 30 days (the prompt's
reference-only list: done or dropped, not merged away, at most 25). A `waits_on` a finished task is refused
("a finished task holds nothing up"). A re-synced recording never adds a stated link for a pair that has
any `relations` row, active or removed, so what the owner removed does not come back; its inferred links are
cards, and `suggestions.why_not` keeps a dismissed pair from returning.

**Mailbird.** Accounts and roles live in the `accounts` table, managed in Settings → Email
accounts (add, edit, remove, enable). Allow/deny globs in config. Sweep every `sweep_minutes`
(`factotum/mail_sweep.py`, Python only, read-only client `factotum/mailbird.py` that refuses any
tool outside its read list). Per monitored account it walks Inbox and Sent above a per-account
time watermark and writes rows to `evidence`: a NEW message (arrived since the last pass, not the
owner's own) from the ADDRESS a task waits on clears `waiting_on`; a display name carrying every
word of the name is only a hint on the task's Review card (names are forgeable and first names
common: no flip, no link, and the mail still goes to the other rules). A message the account itself
sent to a task's `counterpart` after the task was created closes the task by itself in ONE case:
the task is a reply ("Reply to …", "Respond …", "Answer …", "Get back to …") and the message is in
the thread the task already follows (closed_by `mail-evidence`). Every other mail to that address
is `sent_to_counterpart` evidence carrying what was written (400 characters), for the planner to
judge: until 2026-11-05 the rule was "any mail to the address closes any send / email / write /
reply task", which one "Coffee Thursday?" to a person three tasks are owed to would have turned
into three closed tasks (it never happened only because the sweep saw no senders); a task under a watch
(`close_on_sent_since` and `close_on_sent_to`, set by the `watch_sent` op: "close this when I have
sent the mail") closes when a message to the watched address is sent after the watch began,
whatever it is called, and the watch ends with it (evidence `sent_watched`, judged in the same
batch; a reopened task is not closed again; a watched task is closed by its watch only). The Inbox
and Sent folders keep separate watermarks and Sent re-reads a ten-minute overlap, because a sent
message can reach Mailbird after a newer Inbox one was swept; a linked thread
(`links.kind = mail_thread`) that moved is flagged; `.ics` invitations and confirmation-shaped
subjects are candidates with a 500-character excerpt. Review shows the flags as "Thread updated";
the planner judges the candidates. Bodies are fetched for candidates only and treated as data.
Senders and recipients are read in Mailbird's own shape, `{email, displayName}` (the sweep read
`{address, name}` until 2026-11-05 and so saw nobody on any real message: none of the rules above
that depend on who wrote or received a mail had ever fired; the test double now has the real shape).

**Tasks suggested from mail (2026-11-05, §8.5).** With `sources.mail.suggest.enabled`, Inbox mail
that none of the rules above claims is kept as `mail_new` evidence for the triage run: the newest
message of a conversation, with a sender line (`from Name <addr> · to you | to you and N others |
you are not in To · unread`) and up to 1,500 characters of the body as words (link text kept, a long
bare URL cut to its host, invisible padding removed; the patterns are bounded and the read capped,
because strangers write this text). Never kept: the owner's own mail (every address in `accounts`,
`owner.emails` and the SMTP user, so the brief is not a candidate), a thread an open task already
follows, a sender matching `sources.mail.suggest.skip_senders`, and whatever `allow` / `deny`
excludes. A later message in the same conversation is a new candidate. The first pass over a
mailbox takes only mail that is still unread (of the last `lookback_days` for a mailbox never swept,
of the last `backfill_days` (3) for one swept before suggestions were on); after that, every new
arrival, read or not (`only_unread: true` narrows it). At most `max_per_sweep` (20) per pass: when
the cap leaves mail behind, the Inbox watermark does not move, so the rest is met on the next pass
(what was already kept is skipped by its `(conversation, time)`). A body Mailbird cannot hand over
is still a candidate, judged by subject and sender. The message is always the conversation's last
one, fetched by its own id (a row's `lastMessageId` can point elsewhere), and an invitation's
attachment is found by `fileName` (Mailbird's key; `name` was never there). Every Mailbird call
first commits what the sweep has written, so the write lock is never held across the network; the
price is that evidence can be committed and the sweep die before its batch is applied, in which
case the planner finds that evidence unjudged and proposes the change.

*Links from a mail suggestion (stage 7, design §10.2).* A suggestion may carry `link` (`{kind, task_id,
why}` naming an open row the run was shown); it is stored on the card and Create writes the
link in the create's own `review` batch (one Undo takes back both), so the card says "Will be a step of Task-N …" before the tap.
The run may also return `about`: a mail that belongs to an open task the run was shown becomes a card on
that task, "This mail is about Task-N. Follow its thread?", whose change is `follow_thread` (§4). Both are
staged only; at most 8 `about` cards per run.

**Canvas.** `/api/v1/planner/items` with submission state; course map, effort by type and skip
patterns in config. Auto-close on submitted/graded/excused/complete (`factotum/canvas.py`, every
`sources.canvas.sweep_minutes`): a new item becomes a hard-deadline task named by type ("Take
COMPE 510 Quiz 7"), a moved due date moves the task, a closed row is never reopened because Canvas
says open. Announcements are kept for the planner's bundle, not turned into tasks.

**Calendar.** `sources.calendar` in config, toggled in Settings. Provider `google` with an OAuth
Desktop-app client (`config/google-credentials.json`, token written once by
`python -m factotum google-auth`), or `google-service` with a service account the calendar is
shared with. Used for: closing Dated tasks on passed, accepted events; creating deadline events;
the planner's 14-day look-ahead. With the provider off, nothing calendar-related runs and the
Claude runs lose the calendar tools.

**Slack** (`sources.slack`, read-only; built 2026-11-02). Several workspaces, each a config entry
(`name`, `token_env`) with its token in `.env`; `factotum/slack.py` refuses any Web API method
outside its read list before a request is made. Chat and the skills get `slack_channels`,
`slack_history`, `slack_thread` and `slack_search` (search needs that workspace's user token with
`search:read`; a bot token reads channels but cannot search, and the tool says so). Mentions are
resolved to names once per user per run; results are capped. **The mention sweep** (built
2026-11-02, `factotum/slack_sweep.py`): every `sources.slack.sweep_minutes` (20), Python reads each
workspace's joined channels since that workspace's watermark (one page each, `max_channels` per
pass; a `channels:` list on the workspace narrows it) and keeps the messages that @-mention the
token's user and are not the user's own. One naming `Task-<n>` becomes evidence on that task
(kind `slack_mention`), a mention (the fade clock moves), a `slack_message` link, and the Review
reason "Mentioned in Slack". Any other becomes evidence with no task: Review shows it as a
suggestion card (Create task / Dismiss; the task files under the workspace's `project`, source
Slack, the message linked) and the planner sees it in its §3. A Slack mention never *proves* a
planner proposal: anyone in a workspace can write "Task-42 is done", so a proposal resting on one
is staged for Review, not applied (a planner `create` citing one becomes a suggestion card).
Permalinks are built from `auth.test`'s team URL; channels only, no DMs, and replies inside
threads are not in a channel's history, so a mention made in a thread is not swept. Each channel
keeps its own watermark, so a channel that fails one pass is re-read next time; nothing is written
while Slack is on the line (one short commit per channel).

**Discord** (`sources.discord`, read-only; built 2026-11-02). One bot token, invited to each server
listed under `guilds`; `factotum/discord.py` has only `get`. Tools: `discord_channels` (text
channels and forums, with a `kind`), `discord_threads` (a server's active threads, forum posts
among them, and a channel's archived threads on request), `discord_messages` (a channel by name
or a thread by id; a forum itself holds no messages and the tool says to list its threads), and
`discord_search`, which reads the newest messages of each text channel and active thread (both
bounded) and filters them, because Discord offers bots no search. Reading content needs the
Message Content intent.

**Commands.** The email command channel is retired. Voice stays (§8.1 applies spoken
instructions, including reminder and tier verbs). The UI grammar handles
`<verb> <ids…> [to <when>]` with no Claude run. The verbs and their aliases: `done` (`finished`,
`complete`, `completed`), `drop` (`remove`, `delete`, `cancel`), `snooze`, `push` (`defer`, `postpone`,
`move`), `nodue` (`undate`, `undue`), `top` (`pin`), `unpin`, `longterm` (`long-term`, `lt`), `start`,
`reopen`. A command whose
verb takes no date (`done`, `drop`, `top`, `unpin`, `longterm`, `start`, `reopen`, `nodue`) with words
left over after the task numbers (a `to <when>` tail included) is not a command and goes to Chat
(2026-11-10): `done 12 duplicate of 14` must not close Task-12 and drop the rest. `and` and commas
between numbers are separators (`done 12 and 14` is a command). `snooze` with words it cannot read
as a date is not a command either; it no longer silently means "tomorrow".

**The add line** (stage 6, 2026-11-11, design §12.7). `add` / `new` / a leading `+` (a bare one too) makes
a task; it is never a tray command (`commands.parse` returns None for it) and is read by
`commands.parse_add`. The New task line and the phone sheet send no verb; the Today command box sends
`verb: true` and the verb is stripped. Parts: `#project` (compared with each configured project name
lowercased and stripped of everything but letters and digits; an exact match wins, else a unique prefix of
at least 2 characters, else refused as `no project matches #x` or `#x could be A or B`; `#` with only digits
is not a project), `~effort` and a lone `!` (hard) are read anywhere after the verb; a date phrase (today,
tomorrow, weekday names, `next <weekday>`, `next week`, `<month> <day>`, ISO; `by` / `due` go with it),
`after N` (waits on Task-N) and `under N` (a step of Task-N) are read only in the trailing run, so "Plan
monday meeting fri" keeps "monday" in the name. A kind named twice: the last wins and the earlier stays in
the name. A refused part (an unknown project, an impossible date, a task that does not exist or whose link
the rules refuse) stays in the name and is shown with its reason; Enter still adds the task, and the toast
repeats the refusal. `GET /api/add/preview?text=&verb=` answers with the parse and the checked tasks
(selects by primary key, no write). `POST /api/add` takes `{text, verb?}` or `{fields}` plus a client `key`:
one `batches.apply` with source `ui` (the task's Source is `Manual`), `source_key` `add:<key>`, under a lock,
so a second Enter or a retry answers `already: true` with the first task. `python -m factotum add TEXT
[--dry-run]` does the same from the terminal. Search: `GET /api/search?q=&limit=` over every status (a number
or `Task-12` / `#12` matches that id first, then ids starting with it; words must all appear in the name or
project; open first, then closed newest first; 12, at most 30); `python -m factotum find Q`.

## 7. Outputs (each with an `enabled` flag)

- **Notion notes** (`sinks.notion_notes`): one page per recording in the Plaud notes database
  with Summary, Mind Map, Transcript and "Mentioned follow-ups". Database id, data source id and
  the `Source Link` prefix are config.
- **Notion tasks mirror** (`sinks.notion_tasks_mirror`; built 2026-11-02): one-way upsert of every
  task row so relations and dashboards keep showing history. Never read back. The Notion property
  names are config (`properties:`); the property *types* come from the database's own schema,
  fetched once per pass, so a retyped or missing column is skipped rather than guessed. A
  scheduler pass every `sweep_minutes` pushes rows with no page yet (creates, in id order, so
  Notion's own Task ID counter keeps pace with Factotum's ids while nothing else creates rows
  there) and rows edited since their last push (`tasks.mirrored_at`, stamped without touching
  `edited_at`); `factotum mirror --all` is the first full pass. Read-only Notion columns
  (unique_id, created time, formulas) are never written. Default on for Robert; the Settings tab
  shows the toggle, the interval and the config file name. With it off, Factotum is a standalone
  SQLite task list.
- **Morning brief** (email at `notify.morning_brief.time`): verdict (phase 4), Top, overdue hard
  deadlines, due today, due tomorrow, reminders firing today, hard deadlines this week, calendar
  for today and tomorrow when the calendar is on, closed on evidence since yesterday (with who
  closed it), waiting on, and how many items Review holds. Sent over SMTP from `SMTP_USER`
  (falling back to the IMAP account) to `notify.morning_brief.to`. No evening email.
- **ntfy** (§10).

## 8. Claude runs

All runs: `claude -p` as the service user, `--output-format stream-json`, explicit
`--allowedTools` built from which sources are enabled, a budget guard in the runner, JSON-only
final message, logged to `runs`. Model and budget per run kind are config.

### 8.1 `sync-file` (one per recording)
Inputs: the recording's title and time from the Plaud CLI, the two files it wrote, the store's
slice (every open row plus the last 60 days' faded and lapsed ones, ≤ 150, inline: voice
instructions reach any project), the taxonomy from config (`taxonomy.projects[].description`,
`tie_breakers`, `note_types`, `note_tags`). Tools: Read, nothing else; no MCP server is loaded.
Python dedups the note page on Source Link and the calendar event on title and day, so the run
cannot stall on a permission prompt (the 2026-07-22 class of failure). The output is untrusted: a
`source_key` outside `plaud:<id>:` is replaced, and an update, instruction or reminder aimed at a
row the prompt did not list is reported under `unresolved`, not applied. Every change to an
existing row rides in one batch (source `plaud`, closed_by `voice`), so the Log tab undoes a
recording in one tap; creations are upserts by `source_key`. Returns:
```json
{ "note": { "action": "create|update", "properties": {…}, "body_md": "…", "followups": ["…"] },
  "tasks": [ { "op": "create|update", "id": null, "name": "…", "project": "…", "due": "…", "hard": true,
               "effort": "S", "counterpart": "max@…", "context": "…", "source_key": "plaud:<id>:1" } ],
  "instructions": [ { "op": "done|defer|drop|waiting|priority|start|top|longterm|remind|correct",
                      "task_id": 364, "fields": {…}, "evidence": "spoken sentence" } ],
  "reminders": [ { "task_id": null, "task_ref": "tasks[0]", "fire_at": "2026-11-02T14:00", "label": "Get started" } ],
  "links": [ { "kind": "part_of|waits_on|same_as", "from": "tasks[0]" | 412, "to": 388, "stated": true,
               "evidence": "spoken sentence", "why": "…" } ],
  "calendar": [ … ], "unresolved": [ "…" ] }
```
`links` (stage 7): at most `MAX_LINKS` are read, the rest reported under `unresolved`. Python enforces that `from`
is a task this run created or an open row the prompt listed, `to` also a recently finished one (the prompt's
closed list, §6 Plaud), `kind` is one of three, and that a stated link (`stated: true` with a sentence)
is a `part_of` / `waits_on` that no relation already holds and `relations.link_problem` accepts; every
refusal is a line in `unresolved`. Only that case is written; the rest are `link_suggestion`s (signal
`recording`) through `suggestions.why_not`.

### 8.2 `planner-morning`
Inputs: a bundle Python built (`factotum/planner.py`): scored open rows (Top first, then by
score, ≤ 60), the mail sweep's unjudged `evidence` rows with their excerpts, Canvas announcements
since yesterday, reminders due today, the calendar for 7 days, the Waiting rows and the Review
count. Tools: none; the excerpts are in the bundle, so the run cannot stall and costs one turn.
Returns the verdict, `why_now` for ≤ 15 listed rows, proposals and dismissals. Python applies a
proposal only when it cites an `evidence` row that exists, is unjudged and is about the same task
(an automatic close always leaves a `mail_thread` link); an uncited proposal on a listed row becomes
a "Planner suggests" item in Review (kind `proposal` in `evidence`); everything else is rejected
and listed in the run row. `why_now` and the applied proposals ride in one batch (source
`planner`). The brief stays Python-built (phase 2) and leads with the verdict, shows `why_now`
beside the rows that have one, and lists what the planner did and what it left for Review. If the
run fails, the plain brief goes out with the reason. `python -m factotum planner [--dry-run]
[--send]` runs it by hand. The planner may run more than once a day (the Today tab's Refresh, §8.4),
so the prompt lists the suggestions already waiting in Review ("do not propose these again"), Python
refuses to stage a card that is already waiting (same op and task and fields; for a create, the same
name), and a created row's `source_key` is the evidence it came from (`planner:e<id>`), never the
date and a counter: a second run cannot overwrite the task the first one made. The prompt carries
the time of day and what was closed today, so a run at 15:00 does not write a morning's verdict.

*Link proposals (stage 7, design §10.2).* The bundle lists link candidates and the prompt allows the ops
`link`, `unlink` and `merge` (`planner.LINK_OPS`) on listed rows. Python never applies one, whatever it cites:
a `link` or `merge` (`same_as`, the proposed row goes, `into` stays) becomes a `link_suggestion` with signal
`planner` through `suggestions.why_not` / `suggest_pair`; an `unlink` becomes a `proposal` card. Both ends must
be listed rows, the evidence it cites must be about one of those two tasks, and at most `LINK_CAP` (5) such
proposals are staged per run (the prompt asks for at most 3); the rest are rejected with a reason in the run row.
Link suggestion and `filing` evidence are not the planner's: it never judges them.

### 8.4 The Today summary (`factotum/summary.py`, added 2026-11-04)
The planner's verdict is also the summary at the top of the Today tab, kept in the settings table
(`state.summary`: the text, when it was written, by what, and the rows it is about). Those are the
rows the model names (`verdict_tasks`, ≤ 8, only rows the bundle listed), the task codes in its
text, and the urgent rows (overdue-hard, due today or tomorrow, pinned), which a summary of today
is about whether it lists them or not. Each is remembered as the run READ it (the bundle's
snapshot), not as it is when the text comes back, so a change made while Claude was writing shows
as stale at once; rows the run itself changed or created are remembered as it left them. **Python decides when it is out of
date, never a model**, and only for reasons a reader would care about: a row it is about was closed,
re-dated, put off, handed to someone or changed hardness; a row it could not know became urgent
(overdue and hard, due today or tomorrow, pinned to Top); or the day changed (one reason, "written
yesterday", not a line per row). An edit to any other row leaves it fresh; an undone change is no
change. `GET /api/summary` returns the summary with its reasons, the refresh state, and whether
this instance can refresh.

Three things rewrite it. (1) The morning planner, as before. (2) **Automatically**, when it is stale
and `claude.summary.auto` is on: one light run (`prompts/today-summary.md`, kind `today_summary`, no
tools) over the same bundle plus what was closed today, returning `{summary, task_ids}`. It writes
the summary and nothing else: no task, evidence or proposal changes, so a rewrite never surprises.
It runs at once when the last summary is older than `claude.summary.auto_minutes` (10), otherwise
when it is; yesterday's summary waits for the morning brief time, where the planner normally writes
it first. (3) **Refresh** on the Today tab: every enabled sweep now (mail, Canvas, calendar, Slack;
one that fails does not stop the rest, and each interval restarts from here), then the whole planner
on what they found, or the light run when the planner is off; no brief is mailed. The request is a
settings row (`state.refresh.requested`) that the scheduler thread picks up, woken at once by
`POST /api/summary/refresh`; the API never sweeps itself, so there is still one sweeper per
database, and an instance started with `--no-scheduler` answers 409. `state.refresh.last` is what
the UI reports when it is over; a request or a run older than 20 minutes was lost to a restart: it
reads as idle and the scheduler drops it instead of running it late. A failed automatic rewrite is
retried after an hour, like every job.

### 8.5 `mail-triage`: tasks suggested from mail (`factotum/mail_triage.py`, added 2026-11-05)
One light run, no tools, over a batch of at most 15 `mail_new` candidates (§6), with the taxonomy,
the open rows (to see what is already tracked) and the suggestions already waiting. It returns
`{suggest: [{evidence_id, name, project, due, hard, effort, context, reply_expected, reason}],
dismiss: [...]}`. The bar, in the prompt: something a reasonable person would put on a to-do list
(a request to the owner, something to do by a date, a meeting with a time); when in doubt, set
aside, because a card that should not exist costs more than a mail the owner will read anyway.

Python decides what that becomes. A suggestion is a `proposal` card in Review ("From your mail":
what Create would make, the model's context and reason, who wrote and when, Show the message in
Mailbird) and never a task, a change or a link. Its fields go through the same cleaner as every
create; its `counterpart` is the sender address Mailbird reported, set only when the model says a
reply finishes the task, never an address the model wrote; a name that an open task or a waiting
card already has is set aside; at most 8 per run, and a good suggestion past that stays unjudged
for the next run. Every other mail in the batch is judged by the end of the run (`staged` or
`dismissed`, outside a batch: these are raw mail, not task state, and a day's worth would bury the
Log), so nothing is asked about twice. A reply that is not `{suggest: [...], dismiss: [...]}` is a
failed run, whatever JSON it holds; a failed run judges nothing and the batch waits, but a batch
that fails twice in a row is set aside unread (it would otherwise cost a run an hour for a week and
hold newer mail behind it; the mail is still in the mailbox). The run is started with no tools at
all (`tools=""`), since everything it reads was written by strangers. A candidate nobody triaged
within a week is set aside unread. **Create** makes an
open task with source `Mail`, the mail's account, and a `mail_thread` link to the conversation, so
from then on the sweep follows that thread (seen up to the message the card was made from), and a
"Reply to …" task closes itself when the owner answers in it (§6); for any other name the planner
reads what was sent. The planner never sees `mail_new`.

Stage 7 adds two parts to the reply: `link` on a suggestion (`{kind, task_id, why}`, an open row the run was
shown; kept on the card, written by Create) and `about: [{evidence_id, task_id, why}]` (the mail is about an
open task the run was shown: a `proposal` card on that task whose change is `follow_thread`, at most 8 a run).
A task id the run was not shown is dropped. Neither writes a link or follows a thread without a tap.

The scheduler runs it only when candidates exist and no more often than
`sources.mail.suggest.minutes` (30); a Refresh (§8.4) runs it between the sweeps and the planner.
A run is a `mail-triage` row with its cost and a line such as "15 mail: 1 suggested (…), 14 set
aside"; prompt and result under `data/runs/triage/<date>/`. Measured on real mail 2026-11-05: 15
messages, $0.14, 7 s.

### 8.6 `ripple`: bringing linked tasks up to date (`factotum/ripple.py`, added 2026-11-11)
When a task that others are linked to is closed, materially changed or merged, one light run (no tools)
brings the tasks linked to it up to date. It runs while `claude.ripple.enabled` is on, which it is when the key
is absent (since 2026-11-11, §9 "Configuration"). Where Claude cannot run (no `claude` program, or not signed in)
the group is set aside, the scheduler backs off an hour, one failed row a day goes to the Log and Settings → Claude
says ripples and filing wait (`runner.unavailable`, `state.claude.unavailable`).

**What queues.** `batches.apply` calls `ripple.enqueue` for every source but `ripple` (a ripple never
triggers a ripple), reading the batch's own `changes`: `status` open → done / dropped ("closed", unless the
same batch merged it); `name`, `due`, `hard`, `outcome` (not the one a reopen clears); `context` when its normalised text differs
("text"); a `same_as` link added with the survivor at its end ("merge"). Only for a task with at least one
active link afterwards. Fade, lapse, reopen, `why_now`, effort and the rest never queue. The queue is the
table `ripple_queue` (one row per changed task: `queued_at`, `now`, `last_batch_id`, the batches and what
changed), not a setting: the API's threads add to it while the scheduler's thread runs a group that can
take minutes, and a run deletes a row only `WHERE task_id = ? AND last_batch_id = ?`, so a change made
during a run stays queued and ripples again. Undoing the only change that queued a task takes its row back.

**Quiet period and groups.** Queued tasks are grouped when one is a neighbour of another or they share a
neighbour (one link away, or steps of one goal). A group runs when any row has `now` (an outcome was just
given) or its newest row is `claude.ripple.delay_seconds` old (60), so a Done followed by the outcome line
is one run. One group per scheduler tick, at most 5 changed tasks per run (the rest stay queued). A row
older than 24 h is set aside unrun; with ripples off the scheduler empties the queue every tick.

**The input.** Per changed task: what changed as before → now, the outcome, any task merged into it. Per
linked task (open only, at most 12: direct links first, then other steps of the same goal): its link, due
date, who it waits on, name and text, each text cut to 1500 characters (a cut text is marked so in the
prompt). Every value is one JSON string in the prompt (stranger text, §2), with `<` and `>` and the
characters a reader may take for a line break as JSON escapes; never HTML entities, so a rewrite that keeps
the owner's words writes back exactly what the owner wrote.

**The reply** is `{"ripples": [{"task_id", "effect": "none" | "text" | "propose", "context", "why",
"proposals": [{"op", "fields", "reason"}]}]}`, one entry per linked task. A wrong shape anywhere (not that
object, a `task_id` that is not a linked task of the run, a repeated one, an unknown effect, `text`
without a string `context`) fails the whole run and nothing is applied.

**What applies, and the guard.** Only a text rewrite (`context`, at most 2000 characters), as one batch
with source `ripple`, through `batches.apply` with `expect` set to the text the run was shown, so a task
edited by hand meanwhile is skipped with the usual reason; a task closed or merged meanwhile is left alone.
A rewrite that loses something (empty, too long, or it drops a web address or a `Task-N` the old text had)
is refused and becomes an `add_note` ("Ripple from Task-N: why"), dated by the server, in the same batch;
so does any rewrite of a text the run was shown cut (over 1500 characters: a rewrite would replace the whole
text with what was built from its first part), History's line saying "the text is too long to rewrite safely,
so a note was added";
when the reason names an address the old text lacked, the note says fixed words instead, and History's line
for the change has that address replaced by "[address left out]". A rewrite that loses nothing but adds an
email or web address the old text did not have is not applied on its own (a ripple never brings an address
to a task, design §8.6) and is not a note either: it waits on Review as a card ("Ripple suggests", with the
text before and after); Accept stages it with `expect` set to the text the run was shown, and when the task's
text has changed since, the card says so and offers only Dismiss. Only when no card can be staged (the same
one already waits, or the run's 6 cards are used) does such a rewrite become a note instead. The prompt also tells the model
to keep the sentences the owner wrote, add where things stand after them, never remove or soften a
requirement the owner wrote, and say when the news conflicts with one.

**What stages, and what is refused.** Every other proposal is `proposal` evidence with `account = 'ripple'`
(Review reason `ripple`, "Ripple suggests"): `done`, `drop`, `set` (name, due, hard, effort, project),
`add_reminder`, `link` / `unlink` (part_of, waits_on, between tasks of the run), `create` (a card, no task).
At most 6 per run, deduplicated against waiting cards, never applied and never proof of anything. Refused,
and named in the log line: `counterpart` or `waiting_on` (a ripple never copies an address), `context`,
`merge`, anything else.

**The cause and undo.** A ripple batch's `cause_json` is `{"kind": "ripple", "from": [ids], "run": <runs.id>,
"batches": [causing batch ids], "what": {id: [what]}, "noted": [ids whose rewrite became a note]}`. Undoing a
causing batch also undoes each of its ripple batches when every field the ripple wrote is still as it wrote
it; otherwise the ripple batch stays whole and its cause gains `cause_undone_at`, which History and Review
show. The owner can still undo it alone. A ripple undone with one of its causes while its other causes
still hold queues those causes again in the same transaction, so the next run brings the linked tasks up to
date for what is still true (closing Task-1 and Task-2 rippled once; undoing Task-1's close must not leave
Task-2's close unsaid).

**Failure.** A failed run (no JSON, a wrong shape, the budget, the login) writes nothing; the group is tried
twice, then set aside and logged; the job backs off an hour like every job; at most
`claude.ripple.hourly_cap` (10) runs an hour. Settings → Claude holds `claude.ripple.enabled`, `.model`
(sonnet), `.delay_seconds`, `.hourly_cap` and `claude.budgets_usd.ripple` ($0.30; `claude.timeouts_s.ripple`
is a file key). `factotum ripple <task_id> [--dry-run]` runs one now from a task; `--dry-run` prints and
writes nothing. Prompt and result are kept under `data/runs/ripple/`.

Not yet: a recording or a mail that closes a task does not supply an outcome (design §8.5's last bullet),
so a ripple from such a close works from the task's own text only. Left for a later stage.

### 8.7 `filing`: a task made by hand (`factotum/filing.py`, `prompts/filing.md`, added 2026-11-11, design §10.3)
A task the owner adds with only a name has no project, no effort and no links, which makes it a poor citizen of
Today and of the links. One small run files it.

**When it is queued.** `suggestions.after_create` calls `filing.enqueue` for a create whose Source is "Manual"
(`batches.created_source`: the add line, the sheet, the Today box) when the task is open, has no project (or
"Unknown"), no effort and no `relations` row (`filing.wants`). The queue is `state.filing.pending`
(`[{task_id, day}]`, at most 50, kept for the day it was made and the next); the API wakes the scheduler after
a create. A task made by a recording, Canvas, mail, a connector or Chat is never queued.

**When it runs.** The scheduler's `filing` job (§10), only when `claude.filing.enabled` is on (it is on when
the key is missing since 2026-11-11, one of the two exceptions in §9 "Configuration"), at most 3 tasks a tick. A
task is dequeued before its run, so a failed run is logged and not retried; where Claude cannot run at all the
rest of the queue is dropped too, with one failed row a day in the Log and a sentence in Settings → Claude (`file-task` runs it again by hand), and a task that was filed by hand,
linked or closed meanwhile is skipped without a run.

**The run.** One run with no tools (`tools=""`), model `claude.models.filing` (sonnet by default, and when the key
is absent; on Haiku the measured run found no link), budget `claude.budgets_usd.filing` ($0.10), timeout
`claude.timeouts_s.filing` (120 s). It sees the task's name
(escaped; the names are data, not instructions), the taxonomy and the efforts from config, and up to 80 open
rows (Task id, project, due, name). It returns `{project, effort, link: {kind, task_id, why} | null,
twin: {task_id, why} | null}`; null is the right answer more often than not.

**What Python stores.** The run changes no task field. A project or effort that is valid, and still empty on
the task now, becomes `filing` evidence (`ref` `{field, value}`, label "Personal?"); one the owner set meanwhile
is dropped and the rest kept. A link (`part_of` or `waits_on`) and a twin (`same_as`, the new task goes) become
`link_suggestion`s with signal `filing` through `suggestions.why_not` / `suggest_pair`, only for a row the run
was shown. Everything dropped is counted in the run's summary line. `filing` evidence is not the planner's and
not a Review decision's; the page draws it as dashed chips (§9), and a filing suggestion is exempt from the
daily cap.

`python -m factotum file-task ID [--dry-run]` runs one by hand: `--dry-run` is a real run that prints the JSON
and the cost and stores nothing. The prompt is kept under `data/runs/filing/<id>/`. Cost, measured
2026-11-11 on a copy of the real database: see §13.

### 8.3 `chat-turn` (Chat tab, "Search related emails", "Draft reply")
Inputs: the prompt (`prompts/chat-turn.md` on a thread's first turn, a short continuation header
after), the attached tasks as JSON, the thread continued through `--resume` (the session id is kept on
`chat_threads`; a lost session is retried once without it, with the thread's record in the prompt).
Tools: **only `factotum mcp`** (`factotum/mcp_server.py`, started by the runner per turn with
`--tools ""` so no built-in tool exists): `tasks_query`, `task_get`, `reminders_list` (a `query_only`
connection), `mail_search`, `mail_thread`, `mail_message`, `mail_draft_reply`, `mail_draft_new` (through
`mailbird.Client`: reads plus drafts, never send/trash/move), `calendar_events`, `plaud_search`,
`plaud_summary`. Mailbird's own MCP is not loaded into the run: Claude Code's client would reject the
self-signed loopback certificate, the bearer token stays out of the run's config, and Python's
whitelist holds by construction. Every result is trimmed (previews, bodies capped at 6 KB, 30 KB per
result) so nothing nears the spill. The run is streamed (`stream-json`): the UI shows "Searching mail ·
from:max" as it happens. The final message is one JSON object:
```json
{ "reply_md": "…",
  "proposals": [ { "op": "set|done|drop|pin_top|longterm|add_reminder|link_thread|create",
                   "task_id": 388, "fields": {…}, "label": "Task-388 → due Thu Nov 5",
                   "evidence": [ { "kind": "mail_thread", "ref": "<conversation id>", "label": "Oct 29 15:03 from Max" } ] } ] }
```
Python validates before the UI may stage anything: ops in that set only; a task op names an existing
row; `set` fields pass the voice-note cleaner (settable fields, taxonomy, ISO dates); a reminder
validates like the tray's; a `create` is made by its Create button at once or staged in the tray as
`from_chat` (the server makes the stored proposal; both key it `chat:<message>:<n>`, so the two never make
two tasks); mail evidence counts only for a conversation the run actually fetched. **`link_thread` is applied at once**,
but only for a conversation the run fetched (the server records `seen.jsonl` per run): a link is
metadata the sweep then watches for free, not task state, so a task linked once never goes stale
silently. **Drafts** come from the server's own record (`drafts.jsonl`), never from the model's claim;
they are listed with "Open in Mailbird" (`select_conversation`, or `show_notification` for a new
message). A new draft goes only to a bare address a task names (counterpart, waiting_on) or one of the
user's own accounts — a hostile mail that CCs a stranger cannot make that stranger a recipient; a reply
draft needs a conversation the turn read and is flagged in the UI when it reaches an address no task
names. The UI renders proposals as checkboxes; "Stage selected" puts them in the tray. "Search
related emails" and "Draft reply" are chat turns with a canned prompt (`prompts/chat-search-mail.md`,
`chat-draft-reply.md`) and the task attached; on a Review card the conversation lists what a reply proposes
and "Stage all N with this decision" puts the ticked ones in the tray beside the card's decision (one Apply,
one Undo; see Places below). Every turn is a `runs` row (`chat-turn`)
with its cost; prompt and result live under `data/runs/chat/<thread>-<message>/`.

**Places, and what a turn carries (stage 6, 2026-11-11, design §12.6).** A conversation has a place:
`chat_threads.origin` (`review`, `today`, `task`; NULL for a Chat-tab thread) and `task_id`. A Review card
and an opened task share one thread per task (the newest with that `task_id` and origin `review` or `task`);
the Today box continues the newest `today` thread whose last message is under 12 hours old (`fresh: true`
starts another); `GET /api/chat/place` answers which. `POST /api/chat/turn` resolves the thread under a lock
and answers 409 while that thread has a turn running. `chat.PLACE_TEXT` tells the model where the user is.
A turn's result also carries `read` (what the run read, stored in `chat_messages.read_json`) and `named`
(the tasks its proposals name, with their names), `origin` and `task_id`. A `link` proposal may name, as
`other`, the `ref` of a `create` in the same reply, so an existing task can wait on a new one. Everything a
reply proposes can be staged together: a chat `create` goes to the tray as `{op: "create", from_chat:
{message_id, index}}`; the server replaces its fields with the stored, validated proposal (the page cannot
forge one), keys it `chat:<message>:<index>` like Create (so Create and Stage never make two), gives it
Source `Chat` and marks the proposal applied (`chat.resolve_staged`, `mark_staged`). In the tray its `ref` is
scoped to its message (`m<message>-<index>`), never the model's `s1`, which another message may use too; a
link may name a sibling's ref only inside one batch with that create, or once Create made it. One canned
prompt, `split` (`prompts/chat-split.md`), serves "Split into steps" and "Make them tasks": it turns steps
the text lists into tasks, or drafts two to six, as creates with `ref`s, `part_of` the goal and `waits_on`
only where order is real.

## 9. The UI

One page, phone-friendly, served by the service. Tabs: **Today · Timeline · Projects · Review ·
Chat · Log · Settings**. The mockup is the reference. Rows show only: checkbox, code, name, tier and
fade chips, next reminder, project, due, effort, staged-change chip; the chevron opens details
(score reasons, why now, context, source and account, links, lifecycle, reminders, actions, the
Claude buttons). Top rail with drag. Composer on every tab with attached chips; grammar commands
stage instantly, anything else opens Chat (phase 5): the Chat tab is a thread picker, the messages,
the live status of the run, and under each answer the proposals as checkboxes ("Stage selected"),
"Create task" for a proposed row, and the drafts with "Open in Mailbird". On a desktop a tap on a
row or its checkbox selects it (no modifier); a double-tap opens its details; Esc clears the
selection. On a phone (stage 1, below) a tap opens the task and only the tick box selects. The bulk
bar floats at the bottom of the screen with any selection (it never shifts the list, so the second
tap of a double-tap lands on the same row) and offers Done, Drop, Snooze, a due date to set or
clear, Top and Long-term. Due dates can also be set or cleared from a row's
details and from a Review card, staged beside the decision rather than replacing it; "Remove due
date" sits beside "Change due", `nodue 364` is the command, and a connector sends `due: null`.
Removing a date also clears `hard` and the time flag (an undated row is not a hard deadline), and
the row then follows the undated lifecycle (§5): it leaves Today and can fade unless pinned or
long-term. A Canvas row gets its date back at the next Canvas sweep (Canvas owns it), and a
recording never removes a date: an empty `due` on an update or a re-synced recording means "not
said", and the row keeps its date and hardness (`sync_file.apply_result`). Timeline groups
open tasks by due date and ends with "Recently added", the newest 30 by creation date, so what a
recording or a sweep just added is one scroll away. The Log tab
lists recent batches with every field change (old → new, task named) and an Undo per batch, and
the Claude runs with their cost. Assets are served with `no-cache` and version-stamped URLs so a
deploy reaches the phone without a hard refresh. Tray with one Apply and
one Undo. Review as a card stack. Settings: lifecycle values, inputs and outputs on/off, email
accounts table, ntfy topic, brief time and recipient, with a note naming the config file.
Keyboard: `j k d x s t l e`, Esc deselects; Review `d k s x`.

**Configuration** (stage 3 of the linked-tasks design, 2026-11-10, design §15). A value comes from
the first of: a Settings override (the `settings` table, `SETTINGS_KEYS` only), the user's file,
the code's default (`config.DEFAULTS`, one entry per Settings key, read through `C.value`). The
user's file is `config/factotum.yaml` (or what `FACTOTUM_CONFIG` names); without it Factotum runs
on `config/factotum.starter.yaml` (no name, no addresses, the machine's time zone, three projects of
equal weight, every input, output, notification and scheduled paid Claude run off, ripples and filing on,
connector writes staged),
prints one line to stderr and shows a banner on every tab ("Starter settings in use.") with how to
make it your own; the example is never loaded unless named. Settings shows each control's effective
value, its default filled in, and a change saves that one key (earlier overrides stay until removed
from the table). **Absent means off** for the scheduled paid runs and anything that sends mail: the morning
brief, the planner, the automatic summary, the Notion notes sink and the mail suggestions run only
when their key is explicitly on. Ripples and filing are the two exceptions, on unless switched off
(2026-11-11, the owner's decision), because each runs only after something the owner did (a linked task
closed or changed; a task added by hand with only a name), costs cents (about 3 cents a ripple run, at most
the hourly cap; about 4 cents a filed task; measured 2026-11-11) and writes nothing History cannot undo. Mail accounts are seeded from the user's file once per database
(`state.accounts_seeded`), never from the starter, so a table emptied on purpose stays empty.
`python -m factotum effective [KEY…] [--as-before]` prints each key's value and origin (`settings`,
`file`, `machine`, `default`, `unset`) without writing or printing a secret; `--as-before` uses the
pre-stage-3 defaults (ripples and filing did not exist: off), so a diff of the two is the deploy check (SETUP §18).

**Stage 1 of the linked-tasks design (2026-11-10): the app shell** (design
`docs/superpowers/specs/2026-11-10-linked-tasks-design.md` §13). The page is an installable app: a
manifest (`manifest.webmanifest`, requested with `crossorigin="use-credentials"` so Cloudflare Access
sends the cookie), icons made by `tools/make_icons.py`, and the iPhone touch icon inlined in
`index.html` as a data URI (the iPhone fetches it without the cookie). Addresses are real paths: `/`
is Today, every other tab is `/<tab>`, a task is `/t/<id>`; `/today` and old `/#<tab>` links are
rewritten in place. History: a tab click pushes (nothing if it is the current tab); a jump to a row
on another tab and a prompt sent to Chat push; expanding a row on a desktop changes nothing; on a
desktop `/t/<id>` opens the tab that lists the task (Today, else Timeline) with the row unfolded and
expanded, replacing the entry; on a phone a tapped row pushes `/t/<id>`, and an app opened at
`/t/<id>` puts `/` beneath it (replace, then push) so Back lands on Today, not outside the app.
On a phone (`max-width: 700px`) a tap opens a full-screen task page rendered into `#panel`
(`ui.page`), and the tick box selects; ‹ Back, the phone's Back and Esc close it, the list is redrawn
and its scroll restored. A task that is done or dropped is fetched from `GET /api/tasks/{id}` and
shown read-only ("This task is done." / "This task is dropped."); an unknown id says "Task-N does
not exist." One thing sits at the bottom of a phone screen: the tray, or the short bar while rows
are selected (the tray hides and comes back when the selection is cleared). A redirect, 401 or 403
from `/api` shows "Sign in again" (a restarting server or a dead network never does). A prompt or
command that could not be sent goes back in the box ("Not sent: the server did not answer"), and a
reload the app asks for (Sign in again, Update ready) keeps the command box's text. When the
app comes back to the front the lists are reloaded unless something is typed (`typingNow()`: text
in a box, an open reminder or due-date form, a chat turn running, or focus in a box whose text a
redraw would lose; focus in an empty command, Review or picker box does not count), and
a new build is noticed by `X-Factotum-Build` on every `/api` response against
`<meta name="factotum-build">` (the first 12 hex digits of SHA-1 over the mtimes of `index.html`,
`app.js`, `app.css` and `manifest.webmanifest`): the page reloads itself, or shows "Update ready"
while something is typed or changes are staged in the tray. A `/t/<id>` entry the app put in the
history is marked (`history.state.page`), so a reload or self-update on a phone task page adds no
second `/` beneath it.

Phone layout (2026-11-02): under 700 px a row is title first (code and name on one line, wrapping)
with its chips on a second line and the chevron as a real tap target; the tab strip scrolls
sideways instead of wrapping; the keyboard hint is replaced by a one-line touch hint; buttons are
36 px and inputs 16 px (no iOS zoom); the Top rail collapses to its header; a long Review context
is clamped with "Show all context" so the decision buttons stay near the top; the runs table
scrolls inside the Log. The Chat tab has a thread sidebar beside the messages on wide screens and a
drawer (☰ Threads) on phones, and shows each turn's cost and duration. Review also shows the
planner's suggestions (creates it could not prove) as cards with Create / Dismiss, and offers
"Accept" for a planner proposal on a task's card; a decision judges the card's evidence in the same
batch. A Slack mention naming a task is the Review reason "Mentioned in Slack"; one naming none is
a suggestion card beside the planner's (Create task files it under the workspace's project with the
message linked). A batch a skill staged (§15) appears as a banner above every tab with Apply /
Dismiss. The tray's summary opens a checklist of the staged changes with Apply selected / Discard
selected beside Apply all / Discard all (a wrong decision deep into a review is undone alone). On
the Review card, Later's "Pick a date…" opens a date field with a "Use this date" button: only
that button stages, never the field's own change event (a phone's wheel fires it mid-scroll), and
every context gets a More / Less button on phones. Settings saves on change and exposes the sweep intervals (Slack's too), the planner toggle
and the chat budget; Settings → Connections turns the claude.ai connector on, arms it for a new
client, and lists every connector with Revoke.

Wave 9 (2026-11-04). **Today** opens with the summary card (§8.4): the text, when it was written,
a badge with the number of changes since and the reasons under it (each task code goes to its row),
and Refresh, which shows progress while the scheduler works and then reloads the lists. **Sections
fold**: every group on Today and Timeline has a heading that folds it, Projects remembers which
projects were opened, and the state is kept per browser (`localStorage`, `factotum.folds`), so a
phone and a desktop can differ. A folded section renders no rows, so `j`/`k` and the bulk bar only
see what is on screen. **The Top rail** shows tiles (rank, the name on up to three lines, code,
project, date) in a wider column that stays in view while the list scrolls; it folds on every width
to one numbered tile per item, ringed red (overdue) or amber (due within a day), and the fold is
remembered. A click on a tile, folded or not, goes to the task's row: on the current tab if it lists
the task, else Today, else Timeline (which lists every open task; a toast says so); the section is
unfolded and the row focused and flashed. Two clicks open its details as well; the first waits
260 ms for the second, because the jump scrolls the page and a second tap would otherwise land on
whatever moved under the finger. The Top chip on a row shows the same number as its tile (stored
ranks can have gaps). Settings → Claude has the Today summary toggle and its minimum interval.

Wave 10 (2026-11-05). **Prompt boxes** (every composer and the Review card's Ask box) are text
areas that grow with what is typed, up to about ten lines. Enter sends and Shift+Enter starts a new
line; on a touch keyboard Enter is a new line and the button sends. What is typed lives outside the
DOM (`draft` in app.js) and is put back with its caret after every render, because a running chat
turn re-renders the panel every second. **Chat**: what a prompt carries is the selection, shown as
chips with the task's name and a × each, plus Remove all; "+ Attach a task" opens a box that finds
open tasks by number or by words of the name (Enter takes the first), so nothing has to be selected
on another tab, and a thread can start with nothing attached (the focused row is no longer attached
there). The row hints and the bulk bar are not shown in Chat, Esc closes the task box instead of
clearing the attachments, an empty thread offers three starter prompts (they fill the box, never
send), the header has Delete thread (`DELETE /api/chat/threads/{id}`: the thread and its messages;
nothing it applied is undone and its runs stay in the Log; refused with 409 while a turn is running
in it), Enter while a turn is running keeps the prompt in the box instead of dropping it, and the box stays at the bottom of the
window while a long thread scrolls. **Review** shows a mail suggestion (§8.5) as its own card and
re-reads the queue after Create, so the new row's card comes next. **Log** has four views,
remembered per browser: *Timeline* merges change batches and runs by time, newest first, under a
heading per day with that day's counts and cost; each is one line (time, who or what, a one-line
summary, cost and duration) with a stripe saying which it is (a change, a Claude run, an automation,
a failure), and a click opens it: a batch as one line per task in words ("done", "due Nov 5 →
Nov 9", "note added: …"; bookkeeping fields are left to Changes), a run as its whole result.
*Changes* and *Runs* are the two lists as before, *All* stacks them. "Hide routine runs" drops the
mirror, the sweeps, reminders and the review nudge when they cost nothing and did not fail; "Show
older" widens the window (`/api/log?limit=`, which also reports whether each list was cut, and the
timeline stops at the older list's last entry so its tail never shows one kind only). A card layout
for the timeline was tried and dropped: one batch's field changes filled the screen. Settings →
Inputs has "Tasks from mail" with its minimum interval.

**Settings → Notifications** (stage 2 of the linked-tasks design, 2026-11-10, design §14). "Send
reminders to" (`notify.provider`: ntfy, The app, Both; §10) is the first control, then "This device"
and "Devices with notifications on", then the ntfy switch and topic (moved here from Outputs). The
"Review at" pill in the header shows when the nudge can reach somewhere (ntfy on with a topic, or the
app with a device, as the provider allows). "This device" is in one of these states:
- *Checking this device…* while `GET /api/push` and the browser's subscription are read.
- *Cannot*: "This browser cannot show notifications from Factotum." (no service worker, Push API or
  Notification), with the browsers that can.
- *Install first*: on an iPhone or iPad outside the installed app, "On an iPhone, notifications work
  only in the installed app." and how to add it to the Home Screen.
- *Factotum cannot send*: the key file is unreadable (or the page could not ask), with the reason.
- *Not allowed*: the browser's permission is denied; how to allow it in the site settings (the word
  "blocked" is not used).
- *Off*: "Turn on notifications on this device". The tap asks the permission first, registers the
  worker if needed, subscribes with the server's public key (a subscription made with an older key
  is dropped first) and posts it with the device's label and `location.origin`.
- *Turning on…* while that runs.
- *On*: "Notifications are on for this device." with Send a test and Turn off; when the provider is
  ntfy alone, a hint that reminders go to ntfy only at the moment.
- *Refused*: "The push service refused:" and why (the browser's subscribe failed, Factotum did not
  take the subscription, or this device's last push failed), with Try again (and Turn off when
  there is a subscription).

The devices table lists each row (label, "(this device)", added, last sent, the last failure when it
is newer than the last success) with Remove on every other device.

**Links in the API (stage 4a; the page is stage 4b, below).** `GET /api/tasks` carries a compact `rel`
per task (links, `waits_on_open`, `frees_open`, `goal_id`, steps open / total, `needed_by`, `changed`,
`suggestions`; omitted when empty) and a `neighbours` map naming every other task they mention. `GET
/api/tasks/{id}` also carries `rel`, `relations` (active, with the other task's state), `nearby`
(tasks sharing a recording or mail thread, never stored), and `history` (this task's `changes`, newest
first, 30 a page, `before=<change id>`, each with source, cause, undone, note and, for a link, the other
task); opening a task stamps `neighbours_seen_at`. Review items carry `texts` for the link reasons and
`link_suggestions`. `POST /api/batches/{id}/undo` returns `restored` and `skipped` (a field the owner
changed since, a link whose restore would loop or duplicate, each with a reason) and 404 only for a batch
that does not exist. The Log carries each batch's `cause` and each change's `note`. The page is described next.

**Links on the page (stage 4b, 2026-11-11).** A row carries one badge per kind, in this order:
"do first: frees" / "frees" (teal), "waits on" (amber), "steps" (teal), "updated by" (purple), "part of"
(teal), a suggestion (dashed amber); a badge whose link is staged in the tray takes the staged look instead
of its colour. Several tasks of one kind read "waits on Task-12 +1". On a phone a row shows its first badge
and "N more links" for the rest. A badge is a link to `/t/N` and jumps to that task.
A bar over Today and Timeline chooses All / Linked / Ready now (one remembered choice for both); Ready now
hides waiting tasks but keeps a hard-dated waiting task, dimmed, with its badge. The Linked view regroups
every open task that has a link or a suggestion into three kinds of group: a goal with its steps, a chain
of waiting tasks from the first one they wait on, and a card for a pair suggested as the same. With no
links, suggestions or markers nothing is drawn: no bar, no badges, no legend. Today's order: a task that
frees a hard-dated task in an earlier group is pulled forward into that group, a waiting task is drawn
under the task it waits on, and a hard-dated waiting task never leaves its group.

The opened task has Linked tasks, provenance and History, fetched only when the task is opened (opening
stamps `neighbours_seen_at`). History is one entry per batch: who, what, why, before and after, an Undo
for the batch ("Undo all N changes made together" when it holds more than one), and Earlier for the next
page. The task page (the phone's, and on a desktop for a task no list holds) keeps a trail back to the task you
came from; on a phone it pins Done / Later / More at the bottom, on a desktop they sit in the page. A closed task is read-only with Reopen, on both devices.

Review shows the link reasons in words, a steps chip for progress, and link suggestions (a suggestion
carries `dir`: "out" when the listed task is the suggestion's first end, so the page knows which end is the
step or the waiting one). The tray keys a staged link `rel:<a>:<kind>:<b>`; a staged text edit from Chat
or the planner carries `expect` (the server's current value); a Review note is staged as `add_note`.
The Log says each change in words. The page after a reminder's Done lists what that frees now.

**Ripples on the page (stage 5, 2026-11-11).** Closing a task that others hang on asks one optional line,
"How did it turn out?" (the toast after Done offers "Say how it went"; on Review the note typed with a
close is the outcome; the page after a reminder's Done has the same box, posted to `/r/<token>/outcome`).
Done never waits on it. After an Apply that queued a ripple the page asks `GET /api/ripple` every 15 s,
only while a scheduler runs, the page is visible and one is pending (at most 10 minutes), then says "N
linked tasks brought up to date" with See changes and Undo. A Review card a ripple changed shows, under
"Since you last looked", what the linked task did (with its outcome) and that its text was brought up to
date, with See what changed and Undo; a ripple's proposal reads "Ripple suggests" with Accept and Dismiss.
History and the Log say when a rewrite became a note and when a ripple's cause was undone. Settings →
Claude has the Ripples controls and says when ripples wait because this instance runs no scheduler.

**In place, by hand (stage 6, 2026-11-11).** *The conversation in place.* One component (`placeHtml`) is
drawn in three places: on a Review card, under the box on Today / Timeline / Projects (typing plain text
there continues the `today` conversation without changing tab; the Chat tab's own box is unchanged), and in
an opened task (its canned buttons now answer there instead of switching to Chat). Proposals are listed in
the order Claude gave them with a hint on each kind; Review's button reads "Stage all N with this decision",
elsewhere "Stage N"; a create row also has Create (at once) and Edit first (it fills the New task line). A
link to a new task of the same reply is left out (and says so) while that task is unticked and not made. A
proposal applied from a place or the Chat tab reads "applied" and is not offered again (the page remembers it
per browser; an Undo of that batch offers it again). One turn runs at a time on the page; a follow-up while
one runs is refused with the text kept. The Today box's conversation is asked for again on every return to
the app, so one quiet for 12 hours is let go in an app left open. Close lets it go at once and holds: the page
remembers the closed thread's id in this browser (`factotum.todayClosed`), so a reload does not draw it again and
the next sentence starts a new thread (`fresh: true`); a newer Today thread is drawn as usual.
*New task.* On desktop a New task line (`n` or the button) with the add line's live preview ("Will be added
as", chips, a legend of what each part meant, refused parts with their reasons); Enter adds at once, the
toast says "Added Task-N" with Open and Undo, and "Add and open" opens it. Adding never changes the tab and
never focuses or attaches the new task (the box keeps the focus, so the next one can be typed at once; a row drawn
in the current view flashes), and Undo of an add lets go of that task if it was attached since. A bare `add` /
`new` / `+` is refused in the preview ("a task needs a name") and sends nothing. Edit first moves a proposed task
into this line. On a phone a round button at the bottom opens a sheet (a name, then taps for the date,
project, effort and a link; Add task adds, while the keyboard's Enter or Done only puts the keyboard away;
or let Claude file a typed or dictated sentence, which carries no selected rows). Each box that adds (the line,
the Today box, the sheet, each Add a step line) has its own key; Duplicate takes a fresh one. On a phone one
thing sits at the bottom (the tray, the short bar, the pinned bars, the round button) and none shows while
a box has focus (`body.kb-open`), so nothing covers the box (the short bar stays only while its own date box
has the focus; Review's four decisions stay while its note box has it, `body.kb-note`, since the note is Done's
outcome: one short row of four just above the keyboard, at `--kb`).
*The editor.* An opened open task is its own editor: name, where it stands (`context`), due and hard
deadline, project, effort and one "who" field with a choice `is for` / `waiting on` (it writes `counterpart`
or `waiting_on`, never both; changing the choice moves the text). Each edit stages under its own key
(`<id>:edit-<field>`) and shows on the row before Apply; a text edit carries `expect`; the task's own staged
line applies or discards just its changes; a closed task is read-only.
*Linking by hand.* "Link a task" finds any task and offers is a step of it / waits on it / is the same task;
a link shows a Remove; "Add a step" (on every open task that is not itself a step) adds a task under it at
once; More holds Split into steps, Duplicate and Merge into another; two selected rows offer Link in the
bulk bar; a Linked-view goal whose text lists at least two steps offers Make them tasks.
*Find any task.* `/` or "Find any task" opens a finder over `GET /api/search` (closed tasks too);
`python -m factotum find`. *The empty Today tab* says how to add a first task (and no longer tells a
newcomer to run the Notion import first).

**Suggested links (stage 7, 2026-11-11, design §10.1).** Three free detectors in `factotum/suggestions.py` run
after the sweeps (every 30 minutes, `link_suggestions` in §10) and at every create, and write
`link_suggestion` evidence through `suggest_pair` only; they never write `relations`.
*Twins* (`same_as`): an open Canvas row and an open row of another source, same project, the same number
in the name, due within 36 hours (`relations.twin_pairs`); two rows that are both from Canvas pair only
when their names are one item's (equal after normalising numbers: "Quiz 05" is "Quiz 5"), because two Canvas
items are two items, so Quiz 5, Discussion 5 and Reading 5 of one week make no twins; and a row with several
twin candidates keeps the one it shares a title word with (project words and Canvas verbs left out), or none.
The newer row goes and the Canvas or older one is kept. *Threads* (`any`): two to four open tasks with the
same `mail_thread` or `calendar` link; five or more is a mailing list, not a pair; a shared recording or a
shared person (`plaud_note`, `counterpart`) is provenance and never read. *Text* (kind guessed): "Task-N"
(`\bTask-(\d{1,7})\b`, case-insensitive) in a task's name or text, the first 4,000 characters, at most 10
references per task; the kind comes from the 60 characters before it on the same line (part of, steps,
after, waits on, same as, merged into, before, ...) or a list label (`Sub-tasks:`, `Steps:`) earlier on that
line, else `any`; `#N` and "task 12" are not the form. A `why` is generated ("Task-10's text names Task-20")
and never quotes the task's text. **One card per unordered pair, ever** (`suggestions.why_not`): a pair with any
`link_suggestion` row (pending or judged, either order, any signal) or any `relations` row (active or removed)
is never suggested again, so a dismissed, linked or unlinked pair stays decided.
*The daily cap.* `links.suggestions_per_review` (Settings, default 5; 0 shows none) is the number of link
suggestions offered per day. `suggestions.session` picks them, never-offered first, then the longest unseen,
then the oldest, so a card the owner keeps skipping cannot keep another out for good, records the day's set in
`state.links.session` (and each one's day in `state.links.offered`) and takes no slot for a card whose task
closed; a judged one is not replaced the same day. `relations.annotate` filters each row's suggestions to that
set, so Review, the dashed badges, the Linked view, the brief's counts and the agent's `task_get` agree; with no
session yet (a fresh database) every pending one shows. A `filing` suggestion is exempt: it is about a task
the owner just made and shows as chips.
*Filing chips.* A task made by hand shows what the filing run (§8.7) suggested as dashed chips on its row and in
the opened task and task page ("Personal?", "S?", "part of Task-N?"), each with a ×: a tap on the chip stages
the field (under the editor's key) or the link with its verdict, × dismisses it; a filing link is a chip, not a
second dashed badge; chips and × are at least 44 px where a finger lands. *The Review mail card* is
"This mail is about Task-N. Follow its thread?" with the sender and subject, "Follow its thread" (stages
`follow_thread`) and "Not about this" (dismisses); a mail suggestion's card says "Will be a step of Task-N ..."
before Create. *Settings* gains "Suggested links" (the number a day, under Review and lifecycle) and "Filing a
task you add" (a switch under Claude).

**App extras (stage 8 of the linked-tasks design, 2026-11-11).** The number beside Review comes from
`GET /api/review/count` (`review_queue.count`, read-only: `GET /api/review` records the day's suggestion
session and stamps `last_offered_at`, so it is never called for a number) from the first load until Review is
loaded, then from the Review page, which leaves out what the tray already decided; back at the front off
the Review tab, that page is let go and the server's count is read again. The app's icon gets exactly
the number the tab shows (`navigator.setAppBadge`; `clearAppBadge` at 0, when the sign-in has expired and when
the switch is off; one call per change, not one per redraw, and once more on every return to the front, so a
number a push left there is corrected; while no count is known yet the icon is left as it is). Settings → Notifications has "Count on the app
icon" (`notify.app.badge`, on when absent: the count makes no run, sends no mail and no push of its own, so it
is not one of the paid or mailing switches that absent turns off, §9 "Configuration"). The manifest has two
`shortcuts`, "New task" (`/new`) and "Review" (`/review`), each with a 96 px icon drawn by `tools/make_icons.py`
(`shortcut`). `/new` is a page address, not a tab: it opens Today with New task through the same opener as the
`n` key, replaces the address with `/` (no history entry, so a reload opens Today plainly), and a sign-in on the
way keeps it. The limits, as they are: iOS ignores manifest shortcuts. An iPhone shows the number only for the
installed app and only once notifications are allowed for it. Chromium re-reads an installed app's manifest
lazily, so new shortcuts may need a reinstall to appear. Firefox has no Badging API and Chrome on Android shows
no number from it. On a desktop only the installed app shows the number, not a browser tab. The number is as
fresh as the last time the app was open or the last push it received. Before Review is opened on a day the
count may differ from Review's own by that day's link suggestions, which opening Review adds.

## 10. Reminders and push

- The public ntfy.sh server caps delay at 3 days and 250 messages/day, so **the service owns the
  schedule** and publishes at fire time.
- Task reminder: title = task name, body = label + due, up to three `view` actions (*Snooze 1 h*,
  *Done*, *Open*) pointing at `/r/<token>?…`, since `http` actions cannot carry an Access session.
  No `Click` header (2026-11-02): with one, a tap on the phone bounced to the browser before the
  buttons could be used; `notify.ntfy.tap_opens_app: true` restores it. Tokens are single use, 24 h.
- **"Task-N is free now"** (2026-11-11): `reminders.free_now`, from the scheduler tick, through the chosen
  providers, for an open hard-dated task whose last open waits-on was done today; once per (task, blocker,
  day), remembered in `state.free_now.sent`. No Claude run, independent of `claude.ripple.enabled`.
- Review push: daily at `notify.review_time`; skipped when reviewed today or the queue is empty.
- **Suggested links and filing** (stage 7, 2026-11-11): the scheduler's `link_suggestions` job runs the three link detectors (§9) every `suggestions.SWEEP_MINUTES` (30; free, a constant), after the sweeps, and writes the day's cap through `suggestions.session`. The `filing` job runs the filing queue (§8.7), at most 3 tasks a tick, only while `claude.filing.enabled` is on and a task is waiting. Neither needs a Refresh.
- **The clock** is one thread in the service (`factotum/scheduler.py`): a tick every 30 s re-reads the
  effective config, fires due reminders, and runs each daily job once at or after its time (so a
  service down at 06:30 still sends the brief when it comes back); a failed job retries after an
  hour. Daily jobs: `expire` at `lifecycle.expire_time` (03:30), the brief, the review push; the
  calendar sweep every `sources.calendar.sweep_minutes`. Everything that did something lands in
  `runs` with cost 0, so the Log tab shows it next to the Claude runs.
- **Nothing expires unseen.** `/api/review` stamps `last_offered_at` on the rows it serves; `expire`
  fades or lapses a row only if it was on a Review page on or after the day its warning started.
  Until then it stays open, stays in Review, and the push keeps asking. A Review decision (Keep,
  Later) restarts the quiet clock, so a kept backlog item returns as Fading in `fade_days`.
- Action tokens live on the reminder row; Done and Snooze are single use (a second tap says what
  already happened), Open only navigates (to `/t/<id>`, as does the optional tap; the page after Done or Snooze links the
  task too); all three stop working 24 h after the push. Snooze is
  one hour from the tap. A repeating reminder's next occurrence is queued when it fires; a
  reminder with a malformed `fire_at` never fires and shows in the task's details for repair.
- **Setting one.** "Set reminder" on a Review card and in a row's details: date-time, optional
  label, once or daily / weekdays / weekly / monthly. It is a staged change (`add_reminder`) under
  its own key, so it rides in the same batch as the decision without replacing it, Keep, Later or
  even Drop; undoing the batch cancels it. A reminder fires whatever the task's status is: a
  dropped task can still carry a "check anyway" nudge. When a repeating reminder fires, the
  scheduler marks it sent and inserts the next occurrence as a new pending row (phase 2). `fire_at`
  is naive local wall time (`YYYY-MM-DDTHH:MM`) in `owner.timezone`; the scheduler converts.
- Voice: "add X due next week, remind me tomorrow afternoon" → a task plus one reminder.
- The morning brief lists reminders firing today, so email and push agree. The planner runs at most
  once per brief day: its result (or its failure) is kept in `state.brief.planner` before the mail
  is sent, and a retried send reuses it (2026-11-10).

**Notifications from the app** (stage 2 of the linked-tasks design, 2026-11-10, design §14).
`factotum/webpush.py` is the protocol (RFC 8291 `aes128gcm` encryption, RFC 8292 VAPID with an ES256
JWT, one HTTP send) on the installed `cryptography` and `httpx`; `factotum/app_push.py` owns the key
pair, the devices, the payload and sending; `factotum/web/sw.js` is the worker.

- **Providers.** `notify.provider` is `ntfy`, `app` or `both` (a Settings key, "Send reminders to").
  Absent or unknown means ntfy (`config.DEFAULTS`), so an existing configuration keeps ntfy; the
  starter has no provider key and ntfy off, so it sends nothing. Reminders and the evening review
  nudge go to each chosen provider; sync-file's failure alert stays on ntfy.
- **The key pair** is `push-keys.json` beside the database (`app_push.KEY_FILE`): `{"private",
  "public", "created_at"}`, base64url, written atomically with mode 0600. Only `GET /api/push` and
  `POST /api/push/subscribe` make one, when it is missing; making one deletes every row of
  `push_subscriptions` (each subscription was bound to the old public key). The sender never makes
  one: missing or unreadable at send time is `app_push.KeyFileError`, nothing is sent, the reminder
  stays pending and the Log says why. An unreadable file is never overwritten.
- **The payload** is the declarative Web Push format (`{"web_push": 8030, "notification": {…}}`,
  `app_push.payload`): title, body, `navigate` (the task's `/t/<id>`, `/review` for the nudge,
  `/settings` for a test), `tag` with `renotify`, the buttons, and `data.task_id`. Safari (iOS 18.4+,
  macOS 15.5+) shows it without running the worker; Chrome, Edge and Firefox hand the same JSON to
  the worker, whose `shape()` shows it the same way. It has no `mutable` member: with it Safari would
  run the worker and wait on it. Its links are absolute on each device's own origin
  (`push_subscriptions.origin`), so the dev instance's notifications open the dev instance. It is at
  most `webpush.MAX_PLAINTEXT` (3993) bytes, one 4096-byte record: over that, the body is cut (ending
  in "…"), then the title, then the buttons are dropped; `navigate`, the tag and `data` are never cut
  (every notice Factotum makes keeps them short; one too large anyway is refused by `encrypt` and
  recorded as that device's failure).
- **Tag, Topic, TTL, urgency.** The tag is `task-<id>` (the nudge `review`, a test `test`) and is
  also the push service's `Topic` header, so a newer reminder for the same task replaces an older
  one on the device and at the service. A reminder lives 86400 s (the token's life) at urgency
  `high`; the nudge 14400 s at `normal`; a test 600 s at `high`. The VAPID `sub` is
  `server.public_url` when it is `https://…`, else the device's origin when that is, else none.
- **Buttons.** A reminder for an open task carries Done, then Snooze 1 h (browsers show at most two;
  the tap is Open). Both are the same `/r/<token>?a=…` links ntfy carries (`reminders.notice`, one
  token for both providers), which stay behind the sign-in. The worker fetches the link with the
  cookie and, on an answer that is not ok (an expired sign-in is a redirect, an expired token an
  error page) or no answer, opens the task instead; on success the notification becomes "Marked
  done" or "Snoozed for 1 hour". Where Safari shows declarative buttons they navigate to the link.
  A reminder whose task is no longer open has no buttons; its body says the task's status and the
  tap opens the task page.
- **The worker** (`/sw.js`) handles `push` and `notificationclick` only; it has no fetch handler,
  so it never stores or serves the app. It is served by its own route with `Cache-Control: no-cache,
  must-revalidate`, registered with scope `/` and `updateViaCache: 'none'`, takes over at once
  (`skipWaiting`, `clients.claim`) and is not in `api.STAMPED` (a worker change reloads no page).
  Every push shows a notification: a payload it cannot read, or a refused one, shows "Factotum". A
  tap opens the app at the notification's address: an open window is sent `{type: 'open', path}` and
  follows it like Back (`followPath` → `applyAddress`), else a new window opens.
- **Delivery.** `app_push.send_all` makes every network call before its first write, then writes
  the device rows and commits them itself (a removal must survive the caller's rollback). A 404 or
  410 removes the device; any other failure or no answer is kept on its row (`last_failure_at`,
  `last_error`: `HTTP <status> from <host>`, `could not reach <host>: <error name>`; a push service's
  own answer is never repeated, in a route, on a row or in the Log). It raises `NothingSent` when there was a device and none took the push. One
  provider failing never holds the other (`notify.send_each`): a reminder is marked sent when at
  least one chosen provider delivered (ntfy returned a message id; the app reached a device); when
  none did and one raised, the reminder stays pending and the error is logged once an hour, as an
  ntfy failure always was. The scheduler fires reminders only when `notify.ready` names a chosen
  provider that can deliver (ntfy on with a topic; the app with a device), so nothing is marked sent
  into the void. A test from Settings (`POST /api/push/test`) is sent in the request, to that one
  device.
- **Only known push services.** A subscription's endpoint must be `https` on a known push service:
  `fcm.googleapis.com`, `updates.push.services.mozilla.com`, `web.push.apple.com` or any
  `*.push.apple.com`, any `*.notify.windows.com`, the names in `notify.app.push_hosts` (config file
  only, not a Settings key; exact names), and any name under `.invalid` (it never resolves; the UI
  checks use `push.invalid`). An IP literal, `localhost` (with a trailing dot or as a suffix),
  credentials in the address, or a host a resolver might read as a number is refused whatever the
  config says. `app_push.endpoint_problem` decides, at subscribe (a 400 that names the host and the
  key) and again before every send, so a row stored earlier or by hand is recorded as a failure and
  never posted to. The server must never be a way to make requests to an address a caller chose.
- **The count on the icon** (stage 8, 2026-11-11). A push that is sent anyway carries `app_badge`, the
  Review count, as a top-level integer of the declarative payload (WebKit moved it out of `notification` in
  2025, and a nested one crashed an update path, so it is never nested and never a string). It is added in one
  place, `app_push.with_badge`, called by `send_all` and `send_test`, and is absent when `notify.app.badge` is
  off or the count fails (a count that raises never stops a push). Safari applies it without the worker; the
  worker's `setBadge` does elsewhere; a message a Safari build cannot read declaratively is shown by the worker
  instead, which sets the badge itself. Never a push for the number alone: every push shows a notification.
  The evening review nudge counts what Review shows (`review_queue.queue`, plus the waiting suggestion cards).
- **Routes.** `GET /api/push` (the public key, made on first ask, or why there is none; the
  provider; the devices without their keys), `POST /api/push/subscribe`, `/unsubscribe` (by
  endpoint or id), `/test`. A refusal is a 400, 404 or 409, never a 401 or 403.

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
| planner-morning | $0.75 | $0.75 (measured ≈ $0.10 a run; each Refresh on the Today tab is one more) |
| today-summary (§8.4) | $0.30 | ≈ $0.18 a rewrite measured on sonnet (2026-11-04), at most one per `claude.summary.auto_minutes` and only while the summary is stale; a busy day of edits is $1–3. `claude.models.today_summary: haiku` or a longer interval cuts it |
| mail-triage (§8.5) | $0.40 | ≈ $0.14 per batch of 15 messages (measured 2026-11-05); runs only when there is new mail, at most every `sources.mail.suggest.minutes` |
| ripple (§8.6) | $0.30 | one light run per group of changes, no tools; measured cost: about $0.03 a run (one real run on 2026-11-11: Sonnet, no tools, a 4.5 KB prompt, 9 s, three linked tasks; preflight ruling P11) |
| filing (§8.7) | $0.10 | one no-tools run per task added by hand, only with `claude.filing.enabled` on. Measured 2026-11-11 on a copy of the real database: Sonnet by default (`claude.models.filing: sonnet` in the example and the starter), about $0.04 a task; Haiku is cheaper (about $0.003) and found no link, so `filing: haiku` gets a project and an effort but rarely a link or a twin (SETUP §21). The link detectors and the link sweep are free (no Claude run) |
| chat-turn / search / draft | $0.60 | on demand |
| sweeps, UI, reminders, brief | $0 | $0 |

Every paid run Factotum starts on its own is off unless its switch is explicitly on (absent means
off, §9 "Configuration"); a chat turn runs only when the user sends one.

Target under $4/day all in, versus ≈$9/day measured in September. Settings shows the rolling
7-day total from `runs`. Headless allowlists exclude every destructive Mailbird tool; Python
validates every proposal before writing (`batches.SETTABLE`); automatic closes always cite a
link; every batch is undoable; sinks are write-only.

Notifications from the app (§10) cost nothing: the browsers' push services are free and no Claude
run is involved. The private key never leaves the server (no `/api` answer carries more than the
public half, and `/api/settings` carries neither); a new key pair empties the devices, which then
turn notifications on again. The payload is encrypted end to end for each device, so the push
service carries a task's name and its token links without being able to read them; ntfy's topic
readers can read both, which is why ntfy's topic must not be guessable (SETUP §4).

The count on the app's icon (§9, §10) costs nothing: no run, no mail, no extra push; it travels encrypted inside
pushes that are sent anyway.

## 14. Build order

0. **Prep (mf-pc, mostly the user):** `docs/SETUP.md` §1–§8.
1. **Core (this commit):** store, scoring, lifecycle, import with dry-run report, Today /
   Timeline / Projects / Review / Settings, tray with undo, grammar commands, accounts table.
   *Gate: the user's first Review session on real rows.*
2. **Push + brief (built 2026-11-01):** scheduler, ntfy reminders and the review push, `/r/<token>`
   routes, morning brief email (Python-built until the phase 4 planner adds the verdict),
   `google-auth`, `calendar-check` and the calendar sweep. *Gate: a reminder's buttons work from the
   phone, the brief arrives, the calendar is authorised.*
3. **Plaud port (built 2026-11-01):** IMAP trigger, CLI fetch, `sync-file` run with the new bar,
   Notion notes sink, voice verbs incl. reminders and tiers. In parallel with plaud-sync until the
   first few recordings look right, then stop it (both write the note page; only Factotum writes
   tasks, so the interim `--new-only` cron is already gone). *Gate passed 2026-11-01 22:58: a
   recording made after the restart landed as a task 25 s after Plaud's mail, with its note page
   and a run row; plaud-sync disabled on lark-server the same night.*
4. **Mail + Canvas + planner (built 2026-11-02):** Mailbird client and sweep, Canvas sweep,
   `planner-morning`. The lark-server planner and digest timers are disabled; the Gmail forwarding
   filter on the SDSU account is the user's to delete. *Gate: a morning brief with a verdict whose
   proposals were right, and a Waiting task flipped by a real reply.*
5. **Chat (built 2026-11-02):** `factotum mcp`, chat turns streamed into the Chat tab, "Search
   related emails" and "Draft reply" on every row, the Review card's prompt field, proposals staged
   into the tray, drafts in Mailbird. *Gate: a real "Search related emails" links the right thread, a
   draft reply appears in Mailbird unsent, and a Review-card question stages the intended change.*
6. **Mirror and cleanup (built 2026-11-02):** the Notion tasks mirror and its scheduler pass; the
   two skills (`skills/factotum-planner`, `skills/factotum-ops`) on `factotum mcp`, registered for
   Claude Code and reachable over `POST /mcp` with a bearer token; batches a skill stages land in
   the tray. Retiring the Today page and the Command Log and archiving plaud-sync are the user's
   clicks (SETUP §15). Same day, from the user's list: evidence verdicts as batch changes, planner
   suggestions and Accept on Review, Settings for intervals / planner / chat budget, Slack and
   Discord read tools, the ntfy tap, the phone layout, the chat sidebar and turn duration, the
   chat link's account. *Gate: the first `factotum mirror --all` pass lands every row in Notion,
   a skill turn stages a batch that the tray applies, and the phone shows a row's title in full.*
   Wave 7 (2026-11-02, after the restart): the Slack mention sweep, Discord forums and threads, the
   claude.ai connector (§15), `mirror --task` and the Notes-relation fix behind the three 404s.

## 15. Claude Code skills

`plaud-sync-planner` and `plaud-sync-ops` (installed in Claude Code and claude.ai) kept working
until import day; from then the Notion Tasks DB is a mirror and writes there are lost. Phase 6
replaces them with `factotum-planner` and `factotum-ops` under `skills/` (symlinked into
`~/.claude/skills`): the planner skill uses the same `factotum mcp` server Chat uses, plus one
write tool, `stage_changes`, which records a pending batch in `proposals` (validated like a chat
proposal, through a separate connection; the tool connection stays `query_only`). The tray shows
it with Apply / Dismiss, so the tray rule holds from a skill too; `stage_changes` is left out of a
chat turn's allowlist, whose proposals travel in its JSON. For Claude Code on mf-pc the server is
local stdio (`claude mcp add -s user factotum -e PYTHONPATH=… -- .venv/bin/python -m factotum mcp`).
For another machine it is `POST /mcp` on the service: one JSON-RPC message per request behind
`Authorization: Bearer $FACTOTUM_MCP_TOKEN` (constant-time compared; no token, no route), serving
reads and `stage_changes` by default, drafts and the Plaud subprocess only when
`server.mcp_http_tools` opts in.

**The claude.ai connector (built 2026-11-02, `factotum/oauth.py`).** A claude.ai or Claude Desktop
custom connector cannot send a bearer header, so Factotum is its own OAuth 2.1 authorization server
for `/mcp` when `server.oauth.enabled` is on: RFC 9728 protected-resource metadata (and a 401 on
`/mcp` that points at it) → RFC 8414 server metadata (advertising `client_id_metadata_document_supported`)
→ the client identifies itself either by its Client ID Metadata Document (the client_id is an https
URL on a host in `server.oauth.cimd_hosts`, claude.ai / claude.com; the document must name itself
and is the authority on its redirect URIs, which must still be in `server.oauth.redirect_uris`
unless loopback; stored once the owner reaches its consent page, and because that id is public
every consent needs the arming window) or by RFC 7591 dynamic registration, which accepts only
the allowlisted callbacks, only while Settings → Connections is armed, and caps live clients →
the consent page → PKCE S256 code exchange (`resource` = `<public_url>/mcp`) → opaque tokens stored
as SHA-256 hashes, refresh rotation, 24 h access / 90 day refresh by default. The consent page is
the one human step and the one place identity matters: it stays behind Cloudflare Access and
verifies the signed assertion Access attaches to every request it lets through (RS256 against the
team's published certs, `aud` = the Access application's tag, `iss` = the team; `access_team` and
`access_aud` in config, and the gate refuses until both are set) and requires an `owner.emails`
address; a bare identity header is never enough, since anything that can reach the origin can send
one. A connector that was never granted is approved only while the owner has armed Settings →
Connections ("Allow a new connector", 10 minutes), so a consent link somebody else minted cannot be
approved by reflex; the page states when the client registered and warns to Deny anything the owner
did not just add. **Writing from a connector or a skill (2026-11-04).** Two tools, one validator, one write path.
`stage_changes` records a batch for the tray (Apply / Dismiss); `apply_changes` writes the same
batch at once and exists only when `server.mcp_writes` is `apply` (Settings → Connections; the
default is `stage`, so the tray rule holds until the owner chooses otherwise). Either way the
changes go through `batches.apply` and nothing else: source `connector` (the HTTP route) or
`skill` (stdio), every old value in the Log, one Undo per call. The ops are the staged set plus
`create` (a batch op now: an open task with Source "Claude", its reminders and an optional
sent-mail watch in the same batch; undo drops the row, never deletes it, since ids are forever and
the mirror may have a page), `reopen`, `watch_sent` / `unwatch_sent`. Validation is shared:
existing rows in the right state, settable fields only (score, priority, status and mentions stay
derived), taxonomy projects, well-formed dates, efforts, reminders and addresses, integer ids,
at most 40 changes a call (the cut is reported), and no twin of an open task's name (compared
case- and space-insensitively, within the call too). Nothing is dropped quietly: a value the
cleaner would lose rejects its change, and a key that is not settable comes back in `ignored`.
Because nobody taps Apply on a direct write, three more bounds apply to it: direct changes are
capped per hour (`server.mcp_apply_hourly_cap`, 100; beyond it Claude must stage), an address a
direct batch put on a task is not a contact `mail_draft_new` may write to (the same address through
the tray, the UI or an input is), and a watch keeps its address in its own column, never in
`counterpart`. The Stage setting is read on every call, so switching back reaches a running Claude
Code session at once. Tools carry MCP annotations (reads `readOnlyHint`; `apply_changes`
`destructiveHint`) so the client can ask the user first. The cost of `apply` is stated on the
consent page: text Claude read in the same conversation (a mail, a Slack message) can steer what
it writes, and the remedy is the Log's Undo rather than a tap beforehand. `/mcp` takes the static token or an access token and serves the same allowlist
either way. Settings → Connections and `factotum connectors` list every client, when it last spoke
and its live tokens, with Revoke (every token at once). The Access bypass is for exactly the paths a
connector's servers call — `/mcp`, `/oauth/register`, `/oauth/token`, the three `/.well-known`
documents — and never `/oauth/authorize` (SETUP §16). For Claude Code on another machine the bearer
route remains the simpler path.

**Links from Claude (stage 4a).** Four read tools: `task_neighbours` (what a task is linked to, steps
and what it waits on), `waiting_tasks`, `twin_candidates` (pairs that look like the same task) and
`goal_tree`; `tasks_query` gains link facts and the filters `ready`, `linked_to`, `goal`, and fits its JSON
into `TOOL_BUDGET` by dropping whole rows from the end and reporting the count cut, so a result is never
cut mid-JSON; `task_get` shows `linked` (task links; `links` stays outside things). New ops for
`stage_changes`: `link`, `unlink`, `merge`, `add_note`, `done` / `drop` with `outcome`, and a `set` that
carries `expect`. `apply_changes` accepts `link` and `unlink` but never `merge`: a merge drops a task and
always waits for a tap. A link counts as two rows against the hourly cap. The validator checks a link with
the same rules as `batches.apply` before the open-task rule.

## 16. Open questions

- Hostname: `factotum.nthperson.dev` (recommended) or `plaud-sync.nthperson.dev`.
- Review cap 15 and the 3-day fade warning: right sizes to start?
- Google Calendar: OAuth desktop client, published to production so the token does not expire weekly (SETUP §6); service account kept as a documented alternative.
