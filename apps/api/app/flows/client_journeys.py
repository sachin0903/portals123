"""Journeys on one client, and the forms they answered.

The client record's Journeys section reads this. A journey belongs to the person when a
profile case is about them, they are the case's contact, or a step names them as its
participant — a couple's fact find is on both records, and each record shows that
person's own forms. The letter is the journey's, so it is listed for everyone on it.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# A profile case, the case's own contact, or a step fanned out to this person.
_ON_JOURNEY = """
EXISTS (
  SELECT 1 FROM flows.cases mine
  WHERE mine.execution_id = e.id
    AND (
      mine.subject_contact_id = :cid
      OR (mine.kind = 'profile' AND mine.subject_type = 'contact' AND mine.subject_id = :cid)
      OR EXISTS (
        SELECT 1 FROM flows.case_steps ps
        WHERE ps.case_id = mine.id AND ps.participant_subject_id = :cid
      )
    )
)
"""

_LIST = f"""
SELECT e.uid, e.status, e.created_at, f.name AS flow_name, f.code AS flow_code,
       COUNT(cs.id) FILTER (
         WHERE cs.status NOT IN ('skipped', 'cancelled')
       ) AS steps_total,
       COUNT(cs.id) FILTER (WHERE cs.status = 'fulfilled') AS steps_done
  FROM flows.executions e
  JOIN flows.flows f ON f.id = e.flow_id
  JOIN flows.cases c ON c.execution_id = e.id
  LEFT JOIN flows.case_steps cs ON cs.case_id = c.id
 WHERE {_ON_JOURNEY}
 GROUP BY e.id, f.id
 ORDER BY e.created_at DESC, e.id DESC
"""  # noqa: S608 — the fragment is a constant predicate, every value is bound.

_HEAD = f"""
SELECT e.uid, e.status, e.created_at, f.name AS flow_name, f.code AS flow_code
  FROM flows.executions e
  JOIN flows.flows f ON f.id = e.flow_id
 WHERE e.uid = :uid AND {_ON_JOURNEY}
"""  # noqa: S608

# This person's fill_form steps only. Another person on the same journey has their own record.
_FORMS = """
SELECT cs.step_key, cs.status,
       COALESCE(NULLIF(btrim(cs.title), ''), fm.name, cs.step_key) AS title,
       ri.form_uid,
       fr.answers, fr.outputs, fr.submitted_at,
       NULLIF(btrim(concat_ws(' ', u.first_name, u.last_name)), '') AS submitted_by
  FROM flows.case_steps cs
  JOIN flows.cases c ON c.id = cs.case_id
  JOIN flows.executions e ON e.id = c.execution_id
  LEFT JOIN documents.request_items ri ON ri.id = cs.request_item_id
  LEFT JOIN documents.forms fm ON fm.uid = ri.form_uid
  LEFT JOIN LATERAL (
    SELECT answers, outputs, submitted_at, submitted_by
      FROM documents.form_responses
     WHERE request_item_id = ri.id
     ORDER BY id DESC
     LIMIT 1
  ) fr ON true
  LEFT JOIN auth.users u ON u.id = fr.submitted_by
 WHERE e.uid = :uid
   AND cs.step_type = 'fill_form'
   AND (
     cs.participant_subject_id = :cid
     OR (
       cs.participant_subject_id IS NULL
       AND c.subject_type = 'contact'
       AND (c.subject_id = :cid OR c.subject_contact_id = :cid)
     )
   )
 ORDER BY cs.id
"""

_FIELDS = """
SELECT f.uid AS form_uid, ff.field_key, ff.label, ff.section, ff.field_type,
       ff.options
  FROM documents.forms f
  JOIN documents.form_fields ff ON ff.form_id = f.id
 WHERE f.uid = ANY(:uids)
 ORDER BY ff.sort_order, ff.id
"""

_DOCUMENTS = """
SELECT COALESCE(NULLIF(btrim(cs.title), ''), 'Document') AS title,
       cs.status, doc.uid, doc.name
  FROM flows.case_steps cs
  JOIN flows.cases c ON c.id = cs.case_id
  JOIN flows.executions e ON e.id = c.execution_id
  LEFT JOIN documents.documents doc
    ON doc.id = NULLIF(cs.data ->> 'document_id', '')::uuid
 WHERE e.uid = :uid AND cs.step_type = 'generate_document'
 ORDER BY cs.id
