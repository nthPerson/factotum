# Linked tasks, ripples, and an app you can install: design

Status: draft for the owner's approval, written 2026-11-10. Nothing here is built except the
install spike described in §13.

Mockups (private to the owner): <https://claude.ai/artifact/72hwR4wcRtD7XVBEhRPcax>. Frames 1 to 7
are desktop, 8 to 12 are a phone. This document describes every surface in words, so it can be
planned from without opening them; the frame numbers are there for whoever can.

This document adds to `SPEC.md`. As each stage lands, the decisions move into `SPEC.md`, the setup
steps into `docs/SETUP.md` and the gotchas into `CLAUDE.md`, as the repository's rules require.

## 1. Why

The owner was reviewing several tasks that were all steps toward one goal. They wanted two things:
to find the sibling tasks easily, and for progress on one to register on the others. They asked the
chat agent to link the tasks. It answered that Factotum cannot link one task to another and wrote
"Sub-tasks: Task-A ... Task-B" into each task's text instead.

A read of the live task list (2026-11-10) showed this was not a one-off:

- About 20 of 54 open tasks take part in a real relationship with another task.
- Three kinds cover nearly all of them: a task is a *step of* a goal; a task *waits on* another
  task; two rows are *the same task* created twice (most often once from a recording and once from
  Canvas).
- The relationships already exist as prose that no code can use: 84 "Task-N" references in task
  text, ten "merged into Task-N" notes, a plan written into a *waiting on* field.
- One overdue hard-deadline task was waiting on a low-priority task with no date. The Today summary
  nagged about the first and never named the second.

Other tools teach one rule that this design follows throughout: a single user keeps a link only
when it changes what the list does. Links that only display are abandoned, and so are deep trees
and planned chains.

The conversation then widened, and the owner asked for all of the following in one design:

- Linked tasks should update each other intelligently when one changes, with a trail that shows
  what changed and why.
- The Review tab should let the owner talk to Claude and act on its proposals without being sent to
  the Chat tab.
- Tasks must be creatable and editable by hand, designed with the same care as the rest.
- The phone must be a first-class surface, not an afterthought.
- The app should install like an app, update itself, and send its own notifications that open the
  task they are about.
- A new user must start from a neutral configuration, not the owner's.

The owner's standing preference (twice stated): prefer usefulness, richness and freshness over
saving Claude runs. The design does not economise on runs, and states their measured cost. A fresh
install still starts with every paid run off (§15); the owner switches the new ones on.

## 2. Goals, non-goals, anti-goals

### Goals

1. Three kinds of link between tasks, each of which changes how the list behaves.
2. Links are mostly proposed by the system and confirmed with one tap. Typing one by hand is
   possible and quick, never required.
3. The chat agent, the skills and the claude.ai connector can read, create, explain and remove
   links through the same validated write path as every other change.
4. When a linked task is closed or materially changed, the tasks linked to it are brought up to
   date by a Claude run (a *ripple*).
5. Every task has a History: what changed, what caused it, why, with an Undo.
6. Review keeps the owner on the card: the conversation with Claude and everything it proposes
   live there.
7. Tasks can be created and edited by hand, on a desktop and on a phone.
8. Every surface has a designed phone layout.
9. The app installs to a home screen or desktop, updates itself, and can send its own
   notifications.
10. A fresh install starts neutral, with every integration and every paid run off.

### Non-goals (deliberately out of this design)

- A graph or timeline view, date shifting along chains, Gantt-style planning.
- More than one level of steps (a step cannot have steps).
- "Related" as a link kind. Loose association is shown as provenance (§4), never stored.
- Mirroring links to Notion.
- Offline use.
- Sharing a page or text from another phone app into Factotum.
- Multi-user.
- The wider packaging work: removing the owner's details from docs, tests and git history; a
  setup wizard; a `doctor` command; installing as a package; a mail provider other than Mailbird;
  a built-in login; a licence. These get their own design. Only the neutral starting configuration
  (§15) is in this one.

### Anti-goals (these would make it a failure even if it works)

- A link that only displays.
- Anything that needs upkeep to stay true.
- A change to a task that cannot be traced to its cause afterwards.
- A ripple, or any text a stranger wrote, moving a date, closing a task or creating a task unseen.
- One task's close cascading into closing others.
- An installed app that goes stale and has to be deleted and re-added.
- A phone layout that is the desktop layout squeezed.

## 3. Constraints from the repository

These are existing rules (`CLAUDE.md`, `SPEC.md`) that shape the design. The builder should read
`CLAUDE.md` in full; the ones below bite hardest here.

- `factotum/batches.py::apply` is the only write path for task state. Claude proposes, Python
  writes, every batch is undoable. Links are task state: they are batch ops.
- A value that differs between users is a config key, never a code branch. New Settings controls
  need `config.SETTINGS_KEYS`, a control in `renderSettings`, and a default in the example YAML.
- For a write that nobody taps (a connector in apply mode, a ripple), the validator is the only
  check. A new settable field needs a line in `mcp_server._validate_changes`, not only in
  `batches.SETTABLE`.
- Text that strangers wrote (mail, Slack, a recording of someone else) can sit inside a task's
  text. It must never prove a change (`planner.NON_PROVING`).
- The API never runs Claude or a sweep itself. It stores a request and wakes the scheduler, which
  is the one thread that does (`serve --no-scheduler` has no scheduler; such routes answer 409).
- `db.log_run` and `set_setting` do not commit; a scheduler branch that returns without
  `conn.commit()` holds SQLite's write lock.
- A tool result over about 30 KB reaches the model cut short (`mcp_server.RESULT_CAP`), as invalid
  JSON.
