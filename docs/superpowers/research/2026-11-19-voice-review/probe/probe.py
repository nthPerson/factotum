import json, subprocess, sys, time
PROMPT = """Today is Thursday 2026-11-19. The card on screen:
{"id": 412, "name": "Email the lab about the TA schedule", "due": "2026-11-21", "hard": 0, "project": "Teaching"}
The owner said: "push that to monday and remind me at nine".
Reply with one JSON object only: {"spoken": "<one short sentence>", "decision": null, "proposals": [{"op": "...", "fields": {...}}]}"""
SYS = "You turn one spoken sentence about one task card into a JSON object. Output the JSON object and nothing else."
def run(model, sysprompt):
    cmd = ["claude", "-p", "--model", model, "--output-format", "json", "--max-budget-usd", "0.10", "--max-turns", "1",
           "--permission-mode", "default", "--setting-sources", "", "--disable-slash-commands",
           "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--allowedTools", "", "--tools", ""]
    if sysprompt: cmd += ["--system-prompt", SYS]
    t = time.monotonic()
    p = subprocess.run(cmd, input=PROMPT, capture_output=True, text=True, timeout=120)
    wall = time.monotonic() - t
    try:
        env = json.loads(p.stdout)
        u = env.get("usage", {})
        print(f"{model:7} sys={'own' if sysprompt else 'default'} wall={wall:5.2f}s dur={env.get('duration_ms')} api={env.get('duration_api_ms')} cost=${env.get('total_cost_usd'):.5f} cache_read={u.get('cache_read_input_tokens')} cache_write={u.get('cache_creation_input_tokens')} in={u.get('input_tokens')} out={u.get('output_tokens')} err={env.get('is_error')} :: {(env.get('result') or '')[:160]!r}", flush=True)
    except Exception as e:
        print(f"{model} sys={sysprompt} wall={wall:.2f}s rc={p.returncode} parse-fail {e}: {p.stdout[:200]!r} {p.stderr[:200]!r}", flush=True)
for model, s in [(a, b) for a, b in (x.split(":") for x in sys.argv[1:])]:
    run(model, s == "own")
