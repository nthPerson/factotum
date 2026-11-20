"""The page's Review on the pass (spoken Review 2a-2b, design 6.11): one controller over the server's View, every
press through reviewRun, decisions through decideOn, moves through passMove; what must be in the page script and its
stylesheet for that to hold (the behaviour is checked rendered, and the pass itself in tests/test_review_pass.py)."""
import re
from pathlib import Path

from factotum import api

WEB = Path(api.__file__).parent / "web"
JS = (WEB / "app.js").read_text(encoding="utf-8")
CSS = (WEB / "app.css").read_text(encoding="utf-8")


def _body(name):
    m = re.search(rf"\n  (async )?function {name}\(", JS)
    assert m, name
    i = m.start() + 1
    ends = [x for x in (JS.find("\n  function ", i + 1), JS.find("\n  async function ", i + 1)) if x > 0]
    return JS[i:min(ends) if ends else len(JS)]


def test_review_is_one_controller_over_the_pass():
    for f in ("reviewCurrent", "reviewOptions", "reviewRun", "loadPass", "passMove", "passReread", "navHtml", "listHtml",
              "stagedBannerHtml", "taskItemHtml", "cardItemHtml", "checkpointHtml", "endHtml", "finishedHtml"):
        _body(f)
    assert re.search(r"\n  let rp = \{ view: null, loading: null, seq: 0, failed: '', finished: null", JS)
    st = _body("renderReview")
    assert "reviewCurrent()" in st and "loadReview" not in JS


def test_the_old_review_and_its_closures_are_gone():
    for gone in ("data-rv-accept", "data-rv-dismiss", "data-sugg-create", "data-sugg-dismiss", "data-rv=", "data-rv-act", "data-lsug",
                 "data-follow=", "data-notabout", "function reviewAct", "function linkSuggestionAct", "mailCard", "reviewIdx",
                 "reviewPaused", "Batch applied", "review-more", "let review = ", "reviewLoading", "reviewFailed", "reviewSeq"):
        assert gone not in JS, gone
    assert "panel.querySelectorAll('[data-opt]')" in JS and "reviewRun(b.dataset.opt" in JS


def test_every_decision_and_answer_goes_through_decideOn_and_every_move_through_passMove():
    run = _body("reviewRun")
    assert "decideOn(key, " in run and "passMove(" in run and "stage(" not in run.replace("decideOn(", "")
    assert "stageJudge(" not in run and "stageChange(" not in run
    assert run.index("ui.laterOpen = false; ui.datePick = false;") < run.index("decideOn(key, opt")      # the Later menu shuts before the card moves on
    assert JS.count("'/api/review/decide'") == 1 and "'/api/review/decide'" in _body("decideOn")
    assert "passAnswer(" in _body("decideOn")                                                        # a decision's answer carries the pass
    mv = _body("passMove")
    assert "from: " in mv or "body.from = " in mv
    assert "'/api/review/pass/' + " in mv and "/move'" in mv
    assert "'/api/review/pass'" in _body("loadPass") and "'/api/review/pass'" in _body("passReread")
    for f in re.findall(r"\n  (?:async )?function (\w+)\(", JS):
        if "'/api/review/pass" in _body(f):
            assert f in ("loadPass", "passMove", "passReread", "followCheck", "passRegroup"), f      # followCheck: 2a-3; passRegroup: 2d


def test_the_keys_move_and_decide():
    keys = JS[JS.index("if (ui.tab === 'review') {", JS.index("document.addEventListener('keydown'")):]
    keys = keys[:keys.index("return; }") + 9]
    for k in ("ArrowRight", "ArrowLeft", "']'", "'['", "reviewRun("):
        assert k in keys, k
    assert "reviewAct" not in keys


def test_after_an_apply_review_reads_the_pass_and_never_pauses():
    aa = _body("applyAll")
    assert "await passReread(res.finished)" in aa and "reviewPaused" not in aa
    assert "rp.view ? rp.view.counts.left" in _body("reviewShown")


def test_the_card_stays_the_cards():
    assert ".review .card[data-id=\"${id}\"] > h2" in JS                                  # editRefresh redraws it in place (Trap 7)
    t = _body("taskItemHtml")
    assert re.search(r'<div class="card" data-id="\$\{x\.id\}"[^>]*>', t)
    head = t[t.index('<div class="card"'):t.index("<h2>")]
    assert head.count("<div") == head.count("</div>") + 1      # the h2 is a direct child of the card, never wrapped
    assert '<div class="rvbar">${rvnavHtml(v)}<div class="big">' in t      # the moves above the decisions, one element
    assert 'class="rvnav"' in _body("rvnavHtml")


