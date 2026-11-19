# 06 · The conversation and the autonomy around it

What a spoken Review session should sound and feel like (part A), and what about Review itself should change, voice or not (part B).
Research only. Nothing in the repository was edited. The live database was opened read-only (`?mode=ro`), `.env` was never read
(the analysis script stubbed `dotenv.load_dotenv` before importing `factotum.config`), and no write tool of any service was called.
Scripts used: `/tmp/claude-1000/-home-robert-factotum/9a480db7-2549-4c53-9cbb-0e57afb9e3a5/scratchpad/vr-scripts/q1.py` … `q6.py`.

Status marks: **[verified today]** means I checked it in code, in the live database or on a page today. **[documented, untested]** means a source says it
and I did not try it. **[recalled]** means it comes from memory and is not checked.

---

## 0. How this owner actually reviews (the ground the proposals stand on)

All figures come from the live database, read-only, on 2026-11-19, for review batches since 2026-11-05 (13 days). **[verified today]**

| Fact | Number | Where |
|---|---|---|
| Review batches | 25 (one undone) | `batches.source = 'review'` |
| Sessions (batches less than ~30 min apart) | about 10 in 13 days; local times 06–07, 13–14, 16–17, 20–21 h | q2.py |
| Decisions per session | 2 to about 37; the median is about 8 | q2.py |
| Batches per session | often 2–6 a few minutes apart: the owner applies in small groups (for example 2026-11-17 13:43–13:52 has six batches; 11-15 20:09–20:17 has four) | q2.py |
| Decisions overall | Keep about 50, Done 20, Drop 16, Later 3, Create 3 (plus link, reminder and text edits made in the card) | q2.py |
| **First-time items (`last_reviewed_at` was null, the reason "New")** | **Keep 37, Later 3, Drop 1 of 41: 90 % Keep** | q6.py |
| Items seen before (slipped, fading, last call, planner and so on) | Done 20, Drop 15, Keep 19: the real decisions | q6.py |
| Planner or ripple proposals on a task | 2 accepted, 3 only "seen" (decided otherwise), 2 waiting | q3.py, `evidence.kind = 'proposal' AND task_id NOT NULL` |
| Suggestion cards (proposals with no task: planner, mail, ripple) | 3 created, 13 dismissed (**19 % accepted**), 6 waiting | q3.py |
| Slack-mention cards | 5 staged, 4 dismissed | q3.py |
| Link suggestions | **7 of 7 judged were accepted**; 3 waiting | q3.py |
| New mail triaged by the model | 19 of 110 became cards (17 %) | q3.py |
| Conversation turns asked from a Review card | 10 threads with origin `review`; each a long natural-language instruction ("Please set a reminder for the day before…", "find that task and merge it with this task") | `chat_threads`, `chat_messages` |
| Latency and cost of a chat turn | Review origin: 10.7 s on average (2.8–17.9 s), $0.065 a turn | `chat_messages.duration_ms`, `cost_usd` |
| The review nudge time | `notify.review_time` = 19:30 (a Settings override) | `settings` |

The live queue today: **16 task items plus 6 suggestion cards = 22.** Primary reasons: new 8, link suggested 3, thread updated 1, planner 1,
linked changed 1, slipped 1, fading 1. Ten of the 16 carry "new" (eight as their primary reason): eight from two recordings (Tasks 477–481
from one, 482–484 from another) and two Canvas rows that are one course's final project. **[verified today]**, via `review_queue.queue` on a read-only connection.

What this means for design:

1. **"New" is an acknowledgement ritual, not a decision.** 90 % of first-time items are kept. A spoken session that asks about each one
   is an interrogation. Present them as a group and keep them all unless the owner speaks up (§6.3).
2. **The real decisions are on items seen before**, and there Done, Drop and Keep are about equally likely. These need one item at a time.
3. **Suggestion cards are mostly wrong (81 % dismissed). Link suggestions are always right (7/7).** A recommendation's wording must follow
   those base rates: "Link it?" can expect a yes; "Create it?" cannot.
4. **The owner already talks to Review in sentences.** Every Review-card conversation is a spoken-style instruction. Voice is the same
   behaviour with the hands free, not a new one.
5. **A model answer takes about 11 seconds.** Anything that goes to Claude needs a sound and one spoken "looking at your mail" line, not silence.
6. **Evening sessions (20–21 h) and the 19:30 nudge fit the dinner case** the owner described.

Two oddities found on the way (not voice-specific):
- Evidence rows 2, 3 and 4 (`proposal`, "Task-397 → drop", etc.) are still unjudged although their tasks were dropped on 2026-11-02. They
  never show (Review lists open tasks only, `factotum/api.py:562-575`), so they do no harm, but they are dead rows. **[verified today]**
- Card 167 ("Revise … slides … before today's meeting", made 2026-11-18) is about a meeting that has passed and is still waiting. Suggestion
  cards have no expiry (`factotum/review_queue.py:29-37`, `card_count`). **[verified today]**

---

## 1. What exists today (the parts voice would sit on)

| Piece | What it does | Code |
|---|---|---|
| The queue | Reasons in a fixed order (last call … reminder missed), sorted by primary reason, then due, then score; 15 to a page | `factotum/lifecycle.py:14` (`REVIEW_ORDER`), `:73-133` (`review_reasons`), `:177-189` (`build_review_queue`) |
| "New" | Never decided on (`last_reviewed_at` is null), whatever its age | `factotum/lifecycle.py:117-121` |
| The count | One read-only count used everywhere | `factotum/review_queue.py:16-43` |
| GET /api/review | It **writes**: the day's link-suggestion session and the `last_offered_at` stamp that `expire` needs. Items carry `reasons`, `texts`, `evidence` (with a proposal's `change`), `link_suggestions`, `since`; cards carry `fields`, `mail`, `links` | `factotum/api.py:526-593` |
| The decisions | Done, Keep, Later (tomorrow / Friday / next week / a date), Drop; extras Top and Long-term; due, reminder, note; the card's evidence is judged `reviewed` with the decision | `factotum/web/app.js:1964-1969`, `:4198-4229` (`reviewAct`), `:4227` |
| A planner proposal on the card | "Accept" stages the change; the card still waits for a decision | `factotum/web/app.js:1967`, `:3975-3990` |
| Link suggestions on the card | Link / Not related; Merge into N / Not the same; for "related" four choices | `factotum/web/app.js:1885-1914` |
| Suggestion cards | Shown **only after every task item is decided** (`if (!items.length && sugg.length …)`); Create / Dismiss / Edit before creating / its own conversation | `factotum/web/app.js:1936-1937`, `:1976-2001`; `factotum/api.py:638-723` |
| The tray | **Per browser, in `localStorage` (`factotum.staged`)**; one Apply makes one `review` batch; then `POST /api/review/finish` and the "Batch applied" pause | `factotum/web/app.js:63`, `:575`, `:614-668` |
| The conversation on a card | One thread per task, a chat turn with the MCP tools (mail, calendar, Plaud, tasks); its proposals stage beside the decision | SPEC.md §8.3 "Places", `factotum/chat.py` |
| Progress estimate | "~N min left" at **12 s an item** | `factotum/web/app.js:1953` |
| The nudge | Daily at `notify.review_time`, quiet if reviewed today or empty; ntfy and/or app push to `/review` | `factotum/scheduler.py:195-218` |
| Shorthand grammar | `done 389 392`, `drop`, `snooze`, `push … fri`, `nodue`, `top`, `longterm`; "remove", "delete", "cancel" all mean drop | `factotum/commands.py:30-41` |
| Planner proof rule | A proposal is applied only when it cites unjudged evidence the sweep attached to that task; a Slack mention never proves | `factotum/planner.py:43`, `:238` |
| Forbidden words | "blocked" and "dependency" in nothing a user or the agent reads (a test enforces it) | CLAUDE.md, `relations.WORDS` |

