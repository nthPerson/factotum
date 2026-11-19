# Review on the server: items, decisions, changes (for a spoken Review session)

Research only, 2026-11-19. Nothing in the repository was edited; the live database was opened read-only
(`file:data/factotum.db?mode=ro`, `PRAGMA query_only`). One experiment (Keep on three tasks) ran on a
backup-API copy in the scratchpad (`voice-review/probe/copy.db`), never on the live file. `.env` was not
read (`config.load_dotenv` was replaced by a no-op in the probe scripts).

Evidence labels used below:
- **[verified today]**: run or read against the live DB / a copy today.
- **[read in code]**: read in the source at the line given; not executed.
- **[not reproduced]**: a behaviour inferred from code that I did not see happen in a browser.

Line numbers are for commit `75d2c77` (the clean `main` at the start of this run).

---

## 0. Ten facts a designer must not get wrong

1. **A Review "decision" is not one op.** The page turns Done / Keep / Later / Drop into the op *plus* a
   `judge_evidence` (verdict `reviewed`) for every pending evidence row on the card, plus an `add_note` for a
   note (`web/app.js:4198-4229`, the judges at `:4227`). A bare `keep` leaves "Thread updated", "Planner
   suggests" and "Suggested link" cards in the queue. **[verified today on a copy]**: after `keep` on
   Task-477 / 419 / 449, 419 left the queue, 477 stayed (`link_suggested`), 449 stayed
   (`thread_updated`, `planner`).
2. **That rule lives only in the browser.** No Python function says "what a Review decision on item X is".
   A voice loop that does not run inside the page must get it from a new server function.
3. **The tray is per browser.** Staged changes are a `Map` persisted to `localStorage['factotum.staged']`
   (`web/app.js:63, 529-538, 575, 590-594`). The phone and the desktop never see each other's tray. The
   only server-side "staged batch" store is the `proposals` table (skills / connector; 0 rows live), whose
   Apply forces the batch source to `skill` / `connector` (`api.py:1078`).
4. **Not everything on Review is staged today.** Create on a suggestion card writes at once (one `review`
   batch, `api.py:638-723`); Undo under "Since you last looked" writes at once (`app.js:4002-4006`); "Show the
   message in Mailbird" calls a Mailbird *write* tool, `select_conversation` (`api.py:737`). Dismiss on a card,
   and every task-card decision, are staged.
5. **`GET /api/review` writes.** It records the day's link-suggestion set and stamps `last_offered_at` on the
   first `review_cap` (15) task rows; `expire` fades / lapses only rows stamped that way
   (`api.py:547-548, 585-591`; `lifecycle.py:192-214`). Never call it for a number or a poll.
6. **There is no session, cursor or "current item" on the server.** The page's current item is "the first
   item of the loaded page whose task id is not a key in the tray" (`app.js:1933, 1945`).
7. **Reading a task normally marks its neighbours seen** (clears "A linked task changed"). Use
   `GET /api/tasks/{id}?peek=1` to answer questions (`api.py:396-420`).
8. **Derived facts are already on the item.** `_review` builds items from `decorate_all` →
   `relations.scored` → `relations.annotate` (`api.py:304-315, 549`); the spoken-card builder must read
   `item["task"]["rel"]`, `item["texts"]`, `item["since"]`, never re-derive links.
9. **A chat turn is too slow and too costly per utterance.** Review-card turns: 10 measured, mean 10.7 s
   (2.8-17.9 s), mean $0.065 **[verified today, `chat_messages` joined to `chat_threads.origin='review'`]**.
   Shorthand ("done", "keep", "later Friday", "next") must be parsed deterministically; Claude only for
   questions and free-form edits.
10. **"Pin to Top" and "Mark long-term" on a Review card are keyed like decisions** (`stage(id, …)`, bare
    id: `app.js:4203 → 595-606 → 590-594`), so the card leaves the page as if decided, without
    `last_reviewed_at`, and a later Keep would replace the pin in the tray. **[read in code, not
    reproduced]**. A voice design that copies the page's key scheme inherits this.

---

## 1. Every kind of Review item

Review shows two families: **task cards** (an open task with one or more *reasons*) and **suggestion
cards** (evidence with no task: a task that does not exist yet).

### 1.1 How the queue is built