def test_the_phones_bar_is_one_thing_at_the_bottom():
    phone = CSS[CSS.index("/* Review's decisions are pinned at the bottom"):]
    phone = phone[:phone.index("\n}\n")]
    assert ".review .card .rvbar { position: fixed;" in phone
    assert "body.kb-open .review .card .rvbar" in phone and "body.kb-open.kb-note .review .card .rvbar" in phone
    assert "body.kb-open.kb-note .review .card .rvbar .rvnav { display: none; }" in phone
    assert "#panel .review .card .rvbar" in _body("placeReviewBar")
    assert ".rvnav" in CSS and ".rvtop" in CSS and ".rvlist" in CSS and ".rvstaged" in CSS


def test_a_quick_second_tap_never_lands_on_a_decision_that_moved_under_it():
    """The bar stays put but its buttons change between a task (four) and a card (two): a decision tapped within
    RV_SETTLE_MS of a new item coming in front is dropped; moves and keys are not."""
    assert "const RV_SETTLE_MS = 400;" in JS
    assert "if (b.closest('.big, .rvrec') && Date.now() - rp.drawnAt < RV_SETTLE_MS) return; reviewRun(b.dataset.opt);" in JS      # the suggested answer too (2c)
    assert "rp.drawnAt = Date.now();" in JS


def test_a_page_on_review_follows_the_pass_and_reads_it_again_on_return():
    """Spoken Review 2a-3 (design 6.5, 6.11): every 3 s, only while Review is in front, the document visible, no task page
    open, nothing typed and no press waiting on an answer, the page asks GET /api/review/pass/now and, when the pass, its
    revision or the tray's differ from what is drawn, reads the pass (never a begin) and the tray and redraws after the
    press in progress. A return to the app on Review reads them again unless something is typed."""
    assert "const FOLLOW_MS = 3000;" in JS
    fp, tick, chk = _body("followPass"), _body("followTick"), _body("followCheck")
    assert "setTimeout(followTick, FOLLOW_MS)" in fp and "clearTimeout(rp.follow)" in _body("stopFollow")
    for cond in ("ui.tab !== 'review'", "document.hidden", "ui.page !== null", "typingNow()", "rp.inflight", "rp.loading"):
        assert cond in tick, cond
    assert "'/api/review/pass/now'" in chk and "api('/api/review/pass')" in chk and "method: 'POST'" not in chk
    assert "n.pass_id !== v.id || n.rev !== v.rev || n.tray_rev !== v.tray_rev" in chk
    assert "trayPoll(" in chk and "afterPress('review'," in chk
    assert "toast('Moved ' + movedWhere(who))" in chk and "movedWhere(r.by)" in _body("passMove")      # "Moved on iPhone", never "page · iPhone"
    assert "if (rp.view) reviewCount = rp.view.counts.left;" in _body("reviewShown")      # the tab keeps the pass's number when the view is let go
    assert "followPass();" in _body("renderReview") and "stopFollow();" in _body("render")
    ret = _body("onReturn")
    assert "followCheck(" in ret and ret.index("if (typingNow())") < ret.index("followCheck(")      # nothing typed: read the pass again

def test_review_cap_is_items_per_sitting_in_settings():
    """A pass has no pages: `lifecycle.review_cap` is now where Review stops and asks (design 6.3, 6.12)."""
    assert "f('lifecycle.review_cap', 'Items per sitting', 'Review stops and asks before going on after this many.', num('lifecycle.review_cap', 5, 50))" in JS


