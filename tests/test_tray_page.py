"""The page's mirror of the server's tray (spoken Review stage 1, design §5.6), run for real in node: the tray functions
of factotum/web/app.js are cut out of the page script and driven against a fake server, so a change taken back while
its send is in flight, and a cache read before the tray has started, are checked as behaviour, not as source text."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from factotum import api

JS = (Path(api.__file__).parent / "web" / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _fn(name):
    """`function <name>` (or `async function <name>`) up to the next top-level function of the page script."""
    m = re.search(rf"\n  (async )?function {name}\(", JS)
    assert m, name
    i = m.start() + 1
    ends = [x for x in (JS.find("\n  function ", i + 1), JS.find("\n  async function ", i + 1), JS.find("\n  window.", i + 1)) if x > 0]
    return JS[i:min(ends)]


def _tray_script(scenario):
    state = re.search(r"\n  const tray = \{.*?\};\n", JS).group(0)
    block = JS[JS.index("  const TRAY_FIELDS"):JS.index("  function syncTray()")]
    fns = "\n".join(_fn(n) for n in ("syncTray", "sendTray", "trayAnswer", "trayRedraw", "trayFail"))
    return f"""
let staged = new Map();
{state}
const store = {{}};
const localStorage = {{ getItem: k => (k in store ? store[k] : null), setItem: (k, v) => {{ store[k] = String(v); }}, removeItem: k => {{ delete store[k]; }} }};
const afterPress = (_, f) => f(), render = () => {{}}, renderTray = () => {{}}, bottomRule = () => {{}}, placePageBar = () => {{}};
const placeReviewBar = () => {{}}, typingNow = () => false, trayBar = () => {{}}, deviceLabel = () => 'test', toast = () => {{}};
// The fake server: a tray it changes by the ops it is sent, answering once `gate` lets the first call through.
const server = new Map(); let rev = 0, held = null, calls = [];
const answer = () => ({{ rev, entries: [...server].map(([k, e]) => [k, e, {{}}]) }});
function api(path, opts) {{
  const body = JSON.parse(opts.body); calls.push(body.ops);
  const run = () => {{ for (const o of body.ops) {{ if (o.del != null) server.delete(o.del); else server.set(o.put, o.entry); }} rev += 1; return answer(); }};
  if (calls.length === 1 && hold) return new Promise(r => {{ held = () => r(run()); }});
  return Promise.resolve(run());
}}
let hold = false;
const tick = () => new Promise(r => setTimeout(r, 0));
async function settle() {{ for (let b = null; b !== tray.busy;) {{ b = tray.busy; await b; await tick(); }} }}
{block}
{fns}
(async () => {{
{scenario}
}})().catch(e => {{ console.error(e && e.stack || e); process.exit(1); }});
"""


def _run(scenario):
    out = subprocess.run([NODE, "-e", _tray_script(scenario)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


PIN = "{ task_id: 412, op: 'set', fields: { top_rank: 1 }, label: 'pin to top' }"
DONE = "{ task_id: 412, op: 'done', fields: {}, label: 'done' }"
REPORT = "console.log(JSON.stringify({ server: [...server.keys()], staged: [...staged.keys()].map(String), calls }));"


def test_a_change_taken_back_while_its_send_is_in_flight_stays_taken_back():
    """Pin, then Pin again (unstaged) before the first send is answered: the server's answer must not bring it back."""
    r = _run(f"""
tray.booted = true; hold = true;
staged.set('412:top', {PIN}); syncTray(); await tick();
staged.delete('412:top'); syncTray();
held(); await settle();
{REPORT}""")
    assert r["server"] == [] and r["staged"] == []


def test_a_change_staged_again_while_its_discard_is_in_flight_is_kept():
    """Done is in the tray; Discard (in flight), then Done again, the same entry: the Done must reach the server."""
    r = _run(f"""
tray.booted = true;
server.set('412', {DONE}); rev = 1; trayAnswer(answer(), null, {{ quiet: true }});
hold = true;
staged.delete(412); syncTray(); await tick();
staged.set(412, {DONE}); syncTray();
held(); await settle();
{REPORT}""")
    assert r["server"] == ["412"] and r["staged"] == ["412"]


def test_a_change_made_again_while_its_send_is_in_flight_goes_next():
    """The case the old filter got right stays right: a change changed again in flight is sent again."""
    r = _run(f"""
tray.booted = true; hold = true;
staged.set('412:top', {PIN}); syncTray(); await tick();
staged.set('412:top', Object.assign({PIN}, {{ fields: {{ top_rank: 2 }} }})); syncTray();
held(); await settle();
console.log(JSON.stringify({{ rank: server.get('412:top').fields.top_rank, staged: staged.get('412:top').fields.top_rank, sends: calls.length }}));""")
    assert r == {"rank": 2, "staged": 2, "sends": 2}


def test_the_cache_is_not_written_before_the_tray_has_read_it():
    """A return before load() has started the tray must not overwrite what the last session kept unsent."""
    r = _run("""
const kept = JSON.stringify({ rev: 3, entries: [], unsent: [{ put: '412', entry: { task_id: 412, op: 'done', fields: {}, label: 'done' } }], unsentAt: Date.now() });
store['factotum.tray'] = kept;
syncTray(); trayCache(); await settle();
console.log(JSON.stringify({ same: store['factotum.tray'] === kept, calls: calls.length }));""")
    assert r == {"same": True, "calls": 0}


def test_a_return_before_the_tray_has_started_leaves_it_to_load():
    body = _fn("onReturn")
    assert "if (tray.booted) { syncTray(); trayPoll(); }" in body