- `factotum mcp` writes nothing to stdout but JSON-RPC, and its connection is `query_only`.
- The UI has no build step and no JavaScript tests. `render()` rebuilds the panel; anything typed
  must live in `draft`, be wired through `typing()`, and be in the focus-restore list.
- A folded section renders no rows; `jumpTo` must unfold first.
- The offline test suite must stay green at every commit (159 tests, about 17 s today). Since stage 3 it
  loads `tests/fixtures/factotum.fixture.yaml` as its fixture, never the example (§15).
- Task ids are forever. A create is undone by dropping the row, never by deleting it.

## 4. Vocabulary

| Term | Meaning |
|---|---|
| **step of** (`part_of`) | Task A is one step toward goal B. B is the *goal*; a goal shows progress ("1 of 2 steps done"). |
| **waits on** (`waits_on`) | Task A cannot sensibly be done before task B. Seen from B: B *frees* A. |
| **same as** (`same_as`) | Row A is the same task as row B. Confirming it *merges* A into B; the record remains. |
| **waiting** | Derived, never stored: an open task with at least one open task it waits on. |
| **ready** | Derived: open and not waiting. |
| **provenance** | Tasks that came from the same recording, mail thread or person. Shown as "came from the same place". Computed when read, never stored, never a link. |
| **ripple** | A Claude run that brings linked tasks up to date after one of them changes. |
| **outcome** | One optional line recorded when a task is closed: how it turned out. |
| **History** | Per task: the dated list of changes, each with its cause. |

Words shown to the user are the plain ones: "part of", "steps", "waits on", "frees", "same as".
"Blocked" and "dependency" are not used in the interface.

## 5. Data model

### 5.1 `relations` (new table)

Links do not go in the existing `links` table. That table ties a task to an outside thing; it has
no kind-of-relation, no direction, no record of who made a row, and several readers load it in
bulk. One importer row kind (`links.kind = 'task'`, written for a duplicate source key) exists
today, is read by nothing, and renders as a broken link in the UI.

```
relations(
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  src           INTEGER NOT NULL REFERENCES tasks(id),
  dst           INTEGER NOT NULL REFERENCES tasks(id),
  kind          TEXT NOT NULL,          -- part_of | waits_on | same_as
  why           TEXT,                   -- one line, at most 160 characters
  source        TEXT NOT NULL,          -- the batch source that made it
  batch_id      INTEGER,
  created_at    TEXT NOT NULL,
  removed_at    TEXT,                   -- soft delete; History and undo need the row
  removed_batch_id INTEGER,
  CHECK (src <> dst)
)
```

- Direction: `src part_of dst` (src is the step, dst the goal); `src waits_on dst`;
  `src same_as dst` (src was merged into dst).
- A partial unique index on `(src, dst, kind) WHERE removed_at IS NULL`; indexes on `src` and
  `dst`.
- Rules, enforced in `batches` (§6): a task has at most one goal; a goal cannot itself be a step
  and a step cannot have steps; `waits_on` has no cycles; a task cannot wait on its own goal; at
  most 20 active links of one kind per task.

### 5.2 Other schema changes

- `tasks.outcome TEXT` (at most 300 characters). Settable; shown on closed tasks and in their
  neighbours' linked lists.
- `tasks.neighbours_seen_at TEXT`. Stamped when the task is opened or reviewed; used for the
  "changed by a linked task since you last looked" marker. Stamped by the API directly, as
  `last_offered_at` already is.
- `batches.cause_json TEXT` (nullable). What caused the batch, when something did:
  `{"kind": "ripple", "from": [428], "run": 1234}`.
- `changes.note TEXT` (nullable). The one-line reason for a single change. A ripple fills it; any
  source may.
- `chat_threads.origin TEXT` and `chat_threads.task_id INTEGER` (nullable), so a conversation
  started on a Review card, under the Today box or in an opened task can be found and continued
  there (§12.6).
- `push_subscriptions` (new, stage 2): one row per device that turned notifications on
  (endpoint, the two keys, a label, created, last success, last failure).

### 5.3 Migrations

The store today only adds tables and columns. This work needs more, and other people's databases
will need it too:

- Add `PRAGMA user_version` and an ordered list of migration functions.
- Before a migration that changes data, copy the database with `sqlite3.Connection.backup`
  (never `cp`; the database runs in WAL mode).
- First data migration: the importer's `links` rows of kind `task`. Where the row that carries the
  link is closed, copy it into `relations` as `same_as`, source `import`. Where it is still open,
  make a merge suggestion (§5.4) instead: a `same_as` between two open rows has no meaning here,
  and would send mentions away from an open task. Then stop reading that link kind anywhere.

### 5.4 Suggestions

A suggested link is not a row in `relations`. It is an `evidence` row of kind `link_suggestion`,
with `ref` holding `{kind, a, b, why, signal}`, judged through the existing `judge_evidence` batch
op. Accepting one is a batch holding the `link` (or `merge`) and the verdict, so it is undone as
one. A dismissed pair is remembered by its judged row and is never suggested again, whichever
signal finds it next.

## 6. Writing links: batch ops

New ops in `batches.OPS`, each with its own branch (the generic branch drops fields):

| Op | Shape | Effect |
|---|---|---|
| `link` | `{task_id, fields: {kind, other, why?}}` | Adds a `part_of` or `waits_on` link from `task_id` to `other`. |
| `unlink` | `{task_id, fields: {kind, other}}` | Soft-removes it. |
| `merge` | `{task_id, fields: {into}}` | `task_id` is the row that goes away (below). |

Also:

