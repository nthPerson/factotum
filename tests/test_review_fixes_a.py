"""The security review's scope-A findings (2026-11-11): what a ripple's rewrite may lose or add on its
own (the guard), and where a connector may set an outcome."""
import pytest

from conftest import TODAY
from stage4_helpers import link, mk
from stage5_helpers import entry, no_claude  # noqa: F401  (no_claude is autouse: no test reaches a real run)

from factotum import batches, ripple
from factotum.db import get_task


# --- 1. a rewrite never silently drops an address the old text held, scheme or not -------------------

REVIEWER_OLD = "Forms are at www.grad.sdsu-example.edu/forms and the portal lab.example.edu/upload; ask Dana."


def test_the_reviewers_rewrite_that_drops_two_bare_addresses_is_refused():
    new = "The chair signed; only the upload is left. Ask Dana."
    assert ripple._loss(REVIEWER_OLD, new) == "drops www.grad.sdsu-example.edu/forms"
    assert ripple.guard(REVIEWER_OLD, new) == "drops www.grad.sdsu-example.edu/forms"
    kept_one = "The chair signed; only the upload is left, at www.grad.sdsu-example.edu/forms. Ask Dana."
    assert ripple.guard(REVIEWER_OLD, kept_one) == "drops lab.example.edu/upload"


@pytest.mark.parametrize("old, new, lost", [
    ("Forms: www.example.edu/forms. Ask Dana.", "Ask Dana.", "www.example.edu/forms"),               # www. form
    ("Upload to lab.example.edu/upload by Friday.", "Upload by Friday.", "lab.example.edu/upload"),   # bare host.tld/path
    ("Send it from lab.example.edu/forms/42 to the chair.", "Send it to the chair.", "lab.example.edu/forms/42"),
    ("See grad.example.edu for the rules.", "See the rules.", "grad.example.edu"),                    # bare host, common TLD
    ("Mail dana@example.edu the draft.", "Mail Dana the draft.", "dana@example.edu"),                  # an email address
    ("Upload to lab.example.edu/upload by Friday.", "Upload to lab.example.edu/uploads by Friday.", "lab.example.edu/upload"),  # a different path
])
def test_a_rewrite_that_drops_an_address_without_a_scheme_is_refused(old, new, lost):
    assert ripple._loss(old, new) == f"drops {lost}"
    assert ripple.guard(old, new) == f"drops {lost}"


@pytest.mark.parametrize("old, new", [
    (REVIEWER_OLD, "Ask Dana: the chair signed. Forms are at www.grad.sdsu-example.edu/forms; the portal is lab.example.edu/upload."),
    ("Upload to lab.example.edu/upload by Friday.", "The chair signed, so upload to lab.example.edu/upload. by Friday"),   # a full stop after it
    ("Upload to lab.example.edu/upload, then tell Dana.", "Tell Dana once it is at lab.example.edu/upload/."),             # a trailing slash
    ("Upload to lab.example.edu/upload by Friday.", "Upload (https://lab.example.edu/upload) by Friday."),                  # a scheme and a bracket
    ("Upload to https://lab.example.edu/upload by Friday.", "Upload to lab.example.edu/upload by Friday; the chair signed."),  # the scheme dropped
    ("Mail Dana@Example.edu the draft.", "The chair signed; mail dana@example.edu the draft."),                              # an email's case
    ("Mail dana@example.edu the draft.", "The chair signed; mail dana@example.edu the draft."),
])
def test_a_kept_address_with_its_sentence_reworded_is_applied(old, new):
    assert ripple._loss(old, new) is None and ripple.guard(old, new) is None


def test_a_text_with_no_address_still_rewrites_freely():
    old = "Ask the lab for the dataset, e.g. the 3.5 GB one. Dr. Lee said Nov. 12 at 9.30 a.m."
    assert ripple.guard(old, "Dr. Lee confirmed: ask the lab for the 3.5 GB dataset by Nov. 12.") is None


def _done_with_one_waiting(conn, cfg, context):
    mk(conn, 1, name="Ask the chair"); mk(conn, 2, context=context)
    link(conn, 2, 1, "waits_on")
    batches.apply(conn, [{"op": "done", "task_id": 1, "fields": {"outcome": "The chair signed"}}], "ui", TODAY)
    return ripple.bundle(conn, cfg, [1], TODAY)