"""

_OTHERS = """
SELECT DISTINCT c2.uid,
       COALESCE(
         NULLIF(btrim(concat_ws(' ', c2.first_name, c2.surname)), ''),
         NULLIF(btrim(c2.name), ''),
         'Client'
       ) AS name
  FROM flows.cases ca
  JOIN flows.executions e ON e.id = ca.execution_id
  JOIN crm.contacts c2 ON c2.id = ca.subject_id
 WHERE e.uid = :uid
   AND ca.kind = 'profile'
   AND ca.subject_type = 'contact'
   AND ca.subject_id IS NOT NULL
   AND ca.subject_id <> :cid
 ORDER BY name
"""


def _obj(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return dict(value) if isinstance(value, dict) else {}


def assemble_forms(
    steps: list[dict[str, Any]], fields: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Attach each form's fields to its step, and drop a step nobody has answered.

    A saved draft counts: the row exists before the step is fulfilled. A step that was
    never opened does not — the list already says how far the journey has got.
    """
    by_form: dict[str, list[dict[str, Any]]] = {}
    for field in fields:
        uid = field.get("form_uid")
        if not isinstance(uid, str) or not uid:
            continue
        label = field.get("label")
        key = field.get("field_key")
        by_form.setdefault(uid, []).append(
            {
                "key": key if isinstance(key, str) else "",
                "label": label.strip() if isinstance(label, str) and label.strip() else key,
                "section": field.get("section") or None,
                "field_type": field.get("field_type") or "text",
                "options": field.get("options"),
            }
        )

    forms: list[dict[str, Any]] = []
    for step in steps:
        answers = _obj(step.get("answers"))
        if not answers and step.get("status") != "fulfilled":
            continue
        form_uid = step.get("form_uid")
        forms.append(
            {
                "step_key": step.get("step_key"),
                "title": step.get("title") or step.get("step_key") or "Form",
                "status": step.get("status"),
                "submitted_at": step.get("submitted_at"),
                "submitted_by": step.get("submitted_by"),
                "answers": answers,
                "outputs": _obj(step.get("outputs")),
                "fields": by_form.get(form_uid, []) if isinstance(form_uid, str) else [],
            }
        )
    return forms


async def list_for_contact(session: AsyncSession, contact_id: int) -> list[dict[str, Any]]:
    rows = (await session.execute(text(_LIST), {"cid": contact_id})).mappings()
    return [
        {
            "uid": row["uid"],
            "status": row["status"],
            "flow_name": row["flow_name"],
            "flow_code": row["flow_code"],
            "created_at": row["created_at"],
            "steps_done": int(row["steps_done"] or 0),
            "steps_total": int(row["steps_total"] or 0),
        }
        for row in rows
    ]


async def get_for_contact(
    session: AsyncSession, contact_id: int, execution_uid: str
) -> dict[str, Any] | None:
    head = (
        (await session.execute(text(_HEAD), {"cid": contact_id, "uid": execution_uid}))
        .mappings()
        .first()
    )
    if head is None:
        return None

    steps = [
        dict(row)
        for row in (
            await session.execute(text(_FORMS), {"cid": contact_id, "uid": execution_uid})
        ).mappings()
    ]
    form_uids = [row["form_uid"] for row in steps if isinstance(row.get("form_uid"), str)]
    fields: list[dict[str, Any]] = []
    if form_uids:
        fields = [
            dict(row)
            for row in (await session.execute(text(_FIELDS), {"uids": form_uids})).mappings()
        ]
    documents = [
        {
            "title": row["title"],
            "status": row["status"],
            "uid": row["uid"],
            "name": row["name"],
        }
        for row in (await session.execute(text(_DOCUMENTS), {"uid": execution_uid})).mappings()
    ]
    others = [
        {"uid": row["uid"], "name": row["name"]}
        for row in (
            await session.execute(text(_OTHERS), {"cid": contact_id, "uid": execution_uid})
        ).mappings()
    ]
    return {
        "uid": head["uid"],
        "status": head["status"],
        "flow_name": head["flow_name"],
        "flow_code": head["flow_code"],
        "created_at": head["created_at"],
        "forms": assemble_forms(steps, fields),
        "documents": documents,
        "others": others,
    }
