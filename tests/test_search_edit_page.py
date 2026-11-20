"""The page's side of the Search tab and of editing on Review: what must be there for it to work at all."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from factotum import api, chat, search
from stage4_helpers import mk

WEB = Path(api.__file__).parent / "web"
JS = (WEB / "app.js").read_text(encoding="utf-8")
CSS = (WEB / "app.css").read_text(encoding="utf-8")


def test_search_is_a_tab_with_an_address_between_review_and_chat(cfg, tmp_path):
    assert "'review', 'search', 'chat'" in JS and api.PAGE_TABS.index("search") == api.PAGE_TABS.index("review") + 1
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert html.index('data-tab="review"') < html.index('data-tab="search"') < html.index('data-tab="chat"')
    assert "search: renderSearch" in JS and "wireSearch(panel);" in JS
    c = TestClient(api.create_app(cfg, db_path=str(tmp_path / "t.db")))
    assert c.get("/search").status_code == 200 and "factotum-build" in c.get("/search").text


def test_the_pages_search_filters_are_the_engines(cfg):
    blank = re.search(r"const SEARCH_BLANK = \{([^}]*)\}", JS).group(1)
    keys = set(re.findall(r"(\w+):", blank))
    assert keys == set(search.normalise({})) - {"limit", "offset"}
    for name, known in (("SEARCH_STATUS", search.STATUSES), ("SEARCH_FLAGS", search.FLAGS), ("SEARCH_DATES", search.DATE_FIELDS), ("SEARCH_SORTS", search.SORTS), ("SEARCH_WITHIN", search.WITHIN)):
        line = re.search(rf"const {name} = (\[\[.*?\]\]);", JS).group(1)
        assert re.findall(r"\['(\w+)',", line) == list(known), name
    assert f"const NO_VALUE = '{search.NONE}';" in JS


def test_every_new_text_box_is_a_draft_box_and_has_a_draft():
    boxes = re.search(r"const DRAFT_BOXES = \[([^\]]*)\]", JS).group(1)
    assert all(f"'{b}'" in boxes for b in ("search-q", "search-ask", "sg-ask", "pick-sglink"))
    assert "ed-(name|context|who|why)|sg-(name|context|who)" in JS
    assert "searchQ: '', askSearch: ''" in JS and "sg: {}, askSg: ''" in JS
    assert "draft.askSearch, draft.askSg" in JS                      # a question half typed keeps the lists from reloading under it


def test_the_places_the_page_sends_are_the_servers():
    assert "search: { closed: 'factotum.searchClosed', draft: 'askSearch' }" in JS
    assert "placeOf('search', null)" in JS and "placeOf('suggestion', id)" in JS and {"search", "suggestion"} <= set(chat.ORIGINS)
    assert "body.search = Object.assign({}, sq)" in JS and "body.card_id = pl.card_id" in JS and "'&card_id=' + pl.card_id" in JS
    assert "t.origin === 'search' ? 'Search'" in JS                  # the Chat tab names where a thread began
    assert "'reopen'" in re.search(r"const PLACE_OPS = new Set\(\[([^\]]*)\]", JS).group(1) and "reopen" in chat.CHAT_OPS


def test_a_cards_revised_draft_is_never_staged_or_created_from_its_conversation():
    assert "const cardDraft = (pl, p) => pl.origin === 'suggestion' && p.op === 'create';" in JS
    assert "placeOpen(p, mid, i) && !cardDraft(pl, p) && !pl.off.has" in JS and "data-sg-use=" in JS
    row = JS[JS.index("if (cardDraft(pl, p)) return"):JS.index("return `<label class=\"pprop\">")]
    assert "data-place-create" not in row and "data-pprop" not in row
    assert "decideOn('s:' + id, 'create', edited ? { draft: sgBody(sgDraft(card)).create } : {})" in JS      # an untouched card is created as suggested


def test_reviews_editor_is_the_one_editor_and_its_redraw_waits_for_the_press():
    assert "${editorHtml(x)}<div data-dlinks=\"${id}\">${detailAsyncHtml(id)}</div>${linkByHandHtml(x)}${moreHtml(x, true)}" in JS
    assert "loadDetail(id, undefined, id === peek)" in JS and "peek ? '?peek=1' : ''" in JS
    press = JS[JS.index("afterPress('meta:' + id, () => {"):JS.index("document.querySelectorAll(`#panel [data-edstaged=\"${id}\"]`)")]
    assert all(w in press for w in (".review .card[data-id=\"${id}\"] > h2", "[data-rvwhy=\"${id}\"]", "[data-rvsub=\"${id}\"]"))
    assert "if (ui.tab === 'review') { await passReread(res.finished); render(); }" in JS      # the server says whether an Apply was a review (tray.finishes); the pass is read again, never begun
    assert "editsOnly" not in JS
    assert JS.index("const pickers = {}, pickOpts = {};") < JS.index("pickers.sglink = ")      # a const used above its line stops the page at load
    assert "'why_now'" in re.search(r"const EDIT_FIELDS = \[([^\]]*)\]", JS).group(1)


def _body(name):
    """The text of `function <name>` up to the next top-level function of the page script."""
    i = JS.index(f"function {name}(")
    j = JS.find("\n  function ", i + 1)
    k = JS.find("\n  async function ", i + 1)
    ends = [x for x in (j, k) if x > 0]
    return JS[i:min(ends) if ends else len(JS)]


def test_the_tray_is_the_servers():
    """The page's staged Map mirrors the server's tray (spoken Review stage 1, design §5.6): nothing writes the old
    local tray, only the carry reads it, the ops route is called from one place, and Pin and Long-term stand beside."""
    assert "setItem('factotum.staged'," not in JS                      # the old local tray is never written again
    for call in ("getItem('factotum.staged')", "removeItem('factotum.staged')"):    # read and let go once, by the carry alone
        assert JS.count(call) == 1 and call in _body("carryOldTray"), call
    assert JS.count("'/api/tray/ops'") == 1 and "'/api/tray/ops'" in _body("sendTray")
    assert JS.count("'/api/tray/apply'") == 1 and "'/api/tray/apply'" in _body("trayApply")
    assert "await trayApply(" in _body("applyAll") and "'/api/batches/apply'" not in _body("applyAll")
    assert "'/api/review/finish'" not in _body("applyAll")
    act = _body("act")
    assert "id + ':top'" in act and "id + ':lt'" in act
    assert "stageExtra(id + ':top', id, 'set', { top_rank: i + 1 }" in _body("reorderTop")
    assert 'min="${isoPlus(1)}"' in JS
    for f in ("persistStaged", "stage", "stageExtra", "stageReminder"):
        assert "syncTray()" in _body(f), f
    assert "e.body = j" in JS                                         # a 409 hands its tray to the page
    rv = _body("reviewRun")
    assert rv.index("ui.laterOpen = false; ui.datePick = false;") < rv.index("decideOn(key, opt")      # the Later menu is shut before the card moves on


def test_review_decisions_go_through_the_rule():
    """decideOn is the one page function that calls POST /api/review/decide (spoken Review stage 1, design §5.6): a
    Review card conversation's decision about its own task, and Create and Dismiss on a suggestion card, go through
    it; a staged card is drawn with Take back, and the number beside Review leaves it out."""
    assert JS.count("'/api/review/decide'") == 1 and "'/api/review/decide'" in _body("decideOn")
    assert "decideOn('t:' + pl.task_id" in _body("stagePlace")
    assert "decideOn('s:' + id, 'dismiss')" in JS
    assert "decideOn('s:' + id, 'undecide')" in JS and "function sgStagedHtml(card, entry)" in JS
    assert "`/api/review/suggestions/${id}/create`" not in JS          # the old route stays for pages loaded before the deploy
    assert "rp.view.counts.left" in _body("reviewShown")      # the pass's number: undecided_count, which leaves the tray's decisions out
    assert "res.cards" in _body("applyAll") and "cardMade.set(res.batch_id" in _body("applyAll")


def test_every_review_press_goes_through_the_rule():
    """Spoken Review 2a-2 (design 6.11): the four decisions, Pin and Long-term, Accept and Dismiss on a proposal, Follow
    and Not about on a mail, and every answer to a suggested link are option ids sent by reviewRun through decideOn (the
    rule, review_decide.plan), never an op staged by the page; a conversation's Pin or Long-term on a task already so is
    not staged again; a refused one is staged the page's way after the press; one discarded on its way stays discarded."""
    run = _body("reviewRun")
    assert "decideOn(key, opt" in run and "decideOn(key, id" in run and "stageJudge(" not in run and "stageChange(" not in run
    for opt in ("data-opt=\"accept:${e.id}\"", "data-opt=\"dismiss:${e.id}\"", "data-opt=\"follow:${e.id}\"", "data-opt=\"not_about:${e.id}\"",
                "data-opt=\"${act === 'not' ? 'not_related' : act}:${s.evidence_id}\""):
        assert opt in JS, opt
    assert "propNoop(p)" in _body("stagePlace") and "!propNoop(p)" in JS[JS.index("const placeOpen = "):JS.index("const cardDraft = ")]
    assert "afterPress('tray', () => { o.fallback(); render(); })" in _body("decideOn")      # a refused one is staged after the press
    assert "takenBack" in _body("decideOn") and "takenBack" in _body("discardStaged")


def test_peek_reads_a_task_without_marking_its_neighbours_seen(cfg, tmp_path):
    app = api.create_app(cfg, db_path=str(tmp_path / "t.db"))
    mk(app.state.conn, 5)
    c = TestClient(app)
    seen = lambda: app.state.conn.execute("SELECT neighbours_seen_at FROM tasks WHERE id = 5").fetchone()[0]      # noqa: E731
    t = c.get("/api/tasks/5", params={"peek": 1}).json()
    assert t["id"] == 5 and "history" in t and "relations" in t and seen() is None
    c.get("/api/tasks/5")
    assert seen() is not None


def test_the_new_parts_are_styled_and_fit_a_phone():
    for sel in (".search .sbar", ".sfilters", ".schip.on", ".sres .hit", ".snip", "mark {", ".card .rvedit", ".card .sgform", ".card .rvedit-bar"):
        assert sel in CSS, sel
    phone = CSS[CSS.index("/* ---------- Search (2026-11-18)"):]
    assert "@media (max-width: 700px)" in phone and ".search .seg button { min-height: 44px; }" in phone
