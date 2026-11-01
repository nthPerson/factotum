# legacy/ — reference copies from `nthPerson/plaud-sync`

Copied verbatim from lark-server's `~/plaud-sync` (plaud-sync commit `f7c5ae2`, 2026-11-01) per
`REPO_RETIREMENT_AND_NEW_REPO_INITIALIZATION.md` §5. Nothing here runs; these are the files the
later phases port, kept next to the code that replaces them:

| File | Ported in | What to take |
|---|---|---|
| `plaud-sync-prompt.txt` | phase 3 → `prompts/sync-file.md` | Steps 1–4, the taxonomy tie-breakers |
| `planner-prompt.txt` | phase 2 / 4 → `prompts/planner-morning.md`, the brief | Steps 2, 5, 6 (ingest rules, Today layout, brief HTML) |
| `planner-commands-prompt.txt` | phase 3 (voice verbs) | Step 2's verb table |
| `planner.py` | phase 3 / 4 | `canvas_bundle`, `check_auth`, `describe_failure`, `send_mail` |
| `plaud_watcher.py` | phase 3 → `plaud/` | the IMAP IDLE loop, `process_message`'s retry and `\Seen` handling |
| `planner-sources.json` | already folded into `config/factotum.example.yaml` | — |
| `*.service`, `*.timer` | reference only | what to disable on lark-server at each cutover |

Delete this directory in phase 6 when plaud-sync is archived.