- `done` and `drop` accept an optional `fields.outcome`. `outcome` joins `SETTABLE`.
- `create` accepts `fields.ref` (a short name local to the batch) and `fields.links`
  (`[{kind, other}]`, where `other` is a task id or a `ref` created earlier in the same batch).
  `link` accepts a `ref` for either end. This makes "split this task into three steps" one batch
  with one Undo. `apply` already runs changes in order on one cursor, so earlier creates are
  visible; a ref to a create that was skipped is skipped with a reason.
- Chat's "Create task" today writes outside any batch. It moves to `batches.apply`, so a chat
  proposal can create and link in one undoable batch.
- A task created through a batch from the UI or a command is labelled as made by hand. Today
  `CREATED_SOURCE` labels every batch-created task "Claude"; the label must follow the batch
  source.

### Validation

`batches` is authoritative, because a proposal staged hours earlier is applied without being
re-validated. It checks, on the same cursor so that links made earlier in the batch count: the
kind; that both tasks exist; not a self-link; not a duplicate (an unchanged link is a no-op, as an
unchanged `set` is); the rules in §5.1, with the cycle check as a recursive query over active
`waits_on` links. A `waits_on` link to a task that is already closed is refused with a reason. A
`same_as` may point at a closed task.

### What is recorded

- One `relations` row carrying the `batch_id`.
- Two `changes` rows, one per task, field `link`, whose `new` value carries the relation id, the
  kind, the direction and the other task's id and name. Both are needed: History and the agent's
  `recent_changes` are read by task id, and the Log joins one task name per change.
- `edited_at` is bumped on both tasks.
- One undo entry `{relation_id, undo: "remove" | "restore"}`.

### Undo

`batches.undo` gains a branch for relation entries, and gains the ability to skip an entry with a
reason and report it (today an unknown entry raises `KeyError`, which the API turns into a 404
while leaving the transaction open and the write lock held; that path must be fixed in the same
commit that adds the op). Restoring a removed link is skipped, with the reason shown in the Undo
toast, if it would now duplicate an active link or create a cycle.

Undo today writes each old value back without looking at what is there now. History puts an Undo
on every entry, including old ones, so that must change: undo skips, with a reason, any field whose
current value is no longer the value that batch wrote. The owner's later edits are never undone by
undoing something older.

The same care applies to a text edit that waits in the tray. Today the page computes the whole new
text when an edit is staged, so applying it after a ripple has rewritten the task would silently
undo the rewrite. A staged text edit carries the text it started from; if the task's text has
changed since, the change is skipped with a reason. A note added to a task is appended by the
server at apply time, never sent as a whole new text.

### Merge

Confirming "same as" merges the duplicate into the survivor, in one batch:

- The duplicate is dropped with `closed_by = 'merge'`. No new status is introduced.
- Its links to outside things (mail threads, recordings, calendar events), its pending reminders
  and its links to other tasks move to the survivor. Each move is an undo entry.
- A move that would break a rule in §5.1 (a second goal, a cycle) is skipped with a reason.
- No mention row is written. `mentions` is append-only evidence and an undo could not take one
  back. The merge itself counts as activity on the survivor for the fade clock, derived from the
  `same_as` row, so undoing the merge removes it.
- If the duplicate has a due date and the survivor has none, the survivor takes it.
- A `same_as` row records it.
- From then on, what would have landed on the duplicate lands on the survivor, in these ways and
  no others: a later mention of the duplicate becomes a mention of the survivor, with the
  recording or thread linked to it; a Canvas close or change of due date for the duplicate's item
  is applied to the survivor; an instruction that names the duplicate's id acts on the survivor.
  Fields are never copied over: a recording synced again must not overwrite the survivor's name or
  date with its own guesses.
- Undo restores all of it.

The card that offers a merge says which row is kept. The default survivor is the row with an
outside identity that closes itself (a Canvas item); failing that, the older row.

Closing never cascades. Closing a goal does not close its steps, and closing the last step does
not close the goal. Both raise a Review card (§7.3).

## 7. What a link changes

### 7.1 One place computes neighbour facts

Score and lifecycle are pure functions of one task, and the score is computed at read time in
eight places (`api.py`, `planner.py`, `brief.py`, `chat.py`, twice in `mcp_server.py`,
`notion_mirror.py`, `__main__.py`). A link cannot reach them unless one shared step runs first.

Add `factotum/relations.py` with `annotate(conn, tasks)`. For the given tasks it loads the active
links once and attaches derived keys to each task dict:

- `waits_on_open`, `frees_open`: lists of open task ids.
- `goal_id`; `steps_open`, `steps_total`.
- `needed_by`: the earliest hard due date among the open tasks this one frees, followed through
  chains to a depth of five, safe against a cycle in old data; also a step's goal's hard due date.
- `freed_at`: when the last task it waited on closed.
- `neighbour_activity_at`: the latest mention or close among its steps.

Every caller of `scoring.score` goes through one wrapper that annotates first. A caller that
forgets would silently rank without links, so the Today list, the brief and the agent's tools
would disagree with no error. A test asserts that the surfaces agree.

The same step must run before the lifecycle functions, not only before scoring. `fade_at`,
`review_reasons` and `expire` read the annotated keys (§7.3), and several callers hand them raw
rows without scoring first: the nightly expire and the review push in `scheduler.py`, and the
Review counts in `brief.py`, `planner.py` and `__main__.py`. If only the scoring callers annotate,
the nightly expire fades waiting tasks for real, which is a write and not a display fault.

### 7.2 Scoring (all parameters in config under `scoring.links`)

With `scoring.links.enabled: false` every score is exactly what it is today; a test holds that.

- **A waiting task is capped** (default 30, the bottom band), unless it has a hard due date that
  is within two days or already past. A manual priority floor still wins. Reason shown: "waits on
  Task-N".