---

## A. The spoken session

### A1. A session, turn by turn (five live items, shortened and anonymised)

People are initials; course and grant names are generalised. The items are real rows of today's queue. **The words the assistant says about
mail are illustrative**: I did not read any mail, so the facts in those answers stand in for what a chat turn would find.

Earcons are written ⟨like this⟩ (§A2.6 lists them). Owner = O. System = S. What happens is in *italics*.

**Opening.** The owner taps "Review aloud" on the Review tab (or on the 19:30 notification, then one tap). The phone is propped by the stove.

> S: ⟨session start⟩ "Twenty-two to look at. Ten are new tasks, mostly from two recordings, so this is about six minutes. First, two things for tomorrow."
> *A server-side session is created (§B-2): a snapshot of the queue in spoken order, with a recommendation on each item (§A5).
> Time-critical items come first even though the current order would put them 1st and 4th, with the cards last.*

**Item 1: Task-449, rehearse the funding pitch.** Reasons `thread_updated` and `planner`. The planner proposes a due of Fri 14:00 (evidence 190, cited by booking mail 192).

> S: ⟨task⟩ "Rehearse the funding pitch. Mail moved: your presentation slot is booked for Friday at two. Set it due Friday at two?"
> O: "Yes."
> S: ⟨staged⟩ "Due Friday at two."
> *Stages `set due 2026-11-20T14:00` (the planner proposal, judged `applied`) and, since the accepted change answers the card, Keep with
> `thread_updated` judged `reviewed`. One word, one implicit confirmation.*

**Item 2: suggestion card 162, "Book the seed-fund presentation slot".** Python sees that its subject is done: the booking confirmation (evidence 192) landed on Task-449.

> S: ⟨card⟩ "A suggestion from mail: book the presentation slot. You've already booked it; the confirmation came in last night. Dismiss it?"
> O: "Yep."
> S: ⟨staged⟩ "Dismissed."
> *Stages `judge_evidence 162 dismissed`. The cross-item reasoning (a card's ask is already met) is the "auto-resolution" of §B-4; here
> it is only offered, not applied.*

**Item 3: Task-477, send the simulator scenery screenshots.** Reasons `link_suggested` and `new`; due **today**, hard; the suggestion is "part of Task-474 (the ethics-board submission)". **The misheard command and the undo happen here.**

> S: ⟨task⟩ "New from yesterday's meeting: send the scenery screenshots, due today, firm. It looks like a step of the ethics-board submission. Link it?"
> O (to someone else in the room): "…stop, stop, that's hot—"
> *The recogniser returns "drop". The risk rule (§A2.8) says a Drop on a hard-dated item due within two days needs an explicit yes.*
> S: ⟨question⟩ "Drop the screenshots? It's due today."
> O: "No! Undo."
> S: ⟨undone⟩ "Nothing dropped. Link it to the ethics submission?"
> O: "Yes, and keep it."
> S: ⟨staged⟩ "Linked and kept."
> *Stages `link 477 part_of 474` with evidence 187 judged by the change, and `keep`. Had the drop been staged (for a low-risk item it would
> be, with an implicit "Dropped."), "undo" would have removed it from the session tray, not from the database: nothing was applied yet.
> The "stop/drop/top" collision is why "stop" is never a decision word (§A3).*

**Group: six new tasks from two recordings.** Tasks 478, 479 and 481 come from yesterday's meeting (the recording that also made 477 and 480;
480 carries its own link suggestion and is asked alone); 482, 483 and 484 come from this afternoon's recording. (`tasks.source_key` prefixes
`plaud:of_de10…` and `plaud:of_6f0b…`, **[verified today]**.)

