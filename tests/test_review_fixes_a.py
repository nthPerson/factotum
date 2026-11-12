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
