# How Review is drawn and driven in the page, and where a spoken session attaches

Research for a spoken Review session. Read-only. Code as of commit `75d2c77` (main, 2026-11-19).
All paths are under `/home/robert/factotum/`. "app.js" means `factotum/web/app.js` (4,351 lines).

Evidence labels:
- **[code]**: read in the source at the line given (verified today).
- **[db]**: measured today from `data/factotum.db`, opened read-only (`mode=ro`).
- **[web, date]**: documented on the web, checked today, not tested on a device.
- **[memory]**: recalled, not checked today.

---

## 0. Summary for the designer

1. **There is no "current Review item" object.** The current card is worked out inside
   `renderReview` (app.js 1933-1945): the first `review.items` entry whose bare numeric id is not a
   key in `staged`. When every task card is staged, the first suggestion card not dismissed or created
   (1936-1937) is shown instead. `ui.reviewIdx` exists (45, set to 0 at 1848) but **nothing reads it**.
2. **What can be decided is spread over three places:** HTML builders (`renderReview` 1952-1972,
   `suggestionCard` 1976-2001, `linkSuggestionsHtml` 1885-1898, `mailAboutHtml` 1868-1874,
   `rippleLineHtml` 2181-2191, `sinceHtml` 2156-2176), **anonymous click handlers written inside
   `wire()`** (3973-4039), and the keyboard map (4333). Only `reviewAct` (4198-4229) and
   `linkSuggestionAct` (1901-1914) can be called as functions. Accepting a suggestion, Dismiss, Follow
   its thread, Not about this, and a suggestion card's Create and Dismiss exist only as closures in
   `wire()`. A spoken layer would have to click DOM nodes unless these are pulled out (§4).
3. **A decision only stages.** Nothing is written until `applyAll` (614-668), which applies the whole
   tray. On the Review tab it then posts `/api/review/finish`, reloads the queue and shows "Batch
   applied." (`ui.reviewPaused`).
4. **A decision made through the card's conversation is not the same as a decision made with a
   button.** `reviewAct` also judges the card's evidence and sets `last_reviewed_at` on Later. A
   `done`/`drop` proposal staged from a reply (`stageProposalAs` 2256) does neither (§2.5). A spoken
   front end that sends everything to Claude would skip that rule without anyone noticing.
5. **A Claude turn takes about 12 s (median), up to about 19 s, and costs about $0.065** [db: 27
   `chat-turn` runs]. The page shows a spinner line and gets no streamed text it could speak (§2.4).
   Shorthand ("done", "keep", "later Friday") has to be handled in the page or by Python, at once and
   for free. Claude is the fallback.
6. **Several things assume someone is looking at or typing on the page:** `render()` rebuilds `#panel`
   on every staged change and every server-sent event. `onReturn` reloads, or even `location.reload()`s
   when a new build is waiting. Toasts are visual and last 8 s. Later → Pick a date is an
   `<input type=date>`. The outcome question is a focused text box in the toast. Each has a fix in §3.
7. **Platform:** the page has no Content-Security-Policy and no Permissions-Policy that would block the
   microphone [code: `api.py` 1158 is the OAuth consent page only]. The service worker has no fetch
   handler and needs none. Speech recognition in an **iOS Home Screen app** is documented as
   unavailable (WebKit bug 225298, closed RESOLVED LATER) [web, 2026-11-19]. Screen Wake Lock works in
   Home Screen apps since iOS 18.4 (March 2025) [web, 2026-11-19]. Whichever recogniser is chosen
   (see the other reports), the page part should be the same.

---

## 1. How one Review item moves through the page

### 1.1 State that holds Review

| State | Where | What |
|---|---|---|
| `review` | app.js 9; set by `loadReview` 1846 | The `/api/review` payload: `{today, last_review_at, cap, total, labels, items[], suggestions[]}` (api.py 592-593). `items[i]` = `{task, reasons, primary, flags, texts, link_suggestions, evidence[], since?}` (api.py 549-575). |
| `staged` | 15; persisted to `localStorage['factotum.staged']` | `Map(key → {task_id, op, fields, label, expect?, note?, ev?, fsug?, from_chat?, prop?})`. A **bare numeric key** is the task's *decision*. Other keys sit beside it (`<id>:due`, `<id>:r`, `<id>:note`, `<id>:edit-<f>`, `<id>:c<mid>-<i>`, `<id>:a<eid>`, `rel:a:kind:b`, `e:<eid>`, `new:…`) (`stageKey` 861-870). |
| `ui.reviewPaused` | 45 | True after an Apply on the Review tab (653). Shows the "Batch applied." screen and hides suggestion cards while more pages remain (1937-1943). |
| `ui.laterOpen`, `ui.datePick` | 45 | The Later menu and its date picker are open. |
| `ui.rvNote` | 45; `{id, text}` | The card's note box (`#rv-note`), written on input (4038). Read by `reviewAct` (4208). |
| `ui.rvEdit` | 57; `localStorage['factotum.rvedit']` | The card's editor is open (kept across cards and sessions). |
| `ui.ctxOpen`, `ui.sinceOpen` | ad hoc | Context expanded; the ripple diff is open. |
| `draft.ask` / `draft.askFor` | 22 | The card conversation's box. `reviewPlaceHtml` (2649-2650) empties it when the card's id changes. |
| `draft.sg[<eid>]`, `draft.askSg` | 28 | A suggestion card's draft and its question. |
| `places` | 2392 | Conversation places, keyed `review:<taskId>`, `suggestion:<eid>`, … |
| `reviewLoading`, `reviewSeq`, `reviewFailed` | 1834 | One GET in flight; a newer GET wins. |
| `reviewCount` | 361 | The number beside the tab and on the app's icon (`/api/review/count`, read-only). |
| `ui.reviewIdx` | 45, 1848 | **Not read anywhere.** |

