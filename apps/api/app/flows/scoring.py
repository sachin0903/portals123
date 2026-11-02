"""Turn a form's answers into a band. The browser never gets to choose the band.

``documents.forms.scoring`` names which questions count, the points for each option, and
the bands the total falls into::

    {"fields": [{"field_key": "q1", "weights": {"a": 1, "b": 5}}],
     "bands": [{"key": "cautious", "label": "Cautious", "min": 1, "max": 4,
                "definition": "…"}]}

On submit the server writes ``documents.form_responses.outputs``::

    {"band": "cautious", "score": 3, "label": "Cautious", "definition": "…"}

A posted answer whose key is one of those four is replaced with the computed value.
Anything else in the answer set is left alone — a fact-find is not copied into the
outputs, and it is not copied into case memory either. Memory promotion
(``memory.promotions``) may *name* an output key (``risk_band`` ← ``band``). That is
one authored fact, not the questionnaire.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: Written on every scored submit, in this order. A posted answer under one of these
#: keys is replaced; the key is not invented in ``answers`` when the client did not send it.
OUTPUT_KEYS: tuple[str, ...] = ("band", "score", "label", "definition")


class ScoringInvalid(ValueError):
    """The scoring document an author tried to save is not the shape above."""


def _as_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def _number(value: Any) -> int | float | None:
    """A finite number. Booleans are not numbers here — ``True`` must not score as 1."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        if value.is_integer():
            return int(value)
    return value


def parse_scoring(raw: Any) -> dict[str, Any] | None:
    """The canonical scoring document, or ``None`` when the form is not scored.

    Raises ``ScoringInvalid`` for a document that is present but not the shape authors
    save. An empty ``fields`` and ``bands`` is a draft and is kept, so the builder can
    save a panel it has only just opened.
    """
    raw = _as_json(raw)
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ScoringInvalid("Scoring must be an object with fields and bands.")
    fields_raw = raw.get("fields", [])
    bands_raw = raw.get("bands", [])
    if not isinstance(fields_raw, list) or not isinstance(bands_raw, list):
        raise ScoringInvalid("Scoring fields and bands must both be lists.")

    fields: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in fields_raw:
        if not isinstance(item, Mapping):
            raise ScoringInvalid("Each scored question needs a field_key and weights.")
        key = item.get("field_key")
        if not isinstance(key, str) or not key.strip():
            raise ScoringInvalid("Each scored question needs a field_key.")
        field_key = key.strip()
        if field_key in seen:
            raise ScoringInvalid(f"'{field_key}' is scored more than once.")
        seen.add(field_key)
        weights_raw = item.get("weights", {})
        if not isinstance(weights_raw, Mapping):
            raise ScoringInvalid(
                f"Weights for '{field_key}' must map each option to a number of points."
            )
        weights: dict[str, int | float] = {}
        for option, points in weights_raw.items():
            number = _number(points)
            if number is None:
                raise ScoringInvalid(
                    f"The weight for '{field_key}' option '{option}' must be a number."
                )
            weights[str(option)] = number
        fields.append({"field_key": field_key, "weights": weights})

    bands: list[dict[str, Any]] = []
    for item in bands_raw:
        if not isinstance(item, Mapping):
            raise ScoringInvalid("Each band needs a key, label, minimum, maximum and definition.")
        key = item.get("key")
        label = item.get("label")
        if not isinstance(key, str) or not key.strip():
            raise ScoringInvalid("Each band needs a key.")
        if not isinstance(label, str) or not label.strip():
            raise ScoringInvalid(f"Band '{key}' needs a label.")
        low = _number(item.get("min"))
        high = _number(item.get("max"))
        if low is None or high is None:
            raise ScoringInvalid(f"Band '{key}' needs a numeric minimum and maximum.")
        if low > high:
            raise ScoringInvalid(f"Band '{key}' has a minimum above its maximum.")
        definition = item.get("definition", "")
        if definition is None:
            definition = ""
        if not isinstance(definition, str):
            raise ScoringInvalid(f"Band '{key}' has a definition that is not text.")
        bands.append(
            {
                "key": key.strip(),
                "label": label.strip(),
                "min": low,
                "max": high,
                "definition": definition,
            }
        )
    return {"fields": fields, "bands": bands}


