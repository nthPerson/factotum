# Task 12 report
Implemented the two Settings controls in renderSettings (wording from the brief; the filing hint adds "Costs about 4 cents for each task you add by hand with only a name"). Filing model default sonnet in example, fixture, starter (models: {filing: sonnet}; runner code default was already sonnet); test_stage7_filing pins sonnet. Full suite 530 passed. `factotum effective` loads.
Scenario: DEV/scenarios/s7-12-settings.mjs, both devices, no problems. Shots in scratchpad/shots: s7-12-review-{desktop,phone}.png (Suggested links reads 5 a day, hint wraps), s7-12-claude-{desktop,phone}.png (Filing unticked, hint with cost; phone no sideways scroll true). S2 PUT bodies only the one key; reload keeps state; 3 then back to 5; S3 effective false/5.
Concern: brief said ids `#s-...`; controls are found by data-key.

## Fix round 1
1. Filing hint restored to the brief's exact text ("... It never changes what you typed. Its cost is in the Log."); the cost sentence is gone. Only the string changed, so no re-render.
2. Reverted the model change: example, fixture, starter and tests/test_stage7_filing.py are back to their state before ca481936 (filing: haiku; the assertion expects haiku). The sonnet-vs-haiku measurement is left for a separate controller decision with SPEC/plan updates.
Command: .venv/bin/pytest -q -> 530 passed.