- **A task that frees a hard-dated task is scored as if due on that date**, on the scale for hard
  dates, using the earlier of its own date and the inherited one. The date is never written to the
  task; it appears as a chip, "needed for Oct 6". Reason shown: "frees Task-N, hard, Oct 6". A
  step inherits its goal's hard date the same way.
- **A task that frees others gets a bonus**: 5 per task freed, capped at 15.
- **Open steps do not make a goal waiting.** Only a `waits_on` link does. A goal is usually only
  partly broken into steps, and capping it would hide the work that has no step yet.
- "Waiting on a task" never goes into the `waiting_on` column. That column is for people: it earns
  a bonus in scoring, and the mail sweep reads names out of it.

### 7.3 Lifecycle and Review

- A waiting task does not fade: `lifecycle.fade_at` returns nothing while it waits. When it is
  freed, its fade clock starts from that moment.
- A step's activity keeps its goal from fading. The reverse does not hold. No mention rows are
  invented; `mentions` stays real evidence.
- `expire` is unchanged, including its rule that it only touches rows Review has shown.
- New Review reasons, computed inside `lifecycle.review_reasons` from the annotated keys (not
  passed as flags, because the evening push and the brief call the queue without flags):

| Reason | Card says |
|---|---|
| `freed` | "Free now: Task-N is done" |
| `blocker_gone` | "Task-N was dropped. Is this still needed?" (the task it waited on was dropped, faded or lapsed) |
| `steps_done` | "All steps are done. Close this, or is there more?" |
| `linked_changed` | "A linked task changed" (a ripple touched this task since it was last seen) |
| `link_suggested` | a suggestion card (§10) |

### 7.4 Today, the brief, reminders, the summary

- **Today.** A waiting task stays in its date group, dimmed, and is drawn directly under the task
  it waits on when both are in view. A task that frees a hard-dated task is drawn in that task's
  date group, with "do first: frees Task-N". A hard-deadline task is never hidden or moved out of
  its group because it is waiting.
- **Filters** on Today and Timeline: All, Linked, Ready now. "Ready now" hides waiting tasks.
- **Morning brief.** A row says "waits on Task-N" when it does. A section "Free since yesterday"
  lists tasks whose last blocker closed. Pure Python, no cost.
- **Reminders.** The body says "waits on Task-N" or "frees Task-N when done". A reminder is never
  suppressed because its task is waiting.
- **Today summary and planner.** The task rows they are given carry link facts ("waits on
  Task-9", "frees Task-12, 14", "2 of 5 steps open"), and the planner's verdict may name the task
  that frees the most. The summary's fingerprint (`summary.FP`) gains the set of open blockers, so
  a summary goes stale when something it names is freed. The fingerprint is compared positionally
  today; it must tolerate a stored fingerprint of the old length, or every summary reads as
  changed on the day this ships.

## 8. Ripples

### 8.1 When one runs

Python decides, never the model. A ripple is queued when an applied batch, from any source except
a ripple, does one of these to a task that has at least one link:

- closes it (done or drop);
- changes its name, text, due date, hard flag or outcome (text counts when it differs by more than
  whitespace);
- merges it.

A ripple never triggers a ripple.

Queued tasks sit in `state.ripple.pending`. The scheduler runs the ripple after a quiet period
(`claude.ripple.delay_seconds`, default 60), so several edits ripple once, or at once when an
outcome is saved. The API wakes the scheduler, as the Refresh button does. With no scheduler the
queue waits and the UI says so. Controls: `claude.ripple.enabled` (a Settings toggle),
`claude.ripple.model`, a budget per run, an hourly cap on runs, and the usual back-off after a
failure. Ripples are on when the key is missing (the owner's decision, 2026-11-11; one of the two
exceptions in §15), and Settings switches them off.

### 8.2 The run

A new run kind `ripple`, prompt `prompts/ripple.md`, no tools. Its input is the changed task
before and after (from the `changes` rows), its outcome if any, and each task one link away, plus
the other steps of the same goal: name, text, due date, who it waits on. The input is capped.

Its output is one entry per neighbour:

```
{"task_id": 429,
 "effect": "none" | "text" | "propose",
 "context": "<the new text, when effect is text>",
 "why": "<one line>",
 "proposals": [{"op": "...", "fields": {...}, "reason": "..."}]}
```

### 8.3 What is applied, and what waits

The owner's rule: a ripple may change a task on its own as long as the change is traceable; a
change to a commitment waits for a tap.

- **Applied automatically**, as a batch with source `ripple`: a rewrite of the task's text (the
  `context` field, shown as "Where this stands"). Nothing else.
- **Staged as a card on Review** ("Ripple suggests ... Accept / Dismiss", the path the planner's
  suggestions already use): anything else. A date, a close, a new task, a link, a reminder.
  Ripple proposals are never treated as proven.

The rewrite states where the task stands now. It is allowed to replace the old text because
History keeps every earlier version. Python guards it: the rewrite is refused if it is empty, over
the length cap, or loses any web address or any "Task-N" reference the old text had. A refused
rewrite becomes a dated note appended to the old text instead, and the run's log line says so.

### 8.4 The trail

The ripple's batch stores its cause (`{"kind": "ripple", "from": [...], "run": ...}`) and each
change stores the model's one-line reason. History shows both (§9). A ripple batch can be undone
on its own. When the batch that caused a ripple is undone, the ripple's batch is undone with it if
the text it wrote is still in place; if the text has changed since, History flags the entry
instead.

### 8.5 Outcome

"Done" records that a task finished, not what was learned, and the neighbours usually need the
second. So closing a task that others hang on offers one optional line, "How did it turn out?".
It never blocks: Done is instant, and the question follows.

