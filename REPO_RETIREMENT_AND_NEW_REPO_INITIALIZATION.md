# Retiring `plaud-sync`, starting `factotum`

Written for: Claude Code running on **mf-pc** (WSL2) as user `robert`, picking up implementation
of `nthPerson/factotum` after the design work done on lark-server on 2026-11-01. Read this,
then `SPEC.md`, then `docs/SETUP.md`. Robert approved the spec; you do not need to re-litigate it.

## 1. What exists and where

| Thing | Where | State |
|---|---|---|
| The old automation (watcher, planner, prompts, skills) | `nthPerson/plaud-sync`, deployed on **lark-server** (bg-pc) | running; keep it running until phase 3 cutover |
| Its runtime files (`processed.json`, `runs.jsonl`, `planner-runs.jsonl`, `planner-state.json`, `.env`) | lark-server only, git-ignored | copy, never sync |
| The new service | `nthPerson/factotum` (this repo) | phase 1 scaffolded and tested offline; nothing deployed |
| Spec | `SPEC.md` here; `FACTOTUM-SPEC.md` in plaud-sync is a pointer | canonical here |
| Mockup | https://claude.ai/artifact/DcutyeKweJaxWYJGBGVr8X | reference for the UI |
| Notion | 🎙️ Plaud Synced Notes DB, Tasks DB (274 open rows), 📋 Planner page | Tasks DB becomes import source then mirror target |
| Mailbird Next MCP | `https://127.0.0.1:52000/mcp` on the Windows host, bearer in Claude Code's `~/.claude.json` env | reachable from WSL (mirrored networking) |
| Claude Code MCPs on mf-pc | plaud (hosted), mailbird, Google Calendar, Notion, Gmail, Slack, … | headless runs inherit them |

Facts that cost time to learn, carried over from plaud-sync's CLAUDE.md:
- MCP write tools in a project's `ask` list silently stall headless runs. Keep every Notion,
  Calendar and Mailbird write tool out of `ask`.
- A tool result over ~40 KB never reaches Claude; it spills to a file. That is why Factotum
  fetches transcripts with the `plaud` CLI and passes file paths, and why nothing reads whole
  Notion views any more.
- Headless `claude -p` cannot re-authenticate; a dead refresh token exits in 50 ms with an empty
  stderr. The old `planner.py` has a `check_auth()` pre-flight worth porting in phase 3.
- Restarting a watcher mid-run loses that recording. Check the journal before restarting.
- Notion SQL queries are metered; the REST API used here is not.

## 2. Phase 0 — make the machine ready (Robert does most of this; you verify)

Work through `docs/SETUP.md` §1–§8. Each section ends with a check; run them all before
touching code. Do not proceed to phase 1 until:
```bash
cd ~/factotum && .venv/bin/pytest                     # green
plaud today                                           # lists recordings
curl -sk https://127.0.0.1:52000/mcp -H "Authorization: Bearer $MAILBIRD_TOKEN" …   # JSON-RPC answers
.venv/bin/python -m factotum import-notion --dry-run  # prints the bucket report
```
Secrets go in `.env` only. `config/factotum.yaml` is a copy of the example with Robert's values
(the example already carries them).

## 3. Phase 1 — bring up the store and the UI on real data

1. `python -m factotum import-notion --dry-run`. Show Robert the bucket counts and the
   `hard_last_call` / `undated_keep` lists. Expect roughly: ~90 Canvas future, ~40 hard lapsed,
   ~10 last call, ~20 soft faded, ~80 undated faded, ~30 undated keep. If a bucket looks wrong,
   adjust `lifecycle.classify_import`, not the data.
2. `--apply`. Then `python -m factotum score` and `python -m factotum review` as sanity checks.
3. `sudo cp deploy/factotum.service /etc/systemd/system/ && sudo systemctl enable --now factotum`.
   Open http://127.0.0.1:8750, then https://factotum.nthperson.dev through Access.
4. **Gate:** Robert does one Review session, applies the batch, and says whether the cap, the
   buckets and the row layout feel right. Fix what he reports, then tag `v0.1`.