def test_a_note_belongs_to_its_item_and_counts_as_typed_only_in_front():
    """Review finding 2026-11-20: a note typed on one item and left with Next stayed the page's one note, so typingNow kept
    the page from following another device and from reloading while other items were in front, and the next decision on
    any item cleared it. Notes are held per item; only the note of the item in front, with its box drawn, counts; a
    decision takes only its own item's note."""
    import json
    import shutil
    import subprocess
    line = re.search(r"\n  const rvNoteNow = .*\n", JS).group(0)
    assert "rvNoteNow()" in _body("typingNow") and "ui.rvNote." not in JS and "ui.rvNote =" not in JS
    run = _body("reviewRun")
    assert run.count("delete ui.rvNotes[key]") == 2 and "ui.rvNotes = " not in run
    assert "const text = (ui.rvNotes[key] || '').trim();" in run
    assert "const note = ui.rvNotes[entry.key] || '';" in _body("taskItemHtml")
    node = shutil.which("node")
    if node is None:
        return
    script = f"""
const ui = {{ rvNotes: {{ 't:477': 'ask Sam first' }} }}; let rp = {{ view: {{ cursor_key: 't:424' }} }}; let box = true;
const drawn = () => box;
{line}
const out = [rvNoteNow()];
rp.view.cursor_key = 't:477'; out.push(rvNoteNow());
box = false; out.push(rvNoteNow());
rp.view = null; box = true; out.push(rvNoteNow());
console.log(JSON.stringify(out));"""
    r = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == ["", "ask Sam first", "", ""]


def test_following_notices_a_new_day_and_an_applied_row_offers_no_take_back():
    """Review notes 2026-11-20: a page following across midnight compared only the pass, its revision and the tray's,
    so it kept yesterday's pass; and after a partial Apply the end screen offered Take back on a decision already
    applied, which no tray row holds any more."""
    chk = _body("followCheck")
    assert "n.today !== v.today" in chk and "newDay" in chk
    assert "r.state === 'decided' && !r.applied" in _body("stagedRowHtml")


def test_apply_waits_for_a_decision_still_on_its_way():
    """Review note 2026-11-20: a Keep drawn at the click is a local row until its answer comes; Apply pressed meanwhile
    left it out, and did nothing at all when it was the only row."""
    aa = _body("applyAll")
    assert aa.index("s.local && s.deciding)) await tray.busy") < aa.index("const picked =")


def test_the_suggested_answer_is_one_option_through_the_rule():
    """Spoken Review 2c (design 6.8, 6.11): the card draws the recommendation as one wide button with its basis under it
    (recHtml), the decision it is wears a ring in the bar, the key a presses it, and it goes to the server as the option
    `rec` through decideOn, never as steps staged by the page; Settings has the switch."""
    assert "${recHtml(entry)}" in _body("taskItemHtml") and "${recHtml(entry)}" in _body("cardItemHtml")
    assert "recRing(entry, o.id)" in _body("taskItemHtml") and "recRing(entry, 'dismiss')" in _body("cardItemHtml")
    rh = _body("recHtml")
    assert 'data-opt="rec"' in rh and "esc(r.label)" in rh and "esc(r.basis)" in rh and "entry.rec_held" in rh
    assert "if (id === 'rec') return recRun(cur);" in _body("reviewRun")
    rr = _body("recRun")
    assert "decideOn(key, 'rec', args, { prov })" in rr and "stage(" not in rr.replace("decideOn(", "")
    keys = JS[JS.index("if (ui.tab === 'review') {", JS.index("document.addEventListener('keydown'")):]
    keys = keys[:keys.index("return; }") + 9]
    assert "e.key === 'a'" in keys and "reviewRun('rec')" in keys
    assert "f('review.recommend', 'Suggest an answer'," in JS and "bool('review.recommend')" in JS
    assert ".card .rvrec" in CSS and ".card .big button.recd" in CSS