def test_the_reviewers_case_becomes_a_dated_note_and_the_text_keeps_both_addresses(conn, cfg):
    b = _done_with_one_waiting(conn, cfg, REVIEWER_OLD)
    out = ripple.apply(conn, cfg, b, ripple.parse({"ripples": [entry(2, context="The chair signed; only the upload is left. Ask Dana.",
                                                                     why="the chair signed")]}, b), 1, TODAY)
    assert out["rewritten"] == [] and out["noted"] == [{"task_id": 2, "why": "drops www.grad.sdsu-example.edu/forms"}]
    assert get_task(conn, 2)["context"] == REVIEWER_OLD + "\n2026-11-01: Ripple from Task-1: the chair signed"


# --- 2. a rewrite that adds anything address-shaped waits on Review --------------------------------

OLD = "Ask the lab for the dataset. Confirm the format with Dana."

FLAGGED = [
    "https://evil.example/x",                # a scheme (worked before)
    "http://185.12.4.9:8080/x",
    "hxxps://evil[.]com/x",                  # de-fanged scheme and dot
    "www.evil.example",                      # www. (worked before)
    "evil.com",                              # a common TLD (worked before)
    "Evil.COM",
    "x.com",
    "bit.ly/x3",
    "evil.example/pay",                      # any TLD with a path (worked before)
    "data-portal.top",                       # the reviewer's five: a real TLD outside the old list, no path
    "labdata.zip",                           #   .zip is a real TLD
    "lab-dаta.com/upload",              #   a Cyrillic а in the host
    "185.12.4.9/upload",                     #   an IPv4 address without a scheme
    "labdata[.]com/upload",                  #   de-fanged
    "185.12.4.9",                            # an IPv4 address alone
    "labdata(.)com",
    "labdata[dot]com",
    "labdata dot com",
    "evil。com",                         # an ideographic full stop, which IDNA reads as a dot
    "evil.example:8443",                     # a port
    "пример.рф",   # пример.рф: an IDN
    "lаb.example",                      # a look-alike host, unknown TLD, no path
    "sub.domain.co.uk/path",
    "labdata.xyz",
    "files.evilcorp.io",
    "upload.lab-data.net/x?y=1",
    "_evil.com",                             # an underscore before the host
    "e.v.com",                               # single-letter labels before a long TLD
    "163.com",                               # a host of digits with a known TLD
    "labdata dot top/upload",                # a spaced de-fanged dot with a path
    "eve@evil.example",                      # an email address (worked before)
]

NOT_FLAGGED = [
    "e.g. the slides", "i.e. the draft", "about 3.5 pages", "v2.1 of the form", "v2.1.3", "by Nov. 12", "at 9 a.m.",
    "at 3 p.m.", "Dr. Lee agreed", "after Task-12.", "it costs $3.50", "at 9.30", "J.R.R. Tolkien", "attach report.pdf",
    "the Node.js notes", "the Node.js/React app", "the U.S. visa", "the Ph.D. defense", "rename file.name", "update README.md",
    "run setup.py", "set git user.email", "send the résumé.pdf", "pp. 12-14", "St. Louis", "Python 3.12", "1,234.56 total",
    "Fig. 2.3", "Smith et al.", "Smith et.al.", "ch.3/4", "a 2.5x speedup", "at 10.30am", "on 11.12.2026", "No.5", "Mr.Smith",
    "fix row.id", "m.group(1)", "call requests.post", "use date.today()", "wait...so", "Q&A at 2.30 p.m.", "put a dot in the box",
    "connect the dot to the line",
]


@pytest.mark.parametrize("added", FLAGGED)
def test_a_rewrite_that_adds_an_address_shaped_thing_is_never_applied_on_its_own(added):
    assert ripple.guard(OLD, OLD + " " + added) is not None


@pytest.mark.parametrize("added", NOT_FLAGGED)
def test_an_ordinary_rewrite_is_applied_on_its_own(added):
    assert ripple.guard(OLD, OLD + " " + added) is None
    assert ripple._no_new_addresses(added, OLD) == added          # History's line keeps it too