### 1.2 Which card is current (renderReview, app.js 1927-1973)

```
items = review.items.filter(i => !staged.has(i.task.id))          // 1933
sugg  = review.suggestions.filter(x => !staged.has('e:'+x.id) && !x.created)   // 1936
if (!items.length && sugg.length && !(paused && remaining>0)) → suggestionCard(sugg[0])   // 1937
if (paused || !items.length) → the finish screen ("Batch applied." / "N decisions staged." / "Nothing to review.") // 1938-1944
else current = items[0]; x = view(items[0].task)                   // 1945-1946
```

- Order is the server's (`lifecycle.REVIEW_ORDER`, `lifecycle.py` 14). The page never reorders it.
- **No "skip" and no "back".** A card leaves only when something is staged under its bare id. Keep
  ("no change; not asked again until something changes", note at 1972) is the nearest thing to
  "next". Going back means discarding that decision from the tray, after which the card is first again.
- The page holds one page of the queue: `review_cap`, 15 by default (`config/factotum.example.yaml`
  90; api.py 583). "Review another N of the remaining M" (1939) calls `loadReview` again. The ids in
  the tray are excluded (1838), so a person can work through several pages before Apply.
- `GET /api/review` **writes**: it runs the day's link-suggestion session and stamps `last_offered_at`
  (api.py 547-548, 585-589), under `commit_lock`. Never poll it. Use `/api/review/count` (read-only,
  api.py 536-540) for a number.
- Progress line (1953): "i of total" and "~N min left" at **12 s per item**. A spoken session will be
  slower.

### 1.3 What a card shows (task card, 1952-1972)

From top to bottom: the reason line (`reasonText` 1857-1865: the server's sentence `item.texts[primary]`
or `review.labels[primary]`, plus "also …" and the planner and ripple flags) · name · chips
(`reviewSubHtml` 1916-1919: code, project, due, effort, steps, mentions, score) · "Since you last
looked" (`sinceHtml`) · why now (or the score reasons) · context (clamped on a phone, More/Less) ·
"✎ Edit this task" (`rvEditHtml` 1923-1926 → `editorHtml` 3581) · the note box `#rv-note` · the
source link · **the four decisions** `.big` (Done d / Keep k / Later s / Drop x) · the Later menu
(Tomorrow / Friday / Next week / Pick a date…) · the date picker · evidence lines (ripple Accept/Dismiss,
mail Follow its thread / Not about this, planner Accept) · suggested links · extras (★ Pin to Top,
Long-term, `dueForm`, `reminderForm`) · the conversation (`reviewPlaceHtml`).

A suggestion card (`suggestionCard` 1976-2001) shows: "Planner suggests" / "From your mail" /
"Mentioned in Slack" / "Ripple suggests" · the draft's name, chips, links and context · the mail
sender and subject · ✎ Edit before creating (`sgFormHtml`) · **Create task / Dismiss** · its own
conversation (`sgPlaceHtml`, origin `suggestion`).

### 1.4 How a decision is staged (reviewAct, app.js 4198-4229)

- `later` and `pickdate` only toggle the menu (4201-4202). `top` and `longterm` are extras staged
  beside the decision through `act` (4203, 595-612).
- With a note: Done or Drop with a note of at most 300 characters (`OUTCOME_MAX` 703) stages
  `{op: done|drop, fields: {outcome}}` (4214). A longer note, or a note on Keep or Later, becomes
  `add_note` under `<id>:note` (4223), which the server appends dated at Apply.
- Later stages `set {snoozed_until, last_reviewed_at: today}` (4209). The dates come from page helpers
  (`isoPlus`, `nextFri`; "next week" is next Monday, 4220). Later **never touches `due`** (SPEC §5,
  "Review queue").
- **Every pending evidence row on the card is judged `reviewed` in the same tray** (4227), so the card
  does not come back tomorrow for the same reason.
- Each `stage`/`stageExtra` call runs `render()` at once (593, 537). The card leaves on the first of
  them.
