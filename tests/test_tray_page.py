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
    fns = "\n".join(_fn(n) for n in ("syncTray", "sendTray", "trayAnswer", "trayRedraw", "trayFail", "trayPoll", "trayApply",
                                       "cardProv", "decideOn", "chipLeft", "discardStaged"))
    return f"""
let staged = new Map();
{state}
const store = {{}};
const localStorage = {{ getItem: k => (k in store ? store[k] : null), setItem: (k, v) => {{ store[k] = String(v); }}, removeItem: k => {{ delete store[k]; }} }};
const presses = [], toasts = [], renders = [], ui = {{ trayOpen: false, trayOff: new Set() }}; let inPress = false;
const afterPress = (k, f) => {{ presses.push(k); const w = inPress; inPress = true; try {{ f(); }} finally {{ inPress = w; }} }}, render = () => {{ renders.push(inPress); }}, renderTray = () => {{}}, bottomRule = () => {{}}, placePageBar = () => {{}};
const placeReviewBar = () => {{}}, typingNow = () => false, trayBar = () => {{}}, deviceLabel = () => 'test', toast = m => {{ toasts.push(m); }};
let rp = {{ back: null, inflight: 0 }}; const passAnswer = () => {{}};      // the pass (spoken Review 2a) is not this test's
const persistStaged = () => syncTray(), sgCard = () => null, sgDraft = () => ({{ name: '' }}), cut60 = s => String(s).slice(0, 60);
// The fake server: a tray it changes by the ops it is sent, answering once `held` lets the first held call through.
// /api/tray/ops: the ops; /api/review/decide: `decideRows` (key, entry, decides), or `decideFail` thrown;
// /api/tray/apply: everything, or 409 when seen_rev is not the tray's revision. `hold` holds the first ops call,
// `holdPath` the first call to that path.
const server = new Map(), metas = new Map(); let rev = 0, held = null, calls = [], paths = [], holdPath = null, decideRows = [], decideFail = null;
const answer = () => ({{ rev, entries: [...server].map(([k, e]) => [k, e, metas.get(k) || {{}}]) }});
const fail = (status, detail, body) => Object.assign(new Error(String(status)), {{ status, detail, body }});
function api(path, opts) {{
  const body = opts && opts.body ? JSON.parse(opts.body) : {{}}; paths.push(path);
  let run;
  if (path === '/api/tray/ops') {{
    calls.push(body.ops);
    run = () => {{ for (const o of body.ops) {{ if (o.del != null) {{ server.delete(o.del); metas.delete(o.del); }} else server.set(o.put, o.entry); }} rev += 1; return answer(); }};
  }} else if (path === '/api/review/decide') {{
    run = () => {{ if (decideFail) throw fail(decideFail, 'refused', {{ tray: answer() }}); for (const [k, e, d] of decideRows) {{ server.set(k, e); metas.set(k, {{ decides: d }}); }} rev += 1; return {{ tray: answer(), keys: decideRows.map(r => r[0]), removed: [], said: '' }}; }};
  }} else if (path === '/api/tray/apply') {{
    run = () => {{ if (body.seen_rev !== rev) throw fail(409, 'the tray changed', {{ tray: answer() }}); const n = server.size; server.clear(); metas.clear(); rev += 1; return {{ tray: answer(), applied: n, batch_id: 1, skipped: [] }}; }};
  }} else run = () => answer();
  const go = () => new Promise((ok, no) => {{ try {{ ok(run()); }} catch (e) {{ no(e); }} }});
  if ((path === '/api/tray/ops' && calls.length === 1 && hold) || (holdPath === path && !held)) {{ holdPath = null; return new Promise((ok, no) => {{ held = () => go().then(ok, no); }}); }}
  return go();
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


DECISION = "{ task_id: 412, op: 'done', fields: {}, label: 'done' }"


def test_a_change_staged_during_apply_waits_for_it_and_is_kept():
    """Reviewer note 1: Apply runs in the tray's queue, so a change staged while its request is in flight is sent after
    it: the Apply is not refused as if another device had changed the tray, and the change stays staged."""
    r = _run(f"""
tray.booted = true;
server.set('412', {DONE}); rev = 1; trayAnswer(answer(), null, {{ quiet: true }});
holdPath = '/api/tray/apply';
let res = null, err = null;
const p = trayApply(null, {{}}).then(x => {{ res = x; }}, e => {{ err = e.status || e.message; }});
await tick();
staged.set('413:top', {PIN}); syncTray(); await tick();
held(); await p; await settle();
console.log(JSON.stringify({{ applied: res && res.applied, err, server: [...server.keys()], staged: [...staged.keys()].map(String) }}));""")
    assert r == {"applied": 1, "err": None, "server": ["413:top"], "staged": ["413:top"]}


def test_a_decision_discarded_while_its_request_is_in_flight_stays_discarded():
    """Reviewer note 2: Discard all while a decision is on its way: the server's answer must not draw it again, and the
    rows it staged are taken out of the server's tray too."""
    r = _run(f"""
tray.booted = true;
holdPath = '/api/review/decide';
decideRows = [['412', {DECISION}, 't:412'], ['e:7', {{ task_id: 412, op: 'judge_evidence', fields: {{ evidence_id: 7, verdict: 'reviewed' }}, label: 'thread seen' }}, 't:412']];
decideOn('t:412', 'done', {{}}, {{ prov: [[412, {DECISION}]] }}); await tick();
const drawn = staged.has(412);
discardStaged(undefined);
held(); await settle();
{REPORT.replace("calls }", "calls, drawn }")}""")
    assert r["drawn"] is True and r["server"] == [] and r["staged"] == []


def test_a_refused_conversation_decision_falls_back_after_the_press():
    """Reviewer note 7: a conversation's decision the rule refuses is staged the page's way, and the redraw that shows
    it waits for the press in progress (afterPress), like every other redraw caused from outside a press."""
    r = _run(f"""
tray.booted = true;
decideFail = 400;
decideOn('t:412', null, null, {{ from_chat: {{ message_id: 3, index: 0 }}, prov: [[412, {DECISION}]], quiet: true,
  fallback: () => {{ staged.set('412:c3-0', {{ task_id: 412, op: 'set', fields: {{ due: '2026-11-30' }}, label: 'due Mon Nov 30' }}); syncTray(); render(); }} }});
await settle();
console.log(JSON.stringify({{ presses, renders, server: [...server.keys()], staged: [...staged.keys()].map(String), toasts }}));""")
    assert r["renders"] and all(r["renders"]) and r["server"] == ["412:c3-0"] and r["staged"] == ["412:c3-0"] and r["toasts"] == []
