"""The investigation-card blurb falls back to the authored narrative."""
from vivarium_workbench.lib.investigation_status import _card_description


def test_authored_description_wins():
    assert _card_description({"description": "Authored.", "executive": {"what_is_this": "x"}}) == "Authored."


def test_falls_back_to_what_is_this():
    spec = {"executive": {"what_is_this": "A recovery\nbenchmark."}}
    assert _card_description(spec) == "A recovery benchmark."  # whitespace collapsed


def test_falls_back_to_lead_then_main_claim():
    assert _card_description({"lead": "Lead first para.\n\nSecond."}) == "Lead first para."
    assert _card_description({"scientific_argument": {"main_claim": "The claim."}}) == "The claim."


def test_empty_when_nothing_authored():
    assert _card_description({}) == ""
    assert _card_description({"description": "   "}) == ""


def test_precedence_order():
    # what_is_this beats lead beats main_claim
    spec = {"executive": {"what_is_this": "WIT"}, "lead": "L", "scientific_argument": {"main_claim": "MC"}}
    assert _card_description(spec) == "WIT"
