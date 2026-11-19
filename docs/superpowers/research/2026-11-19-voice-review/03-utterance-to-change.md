# 03 · From a spoken sentence to a staged change or a spoken answer

Research for the voice Review session. Date: 2026-11-19. Repository at commit 75d2c77, read only.
Evidence labels: **[verified today]** = run or measured in this session; **[documented]** = read in
current documentation today, not tested; **[recalled]** = from memory, not checked.

---

## 0. The short version

- The existing grammar (`commands.parse`) is fast (about 3 µs a call, [verified today]) and safe in
  the sense that it refuses rather than guesses. But it **needs a task number in every command**, and it
  breaks on what speech recognition actually produces: a trailing full stop, number words, "tusk",
  "this friday". A session-scoped wrapper around it can reuse `parse` and `resolve_when` unchanged.
- **A Review decision is not a command.** The page's `reviewAct` (`factotum/web/app.js:4198-4229`)
  stages Later with `last_reviewed_at`, stages `keep` (which the grammar has no verb for), turns a typed
  note into an outcome, and judges the card's evidence in the same batch (`app.js:4227`). A voice
  decision has to go through that same function, or the card comes back tomorrow for the same reason.
- Measured from the live run log (`runs` table; last two weeks): **a chat turn from a Review card takes
  a median of 10.9 s (p90 15.3 s) and costs a median of $0.069** (n = 10). No-tools runs are faster:
  mail triage median 2.9 s, Today summary 4.0 s, ripple 5.6 s. The bare CLI start-up floor with
  haiku is **2.4 s wall** for a one-word reply (1.3 s of that inside the CLI's own timer).
- So: tier 0 (page) and tier 1 (grammar) answer in well under a second; tier 2 (one no-tools run)
  answers in about 2 to 5 s; tier 3 (a chat turn with tools) answers in 10 to 15 s. Tier 2 is usable in
  conversation only with an instant spoken acknowledgement; tier 3 needs spoken progress.
- Nothing faster than a headless CLI run is available **under the current rules** without new work:
  there is no API-key path (`.env.example` names none; the runner has none), and `--bare` needs one.
  The CLI does have a documented streaming-input mode (`--input-format stream-json`) that keeps one
  process and one conversation open between messages; that is the one realistic way to stay warm.
- Speech mishears. Staging is not applying, so a decision on **the card on screen** can be staged at once
  with a spoken echo and a spoken "undo". Anything that names another task by number, sets a date,
  links, merges, creates, or comes from a model must be read back first, and the read-back must be
  built by Python from the cleaned fields, never from the model's own `label`.

---

## 1. What the command grammar understands, and how it survives speech

### 1.1 The grammar as written

`factotum/commands.py`:

| Verb (and aliases) | Op produced | Takes a date? | Code |
|---|---|---|---|
| `done` (`finished`, `complete`, `completed`) | `done` | no | `commands.py:31, 73-74` |
| `drop` (`remove`, `delete`, `cancel`) | `drop` | no | `:32, 75-76` |
| `snooze` | `set snoozed_until` (tomorrow when no date) | optional | `:33, 77-81` |
| `push` (`defer`, `postpone`, `move`) | `set due` + `snoozed_until: null`; with no date, `snoozed_until` tomorrow | optional | `:33, 82-89` |
| `nodue` (`undate`, `undue`) | `set due: null` | no | `:34, 90-93` |
| `top` (`pin`) | `pin_top` | no | `:35, 94-95` |
| `unpin` | `set top_rank: null` | no | `:35, 96-97` |
| `longterm` (`long-term`, `lt`) | `set longterm: 1` | no | `:36, 98-99` |
| `start` | `set legacy_status: "In progress"` | no | `:37, 100-101` |
| `reopen` | `reopen` | no | `:37, 102-103` |

Shape: `^<verb>\s+<rest>$` (`:50`), ids by `_ID = (?:task[-\s]?|#)?(\d{1,6})\b` (`:43`), an optional
`to|until|till <when>` tail (`:57`), `and` and commas between ids are separators (`:64`). A dateless
verb with any leftover word is **not a command** (`:41, 65-66`), which SPEC.md:399-404 explains: `done
12 duplicate of 14` must not close Task-12. The API route is `POST /api/commands`
(`factotum/api.py:422-427`); it only parses (no write) and returns `{is_command, changes}`; the page
stages each change (`app.js:4250-4252`).

Not in the grammar: `keep`, `later` (Review's Later is snooze **plus** `last_reviewed_at`,
`app.js:4209`), reminders, notes, outcomes, links, merge, create (the add line is separate:
`parse_add`, `commands.py:183-330`, SPEC.md:406-424).

`factotum/dates.py:25-55` `resolve_when` understands: `today`, `tomorrow`/`tmrw`, `next week`
(next Monday), a weekday name or 3-letter prefix (strictly after today), `next <weekday>`, ISO
`YYYY-MM-DD`, `<month> <day>` (rolling to next year when past). Nothing else.

### 1.2 Speech-shaped inputs, run through the real code [verified today]

Today is Thursday 2026-11-19. Each line is `commands.parse(text, date(2026,11,19))`:

| Transcript | Result | Problem |
|---|---|---|
| `done 389` | done Task-389 | fine |
| `Done 389.` | **None** | recognisers add a full stop; `\b` then `$` fails |
| `done three eighty nine` | None | number words are not read |
| `done 3 89` | **done Task-3 and done Task-89** | a split number closes two wrong tasks: the most dangerous case |
| `done task 389` | done Task-389 | fine |
| `done tusk 389` | None | refuses (safe) |
| `done for 389` | None | "for"/"four" refuses (safe) |
| `done 389 and 392` | done both | fine |
| `push 364 to Friday` / `push 364 friday` | due 2026-11-20 | fine |
| `Push 364 to Friday.` | None | the full stop again |
| `push it to friday` | None | no id; "it" is a leftover word |
| `defer to next Tuesday` | None | no id |
| `defer 364 to next Tuesday` | due **2026-12-01** (12 days) | "next Tuesday" on a Thursday means the Tuesday after next here; a listener may mean 5 days |
| `snooze 364 until monday` | snoozed to 2026-11-23 | fine |
| `drop 12 I already emailed her` | None | leftover words (by design) |
| `done` | None | no id |
| `push 364 to this friday` / `december 1st` / `the 25th` / `in two weeks` | None | `resolve_when` does not read these |

`resolve_when`: `friday` → 11-20, `next friday` → 11-27, `this friday` → None, `monday morning` →
None, `in 2 days` → None, `december 1st` → None, `dec 1` → 12-01, `Friday.` → None.

Summary: the grammar fails closed on almost everything speech adds (good), with one exception that
fails open: **a number split by the recogniser into two numbers is read as two task ids**.

### 1.3 A session-scoped grammar

In a session the current card is known (task id, or a suggestion card's evidence id), so a command
needs no number. Proposed shape, layered **in front of** `commands.parse`, which stays unchanged:

1. **Normalise the transcript** (new, Python, shared by page and server so both read the same):
   lower-case; strip trailing and inner punctuation `.,!?;:`; collapse spaces; map number words
   ("three eighty nine", "three hundred and eighty nine", "four hundred twelve") to digits; map
   ordinals (`1st` → `1`, `twenty-fifth` → `25`); map known mishearings of the id prefix (`tusk`,
   `tasks`, `test`, `desk` → `task`) **only immediately before a number**; drop fillers at the start
   (`ok`, `um`, `uh`, `so`, `alright`, `please`) and at the end (`please`, `thanks`).
2. **Pronoun slot**: `it`, `this`, `that`, `this one`, `this task`, `the task` in the id position
   become the current card's id. `push it to friday` → `push <cur> to friday` → `commands.parse` →
   due Friday. No pronoun and no number also means the current card: `done` → `done <cur>`.
3. **Review decisions take priority over grammar verbs** when the card is a task card:
   `done`/`finished`/`complete` → `reviewAct('done')`; `drop`/`cancel`/`remove` → `reviewAct('drop')`;
   `keep`/`keep it`/`still need it` → `reviewAct('keep')`; `later`/`snooze [until X]`/`ask me [X]`/
   `not now` → `reviewAct('date', X)` (or tomorrow); `top`/`pin it` → `reviewAct('top')`;
   `long term` → `reviewAct('longterm')`. These go to the page function, not to `/api/commands`,
   because only `reviewAct` stages the evidence verdicts and `last_reviewed_at` (`app.js:4209, 4217,
   4227`). `keep` exists as a batch op (`factotum/batches.py:19, 191-192`) but not as a command verb.
4. **Field verbs go through `commands.parse` with the id filled in**: `push/move/due <when>` (due
   date), `no due date`/`undate` (nodue), `start`, `reopen`. Such an edit is not a decision on its own
   (Review's editor rule, CLAUDE.md "Apply on a Review card's editor line is not a decision"); the
   session then asks "Keep it?" or treats an explicit "next" as Keep. That choice is an open question.
5. **A decision with a clause**: `drop that, I already emailed her` → `drop` + outcome "I already
   emailed her", exactly what the page does with a typed note on Done/Drop (`app.js:4208-4216`,
   outcome cap `OUTCOME_MAX`, batches cuts at 300, `batches.py:195-196`). Guard: if the clause holds a
   contrast or condition word (`but`, `except`, `not`, `only`, `still`, `until`, `after`, `unless`,
   `if`, `when`), it is **not** taken as an outcome; the utterance goes to tier 2. `done, except the
   slides` must not close the task.
6. **A number that is not the current card** (`done 412`) is allowed but always read back (§5), and a
   number split into two (`3 89`) is refused when both pieces are short and adjacent: in a session,
   multi-id commands are rare enough that `done 3 89` should become a question, not two closes.
7. **Dates**: extend `resolve_when` (in `dates.py`, used by the add line too, so changes there are
   shared and need tests for both) with `this <weekday>`, `in N days/weeks`, `<month> <ordinal>`,
   `the <ordinal>` (this month or next), `end of (the) week/month`. Every spoken date is read back as
   weekday + month + day ("Friday, November 20th"), and `next <weekday>` should be read back with the
   number of days, because it is ambiguous in speech.

Reuse: `commands.parse` and `parse_add` unchanged; `resolve_when` extended (backwards compatible);
`VERBS`/`DATELESS` unchanged. New: one normaliser, one session wrapper (`session_parse(text, card,
today) -> {"decision": …} | {"changes": […]} | {"answer": …} | None`), and its table of decision
words. The page's tier 0 can hold the same word lists, but the server copy is the one tests pin.

### 1.4 Questions tier 1 can answer without a model

The card payload already holds what several "questions" need: `attached_rows` builds name, project,
status, due, hard, effort, counterpart, waiting_on, why_now, context, linked tasks with their side
words, links, reminders (`factotum/chat.py:163-178`). A fixed set of question patterns can be answered
from that data by templates, in tens of milliseconds:

| Utterance pattern | Answer from |
|---|---|
| "what was this about (again)" / "read it (again)" / "details" | name, project, `why_now`, first ~300 chars of `context` |
| "when is it due" / "is it a hard deadline" | `due`, `hard` |
| "is anything waiting on this" / "what does this free" | `linked` with `waits_on` pointing here (`relations.detail`) |
| "what is it waiting on" / "who are we waiting on" | `waiting_on`, `linked` waits_on |
| "is it part of something" / "what are its steps" | `linked` part_of both directions |
| "any reminders" | `reminders` |
| "why is this here" | the card's Review reasons (`lifecycle.REVIEW_LABELS`, `lifecycle.py:15-30`) |

A question that matches none of these, or wants mail, calendar, recordings, or other tasks found by
words, goes to tier 3.

---

## 2. The four tiers

### 2.0 What the live run log says [verified today]

Source: table `runs` (`factotum/db.py:116-120`, written by `db.log_run`, `db.py:318-324`), opened
read-only, rows with `started_at >= 2026-11-05`. Only Claude runs listed (cost > 0).

| Run kind | ok | n | median s | p90 s | min s | median $ | p90 $ |
|---|---|---|---|---|---|---|---|
| chat-turn (all places) | 1 | 20 | 10.9 | 15.1 | 2.8 | 0.069 | 0.082 |
| ↳ from a Review card | 1 | 10 | 10.9 | 15.3 | 2.8 | 0.069 | 0.081 |
| ↳ from the Chat tab | 1 | 7 | 13.3 | 15.1 |  | 0.074 | 0.092 |
| ↳ from Today's box | 1 | 3 | 6.5 | 6.9 |  | 0.065 | 0.071 |
| mail-triage (no tools) | 1 | 95 | 2.9 | 6.8 | 1.7 | 0.056 | 0.062 |
| today-summary (no tools) | 1 | 29 | 4.0 | 5.9 | 2.5 | 0.051 | 0.056 |
| ripple (no tools) | 1 | 5 | 5.6 | 7.3 | 4.0 | 0.027 | 0.030 |
| planner-morning | 1 | 18 | 10.2 | 11.8 | 5.6 | 0.068 | 0.079 |
| sync-file | 1 | 22 | 45.7 | 105.7 | 11.9 | 0.221 | 0.369 |

Per-origin chat numbers come from `chat_messages` joined to `chat_threads.origin`. Every Review-card
turn in the log was the **first** turn of its thread; there is no measurement of a resumed turn from a
card (one resumed Today turn: 6.5 s). **No `filing` run exists in the log**, so filing's floor is not
measured here; SPEC.md:1317 records about $0.04 a filed task (measured 2026-11-11).

Do the logged durations include CLI start-up? The logged number is the envelope's `duration_ms`
(`chat.py:531`, `runner.py:96`). For 22 chat runs I compared it with the wall time between the run
directory's `prompt.md` (written just before the run, `chat.py:520`) and `result.txt` (written just
after, `chat.py:532`): the wall time is a median **0.7 s** longer (range −1.3 to +1.2 s). So the logged
durations are close to wall time; add about a second.

The owner's real Review-card questions (read from `chat_messages`, not quoted here) are long
sentences: set a periodic reminder, find related tasks and link them, update the text with where
things stand, merge with a twin, create a parent and link children. Nearly all needed a tool (task
search, reminders with reasoned dates, links to tasks found by words). That is tier 3 work; tier 2
would cover the short ones (a reminder for a stated time, a note, done with an outcome).

### 2.1 Start-up floor of the CLI [verified today]

`claude` 2.1.319 at `~/.local/bin/claude`, run from the scratchpad directory.

| Probe | Wall | Envelope `duration_ms` | `duration_api_ms` | Cost | Cached prefix |
|---|---|---|---|---|---|
| 1. `claude -p --model haiku --setting-sources "" --disable-slash-commands "Reply with the single word ok"` | lost (my timing arithmetic failed; output was `ok`) | — | — | — | — |
| 2. same + `--output-format json` | **2.37 s** | 1.31 s | 1.97 s | $0.00098 | 36,422 tokens read from cache, 2,441 written |
| 3. runner-shaped flags (sonnet, `--tools ""`, strict empty MCP config) with the prompt on argv | 0.56 s, then exit | — | — | $0 | — |

Probe 3 failed before any model call: `--allowedTools ""` / `--tools ""` are variadic and swallowed the
prompt argument ("Input must be provided either through stdin or as a prompt argument"). The runner
avoids this by sending the prompt on stdin (`runner.py:173, 207`). Its 0.56 s is a useful number on its
own: **process boot to argument error is about half a second**. I did not run a fourth probe.

Reading: a trivial haiku run costs about 2.4 s of wall time, of which roughly 1 s is process start and
1.3 s is the CLI's request, which carries **about 38,000 tokens of Claude Code's default system prompt
and tool definitions** even with `--setting-sources ""` (served from cache). A Factotum run replaces
neither: `runner.command` passes no `--system-prompt` (`runner.py:74-89`). The CLI documents
`--system-prompt` to replace the default prompt (`claude --help`, [verified today];
https://code.claude.com/docs/en/headless "Customize the system prompt", [documented]). For a no-tools
run that would cut the request to the Factotum prompt only; effect on latency **not measured**.

### 2.2 Tier 0: fixed words in the page

| | |
|---|---|
| Words | next, skip, back, previous, repeat, again, stop, pause, exit, yes, no, undo, cancel that, louder/slower (TTS rate), "what can I say" |
| Latency | none beyond speech recognition end-pointing (no request) |
| Cost | $0 |
| Validates | the utterance must be one of the words, whole (after normalisation), and the session must be in a state where it means something (`yes`/`no` only while a question is pending) |
| May never | stage or apply anything by itself, except `yes` confirming a read-back that is already on screen, and `undo` removing the change just staged (`staged.delete`, the tray's own action), or undoing the batch just applied by Create (the toast's Undo, `app.js:4029`) |

These are cheap enough that the page should own them, so the session works with the server slow or
the network flaky. "Undo" in the session means **unstage the last staged change**; "undo" after an
applied batch is History's Undo (`POST /api/batches/{id}/undo`, `api.py:480`). The two must be spoken
differently in the echo ("Unstaged" vs "Undone").

### 2.3 Tier 1: the grammar on the server, card implied

| | |
|---|---|
| Input | normalised transcript + current card (task id or evidence id) |
| Code | new `session_parse` wrapping `commands.parse` (unchanged) and `resolve_when` (extended), plus the question templates of §1.4 |
| Latency | parse ≈ 3 µs ([verified today], `timeit` of `parse('defer 364 to next Tuesday', …)`); the request is one HTTP round trip: on the same machine, milliseconds; from a phone through the tunnel, unmeasured (tens to low hundreds of ms, [recalled]) |
| Cost | $0 |
| Validates | the grammar's own refusal rules; the batch validator at Apply (`batches.SETTABLE`, `batches.py:14`); the page stages decisions through `reviewAct` |
| May never | guess: anything it cannot read fully goes to tier 2; name a task other than the card without a read-back; take a clause with a contrast word as an outcome; write (it returns changes for the page to stage, like `POST /api/commands`, which does not write) |

It could also run in the page in JavaScript, but then two copies of the grammar exist; keep it on the
server and let tier 0 stay tiny.

### 2.4 Tier 2: one small no-tools model run

| | |
|---|---|
| Input | the normalised utterance, the card as data (the `attached_rows` JSON of the card **and its linked tasks**, the Review reasons, the last two exchanges of the session), today and the time zone |
| Output | the chat turn's shape plus two keys: `{"spoken": "≤ 2 sentences", "decision": "done\|drop\|keep\|later\|null", "later_until": "YYYY-MM-DD\|null", "outcome": "…\|null", "proposals": [same as chat-turn.md], "escalate": false}`; `escalate: true` when the sentence needs mail, the calendar, recordings or other tasks |
| Validates | `chat._clean_proposals(conn, cfg, data, seen={}, tasks_seen=set(), attached=[card, *linked])` (`chat.py:306-434`), the same cleaner as a chat turn: ops in `CHAT_OPS` (`:46`), existing rows, `_clean_task_fields`, `_reminder_fields`, links only between attached tasks, `link_thread` impossible (no thread was read). The decision is mapped to `reviewAct` by the page. A wrong JSON shape applies nothing (as ripple: CLAUDE.md "a wrong shape applies nothing") |
| Latency | bounded by the measured no-tools runs: 2.9 s median (mail triage, sonnet), 4.0 s (summary), 5.6 s (ripple); plus ~1 s of process start; a haiku floor of 2.4 s. Expect **about 2 to 3 s on haiku, 3 to 6 s on sonnet** |
| Cost | the 38k-token cached prefix dominates a tiny prompt: about $0.001 on haiku (probe), about $0.02 to $0.05 on sonnet (ripple $0.027, triage $0.056 with larger prompts) |
| May never | call a tool, read mail, name a task outside the card and its linked tasks, write anything, invent an id or a date not said (dates in the utterance only), propose `done`/`drop` unless the utterance says so (the Review place rule, `chat.py:60`), merge (a merge always goes through the tray with a read-back; tier 3 may propose it) |

**Where it lives.** A new run kind next to filing and ripple, which are the templates for a small
no-tools run (`filing.py:128`: `runner(cfg, KIND, prompt, allowed_tools=[], tools="", workdir=…)`;
`ripple.py:930` the same):

- `factotum/voice.py` (`build_prompt`, `interpret`, `parse`), `prompts/voice-utterance.md`;
- a line in each of `runner.BUDGETS_USD`, `runner.TIMEOUTS_S`, `runner.MODELS`
  (`factotum/claude/runner.py:34-40`), e.g. `"voice": 0.05`, `20`, `"haiku"` — CLAUDE.md: "a new run
  kind needs a line in all three and in the example", and a test pins the tables to the example;
- `config/factotum.example.yaml` `claude.models`, `claude.budgets_usd`, `claude.timeouts_s`
  (`factotum.example.yaml:243-250`), and `tests/fixtures/factotum.fixture.yaml`, and the starter;
- `claude.max_turns.voice: 1` (the default is 40, `runner.py:42, 79`);
- `log_run(conn, "voice", …)` per utterance, so Settings' 7-day cost includes it;
- use `prompts.fill` and `prompts.one_line`/`json_breaks` for every value (CLAUDE.md "Every prompt
  builder fills its {{placeholders}} with prompts.fill"); the utterance is user data, the card's text is
  data written by many people;
- `runner.unavailable` (`runner.py:116-125`): when Claude cannot run, tier 2 and 3 say so once and the
  session keeps working on tiers 0 and 1.

It is not a scheduled paid run (it runs only when the owner speaks, like a chat turn), so "absent means
off" does not force a switch; a model choice in Settings would be a new `SETTINGS_KEYS` entry with
`DEFAULTS`, `CHOICES`, a control and the example line (CLAUDE.md "Settings-tab keys are a whitelist").

**Is the CLI floor too slow?** Not for tier 2 if the page answers at once. A spoken "Okay…" or an
earcon within 200 ms of the end of speech, then the result 2 to 4 s later, is tolerable for a decision
the owner just made; the screen shows the parsed result as soon as it arrives. It is too slow for the
common words (done, keep, later, next), which is why tiers 0 and 1 must catch them. If tier 2 is warm
(§3) and runs on haiku with a replaced system prompt, 1 to 2 s is plausible but unmeasured.

### 2.5 Tier 3: the full chat turn with tools

| | |
|---|---|
| Path | `POST /api/chat/turn` with `origin: "review"`, `task_id` (`api.py:933-1013`) → `chat.turn` (`chat.py:482-559`), the thread shared with the opened task (`place_thread`, `chat.py:87-108`); a suggestion card uses `origin: "suggestion"` with `card_id` and the draft (`chat.py:225-236`) |
| Latency | **10.9 s median, 15.3 s p90 from a Review card** [verified today]; the stream already emits `status` events per tool ("Searching mail · …", `chat.py:50-53, 456-462`), which the session can speak as progress |
| Cost | **$0.069 median**, budget $0.60 (`runner.py:34`) |
| Validates | `_clean_proposals` (as above, with the run's `seen.jsonl` and `tasks_seen.jsonl`), the page's tray and Apply, `batches.apply` |
| May never | write (rule 1 of `prompts/chat-turn.md`); send mail (drafts only); run two turns in one thread (409, `api.py:962`): the session must queue or say "still working on the last one" |

The reply is a final JSON object (`reply_md`, `proposals`); text cannot be spoken until the run ends
unless the stream is parsed incrementally (`--include-partial-messages`, [documented]). Speak the
tool progress instead.

### 2.6 Routing between tiers

1. Tier 0 word, in a state where it means something → page.
2. Else POST to a new `/api/voice/utterance` (or a mode of `/api/commands`) with the card → tier 1.
3. Tier 1 returns `None` → tier 2 (if Claude runs here), with the card.
4. Tier 2 says `escalate`, or the utterance is a question tier 1 did not template → tier 3, telling the
   owner first ("Let me look that up", then tool progress).
5. Any tier may answer "I didn't catch that" instead; two misses in a row offer the buttons.

The session should never fall from tier 1 to tier 3 directly for a decision: tier 2 is the cheap,
tool-less place where a sentence becomes a decision.

---

## 3. Anything faster than a headless CLI run?

| Option | Available now? | Notes |
|---|---|---|
| Direct Messages API with an API key | **No.** `.env.example` names no `ANTHROPIC_API_KEY` (its names: FACTOTUM_CONFIG, FACTOTUM_DB, NOTION_TOKEN, FACTOTUM_MCP_TOKEN, SLACK_TOKEN_EXAMPLE, DISCORD_BOT_TOKEN, MAILBIRD_TOKEN, NTFY_TOKEN, CANVAS_TOKEN, SMTP_*, IMAP_*) [verified today]; `runner.py` only shells out to `claude` [verified today] | Fastest (no process, no 38k prefix) but pay-as-you-go billing, a new secret, and a second code path. The owner runs on a subscription. Would have to be an optional config key, absent = off |
| `claude --bare` | **No** without an API key: "bare mode doesn't use your subscription login … never reads OAuth credentials" (https://code.claude.com/docs/en/headless, [documented]); CLAUDE.md gotcha 2026-11-01 ("--bare reports Not logged in") | The runner never passes it |
| `--system-prompt` replacing the default | **Yes**, a flag of this CLI (`claude --help` [verified today]) | Removes most of the ~38k-token prefix for a no-tools run; latency gain unmeasured. Worth a measured probe before deciding the tier-2 model |
| Session resume (`--resume`) | Yes, already used by chat (`chat.py:518-530`, `runner.py:87-88`) | Still a new process per utterance; the whole transcript is sent again (cached). Keeps context, not speed |
| **Streaming input: one long-lived `claude -p --input-format stream-json --output-format stream-json` process per voice session** | **Yes, documented**: `--input-format stream-json` is "realtime streaming input" (`claude --help` [verified today]); the Agent SDK's streaming input "keeps an Agent SDK session open so your application can send it more than one message while it runs", with `interrupt()` (https://code.claude.com/docs/en/agent-sdk/streaming-vs-single-mode, [documented]); the Python SDK is a wrapper that "runs the Claude Code binary" (https://code.claude.com/docs/en/agent-sdk/overview, [documented]) | Saves process start (~1 s), MCP server start for tier 3, and keeps the cache warm. Costs: a new long-lived runner mode (today's runner is one-shot), a process per session to kill on exit and on a timeout, `FACTOTUM_RUN_DIR` shared across utterances so `seen.jsonl`/`tasks_seen.jsonl` would need per-utterance offsets, and the `commit_lock`/`chat_busy` rules still hold. Untested here |
| Agent SDK (Python) instead of the CLI | Technically yes; same login as the CLI it wraps [documented] | The overview says: "Unless previously approved, Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products, including agents built on the Claude Agent SDK" [documented]. Factotum runs the owner's own CLI login today; whether a tool handed to other people may do the same through the SDK is a policy question to settle, not a technical one |
| Prompt cache | Already in effect: probe 2 read 36k tokens from cache [verified today] | Cache lifetime is minutes [recalled: 5 min default]; a session with an utterance every half-minute keeps it warm |

**Recommendation for v1:** one-shot CLI runs, tier 2 on a new run kind, measured with and without
`--system-prompt`. Keep the long-lived streaming mode as a phase 2 optimisation, only if tier 2's
measured wall time is above ~3 s in practice. Do not add an API-key path for v1.

---

## 4. Turning a reply into speech

What a chat reply holds now: `reply_md` (Markdown, "a few sentences", `prompts/chat-turn.md` rule 5),
task ids as `Task-388`, ISO dates, sender names, sometimes URLs and email addresses, and
`proposals[]` with a model-written `label` ("Task-388 → due Thu Nov 5") plus fields.

What is wrong for the ear: Markdown marks, bullet lists, `Task-388` (read as "task dash three eight
eight"), ISO dates, times like `15:03`, email addresses, URLs, ids of mail threads, long lists, and
length (a 650-character Review reply, the measured median, is about 40 s of speech).

Recommendation, three layers:

1. **A `spoken` field in the reply**, asked for only when the place is a voice session (a sentence in
   `PLACE_TEXT`-style place text, e.g. a `voice` flag beside `review`, `chat.py:59-65`, and one line in
   the Output section of `prompts/chat-turn.md:27-56`): at most two sentences, no Markdown, tasks by
   their name (not their number), dates as weekday and day, people by name, never an address or a URL,
   and never a description of the proposals (Python reads those). `reply_md` stays for the screen.
2. **A server-side flattener** (`speakable(text, named)`) applied to `spoken`, or to `reply_md` when
   `spoken` is missing (tier 3 on an older prompt, a failed shape): strip Markdown, replace `Task-N`
   with the task's name from `chat.named` (`chat.py:111-117`), ISO dates with spoken dates, drop URLs
   and addresses, cut at a sentence boundary under ~300 characters with "More is on the screen."
   Python owns the cut, as `summary.py` owns staleness.
3. **Proposal read-backs are built by Python from the cleaned fields**, never from `label`:
   "Due Friday, November 20th", "Remind you Monday at 9", "Mark it done, noting: already emailed
   her", "Link it as waiting on *Get the dataset*". The page already has the words in `changeWords`
   (`app.js`, per CLAUDE.md) and the server has `relations.side_word`; one shared table of templates
   keyed by op is the safety check of §5, so it must describe exactly what will be staged.

Text written by strangers (mail bodies, recordings) can reach `spoken` through the model. TTS reading
it is low risk, but it must not reach the read-back, which is why (3) is Python's.

---

## 5. Safety: what may be staged without asking

Facts that frame this:

- **Staging is not applying.** Staged changes live in the page (`staged` map, persisted in
  `localStorage`, `app.js:590-593`) until Apply; `batches.apply` is the only write path, and every
  batch is undoable (CLAUDE.md "Claude decides, Python writes").
- **Two Review actions apply at once today**: a suggestion card's **Create** (`POST
  /api/review/suggestions/{id}/create`, one review batch, `app.js:4017-4033`, `api.py:638`) and
  Accept on some evidence (`data-rv-accept`, `app.js:2190`). Dismiss stages (`app.js:4016`).
- The direct-write rules in `mcp_server` exist because "nobody taps Apply" (CLAUDE.md, review
  2026-11-04): `_validate_changes` rejects instead of dropping (`mcp_server.py:858`), `task_id` must
  be `type(...) is int`, a merge is never direct (`direct` refuses it), direct changes are capped per
  hour (`apply_changes`, `mcp_server.py:1118-1134`, `server.mcp_apply_hourly_cap`, default 100), and
  `_rows_written` counts the rows (`:823-833`).

Proposed policy:

| Class | Examples | Rule |
|---|---|---|
| A. Decision on the card on screen, from tier 0/1 | done, drop, keep, later (tomorrow), top, long-term | **Stage at once**, speak a short echo naming the task ("Done: *Email the lab*."), accept "undo"/"no" as the next utterance to unstage; move on only on "next" or after the echo if auto-advance is on |
| B. Anything with a date | later until X, push to X, a reminder time | Stage at once **and** speak the resolved date ("Later, back Friday, November 20th"); `next <weekday>` read with "in 8 days" |
| C. Anything naming another task by number | `done 412` during card 389 | **Read back first** with the other task's name; stage on "yes" |
| D. A decision with a clause taken as an outcome | "drop that, I already emailed her" | Stage at once with the outcome read back ("Dropped, noted: already emailed her") — the outcome is undoable text |
| E. Everything a model proposes (tiers 2 and 3) | reminders, notes, links, creates, text edits, reopen | **Read back first**, from Python's words; "yes" stages all, "just the first", "not the link" etc. go to tier 2 against the pending list; the screen shows the ticks (the card's proposal list already has ticks, `stagePlace`, `app.js:2601-2611`) |
| F. Merge, unlink, reopen, more than three changes at once | | Read back first, one by one or as a counted summary, and never auto-advance |
| G. Suggestion card Create (applies now) | "yes, make it" | **Read back the draft** (name, date, project) and create on "yes"; the toast's Undo is spoken as "Say undo to take it back". Better: let a session stage the create (the tray can already carry creates: `from_chat` in `chat.resolve_staged`, `chat.py:660-703`), so voice never applies; a system change worth considering |
| H. Apply | "apply", "save", "that's all" | See below |

**Should Apply be spoken?** It can be done safely, but only as: a spoken summary built by Python from
the tray ("Apply 9 changes: 4 done, 2 later, 1 dropped, 1 reminder, 1 note?"), a "yes", and the
batch's Undo spoken afterwards ("Applied. Say undo to take it all back"). Because a batch is one Undo,
that is safer than the connector's direct writes, and no hourly cap is needed for an owner who
confirms. If the owner prefers, leave Apply to the screen and say "9 changes are waiting in the tray."
This is the owner's call; the default I suggest is spoken Apply **with** the summary and confirmation,
never on a single word in passing, and never from a model's proposal.

Which existing direct-write rules apply to voice:

- `_validate_changes`' strictness applies wherever voice causes an immediate write (G, H): a value the
  cleaner would drop must be refused and said, not lost. For staged changes, `_clean_proposals` (chat)
  is the right validator and is already strict enough for a tray.
- The integer-id rule applies to the session grammar: ids come from the card or from digits, never from
  a model's string.
- "A merge always goes to the tray" applies: never merge from voice without class F read-back.
- The hourly cap does not apply (nothing writes without the owner's "yes").
- The "one turn per thread" rule (409) applies to tier 3.

Speech-specific guards:

- Accept a tier 0 word only as a whole utterance, so a TV saying "…and we're done here" mid-sentence
  does not count; a session should prefer push-to-talk or a short listening window after each prompt
  over always-on listening (open question for the speech-recognition researcher).
- If the recogniser reports a confidence (the Web Speech API has a `confidence` field; some engines
  always report 0, [recalled]), treat a low one on a class A word as a question: "Done?".
- Refuse split numbers (`done 3 89`) as a question, as in §1.3.
- After two misunderstandings in a row, say what can be said and show the buttons.

---

## 6. Open questions (could not find out here)

1. Tier 2's real wall time on haiku and sonnet with `--system-prompt` replacing the default (probe
   budget spent; a fourth probe was not allowed). This decides the tier-2 model.
2. Whether a resumed tier-3 turn from a Review card is faster than a first turn: the log holds no such
   turn.
3. Phone-to-server round trip through the tunnel (Cloudflare) for tier 1: not measured.
4. Whether streaming input (`--input-format stream-json`) behaves with the runner's flag set
   (`--strict-mcp-config`, `--tools ""`, `--max-budget-usd` across many messages, `--max-turns`
   per message or per session); documented, not tested.
5. Policy: whether a tool handed to other people may drive each person's own subscription login
   through the Agent SDK (the overview's note) as it already does through the CLI.
6. Whether a due-date change spoken on a Review card should count as Keep (a decision) or only as an
   edit that leaves the card up.
7. Whether suggestion-card Create should stage in a voice session (and perhaps everywhere) so that
   voice never applies anything except a confirmed Apply.
8. Filing's floor: no `filing` row in the run log, so the measured no-tools floor rests on ripple,
   triage and summary instead.