- In the list: the toast after Done offers "Say how it went".
- On Review: the note saved with a Done or Drop decision is the outcome, and is no longer also
  appended to the task's text (it would ripple twice). A note saved with Keep or Later is still
  appended to the text, by the server (§6).
- From a reminder: the page shown after tapping Done asks it (§14).
- From a recording or a mail: the run that closes the task may supply it.

An outcome that arrives inside the quiet period is used by the first ripple. One that arrives
later queues another.

### 8.6 Safety

A task's text can contain a stranger's words, and the ripple reads task text. The damage such
text can do is bounded by construction: a ripple can only rewrite text and stage cards. It cannot
close, date, create, link, or copy an address from one task to another. The validator refuses a
ripple proposal that sets `counterpart` or `waiting_on`. The worst case is a wrong paragraph,
visible in History and undone with one tap.

### 8.7 Cost

Not yet measured. The run is small (a few task texts, no tools); the first real runs record their
cost in `runs` like every other kind, and the figure goes into `SPEC.md` §13 before the stage is
called done.

## 9. History

- **Data.** `GET /api/tasks/{id}` already returns a task's changes and the UI never calls it. It
  is extended with, per change: the batch's source, cause and undone flag; the change's note; for
  a link change, the other task's name and status. It returns the latest entries with a way to ask
  for earlier ones.
- **An entry** reads: when; who (you, automatic, ripple from Task-N, planner, a recording, mail,
  Canvas, a connector); what, in words; why, when a reason was stored. A text change shows before
  and after.
- **Undo** on an entry undoes its batch. When the batch touched other tasks too, the button says
  so ("Undo all 3 changes made together").
- **The Log tab** shows a ripple as one line, "Ripple from Task-N: 2 tasks updated", and a link
  change in words ("Task-12 now waits on Task-14").

## 10. Suggestions

Links are proposed far more often than typed. Three sources, cheapest first.

### 10.1 Python, no model (a scheduler job after the sweeps, and at create time)

- **Twin detector.** Two open tasks in the same project whose names carry the same number after
  normalising ("Assignment 03" and "Assignment 3"), due within 36 hours of each other, at least
  one from Canvas: suggest `same_as`. Series siblings whose numbers differ are never suggested.
  Name similarity alone is not a signal: course series produce hundreds of near-identical names.
- **Shared thread or event.** Two open tasks that follow the same mail thread or calendar event:
  suggest that they belong together. Python cannot know the kind, so the card offers the choices.
- **"Task-N" in text.** A task whose text names another task (the form "Task-N" only; "#N" is
  noise): suggest a link, with the kind guessed from nearby words ("merged into", "after",
  "blocked on", "unlocks", "sub-task", "step"). This also seeds the existing references, as cards,
  never applied directly.
- A shared recording or a shared person is shown as provenance and is not suggested as a link:
  about half of same-recording groups are unrelated, and the owner's advisor is on six open tasks.

Review shows at most a set number of link suggestions per session (`links.suggestions_per_review`,
default 5), so seeding does not flood it.

### 10.2 Runs that already happen

- **The planner** is given link facts and Python's candidates, and may propose `link`, `unlink`
  and `merge`. These always stage for Review, whatever evidence they cite. Its duplicate check for
  suggestions must treat a symmetric pair as one.
- **sync-file** gains a `links` list in its output, resolved with the same `tasks[i]` and task-id
  references its reminders use. A link the speaker stated ("I can't do X until Y") carries the
  sentence as evidence and rides in the recording's batch, as spoken instructions do today. A link
  the model inferred is staged. The prompt is given a short, reference-only list of recently
  closed tasks so that a follow-up can point at finished work; those ids can be link targets and
  nothing else.
- **mail-triage** gains two things. A suggested task may carry a link to an existing task. And a
  mail that is about an existing task, which today is dismissed as "already covered", becomes a
  card: "This mail is about Task-N. Follow its thread?"; one tap adds the thread link.
- No link ever comes from a Slack mention except as a card.

### 10.3 Filing a task made by hand