- Possible glitch [code, not run in a browser]: `ui.laterOpen = false` is set at 4228, *after* the
  renders. A Later decision on a card with no evidence renders the next card with its Later menu still
  open, until the next `render()` (for example when that card's conversation finishes loading).

Other ways a card's things get staged, all in `wire()`:
- Accept on a planner or ripple proposal (3975-3989). A `done`/`drop` proposal **is** the decision
  and judges the card's other evidence `reviewed`. Anything else stages beside the decision and judges
  the proposal `applied`.
- Dismiss (3990) → `judge_evidence dismissed`.
- Follow its thread / Not about this (3993-4000), through `mailCard`, a helper defined *inside*
  `wire()`.
- Suggested link buttons → `linkSuggestionAct` (1901-1914, a named function).
- A suggestion card's Dismiss (4016) → `stageJudge(eid, null, 'dismissed')`. Its **Create** (4017-4033)
  is **not staged**: it posts `/api/review/suggestions/{id}/create` at once (one undoable `review`
  batch), then reloads the tasks and the queue.

### 1.5 When the card leaves

- A task card leaves as soon as its bare id is in `staged` (1933): any of the four decisions, an
  accepted `done`/`drop` proposal, or a `done`/`drop` staged from the conversation.
- Staging a due date, a note, a link, a reminder or an edit does **not** move it on.
- A suggestion card leaves when `e:<id>` is staged (Dismiss) or after Create (`sg.created`, 4025).
- Discarding a decision in the tray (`discardStaged` 585-589) brings the card back in its queue place.

### 1.6 What Apply does (applyAll, 614-668)

1. It takes every staged entry, or the tray's ticked ones, with creates first. It posts
   `/api/batches/apply` with source `ui.tab === 'review' ? 'review' : 'ui'` (632). **The source comes
   from the tab, not the session.**
2. It remembers which conversation proposals were applied, then clears the tray (all of it, or what
   was applied).
3. It runs `load()` (the tasks, the skill-staged proposals, the summary, the count) and then the
   Review step:
   - **"Edits only"** (applied through a card's editor Apply: no numeric key and no `judge_evidence`,
     646): reload the queue, and the card stays.
   - **Otherwise on the Review tab:** `POST /api/review/finish` (sets `state.last_review_at`, api.py
     744-749), `loadReview()`, `ui.reviewPaused = true`. The person sees "Batch applied." and a
     "Review another" button. **The next page waits for a press** (comment at 649-650).
4. The toast reports applied and skipped, with Undo. If a closed task freed others, the toast offers
   "Say how it went" for 20 s (627-630, 662-664). If ripples were queued, `watchRipple` polls
   `/api/ripple` every 15 s for up to 10 minutes (791-813) and later toasts "N linked tasks brought up
   to date".

There is no separate "finish" button: "finish" is what Apply does on the Review tab.

### 1.7 Keyboard (app.js 4315-4347)

- On Review: `d` done, `k` keep, `s` **opens the Later menu** (it does not pick a date), `x` drop,
  `t` top, `l` long-term (4333). `n` opens New task (4332, before the Review map). Esc (4317-4325)
  closes the sheet or page or clears the selection.
- Missing: a key for a Later date, for Apply, for the next page, for Accept/Follow/Dismiss, and **any
  key on a suggestion card**. `reviewAct` returns when `items` is empty (4199), so d/k/s/x do nothing
  there.
- Keys are ignored while focus is in an input (4326), under the sheet (4327), on a task page (4330), or
  signed out (4316).

### 1.8 The phone layout

- `PHONE()` is `max-width: 700px` (67). `TOUCH()` is `hover: none` (31).
- **The decisions are pinned at the bottom.** `.review .card .big` is `position: fixed; bottom: 0`
  with four columns and 48 px buttons (app.css 762-765). The card is padded `88px + --kb` (766).
  `placeReviewBar` (app.js 3902-3906) lifts the bar above the tray when the tray shows.
- `body.kb-open` (set on focusin of a text box in `#panel`/`#sheet`, 2700-2712) hides the tray, the
  decisions, the page bar and the round button (app.css 768). `body.kb-note` keeps the four decisions
  as a short row just above the keyboard while `#rv-note` has focus (772-773). That way the note is
  typed and Done tapped without closing the keyboard.
- **The round button** (`.fab`, New task) shows only on the list tabs (`bottomRule` 1479-1481,
  `LIST_TABS` 3230), so **never on Review**. CLAUDE.md's "one thing at the bottom" rule on Review is:
  tray plus the pinned decisions above it.
- `revealLaterMenu` (3890-3901) scrolls the page so an open Later menu ends just above the pinned
  bar.
- Under 480 px the decision grid is 2×2 inside the card (app.css 458), but on a phone the pinned rule
  (764) sets four columns again.
- The toast sits at `bottom: 70px + safe area` (app.css 404), centred, as a pill that wraps on a phone
  (408).

---

## 2. The conversation on a Review card

### 2.1 Origin and thread

- `reviewPlaceHtml(id)` (2649-2655) makes `placeOf('review', id)`: key `review:<id>`, `task_id = id`
  (2394-2398). It draws `placeHtml` with `{decision: true, title: 'Ask Claude about this task', box:
  {id: 'rv-ask', key: 'ask'}}`.
- `loadPlace` (2403-2417) runs `GET /api/chat/place?origin=review&task_id=N` the first time a card is
  drawn. It redraws with `render()`, or `swapPlace` while something is typed.
- On the server, **a Review card and an opened task share one thread per task**: the newest thread
  with `origin IN ('review','task')` and that `task_id` (`chat.place_thread`, chat.py 87-108). The
  prompt is told the place (`PLACE_TEXT['review']`, chat.py 60): *"…propose `done` or `drop` for
  Task-{id} only when they ask for it."*
- A suggestion card has its own thread (origin `suggestion`, `chat_threads.card_id`). Its reply's
  `create` is the revised draft, offered as "Use on this card" and never staged (`cardDraft` 2539,
  `useOnCard` 2077-2090).

### 2.2 Sending a turn (sendTurn, 2433-2477)

- `askPlace` (2622-2627) takes the box's text, empties it and calls `sendTurn(pl, text, null, 'ask')`.
  The canned "Split into steps" from the card's editor sends `canned: 'split'` (3853-3857).
- Body: `{thread_id, origin:'review', task_id, fresh, text, attached:[task_id], canned}` to
  `POST /api/chat/turn` (api.py 933-1013), which streams server-sent events: `meta`, `status`, `text`,
  `done` or `error`.
- If the turn is refused (409, a non-OK status, or a network failure before `meta`), the text goes back
  into the box (`giveBack`, 2434) and a toast says why.

### 2.3 One turn at a time

- In the page: `chat.busy` is **global** (17, 2435-2436). Any second turn anywhere in the page is
  refused with "Claude is still answering · your text is kept". Ask buttons are disabled while it is
  set (2587).
- On the server: `app.state.chat_busy` holds thread ids, and a second turn on a busy thread answers
  409 (api.py 962-963). `turn_lock` serialises finding or making the thread (939).

### 2.4 How long a turn takes, and what the page shows meanwhile

- [db] `runs` kind `chat-turn`, n = 27: min 2.8 s, p25 7.0 s, **median 11.6 s**, p75 14.7 s, p90 17.9 s,
  max 18.9 s. Mean cost $0.065. `chat_messages` from origin `review`: 10 turns, mean 10.7 s, $0.065.
  The model is `sonnet`, the budget $0.60 and the timeout 240 s (example config 243-250).
- While it runs, `pl.live = {status, texts}`. Each `status` event (from chat.py 450-462: "Thinking…",
  then a friendly tool name such as "Reading a task · 412 · task_get") and each `text` event calls a
  **full `render()`** (2453-2454). The place shows a spinner, the status and the last text cut to 300
  characters (`placeHtml` 2581).
- **Nothing can be spoken while it streams.** `text` events are whole assistant text blocks, which may
  be the model's JSON envelope. The words meant for the person (`reply_md`, Markdown) arrive only in
  `done` (2466-2468). Text-to-speech has to wait for `done` and strip the Markdown (`md()` 2205-2220
  renders it to HTML; there is no plain-text form).
- At the end the toast says "Answered · $0.07", and `chat.threads = null` so the Chat tab reloads.

### 2.5 How a reply's proposals become staged changes

- `placeMsgHtml` (2541-2574) lists the newest reply's proposals with tick boxes (all ticked by default;
  `pl.off` holds the unticked ones) and one button, "Stage N with this decision" (2566-2567). A create
  shows Create / Edit first. Drafts show "Open in Mailbird". Refusals sit in a fold.
