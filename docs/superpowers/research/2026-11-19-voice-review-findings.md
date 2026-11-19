# A spoken Review session: findings

2026-11-19. Research only; nothing in the code was changed. Code references are for commit `75d2c77`.

This document reconciles six research reports into one set of findings to design from. The reports hold
the detail and are cited as R1 to R6:

| | Subject | File |
|---|---|---|
| R1 | Review on the server | `docs/superpowers/research/2026-11-19-voice-review/01-review-backend.md` |
| R2 | Review in the page | `docs/superpowers/research/2026-11-19-voice-review/02-review-page.md` |
| R3 | From an utterance to a change | `docs/superpowers/research/2026-11-19-voice-review/03-utterance-to-change.md` |
| R4 | What a browser can do with voice | `docs/superpowers/research/2026-11-19-voice-review/04-browser-voice.md` |
| R5 | Speech engines | `docs/superpowers/research/2026-11-19-voice-review/05-speech-engines.md` |
| R6 | The conversation and autonomy | `docs/superpowers/research/2026-11-19-voice-review/06-conversation-and-autonomy.md` |

The reports are kept beside this file, in `docs/superpowers/research/2026-11-19-voice-review/`.

Evidence marks:

- **[V]** verified today by a researcher: run, measured, or read in a primary source.
- **[I]** checked again today by the integrator: the code read at the lines given, the web page fetched, or
  the measurement repeated.
- **[D]** documented, not tested.
- **[R]** recalled from memory, not checked.

Nothing in this document was tested on a phone.

---

## 0. The findings in brief

1. **Speech is not the slow part and not the hard part. Review's own shape is.** A Review decision is a
   rule that exists only in the page's JavaScript, the staged changes live in one browser's storage, and
   there is no session or current item anywhere. A spoken session needs all three on the server.
2. **Do not build on the browser's speech recognition on the iPhone.** On iOS 27 it goes deaf after the
   first session until the app is force-quit, and Apple has not answered the bug [I]. The path that works
   on every target is: record in the page, find the end of the utterance in the page, send the clip to
   Factotum, transcribe there.
3. **This machine can run good speech locally** (RTX 3070: Whisper large-v3 in about 0.45 s for a 5 s
   clip [V]). A paid stack costs about $7 to $9 a month at 20 minutes a day [V, list prices]. A new person
   may have neither, so each direction needs a provider choice with the browser as the free default.
4. **Shorthand must never go to Claude.** "Done", "keep", "later Friday", "next" are parsed by Python in
   milliseconds. A whole sentence goes to one small no-tools run: 2 to 3.5 s as a new process, **1.0 to
   1.4 s when one process stays open for the session** [I, measured today]. Questions that need mail or
   the calendar go to the existing chat turn: 11 s median, $0.065 [V].
5. **The owner's own data says what to build around voice.** Nine of ten "new" items are simply kept, four
   of five suggestion cards are dismissed, and every link suggestion so far was accepted [V]. A session
   that asks 22 separate questions is an interrogation; grouping and a recommended answer turn most items
   into one word.
6. **Recommendation:** build the session on the server (way B in section 7), in an order where each stage
   also improves Review in the page, and run two short tests on the owner's iPhone before the audio stage.

---

## 1. What Review is today

### 1.1 The items

Review shows two families of item (R1 §1).

**Task cards.** An open task with at least one *reason*. `lifecycle.review_reasons`
(`factotum/lifecycle.py:73-133`) decides the reasons; `build_review_queue` (`:177-189`) sorts by the
primary reason's place in `REVIEW_ORDER` (`:14`), then due date, then score. Fourteen reasons are
produced: last call, thread updated, mentioned in Slack, planner suggests, ripple suggests, free now, a
task it waited on closed, all steps done, a linked task changed, suggested link, back from Later, new,
soft date slipped, fading soon. Two more are defined and never produced (`back`, `reminder_missed`:
nothing sets their flags) [V, R1 §1.2].

**Suggestion cards.** Unjudged evidence with no task, of kind `proposal` or `slack_mention`
(`factotum/review_queue.py:13, 29-30`) [I]: a task that does not exist yet, suggested from mail, by the
planner, by a ripple or from a Slack mention.

`GET /api/review` serves the first 15 task items (`lifecycle.review_cap`) and every card
(`factotum/api.py:526-593`) [I]. The page shows cards only when no task item is left on the page
(`factotum/web/app.js:1936-1937`) [I], so a card about tomorrow comes after every task. Cards never
expire.

Live queue today: 16 task items and 6 cards. Ten of the 16 are "new", eight of them from two recordings
[V, R1 §3.4, R6 §0].

### 1.2 The decisions

| On | Decision | What is staged |
|---|---|---|
| Any task card | Done, Drop | the op, with the note as `outcome` when it is 300 characters or less |
| | Keep | `keep` (the server sets `last_reviewed_at`) |
| | Later (tomorrow, Friday, next week, a date) | `set {snoozed_until, last_reviewed_at}`; never touches `due` |
| | with every one of the four | `judge_evidence reviewed` for each pending evidence row on the card; a note on Keep or Later as `add_note` |
| A planner or ripple proposal on the card | Accept, Dismiss | the proposed change plus `judge_evidence applied`, or `dismissed`. Only an accepted `done` or `drop` counts as the card's decision; anything else leaves the card waiting |
| Mail about the task | Follow its thread, Not about this | `follow_thread` plus the verdict, or `dismissed` |
| Suggested link | Link, a step of it, waits on it, same task, Merge, Not related | `link` or `merge`, or `judge_evidence dismissed`. The card still needs a decision, and Keep does not answer the suggestion |
| Extras | Pin to Top, Long-term, due date, reminder, the editor, the card's conversation | staged beside the decision |
| Suggestion card | Create | **written at once**, as one `review` batch |
| | Dismiss | staged `judge_evidence dismissed` |

The four decisions are `reviewAct` (`app.js:4198-4229`), with the evidence verdicts at `:4227` [I]. Accept
is a closure inside `wire()` (`:3975-3989`) [I]. Create is `:4017-4033` and `api.py:638-723`. R1 §2 has
every row with its batch op.

### 1.3 From a decision to a batch

1. A click or key calls `reviewAct`, which puts entries in `staged`, a `Map` saved to
   `localStorage['factotum.staged']` (`app.js:529-538, 590-594`) [I]. The key is the bare task id for a
   decision and a suffixed key for everything beside it (`stageKey`, `:861-870`) [I].
