"""What a journey's generated letter is rendered with.

The worker spreads ``documents.documents.inputs`` into the template context. Built when the
``generate_document`` step is enqueued.

A pension-transfer letter does not ask the fact find again. This execution contributes the
steps it actually ran (the recommendation). Each person's fact find and risk result are filled
from their latest fulfilled answers of those steps, which live on the separate fact-find
journey.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: Step keys the advice seeds use. A definition that names the step differently is stored
#: under its own key, and also under one of these when the key is that role with a prefix or
#: a suffix (``client_fact_find``, ``risk_questionnaire``).
_ROLES = ("fact_find", "risk", "adviser", "recommendation")

#: What a letter may take from an earlier journey when this execution did not ask it. The
#: recommendation stays on the pension journey; carrying ``adviser`` as well would overwrite it
#: with the fact-find's vulnerability notes.
_CARRIED = ("fact_find", "risk")


def _json_obj(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    return dict(value) if isinstance(value, dict) else {}


def storage_keys(step_key: str) -> list[str]:
    """Where one completed form is placed on a participant.

    The step's own key is always included. ``fact_find``, ``risk`` and ``adviser`` are added
    when the key is exactly that role or carries it as a prefix or suffix, so a seed that uses
    those names and a definition that spells them ``client_fact_find`` both land in the shape
    the letter template reads.
    """
    keys = [step_key]
    for role in _ROLES:
        if role == step_key:
            continue
        if step_key.endswith(f"_{role}") or step_key.startswith(f"{role}_"):
            keys.append(role)
    return keys


def answer_body(step_key: str, answers: dict[str, Any], outputs: dict[str, Any]) -> dict[str, Any]:
    """Fact-find and adviser steps contribute their answers. A risk step contributes the
    answers with the computed outputs laid over them, so a posted ``band`` cannot hide the
    server's result."""
    if "risk" in storage_keys(step_key):
        return {**answers, **outputs}
    return dict(answers)


def _display_name(row: dict[str, Any]) -> str:
    parts = " ".join(
        part
        for part in (
            str(row.get("first_name") or "").strip(),
            str(row.get("middle_name") or "").strip(),
            str(row.get("surname") or "").strip(),
        )
        if part
    )
    return parts or str(row.get("name") or "").strip()


def carry_prior(
    people: dict[int, dict[str, Any]], prior: list[dict[str, Any]]
) -> dict[int, dict[str, Any]]:
    """Fill ``fact_find`` and ``risk`` from an earlier journey when this one did not ask them.

    A key this execution already holds is left as it is. ``prior`` is newest-first per person
    and role; the first row for a missing key wins.
    """
    for row in prior:
        contact_id = int(row["contact_id"])
        person = people.get(contact_id)
        if person is None:
            continue
        body = answer_body(
            str(row["step_key"]), _json_obj(row["answers"]), _json_obj(row["outputs"])
        )
        for key in storage_keys(str(row["step_key"])):
            if key in _CARRIED and key not in person:
                person[key] = body
    return people


async def letter_inputs(
    session: AsyncSession, *, tenant_id: int, execution_id: int
) -> dict[str, Any]:
    """``{"participants": [...], "memory": {...}}`` for one execution.

    One participant per person who has a fulfilled ``fill_form`` on this journey. A pension
    journey records the recommendation here; the fact find and the risk result are carried
    from each person's latest fulfilled answers of those steps. ``memory`` is the remembered
    keys across this execution's cases; a key written on more than one case keeps the later
    write.
    """
    rows = (
        (
            await session.execute(
                text(
                    "SELECT cs.step_key, "
                    "       COALESCE(cs.participant_subject_id, "
                    "                CASE WHEN c.subject_type = 'contact' THEN c.subject_id END) "
                    "         AS contact_id, "
                    "       fr.answers, fr.outputs, "
                    "       ct.first_name, ct.middle_name, ct.surname, ct.name "
                    "FROM flows.case_steps cs "
                    "JOIN flows.cases c ON c.id = cs.case_id "
                    "JOIN LATERAL ("
                    "    SELECT answers, outputs FROM documents.form_responses "
                    "    WHERE request_item_id = cs.request_item_id "
                    "    ORDER BY id DESC LIMIT 1"
                    ") fr ON true "
                    "LEFT JOIN crm.contacts ct ON ct.id = COALESCE("
                    "    cs.participant_subject_id, "
                    "    CASE WHEN c.subject_type = 'contact' THEN c.subject_id END) "
                    "WHERE c.execution_id = :e AND cs.tenant_id = :t "
                    "  AND cs.step_type = 'fill_form' AND cs.status = 'fulfilled' "
                    "  AND cs.request_item_id IS NOT NULL "
                    "ORDER BY contact_id, cs.id"
                ),
                {"e": execution_id, "t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    people: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    for row in rows:
        if row["contact_id"] is None:
            continue
        contact_id = int(row["contact_id"])
        if contact_id not in people:
            people[contact_id] = {"name": _display_name(dict(row))}
            order.append(contact_id)
        step_key = str(row["step_key"])
        body = answer_body(step_key, _json_obj(row["answers"]), _json_obj(row["outputs"]))
        for key in storage_keys(step_key):
            people[contact_id][key] = body
    if people:
        prior = (
            (
                await session.execute(
                    text(
                        "SELECT DISTINCT ON (contact_id, step_key) "
                        "       step_key, contact_id, answers, outputs "
                        "FROM ("
                        "    SELECT cs.step_key, fr.answers, fr.outputs, fr.id AS response_id, "
                        "           COALESCE(cs.participant_subject_id, "
                        "                    CASE WHEN c.subject_type = 'contact' "
                        "                         THEN c.subject_id END) AS contact_id "
                        "    FROM flows.case_steps cs "
                        "    JOIN flows.cases c ON c.id = cs.case_id "
                        "    JOIN LATERAL ("
                        "        SELECT id, answers, outputs FROM documents.form_responses "
                        "        WHERE request_item_id = cs.request_item_id "
                        "        ORDER BY id DESC LIMIT 1"
                        "    ) fr ON true "
                        "    WHERE cs.tenant_id = :t AND cs.step_type = 'fill_form' "
                        "      AND cs.status = 'fulfilled' AND cs.request_item_id IS NOT NULL "
                        "      AND cs.step_key = ANY(:roles)"
                        ") carried "
                        "WHERE contact_id = ANY(:ids) "
                        "ORDER BY contact_id, step_key, response_id DESC"
                    ),
                    {"t": tenant_id, "ids": list(people), "roles": list(_CARRIED)},
                )
            )
            .mappings()
            .all()
        )
        carry_prior(people, [dict(row) for row in prior])
    memory_rows = (
        (
            await session.execute(
                text(
                    "SELECT m.key, m.value FROM flows.case_memory m "
                    "JOIN flows.cases c ON c.id = m.case_id "
                    "WHERE c.execution_id = :e AND c.tenant_id = :t "
                    "ORDER BY m.id"
                ),
                {"e": execution_id, "t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    memory: dict[str, Any] = {}
    for row in memory_rows:
        memory[str(row["key"])] = row["value"]
    return {"participants": [people[contact_id] for contact_id in order], "memory": memory}
