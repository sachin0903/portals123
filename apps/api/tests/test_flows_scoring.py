"""A scored form's band is computed here, and only the authored key is remembered.

The weights live on the form. The browser may post a ``band`` of its own; that value is
replaced. ``remember`` can name the output (``risk_band`` ← ``band``) and must not take
the rest of the fact-find with it.
"""

from __future__ import annotations

import pytest

from app.flows.memory import promotions
from app.flows.scoring import ScoringInvalid, apply_scoring, compute_outputs, parse_scoring

_SCORING = {
    "fields": [
        {"field_key": "q1", "weights": {"a": 1, "b": 5}},
        {"field_key": "q2", "weights": {"a": 1, "b": 5}},
    ],
    "bands": [
        {
            "key": "cautious",
            "label": "Cautious",
            "min": 2,
            "max": 4,
            "definition": "Prefers capital preservation.",
        },
        {
            "key": "balanced",
            "label": "Balanced",
            "min": 5,
            "max": 10,
            "definition": "Accepts some fluctuation.",
        },
    ],
}


def test_the_score_is_the_sum_of_the_weights() -> None:
    answers = {"q1": "a", "q2": "b", "salary": 82_000, "ni_number": "QQ123456C"}
    _answers, outputs = apply_scoring(answers, _SCORING)
    assert outputs == {
        "band": "balanced",
        "score": 6,
        "label": "Balanced",
        "definition": "Accepts some fluctuation.",
    }
    # The fact-find is not copied into the outputs.
    assert "salary" not in outputs
    assert "ni_number" not in outputs


def test_a_posted_band_is_overwritten_with_the_computed_one() -> None:
    answers = {"q1": "a", "q2": "a", "band": "adventurous", "score": 99}
    scored, outputs = apply_scoring(answers, _SCORING)
    assert outputs["band"] == "cautious"
    assert outputs["score"] == 2
    assert scored["band"] == "cautious"
    assert scored["score"] == 2
    assert scored["q1"] == "a"
    # A key the client did not post is not invented inside the answers.
    assert "label" not in scored


def test_remember_stores_the_computed_band_and_not_the_fact_find() -> None:
    answers = {
        "q1": "b",
        "q2": "a",
        "band": "adventurous",
        "salary": 82_000,
        "pensions": [{"provider": "Aviva"}],
    }
    scored, outputs = apply_scoring(answers, _SCORING)
    remembered = promotions({"risk_band": "band"}, scored, outputs)
    assert remembered == {"risk_band": "balanced"}
    # Naming the output does not pull the questionnaire, the salary, or the other outputs.
    assert "salary" not in remembered
    assert "pensions" not in remembered
    assert "score" not in remembered
    assert "label" not in remembered


def test_remember_finds_an_output_the_client_never_posted() -> None:
    """``band`` lives on the outputs. It does not have to have been an answer."""
    answers = {"q1": "b", "q2": "b"}
    scored, outputs = apply_scoring(answers, _SCORING)
    assert "band" not in scored
    assert outputs["score"] == 10
    assert promotions({"risk_band": "band"}, scored, outputs) == {"risk_band": "balanced"}


def test_an_unscored_form_does_not_invent_outputs_or_rewrite_answers() -> None:
    answers = {"band": "adventurous", "name": "Ada"}
    scored, outputs = apply_scoring(answers, None)
    assert outputs == {}
    assert scored == answers
    assert scored is not answers
    assert promotions({"risk_band": "band"}, scored, outputs) == {"risk_band": "adventurous"}


def test_a_score_that_matches_no_band_still_reports_the_total() -> None:
    scoring = {
        "fields": [{"field_key": "q1", "weights": {"a": 1}}],
        "bands": [
            {
                "key": "high",
                "label": "High",
                "min": 10,
                "max": 20,
                "definition": "Above this form.",
            }
        ],
    }
    outputs = compute_outputs(scoring, {"q1": "a"})
    assert outputs == {"band": None, "score": 1, "label": None, "definition": None}
    assert promotions({"risk_band": "band"}, {"q1": "a"}, outputs) == {"risk_band": None}


def test_option_values_match_as_text() -> None:
    """A select stores ``"2"``; a weight keyed ``2`` in JSON arrives as the same text."""
    scoring = {
        "fields": [{"field_key": "q1", "weights": {"2": 2, "4": 4}}],
        "bands": [{"key": "mid", "label": "Mid", "min": 2, "max": 4, "definition": ""}],
    }
    assert compute_outputs(scoring, {"q1": 2})["score"] == 2
    assert compute_outputs(scoring, {"q1": "4"})["band"] == "mid"


def test_an_unanswered_question_adds_nothing() -> None:
    outputs = compute_outputs(_SCORING, {"q1": "b"})
    assert outputs["score"] == 5
    assert outputs["band"] == "balanced"


def test_the_first_authored_band_wins_an_overlap() -> None:
    scoring = {
        "fields": [{"field_key": "q1", "weights": {"a": 4}}],
        "bands": [
            {"key": "low", "label": "Low", "min": 0, "max": 4, "definition": "First."},
            {"key": "also", "label": "Also", "min": 4, "max": 8, "definition": "Second."},
        ],
    }
    assert compute_outputs(scoring, {"q1": "a"})["band"] == "low"


def test_parse_scoring_rejects_a_band_whose_minimum_exceeds_its_maximum() -> None:
    with pytest.raises(ScoringInvalid):
        parse_scoring(
            {
                "fields": [],
                "bands": [{"key": "odd", "label": "Odd", "min": 5, "max": 1, "definition": ""}],
            }
        )


def test_parse_scoring_rejects_a_boolean_weight() -> None:
    with pytest.raises(ScoringInvalid):
        parse_scoring({"fields": [{"field_key": "q1", "weights": {"a": True}}], "bands": []})


def test_a_blank_scoring_document_is_not_a_score() -> None:
    assert parse_scoring(None) is None
    assert compute_outputs({}, {"q1": "a"}) == {}
    assert compute_outputs({"fields": [], "bands": []}, {"q1": "a"}) == {}