2. The current card is "the first item of the loaded page whose task id is not a key in `staged`"
   (`app.js:1933, 1945`) [I]. Staging a decision is what moves to the next card. There is no skip, no back
   and no cursor.
3. Apply in the tray posts the whole tray to `POST /api/batches/apply` with source `review`
   (`app.js:614-668`). `batches.apply` writes one undoable batch.
4. On the Review tab the page then posts `/api/review/finish` (`api.py:744-749`) [I], which sets
   `state.last_review_at` (read by the evening push, the brief and the planner), reloads the queue and
   waits on a "Batch applied" screen for a press.

Four things on Review write at once instead of staging: Create on a suggestion card, Undo under "Since
you last looked", Apply and Dismiss on a skill's staged batch, and "Show the message in Mailbird" (which
calls a Mailbird tool) [I for the first three, V for the last, R1 fact 4].

`GET /api/review` itself writes: it records the day's link-suggestion set and stamps `last_offered_at` on
the 15 rows of the page (`api.py:547-548, 585-591`) [I]. `expire` acts only on rows stamped that way. The
number beside the tab comes from `review_queue.count`, which reads only.

### 1.4 What is scattered and would have to be in one place

| Knowledge | Where it is now | Problem for a spoken session |
|---|---|---|
| What a decision on an item is (the op, the verdicts, the note rule) | `reviewAct` in the page, only | No Python function knows it. R1 ran `keep` without the verdicts on a copy of the database: two of three cards came back [V] |
| What Accept, Follow, Not about this, Create and Dismiss do | closures inside `wire()` | Nothing but a click can run them |
| Which item is current | worked out twice inside the page, from the tray | No cursor, no skip, no back, nothing another device can see |
| What is staged | one browser's `localStorage` | The phone and the desktop never agree; a page the phone ends takes the tray's place in the queue with it |
| The Review vocabulary | buttons and six keys | `commands.parse` is a different grammar: it needs task numbers, has no Keep, and its `push` moves `due` |
| The words for a card | HTML builders (`reasonText`, `sinceHtml`) | Nothing produces plain sentences |
| The same decision by conversation | `stageProposalAs` (`app.js:2256-2261`) | A `done` or `drop` staged from a reply judges no evidence, and a Later from a reply carries no `last_reviewed_at` [I]. Button and conversation stage different batches |

Two page faults found on the way:

- **Pin to Top and Long-term on a Review card are staged under the bare task id** (`reviewAct` → `act` →
  `stage(id, …)`, `app.js:4203, 595-609`) [I, read in code, not run in a browser]. The card therefore
  leaves the page as if decided, with no review recorded, and comes back after Apply. The Accept path
  stages the same ops beside the decision (`:3983`), which is what the button was meant to do.
- R2 suspects the next card can be drawn with the Later menu still open (`ui.laterOpen` is reset at
  `:4228`, after the renders). Not run in a browser.

---

## 2. What the platforms allow

### 2.1 Support table

Condensed from R4 §6, which has the versions and sources. "Recognition" is the browser's own
`SpeechRecognition`.

| | Browser recognition | Browser speech output | Microphone capture | Wake lock |
|---|---|---|---|---|
| Chrome desktop | Yes. Google's servers by default; on the device with `processLocally` (139+). Phrase hints from 142 [I] | Yes. Google voices cut off at about 15 s [D] | Yes | Yes |
| Edge desktop | Yes, Microsoft's servers; the local model is behind a flag [V] | Yes, the best free voices [D] | Yes | Yes |
| Firefox desktop | Yes from 160, on the device only; phrase hints accepted and ignored [I] | Yes, system voices | Yes | Yes |
| Safari on a Mac | With caveats: needs Siri or Dictation on [D] | Yes | Yes | Yes |
| Chrome on Android | Google's servers only; `continuous` has no effect [I] | Yes | Yes | Yes |
| Firefox on Android | No [I] | Yes | Yes | Yes |
| iPhone, Safari tab or installed app | **Not usable** (below) | Compact system voices only [D] | Yes, with open questions | Yes; in the installed app since iOS 18.4 [V] |

### 2.2 Certain