@pytest.mark.parametrize("added", FLAGGED[:-1])
def test_an_address_shaped_thing_the_old_text_held_may_stay_and_may_not_go(added):
    old = OLD + " " + added
    assert ripple.guard(old, "Dana confirmed. " + old) is None
    assert ripple._loss(old, "Dana confirmed the format.").startswith("drops ")


def test_a_note_never_carries_a_new_de_fanged_or_look_alike_address():
    why = "upload to labdata[.]com/upload or 185.12.4.9 or lab-dаta.com/upload."
    assert ripple._no_new_addresses(why, OLD) == "upload to [address left out] or [address left out] or [address left out]."


# --- the automated review of the first fix: an "@" beside an address hid it; a path's case was folded ----

@pytest.mark.parametrize("added", ["@https://evil.example/x", "see @evil.com/login", "evil.com@", "a@b.co evil.com/x", "@www.evil.example"])
def test_an_at_sign_beside_an_address_does_not_hide_it(added):
    assert ripple.guard(OLD, OLD + " " + added) is not None
    old = OLD + " " + added
    assert ripple._loss(old, OLD).startswith("drops ")                # and one the old text held is protected


def test_a_second_address_beside_an_email_is_its_own_finding():
    assert ripple._new_addresses(OLD, OLD + " a@b.co evil.com/x") == (["a@b.co"], ["evil.com/x"])
    old = "Mail a@b.co; the form is at evil.com/x."
    assert ripple._loss(old, "Mail a@b.co.") == "drops evil.com/x"
    assert ripple._loss(old, "The form is at evil.com/x.") == "drops a@b.co"


@pytest.mark.parametrize("text", ["mailto:dana@lab.example.edu", "dana@lab.example.edu", "first.last@lab.example.it"])
def test_an_email_address_is_one_email_and_not_also_a_web_address(text):
    assert ripple._new_addresses(OLD, OLD + " " + text) == ([text.removeprefix("mailto:").lower()], [])
    assert ripple._web_in(text) == []
    old = OLD + " " + text
    assert ripple._loss(old, OLD) == f"drops {text.removeprefix('mailto:')}"
    assert ripple.guard(old, "Dana confirmed. " + old) is None


@pytest.mark.parametrize("old, new", [
    ("Share: https://lab.example.edu/Share/AbC123", "Share: https://lab.example.edu/share/abc123"),
    ("Forms at lab.example.edu/Forms/A7.", "Forms at lab.example.edu/forms/a7."),
    ("See evil.example/abc?Token=XyZ#Top", "See evil.example/abc?token=xyz#top"),
])
def test_a_rewrite_that_changes_the_case_of_a_path_is_a_drop_and_an_addition(old, new):
    assert ripple._loss(old, new) is not None and ripple._loss(old, new).startswith("drops ")
    _, web = ripple._new_addresses(old, new)
    assert web                                                    # for the card rule it adds an address


def test_the_scheme_and_the_host_are_compared_without_case():
    old = "Forms: HTTPS://Lab.Example.EDU/Forms"
    assert ripple.guard(old, "The chair signed. Forms: https://lab.example.edu/Forms") is None
    assert ripple.guard(old, "The chair signed. Forms: lab.example.edu/Forms/.") is None
    assert ripple._loss(old, "The chair signed. Forms: https://lab.example.edu/forms") == "drops HTTPS://Lab.Example.EDU/Forms"


@pytest.mark.parametrize("old, new", [
    ("Forms: www.lab.example.edu/a", "Forms: lab.example.edu/a"),          # www. is a different host
    ("Forms: lab.example.edu/a%20b", "Forms: lab.example.edu/a b"),         # a percent-escape is compared as written
    ("Forms: lab.example.edu:443/a", "Forms: lab.example.edu/a"),           # so is a default port
    ("Forms: lab.example.edu/a", "Forms: lab​.example.edu/a"),         # an invisible character is another address
    ("Forms: labdata[.]com/upload", "Forms: labdata.com/upload"),           # re-fanged: now a live link
])
def test_what_looks_alike_but_is_written_differently_is_another_address(old, new):
    assert ripple._loss(old, new) is not None and ripple._new_addresses(old, new)[1]
