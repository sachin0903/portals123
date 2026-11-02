"""Tags — plain labels on contacts and cases, and the ONE service that writes them.

A tag is a label and nothing else (see 0044_tags.sql for why that austerity is the feature). What
this module owns is the small set of rules that make a label set usable:

* **Normalisation.** ``VIP``, ``vip`` and ``  Vip  `` are one tag. Without this an idempotent
  apply is not idempotent and a filter cannot be written, because the label set silently forks on
  whitespace and case that nobody can see.
* **Idempotency.** ``apply`` twice is one row and one event; ``remove`` on an absent tag is a
  no-op, not an error. Automations retry (at-least-once redelivery is the consumer's whole
  design), so anything else here would turn a redelivery into a duplicate or a dead letter.
* **The ``tags.applied`` domain fact.** Applying a tag appends to ``flows.domain_events`` in the
  SAME transaction as the row — the trigger registry's ``tag.applied`` reads it, and a
  composition primitive whose event can be lost is not one.

Runs on whatever session the caller holds: an RLS-scoped ``tenant_session`` on the request path,
a ``service_role`` session in the worker. Row-level audit comes from the 0001 spine
(``audit.attach`` in the migration) — no manual audit writes here.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: What a tag can hang on. Mirrors the contract's ``apply_tag.config.target``.
Target = Literal["contact", "case"]
TARGETS: tuple[Target, ...] = ("contact", "case")

#: Where a tag came from. ``flow`` is Phase B's action-step path; it exists here so that wave adds
#: no migration.
SOURCES: tuple[str, ...] = ("manual", "automation", "flow")

#: Mirrors the ``length(tag) <= 64`` CHECK. A label longer than this is a sentence, and a sentence
#: belongs in a note or a real field.
MAX_LENGTH = 64

#: The domain fact an apply appends. Named by the contract's ``tag.applied`` trigger
#: (``flow-contract.json`` → triggers → tag.applied → event) and mirrored in
#: ``app/flows/triggers.py::PENDING_DOMAIN_EVENTS``; change the three together.
APPLIED_EVENT = "tags.applied"

#: Any run of whitespace (including the unicode kinds a paste from Word carries) collapses to one
#: space, so "high  risk" and "high risk" are the same label.
_WHITESPACE = re.compile(r"\s+")

#: target → (table, id column). Keyed by plain ``str`` rather than ``Target`` because the values
#: arriving here come from authored JSON, so the membership test IS the validation.
_TABLES: dict[str, tuple[str, str]] = {
    "contact": ("crm.contact_tags", "contact_id"),
    "case": ("flows.case_tags", "case_id"),
}


class TagError(ValueError):
    """A validation failure the caller should surface as a 422 (bad label, unknown target)."""


def normalise(tag: str) -> str:
    """The canonical form of a label: trimmed, whitespace-collapsed, casefolded.

    Casefold rather than ``lower`` so non-English labels normalise the way their readers expect
    (German ``ß`` folds to ``ss``). The database CHECK compares against ``lower(btrim(tag))``,
    which agrees with casefold on every input either will ever see and is all Postgres can
    cheaply express — the belt is there to catch a writer that bypasses this function, not to
    re-derive it.
    """
    if not isinstance(tag, str):
        raise TagError("A tag must be text")
    cleaned = _WHITESPACE.sub(" ", tag).strip().casefold()
    if not cleaned:
        raise TagError("A tag cannot be blank")
    if len(cleaned) > MAX_LENGTH:
        raise TagError(f"A tag cannot be longer than {MAX_LENGTH} characters")
    return cleaned


def _table(target: str) -> tuple[str, str]:
    """``(table, id column)`` for a target. The ONE place the two tables are named, so a caller
    cannot interpolate a table name of its own into SQL."""
    table = _TABLES.get(target)
    if table is None:
        raise TagError(f"Unknown tag target '{target}'")
    return table


async def apply(
    session: AsyncSession,
    *,
    tenant_id: int,
    target: Target,
    target_id: int,
    tag: str,
    source: str = "manual",
    applied_by: str | None = None,
    case_id: int | None = None,
) -> bool:
    """Put a label on a contact or a case. Returns True if it was newly applied.

    The return value is the honest answer to "did anything change", and callers use it: the
    executor reports "already tagged" rather than claiming work it did not do, and the
    ``tags.applied`` fact is appended ONLY on a real transition — a redelivered firing must not
    re-fire every automation armed on the tag.

    ``case_id`` lets a contact tag applied from inside a journey record which case it happened in,
    so the fact carries it. It is ignored for ``target='case'``, where the case is the target.
    """
    if source not in SOURCES:
        raise TagError(f"Unknown tag source '{source}'")
    table, column = _table(target)
    label = normalise(tag)

    # ON CONFLICT DO NOTHING + RETURNING: a row comes back only when this INSERT is the one that
    # created it, which is exactly the "did anything change" signal — and it is race-free, unlike
    # a SELECT-then-INSERT that two concurrent firings would both pass.
    inserted = (
        await session.execute(
            text(
                f"INSERT INTO {table} (tenant_id, {column}, tag, source, applied_by) "  # noqa: S608
                "VALUES (:t, :i, :tag, :src, CAST(:by AS uuid)) "
                f"ON CONFLICT ({column}, tag) DO NOTHING RETURNING id"
            ),
            {"t": tenant_id, "i": target_id, "tag": label, "src": source, "by": applied_by},
        )
    ).scalar_one_or_none()
    if inserted is None:
        return False

    await _emit_applied(
        session,
        tenant_id=tenant_id,
        target=target,
        target_id=target_id,
        tag=label,
        source=source,
        applied_by=applied_by,
        case_id=target_id if target == "case" else case_id,
    )
    return True


async def remove(
    session: AsyncSession,
    *,
    tenant_id: int,
    target: Target,
    target_id: int,
    tag: str,
) -> bool:
    """Take a label off. Returns True if it was there.

    No ``tags.removed`` fact, deliberately: nothing consumes one, and the contract's trigger menu
    offers no "tag removed" entry. The DML audit trigger already records the deletion, so the
    evidence exists — what does not exist is a public event promising a subscriber that does not
    yet have a consumer. Add it with its producer, the ``app/email/transport.py`` precedent.
    """
    table, column = _table(target)
    deleted = (
        await session.execute(
            text(
                f"DELETE FROM {table} WHERE tenant_id = :t AND {column} = :i "  # noqa: S608
                "AND tag = :tag RETURNING id"
            ),
            {"t": tenant_id, "i": target_id, "tag": normalise(tag)},
        )
    ).scalar_one_or_none()
    return deleted is not None


async def list_for(
    session: AsyncSession, *, target: Target, target_id: int, tenant_id: int | None = None
) -> list[str]:
    """The labels on one contact/case, sorted. Sorted because this feeds a UI and a condition
    context, and an unstable order makes both flicker and hard to assert on.

    ``tenant_id`` is optional because an RLS-scoped session already constrains the read; the
    worker's ``service_role`` session does not, so it passes one.
    """
    table, column = _table(target)
    rows = (
        (
            await session.execute(
                text(
                    f"SELECT tag FROM {table} WHERE {column} = :i "  # noqa: S608
                    "AND (CAST(:t AS int) IS NULL OR tenant_id = :t) ORDER BY tag"
                ),
                {"i": target_id, "t": tenant_id},
            )
        )
        .scalars()
        .all()
    )
    return [str(r) for r in rows]


async def _emit_applied(
    session: AsyncSession,
    *,
    tenant_id: int,
    target: Target,
    target_id: int,
    tag: str,
    source: str,
    applied_by: str | None,
    case_id: int | None,
) -> None:
    """Append the ``tags.applied`` fact, in the caller's transaction.

    Transactional with the row on purpose. This is the distinction ``app/audit_actions.py``'s header
    draws: the action-event stream is a best-effort post-commit floor, while a subscriber that
    must not miss an event uses a domain fact written in the same transaction as the thing it
    describes. Composition through tags is exactly that kind of subscriber — "when a client is
    tagged `ready-to-invest`, start the investment journey" cannot be allowed to silently not
    happen because a separate write failed after the tag landed.

    The idempotency key includes the target and the tag but NOT a timestamp, so re-applying a tag
    that was removed and re-applied appends no second fact. That is a deliberate trade: the
    alternative (a unique key per application) would let a remove/apply loop generate unbounded
    facts, and the trigger's job is "this client now carries this label", which is idempotent by
    nature. The DML audit trail remains the record of every individual change.
    """
    await session.execute(
        text(
            "INSERT INTO flows.domain_events "
            "  (tenant_id, execution_id, case_id, event_type, payload, idempotency_key) "
            "VALUES (:t, NULL, :c, :et, CAST(:p AS jsonb), :k) "
            "ON CONFLICT (tenant_id, event_type, idempotency_key) DO NOTHING"
        ),
        {
            "t": tenant_id,
            "c": case_id,
            "et": APPLIED_EVENT,
            "p": json.dumps(
                {
                    "target": target,
                    "target_id": target_id,
                    "tag": tag,
                    "source": source,
                    "applied_by": applied_by,
                }
            ),
            "k": f"{target}:{target_id}:{tag}",
        },
    )


async def tags_by_contact(
    session: AsyncSession, *, tenant_id: int, contact_ids: list[int]
) -> dict[int, list[str]]:
    """Labels for several contacts at once, for a listing that would otherwise N+1."""
    if not contact_ids:
        return {}
    rows = (
        (
            await session.execute(
                text(
                    "SELECT contact_id, tag FROM crm.contact_tags "
                    "WHERE tenant_id = :t AND contact_id = ANY(:ids) ORDER BY contact_id, tag"
                ),
                {"t": tenant_id, "ids": contact_ids},
            )
        )
        .mappings()
        .all()
    )
    out: dict[int, list[str]] = {}
    for row in rows:
        out.setdefault(int(row["contact_id"]), []).append(str(row["tag"]))
    return out


def to_dict(row: Any) -> dict[str, Any]:
    """One tag row as the API shape (kept here so a future router and the tests agree)."""
    return {
        "tag": row["tag"],
        "source": row["source"],
        "applied_by": str(row["applied_by"]) if row["applied_by"] else None,
        "created_at": row["created_at"],
    }