- **iPhone recognition is not a foundation.** WebKit bug 321436 (filed 2026-08-10, status NEW, last
  changed 2026-11-18, no reply from a WebKit engineer) [I,
  <https://bugs.webkit.org/show_bug.cgi?id=321436>]: on iOS 27.0 to 27.3 beta the first recognition after
  launch works and later ones get no result, no error and no end, in Safari, in Chrome on iOS and in
  Home Screen apps, until the app is force-quit. Reporters also say recognition hears nothing while
  `speechSynthesis` speaks, cannot run beside `getUserMedia`, and went deaf with the audio session type set
  to `play-and-record`. The same reports show that the API now exists in installed apps; the older
  statement that it does not (bug 225298, 2021) is out of date. The conclusion is the same either way.
- **Capture works everywhere**: `getUserMedia`, `AudioWorklet` and WebAssembly are in every target [V].
  16 kHz mono 16-bit audio is the one format every recogniser takes; a 5 s utterance is 160 KB.
- **The owner's phone is an iPhone with the app installed**: the one push subscription in the live
  database is on `web.push.apple.com`, labelled iPhone [I, read-only]. Its iOS version is not known.
- **A hands-free session is foreground only on the iPhone.** A locked or backgrounded installed app loses
  audio, and its microphone track goes muted or ended [D]. The screen wake lock keeps the phone awake on
  the counter. The session must pause on `visibilitychange` and resume on a tap.
- **Audio and the microphone start only inside a tap** [R, the page already follows this rule for
  notifications at `app.js:309-311`]. A notification or a shortcut can only open a Start button.
- **Every state-changing `/api` call must be JSON** (`JsonOnlyMutations`, `api.py:243-257`) [I]. A clip
  goes up as base64 inside JSON (about 213 KB for 5 s); spoken audio comes back on a GET.
- **A `fetch` per utterance fits the existing sign-in handling**; a WebSocket through Cloudflare Access
  does not and is not needed (R4 §3.4).
- **Echo.** The browser's echo canceller reliably removes only what the page itself plays through an
  audio element or Web Audio [R]. Speaking through `speechSynthesis` is outside it. On a phone speaker the
  safe design is half-duplex: do not listen while speaking, and interrupt with a tap. Earbuds remove most
  of the problem.
- **No wake word.** The page must be in front anyway, and the usable library needs a commercial key
  (R4 §4.4).
- **Anthropic offers no speech API**, and Claude Code's `/voice` is dictation for the interactive
  terminal only [V, R5 §5].

### 2.3 Only a test on the owner's iPhone settles these

Ordered by how much the answer changes the design. R4 §7.3 has the full list of eleven.

1. With the microphone open, does reply audio play through the main speaker at a usable volume? iOS
   switches to a play-and-record session and may move playback to the earpiece or make it quiet [D]. If it
   does, the session must release the microphone while it speaks, at the cost of a gap each turn.
2. Does the voice-activity detector (`@ricky0123/vad-web`: Silero on onnxruntime-web, an 11 MB WebAssembly
   file) load and run in the installed app, and does it fire on Factotum's own voice at cooking volume?
3. Is the microphone permission kept across launches of the installed app?
4. After the phone locks or another app comes to the front, does one tap bring back both the microphone
   and audio?
5. With the ringer switch on silent, which of `speechSynthesis`, Web Audio and an audio element still
   plays? Sources conflict.
6. Do earbud presses reach the page (Media Session) while the microphone is open, and how bad is playback
   once the earbuds switch to the hands-free profile?
7. Does the wake lock hold for 20 minutes?

A throwaway page of about a hundred lines answers 1 to 5 in a quarter of an hour. It should be run before
the audio stage is designed in detail.

---

## 3. Speech in and speech out

### 3.1 The machine

RTX 3070 with 8 GB, visible to CUDA from WSL; 16 cores; 47 GB RAM; Python 3.12.3. No ffmpeg, no cuDNN in
the system libraries, nothing speech-related in Factotum's `.venv`. Whisper large-v3 and small are
already in the Hugging Face cache. The GPU is shared with the owner's other work [V, R5 §1].

### 3.2 Speech to text

| Engine | Where | One 5 s utterance | Cost | Notes |
|---|---|---|---|---|
| Whisper large-v3 (transformers) | this GPU | 0.43 to 0.47 s warm [V] | $0 | measured; first run 1.7 s; model load 7.6 s |
| Whisper small | this GPU / this CPU | 0.15 to 0.21 s / 1.2 s [V] | $0 | measured |
| faster-whisper `large-v3-turbo` | this GPU / CPU | 0.2 to 0.4 s / 1.5 to 3 s [estimate, not measured] | $0 | takes `hotwords` and `initial_prompt` for the card's words [V, source read]; needs the cuDNN 9 wheel and a library path in the systemd unit |
| OpenAI `gpt-transcribe` | service | about 0.3 to 1 s [R] | $0.0045/min [V] | keyword hints |
| Deepgram Nova-3, Flux | service | under 1 s [D] | $0.0043 to $0.0065/min [V] | Flux detects the end of a turn itself |
| Groq Whisper turbo | service | "near-instant" [D] | $0.04/hr, 10 s minimum a request [V] | |
| The browser's own | the browser's vendor | 0.3 to 0.8 s [R] | $0 | section 2; audio goes to Google, Microsoft or Apple unless on the device |

R5 §3 and §4 list the others (Parakeet, Moonshine, Vosk, AssemblyAI, ElevenLabs Scribe, Voxtral). For a
3 s command the benchmark differences matter less than three things: hints from the current card, a quick
and correct end of utterance, and not inventing words from kitchen noise. Whisper is known to write
"Thank you." over silence [R]; the guards are the voice-activity gate, the model's own no-speech score and
ignoring a transcript that is only a filler.

### 3.3 Text to speech

| Engine | Where | First audio | Cost | Notes |
|---|---|---|---|---|
| Kokoro-82M | this GPU / CPU | under 0.2 s / 0.2 to 0.8 s [estimate, not measured] | $0 | Apache-2.0; 54 voices; needs `espeak-ng` from apt, or the ONNX build |
| Piper | CPU | fast on a CPU [D] | $0 | GPL-3.0 since 2025: run it as a subprocess, never import it. Factotum declares no licence |
| ElevenLabs Flash v2.5 | service | about 75 ms plus the network [D] | $0.04 per 1,000 characters [V] | about $8.40 a month at the volume below |
| Deepgram Aura-2 | service | about 90 to 250 ms [D] | $0.03 per 1,000 characters [V] | about $6.30 a month |
| OpenAI `tts-1` | service | slower [D] | $15 per million characters [V] | about $3.15 a month |
| The browser's own | the device | about 0.1 s | $0 | robotic on the iPhone [D]; Edge's are good; split long text by sentence |

Monthly figures assume 20 minutes of review a day: about 150 minutes of the owner's speech and 210,000
spoken characters a month (R5 §11). A typical paid pair is about $9 a month.

Two things cut the wait whatever the engine: synthesise the next card while the owner is deciding the
current one, and keep the handful of fixed confirmations ("Kept.", "Done.") as stored audio.

### 3.4 What was ruled out

Realtime speech-to-speech models from another vendor (OpenAI's live models at $0.05 a minute, Gemini Live)
would make that vendor's model the one that speaks and decides, against "Claude decides, Python writes",
for a gain of about a second a turn. Voice-agent frameworks (Pipecat, LiveKit Agents) expect to own the
conversation loop and call a model over HTTP; Factotum's model is a command-line run and its decisions end
as staged batches (R5 §9).

### 3.5 The seam

R5 §12 sketches it and the reports do not disagree: two small interfaces, `Ear.transcribe(pcm16, rate,
hints, lang)` and `Voice.speak(text, voice)`, each with a `ready()` that returns the sentence Settings
shows when it cannot run. `browser` is not a server class; it tells the page to do that direction itself.
Hints are built in Python from the card, capped and stripped like any text a stranger may have written.
Turning dates, task numbers and markdown into speakable words happens once, above the seam.

---

## 4. From an utterance to a change

### 4.1 The tiers

| Tier | Handles | Wait after the owner stops speaking | Cost | What checks it |
|---|---|---|---|---|
| 1. A Review grammar in Python | the decisions, Later with a date, the answers to a proposal or a link, next, back, repeat, more, undo, how many left; questions the card's own data answers ("when is it due", "what waits on this") | recognition plus milliseconds: about 1 to 2 s in all | $0 | its own refusal rules; the Python decision function; `batches.apply` at Apply |
| 2. One small run, no tools | a whole sentence about this card: "push it to Monday and remind me at nine", "keep them all except four, four is done" | 1.0 to 1.4 s more with a process kept open, 2 to 3.5 s as a new process | about $0.002 to $0.01 API-equivalent | `chat._clean_proposals` with the card and its linked tasks as the only tasks it may name; a wrong shape applies nothing; the read-back is built by Python |
| 3. The existing chat turn | questions that need mail, the calendar, recordings or other tasks | 11 s median, 18 s at the 90th percentile | $0.065 | as today: the cleaner, the tray, Apply |

The page itself handles only what must work with no round trip: a tap, Stop, and stopping speech. With
transcription on the server the clip, the transcript, the parse and the reply are one request, so a second
grammar in JavaScript would only be a copy that pytest cannot test (R3 proposed one; R2 argued against).

### 4.2 Why `commands.parse` is not the grammar

R3 ran the real parser on speech-shaped text [V]: a trailing full stop gives nothing, number words give
nothing, "push it to Friday" gives nothing, "this Friday" and "in two weeks" give nothing, and **"done 3
89" closes Task-3 and Task-89**. It has no Keep, and its `snooze` is not Review's Later. The Review grammar
is a separate function in front of it: it cleans the transcript, treats "it" or no number as the current
item, maps decision words to the decision function, passes field verbs on to `commands.parse` with the id
filled in, and refuses a number that arrives in two pieces. `dates.resolve_when` needs "this Friday", "in
N days", "the 25th", "December 1st"; it is shared with the add line, so both get tests.

A decision followed by a clause ("drop it, I already emailed her") is the decision plus that outcome,
which is what a typed note does today. A clause with "but", "except", "until", "unless" goes to tier 2:
"done, except the slides" must not close the task.

### 4.3 The latency floor of a headless run

Measured today, runner-shaped flags, no tools, a lean prompt with one card inlined.

| How | Model | Wall time per utterance | Source |
|---|---|---|---|
| A new process each time | sonnet | 1.8, 1.8 s | R5 §10 [V] |
| A new process each time | sonnet | 2.8, 3.0 s | [I] |
| A new process, `--system-prompt` replacing the default | sonnet | 3.4, 3.1 s | [I] |
| A new process each time | haiku | 2.4 to 4.2 s (five runs across R3, R5 and today) | [V] [I] |
| **One process kept open** (`--input-format stream-json`) | sonnet | 2.5 s for the first, then **1.4, 1.4, 1.0 s** | [I] |
| One process kept open | haiku | 2.9 s for the first, then 2.2, 2.0, 1.7 s | [I] |
| A chat turn with tools | sonnet | 11 s median | R2, R3, R5 [V] |

What this settles:

- **Good enough for tier 2, not for shorthand.** One to three seconds after a sentence is tolerable with
  an immediate sound; the same wait after "keep" twenty times a session is not.
- **A process kept open for the session works with the runner's flags** and halves the wait. It also
  keeps the conversation: "actually make it Tuesday" was understood on the second turn. Four turns cost
  $0.0145 API-equivalent on sonnet. This was an open question in R3 and R5. Not tested: a session of 20
  minutes, how `--max-budget-usd` behaves over many messages, a long idle gap.
- **Replacing the system prompt gains nothing** for a no-tools run. With `--tools ""` the default prompt
  is about 3,300 tokens, not the 38,000 R3 saw in a probe that left the tools in.
- **Sonnet was not slower than haiku** in any measurement here. The reason is not known.
- R5 saw 15.7 s on one first call after an idle period. It was not reproduced today (first call 2.8 s).
  Starting the session's process when the session opens covers it either way.
- No faster path exists without new work: there is no API key in `.env.example`, and `--bare` needs one.

The probe scripts are
`docs/superpowers/research/2026-11-19-voice-review/probe/probe.py`
and `docs/superpowers/research/2026-11-19-voice-review/probe/stream.py`.

### 4.4 What may be staged without asking

Staging is not applying, and every batch can be undone, so the bar is lower than for the connector's
direct writes. R3 §5 and R6 §A2.7 agree on the shape:

- **Staged at once, with a two-to-four-word echo**: a decision on the item just spoken, from tier 1.
  "Undo" as the next utterance takes it back.
- **Staged at once, with the resolved date spoken**: anything with a date. "Next Tuesday" said on a
  Thursday resolves 12 days out today [V]; the echo must say the day and the date.
- **Asked first**: Drop or Done on a task with a hard date within two days, on Top, or with open steps or
  tasks waiting on it; a merge; anything that names another task by number; everything a model proposes;
  Create on a suggestion card while it writes at once; Apply.
- **Read-backs are built by Python from the cleaned fields**, never from the model's own label.
- **Silence never means yes.**

---

## 5. The conversation

R6 part A has a full scripted session over five live items. The rules that follow from it and from R1 §5:

### 5.1 What is spoken

- **One item's opening is 6 to 10 seconds, at most about 25 words.** Live task names run 4 to 5 seconds on
  their own; the task text runs 14 to 40 [V, R1 §5]. The text is read only on "more".
- **Order**: a short sound for the kind of item; why it is here, in five words or fewer; a shortened
  name; the one fact that decides it (the date and whether it is firm, who waits, what moved, how long it
  has been quiet); then a yes-or-no recommendation, or the two or three likely answers.
- **Said as words**: dates relative within a week ("Friday at two"), then "the thirtieth", then "December
  thirty-first"; people by display name; links as "a step of", "waits on", "frees", "same as"; the other
  task by its name.
- **Never spoken**: task numbers unless asked, scores, priority letters, effort codes, ISO dates, URLs,
  ids, markdown, and anything shaped like an email address. Two live `counterpart` values are addresses
  [V].
- **Built by Python** from the item `_review` already annotated (`rel`, `texts`, `since`,
  `link_suggestions`, `evidence`), in one new module, never by stripping HTML and never by querying links
  again. The templates join the test that keeps "blocked" and "dependency" out of everything a person
  reads (`tests/test_stage4_agent.py:462-466`) [I].
- **Model output is spoken through a flattener** and capped at two sentences, with the rest on the screen.
- The evidence that brings a "Thread updated" or "Mentioned in Slack" card (sender, subject) is not drawn
  on the card today (`app.js:1967` keeps only evidence with a change) [I]. A spoken card needs it, and so
  does the page.

### 5.2 The command set

Decisions: **keep**, **done**, **drop**, **later** with an optional day. Answers to a recommendation,
a proposal or a link: **yes**, **no**. On a suggestion card: **make it**, **dismiss**. Extras: **pin
it**, **long-term**, **due** a day, **remind me** a time, **note** some text.

Always available: **next** (leave undecided), **back**, **repeat**, **more**, **wait**, **continue**,
**undo**, **how many left**, **apply** (always confirmed), **that's all** (end the session), **stop**
(silence the speech, never a decision), **what can I say**.

Three collisions shape the list (R6 §A3.1):

- *drop*, *stop* and *top* rhyme. "Stop" is never a decision, pinning is "pin it", and a risky Drop asks
  first.
- *skip* sounds like *keep*, and Keep hides the item until something changes. The word for moving on is
  "next".
- A bare "done" is the task. "I'm done" asks whether to end the review.

### 5.3 Turn-taking

- **Default: the microphone opens after each prompt and closes at the end of the owner's speech.** A tap
  anywhere is always available as well, and is the fallback when the detector misbehaves. Push-and-hold
  needs a clean hand and is out.
- **End of speech**: about 0.8 to 1 s of silence after a known command word, about 1.5 s after an open
  sentence, so "push it to… Monday" is not cut [D, R6 §A4.2].
- **Interrupting by voice only with earbuds.** On the phone's speaker a tap stops the speech. Short
  openings make this bearable.
- **Not understood**: a tone and the item's options the first time; "say it another way, or tap" the
  second; then silence with the buttons showing. The transcript is always on screen.
- **No answer**: one shorter reprompt after about 8 s, then the session pauses itself, closes the
  microphone and lets the phone sleep after a minute. A resume restates the item.
- **While a run works**: an immediate tick, a quiet thinking sound, and for a chat turn one spoken line
  from its tool status ("Looking at your mail").
- **The system never starts talking by itself.** A session runs only after the owner started it.

### 5.4 A recommended answer

This is what turns most items into one word. Sources, cheapest first (R6 §A5):

1. **Python rules over what is already known**: new → keep; a link suggestion → link; all steps done →
   close; a slipped soft date → move it; a card whose moment has passed → dismiss.
2. **The planner's proposals and the sweeps' evidence**, already paid for.
3. **An optional prep run when a session starts**, for cards and for spoken short names. Its cost is not
   measured; a planner run over 60 rows costs about $0.07.

Guards: always say the basis; a model's judgment is "my guess"; a recommendation never applies anything;
none for a risky close or a merge; record whether each was followed and stop a source that falls under a
threshold.

The base rates behind this are 13 days of one person's reviews: 41 first-time items, 16 cards, 7 link
suggestions. They are enough to choose defaults, not to tune thresholds.

---

## 6. Beyond voice: changes to Review itself

Ranked by value to this owner for the effort. R6 part B has the detail.

1. **A session and a tray on the server.** Where the owner was, and what is staged, survive a dead page
   and follow from the phone to the desktop. Voice depends on it.
2. **The decision rule in Python**, used by the buttons, the conversation and speech alike. It also
   closes the gap where a decision made through the card's conversation leaves the card's evidence
   unjudged.
3. **A recommended answer on each item**, shown as the first button and spoken as a question.
4. **Alike items decided together.** Group "new" items that share a recording, a course or a thread:
   "keep them all except…". Today's ten new items become about three decisions. Only for reasons where
   Keep dominates; never for last call or a recommended close.
5. **Order.** Hard-dated items for today and tomorrow first; cards placed by urgency instead of after
   every task.
6. **Set aside what is already answered**, undoably and said in a digest: a card whose moment has passed,
   a card unjudged for two weeks, evidence left on a closed task. Never a task.
7. **Draw the evidence behind "Thread updated" and "Mentioned in Slack"** on the card.
8. **Small repairs**: the Pin and Long-term keying; `back` and `reminder_missed` wired or removed.
9. **A notification that opens a Start button**, and a spoken or written digest of what was decided.
10. **Fewer taps on the phone**: Later remembering the last choice; a swipe for the recommendation.
11. **Standing rules the owner states once**, limited to dismissing cards and recommending, each with an
    expiry and an undo. Last, after the earlier items show which dismissals repeat.

---

## 7. Three ways to build it

All three need the Review grammar, the spoken card and the decision rule somewhere. They differ in where
the session lives and who does the listening and speaking. The speech engine is a separate choice in A
and B (the seam of section 3.5).

### A. The session lives in the page

**What runs where.** A state machine in `app.js` drives the existing card. Review's page code is first
pulled into a controller (`reviewCurrent()`, `reviewOptions()`, named actions in place of the `wire()`
closures; R2 §4.2), so a click, a key and a spoken phrase run the same functions. The tray stays in
`localStorage`. The server gains two stateless routes: one takes a clip (or text) plus the current item's
option ids and returns the transcript and the matching option; one returns spoken audio. Sentences go to
the card's existing conversation.

**What the owner hears and waits for.** The card read from sentences the page builds; 1 to 2 s after a
shorthand answer; about 11 s for anything that is not shorthand, because the only model path is the
chat turn.

**What a new person needs.** Nothing for the browser provider (hands-free on desktop Chrome and Firefox
only); an optional package set or a key for server speech.

**What changes in existing code.** `app.js` only, plus the two routes. No schema change.

**Size.** About three stages: the controller; the grammar route and the session bar; audio.

**Trade-offs.** Smallest, and review-by-page is untouched. But the decision rule and the spoken sentences
stay in JavaScript, where the project has no tests beyond source strings and scenarios kept outside the
repository. The session dies with the page: an iPhone that ends the app, or the page's own reload for a
new build (`onReturn`, `app.js:432-454`), ends it. The phone that listens and the desktop that shows never
agree. Grouping, recommendations and a new order would be built in the page as well, or not at all.

### B. The session lives on the server

**What runs where.** SQLite holds the session: an ordered snapshot of item keys, a cursor, what was
passed over, which device holds the microphone, and the staged changes. The tray moves to the server,
keyed as today, and the page reads and writes it, so Review in the page and a spoken session are two
views of one thing. Python owns the decision rule (`review_queue`), the spoken card (a new module), the
recommendation, the grouping and the order. One route takes an utterance (a clip as base64 JSON, or text)
and returns what was heard, what was staged, what to say and the next item. The page is a terminal: a
microphone with a voice-activity detector, a speaker, a session bar outside `#panel`, and the same card.
Tier 2 is one `claude` process kept open for the session. Speech goes through the two providers.

**What the owner hears and waits for.** A tap on "Review aloud", then "Twenty-two to look at, about six
minutes." Items of 6 to 10 s. After a shorthand answer, the echo in 1 to 2 s. After a sentence, a tick
and the answer in 2 to 3.5 s. After a question that needs mail, "Looking at your mail" and about 11 s.
The same session continues on the desktop with the decisions made so far in its tray.

**What a new person needs.** Nothing for the default (browser speech where the browser has it; elsewhere
the session speaks and the owner taps). Server speech is an optional extra: packages and about 2 GB of
models for local, or a key for a paid service, each off until switched on. Where `claude` cannot run,
tiers 2 and 3 say so once and tier 1 keeps working.

**What changes in existing code.** The tray's write sites in `app.js` (`stage`, `stageExtra`,
`persistStaged`, `discardStaged`, `applyAll`, three direct `localStorage` writes); `reviewAct` and the
closures become calls to the Python rule; `GET /api/review` is split into a read and an explicit "offered"
stamp per item; a migration for the new tables; a long-lived mode in `runner.py`.

**Size.** About six stages: (1) the decision rule in Python and the tray on the server; (2) the session,
the spoken card, recommendations and groups, usable in the page with no audio; (3) the grammar and dates,
usable as a typed box; (4) capture, transcription and speech behind the providers; (5) the sentence run
and the hand-off to a chat turn; (6) hardening on the phone.

**Trade-offs.** The largest, and it changes the tray, which every tab uses. In return: everything that
decides is Python under pytest; a dead page loses nothing; two devices agree; stages 1 to 3 improve
Review for the page alone; and any other client can drive the same session, including way C.

### C. Claude's own voice mode drives Review through the connector

**What runs where.** Factotum has no audio code at all. It gains two or three MCP tools on the server it
already exposes to claude.ai (`review_next`, `review_decide`, `review_status`), backed by the same Python
decision rule and server session as B's first two stages, and a skill that tells Claude how to run a
spoken review. The owner opens the Claude app on the phone, starts voice mode and says "let's do my
review". Decisions land in Factotum's server tray; an open Factotum page follows the session's cursor.

**What the owner hears and waits for.** Claude's own voice, turn-taking and interruption, in a native app
that does not have the web page's microphone and audio problems. Every turn, including "keep", is a model
turn plus a tool call through Anthropic's cloud and the tunnel. The wait was not measured.

**What a new person needs.** A Claude plan with the app, and Factotum's connector set up (SETUP §16),
which needs the public tunnel. No speech packages, no GPU, no key.

**What changes in existing code.** B's stages 1 and 2, the tools in `mcp_server.py`, the skill.

**Size.** About two stages on top of B's first two.

**Trade-offs.** By far the least speech work and probably the best voice. But it rests on one unverified
capability: Anthropic's help page says connected tools "work the same way in voice mode as they do in
text chat" and names only its own connectors [D,
<https://support.claude.com/en/articles/11101966-use-voice-mode>], and a July 2026 report describes custom
connector tools failing in voice mode (found in search results; the page could not be opened). The session
starts in another app, not in Factotum as the owner described. Factotum cannot fix the wording: the rules
on what is never spoken hold only as far as a prompt holds them. Shorthand is not deterministic.
Voice use counts against the plan's limits [D]. Whether it runs with the phone locked is not documented.

### Recommendation

**Build B, in the order given, with two tests before stage 4.**

- The owner asked for a proper fit, not an attachment. Section 1.4 shows that the fit problems are all in
  where Review's knowledge lives, and only B moves it.
- A's weak points are exactly the dinner case: a phone that ends the page, a reload for a new build, a
  second screen.
- B's first three stages are worth having with no voice at all: one tray on every device, a recommended
  answer, groups, a better order, typed shorthand on a card.
- B makes C nearly free. Once the decision rule and the session exist, C is a few tools and a skill, and
  the owner can keep whichever he prefers, or both.

The two tests, each a quarter of an hour, both on the owner's phone:

1. The throwaway page of section 2.3. If playback with the microphone open is unusable or the detector
   does not run, stage 4 changes shape (release the microphone while speaking; tap to talk).
2. Voice mode in the Claude app with the Factotum connector that already exists: "what is due this
   week?". If it calls the tool, C is real and cheap to try after stage 2.

For this owner's speech: local Whisper (faster-whisper turbo) with hints from the card, and local Kokoro,
with a paid pair one config line away if the GPU path proves fragile under systemd or the voice
disappoints. The owner prefers quality to thrift, so let him hear three voices read real cards before
choosing.

---

## 8. What must change, apart from what is new

### 8.1 Refactors: existing things that move or change

| # | Change | Why voice cannot do without it |
|---|---|---|
| 1 | **The decision rule becomes a Python function** (`review_queue.decision_changes(item, decision, note, today)`, with accept, answer-a-link and dismiss beside it). The page calls it or is tested against it | A decision staged any other way leaves the card in the queue |
| 2 | **The card's conversation stages through the same rule** | Speech makes the conversation the normal path; today it skips the verdicts and the review stamp |
| 3 | **The tray moves to the server**, keyed by `stageKey`; `applyAll` applies from it | The speaking phone and the showing desktop must agree; a dead page must lose nothing |
| 4 | **`GET /api/review` is split**: building the queue reads only; the "offered" stamp is written per item when it is shown or spoken | A session must not stamp 15 rows it never read out, and must not go unstamped either, or its items never fade |
| 5 | **The page's Review code gets a controller** (`reviewCurrent`, `reviewOptions`, named actions) | Clicks, keys and speech must run one set of actions; session state must live outside `#panel`, which `render()` rebuilds |
| 6 | **Create on a suggestion card can be staged** | Otherwise speech writes to the database on one word (an owner decision, section 10) |
| 7 | **Pin and Long-term on a card are staged beside the decision** | The session would inherit the fault |
| 8 | **`dates.resolve_when` learns spoken dates**; the day-in-words helper moves from `brief.py` to `dates.py` | Shared with the add line and the brief |
| 9 | **The queue's order**: cards placed by urgency | A spoken session cannot leave tomorrow's card for last |
| 10 | **`runner.py` gains a process kept open for a session** | The measured halving of tier 2's wait |
| 11 | **`onReturn` and `typingNow` know about a running session** | A new build must not reload the page mid-session |

### 8.2 New

- The session: tables, routes in `api.py` (plain `def`, each write under `commit_lock`), a read-only
  "current item" route.
- The spoken card and the flattener (`factotum/spoken.py`).
- The Review grammar and its transcript cleaner.
- The recommendation rules and the grouping.
- The two providers and their implementations; hints.
- A run kind for a spoken sentence: a prompt, and a line in each of `runner.BUDGETS_USD`, `TIMEOUTS_S`,
  `MODELS`, the example, the fixture and the starter.
- In the page: the session bar (outside `#panel`), capture and the detector (loaded only when a session
  starts), playback, the sounds, the wake lock, pause on hide.
- Settings: provider, voice, language, silence time, sounds; each with its `SETTINGS_KEYS`, `DEFAULTS`,
  `CHOICES` or `MINIMA`, control and example line.
- Tests: grammar tables in pytest (including mishearings), the decision rule against today's batches, the
  session's routes with a fake recogniser and a fake runner.

---

## 9. Risks, and conventions a spoken session could break without anyone noticing

### 9.1 Risks

| Risk | Guard |
|---|---|
| A wrong close by mishearing ("stop" as "drop", a number in two pieces, a television) | the vocabulary rules; a risky close asks first; the echo names the task; nothing is written before Apply; Undo after |
| The session works at the desk and fails at the counter (echo, noise, volume, a locked phone) | the two tests before stage 4; half-duplex; tap to talk always there |
| Whisper invents words from noise | the detector gate, the no-speech score, ignoring filler-only transcripts |
| The GPU is busy with other work, or the service cannot see CUDA | `ready()` says why; fall back to the CPU model, a paid provider or the browser; a separate worker process is an open design choice (R5 §12) |
| A recommendation trains a habit of saying yes | the basis is always said; no recommendation for a risky close; agreement recorded per source |
| Things read aloud with others in the room | names, dates and senders by default; mail content only on "more"; a "names only" setting |
| Audio leaves the machine | local by default for this owner; any outside provider is a switch that is off when absent, and Settings says where audio goes |
| Cost creep | grammar first; the sentence run costs about a cent; each run in the Log |
| A new person has no speech at all | the session still speaks through the browser and takes taps; Settings says what is missing |

### 9.2 Conventions in CLAUDE.md at risk

R1 §6 has the full table. The ones most likely to be broken silently:

- **`commit_lock` on every write on the API's connection, never held across a Claude run, a
  transcription or a network call.** Transcribing or synthesising inside the lock would stall every Apply.
  New routes belong in `api.py`: the test that enforces the lock reads that file's source only
  (`tests/test_review_fixes.py:160-162`) [I].
- **No `async def` route writes.** A streaming audio handler would be the first temptation.
- **`/api` never answers 401 or 403.** "Another device holds the microphone" is a 409.
- **JSON on every state change.** No `sendBeacon` on page hide, no multipart upload.
- **`GET /api/review` is never called for a number or polled**; the count is `review_queue.count`.
- **`expire` acts on `last_offered_at`.** Stamp an item when it is spoken.
- **Opening a task stamps `neighbours_seen_at`.** Answer questions from `?peek=1`.
- **Apply on Review is followed by the finish write**, or the evening push nags after a full spoken
  review.
- **One turn per thread**; the page's `chat.busy` is one flag for the whole page.
- **What a place adds to a prompt is the server's words.** The transcript is the turn's text; the card is
  rebuilt by the server from the session, never pasted from the page. Hints and prompts go through
  `prompts.fill` and `one_line`.
- **"Blocked" and "dependency" appear in nothing a person reads.** They must stay out of the spoken
  templates and be checked for in model output before it is spoken.
- **What is typed lives in `draft`.** A session must never focus a text box: on a phone that sets
  `kb-open` and hides every bottom bar.
- **One thing at the bottom on a phone.** The session bar replaces the pinned decisions; it is not a
  second bar.
- **The build stamp covers `api.STAMPED`.** A new script joins it; the 11 MB detector is better served
  from a versioned path and loaded on demand.
- **The service worker never gets a fetch handler**, so no caching of models through it.
- **Restarting mid-run loses the run.** A process kept open for a session is a run in flight for as long
  as the session lasts; the restart check must see it.
- **Absent means off** for any paid or outward-sending switch; **person-specific values in config** (the
  voice, the language, any key name in `.env.example`).
- **"voice" already means a Plaud recording** (`closed_by: voice`). A spoken session needs another name in
  the Log, or stays source `review` with a mark in the batch's cause.
- **The API uses the machine's day and the scheduler the owner's zone.** Build the spoken card with the
  same `today` as the queue.

---

## 10. Decisions only the owner can make

In order of how much each changes what gets built.

1. **One tray on every device?** Moving the tray to the server changes how staging works on every tab,
   for the gain of a phone and a desktop that agree and a session that survives the page. Yes is way B;
   no is way A.
2. **When do spoken decisions reach the database?** Staged until a confirmed spoken "apply" at the end
   (closest to "exactly as today"); applied in small groups as the owner does now by hand; or never by
   voice, with Apply left to the screen. This sets what the end of a session is and whether Apply is in
   the vocabulary.
3. **Should Create on a suggestion card stage instead of writing at once**, in a spoken session or
   everywhere? If it stays immediate it is the one spoken word that writes, and it needs a read-back and
   a yes every time.
4. **Where does this owner's speech run?** Local models on the GPU (free, private, an install to keep
   working under systemd, a GPU shared with other work) or a paid pair (about $9 a month, audio and task
   names leave the machine). This sets the install work and the privacy text.