- `stagePlace` (2601-2613) → `stageProposal` (2228-2232) → `stageProposalAs` (2236-2264) puts each into
  the tray under `placeKey` (2530-2535):
  - `done`/`drop`/`reopen`/`pin_top`/`longterm` → `stage(task_id, op, …)`. For the card's own task,
    **`done`/`drop` is the decision** (bare id), so the card leaves.
  - `set` → `<id>:c<mid>-<i>` with `expect` for text fields. **The card stays.** A spoken "put it off
    till Friday" that Claude answers with `set {snoozed_until}` leaves the card waiting for a decision.
  - Link, unlink, merge and note proposals become `rel:`/note keys; `add_reminder` → `<id>:r`;
    `link_thread` → `follow_thread`; `create` → `new:c<mid>-<i>` with `from_chat`.
- **Divergence to fix before voice depends on it:** a `done`/`drop` staged from a reply does **not**
  judge the card's evidence (compare 4227 and 3980). A `set {snoozed_until}` from a reply carries **no
  `last_reviewed_at`** (compare 4209). So "Claude, mark it done" and pressing Done stage different
  batches. Today this is a small gap, because few people decide by chat on Review. A spoken session
  that routes free speech through Claude would make it the normal path.
- Nothing applies until Apply. Applied proposals are remembered in `appliedProps` (908-914,
  `localStorage['factotum.applied']`) so they cannot be staged twice.

---

## 3. Every place the page assumes a person is looking or typing

