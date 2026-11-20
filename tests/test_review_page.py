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
            assert f in ("loadPass", "passMove", "passReread", "followCheck"), f      # followCheck: 2a-3


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
    assert "if (b.closest('.big') && Date.now() - rp.drawnAt < RV_SETTLE_MS) return; reviewRun(b.dataset.opt);" in JS
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