5. **May Review recommend an answer, group alike items and change the order, in the page too?** These
   change what Review is for someone who never speaks to it. Without them a spoken session is 22
   questions.
6. **Is "next" a new thing?** Today a card leaves only by a decision. "Next" as "leave it undecided and
   ask again another time" needs a cursor and a rule for when the item returns.
7. **May a model's proposals be staged on a spoken "yes"**, with no tap? And is a prep run at the start
   of a session on by default (a paid run, about the planner's cost, started by the owner's own tap)?
8. **Is the Claude app's voice mode worth trying as a second front door** once the server session exists
   (way C)?

Also needed from the owner, as facts: the iPhone's iOS version, and whether he would wear earbuds while
cooking. Earbuds decide whether interrupting by voice is in the first version.

---

## 11. What was not found out

- Everything in section 2.3: nothing was run on a phone.
- Whether a custom connector's tools work in the Claude app's voice mode today.
- How fast Kokoro, Piper and faster-whisper turbo are on this machine; only transformers Whisper was
  timed. Whether the systemd service can see the GPU and the cuDNN wheel.
- How Whisper behaves with a real extractor fan and a running tap.
- A session-long `claude` process: 20 minutes, a long idle gap, the budget flag over many messages, its
  cost as context grows. Why sonnet answered faster than haiku. What caused the one 15.7 s first call.