## 4. Phases 2–6

Follow `SPEC.md` §14. Each phase is one or a few PRs on `main` with tests under `tests/`.
Guidance per phase:

- **Phase 2 (push + brief + calendar):** add APScheduler; `notify/ntfy.py` publishes with
  `Click` and three `view` actions; `/r/<token>` routes apply snooze/done and render one line;
  `google-auth` subcommand; `calendar/google.py` read + create. The morning brief reuses the
  HTML rules from plaud-sync's `planner-prompt.txt` Step 6 (inline styles, ≤ 640 px).
- **Phase 3 (Plaud port):** port `plaud_watcher.py`'s IMAP IDLE loop (it is sound; keep the
  `\Seen` handling and the one retry). Write `prompts/sync-file.md` from `plaud-sync-prompt.txt`
  Steps 1–4 with the new extraction bar (SPEC §5) and the JSON shape (SPEC §8.1). Run in
  parallel with lark-server for one week: both write the Notion note page (idempotent on
  `Source Link`), only Factotum writes tasks. Then `sudo systemctl disable --now plaud-sync`
  on lark-server and copy `processed.json` across so no UID reprocesses.
- **Phase 4 (mail + Canvas + planner):** `mail/mailbird.py` is an MCP streamable-HTTP client
  (use the `mcp` Python package; `verify=False` for 127.0.0.1 only). Port `planner.py`'s Canvas
  code as is. Write `prompts/planner-morning.md` from `planner-prompt.txt` with Steps 1 and 4
  removed (Python reads and scores). Then disable `planner@morning.timer`,
  `planner@evening.timer`, `plaud-digest.timer` on lark-server and delete the Gmail forwarding
  filter on rashe7414@sdsu.edu.
- **Phase 5 (Chat):** `factotum mcp` is a stdio MCP server (`mcp` package) with three read-only
  tools. The chat runner passes `--resume` per thread and streams `stream-json` lines to the UI
  over SSE. Allowlist from SPEC §8.3; never the destructive Mailbird tools.
- **Phase 6:** turn `sinks.notion_tasks_mirror` on, archive plaud-sync (GitHub → Settings →
  Archive), update `plaud-sync-planner` and `plaud-sync-ops` skills (they live in
  `~/sync/workbench` and the plaud-sync repo) to call the Factotum API.

## 5. What to carry over from plaud-sync, and what to leave

Carry over (copy the files into `legacy/` here for reference, then port):
- `plaud-sync-prompt.txt` Steps 1–4 and the taxonomy tie-breakers (BDA M.S. vs courses,
  SCIBER-CT vs DiCE Lab, ZIP Launchpad vs Car Sounds). They become `prompts/sync-file.md`.
- `planner-prompt.txt` Steps 2, 5 and 6 (ingest rules, Today layout, brief HTML).
- `planner-commands-prompt.txt` Step 2's verb table, as the voice-instruction vocabulary.
- `planner.py`: `canvas_bundle`, `check_auth`, `describe_failure`, `send_mail`.
- `plaud_watcher.py`: the IMAP IDLE loop and `process_message`'s retry.
- `planner-sources.json`: already folded into `config/factotum.example.yaml`.

Leave behind: the email command channel, the Today page and Command Log writers, the weekly
digest (fold into the morning brief later if missed), the Notion `Open` view reads, every
`.tmp_*` file, the `Planner` project option (command recordings become ordinary notes).

## 6. Rules for this repo

- `main` only, small commits, every phase leaves `pytest` green.
- No secrets, no runtime data, no per-person config in git (`.gitignore` already covers it).
- Keep the user-specific facts out of code: if you find yourself typing a Notion id, a course
  code or an email address in a `.py` file, it belongs in `config/factotum.example.yaml`.
- Update `SPEC.md` when a decision changes; update `docs/SETUP.md` when a setup step changes.
- Before restarting `factotum.service`, check `journalctl -u factotum -n 5` for a Claude run in
  flight.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