> S: ⟨group⟩ "Six new ones. I'll read the names; say a number to stop on one, or 'keep them all'.
> From yesterday's meeting: one, send the pilot dates; two, fix the billboard contrast; three, deliver task four by the thirtieth.
> From this afternoon: four, send the slides to a colleague; five, check the conference's paper format; six, find a purchase link for lab supplies."
> O: "Keep them all except four. Four is done, I sent it already."
> S: ⟨staged⟩ "Five kept. 'Send the slides' done."
> *Six decisions in one turn. The list is read with brief pauses (Amazon's list guidance, [A1]); "except four … done" is not in the
> fixed grammar, so it goes to the intent step (§A3.4): the deterministic parser handles "keep them all" and the model maps "except four,
> it's done" in one cheap run. "I sent it already" becomes the Done's outcome. Six names is the upper end of a spoken list; past six,
> read the first five and say "and four more like them".*

**Item 4: suggestion card 170, renew the health plan by 31 December.** **The skip.**

> S: ⟨card⟩ "From your mail: review next year's health plan and confirm your household by December thirty-first. Make it a task?"
> O: "Next. Ask me tomorrow."
> S: ⟨skip⟩ "Tomorrow."
> *Nothing is staged on the card (it cannot be "Later"ed: it is not a task). The session records it as passed over and leaves it out of
> this session. "Ask me tomorrow" is understood as skip, not as Later, because a card has no Later (§A3.2).*

**A question that needs a look at mail (on card 167, revise the slides before the meeting).**

> S: ⟨card⟩ "From your mail: revise the task-four slides per S's comments before Wednesday's meeting. That meeting was yesterday."
> O: "Did I send her the new slides?"
> S: ⟨thinking⟩ "Looking at your sent mail…" *(a chat turn with the card attached, origin `suggestion`; about 11 s, about 7 cents)*
> S: ⟨answer⟩ "Yes: you sent 'Task 4 slides, revised' to S on Tuesday evening." *(illustrative)*
> O: "Then dismiss it."
> S: ⟨staged⟩ "Dismissed."
> *The thinking earcon loops quietly while the run works. If the run stream reports a tool ("Searching mail · from:S"), that phrase is
> spoken once, not repeated. The answer is one sentence; "more" would read the rest of `reply_md`.*

**Item 5: Task-38, write the standard operating procedures.** Reason `fading`; undated; a project that does not never-fade. **The interruption.**

> S: ⟨task⟩ "Fading: write the standard operating procedures for the startup. Nobody's mentioned it in sixteen days. Keep, drop, or long-term?"
> *The doorbell. The owner walks away. Silence.*
> *After 8 s with no speech (no-input): one short reprompt.*
> S: "Keep, drop, or long-term?"
> *After 8 s more: the session pauses itself. One soft ⟨paused⟩ chime, no words; the screen shows "Paused at 9 of 22 · say 'continue' or tap".
> The microphone is closed (not listening to the conversation at the door). Wake lock released after a minute so the phone can sleep.*
> *Six minutes later the owner taps the screen with a knuckle.*
> S: ⟨resume⟩ "We were on the operating procedures, fading. Keep, drop, or long-term?"
> O: "Long-term."
> S: ⟨staged⟩ "Long-term. It won't fade."
> *Stages `longterm` and `keep`. A resume after more than a few minutes restates the item, never the whole list.*

**End of the queue.**

> S: ⟨end⟩ "That's this page, except the health plan, which I'll ask about tomorrow. Thirteen decisions: one date moved, eight kept,
> one done, one long-term, one link, two suggestions dismissed. Apply them?"
> *(In a real session the remaining queue items, such as 424, 419, 474, 480, 457 and the two Canvas rows, would sit between items 3 and 5;
> they are left out of the script for length.)*
> O: "What did I drop?"
> S: "Nothing. You undid the one drop."
> O: "Apply."
> S: ⟨applied⟩ "Applied. You can undo it in History. That's it for tonight."
> *One `review` batch (or the ones already applied in chunks, §A4.6); then `POST /api/review/finish` (`factotum/api.py:744-749`), so the
> nudge stays quiet. Had the owner said "not now" or walked away, the session tray would stay on the server, the phone would show
> "12 decisions waiting to apply", and the next session (any device) would begin "You have twelve decisions from last night not applied
> yet. Apply them first?"*

Items in this script, by kind: a planner-proposal task card (449); a suggestion card answered by evidence elsewhere (162); a new task with a link
suggestion, a misheard command, a risk confirmation and an undo (477); a group of alike new tasks (478–484); a suggestion card skipped
(170); a suggestion card with a question that needs a mail look (167); a fading task with a pause and a resume (38); the end with a read-back
and Apply.

### A2. Rules for what is spoken

**A2.1 Length.** One item's opening must pass the one-breath test [A1]: **about 6–10 seconds, at most 25 words** (about 150 words a
minute, **[recalled]** typical TTS rate). Nothing an item says unasked may exceed **15 s**. "More" gives up to 20 s; "read it all" reads the
full text. The session opening and the end read-back may run 15–20 s. Today's on-screen estimate of 12 s an item (`app.js:1953`) is the right
order of magnitude for a spoken item with a one-word answer.

**A2.2 The order of facts in an item** (the order matters, because the owner may decide before the sentence ends; barge-in is allowed, §A4.3):
1. the earcon for the kind (task, suggestion card, group);
2. **why it is here**, in at most five words, said first only when the reason changes the decision ("Mail moved", "Fading", "Date slipped", "Last call",
   "Free now", "All steps done", "Your planner suggests"). For "New" the reason is the provenance ("New from yesterday's meeting");