- The time from the phone through the tunnel to the server and back, and whether a clip sent after a long
  idle meets an expired Access sign-in.
- How long the owner takes per decision: batches record when they were applied, not when each card was
  decided. Which reason each past decision answered: reasons are computed, not stored.
- The cost and speed of a prep run.
- Whether the Pin keying and the Later-menu fault show in a browser; they were read in code.
- Whether the owner reviews on the phone or the desktop today.
- The iPhone's voices: one report says downloaded voices are available to a page [R], another that they
  are not [D].

---

## 12. The phone test, 2026-11-19 (added after the findings)

The owner ran `factotum/web/voice-test.html` on the iPhone 12 Pro, iOS 27.2.1, in **Edge for iOS as a
browser tab** (WebKit underneath; not Safari itself and not an installed app), then with one AirPods Pro
earbud. The owner also tested the Claude app's voice mode with the existing connector: a read tool and
`stage_changes` were both called with no tap. That is why the build goes through the Claude app first
(the design is `docs/superpowers/specs/2026-11-19-spoken-review-design.md`); what follows is for the later
decision about a microphone inside Factotum.

**Settled**

- **The round trip is not a cost**: five requests through the tunnel, median 64 ms (39 to 165).
- **The detector runs on the phone**: Silero through `@ricky0123/vad-web` 0.0.31 was ready 0.6 s after
  the tap and kept clips of 2.7 to 3.3 s (113 to 136 KB as they would be sent).