def test_rec_draws_its_steps_at_the_click_and_takes_the_note_only_when_a_step_does():
    import json
    import shutil
    import subprocess
    node = shutil.which("node")
    if node is None:
        return
    assert "const REC_NOTE_TAKERS = " in _body("recRun")          # cut out with it
    script = f"""
const ui = {{ rvNotes: {{}} }}; const calls = []; let ahead = 0;
const decisionProv = (id, k) => ({{ task_id: id, op: k, label: k }});
const cardProv = (key, o) => [['e:' + key.slice(2), {{ op: 'judge_evidence', label: 'suggestion dismissed' }}]];
const passAhead = () => {{ ahead++; }};
const decideOn = (key, option, args, opts) => {{ calls.push([key, option, args, opts.prov.map(p => [p[0], p[1] && p[1].op])]); }};
{_body("recRun")}
const ev = [{{ id: 31, change: {{ op: 'follow_thread' }} }}, {{ id: 40, change: {{ op: 'set' }} }}, {{ id: 41, change: {{ op: 'done', fields: {{ outcome: 'x' }} }} }}];
const task = (steps, answer, state) => ({{ kind: 'task', entry: {{ key: 't:7', state, item: {{ task: {{ id: 7 }}, evidence: ev }}, recommendation: {{ steps, answer }} }} }});
ui.rvNotes['t:7'] = 'ask Sam';
recRun(task(['link:9'], 'link', 'waiting'));
recRun(task(['follow:31', 'accept:40', 'keep'], 'accept_keep', 'decided'));
recRun(task(['link:9'], 'link', 'decided'));
recRun(task(['accept:41'], 'done', 'waiting'));
recRun({{ kind: 'card', entry: {{ key: 's:50', state: 'waiting', item: {{ id: 50 }}, recommendation: {{ steps: ['dismiss'], answer: 'dismiss' }} }} }});
console.log(JSON.stringify({{ calls, ahead, notes: ui.rvNotes }}));"""
    r = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    c = out["calls"]
    assert c[0] == ["t:7", "rec", {}, [["e:9", "judge_evidence"]]]                                   # a link alone takes no note: it stays in the box
    assert c[1] == ["t:7", "rec", {"note": "ask Sam"}, [["e:31", "judge_evidence"], ["e:40", "judge_evidence"], [7, "keep"]]]
    assert c[2][2] == {} and c[3][3] == [[7, "done"]] and c[4] == ["s:50", "rec", {}, [["e:50", "judge_evidence"]]]
    assert out["ahead"] == 4                                                                          # not for a link alone on an item already settled
    assert out["notes"] == {}


def test_a_group_is_one_card_with_its_tasks_keep_all_and_one_by_one():
    """Spoken Review 2d (design 6.9, 6.11): a group of alike new tasks is drawn by groupItemHtml (where they came from,
    one row per task with Done, Drop, Later and Open, a task decided by itself with Undo), with Keep all and One by one in
    the bar; k is Keep all on a group; Keep all and a task's decision go through decideOn, Open and One by one through
    passRegroup (POST …/group), queued behind the presses before them; Settings has the switch."""
    assert "c.kind === 'group' ? groupItemHtml(c.entry, c.pass)" in _body("renderReview")
    g = _body("groupItemHtml")
    assert 'data-opt="keep_all"' in g and 'data-opt="one_by_one"' in g and '<div class="rvbar">${rvnavHtml(v)}<div class="big ' in g
    assert "recHtml(entry)" in g and "recRing(entry, 'keep_all')" in g and "rv-note" not in g      # no note box on a group
    row = _body("groupRowHtml")
    for w in ("done", "drop", "later", "open"):
        assert f"b('{w}'" in row, w
    assert 'data-opt="takeback:${esc(m.key)}"' in row and "staged.get(id)" in row      # drawn at the click from the tray
    pr = _body("passRegroup")
    assert "'/api/review/pass/' + v.id + '/group'" in pr and "tray.busy = tray.busy.then(run, run)" in pr and "passAnswer(" in pr
    assert "if (cur.kind === 'group') return groupRun(id, cur);" in _body("reviewRun")
    gr = _body("groupRun")
    assert "decideOn(gk, id, {}, { prov:" in gr and "passRegroup(gk, 'take_out', tid); return passMove('goto', mk);" in gr
    assert "passRegroup(gk, 'one_by_one')" in gr
    keys = JS[JS.index("if (ui.tab === 'review') {", JS.index("document.addEventListener('keydown'")):]
    keys = keys[:keys.index("return; }") + 9]
    assert "c.kind === 'group'" in keys and "reviewRun('keep_all')" in keys
    assert "f('review.group_alike', 'Group alike new tasks'," in JS and "bool('review.group_alike')" in JS
    assert "r.members" in _body("listHtml") and "r.kind !== 'group'" in _body("stagedRowHtml")
    assert ".card .gmembers" in CSS and ".review .rvrow .rvmems" in CSS