After a task is added with only a name, a small run with no tools proposes its project, effort, a
likely link and a likely twin. They appear on the row as dashed chips, one tap each. Nothing the
user typed is changed by this run. It has its own switch, `claude.filing.enabled`, on when the
key is missing (the owner's decision, 2026-11-11; one of the two exceptions in §15).

## 11. The agent's surface

The agent sees tasks through `task_get`, `tasks_query` and the attached-task text of a chat
prompt, none of which mention other tasks today.

### 11.1 Reading

- `task_get` gains `links` (at most 20: the kind as seen from this task, the other task's id,
  name, status and due date, the reason, who made it, when), `nearby` (provenance, at most 8) and
  up to five unjudged evidence rows, which the agent cannot see today.
- `tasks_query` gains a short `rel` string per row, present only when there is something to say
  ("waits on 412 · part of 380 · steps 2/5"), and the filters `ready`, `linked_to` and `goal`. It
  also becomes budget-aware: it drops rows until the result fits and reports how many it cut,
  instead of letting the result be cut mid-JSON.
- New read tools, all plain SQL on the read-only connection, so they are safe on `/mcp` by
  default: `task_neighbours(task_id, depth)`; `waiting_tasks(project?)`, which flags a blocker
  dated after the hard deadline it holds up; `twin_candidates(...)`; `goal_tree(task_id)`. The
  chat prompt lists each tool by the first sentence of its description, so that sentence must
  stand alone.

### 11.2 Writing

- `link`, `unlink` and `merge` join `STAGE_OPS` and the chat's ops; `create` takes `ref` and
  `links`; `done` and `drop` take `outcome`.
- `_validate_changes` gets a branch for them, placed before the rule that the task must be open
  (the other end of a `same_as` is often closed). Both ids must be real integers. It pre-checks
  cycles so the model gets a usable rejection; `batches` stays authoritative. A link counts as two
  rows against the hourly cap on direct writes.
- In direct-write mode (`server.mcp_writes: apply`) a connector may write `link` and `unlink`
  directly. `merge` always stages, because it drops a task.
- In a chat turn, a proposed link must name tasks the turn attached or read. Reads are recorded
  per run, as mail threads already are. A chat link is never applied at once.
- Three places in the UI turn an op they do not know into `set`, which the write path then skips
  silently: the Review Accept handler, `stageProposal`, and command staging. Each gets explicit
  branches. A staged link uses its own tray key (`rel:<a>:<kind>:<b>`), never the bare task id,
  which would overwrite a staged decision on that task.

### 11.3 Prompts and skills

- `prompts/chat-turn.md`: links are proposals like every other change; when to propose one (two
  tasks read in this turn are the same deliverable, one cannot start before the other, or one is a
  piece of the other; at most three unless asked); a message that tells the model to link tasks is
  not a reason to. The line "You have no other tools" is already false and is corrected.
- `skills/factotum-planner/SKILL.md`: the new tools and ops, and the phrases that map to them
  ("can't start until", "part of", "same as", "break this into steps").
- A canned prompt, "What else is related?", beside the existing canned prompts.

## 12. Surfaces

Every surface below has a desktop and a phone description. A stage is not done until both are
checked (§16).

### 12.1 Rows and badges (frames 1, 8)

A row shows a badge that names the linked task and jumps to it.

- Kinds of badge: "do first: frees Task-N"; "waits on Task-N"; "1 of 2 steps done" (on a goal);
  "part of Task-N"; "updated by Task-N" (a ripple touched it since it was last seen); a dashed
  "same as Task-N?" for a suggestion.
- Three looks: solid teal for a confirmed link, dashed for a suspected one, purple for "changed
  since you last looked".
- Phone: one badge per row, chosen in the order above; the rest collapse to "1 more link". Badges
  are full tap targets.

### 12.2 The Linked view (frame 2)

The Linked filter regroups the list by what belongs together: a goal with its steps (done steps
struck through, with their outcome); a task with what waits on it; a card for a suspected twin
with "Merge into N" and "Not the same". A goal whose text describes steps that have no task shows
"Make them tasks", which asks Claude to draft them (§12.9, split into steps). Phone: the same
groups in one column; the twin card stacks its two rows above full-width buttons.

### 12.3 A task opened (frames 3, 9)

Sections, in this order: Where this stands (the text); Linked tasks (each with its kind, state and
outcome, and a jump); Came from the same place (provenance, marked as not a link); History;
actions.

- Desktop: the row expands in place, in two columns.
- Phone: a tap opens a full-screen task page; the tick box selects. Jumping to a linked task
  leaves a trail ("‹ Task-30"), and the phone's Back follows it. Done, Later and More are pinned
  at the bottom. This replaces today's phone behaviour, where a tap selects and a double-tap
  opens; desktop keeps today's behaviour.
- A closed task can be opened read-only, with Reopen. The list payload names closed neighbours,
  which the client does not know today.

### 12.4 Review (frames 4, 10)

- The card shows the new reasons (§7.3), a "Since you last looked" block (what a linked task did,
  and that this task's text was updated, with "See what changed" and Undo), and ripple proposals
  waiting for a tap.
- The conversation with Claude lives on the card (§12.6).
- Phone: the four decisions are pinned at the bottom; the card scrolls above them.

### 12.5 Closing with an outcome (frames 5, 8, 12)

As §8.5. Frame 5 shows the question as a step before Done; the agreed behaviour is that Done is
instant and the question follows.

### 12.6 The conversation in place

One component, used on a Review card, under the box on Today, and in an opened task.

- A follow-up continues the same thread. Today every Ask from Review starts a new one.
- While Claude works, its status and latest text show in place.
- Every proposal is listed in place with a tick box: changes to this task, changes to other tasks,
  new tasks, links, mail drafts, and what Factotum rejected. Today only changes to the card's own
  task are staged, silently, and the rest are left in the Chat tab.
- "Stage all with this decision" puts the ticked proposals in the tray with the Review decision:
  one Apply, one Undo.
- Typing plain text into the Today box no longer switches to the Chat tab.
- The thread is still stored and still appears in Chat.
- A new text box here follows the `draft` / `typing()` / focus-list rule.

### 12.7 Adding a task by hand (frames 6, 11)

Two speeds.

- **Fast, no Claude.** Press `n`, or New task, and type one line:
  `add Email the lab the dataset fri #hardware ~S after 120`. Recognised parts: a date word, a
  `#project`, a `~effort`, `!` for a hard deadline, `after N` (waits on), `under N` (a step of).
  Anything not recognised stays in the name. A preview shows how the task will be filed before
  Enter. The task exists the moment Enter is pressed; the toast offers Undo.
- **Rich.** Say it in a sentence; Claude files it in place, links included (§12.6).
- Phone: a round button at the bottom opens a sheet with the name field and taps for date,
  project, effort and link, since typed shorthand is awkward on a phone keyboard. "Let Claude file
  it" takes a typed or dictated sentence.
- The empty Today tab says how to add a first task. Today it tells the user to run the Notion
  import.

A fault to fix first: for commands that take no date (`done`, `drop`, `top` and others), words
left over are ignored and every number becomes a target. `drop 12 duplicate of 14` stages a drop
of both. Leftover words must make such a command unrecognised.

### 12.8 Editing in place (frame 7)

The opened task is the editor: name, text, due date, hard deadline, project, effort, and who it is
for or who is awaited. The server accepts all of these today; the page offers only the due date.
Edits stage in the tray and apply like every other change, and appear in History.

- **Linking by hand.** "Link a task" finds a task by number or words (the Chat tab's task picker,
  generalised, and able to find closed tasks), then offers three plain choices: this task *is a
  step of it*, *waits on it*, or *is the same task*. Each linked task has Remove.
- **On a goal**, an "Add a step" line creates a step already linked, in the goal's project.
- **More**: Split into steps (Claude drafts them; one batch creates and links them), Duplicate,
  Merge into another.
- **Selecting two rows** offers Link in the bar for selected rows. On a phone that bar shows a
  short set of actions and More; it wraps onto several lines today.
- Phone: the same fields in one column, with the phone's own date and project pickers.

### 12.9 Finding any task

A search that covers open and closed tasks, with Open and Reopen. Nothing in the UI lists closed
tasks today, and a link to a finished task needs somewhere to go.

## 13. The installable app (stage 1)

### 13.1 What the spike showed (2026-11-10)

A throwaway spike added an install file, three icons and a few lines to the page head. It is in
the working tree, uncommitted. Results, behind the owner's Cloudflare sign-in:

- An iPhone ("Add to Home Screen") and desktop Edge both installed it and opened it full-screen,
  without browser bars. Neither asked for a second sign-in.
- The install file must be requested with the sign-in cookie
  (`crossorigin="use-credentials"`); it then arrives.
- On the iPhone the icon request never reached the app (the service log shows none), and the
  phone drew a letter instead. The phone fetches that icon without the cookie. **The iPhone icon
  is therefore inlined in the page**, which needs no request. This was changed in the spike after
  the first install and has not been re-tested on the phone.
- Android was not tested.

Stage 1 adopts the spike's files, properly.

### 13.2 What stage 1 adds

- **Addresses.** A task has a real address (`/t/<id>`) that the server answers with the app. A
  real path survives a sign-in redirect; a `#fragment` may not. The app listens for the address
  changing, so the phone's Back works; today the tab is written to the address and never read
  again.
- **A reminder's Open goes to the task.** Today it is hard-wired to the Today tab.
- **Signed out.** When requests start failing because the sign-in has expired, the app shows
  "Sign in again", which reloads at the same address. Today every action just fails, and an
  installed app has no reload button.
- **Fresh on return.** Coming back to the app reloads the lists, unless something is being typed.
- **Self-update.** There is no service worker in stage 1, so nothing is stored on the device and
  every launch loads from the server, as now. The one gap is an app left open for days: every API
  response carries a build stamp; when it differs from the stamp the page loaded with, the app
  reloads itself the next time it is brought to the front, or shows "Update ready" if something is
  typed or staged.
- **Phone structure.** Tap opens, tick selects (§12.3). One thing at the bottom of the screen at a
  time: staged changes if any, otherwise New task; selecting rows swaps in the short bar.

Stage 1 comes before links, ripples and New task exist, so it ships the frame they later fill.
The task page shows what the opened row shows today. The bottom of the phone screen shows staged
changes or nothing; New task arrives with stage 6. Linked tasks, History and the outcome line
arrive on the page with stages 4 and 5.

## 14. Notifications from the app (stage 2)

The owner's complaints about today's notifications: tapping one opens the ntfy app first; its
links land on Today, not on the task; they are used only as reminders.

- A service worker whose only job is notifications. It has no fetch handler, so it never stores or
  serves the app and cannot make it stale. It activates a new version at once.
- A key pair generated once on the server. It is a secret: never in git, never returned by
  `/api/settings`.
- Settings has "Turn on notifications on this device". The permission must come from a tap, and on
  an iPhone it is only possible from the installed app; Settings says so when the app is not
  installed. Each device is a row; a device whose push address has gone is removed.
- `notify` gains a choice of provider: ntfy, the app, or both. An existing configuration keeps
  ntfy until changed.
- A notification carries the task's address; tapping it opens the task page. The payload uses the
  declarative format newer iPhones show without running the script, and the script handles the
  same payload elsewhere.
- Every push shows a notification; none is silent.
- Where the platform has buttons (Android, desktop), Done and Snooze call the existing reminder
  token links. Those links stay behind the sign-in, as they are today on purpose: the same tokens
  travel through ntfy, and letting them past the sign-in would let anyone who can read the ntfy
  topic close tasks. So a button works while the device is signed in; when the sign-in has
  expired, it opens the task page instead. On an iPhone there are no buttons; the tap lands on the
  task page. The owner uses notifications as reminders and does not act from them, so nothing is
  lost.
- The page shown after Done from a reminder says the task is done and offers Undo, as today. From
  stage 4 it also says what is now free, and from stage 5 it asks the outcome line (frame 12).
- New with ripples (stage 5): "Task-N is free now", sent only for a task with a hard date.

## 15. A neutral starting configuration

Today, when `config/factotum.yaml` is missing, `config.load` silently uses
`config/factotum.example.yaml`. That file is the owner's own setup. A newcomer who skips or half
edits it gets the owner's accounts seeded into Settings, hourly failures from integrations that
are on, and a morning brief addressed to the owner. The owner is not affected: their own file
exists.

- **No silent fallback to an owner-shaped file.** With no `config/factotum.yaml`, the app starts
  from a neutral starter and says so: one line in the log, and a banner in the UI ("Starter
  settings in use").
- **The starter** (`config/factotum.starter.yaml`): no name, no addresses, the machine's time
  zone, three generic projects of equal weight, every source and sink off, the brief off, the
  planner off, the automatic summary off, no notification provider, no public address, writes from
  connectors staged.
- **The example** becomes a reference with placeholders only (`example.edu`, `YOUR_DATABASE_ID`)
  and marks the values that are taste (the fade periods, every scoring number, the brief time).
- **The tests come first.** The suite loads the example as its fixture and asserts on its
  projects and accounts. The current example is copied to `tests/fixtures/` and the tests are
  pointed at the copy, in a commit of its own, before the example is touched.
- **Absent means off** for the scheduled paid runs and anything that sends mail. The morning brief, the
  planner, the automatic summary and the notes sink are on today when their key is missing; their
  absent-key defaults flip to off. The runs this design adds (ripples, filing) are the two exceptions
  (the owner's decision, 2026-11-11): on unless switched off, because each runs only after something the
  owner did, costs cents (about 3 cents a ripple run, about 4 cents a filed task) and writes nothing
  History cannot undo. Before this ships, the owner's effective settings are compared before and
  after and must be identical.
- **A failed brief must not re-run the planner.** Today the planner runs first, a failed send
  leaves the day unmarked, and the job retries every hour, running the planner again each time.
- **Accounts are seeded once**, from the user's own file, not whenever the table is empty.
- **Settings shows the truth.** A switch shows the value the code will use, including its default,
  and changing one control saves that one key. Today the first change saves every control shown,
  after which edits to those keys in the YAML file are ignored.

## 16. Stages

Each stage ships desktop and phone together, leaves the tests green, and updates `SPEC.md`,
`CLAUDE.md` and `docs/SETUP.md`. Stage 3 is independent and may be built at any point.

| Stage | Contents |
|---|---|
| 1. App shell | §13; the command fault in §12.7; the brief retry fault in §15 |
| 2. Notifications | §14 |
| 3. Neutral start | §15 |
| 4. Links | §5, §6, §7, §9, §11, §12.1 to §12.3; migrations |
| 5. Ripples | §8, §12.5, the Review parts of §12.4, "free now" notifications |
| 6. In place, by hand | §12.6 to §12.9 |
| 7. Suggestions | §10 |
| 8. App extras | a count of Review items on the app's icon; long-press shortcuts to New task and Review |

`SPEC.md` §2 says every change waits in a pending batch until the user applies it, with automatic
closes as the one exception. A ripple's text rewrite (stage 5) and a task that exists on Enter
(stage 6) are two more exceptions, and §2 is reworded when they land.

Checks that apply to every stage with a surface:

- On a phone-sized screen (390 px wide) and in the installed app: every control is reachable with
  a thumb, nothing scrolls sideways, Back does what it should, and a text box keeps its text
  through a re-render.
- With `scoring.links.enabled: false` and no links in the database, every list, score and brief
  is identical to today's.
- Every new write is undoable, appears in the Log in words, and appears in History.

## 17. Who decided what

**The owner decided:**

- Linked tasks should say when one of them is finished, carry badges with clickable task numbers,
  and be filterable as a group.
- Linked tasks' text should be updated intelligently when a linked task is completed or changed.
- Automatic changes are fine as long as they are traceable after the fact. A ripple may rewrite
  text on its own; a change to a commitment waits for a tap on Review.
- Prefer usefulness and richness over saving Claude runs.
- The conversation with Claude should live on the Review card.
- Manual creation and editing must exist and be designed with care.
- The phone is a first-class surface; the proposed phone pages (frames 8 to 12) are approved.
- The app should be installable and self-updating, and should send its own notifications.
- New users must start from a neutral configuration; that belongs in this design.
- Frames 1 to 7 as drawn, including the "Ready now" filter.

**Claude's calls, for the owner to overrule:**

- Three kinds only; "related" is provenance, not a link.
- One level of steps; one goal per task.
- A waiting task is capped and dimmed, never hidden; a hard deadline is exempt near its date.
- Open steps do not make a goal waiting.
- A waiting task does not fade.
- Reminder links stay behind the sign-in; a notification button that cannot reach them opens the
  task instead.
- Every scheduled paid run is off when its setting is missing; ripples and filing are on unless
  switched off (2026-11-11), because each runs only after something the owner did.
- Undo never overwrites a later edit, and a staged text edit never overwrites a ripple.
- A merge writes no mention, and nothing is copied onto the survivor except a missing due date.
- A note saved with Done or Drop on Review is the outcome.
- Urgency is inherited from a hard-dated task that is being held up; no date is ever written.
- A ripple may rewrite only the text; a rewrite that loses an address or a task reference becomes
  an appended note.
- A ripple never triggers a ripple. Undoing a cause undoes its ripple when the text is untouched.
- The outcome question never blocks Done.
- Merge keeps the Canvas row, or else the older one, and always goes through a tap.
- A connector in direct-write mode may link and unlink, never merge.
- A new task exists on Enter; edits stage.
- On a phone a tap opens a task and the tick box selects.
- Task addresses are real paths.
- No service worker until notifications, and then one that only handles notifications.
- The iPhone icon is inlined.
- Links are not mirrored to Notion.
- The stage order.

## 18. Left to the builder

- Whether `app.js` is split into native modules before this work. If it is, every new file needs
  the cache stamp that `api.py` gives `app.js` and `app.css` today.
- How the phone task page is mounted, given that `render()` rebuilds the panel.
- The exact detection of an expired sign-in.
- Where the notification key pair is stored, within the rule in §14.
- Whether push messages are encrypted with a library or with the `cryptography` package that is
  already installed.
- The caps on a ripple's input and on a rewrite's length.
- How Python normalises a number in a task name for the twin detector.
- The wording of every label, within the vocabulary in §4.
- Whether to add automated browser checks; there are none today.