3. **the name**, shortened to a spoken short name of at most 10 words (a field written once by Python's shortener, or by the prep run when one is on; §A5);
4. **the one fact that decides it**: the relative date and whether it is firm; who it is for or waits on; what moved; how long it has been quiet;
5. **the recommendation as a yes/no question**, or, when there is none, the two or three likeliest options for this reason ("Keep, drop, or long-term?").

**A2.3 How things are said.**
- **Task numbers:** not by default. The name is the handle. Say a number only when the owner asks ("which task is that?" → "Task four
  seventy-seven"), or to tell two items with the same name apart. When said: "task four seventy-seven", never "Task dash 477".
  The screen always shows `Task-477`. CLAUDE.md says the owner types and says `Task-<n>`, so the recogniser must accept "task four seventy seven",
  "four seventy-seven" and "number 477".
- **Dates:** relative within a week ("today", "tomorrow", "Friday"); "the thirtieth" within the month; "December thirty-first" beyond;
  never the year unless it differs. A time only when `due_is_datetime` ("Friday at two"). Hard: "firm" (or the owner's own word "hard"),
  said only when it is hard. An overdue soft date: "it slipped three days". Never "2026-11-30".
- **People:** the display name ("Max", "your advisor" only if the config says so), never an email address. A counterpart that is only an
  address is said as "the sender" or by the address's name part only if it is a plain given name. Waiting on: "waiting on Max".
- **Links:** in the owner's words: "a step of …", "waits on …", "frees …", "same as …" (the `relations.WORDS` vocabulary). The other end
  by its short name, not its number.
- **Mail:** sender's display name, a few words of subject, when ("last night"). Never a quoted body beyond one sentence unless asked.
- **Effort:** only when it decides ("a quick one", "a big one"), never the letter code.
- **Projects:** only when the name is ambiguous or the owner asks.

**A2.4 Never spoken:** ids (evidence, batch, thread, Notion, Canvas), URLs and anything address-shaped (the ripple guard's address patterns,
SPEC §8.6, are a ready detector), email addresses, the score, the priority letter, the mention count, `Task-N` codes inside a text (replace with
the task's short name when reading a sentence), markdown, emoji, the raw `why_now` when it repeats the date already said, long context
(more than one sentence) unless asked.

**A2.5 When the reason is said.** Always for: last call, fading, slipped, thread updated, mentioned in Slack, planner, ripple, free now, a task
it waited on closed, all steps done, a linked task changed, back. For "new" say the provenance instead. For "back from Later" say "back from
Later" only if it was put off more than once (otherwise it is just the item again). For a link suggestion, the suggestion itself is the reason.

**A2.6 Earcons and words.** Use short non-speech sounds for state and words for content. Speech-based cues are faster to learn for navigation
than abstract earcons [A6]; so state changes the owner hears dozens of times get sounds, and anything with content gets words.

| Sound | Meaning | Length |
|---|---|---|
| ⟨listening⟩ | the microphone is open (rising blip) | < 150 ms |
| ⟨heard⟩ | something was recognised and is being handled (soft tick) | < 100 ms |
| ⟨staged⟩ | a decision was staged (two notes up) | < 250 ms |
| ⟨undone⟩ | the last decision was taken back (two notes down) | < 250 ms |
| ⟨task⟩ / ⟨card⟩ / ⟨group⟩ | the next item is a task / a suggestion / several alike (three timbres) | < 200 ms |
| ⟨thinking⟩ | a model run is working (quiet loop, stops when the answer starts) | loops |
| ⟨not understood⟩ | low single tone (words only on the second miss) | < 200 ms |
| ⟨paused⟩ / ⟨resume⟩ / ⟨end⟩ / ⟨applied⟩ | session state | < 400 ms |

Earcons must be optional ("sounds only", "sounds and words", "words only" for screen-reader users) and short enough not to mask speech.

**A2.7 Confirmations.** Implicit by default [A1][A2]: the system repeats the decision in two to four words as it stages it ("Kept." "Later,
Friday." "Linked to the ethics submission."). That is the owner's chance to say "undo". Explicit (a yes/no question before staging) only for:
- **Drop or Done on an item that is hard-dated and due within two days, on Top, or with open steps or tasks waiting on it** (a wrong close
  there ripples, SPEC §8.6);
- a **merge** (which task survives is said by name);
- anything from a model turn that changes **more than one task** or creates one;
- a recognition the browser marks as low confidence (`SpeechRecognitionAlternative.confidence` below a threshold **[documented, untested]**
  on each engine; Safari's values are not known to be meaningful **[recalled]**);
- **Apply** of the session.

Wording: the decision, then the stake: "Drop the screenshots? It's due today." Never "Are you sure you want to…".

**A2.8 Not understanding, without becoming tiresome.** Following Google's error guidance [A2]:
- **No match, first time:** ⟨not understood⟩ plus the condensed options for this item ("Keep, drop, or later?"). No "Sorry, I didn't get that."
- **No match, second time:** "Say it another way, or tap." The screen shows what was heard (a transcript chip), so a glance explains the miss.
- **Third time:** stop asking; show the buttons; say nothing more until the owner speaks or taps. Google suggests ending after two or three
  failures; here "ending" means falling silent, not quitting the session.
- **No input (silence):** assume the owner did not hear; restate the question once, shorter. A second silence pauses the session (§A4.4).
  **Silence never means yes.**
- Anything not in the vocabulary goes to the intent step before it counts as a miss (§A3.4), so a sentence is never "not understood"
  just because it is long.
- The ⟨heard⟩ tick plays at once so the owner knows the system is working even if the model takes seconds.

**A2.9 The forbidden words.** Everything spoken comes from (a) Python templates, (b) a task's own text or (c) model output. (a) is covered if
the templates are strings in the code that `test_nothing_the_user_or_the_agent_reads_says_blocked_or_dependency` scans; add the voice module
to that test. (b) can contain anything the owner wrote; it is the owner's own words and is read only on "more". (c): the prompts must carry the same
instruction the other prompts do (say part of, steps, waits on, frees, same as), and the spoken short names the prep run writes should go
through a check that rejects those two words (rewrite with the template instead).

### A3. The spoken command set

**A3.1 Principles.** The fewest words that cover every decision; each sounds unlike the others; synonyms are the phrases people actually say
(Vtyurina and Fourney found about a quarter of guided-task exchanges start from implicit cues such as "okay" or a repeat request [A3]);
and destructive words never rhyme with harmless ones.

**Collisions found, and what to do about them:**

| Pair | Risk | Rule |
|---|---|---|
| **drop / stop / top** | "stop" (to the voice, or to a person in the room) heard as **drop**; "top" (pin) heard as either | "stop" is never a decision; it only silences speech. Pinning is "pin it" (full phrase). A Drop on a risky item asks first (§A2.7). |
| keep / skip | a skip heard as keep writes `last_reviewed_at` and hides the item until something changes | do not use "skip"; the move-on word is **"next"** (synonym "pass"). If "skip" is heard, treat it as next. |
| apply / reply | "reply to her" heard as apply | "apply" mid-session always asks "Apply twelve decisions now?" |
| done (the task) / "I'm done" (the session) | ending the session closes a task | a bare "done" is the task. The session ends with "**that's all**", "end review", "I'm finished". "I'm done" or "we're done" counts as the session and asks "End the review?" |
| later (decision) / "later" in "ask me later" | ambiguous on a card | on a task it is Later; on a card (which has no Later) it is next |
| undo / "and do" | rare | accept "undo", "scratch that", "take that back", "no wait" |
| yes / no | acoustically distinct; the safest pair | the recommendation is always phrased so that yes or no answers it |

**A3.2 Decisions per item kind.** (The bold word is the one shown on screen and taught; the rest are synonyms.)

| Item kind (reason) | Decisions | Notes |
|---|---|---|
| Any task item | **keep** (keep it, fine, leave it, that's fine) · **done** (finished, did it, that's done, already done) · **drop** (drop it, not doing it, cancel it, get rid of it) · **later** [when] (put it off, snooze, come back Friday / next week / on the 3rd; with no date the system asks "Tomorrow, Friday, or next week?") | as the four buttons (`app.js:1964`) |
| Task extras | **pin it** (to the top) · **long-term** (never fade) · **due** <date> / **move it to** <date> / **no date** · **remind me** <when> · **note** <text> (or "add that …") · with done or drop: "**it went** <outcome>" / any sentence after "done because…" | the commands grammar already parses dates (`factotum/commands.py`, `dates.resolve_when`) |
| Planner or ripple proposal on a task | **yes** (do it, accept, sure) · **no** (not that) — then the task decision | change: an accepted `done`/`drop`/`set` counts as the decision (§B-1) |
| Link suggestion | **link it** / **yes** · **not related** / **no**; for `same_as`: **merge** / **not the same**; for "related": **a step of it** / **waits on it** / **same task** / **not related** | 7/7 accepted so far: "Link it?" expects yes |
| Mail about a task (follow the thread) | **follow it** / **yes** · **not about this** / **no** | |
| Last call | **still need it** (keep) · **done** · **drop** | say the date passed and that nothing proved it done |
| All steps done | **close it** (done) · **more to do** (keep) | |
| A task it waited on closed (not done) | **still needed** (keep) · **drop** | |
| Free now | **keep** · **pin it** · **later** | |
| Suggestion card | **make it** (yes, create it, add it) · **dismiss** (no, not needed, ignore it) · a change in words ("make it due Friday and then add it") | Create with an edited draft = the card's existing `fields` path (`api.py:638-723`) |
| Group of alike items | **keep them all** · **keep all except** <n…> · **one by one** · then any decision with a number ("three is done") | |

**A3.3 Global commands** (work on any item, any time):

| Command | Synonyms | Effect |
|---|---|---|
| **next** | pass, move on, come back to it | leave undecided; out of this session |
| **back** | previous, the last one | return to the previous item (its staged decision is said and can be changed) |
| **repeat** | again, say that again, what? | repeat the item's opening (no-input logic too) |
| **more** | tell me more, details, what's it about, read it all | the context, the evidence excerpt, the linked tasks; a second "more" reads the full text |
| **wait** | hold on, pause, one sec | pause now (mic closed or on wake-phrase only, §A4) |
| **continue** | go on, I'm back, resume | resume with the item restated |
| **undo** | scratch that, take that back, no wait | remove the last staged decision; before any Apply nothing reaches the database |
| **how many left** | how much more, where are we | "Nine left, about four minutes." |
| **apply** | save them, that's it apply | read back the counts, ask, then one `review` batch |
| **that's all** | end review, I'm finished, stop the review | end; ask "Apply twelve decisions?" if any are staged |
| **stop** / **quiet** | shh, enough | stop speaking at once (barge-in); never a decision |
| **what can I say** | help, options | the decisions for this item plus "next, back, more, undo" in one sentence |
| **show me** | open it | puts the item's editor on screen (for a glance) |
| any question | "who is waiting on this?", "did I send…?" | the intent step or a chat turn (§A3.4) |

**A3.4 How an utterance becomes an action (three tiers).**
1. **Deterministic grammar** (free, < 100 ms **[recalled]**): a small Python or JS parser over the transcript for the words above plus dates and
   numbers, the same way `factotum/commands.py` handles typed shorthand. Most turns end here.
2. **A light intent run** (proposed; one short run, no tools, the item and the session state in the prompt, JSON `{actions: […]}`): for
   sentences such as "keep them all except four, four is done". The output goes through the same validation as a chat proposal. Cost and
   speed **not measured**; by analogy with `mail-triage` runs (3.7 s, $0.056 average, q5.py) a few seconds and a few cents.
3. **A chat turn** (existing, origin `review` or `suggestion`, about 11 s and 6.5 cents measured): questions that need tools (mail,
   calendar, recordings, other tasks). Its proposals are spoken as "Shall I …?" and staged beside the decision as today.

The transcript is always shown on screen, so a misparse is visible at a glance.

### A4. Turn-taking

**A4.1 Options compared.**

| Mode | Kitchen, phone on the counter | Earbuds | Verdict |
|---|---|---|---|
| Push-to-talk (hold) | needs a clean hand: bad | possible with a stem squeeze only through native apps **[recalled]** | no |
| **Tap-to-talk** (a tap anywhere on the screen starts listening) | a knuckle or elbow works; works when open-mic fails | n/a | **always available as the fallback** |
| **Open microphone in listening windows** (mic opens after each system prompt and closes at end of speech) | hands-free; the main mode | best, since there is no speaker echo | **the default** |
| Wake word | always-on hot mic in a noisy kitchen; needs an extra library in the browser (e.g. Porcupine's WASM build needs an access key **[recalled]**); battery | n/a | not for v1 |
| Media buttons (play/pause on earbuds) through the Media Session API | n/a | a press could mean "talk" or "next" | **worth a device test**: MDN documents action handlers for headset keys, but no source says whether a page that is not playing audio receives them [A9] **[documented, untested]** |

**A4.2 The default loop.** The system speaks → ⟨listening⟩ → the mic stays open until end of speech or the no-input timeout → ⟨heard⟩ →
the action → the implicit confirmation and the next item. The session is "hot" only while it is running; "wait" closes the mic until "continue"
or a tap. Silence detection: a fixed silence of about 500 ms is the common default for voice agents [A8] **[documented, untested]**, but kitchen
speech pauses mid-thought; use **about 800–1000 ms after a known command word** and **about 1500 ms after an open sentence** (or a semantic
end-of-turn detector where the engine offers one: OpenAI's `semantic_vad` has an `eagerness` setting [A8] **[documented, untested]**).
The browser's own recogniser decides end of speech by itself; how much control it gives is part of another report's engine survey.

**A4.3 Barge-in.** The owner may speak while the system talks: "yes" during "…Link it?" should act at once. On a phone speaker the
microphone hears the TTS; with `echoCancellation` on, barge-in may work for loud speech but is unreliable **[recalled]**. Rules:
- With earbuds: full barge-in (any utterance stops the speech and is handled).
- On the speaker: barge-in accepts only a short list said loudly (stop, quiet, yes, no, next, wait) and requires the speech to be recognised
  as one of them with good confidence; anything else waits for the listening window. A tap on the screen always cuts the speech.
- Keep item openings short enough (§A2.1) that barge-in is a convenience, not a necessity.

**A4.4 What silence means, and timeouts.**
- After a question: 8 s of silence → one shorter reprompt; 8 s more → **pause** with the ⟨paused⟩ chime and no words. Never decide on silence.
- During a model run: silence is expected; the ⟨thinking⟩ loop plays; after 20 s say "Still looking." once; a run over 60 s is abandoned with
  "That's taking too long; I'll leave it on the screen" and the answer appears in the card's conversation when it lands (chat turns may take
  up to about 18 s today, q5.py).
- A paused session keeps the screen awake for one minute (Screen Wake Lock API **[recalled]**; supported in current Chrome and Safari
  **[recalled, untested]**), then lets the phone sleep; the session stays on the server.

**A4.5 Recovery after an interruption.**
- Under 2 minutes: "continue" → the item's opening again, shortened ("The procedures: keep, drop, or long-term?").
- 2 minutes to 2 hours: "We were on <item>, <n> of <total>. You've made <k> decisions." then the item.
- Over 2 hours, or a new day: the queue is rebuilt (things changed: a sweep may have answered items); staged decisions on items that are
  gone or changed are said and dropped ("Two of your decisions are about tasks that changed since; I've left them out"); then continue.
- From another device: the same session id; the phone that resumes says "Picking up from the laptop."
- The page must survive iOS killing a background PWA: nothing of the session lives only in the page (§B-2).

**A4.6 When things are applied.** The owner applies in small groups today (2–6 batches a session). Options for voice:
(a) everything at the end (one Undo, matches "staged exactly as today"); (b) **apply every N decisions or at each pause**, each its own
batch, each undoable, with the end read-back covering only what is not applied yet. Recommend **(a) by default with (b) as a setting**,
because a session that dies before Apply (the dinner burns) must not lose decisions: that is solved by the server-side session tray, not
by applying early.

### A5. A recommended decision on each item

**Should a session recommend before asking?** Yes. It is what turns most items into one word, and the data says the owner usually agrees
with the obvious answer (New → Keep 90 %; link suggestions 7/7). Phrased as a question with the reason: "This looks finished: the reply went
out Tuesday. Close it?"

**Where recommendations come from, cheapest first:**

| Source | Covers | Cost | Basis said aloud |
|---|---|---|---|
| **Python rules over what is already known** (new rule set, `recommend(item)`) | New → keep (or "keep them all" for a group); link suggestion → link; follow-thread card → follow; all steps done → close; slipped soft date → "move it to <next sensible day>?"; fading in a never-fade project → long-term; fading otherwise → keep if mentioned in the last month, else drop; last call with no evidence → drop (and say nothing proved it done); a task it waited on closed → ask (no recommendation); back from Later twice → "drop it, or later again?"; a suggestion card whose subject a task already covers or whose date passed → dismiss | free, instant | the rule in words ("it slipped three days", "nothing since the 3rd") |
| **The planner's proposals and evidence** (existing: `proposal` evidence with a `change`, `thread_updated`, `sent_to_counterpart`, `reply_from_waiting`) | a proposed date, done or drop on a task; a sent reply that suggests done | already paid by the morning planner (18 runs, $1.27 in 13 days, q5.py) | the evidence ("you replied Tuesday") |
| **A review-prep run when a session starts** (new, optional): one light run, no tools, over the session's items with their evidence excerpts, returning per item `{short_name, recommendation, why, confidence}` | cards (where 81 % are dismissed, the model's judgment helps most), items with conflicting signals, spoken short names | not measured; a planner run over 60 rows costs about $0.07 (q5.py); a prep run over 15–25 items should be of that order **[estimate]** | "my guess" plus the why |

**Keeping it honest.**
- **Say the basis, and say when it is a guess.** A rule or evidence-based recommendation is stated as fact ("the reply went out Tuesday");
  a model's is "my guess: dismiss; it looks like a newsletter". Never "I recommend" with no reason.
- **Proof stays Python's.** A recommendation never applies anything. The existing proof rule (`planner.py:238`: only evidence the sweep
  attached to that task proves) decides what may be *said as fact*; everything else is a guess.
- **Record whether it was followed.** Put the recommendation (source, value) on the staged change (the change's `note` or the batch's
  `cause_json`) so the Log can show, per source and per reason, how often the owner agreed. A source under, say, 60 % agreement stops
  recommending (it asks with options instead). This is the calibration loop; without it a confident wrong recommender trains the owner to say yes.
- **No recommendation when the signals conflict**, when the item is hard-dated and due within two days and the recommendation is to close
  it, or when it is a merge.
- **Base rates in the wording.** For a mail card the default question should not lean to Create: "Make it a task, or dismiss?" unless the
  prep run says otherwise with a reason.
- **One word answers the recommendation; any other decision is still one word.** "No" to "Close it?" asks "Keep, later, or drop?" only
  when the item needs a decision; for a link suggestion "no" is the decision.

**Cost and the switch.** The rules are free. The prep run is a paid Claude run started by the owner (like ripples and filing, it runs only
after something the owner did); whether it is on by default is the owner's decision (CLAUDE.md: "absent means off" for scheduled paid runs;
`config.ON_WHEN_NEW` for the two exceptions). Memory notes say the owner prefers freshness and richness over run cost, which argues for on
by default with the cost stated in Settings. It must fail quietly (where Claude cannot run, the rules alone recommend; `runner.unavailable`).

---

## B. Review changes that are not about voice

Ranked by value to this owner over effort. "Voice needs it" says whether a spoken session depends on it.

### B-1. A recommended decision on every item, shown on the card (high value, medium effort; voice needs it: **yes**)
- *Today:* the card shows the reason, `why_now` or score reasons, and for some items a planner "Accept" line (`app.js:1955-1967`). Accepting
  a planner change does not decide the card (the toast says "decide on the card, then Apply", `app.js:3988`).
- *Change:* `recommend(item)` in Python (§A5) adds `recommendation: {decision, change?, basis, words, confidence, source}` to each item in
  `GET /api/review` (`api.py:542-593`) and to each card. The page shows it as the first, larger button ("Close it · the reply went out
  Tuesday") above Done / Keep / Later / Drop; one tap stages it. An accepted proposal whose op is a decision (done, drop, set + keep)
  counts as the decision.
- Also: the progress line (`app.js:1953`) can say "8 with a suggested answer".

### B-2. A server-side review session (high value, medium effort; voice needs it: **yes, hard**)
- *Today:* the tray is per browser, in `localStorage` (`app.js:63`, `:575`); the queue is paged and re-read; there is no notion of "where I
  was". A phone that kills the page, a switch to the laptop, or a reload in private mode loses the staged decisions or the place.
- *Change:* `review_sessions` (id, started_at, device, cursor, plan_json, staged_json, ended_at) or a reuse of the `proposals` table's
  idea (a server-held, not-yet-applied batch, `db.py:125-129`, `api.py:1047-1100`). Start returns the plan (the ordered items with their
  recommendations and spoken short names); each decision is POSTed to the session; Apply turns the staged list into one `review` batch
  through `batches.apply` (the only write path stays the only write path); `finish` as today. The page's tray shows the session's staged
  items like any others. A second device opening Review sees "a session is in progress: 12 decisions staged; continue here?".
- Rules to keep: no write to task state before Apply; every write under `commit_lock`; GET /api/review's stamps (`last_offered_at`, the day's
  link-suggestion session) happen once at session start, not at every resume.

### B-3. Deciding several alike items at once (high value, low–medium effort; voice needs it: **yes**)
- *Today:* one card at a time; the bulk bar exists on list tabs (Done, Drop, Snooze, due, Top, Long-term), not on Review.
- *Change:* the session plan groups items that share the primary reason **and** a provenance: the same recording (`source_key` prefix
  `plaud:<id>:`), the same Canvas course, the same mail thread, or twins (`suggestions.twins`). A group card lists the names with one tick each
  (all ticked) and offers "Keep all", "Keep the ticked", "One by one". Live example: eight new tasks from two recordings (five and three), and two
  Canvas rows of one final project. Given New → Keep 90 %, this alone turns the 10 "new" items of today's queue into about three decisions.
- Guard: groups only for reasons where Keep is the dominant answer (new, back from Later, linked changed with no proposal); never for last
  call, slipped, or anything with a close recommendation.

### B-4. A shorter queue: order, and resolving what is already answered (high value, medium effort; voice needs it: helps)
- *Order:* put **time-critical items first** whatever their reason (due today or tomorrow, hard), then items with a recommendation, then
  groups, then the rest; **interleave suggestion cards by urgency** instead of after all task items (`app.js:1936-1937` today shows cards only
  when no task item is left, so "Book the slot for Friday" would come last).
- *Resolution, always undoable and always said:* (a) a suggestion card whose subject an open task already covers, or whose stated moment has
  passed (card 167, "before today's meeting"), is set aside in a batch with source `automation` and a line in the digest ("set aside: the
  meeting it was for has passed"); (b) a suggestion card unjudged for 14 days is set aside like an untriaged mail (mail_triage does this for
  candidates after 7 days, SPEC §8.5); (c) dead proposal evidence on closed tasks (rows 2–4) is judged when its task closes. None of these
  closes a task. Closing a task automatically stays the planner's proof rule only.
- *Not recommended:* auto-keeping New items without the owner hearing them. The owner's act of acknowledging is the point of New; the
  group card (B-3) makes it cheap instead.

### B-5. Review from a notification (medium value, low effort; voice needs it: helps)
- *Today:* the 19:30 nudge opens `/review` (`scheduler.py:195-218`); the app's shortcut "Review" too (SPEC §9 stage 8).
- *Change:* the nudge's body says what is decidable fast ("22 waiting · 8 new from yesterday's meeting · about 6 min"); a button
  "Review aloud" opens `/review?aloud=1`, which shows one large "Start" button: the microphone and speech need a user gesture in the page
  **[recalled]**, so the notification tap alone cannot start talking.
- iOS ignores manifest shortcuts (CLAUDE.md) and notification buttons work through the declarative payload; keep the action a plain `navigate`.

### B-6. A digest of what was decided (medium value, low effort; voice needs it: no, but the end read-back is its spoken form)
- *Today:* the Log shows batches in words; the brief lists "Closed since yesterday" (`brief.py:1-9`).
- *Change:* the end of a session reads the counts (§A1 end); the morning brief adds "Decided last night: 12 (1 done, 1 long-term, 3 suggestions
  dismissed) · set aside by rules: 2"; the Log line for a review batch carries the same words.

### B-7. Fewer taps per decision on the phone (medium value, low effort; voice needs it: no)
- *Today:* Later takes two taps (`app.js:4201`, then a day); a planner proposal takes Accept plus a decision; the note box's placeholder is long.
- *Change:* the recommendation button (B-1) makes most items one tap; Later remembers the last choice ("Later · Friday" one tap, long press for the
  menu); swipe right = the recommendation, left = next (not Drop: a swipe that drops is an accident waiting to happen).

### B-8. Standing rules the owner states once (medium value for cards, high effort; voice needs it: no)
- *What they could cover:* suggestion cards and evidence, where most decisions are dismissals (13/16 cards, 91/110 mails set aside): "workshop
  invitations from the department: dismiss", "anything from this course's announcements more than a week old: drop the card".
- *Limits (the guard):* a rule may only **dismiss a card, set aside evidence, or recommend**; it never closes, drops or changes a task. Each rule is a row
  (owner-stated text, a structured matcher Python evaluates, created_at, expires_at by default in 90 days, a counter); each application is a
  batch change with source `rule` and the rule's id, undoable, and appears in the digest; a rule that has fired 20 times without being looked at
  asks to be confirmed again. Stating a rule by voice ("from now on…") makes a *draft* rule shown on screen; it starts only after a tap.
- Effort is high because the matcher must be deterministic (a model may draft it; Python must evaluate it) and the UI needs a rules list.
  Build after B-1 to B-4 have shown which dismissals repeat.

### B-9. Sessions paused and resumed across devices (part of B-2)
Listed separately because it is a visible feature: "Continue on this device" on the second device; the first device's page notices the session
moved and stops listening.

**Order to build:** B-2 (the session), B-1 (recommendations), B-3 (groups), B-4 (order and resolution), then voice on top; B-5, B-6, B-7 alongside;
B-8 last.

---

## 7. Risks and guards

| Risk | How it happens | Guard |
|---|---|---|
| **A wrong close by voice** | "stop" heard as "drop"; "done" meant for the session; a number misheard ("four seventy" vs "four seventeen") | stop/drop/top rule (§A3.1); risk-weighted explicit confirmation (§A2.7); any decision by number is echoed by name ("Done: send the slides"); nothing is written until Apply; History's Undo after Apply |
| **A wrong close by autonomy** | auto-resolution or a rule closes something still needed | resolution and rules never close or drop a task (B-4, B-8); only the planner's proof rule closes, as today; every automatic change is a batch, said in the digest, undoable |
| **Recommendations train a yes-habit** | a confident wrong recommender; the owner says yes to everything | the basis is always said; "my guess" for model judgments; agreement tracked per source; a source below threshold stops recommending (§A5); no recommendation for risky closes |
| **A voice that nags** | reprompts, a long apology, the system starting to talk unasked | the system never speaks unless a session is running and the owner started it; one reprompt then a silent pause; errors are a tone first; no spoken nudge (the push stays a notification) |
| **A queue that feels like an interrogation** | 22 questions in a row | groups for alike items; quick wins first; "about six minutes" said up front; "that's all for now" at any time with progress kept; the session caps at a page (15) and offers "five more?" instead of continuing unasked (as the page does today, SPEC §5) |
| **The kitchen hears private things** | mail summaries read aloud with others present | speak names, dates and senders only; mail content only on "more"; a "names only" setting |
| **Audio leaves the device** | the browser's recogniser sends audio to the vendor (Chrome's server recogniser; Apple's service on iOS [A10]); on-device recognition is experimental in Chrome (`processLocally`) [A11] | say so in Settings; a config switch for people who want on-device only; the engine choice is another report's subject |
| **Cost creep** | every sentence becomes a chat turn at about 6.5 cents | grammar first, intent run second, chat turn only for questions; a per-session budget; each run in the Log |
| **The forbidden words spoken** | a model's spoken short name or answer says "blocked" | templates in the scanned test; model output checked and replaced with the template (§A2.9) |
| **A dead page loses the session** | iOS ends the PWA; the phone sleeps | server-side session (B-2); the wake lock while active |
| **Unclear what was heard** | the owner doesn't know why something happened | the transcript chip on screen; the implicit confirmation names the decision and the task |
| **Speech recognition missing on a new person's machine** | Firefox has no recogniser; iOS home-screen apps have had a history of `webkitSpeechRecognition` not working (WebKit bug 225298, 2021–2022) [A10] | detect and say "Voice isn't available in this browser"; the session works by tap with speech output only (TTS is more widely available **[recalled]**) |

---

## 8. What I could not find out

- How long each decision takes. The database has batch times, not per-decision times, so seconds per item and session length are inferred
  from batch gaps.
- Which reason each past decision answered. Reasons are computed, not stored; I split decisions by "first review or not" instead.
- Whether earbud play/pause reaches a web page that is not playing audio (Media Session) on iOS and Android. Needs a device test.
- Whether `webkitSpeechRecognition` works today in an iOS home-screen web app (sources conflict; the WebKit bug was open in 2022).
- Barge-in quality on a phone speaker with the browser's echo cancellation.
- The cost and latency of the proposed intent run and review-prep run (not built; estimates by analogy only).
- What the mail would actually say in the script's "did I send the slides" answer; it is illustrative.

---

## References (all looked up 2026-11-19 through web search result summaries; pages not fetched in full unless stated)

- [A1] Amazon Alexa Voice Design Guide, "What Alexa says" (one-breath test, lists, implicit confirmation): https://developer.amazon.com/fr/designing-for-voice/what-alexa-says/ and the glossary https://developer.amazon.com/fr/designing-for-voice/glossary/ ; Alexa blog "Things every Alexa skill should do: pass the one-breath test": https://developer.amazon.com/blogs/alexa/post/531ffdd7-acf3-43ca-9831-9c375b08afe0/things-every-alexa-skill-should-do-pass-the-one-breath-test (undated pages). **[documented, untested]**
- [A2] Google conversation design, "Errors" (no input versus no match; short first reprompt; assume a silent user did not hear; limit retries): https://developers.google.com/assistant/conversation-design/errors ; Google Developers, "Three strikes and you're out": https://medium.com/google-developers/handling-actions-no-match-errors-in-dialogflow-three-strikes-and-you-re-out-25cfd7f58c7f . **[documented, untested]**
- [A3] Vtyurina, A. and Fourney, A., "Exploring the Role of Conversational Cues in Guided Task Support with Virtual Assistants", CHI 2018, DOI 10.1145/3173574.3173782 (Wizard-of-Oz cooking study; about a quarter of exchanges start from implicit cues): https://www.microsoft.com/en-us/research/publication/exploring-role-conversational-cues-guided-task-support-virtual-assistants/ . **[documented]**
- [A4] Budiu, R. and Laubheimer, P., Nielsen Norman Group, "Intelligent Assistants Have Poor Usability" (2018; assistants work for short, simple queries; trust and phrasing problems): https://www.nngroup.com/articles/intelligent-assistant-usability/ and https://www.nngroup.com/articles/intelligent-assistants-poor-usability-high-adoption/ . **[documented]**
- [A5] Google Android Auto help, "Send and receive messages" (read, then offer reply; "Send / Change message / Cancel"): https://support.google.com/androidauto/answer/6348317 ; TechCrunch, "Gemini starts rolling out to Android Auto globally", 2025-11-20 (summaries of several messages, then "reply?"): https://techcrunch.com/2025/11/20/gemini-starts-rolling-out-to-android-auto-globally/ . **[documented, untested]**
- [A6] Walker, B. N., Nance, A. and Lindsay, J., "Spearcons: speech-based earcons improve navigation performance in auditory menus", ICAD 2006: https://repository.gatech.edu/entities/publication/ad27273b-db94-4caf-9b43-9b46ef6098e9 . **[documented]**
- [A7] TechCrunch, "Todoist's app now lets you add tasks to your to-do list by speaking to its AI" (Ramble, 2026-01-21; task creation success about 40 % → 62 % during the beta): https://techcrunch.com/2026/01/21/todoists-app-now-lets-you-add-tasks-to-your-to-do-list-by-speaking-to-its-ai/ ; Todoist help, Ramble on Wear OS (2026-03-23): https://www.todoist.com/help/todoist/product-updates/just-talk-to-your-wrist-ramble-on-wear-os-beta-mar-23-2026-Kskv3T9JF . Capture by voice, not review by voice: I found no product that does spoken review of a task queue. **[documented]**
- [A8] OpenAI, "Voice activity detection" (server VAD `silence_duration_ms` default 500 ms; `semantic_vad` with `eagerness`): https://developers.openai.com/api/docs/guides/realtime-vad . **[documented, untested]**
- [A9] MDN, "Media Session API" (action handlers for hardware media keys including headsets): https://developer.mozilla.org/en-US/docs/Web/API/Media_Session_API . Whether the handlers fire without media playing: not documented. **[documented, untested]**
- [A10] WebKit bug 225298, "Speech recognition service is not available" in home-screen web apps (2021, follow-up 2022): https://bugs.webkit.org/show_bug.cgi?id=225298 ; Firtman, iOS 14.5 (Safari and PWAs gain speech recognition through Apple's cloud service): https://firt.dev/ios-14.5/ . **[documented, untested; status today unknown]**
- [A11] MDN, `SpeechRecognition.processLocally` (experimental on-device recognition): https://developer.mozilla.org/docs/Web/API/SpeechRecognition/processLocally ; blink-dev, "Intent to Ship: On-device Web Speech API": https://groups.google.com/a/chromium.org/g/blink-dev/c/VNOok2dbmHM/m/cz7ZnL59AAAJ . **[documented, untested]**
