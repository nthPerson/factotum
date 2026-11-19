# Search tab and editing on Review: design and plan (2026-11-18)

The owner asked for two things, to be planned and built without stopping:

1. A Search feature on its own tab: by keyword (match or fuzzy), by date range and by other useful
   filters, with a chat integration like the other pages have.
2. Every attribute and the body of a Review card editable, by hand and by prompt, with the linking
   tools the Today tab has. Why: full control over the tasks kept and created from Review.

Nobody was asked questions (the owner said not to stop), so every choice below is the builder's call.

## What exists and is reused

- `by_hand.search` + `GET /api/search`: number or every word of the name / project, 12 rows. Find any
  task (`/`) and the shared task picker use it. It stays as it is (the picker depends on its order).
- The conversation in place (`chat.place_thread`, `PLACE_TEXT`, `placeOf` / `placeHtml` / `sendTurn`):
  origins `review`, `task`, `today`. Proposals stage in the tray; Apply is `batches.apply`.
- The editor (`editorHtml`, `stageEdit`, `editRefresh`), linking by hand (`linkByHandHtml`,
  `pickerHtml`, `stageLinkChoice`), the linked list with Remove (`linkedListHtml`), More (`moreHtml`).
  All of it stages; nothing writes outside a batch.
- A suggestion card (`suggestionCard`, `api.create_suggestion`): one `review` batch on Create.

## Approaches considered

**Search engine.** (a) SQLite FTS5 with a trigram tokenizer: fast, but a second copy of the text to keep
in step with every writer, a migration, and no control over ranking by field. (b) `LIKE` in SQL as
`by_hand.search` does: no fuzzy, no ranking, no snippets. (c) Read the rows and match in Python: a few
hundred to a few thousand tasks, so one pass is milliseconds; fuzzy is a comparison against the
vocabulary of words, ranking and snippets are plain code, no schema change. **Chosen: (c).**

**Chat on Search.** (a) Only the Chat tab with the results attached. (b) A conversation in place under
the search box (a new origin, `search`), told what was searched and carrying the selected rows or the
first results, plus a `tasks_search` tool so the run can look further with the same engine.
**Chosen: (b)**: it is how Today works, and the tool makes "find what I promised Reza in October" work.

**Editing a task's card.** (a) A second editor written for Review. (b) The one editor and the one set of
link tools, drawn in the card. **Chosen: (b)**: one behaviour everywhere, edits stage beside the decision.

**Editing a suggestion card** (no task exists yet). (a) Create first, edit after. (b) The card holds a
draft the owner edits; Create sends the draft; the batch is still one `create` with its links, thread
and verdict. A conversation on the card (origin `suggestion`) may answer with a `create`; "Use on this
card" copies it into the draft. **Chosen: (b)**: nothing exists until Create, and its Undo is unchanged.

## 1. Search

### `factotum/search.py` (reads only)

`run(conn, cfg, today, **params) -> dict`

- `q`: words; `"a phrase"`; `-word` leaves rows out; a lone `412`, `#412` or `Task-412` is that task.
- `mode`: `match` (every word is somewhere in the searched text, accents and case folded) or `fuzzy`
  (a word also matches a word that is close: one slip in a short word, a similar spelling or a shared
  stem in a longer one). When `match` finds nothing and there are words, the answer is the fuzzy one
  with `fell_back: true`.
- `within`: `all` (name, project, people, text, why now, outcome), `name`, `text`, `people`.
- `status` (open, done, dropped, faded, lapsed), `project`, `source`, `effort`: lists; empty means any.
- `date_field` (`due`, `created`, `closed`, `changed`) with `date_from` / `date_to` (inclusive days).
- `flags`: `hard`, `undated`, `waiting`, `top`, `longterm`, `linked`, `later`, `reminder`.
- `sort`: `relevance` (default with words), `due`, `created`, `closed`, `changed`; `limit` ≤ 200, `offset`.

Answer: `results` (id, name, status, project, due, hard, effort, source, the dates, people, outcome,
`merged_into`, `fields` matched, `name_spans`, `snippet` {field, text, spans}), `total`, `more`,
`mode`, `fell_back`, `facets` (counts per status, project, source, effort, each with the other filters
applied), `said` (the search in words). Ranking: name over project and people over text over outcome;
a whole word over a part of one; an exact match over a close one; open before closed on a tie.

### Around it

- `GET /api/search/advanced` (reads only; no lock).
- MCP tool `tasks_search` (the same parameters, compact rows, the usual result budget; marks its rows
  seen, so a proposal may name them). Listed for chat, the skills and connectors.
- `python -m factotum search "words" [--fuzzy] [--status …] [--project …] [--from D --to D --date-field F]`.
- Chat: origin `search` (a box conversation like Today's: the newest under 12 h, Close starts anew).
  The turn carries what was searched (`search`: the sentence `said`, the count) and the selected rows,
  else the first ten results.

### The tab

Search sits between Review and Chat, at `/search`. A box (searches 250 ms after the last key), Match /
Fuzzy, Search in, Status chips with counts, Project / Source / Effort, a date field with From / To and
presets, the "Only" flags, Sort, Clear. On a phone the filters fold behind "Filters (n)".
Open results are the ordinary rows (tick, open, edit, link, bulk bar, `j` / `k`), with the matched text
under the name; closed ones are read-only lines with Open and Reopen. "Ask Claude about these results"
is the conversation in place. The search is remembered in this browser. `/` focuses the box; Find any
task gains "Open in Search".

## 2. Review

### A task's card

"Edit this task" (remembered) opens, in the card: the editor (name, where this stands, due, project,
effort, hard, who, and a new field, **Why now**), the linked tasks with Remove, Link a task, Add a step,
and Split into steps / Duplicate / Merge into another. Everything stages beside the decision; the card's
name, chips and text redraw from the staged values. Apply on the editor's own line applies the edits
and stays on the card. By prompt it is the card's conversation, as now.

### A suggestion card

The card is a form: name, text, due, hard, project, effort, who, and its links (remove one; add one with
the picker as "a step of" or "waiting on"). `POST /api/review/suggestions/{id}/create` takes
`{fields, links}`: the edited values over the card's own, cleaned by the same cleaner; `source`, its
key, link and account stay Python's. One batch, one Undo, as before. A conversation on the card
(origin `suggestion`, kept per card in `chat_threads.card_id`) is told the draft; its `create` proposal
shows "Use on this card", which fills the form. Nothing is created until Create.

## Not in this change

Saved searches; search over mail, recordings or chat threads; regular expressions; editing a closed task.

## Plan

1. `search.py` + tests; the route; the tool; the command.
2. Chat: origins `search` and `suggestion`, `card_id`, the turn body, the prompt text; tests.
3. `create_suggestion` with the edited draft; tests.
4. The page: the Search tab; Review's editor and links; the suggestion form and its conversation.
5. Styles; render on a copy of the database at desktop and phone size; SPEC, SETUP, CLAUDE.md.
6. Suite green, merge into `main`, restart the service when no run is in flight.