def _weight(weights: Mapping[str, Any], answer: Any) -> int | float | None:
    """Points for one answer, or ``None`` when this answer is not one of the weighted options.

    Option values are stored as text (a select's ``value``). A numeric answer ``2`` matches
    the weight key ``"2"``. A list (a multi-select) is not a single option and scores nothing.
    """
    if answer is None or isinstance(answer, list | dict):
        return None
    if isinstance(answer, str) and not answer.strip():
        return None
    key = str(answer).strip() if isinstance(answer, str) else str(answer)
    if key not in weights:
        return None
    return _number(weights[key])


def _band_for(bands: list[dict[str, Any]], score: int | float) -> dict[str, Any] | None:
    """The first authored band whose inclusive range contains ``score``. Overlaps are the
    author's order, not an error — the earlier band is the one they wrote first."""
    for band in bands:
        if band["min"] <= score <= band["max"]:
            return band
    return None


def compute_outputs(scoring: Any, answers: Mapping[str, Any] | None) -> dict[str, Any]:
    """The four output keys, or ``{}`` when this form is not scored.

    Unweighted and unanswered questions add nothing. The total is still written when no
    band contains it: ``band``, ``label`` and ``definition`` are then null, and ``score``
    is the total. A document that does not parse is treated as "not scored" — a submit
    must not fail because a stored blob is older than the checker.
    """
    try:
        parsed = parse_scoring(scoring)
    except ScoringInvalid:
        return {}
    if not parsed or (not parsed["fields"] and not parsed["bands"]):
        return {}
    supplied: Mapping[str, Any] = answers if isinstance(answers, Mapping) else {}
    total: int | float = 0
    for field in parsed["fields"]:
        points = _weight(field["weights"], supplied.get(field["field_key"]))
        if points is not None:
            total += points
    band = _band_for(parsed["bands"], total)
    if band is None:
        return {"band": None, "score": total, "label": None, "definition": None}
    return {
        "band": band["key"],
        "score": total,
        "label": band["label"],
        "definition": band["definition"],
    }


def apply_scoring(
    answers: Mapping[str, Any] | None, scoring: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(answers, outputs)``.

    The answers dict is a copy. A posted key that is also an output key (``band``,
    ``score``, ``label``, ``definition``) is replaced with the computed value. Output
    keys the client did not post are not added to the answers — they live on ``outputs``,
    which is what ``remember`` reads when it names one of them.
    """
    posted = dict(answers) if isinstance(answers, Mapping) else {}
    outputs = compute_outputs(scoring, posted)
    for key, value in outputs.items():
        if key in posted:
            posted[key] = value
    return posted, outputs


async def score_stored_response(
    session: AsyncSession, request_item_id: int
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Score the latest response for a request item and store the outputs on that row.

    Returns ``(answers, outputs)``. ``answers`` is ``None`` when nothing has been stored.
    A form with no scoring leaves the row untouched and returns ``outputs`` of ``{}``, so
    a fact-find submit does not rewrite its answers. When the form is scored, colliding
    answer keys are overwritten in the same update that writes ``outputs``.
    """
    row = (
        (
            await session.execute(
                text(
                    "SELECT fr.id, fr.answers, f.scoring "
                    "FROM documents.form_responses fr "
                    "LEFT JOIN documents.forms f "
                    "  ON f.uid = fr.form_uid AND f.tenant_id = fr.tenant_id "
                    "WHERE fr.request_item_id = :i "
                    "ORDER BY fr.id DESC LIMIT 1"
                ),
                {"i": request_item_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None, {}
    raw_answers = _as_json(row["answers"])
    answers: dict[str, Any] = dict(raw_answers) if isinstance(raw_answers, Mapping) else {}
    outputs = compute_outputs(row["scoring"], answers)
    if not outputs:
        return answers, {}
    scored, outputs = apply_scoring(answers, row["scoring"])
    await session.execute(
        text(
            "UPDATE documents.form_responses "
            "SET answers = CAST(:answers AS jsonb), outputs = CAST(:outputs AS jsonb) "
            "WHERE id = :id"
        ),
        {"id": row["id"], "answers": json.dumps(scored), "outputs": json.dumps(outputs)},
    )
    return scored, outputs