def test_group_presses_draw_at_the_click_and_send_one_option_each():
    import json
    import shutil
    import subprocess
    node = shutil.which("node")
    if node is None:
        return
    script = f"""
const ui = {{ rvMemberLater: null }}; const calls = []; let ahead = 0, renders = 0; const staged = new Map();
const decisionProv = (id, k) => ({{ task_id: id, op: k, label: k }});
const passAhead = () => {{ ahead++; }};
const render = () => {{ renders++; }};
const decideOn = (key, option, args, opts) => {{ calls.push(['decide', key, option, args, opts.prov.map(p => [p[0], p[1].op])]); }};
const passRegroup = (key, op, tid) => {{ calls.push(['regroup', key, op, tid === undefined ? null : tid]); }};
const passMove = (to, key) => {{ calls.push(['move', to, key]); }};
{_body("groupRun")}
const m = (id, state) => ({{ key: 't:' + id, state, task: {{ id }} }});
const cur = ms => ({{ kind: 'group', entry: {{ key: 'g:rec:x', recommendation: {{ steps: ['keep_all'] }}, item: {{ members: ms }} }} }});
staged.set(2, {{ op: 'done' }});
groupRun('keep_all', cur([m(1, 'waiting'), m(2, 'waiting'), m(3, 'passed'), m(4, 'decided'), m(5, 'gone')]));
groupRun('member:t:1:later', cur([m(1, 'waiting'), m(3, 'waiting')]));
const opened = ui.rvMemberLater;
groupRun('member:t:1:until:fri', cur([m(1, 'waiting'), m(3, 'waiting')]));
groupRun('member:t:3:drop', cur([m(3, 'waiting')]));
groupRun('member:t:3:open', cur([m(1, 'waiting'), m(3, 'waiting')]));
groupRun('one_by_one', cur([m(1, 'waiting'), m(3, 'waiting')]));
groupRun('member:t:3:explode', cur([m(3, 'waiting')]));
groupRun('done', cur([m(3, 'waiting')]));
console.log(JSON.stringify({{ calls, ahead, opened, later: ui.rvMemberLater }}));"""
    r = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["calls"] == [
        ["decide", "g:rec:x", "keep_all", {}, [[1, "keep"], [3, "keep"]]],          # the undecided ones not already staged: one row each
        ["decide", "t:1", "later", {"until": "fri"}, [[1, "fri"]]],
        ["decide", "t:3", "drop", {}, [[3, "drop"]]],
        ["regroup", "g:rec:x", "take_out", 3], ["move", "goto", "t:3"],
        ["regroup", "g:rec:x", "one_by_one", None]]
    assert out["ahead"] == 2 and out["opened"] == "t:1" and out["later"] is None      # Keep all, and the last task decided


def test_show_how_it_would_be_read_draws_the_servers_words_under_every_card():
    """2e: a link at the foot of a task, a card, a group and a settled item; on, the server's say, basis and question in grey
    (escaped, nothing of the page's own); per browser in localStorage['factotum.rvsay'], read and written inside try."""
    import json
    import shutil
    import subprocess
    sh = _body("sayHtml")
    assert "entry.spoken" in sh and "data-opt=\"say\"" in sh and "esc(s.say" in sh
    for f in ("taskItemHtml", "cardItemHtml", "groupItemHtml", "settledHtml"):
        assert "sayHtml(entry)" in _body(f), f
    assert "try { ui.rvSay = localStorage.getItem('factotum.rvsay') === '1'; } catch (_) {}" in JS
    run = _body("reviewRun")
    assert "if (id === 'say') { ui.rvSay = !ui.rvSay; try { localStorage.setItem('factotum.rvsay'" in run
    assert run.index("id === 'say'") < run.index("cur.entry.settled")          # also on a settled item
    assert "{ id: 'say', kind: 'page' }" in _body("reviewOptions")
    assert ".card .rvsay-text" in CSS
    node = shutil.which("node")
    if node is None:
        return
    script = f"""
const ui = {{ rvSay: false }};
const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
{sh}
const e = {{ spoken: {{ say: 'New: <b>Send</b> it.', basis: 'It is new.', question: 'Keep it?' }} }};
const off = sayHtml(e); ui.rvSay = true; const on = sayHtml(e);
const none = sayHtml({{ key: 't:1' }}); const nobasis = sayHtml({{ spoken: {{ say: 'Fading.', basis: '', question: '' }} }});
console.log(JSON.stringify({{ off, on, none, nobasis }}));"""
    r = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert "Show how it would be read" in out["off"] and "rvsay-text" not in out["off"]
    assert "Hide how it would be read" in out["on"] and "New: &lt;b&gt;Send&lt;/b&gt; it." in out["on"] and "It is new." in out["on"] and "Keep it?" in out["on"]
    assert out["none"] == ""
    assert out["nobasis"].count("<p") == 1