| # | What | Where | Why it matters hands-free | What a spoken session needs |
|---|---|---|---|---|
| 1 | `render()` rebuilds `#panel` with `innerHTML` | 1449-1476. Called by every `stage`/`stageExtra` (537, 593), every SSE `status`/`text` event (2453-2454), `loadPlace` (2415), `loadDetail` (1279), `load()` (401), and most clicks. **No per-second timer exists today** (no `setInterval` in app.js). CLAUDE.md's "every second while a chat turn runs" describes the event rate, not a ticker. | Anything a session draws inside `#panel` is destroyed several times per decision and per turn. A speech or listening indicator kept there would flicker, and its listeners would be lost. | Keep session state in a JS object (`voice = {on, phase, currentKey, transcript, …}`), never in the DOM. Draw the session's fixed parts **outside `#panel`**, as `#toast`, `#tray`, `#sheet` are (index.html 42-52), with a `renderVoice()` called from `render()` as `renderTray()` is (1471). Speak when **the current item's key changes**, not on render. |
| 2 | Focus and caret restore | `heldDraftBox` / `restoreDraftBox` (2382-2391), applied in `render()` (1455, 1465). Boxes listed in `DRAFT_BOXES` (415) and `isDraftBox` (2380). | Harmless if the session never focuses a box. If it puts dictated text in `#rv-ask` or `#rv-note`, a restore can put focus there. On a phone that sets `kb-open` and hides the decision bar (app.css 768). | Transcripts are not typed into draft boxes. The note and the question come from the session object straight into `reviewAct`/`sendTurn`. If text is mirrored into a box for display, mirror it without focusing. |
| 3 | `afterPress` | 3699-3706 (pointer events; a touch lasts until its click or 500 ms). Used by `editRefresh` (3672) and `sgRefresh` (2096). | With no pointer it runs at once. A cook touching the screen to wake it delays redraws for up to 500 ms. | Nothing. Do not route session updates through it. |
| 4 | `typingNow()` stops reloads | 419-428: drafts, `ui.rvNote.text`, editor text, open forms, **`chat.busy`**, focus in a non-draft input. Used by `onReturn` (445), `loadDetail` (1279), `loadPlace` (2415). | A session is not "typing", so on a return to the front the lists reload and `render()` runs. Usually fine, but a reload mid-sentence can change the current card if the queue changed. | Make an active session count as typing (one line: `if (voice.on) return true`), or give `onReturn` its own guard. Keep `loadCount().then(showCount)` so the number still updates. |
| 5 | `onReturn` | 432-454, on every `visibilitychange` (4349). With a new build: `location.reload()` unless something is typed **or the tray holds changes** (434-437). | A session started with an empty tray, then the screen locked and unlocked after a deploy, **reloads the page and ends the session**. The microphone permission and audio unlock are lost too. | Add `voice.on` to the reload guard ("Update ready" instead). On `visibilitychange` to hidden, pause the session (stop listening, cancel speech). On return, resume only after a tap or a "go on" if the platform requires a gesture. |
| 6 | Toasts are visual and short | `toast` 673-697: 8 s default; Undo, "See changes", "Say how it went" buttons. | Confirmations, refusals ("Apply failed", "Nothing applied · skipped: …", "Claude is still answering") are never heard. | The session speaks its own short confirmations for what it did. Errors that matter are spoken from the same place the toast is raised, through a small `announce(msg, level)` the session listens to. Do not speak every toast: "Answered · $0.07" and ripple polling are noise. |
| 7 | The outcome question | `askOutcome` 711-736: a text box in the toast, **focused** (735), offered only from the toast's "Say how it went" after an Apply that closed a task others hang on. | Hands-free, the outcome is better taken **before** Done. `reviewAct` already turns a note on Done into the outcome (4214). | Ask "how did it go?" when the person says Done on a card whose task frees others (`hangsOn` 705-707 is the test). Put the answer in as the note. Never open `askOutcome` in a session. |
| 8 | Later → Pick a date | `<input type=date id=rv-date>` and Use this date (1966, 4036-4037). The page has **no date-words parser**: Tomorrow, Friday and Next week are fixed helpers (4218-4220). | "Later, the 3rd" or "after the weekend" cannot be expressed. | A parser for date words. The server already has `dates.resolve_when` (used by `commands.parse`, commands.py 78-88). Expose it through a small route or a spoken-command route (§4.4) rather than writing a second parser in JS. |
| 9 | Several decisions need a second step | Later opens a menu (4201). The editor, Link a task, Merge into and the reminder form are all forms. | Each needs words: "later Friday", "remind me Monday at 9", "link it to 412". | Spoken intents map to the same staging functions (`reviewAct('fri')`, `stageExtra(id+':r', …)`, `stageLinkChoice`). Anything without a shorthand goes to Claude. |
| 10 | The finish screen waits for a press | Apply → `reviewPaused` (653); "Review another" button (1939, 4039). | Fine, but it has to be spoken: "Applied 6 changes. 9 more waiting, say *more*". | A session intent `more` → `loadReview()`. |
| 11 | `confirm()` dialogs | Chat's Delete thread (4106), connector revoke (4143). | Not on Review. | No need, unless a spoken command that destroys something is ever added. |
| 12 | Scrolling for the eye | `revealLaterMenu`, `jumpTo` smooth scroll, `showBox` (2692-2697), `fitKeyboard` (2681-2689). | Not needed by ear. Moving the page under a finger that has just touched it is the risk. | The session view is a fixed overlay or a large-card mode, so nothing needs scrolling into view. |
| 13 | Rendering rules for touch and narrow screens | `PHONE()` and `TOUCH()` decide the hints, the "kbd" labels (app.css 72) and the place's short list (2560). | None for audio. | A session view can be one layout for both, wide-limited. |
| 14 | The editor's "edits only" Apply keeps the card | 646-647. | A spoken "rename it to …" that stages an edit and then says "apply" would keep the card, which is right. Applying the whole tray mid-session ends the card stack with `reviewPaused`. | Decide whether Apply is spoken mid-session or only at the end. Recommendation: at the end, with a spoken count ("8 changes staged: apply?"). |
| 15 | Polls that redraw | `pollSummary` (491-509) only on Today. `pollRipple` (798-813) → `rippleLanded` → `load()` → `render()`, unless `domHoldsTyping()` (823-827, which does **not** count `chat.busy` or a session). | A ripple landing mid-session redraws the panel. The current item stays the same as long as nothing reorders it. | With the session's state outside the DOM, a redraw costs nothing. Optionally hold `rippleLanded`'s `load()` while `voice.on`. |

---

## 4. Where an audio mode attaches

### 4.1 Is there one function for "the current item and its possible decisions"? No.

Today the knowledge is split up:

| Knowledge | Where it lives now |
|---|---|
| Which item is current | Inline in `renderReview` 1933-1945 (and in `reviewAct` 4199 again, by the same filter) |
| That a suggestion card is current | Inline in `renderReview` 1936-1937 |
| Finish / paused / loading / failed | Inline in `renderReview` 1928-1944 |
| The four decisions and the Later dates | HTML at 1964-1966; behaviour in `reviewAct` |
| Accept / Dismiss an evidence line | HTML in `renderReview` 1967 and `rippleLineHtml`; **behaviour only in `wire()` closures** 3975-3990 |
| Follow its thread / Not about this | HTML in `mailAboutHtml`; behaviour in `wire()` 3992-4000 (`mailCard` is local to `wire`) |
| Suggested links | HTML `linkSuggestionsHtml`; behaviour `linkSuggestionAct` (callable) |
| Extras (top, long-term, due, reminder) | HTML 1969; behaviour `act`, `wireDue`, `wireReminders` (the reminder form reads its values from the DOM, `stageReminder` 557-566) |
| Suggestion card Create / Dismiss | HTML 1996; **behaviour only in `wire()`** 4016-4033 |
| "Since you last looked" Undo | `wire()` 4002-4006 |
| The words for the reason line | `reasonText` returns **HTML** (links), `sinceHtml` returns HTML |
| Keyboard | Its own map at 4333 (d/k/s/x/t/l only) |