- **Capture works**: 48 kHz, echo cancellation on, the 4 s recording judged clear. `MediaRecorder` gives
  `audio/mp4` (AAC) there, 90 KB for 4 s, where Chromium gives webm/opus: send PCM, as section 2.2 says.
- **A short lock or app switch does not end the microphone**: after 14 s locked and after 8 s in another
  app the track was still live and unmuted, the audio engine still running, and the detector kept a clip
  straight away. Only the screen lock had to be taken again (one tap). Longer absences were not tried.
- **The screen stayed on** for the 3.5 minutes it was asked to, until the owner locked the phone.
- **An audio element is the output path.** It played every time, including with the ringer switch on
  silent. Web Audio also played on silent.
- **Never mix the phone's own voice with Web Audio.** Each time `speechSynthesis` spoke, the
  `AudioContext` went to `interrupted`, then `suspended`; tones started through Web Audio meanwhile made no
  sound and all played at once when the engine came back (seven presses finished in the same second). The
  utterances themselves ended as `canceled` twice. A page that captures through an `AudioContext` cannot
  also speak through `speechSynthesis` on this phone.
- **Earbud presses do not reach the page while the microphone is open.** With it closed, one, two and
  three presses arrived as pause / play, next and previous. With it open nothing arrived and iOS showed
  "Cannot Control Mic with AirPods Pro": the system treats a page that holds the microphone like a call,
  where a press means mute, and a page cannot answer that. No AirPods setting turns it into a media key.
  So a press can interrupt speech only in a design that lets go of the microphone while it speaks.