- `review_queue.queue(conn, cfg, today, open_tasks, flags)` (`factotum/review_queue.py:16-26`): open rows
  annotated by `relations.annotate` (or the API's scored rows), flags from `mail_sweep.review_flags`, then
  `lifecycle.build_review_queue(..., cap=10**9)`.
- `lifecycle.review_reasons` (`lifecycle.py:73-133`) decides the reasons of one task; returns `[]` for a
  closed task or one snoozed into the future (`:76-80`). Reasons are returned in `REVIEW_ORDER`
  (`:14`, `:133`).
- `lifecycle.build_review_queue` (`:177-189`) keeps tasks with ≥1 reason as `{task, reasons, primary}` and
  sorts by `(REVIEW_ORDER.index(primary), task.due or "9999", -score)` (`:187`).
- `mail_sweep.review_flags` (`mail_sweep.py:374-384`) turns *unjudged evidence with a task* into flags:
  `thread_updated` / `reply_from_waiting` / `maybe_reply_from_waiting` → `thread_updated: True`;
  `slack_mention` → `mentioned: True`; `proposal` → `planner: <label>` or, when `account = 'ripple'`,
  `ripple: <label>`.
- Suggestion cards: `review_queue.is_card` (`:29-30`) = unjudged evidence with `task_id IS NULL` and kind in
  `SUGGESTION_KINDS = ("proposal", "slack_mention")` (`:13`).

### 1.2 Task-card reasons (in queue order)

| # | reason | label (`lifecycle.py:15-32`) | condition (`lifecycle.py`) | cleared by | live now? |
|---|---|---|---|---|---|
| 1 | `last_call` | Last call · hard deadline passed | hard + due, past `due + last_call_days` (7), not reviewed since (`:84-86`, `last_call_at` `:65-70`) | any decision (sets `last_reviewed_at` ≥ that date) | no |
| 2 | `thread_updated` | Thread updated | unjudged `thread_updated`/`reply_from_waiting`/`maybe_reply_from_waiting` evidence (`mail_sweep.py:378-379`) | **judging the evidence** (the page judges `reviewed` with any decision) | yes (Task-449) |
| 3 | `mentioned` | Mentioned in Slack | unjudged `slack_mention` evidence on the task (`:380-381`) | judging the evidence | no |
| 4 | `planner` | Planner suggests | unjudged `proposal` evidence (planner / mail triage) on the task (`:382-383`) | judging (Accept → `applied`, a decision → `reviewed`) | yes (Task-424, 449) |
| 5 | `ripple` | Ripple suggests | unjudged `proposal` with `account='ripple'` | judging | no |
| 6 | `freed` | Free now | `rel.freed_at` set, latest closed waited-on task is `done`, not reviewed since (`:96-101`) | any decision | no |
| 7 | `blocker_gone` | A task it waited on closed | a waited-on task dropped/faded/lapsed after the last review (`:102-103`) | any decision | no |
| 8 | `steps_done` | All steps done | `steps_total > 0`, `steps_open == 0`, not reviewed since the steps' last activity (`:107-108`) | any decision | no |
| 9 | `linked_changed` | A linked task changed | `rel.changed` (a caused batch after `neighbours_seen_at`, `relations.py:369-374`) | a decision (`_write_fields` stamps `neighbours_seen_at` when `last_reviewed_at` is written, `batches.py:565-577`) or opening the task without `peek` | yes (Task-419) |
| 10 | `link_suggested` | Suggested link | `rel.suggestions` non-empty (only today's offered set, `relations.py:375-391`) | **only answering the suggestion** (link / merge / dismiss); Keep does not | yes (Task-477, 421) |
| 11 | `snooze_over` | Back from Later | `snoozed_until ≤ today`, not reviewed since (`:115-116`) | any decision | no |
| 12 | `new` | New · not yet reviewed | `last_reviewed_at IS NULL` (`:120-121`) | any decision except Done/Drop (they close it) | yes (10 tasks) |
| 13 | `slipped` | Soft date slipped | soft due in the past, not reviewed since the due (`:122-124`) | any decision | yes (Task-457) |
| 14 | `fading` | Fading soon | backlog fade date within `fade_warn_days` (3) (`:125-128`) | any decision (restarts the quiet clock, `:51-59`) | yes (Task-38, 16th, off page 1) |
| 15 | `back` | Back · mentioned again | `flags["back"]` | — | **dead: nothing sets it** |
| 16 | `reminder_missed` | Reminder missed | `flags["reminder_missed"]` | — | **dead: nothing sets it** |

`back` and `reminder_missed`: `grep` over `factotum/*.py` finds them only in `lifecycle.py:14, 30-31, 75,
129-132`; `review_flags` never produces either key **[verified today]**. SPEC §5 promises *Back* on
resurrection (`SPEC.md:214-215`); it is not wired.

Link reasons carry a server sentence in `item.texts` (`lifecycle.reason_texts`, `:136-174`): `freed` "Free
now: Task-N is done", `blocker_gone` "Task-N was dropped. Is this still needed?", `steps_done` "All steps are
done. Close this, or is there more?", `linked_changed` "A linked task changed (Task-N)", `link_suggested`
"Suggested: frees Task-N?". All other reasons have only the label.

### 1.3 Suggestion-card kinds (no task yet)

| card | how to tell (`api.py:578-579`, `app.js:1977, 1984`) | "why" line | Source on Create (`api.py:711`) | live now? |
|---|---|---|---|---|
| From your mail | `kind='proposal'`, `mail` non-null (mail triage, SPEC §8.5) | From your mail | Mail | 5 (ids 156, 162, 167, 170, 182) |
| Planner suggests | `kind='proposal'`, `mail` null, `by` null | Planner suggests | Planner | 1 (id 191) |
| Ripple suggests | `kind='proposal'`, `by='ripple'` (`account='ripple'`) | Ripple suggests | Ripple | 0 (none ever in `evidence`) |
| Mentioned in Slack | `kind='slack_mention'`, task_id NULL | Mentioned in Slack | Slack | 0 now (the planner judges them `staged` and turns them into Planner cards: 6 of 10 historical rows have verdict `staged`, e.g. id 189 → card 191) |

Not Review items, for completeness: `mail_new` (the triage's input), `link_suggestion` (shown *inside* a task
card as its `link_suggested` reason, listed under both ends: `relations.pending_suggestions`,
`relations.py:224-237`), `filing` (dashed chips on rows), `confirmation`, `ics`, `sent_to_counterpart`
(planner inputs). Evidence on a task that has since closed stays unjudged and invisible (ids 2, 3, 4: planner
"drop" proposals on Tasks 397 / 338 / 246, all already dropped by Review) **[verified today]**; a count of
"what is left" must not count those (the existing `card_count` does not, `review_queue.py:33-37`).

### 1.4 The JSON shape of `GET /api/review`

Top level (`api.py:592-593`) **[verified today via TestClient on a copy]**:

```
{ "today": "2026-11-19", "last_review_at": "2026-11-17", "cap": 15, "total": 16,
  "labels": {<reason>: <label>, ...},              // REVIEW_LABELS
  "items": [ <task item> × min(cap, total) ],
  "suggestions": [ <card> × every unjudged card ] }  // not capped
```

Task item keys: `task, reasons, primary, texts, link_suggestions, flags, evidence`, plus `since` only when
`linked_changed` is a reason (`api.py:557-559`). `task` is the whole `tasks` row plus `score, priority,
score_reasons, tier, longterm_effective, fade_at, last_call_at, days_until_due` (`api.py:304-315`) and `rel`
**only when non-empty** (`ship_rel`, `api.py:317-321`; `relations.compact`, `relations.py:444-452`).
`evidence[]`: `{id, kind, label, reason (=excerpt), change (parsed ref for a proposal, else null), at, by}`
for unjudged evidence on the task except `link_suggestion` and `filing` (`api.py:562-575`).
`flags`: `{thread_updated: true, mentioned: true, planner: "<label>", ripple: "<label>"}` (any subset).

Card keys: `id (evidence id), kind, label, reason, at, fields (what Create would make), mail
({ref, account, subject, sender, at} or null), links ([{kind, other, other_name}]), by ("ripple"|null)`
(`api.py:578-582`, `_suggestion_fields` `:620-636`).

### 1.5 One real example of each live kind (shortened; addresses removed)

`thread_updated` + `planner` (Task-449):
```json
{"task": {"id": 449, "name": "Prepare and rehearse ZIP Launchpad funding pitch (if accepted)",
  "project": "ZIP Launchpad", "due": "2026-11-20", "hard": 0, "effort": "M", "source": "Plaud",
  "why_now": "Your Seed Fund presentation is booked for tomorrow at 2 PM, so rehears…",
  "score": 90, "priority": "P0", "score_reasons": ["soft, due in 1d", "ZIP Launchpad high +15", "owed to Lindsey +10"],
  "last_reviewed_at": "2026-11-13", "days_until_due": 1, "counterpart": "Lindsey"},
 "reasons": ["thread_updated", "planner"], "primary": "thread_updated", "texts": {},
 "flags": {"planner": "Task-449 → due Fri 2:00 PM (Seed Fund slot booked)", "thread_updated": true},
 "evidence": [
  {"id": 190, "kind": "proposal", "label": "Task-449 → due Fri 2:00 PM (Seed Fund slot booked)",
   "reason": "The booking confirms the Seed Fund presentation is Nov 20 at 2:00 PM.",
   "change": {"task_id": 449, "op": "set", "fields": {"due": "2026-11-20T14:00", "due_is_datetime": 1}}},
  {"id": 192, "kind": "thread_updated", "label": "Appointment booked: In Flight Seed Fund Presentations (…) @ Fri Nov 20, 2026 2pm",
   "reason": "Lavin Entrepreneurship Center", "change": null}]}
```

`planner` as a mail card on a task (Task-424; mail triage's `follow_thread` proposal):
```json
{"reasons": ["planner"], "flags": {"planner": "This mail is about Task-424. Follow its thread?"},
 "task": {"id": 424, "name": "Attend SDSU Entrepreneurship Mixer (ZIP Launchpad) at Aztec Lanes", "due": "2026-11-19T17:00", "hard": 1,
          "rel": {"links": [{"kind": "part_of", "dir": "in", "other": 457}], "steps_open": 1, "steps_total": 1}},
 "evidence": [{"id": 173, "kind": "proposal", "label": "This mail is about Task-424. Follow its thread?",
   "change": {"op": "follow_thread", "task_id": 424, "fields": {"conversation_id": "d0905f3f-…", "account": "<address>",
              "subject": "Reminder: EI Mixer is TOMORROW 11/19!", "at": "2026-11-18T10:39:36"},
              "mail": {"ref": "d0905f3f-…", "subject": "…", "sender": "ZIP Launchpad <<address>>", "at": "…"}}}]}
```

`linked_changed` (Task-419), with `since` (`ripple.since`):
```json
{"reasons": ["linked_changed"], "texts": {"linked_changed": "A linked task changed (Task-418)"},
 "task": {"id": 419, "name": "Submit full paper to Smart & Sustainable Built Env. conference (Delft)", "due": "2027-03-01", "hard": 1,
          "rel": {"links": [{"kind": "part_of", "dir": "in", "other": 418}, {"kind": "part_of", "dir": "in", "other": 276}],
                  "steps_open": 2, "steps_total": 2, "changed": {"by": 418, "at": "2026-11-19T19:49:41+00:00", "batch_id": 125}}},
 "since": {"batch_id": 125, "undone": false, "by": [{"id": 418, "name": "Submit abstract to …", "status": "open", "did": "changed", "what": ["text"]}],
           "text": {"before": "Full-paper deadline that follows …", "after": "Full-paper deadline that follows …",
                    "note": "Task-418 now says simulation results are enough and gives the format c…"}, "noted": false}}
```

`link_suggested` + `new` (Task-477):
```json
{"reasons": ["link_suggested", "new"], "texts": {"link_suggested": "Suggested: frees Task-480?"},
 "task": {"id": 477, "name": "Send Caltrans simulator scenery screenshots for terrain selection", "due": "2026-11-19", "hard": 1, "effort": "S"},
 "link_suggestions": [
  {"evidence_id": 186, "kind": "waits_on", "other": 480, "dir": "in", "signal": "recording",
   "why": "The scenery can only be applied after the screenshots are sent and Geo…", "other_name": "Apply selected scenery to the simulator across all drives"},
  {"evidence_id": 187, "kind": "part_of", "other": 474, "dir": "out", "signal": "recording",
   "why": "The exact scenarios, which depend on the terrain choice, must be final…", "other_name": "Complete Caltrans ODA IRB initial submission (iMedRIS form)"}],
 "evidence": []}
```

`new` (Task-481): `{"reasons": ["new"], "task": {"id": 481, "name": "Deliver Caltrans Task 4 by Nov 30", "due": "2026-11-30", "hard": 1, "effort": "L", "why_now": null, "context": "Task 4 is nearly complete and was reported on track…"}, "evidence": []}`

`slipped` (Task-457): `{"reasons": ["slipped"], "task": {"id": 457, "name": "Send ZIP a Car Sounds job description to share at the mixer", "due": "2026-11-17", "hard": 0, "days_until_due": -2, "rel": {"goal_id": 424, "needed_by": "2026-11-19", "needed_by_via": "goal"}}}`

`fading` (Task-38, item 16, beyond page 1): name "Generate Car Sounds SOPs and reference materials", no due, `last_reviewed_at` 2026-11-01.

Mail suggestion card (162):
```json
{"id": 162, "kind": "proposal", "label": "create: Book Lavin Seed Fund presentation slot for Car Sounds (Fri Nov…",
 "reason": "The committee asks the owner to schedule a slot for a fixed in-person …",
 "fields": {"name": "Book Lavin Seed Fund presentation slot for Car Sounds (Fri Nov 20, 2-4…", "project": "ZIP Launchpad",
            "hard": 1, "effort": "XS", "due": "2026-11-20", "area": "Work", "context": "…"},
 "mail": {"ref": "d548b4e3-…", "account": "<address>", "subject": "Seed Fund Presentation – This Friday, Nov 20",
          "sender": "Lavin Entrepreneurship Center <<address>>", "at": "2026-11-17T18:53:10"},
 "links": [{"kind": "part_of", "other": 449, "other_name": "Prepare and rehearse ZIP Launchpad funding pitch (if accepted)"}], "by": null}
```

Planner suggestion card (191): `{"id": 191, "kind": "proposal", "label": "Create: BDA 696 paper draft due Nov 25", "reason": "A teammate's Slack message gives a new deadline that has no task.", "fields": {"name": "Submit BDA 696 project paper draft", "due": "2026-11-25", "hard": 0, "project": "BDA 696"}, "mail": null, "links": [], "by": null}`

Not live today (no example possible): `last_call`, `mentioned`, `ripple`, `freed`, `blocker_gone`,
`steps_done`, `snooze_over`, Ripple card, Slack card. Their shapes are the same keys; a Slack card's `fields`
are `{name (message text ≤80), project (workspace's), context (excerpt + permalink), source_link}`
(`api.py:624-628`).

---

## 2. Decisions per kind and the batch ops each becomes

All ops are `batches.OPS` (`batches.py:19-20`); settable fields `SETTABLE` (`:14-18`). Page staging keys are
from `stageKey` (`app.js:861-870`): bare task id = "the decision" (and is what `?exclude=` sends,
`app.js:1838`); `<id>:due`, `<id>:r`, `<id>:note`, `<id>:a<eid>`, `<id>:c<eid>`, `e:<eid>`, `rel:a:kind:b`.

### 2.1 Every task card: the four decisions (`reviewAct`, `app.js:4198-4229`)

| decision (key) | change(s) staged | server effect |
|---|---|---|
| **Done** `d` | `{task_id, op:"done"}`, or with a note ≤300 chars `{op:"done", fields:{outcome}}` (`:4214-4215`); a longer note → `add_note` instead (`:4213, 4223`) | `_resolve`: status done, completed_at, closed_by, top_rank null (`batches.py:179-180`); outcome cut to 300 (`:195-196`). Ripple queued for linked tasks (`:249-250`) |
| **Drop** `x` | same with `drop` | status dropped (`:181-182`) |
| **Keep** `k` | `{op:"keep"}` + note → `add_note` | `last_reviewed_at = today` (`:191-192`); `neighbours_seen_at` stamped (`:565-577`) |
| **Later** `s` opens the menu; Tomorrow / Friday / Next week / Pick a date | `{op:"set", fields:{snoozed_until: D, last_reviewed_at: today}}` (`:4209, 4218-4221`) + note → `add_note` | never touches `due` (SPEC §5, `SPEC.md:258-259`) |
| with **every** decision | `judge_evidence {evidence_id, verdict:"reviewed"}` for each pending evidence on the card not already staged (`:4227`) | `_judge_evidence` (`batches.py:582-607`): a `changes` row (field `evidence`, task 0 when none), undoable |

A note with Keep / Later is `add_note` (dated line appended at apply time, `batches.py:517-522`). Applying with
the tray's Apply is `POST /api/batches/apply {source:"review", changes}` (`app.js:632`) then `POST
/api/review/finish` (`:651`), unless the Apply held only the editor's edits (`editsOnly`, `:646-647`).

### 2.2 Extras on every task card (not decisions)

| control | staged as | note |
|---|---|---|
| ★ Pin to Top / Mark long-term | `stage(id, 'pin_top' | 'unpin' | 'set'{longterm})`, **bare id key** (`app.js:4203, 605-606, 590-594`) | see fact 10: hides the card and is replaced by a later decision **[read in code, not reproduced]** |
| Set / change / remove due | `{op:"set", fields:{due}}` under `<id>:due` (`:540-543`) | removal clears `hard`, `due_is_datetime` (`batches.py:532-536`) |
| Set reminder | `{op:"add_reminder", fields:{fire_at, label, repeat}}` under `<id>:r` (`:557-566`) | a row of its own, undone with the batch (`batches.py:506-515`) |
| The editor (2026-11-18): name, where it stands, why now, due, project, effort, hard, who; links with Remove; Link a task; Add a step; Split / Duplicate / Merge | `set` (text edits carry `expect`, `app.js:897-902`; `batches.py:537-542`), `link` / `unlink` (`rel:` keys), `merge`, `create` | editor Apply is not a decision (`app.js:646`) |
| Ask Claude about this task (origin `review`) | proposals from `CHAT_OPS` (`chat.py:46`: set, done, drop, reopen, pin_top, longterm, add_reminder, add_note, create, link_thread, link, unlink, merge; **no `keep`**), "Stage all N with this decision" | a paid turn (~$0.065, ~11 s) |

### 2.3 Reason-specific controls

| reason | control | changes |
|---|---|---|
| `planner` (a proposal of op done/drop) | Accept (`app.js:3975-3989`) | the proposal's op **is** the decision (bare id key) + `judge applied` + the other evidence `reviewed` (`:3978-3981`) |
| `planner` (set / pin_top / longterm / add_reminder / link / unlink / merge / add_note) | Accept | an extra under `<id>:a<eid>` / `<id>:c<eid>` / `<id>:r` + `judge applied` (`:3983-3988`); **the card still needs a decision** |
| `planner` as a mail card on a task (`follow_thread`) | Follow its thread / Not about this (`:3993-4000`, `mailAboutHtml` `:1868-1874`) | `follow_thread {conversation_id, subject, account, at}` + `judge applied`; or `judge dismissed` |
| `ripple` | Accept / Dismiss (`rippleLineHtml` `:2181-2191`) | as Accept above (a text `set` carries `expect`); Dismiss = `judge dismissed`. A stale text offers Dismiss only |
| `linked_changed` | See what changed; **Undo (writes at once)** (`:4002-4006`) | `POST /api/batches/{id}/undo` |
| `link_suggested` | Link / is a step of it / waits on it / is the same task / Merge into N / Not related (`linkSuggestionsHtml` `:1885-1898`, `linkSuggestionAct` `:1901-1914`) | `link {kind, other}` or `merge {into}` (staged with `ev`, the server judges the pair in the same batch, `batches._judge_suggestions` `:424-430`); Not related = `judge dismissed`. **Then a decision is still needed** |
| `thread_updated`, `mentioned` | none of their own (the card shows only the label; the evidence's subject / sender are **not drawn**, `app.js:1967` filters `e.change`) | judged `reviewed` with the decision |

### 2.4 Suggestion cards (`suggestionCard`, `app.js:1976-2001`)

| decision | what happens |
|---|---|
| **Create task** | `POST /api/review/suggestions/{id}/create` **at once** (`app.js:4017-4024`), body `{fields, links}` only when the draft was edited. Server (`api.py:655-723`): one `review` batch = `create` (source_key `mail-suggestion:<id>` / `planner-suggestion:<id>` / `ripple-suggestion:<id>` / `slack-mention:<id>`, `created_source` Mail / Planner / Ripple / Slack) + the card's links (≤3, or the owner's ≤6) + a mail card's `follow_thread` + `judge_evidence applied`. A second tap answers `already`; Create after the toast's Undo reopens the same task. Answers `{task_id, batch_id, skipped, reopened, already}` |
| **Dismiss** | staged `judge_evidence {evidence_id, verdict:"dismissed"}`, `task_id: null`, key `e:<id>` (`:4016`) |
| Edit before creating | a draft in `draft.sg[<id>]`; nothing staged (SPEC `:1130-1136`) |
| Ask Claude to change this draft | origin `suggestion`, one thread per card (`chat_threads.card_id`); a reply's `create` → "Use on this card" |
| Show the message in Mailbird (mail cards) | `POST /api/review/suggestions/{id}/open` → Mailbird `select_conversation` via `call_write` (`api.py:725-742`) |

### 2.5 Apply, undo, and what the server refuses

- `POST /api/batches/apply` (`api.py:458-478`): refuses source `ripple` (`:460`); strips `source_key`,
  `created_source`, `source_link`, `account`, `reminder_status` from every change (`:462`); takes
  `chat._CREATE_LOCK` then `commit_lock` (`:464`); returns `{batch_id (None if all skipped), applied, skipped:
  [{task_id, why}], created, refs, ripple_queued}` (`batches.py:214-269`).
- `POST /api/batches/{id}/undo` (`api.py:480-488`): `batches.undo` (`batches.py:610-645`) skips a field
  changed since; when everything is skipped it writes nothing and answers `undone: false`.
- Source `review` makes a create's Source "Manual" (`batches.created_source`, `:28-32`) unless Python sets
  `created_source` (the suggestion route does).
- `POST /api/commands` (`api.py:422-427`) parses the grammar (`commands.py`) into changes and applies
  nothing. Its verbs (`commands.py:30-38`) need task numbers and have **no `keep` and no Review-style
  `later`**: `snooze N` is `snoozed_until` tomorrow *without* `last_reviewed_at`, so it is not a Review
  decision.
- `reminders.save_outcome` (`reminders.py:228-251`) is the notification path's outcome (`set outcome` +
  reminder `done_said`, source `ntfy`), keyed by a reminder token; Review's equivalent is the note on Done.

---

## 3. Side effects, finish, order, size

### 3.1 What `GET /api/review` writes (`api.py:526-593`, all under `commit_lock`)

1. `suggestions.session(conn, today, per_review)` (`suggestions.py:358-394`) then `commit` (`api.py:547-548`):
   writes `state.links.session` `{day, ids}` (today's offered link suggestions, at most
   `links.suggestions_per_review`, live value 10) and `state.links.offered` `{<eid>: day}`. Topped up on
   later loads the same day while fewer than `n` were offered. `relations.annotate` then shows only those ids
   (`relations.shown_ids`, `relations.py:243-252`; filter `:376-378`). The scheduler's link job also writes it
   (SPEC `:1150`). Live: `{'day': '2026-11-19', 'ids': [186, 187]}`.
2. `UPDATE tasks SET last_offered_at = today` for the ids on the page (the first `cap`), then `commit`
   (`api.py:585-591`). Suggestion cards get no stamp. `lifecycle.expire` (`:192-214`) fades or lapses only
   rows whose `last_offered_at` is on or after the day their warning started (SPEC `:1157-1160`).

Live stamps today: 12 rows already carry `last_offered_at = 2026-11-19` **[verified today]**.

### 3.2 `POST /api/review/finish` (`api.py:744-749`)

Writes `state.last_review_at = date.today()` under `commit_lock`. Read by: the review push (skipped when it
equals today, `scheduler.py:195-199`), the brief (`brief.py:74`), the planner bundle (`planner.py:63`), and
returned by `/api/review`. It does **not** gate the queue ("New" means never decided, `lifecycle.py:117-121`).
The page calls it after every Apply on the Review tab that held a decision (`app.js:648-653`), then shows
"Batch applied" and waits for "Review another N".

### 3.3 Order

1. Task items by primary reason in `REVIEW_ORDER`, then due (string; undated last), then score descending
   (`lifecycle.py:187`).
2. Served in pages of `lifecycle.review_cap` (15); `?exclude=` drops task ids staged as decisions in the
   tray (`api.py:546, 549`; `app.js:1838`).
3. The page shows suggestion cards only when no task item is left on the page (`app.js:1937`), in evidence
   id order (`api.py:562`). Cards are not capped and not counted in `total`.

### 3.4 Size **[verified today]**

- Now: 70 open tasks, queue 16 task items (primary: new 8, link_suggested 3, thread_updated 1, planner 1,
  linked_changed 1, slipped 1, fading 1) + 6 cards; `review_queue.count` = 22.
- The evening push's counts (runs `review_push`): 19 (Nov 12), 9 (Nov 16), 3 (Nov 17), 16 (Nov 19).
- `review` batches since 2026-11-05 (25 batches, 37 since Nov 2):

| day | batches | of which undone | change rows |
|---|---|---|---|
| 11-06 | 4 | 0 | 29 |
| 11-07 | 1 | 0 | 9 |
| 11-09 | 1 | 0 | 26 |
| 11-12 | 1 | 0 | 29 |
| 11-13 | 5 | 1 | 88 |
| 11-14 | 3 | 0 | 33 |
| 11-16 | 4 | 0 | 20 |
| 11-17 | 6 | 0 | 38 |

  No Review batch on 11-05, 08, 10, 11, 15, 18, 19 (so far). Fields written by those batches:
  `last_reviewed_at` 61, `link` 40 (20 links × 2 rows), status 37 (done 20, dropped 16, open 1), `evidence`
  32, `reminder` 11, `context` 5, `thread` 4, `snoozed_until` 3, `created` 3, `longterm` 2, `outcome` 1,
  `effort` 1. So Keep and Done/Drop dominate, Later is rare, and links are common.
- The page estimates 12 s per item (`app.js:1953`).

---

## 4. What a server-side review session needs

### 4.1 What exists

| thing | where it lives today | shared across devices? |
|---|---|---|
| the queue | computed per request (`review_queue.queue`) | yes (but order shifts as decisions apply and sweeps add evidence) |
| the page of 15 | `review` JS variable after `GET /api/review` (`app.js:1835-1849`) | no |
| current item | `items.filter(i => !staged.has(i.task.id))[0]` (`app.js:1933, 1945`); `ui.reviewIdx` is set but not used for selection | no |
| staged decisions (the tray) | `localStorage['factotum.staged']` | **no** |
| paused after Apply | `ui.reviewPaused` | no |
| a suggestion card's draft | `draft.sg[<id>]` (page memory) | no |
| the card's conversation | `chat_threads` (origin `review` / `task` per task; `suggestion` per card_id), `chat.place_thread` (`chat.py:87+`) | **yes** |
| a staged batch from an agent | `proposals` table, `GET /api/proposals`, Apply / Dismiss (`api.py:1047-1097`) | **yes** |
| "reviewed today" | `state.last_review_at` | yes |

There is **no** session row, cursor, current item, skip list or "audio owner" on the server.

### 4.2 What a session would need

1. **A stable order.** Snapshot the item keys at start (`t:<task id>` / `e:<evidence id>`), re-check an item's
   eligibility when it is reached (a sweep may have judged its evidence; a ripple may have added one), and
   append new items at the end rather than reshuffling under the speaker.
2. **A cursor and a skip list** ("next", "skip", "go back", "where was I").
3. **Server-side staging**, so the phone that speaks and the desktop that shows agree. Options:
   - (a) **Move the tray to the server**: a table keyed by the page's `stageKey`, holding exactly the entries
     the page's Map holds (`{task_id, op, fields, label, expect, note, ev, from_chat}`); the page reads it on
     load / return and writes on stage / discard; Apply is unchanged. Write sites to convert: `stageExtra`,
     `stage`, `persistStaged`, `discardStaged`, `applyAll`, and the direct `localStorage.setItem` calls at
     `app.js:555, 563, 571`. Cleanest "one tray everywhere"; the largest page change.
   - (b) **Reuse `proposals`**: one mutable proposal row per session (`source` would need a new value such as
     `review`; `_proposal_apply` forces skill/connector today, `api.py:1078`). Every device already draws the
     banner. Apply is all-or-nothing, and the exclusion of staged tasks would be computed from the row.
   - (c) **Page-resident voice only**: the loop runs inside the page that holds the tray. Smallest; the phone
     and desktop do not agree, and no other client can drive it.
4. **The decision rule in Python**: e.g. `review_queue.decision_changes(item, decision, note, today)` that
   returns what `reviewAct` builds today (op + `judge_evidence reviewed` for each pending evidence + `add_note`
   / outcome rule at 300 chars), plus `accept(evidence)` / `answer_link(suggestion, act)`. The page should call
   the same function (or be tested against it) so the two never diverge.
5. **Per-item offered stamp**: stamp `last_offered_at` when an item is *spoken*, not a whole page at once
   (see §6).
6. **One audio owner**: which device holds the microphone; others show "a spoken review is running on the
   phone" and read the cursor.
7. **Finish semantics**: on "apply" / leaving audio mode, `batches.apply(..., "review")` then the finish
   write, as the page does.
8. **Push of changes to other open pages**: today pages refresh on return (`onReturn`); a session would need
   polling of a cheap read route or an SSE stream (the chat turn already streams SSE, `api.py:933-1013`).

**Where**: SQLite tables (SQLite is the source of truth; CLAUDE.md fact 1) behind new routes in `api.py`, each
write under `commit_lock` (the enforcement test scans `api.py`'s source only:
`tests/test_review_fixes.py:160-162`, so routes placed in another module would escape it). The decision
rule in `review_queue.py` (it already owns "Review, defined once"); the spoken card in a new module (§5).

---

## 5. What to say aloud

Speech runs at about 15 characters a second. Live lengths of open tasks **[verified today]**: name median 61
chars (p90 74, max 81) ≈ 4-5 s; `why_now` median 92 (p90 144) ≈ 6-10 s; `context` median 217 (p90 586, max
1447) ≈ 14-40 s+; evidence excerpts 29-140. 16 of 70 names contain `( ) & /`. 2 `counterpart` values and 1
context are email addresses; 4 contexts hold URLs.

### 5.1 What a person needs to hear, per kind

| kind | must hear | on request ("more", "why", "what changed") |
|---|---|---|
| any task card | the reason in words, the name, the due in words (today / tomorrow / Fri Nov 20 / "no date"), hard or not | why now, the text, project, effort, who, links |
| `last_call` | "its hard deadline was <day>, a week ago; it lapses unless you keep it" | |
| `thread_updated` | sender (display name only) + subject of the new mail (`evidence.label` / `reason`) | the planner's proposal if any |
| `mentioned` | who mentioned it, where (channel, workspace) | |
| `planner` / `ripple` | the proposal in words ("move the due date to Friday at 2 PM", "drop it: the Oct 19 meeting passed") + its reason; for a mail card "this mail from X, '<subject>', is about this task; follow its thread?" | |
| `freed`, `blocker_gone`, `steps_done` | `texts[...]` with the other task's **name**, not its number | |
| `linked_changed` | which linked task did what (`since.by`: finished / dropped / changed its text) and whether this task's text was brought up to date | `since.text.note` (the ripple's reason) |
| `link_suggested` | the question with both names ("Is this a step of 'Complete Caltrans IRB submission'?") and `why` | the second suggestion |
| `snooze_over`, `new`, `slipped`, `fading` | reason + name + due (slipped: "was due Tuesday") / fading: "untouched since Nov 1; it fades in N days" | |
| suggestion card | "From your mail: <sender name>, '<subject>'. Suggested task: <name>, due <day>, hard." + the card's links in words | `reason`, the draft's text |

### 5.2 Too long or machine-shaped to read by default

`score`, `priority`, `score_reasons` ("ZIP Launchpad high +15"), `tier`, `Task-N` numbers (say names; keep
numbers on the screen), ISO dates and `T17:00` times, `due_is_datetime`, effort codes (XS/S/M/L/XL → "a few
minutes / small / medium / large"), `source_link` URLs, `mail.ref` / `conversation_id`, `account`, any email
address (`counterpart` can be one: card 167), `context` in full, `since.text.before/after` (a diff), the
ripple's before/after, `evidence.label` prefixes ("create: …", "Task-449 → …"), `why` of a link suggestion
beyond one sentence, the Slack permalink in a Slack card's context, parentheses in names.

### 5.3 A server-built "spoken card"

Proposal: a pure function `spoken.card(item | card, today, names) -> {"say": str, "more": {"why": str,
"text": str, "links": str, ...}, "hint": "Done, keep, later, or drop?"}` in a new module
`factotum/spoken.py`, called from `_review` (so it is built from items that came through `decorate_all`
→ `relations.scored` → `annotate`) and from any session route. It reads `item["task"]["rel"]`,
`item["texts"]`, `item["since"]`, `item["link_suggestions"]`, `item["evidence"]`, and names from the
`task_briefs` the route already fetches (`api.py:323-328, 553`); it never queries relations itself and never
calls `scoring`. A shared "day in words" helper (today `brief._fmt_due`, `brief.py:49-55`) should move to
`dates.py`. Its templates belong in the forbidden-words test's list (`tests/test_stage4_agent.py:462-466`,
which today scans prompts, skills, tool descriptions, `REVIEW_LABELS` and `REASON_TEXTS` only).
Addresses must be stripped (reuse `batches.EMAIL_RE`, `batches.py:35`); mail sender → display name only.

Examples, built by hand from the live items above:

- Task-449 (`thread_updated` + `planner`): "New mail on *Prepare and rehearse the ZIP Launchpad funding
  pitch*, due tomorrow: from Lavin Entrepreneurship Center, 'Appointment booked: In Flight Seed Fund
  Presentations, Friday at 2'. The planner suggests moving the due date to Friday at 2 PM. Accept, keep,
  later, done or drop?"
- Task-424 (mail on a task): "A mail from ZIP Launchpad, 'Reminder: EI Mixer is tomorrow', looks like it is
  about *Attend the SDSU Entrepreneurship Mixer at Aztec Lanes*, today at 5 PM, hard. Follow its thread?"
- Task-419 (`linked_changed`): "A linked task changed: *Submit abstract to the Delft conference* changed its
  text, and this task's text was brought up to date. This is *Submit full paper to the Delft conference*, due
  March 1, hard. Say 'what changed' to hear it."
- Task-477 (`link_suggested` + `new`): "New, due today, hard: *Send Caltrans simulator scenery screenshots
  for terrain selection*. Two suggested links. First: *Apply selected scenery to the simulator* waits on this,
  because the scenery can only be applied after the screenshots are sent. Link them?"
- Task-481 (`new`): "New, due November 30, hard, large: *Deliver Caltrans Task 4 by Nov 30*."
- Task-457 (`slipped`): "Soft date slipped: *Send ZIP a Car Sounds job description*, was due Tuesday. It is
  a step of the mixer, which is today."
- Task-38 (`fading`): "Fading soon: *Generate Car Sounds SOPs and reference materials*, no date, untouched
  since November 1."
- Card 162 (mail): "From your mail: Lavin Entrepreneurship Center, 'Seed Fund Presentation this Friday'.
  Suggested task: *Book a Seed Fund presentation slot for Car Sounds*, due tomorrow, hard, a few minutes, as
  a step of the funding pitch. Create it, or dismiss?"
- Card 191 (planner): "Planner suggests a new task: *Submit BDA 696 project paper draft*, due November 25,
  because a teammate's Slack message gives a new deadline. Create it, or dismiss?"

---

## 6. Conventions a spoken session could break silently

| convention (CLAUDE.md) | how a voice session breaks it | guard |
|---|---|---|
| every write on the API's shared connection takes `commit_lock` (`api.py:282-292`); never across a Claude run or network call; lock order turn_lock → `_CREATE_LOCK` → `_ADD_LOCK` → `commit_lock` | a session route that stamps / stages / applies without it interleaves with another request's batch (half-written batches, 500s); a route outside `api.py` is not caught by the source-scan test | routes in `api.py`, plain `def`, `with commit_lock:`; release before any TTS / Claude call |
| no `async def` route writes | a websocket / async audio handler that writes runs on the event loop | do writes in sync routes or `run_in_threadpool` |
| state-changing `/api` calls carry `Content-Type: application/json` (`api.py:243-257`) | `navigator.sendBeacon` on page hide, a form post, or an audio upload (`audio/webm`, multipart) gets 415 | JSON bodies only for state changes; if audio is ever uploaded, a non-`/api` path or a JSON wrapper, decided deliberately |
| `/api` never answers 401, 403 or a redirect | "no session", "another device holds the mic", "not allowed" answered 403 → the page shows "Sign in again" | 409 / 404 / 400 |
| `expire` acts only on `last_offered_at` | (a) a voice route that reads the queue without stamping → spoken items never count as offered, so they never fade / lapse; (b) stamping the whole queue at session start → rows the owner never heard become expirable tonight | stamp each item when it is spoken |
| `review_queue.count` reads only; `GET /api/review` is never called for a number and is loaded once per page (in-flight flag) | a voice loop polling `/api/review` for "how many left" stamps rows and re-runs the suggestion session on every poll | count via `review_queue.count`; a new read-only "current item" route |
| words "blocked" / "dependency" never appear in anything the user or the agent reads | spoken templates say "this is blocked by…" | say waits on / frees / step of; add templates to the test |
| a suggestion's `why` never quotes task text | a spoken link question that reads task text as the reason | read `why` as stored |
| opening a task stamps `neighbours_seen_at` | answering "what is it linked to?" with `GET /api/tasks/{id}` clears "A linked task changed" before the owner decided | `?peek=1` |
| the decision judges the card's evidence (page rule, §2.1) | a voice Keep that sends only `keep` leaves the card in the queue tomorrow (verified on a copy) | one Python decision function |
| Keep does not answer a link suggestion | voice "keep" on a `link_suggested` card loops back | ask the link question first, or treat "keep" as "not now" for the card explicitly |
| Apply on Review is followed by `/api/review/finish` | a voice apply that skips it lets the evening push nag after a full spoken review | call the same finish write |
| one turn per thread (409); a Review card and an opened task share one thread per task | a voice question while a typed turn runs on the same task | serialise, say "still thinking about the last question" |
| a value a run may write back is JSON-escaped, never HTML-escaped; every prompt filled with `prompts.fill`; stranger text through `one_line` | a spoken transcript or a spoken card pasted into a chat prompt | send it as the turn's text through `chat.turn`; never paste page-built sentences (the "place adds the server's words" rule) |
| text written by strangers goes through bounded patterns | parsing a mail subject / Slack text to build speech with an unbounded regex | bounded patterns, caps |
| what is typed lives in `draft`; a new text box needs `isDraftBox`, `typingNow`, an id in the restore list; `render()` rebuilds the panel every second during a chat turn | a live transcript box loses its text mid-utterance | register it like `#rv-ask` |
| on a phone one thing at the bottom; `kb-open` hides bars | a mic bar that is a new bottom bar | join the existing rules |
| absent means off for paid scheduled runs and anything that sends to an outside service; Settings keys are a whitelist with `DEFAULTS`, control, example, `MINIMA` / `CHOICES` | a cloud STT/TTS switch that defaults on, or a key added without its table entries | an explicit switch, off when absent; browser speech costs nothing |
| person-specific facts in config; the suite never reads `.env`; new secrets named in `.env.example` | a voice name, wake word or API key in code | config / `.env.example` |
| the build stamp covers `api.STAMPED` only | a new `voice.js` not in `STAMPED` never reloads after deploy | add it to `STAMPED` |
| the service worker has no fetch handler | caching audio or the app in the worker | never |
| `batches.apply` strips `source_key` etc. from client changes | a voice "create" sent as a client `create` with a key | use the suggestion route for cards; hand creates through `/api/add` |
| `create` undo drops the row, never DELETE | — | unchanged if all writes go through batches |
| the API's `date.today()` is the machine's day; the scheduler uses the owner's zone (`reminders.local_now`) | "due today" spoken from a different day than the queue used near midnight | build the spoken card with the same `today` as the queue |

---

## 7. Things I could not find out

- Whether the Pin-to-Top / Long-term keying on a Review card (fact 10) behaves as the code suggests in a
  browser; I did not drive the page.
- No live example of `last_call`, `mentioned`, `ripple`, `freed`, `blocker_gone`, `steps_done`, `snooze_over`,
  a Ripple card or a Slack card; shapes are from code.
- Per-item decision time in real sessions: batches record only apply times, not when each card was decided.
- Whether the owner usually reviews on the phone or the desktop (no device column anywhere).
- Speech-API availability, latency and cost (outside this question; not researched).
