import json, subprocess, sys, time, threading
CARD = """Today is Thursday 2026-11-19. The card on screen:
{"id": 412, "name": "Email the lab about the TA schedule", "due": "2026-11-21", "hard": 0, "project": "Teaching"}
For each sentence the owner says, reply with one JSON object only: {"spoken": "<one short sentence>", "decision": "done|drop|keep|later|null", "proposals": [{"op": "set|add_reminder|add_note", "fields": {}}]}
The owner said: """
SAYS = ["push that to monday and remind me at nine", "actually make it tuesday", "drop it, she already answered", "keep it, and note that the room changed"]
model = sys.argv[1]
cmd = ["claude", "-p", "--model", model, "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
       "--max-budget-usd", "0.20", "--permission-mode", "default", "--setting-sources", "", "--disable-slash-commands",
       "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--allowedTools", "", "--tools", ""]
t0 = time.monotonic()
p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
def send(text):
    p.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}) + "\n"); p.stdin.flush()
def wait_result(deadline=60):
    end = time.monotonic() + deadline
    first = None
    while time.monotonic() < end:
        line = p.stdout.readline()
        if not line: return None, first
        try: ev = json.loads(line)
        except Exception: continue
        if ev.get("type") == "assistant" and first is None: first = time.monotonic()
        if ev.get("type") == "result": return ev, first
    return None, first
threading.Timer(150, p.kill).start()
for i, s in enumerate(SAYS):
    t = time.monotonic()
    send((CARD if i == 0 else "The owner said: ") + s)
    ev, first = wait_result()
    if ev is None:
        print("no result; rc", p.poll(), p.stderr.read()[:400] if p.poll() is not None else ""); break
    u = ev.get("usage", {})
    print(f"{model} turn {i+1}: wall={time.monotonic()-t:5.2f}s (since spawn {time.monotonic()-t0:5.2f}s) first_assistant={(first - t) if first else None} dur={ev.get('duration_ms')} cost_total=${ev.get('total_cost_usd')} out={u.get('output_tokens')} :: {(ev.get('result') or '')[:140]!r}", flush=True)
p.stdin.close()
try: p.wait(10)
except Exception: p.kill()
print("exit", p.returncode)
