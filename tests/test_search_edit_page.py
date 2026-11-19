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
    assert "JSON.stringify(edited ? sgBody(sgDraft(card)).create : {})" in JS      # an untouched card is created as suggested


def test_reviews_editor_is_the_one_editor_and_its_redraw_waits_for_the_press():
    assert "${editorHtml(x)}<div data-dlinks=\"${id}\">${detailAsyncHtml(id)}</div>${linkByHandHtml(x)}${moreHtml(x, true)}" in JS
    assert "loadDetail(id, undefined, id === peek)" in JS and "peek ? '?peek=1' : ''" in JS
    press = JS[JS.index("afterPress('meta:' + id, () => {"):JS.index("document.querySelectorAll(`#panel [data-edstaged=\"${id}\"]`)")]
    assert all(w in press for w in (".review .card[data-id=\"${id}\"] > h2", "[data-rvwhy=\"${id}\"]", "[data-rvsub=\"${id}\"]"))
    assert "const editsOnly = !!keys && !picked.some(([k, s]) => typeof k === 'number' || s.op === 'judge_evidence');" in JS
    assert JS.index("const pickers = {}, pickOpts = {};") < JS.index("pickers.sglink = ")      # a const used above its line stops the page at load
    assert "'why_now'" in re.search(r"const EDIT_FIELDS = \[([^\]]*)\]", JS).group(1)


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