### 4.2 What to pull into one place (the refactor)

Recommended: a small Review controller section in app.js beside `renderReview`. Then a click, a key
and a spoken command all call the same functions.

1. **`reviewCurrent()`** → one object, computed only here:
   ```
   {state: 'loading'|'failed'|'finish'|'task'|'suggestion',
    key: 't:412' | 's:9031' | 'finish:…',          // changes ⇔ a new item is current (the session speaks on change)
    index, total, remaining, paused,
    item,                       // review.items[k] for a task card
    task: view(item.task),      // with staged edits laid over
    card: sg,                   // for a suggestion card
    evidence: [...unjudged with change], linkSuggestions: [...unstaged]}
   ```
   `renderReview` (1933-1945) and `reviewAct` (4199-4200) both call it instead of filtering
   themselves. Then `ui.reviewIdx` can be deleted, or made the real cursor if "skip" is wanted (§7).
2. **`reviewOptions(cur)`** → a list of `{id, words, kind, run}` that the current item allows, e.g.
   `done`, `keep`, `later:tomorrow|fri|nextweek|date`, `drop`, `top`, `longterm`, `accept:<eid>`,
   `dismiss:<eid>`, `follow:<eid>`, `notabout:<eid>`, `lsug:<eid>:<act>`, `create`, `dismiss-card`,
   `since-undo:<batch>`, `more` (finish), `ask` (the conversation). The HTML builders draw buttons from
   it with `data-opt="<id>"`, and **one** delegated handler in `wire()` runs `opt.run()`. The keyboard
   map becomes "letter → option id". The voice grammar becomes "phrase → option id".
3. **Named actions** replacing the closures: `acceptEvidence(eid)` (from 3975-3989),
   `dismissEvidence(eid, taskId)` (3990), `followMail(eid)` / `notAboutMail(eid)` (3993-4000, with
   `mailCard` moved out of `wire`), `createSuggestion(id)` (4017-4033), `dismissSuggestion(id)`
   (4016), `nextReviewPage()` (4039).
4. **`reviewAct` returns the keys it staged** (decision, note, judgments, beside-keys). A spoken
   "undo that" or "go back" is then `discardStaged(lastKeys)` (585-589), and the card comes back. Today
   there is no way to undo one decision except from the tray.
5. **Make the conversation path stage like the buttons:** when `stagePlace` stages a `done`/`drop` (or
   a `set` with `snoozed_until`) for the card's own task on a `review` place, run the same tail as
   `reviewAct`: judge the card's evidence `reviewed`, and add `last_reviewed_at` to a Later. Or route
   such proposals through `reviewAct` itself.
6. **`reviewSpeech(cur)`**: the card in plain sentences (reason, name, due and hard, why now, the
   first part of the context, "since you last looked", what a pending proposal suggests, how many links
   are suggested). It is built from the same data as the HTML, not by stripping HTML. `reasonText` and
   `sinceHtml` split into a words part and an HTML part, so the reason is said and shown from one
   source.

All of this is page-only and changes no server behaviour. The existing tests pin very little of it
(§6). `test_search_edit_page.py` 61-63 pins the `afterPress('meta:' + id` block and the exact
`editsOnly` line, so keep those strings.

### 4.3 What the session itself needs from the page

- Entry: a "Start a spoken review" control on the Review tab. The **press is the user gesture** that
  starts the microphone and unlocks speech output [memory: iOS Safari will not start
  `speechSynthesis` or audio without a user gesture]. A `/review?voice=1` address could preselect it,
  but it cannot start audio by itself. That matters for the manifest's Review shortcut and for the
  daily review notification, which opens `/review` (`handleClick`/`openApp`, sw.js 76-101).
- Loop: on `reviewCurrent().key` change → speak `reviewSpeech` → listen → match against
  `reviewOptions` (local and instant) → otherwise send the words as a turn to the card's place (Claude)
  → speak `reply_md` and the proposals in words (`proposalText` 2507-2528 already makes them into
  words) → "stage them?" → `stagePlace(pl, mid, true)`.
- Barge-in and Stop: one visible Stop control, plus a spoken "stop" that is always recognised.
- Mid-session Apply is optional; the end of the queue (the finish state) asks "apply N changes?" →
  `applyAll()`. The `review/finish` call then happens as today.

### 4.4 Where the spoken-command grammar should live

`commands.parse` (commands.py 30-104) is **not** Review's vocabulary:
- it needs task ids;
- it has no Keep;
- `push … fri` moves **`due`** (87), while Review's Later sets `snoozed_until` and never touches `due`.

A Review grammar ("done", "drop it", "keep", "later", "later Friday", "next week", "pin it", "long
term", "accept", "follow the thread", "not related", "create it", "skip", "back", "undo that",
"apply", "more", "stop", plus a note: "done, and the note is …") should be **a Python function with
its own route**. The suite is Python-only and the repository has no JS test runner (§6), and the date
words already live in `dates.resolve_when`. The route takes the transcript plus the current option ids
and returns an option id and its arguments, or "not a command". The page runs the option. This mirrors
how `sendPrompt` tries `/api/commands` before Claude (4240-4261). A round trip to the local server
takes milliseconds, against about 12 s for Claude.