**Seen, not settled**

- **One-word commands may be lost.** The owner said "keep", "later Friday", "done" in a row; the clip kept
  was 3.2 s and "done" was not in it. Either the first two were joined and the last was thrown away as too
  short, or it was still open. The library's minimum speech length and its closing pause need tuning for
  a vocabulary that is mostly one word, and the test page did not write its counters to the results.
- The microphone opened in 0.4 s the second time in one launch and 1.3 s when it moved to the AirPods (a
  route change, probably not a question).

**Still not tested**

- How loud the reply is **with the microphone open** on the phone's speaker (question 1 of section 2.3).
  Every rated sound was played with the microphone closed. The kept clip was played back through an audio
  element with the microphone open and was heard clearly, which is encouraging and not a measurement.
- Whether the detector fires on Factotum's own sound, and on a tap or a fan.
- Whether the permission is kept across launches (the first launch never opened the microphone), and
  everything about the installed app.
- How the AirPods sound once the microphone is open (the hands-free profile).

---

## Appendix: where the reports were corrected or reconciled

| Report said | Finding |
|---|---|
| R2, R5, R6: recognition is unavailable in iOS Home Screen apps (WebKit 225298) | Out of date. Bug 321436 shows it running in installed apps on iOS 26.6 and 27, and broken there [I]. R4 had this right. The design conclusion does not change |
| R5: no Chrome version found that shipped `phrases` | Chrome 142 on desktop; not on Android; Firefox 160 accepts and ignores it [I, MDN browser-compat-data] |
| R5: Edge does not support recognition (caniuse) | The compat data has Edge following Chrome; Microsoft documents cloud recognition with a local model behind a flag [V, R4]. Treat Edge as untested |
| R3: every run carries about 38,000 tokens of default prompt, and `--system-prompt` should cut the wait | True only with the tools left in. With `--tools ""` the prefix is about 3,300 tokens and replacing it changed nothing [I] |
| R3: expect 3 to 6 s on sonnet for the small run | Measured 1.8 to 3.0 s as a new process, 1.0 to 1.4 s with a process kept open [V, I] |
| R3, R5: a process kept open is documented, untested | Tested: it works with the runner's flags [I] |
| R5: the first call after idle takes 15.7 s | Seen once; not reproduced [I] |
| R3: Accept on some evidence writes at once | It stages (`app.js:3975-3989`) [I]. What writes at once is listed in section 1.3 |
| R3: voice decisions must go through `reviewAct`; R1: through a new Python function | The Python function, with `reviewAct` calling it. Speech handled on the server cannot call a page function |
| R3: a small fixed vocabulary in the page as well as the grammar on the server | Only Stop and taps in the page. With transcription on the server there is no step where the page holds text the server has not seen |
| R1: after Pin on a card, a later Keep replaces the pin in the tray | The card has already left the page, so no Keep follows. The effect is a card that leaves undecided and returns after Apply [I] |
| CLAUDE.md: `render()` runs every second while a chat turn runs | There is no timer; it runs on each stream event [V, R2 §3] |