---

## 5. A session view: card, indicator, transcript, Stop

### 5.1 Layout constraints found

- Bottom of a phone: one thing at a time (CLAUDE.md; `bottomRule` 1479). On Review that is the pinned
  decisions bar (z-index 6) above the tray (z 6). The toast is at 70 px (z 7). The sheet uses z 8-9;
  signed-out uses z 20.
- `body.kb-open` hides every bottom bar. A session must never focus a text box (§3 #2).
- The phone task page hides the header bar (`body.page-on`, app.css 353).
- Safe areas: everything pinned uses `env(safe-area-inset-*)`.
- Phone buttons are at least 36 px; decisions 48 px; inputs 16 px font (no iOS zoom).
- Review column: `max-width: 620px`, centred (app.css 444). Desktop has the Top rail at 320 px or
  58 px on the left (54-55).
- Reduced motion is honoured for the spinner (699). Any listening or speaking animation needs the
  same rule.
- Colours come from the custom properties with a dark set (app.css 2-24).

### 5.2 Recommended shape

- **Phone (390 px):** the session replaces the pinned `.big` bar with a **session bar**, still one
  thing at the bottom. It holds the state indicator (listening / speaking / thinking, by colour and
  icon and a text label, with no animation under reduced motion), a one-line transcript ("heard: later
  friday"), **Stop** (at least 48 px, the right-most and largest), and the four decisions kept as
  48 px buttons, so wet hands can still tap. The card itself switches to a "large" mode: the name at
  about 24-28 px, the reason, due and why now, and context clamped. The editor, extras, suggested
  links and the conversation's reply box fold away. The conversation's last answer shows as plain text
  under the card.
  Height budget at 390×844: the header and tabs take about 90 px, the session bar about 140 px with
  safe area. That leaves about 600 px for the card. Fits.
- **Desktop:** the same card in the 620 px column, the session bar fixed at the bottom of the column
  (or the window), the rail untouched. Keys keep working during a session (d/k/s/x plus Space for
  push-to-talk if wanted). The keyboard handler skips while focus is in an input, so the session must
  not take focus.
- **Mount point:** a new fixed element in `index.html`, e.g. `<div class="voice" id="voice"
  hidden>`, drawn by `renderVoice()` from session state and called from `render()` like `renderTray`.
  Because `index.html` is in `api.STAMPED` (api.py 56), adding it is a normal build-stamp reload.
- **Stop must also cover:** leaving the Review tab (`go`), a task page opening (`ui.page`), signing out
  (`signedOut` makes `.app` inert, 845), and `visibilitychange` to hidden.

### 5.3 Service worker and manifest

- `sw.js` handles only `push` and `notificationclick`, **no fetch handler, ever** (sw.js 1-6;
  CLAUDE.md). A spoken session needs nothing from it: microphone, speech synthesis and wake lock are
  all window APIs. A notification tap on "Review" opens `/review` in an open window by `postMessage`
  (`openApp` 76-86 → `followPath` app.js 193-197). It cannot start audio (no gesture).
- `manifest.webmanifest` already has a **Review shortcut** (`/review`, lines 19-20). iOS ignores
  manifest shortcuts (CLAUDE.md). A "Spoken review" shortcut could be added (`/review?voice` or a new
  path in `api.SHORTCUT_PATHS` and `parseAddress`). It would land on the start button, because audio
  needs a press.
- Permissions: no `Permissions-Policy` or CSP is set on the page, so `getUserMedia` is allowed on the
  secure origin (Cloudflare HTTPS, or `127.0.0.1`) [code: only `api.py` 1158, the OAuth consent page,
  sets a CSP]. The service worker has no part in it.
- **Platform facts that bear on the page** (the speech-technology report will go deeper):
  - Speech recognition (`webkitSpeechRecognition`) is **not available in iOS Home Screen web apps**
    per WebKit, and the bug is RESOLVED LATER with no later fix noted (last comment 2025-03-01)
    [web, 2026-11-19: https://bugs.webkit.org/show_bug.cgi?id=225298]. A third-party page claims it
    works [web: https://whatpwacando.today/speech-recognition], unverified. So the installed iPhone app
    probably needs audio captured in the page and transcribed elsewhere (a server-side or cloud
    recogniser), or the session runs in Safari proper.
  - Screen Wake Lock works in iOS Home Screen apps since iOS 18.4 (March 2025) and is Baseline [web,
    2026-11-19: https://bugs.webkit.org/show_bug.cgi?id=254545,
    https://web-platform-dx.github.io/web-features-explorer/features/screen-wake-lock,
    https://developer.mozilla.org/docs/Web/API/Screen_Wake_Lock_API]. The lock is released when the
    page is hidden and has to be taken again on `visibilitychange`
    [https://developer.chrome.com/articles/wake-lock/]. The page uses no wake lock today.
  - [memory] iOS suspends a backgrounded or locked page's audio and timers. A kitchen session needs the
    screen on (wake lock) and the app in front.

---

## 6. How the page is tested today, and how a voice loop could be tested offline

### 6.1 Today

- **pytest only, offline.** Page behaviour is pinned by **reading the source as text** and asserting
  that strings are present: `tests/test_search_edit_page.py` (`JS = …read_text()`, 17 such
  assertions), and a few each in `test_stage1.py`, `test_stage8.py`, `test_core.py`, `test_polish.py`
  (790), `test_review_fixes_c.py`, `test_stage4b.py`. Examples: `DRAFT_BOXES` holds the new boxes
  (test_search_edit_page.py 34-40); the `editsOnly` line (63); the `afterPress('meta:'` block (61-62).
  Server routes the page uses are tested with `TestClient` (`/api/review/finish` 415 vs 200,
  test_phase6.py 259-262).
- **No JS unit tests and no Node dependency in the repository**, by decision ("the repository gains no
  Node dependency", `docs/superpowers/plans/2026-11-10-stage-1-app-shell.md` 41).
- **Rendered checks** run as Playwright scenarios **outside the repository** (a dev harness: `node
  pw.mjs scenarios/<name>.mjs --device both`, desktop 1280×900 and phone 390×844 with touch; scenarios
  and screenshots in a scratchpad, not committed) (stage-4b plan line 30; stage-8 plan line 34). On
  this machine: node v20.20.2, and Chromium builds under `~/.cache/ms-playwright` (1208-1228). There is
  no Python `playwright` in `.venv`.
- `node --check` catches syntax only. CLAUDE.md records that a `const` used above its line threw at
  load and only a page load showed it.
- The owner's rule: render web changes at desktop and 390 px on a database copy and look, before
  calling them done (memory "Verify UI by rendering").
- Claude runs in tests: `api.create_app(cfg, db_path, chat_runner=fake)` takes a fake runner
  (api.py 279; used in test_phase5.py 380, test_search_edit_chat.py 29), so a turn's SSE stream can be
  tested offline.

### 6.2 A voice loop, offline

1. **Grammar and dates in Python** (§4.4): table-driven pytest with transcripts × the current item's
   option ids → the expected option and arguments, including mis-hearings ("dun", "drop it", "later
   Friday", "next week", "keep it", "skip") and refusals (an option the current card does not have).
   This is where most bugs would be.
2. **The session's state machine in JS**, written as a pure reducer (`step(state, event) → {state,
   effects}`, events: `heard`, `spoke-end`, `turn-done`, `item-changed`, `stop`, `hidden`). It runs in
   Playwright via `page.evaluate`. If the owner accepts a dev-only Node test, it could be tested
   without a browser, but that goes against the no-Node rule.
3. **Rendered scenarios (outside the repo):** `page.addInitScript` installs a **fake recogniser**
   (an object with `start/stop/onresult` that the scenario drives with `window.__say('later friday')`)
   and a **fake `speechSynthesis`** that records utterances and fires `end` at once. Assert the tray,
   the card moving on, what was "spoken", and the session bar at both sizes. For a real-audio smoke
   test, Chromium flags `--use-fake-ui-for-media-stream --use-fake-device-for-media-stream
   --use-file-for-fake-audio-capture=<wav>` feed a recorded file as the microphone [memory].
4. **Claude turns** with the fake `chat_runner` (fixed `reply_md` and proposals), so "spoken free
   speech → proposal → 'stage it?' → yes" runs end to end with no paid run.
5. **Source pins** in pytest for the rules a refactor must keep: `reviewAct` judges evidence, the
   conversation path does the same, `voice.on` is in the `onReturn` reload guard, the session element
   is outside `#panel`, and no `focus()` inside the session code.

---

## 7. Open questions (not settled by the code)

1. **Skip versus Keep.** Review has no "skip" today. Is "next" in a spoken session Keep (a decision,
   suppressed until something changes), or a new skip that leaves the card in the queue (needs a cursor:
   `ui.reviewIdx` could become real)?
2. **When to Apply.** At the end of the stack (as now, then `review/finish`), or a spoken "apply" at
   any time? Mid-session Apply shows the paused finish screen.
3. **Batch source.** `applyAll` takes `'review'` from the tab. Should a spoken session be a source of
   its own (Log and History wording: `WHO` map 1353, `SOURCE_NAMES` 2721)? Note that "voice" already
   means **Plaud recordings** in Factotum (`closed_by` value `voice`, SPEC.md 132; "voice verbs" SPEC
   §8.1), so a new name such as "spoken review" avoids a clash.
4. **Suggestion cards by voice.** Create is immediate (not staged) and Dismiss is staged. Should the
   spoken flow confirm before Create ("create 'Reply to Ana' under Personal?")? How is an edited draft
   read back?
5. **What Claude may stage from speech without a second "yes".** Today the person ticks and presses
   "Stage N with this decision". Hands-free, either every proposal is read back and confirmed, or a
   setting allows auto-staging (still undoable by tray discard and History).
6. **Cost per session.** Shorthand is free. Each free-speech turn is about $0.065 and 12 s [db]. A
   15-card session with three questions costs about $0.20. Owner memory says "fresh over cheap", but
   the spoken flow should still say when it is asking Claude.
7. **iOS installed app versus Safari** for speech recognition (above). The page design should not
   depend on which recogniser is used.
8. **Not verified in a browser:** the Later-menu glitch (§1.4); how `render()` during a spoken session
   interacts with a real recogniser's callbacks; whether `restoreDraftBox`'s programmatic focus raises
   the iOS keyboard (it should not without a gesture; not checked).

## What I could not find out

- How speech recognition behaves in the installed app on the owner's iPhone. Nothing was run on a
  device; the WebKit bug is documentation only.
- Measured latency of a turn as heard (SSE start to `done`). The DB holds the run's `duration_ms`, not
  the page's wall clock. The server's setup before the run (place lookup, prompt build) is not in that
  number.
- Whether the dev harness (`pw.mjs`) still exists. It lives outside the repository and its path is not
  recorded in the repo.
