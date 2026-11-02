"""Pension contributions and the payment expectations they raise (docs/contributions.md).

The write path behind the platform portal's contributions workbench. Runs on ``firm_session``
(service_role), so every statement is EXPLICITLY scoped to the caller's own ``tenant_id`` and
never to a client-supplied one — the same discipline as ``app.instructions``.

Three records, in order:

1. ``platform.contributions`` — the domain record: what kind of contribution, for whom, the
   relief arithmetic, the contributor's identity.
2. ``platform.transaction_expectations`` — the forecast: money of this size is due on this
   account by this date.
3. The custodian instruction — an Anticipated Cash Receipt at SEI, which moves the expectation
   from ``expected`` to ``instructed``.

**Step 3 is best-effort, deliberately.** The contribution and the expectation are Graphene's
own truth; SEI is a downstream notification. Rolling the local record back because the
custodian is unreachable (or because a firm's SEI reference codes are not configured yet — see
``app.integrations.sei``) would lose an adviser's work over an integration problem. So a
failure is recorded on the expectation and reported alongside the created record, and the
lodge can be retried without re-booking.

Relief arithmetic is duplicated from ``apps/platform/src/lib/contributions/relief.ts``, which
drives the live preview in the form. The server's copy is the authoritative one — a client
cannot be trusted with the number that determines an HMRC reclaim — and
``tests/test_contributions.py`` pins the two against the same cases.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import outbound
from app.ids import new_id
from app.integrations import sei as sei_integration
from app.integrations.sei.reference import portfolio_entity_id
from app.providers import custodians

# ---- HMRC arithmetic (mirror of relief.ts) -------------------------------------------------

#: Basic rate. Relief at source is always at basic rate regardless of the member's marginal
#: rate — higher-rate relief is claimed through self-assessment, not by the scheme.
RELIEF_RATE = Decimal("0.20")

#: Categories paid NET of basic-rate relief, so the scheme reclaims the difference.
NET_PAID = frozenset({"personal_contribution", "third_party_contribution"})

CATEGORIES = frozenset(
    {
        "personal_contribution",
        "employer_contribution",
        "third_party_contribution",
        "transfer_in",
        "not_a_contribution",
    }
)
FREQUENCIES = frozenset({"single", "monthly", "quarterly", "half_yearly", "annually"})
PAID_BY = frozenset({"client", "third_party", "employer"})

#: Wrappers a contribution applies to — THE FALLBACK, not the rule. `platform.products.is_pension`
#: is the authority for whether a SIPP Payment asks contribution type
#: (`account.is_pension ?? isPensionProduct`), and this set stays only for a row whose shelf item
#: states nothing. A firm that ticked
#: `is_pension` on a wrapper outside this set (the stated reason the column is editable) used to
#: get the button, fill the form, and get a 422; beneficiaries already take the shelf first for
#: the same reason. Custodian integration gates keep their own sets — this is a domain write.
PENSION_PRODUCTS = frozenset({"SIPP", "PENSION", "SSAS", "TPPENSION"})

#: The workbench pipeline, in order. Mirrors ``status.ts``'s STATUSES.
STATUSES = (
    "identified",
    "awaiting_confirmation",
    "confirmed",
    "ml_recorded",
    "ras_applied",
    "ras_received",
    "tax_relief_received",
    "reconciled",
)
#: Off-pipeline terminals. Neither is "stage 9" — they are ways a contribution stops — so the
#: workbench excludes them from the pipeline strip rather than appending them to it.
TERMINAL = frozenset({"cancelled"})
OFF_PIPELINE = frozenset({"manual_intervention", *TERMINAL})
ALL_STATUSES = frozenset({*STATUSES, *OFF_PIPELINE})

#: Statuses a categorisation may still be changed from. Past ``confirmed`` the classification
#: has been acted on downstream (recorded to ML, relief claimed) and amending it silently would
#: desynchronise the back office.
CLASSIFIABLE = frozenset({"identified", "awaiting_confirmation", "confirmed"})

#: Categories Morgan Lloyd has no concept of, so nothing downstream ever holds them. Mirrors
#: `integrations/morgan_lloyd/contributions.py::UNPUSHABLE` and the portal's
#: `UNPUSHABLE_CATEGORIES`; kept here because `categorise` needs it and importing the integration
#: into the domain module for a two-member set would invert the dependency.
DISPOSALS = frozenset({"transfer_in", "not_a_contribution"})

# ---- the Morgan Lloyd status mirror --------------------------------------------------------
#
# `morgan_lloyd.enums.ContributionStatus`, inlined for the same reason
# `integrations/morgan_lloyd/contributions.py` inlines the type codes: this module must stay
# importable when the vendor package is absent. Pinned against the enum by a test.

ML_STATUS_APPLICATION = 809130000
ML_STATUS_ACTIVE = 809130001
ML_STATUS_STOPPED = 809130002
ML_STATUS_RECEIVED = 809130003

ML_STATUS_LABELS: dict[int, str] = {
    ML_STATUS_APPLICATION: "Application",
    ML_STATUS_ACTIVE: "Active",
    ML_STATUS_STOPPED: "Stopped",
    ML_STATUS_RECEIVED: "Received",
}

#: Morgan Lloyd's status -> where that puts the contribution in Graphene's pipeline.
#:
#: The two vocabularies are not the same length and should not be forced to be. ML tracks the
#: back-office record; Graphene tracks the whole journey, of which ML is the middle. So Active
#: (ML has it and the HMRC reclaim is running) lands on `ras_applied`, Received (the money is
#: recognised against the scheme) on `ras_received`, and the last two stages —
#: `tax_relief_received` and `reconciled` — have no ML equivalent at all because they are
#: facts about SEI's relief credit and our own three-way compare.
ML_STATUS_TO_PIPELINE: dict[int, str] = {
    ML_STATUS_APPLICATION: "ml_recorded",
    ML_STATUS_ACTIVE: "ras_applied",
    ML_STATUS_RECEIVED: "ras_received",
    ML_STATUS_STOPPED: "cancelled",
}


#: THE STAGE VOCABULARY — the six the workbench counts, plus the two terminals.
#:
#: `status` is the engine's word for where a contribution has got to; a STAGE is the word an
#: adviser reads, and there are fewer of them because several statuses answer the same question
#: ("has anybody downstream been told?"). It drives the KPI strip, the status filter and the
#: classify panel's action set alike, and it is mirrored EXACTLY by
#: `pkgs/ui/.../book/contributions/status.ts::STAGES` — `tests/test_contributions.py` pins the two
#: against each other, because the filter is applied HERE and the options are rendered THERE, so a
#: divergence would offer an option that matches nothing and report it as an empty book.
#:
#: A total function of `status` alone, deliberately. Deriving it from the expectation as well was
#: tried and dropped: an `ml_recorded` row whose SEI expectation had failed would then read as
#: "not lodged", hiding a contribution that IS with the administrator.
STAGES: dict[str, tuple[str, ...]] = {
    "awaiting_classification": ("identified", "awaiting_confirmation"),
    "with_administrator": ("ml_recorded",),
    "relief_claimed": ("ras_applied",),
    "relief_in_transit": ("ras_received",),
    "relief_credited": ("tax_relief_received",),
    # Classified here and nothing downstream knows: the Morgan Lloyd push has not happened
    # (`confirmed`), or the contribution has stalled and needs a human (`manual_intervention`).
    "not_lodged": ("confirmed", "manual_intervention"),
    "completed": ("reconciled",),
    "cancelled": ("cancelled",),
}

_STAGE_OF: dict[str, str] = {
    status: stage for stage, members in STAGES.items() for status in members
}


def stage_of(status: str | None) -> str | None:
    """The stage a status belongs to, or None for a status with no stage.

    None rather than a fallback bucket: an unmapped status must be visible as a gap in the pin
    test, not quietly filed under whichever stage looked closest.
    """
    return _STAGE_OF.get(status or "")


def stage_totals(rows: Sequence[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    """Amount and count per stage, over WHOLE-BOOK rows.

    Computed here and returned in the listing envelope rather than derived by the portal from the
    rows it was handed, because the portal is handed ONE PAGE. The strip above the queue is a
    statement about the firm's book — "£38,400 is with the scheme administrator" — and deriving it
    from twenty-five rows would make it a statement about the scroll position, in the shape of a
    figure a compliance reviewer would reasonably quote.

    Taken before the filters as well as before the page cut: the strip is how you NAVIGATE to a
    stage, so narrowing to one stage must not empty the other five.

    Every stage is present, zeros included. An absent key would render as a missing card, and a
    stage with no money is a real and useful answer.
    """
    totals: dict[str, dict[str, float | int]] = {
        stage: {"amount": 0.0, "count": 0} for stage in STAGES
    }
    for row in rows:
        stage = stage_of(row.get("status"))
        if stage is None:
            continue
        bucket = totals[stage]
        # The amount PAID, never the gross: the gross includes relief HMRC has not paid yet, so
        # totalling it would state money the scheme does not hold.
        bucket["amount"] = float(bucket["amount"]) + float(row.get("amount") or 0)
        bucket["count"] = int(bucket["count"]) + 1
    return totals


def pipeline_index(status: str) -> int:
    """Position in ``STATUSES``, or -1 for an off-pipeline status.

    Used to keep a status update monotonic: a provider refresh must never drag a contribution
    backwards because a nightly ML read arrived after a local advance.
    """
    try:
        return STATUSES.index(status)
    except ValueError:
        return -1


class ContributionConflict(RuntimeError):
    """The record's current state forbids the action — a 409, not a 400.

    A distinct type rather than a ValueError the router splits on message text. That pattern is
    already in ``routers/contributions.py`` (``"past classification" in str(exc)``) and it is
    one reworded error message away from silently downgrading a conflict to a bad request.
    """


class AlreadyClassified(ContributionConflict):
    """This credit already has a contribution against it."""


class AlreadyCancelled(ContributionConflict):
    """This contribution has already been cancelled."""


def _dec(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _round(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def gross_of(amount: Decimal | float | str, category: str) -> Decimal:
    """Gross amount for allowance and illustration purposes."""
    net = _dec(amount)
    if category not in NET_PAID:
        return _round(net)
    return _round(net / (Decimal(1) - RELIEF_RATE))


def relief_of(amount: Decimal | float | str, category: str) -> Decimal:
    """Basic-rate relief the scheme reclaims from HMRC. Zero unless paid net."""
    if category not in NET_PAID:
        return Decimal("0.00")
    return _round(gross_of(amount, category) - _dec(amount))


def tax_year_of(on: date) -> str:
    """The UK tax year a date falls in, as ``2026/27``. The year starts on 6 April."""
    start = on.year if (on.month, on.day) >= (4, 6) else on.year - 1
    return f"{start}/{str((start + 1) % 100).zfill(2)}"


# ---- reads ---------------------------------------------------------------------------------

_SELECT = """
SELECT c.id, c.uid, c.account_id, c.contact_id, c.expectation_id, c.transaction_id,
       c.category, c.amount::float8 AS amount, c.gross_amount::float8 AS gross_amount,
       c.relief_amount::float8 AS relief_amount,
       c.prefunded_ras_amount::float8 AS prefunded_ras_amount, c.currency,
       c.tax_year, c.frequency, c.paid_by, c.contribution_date,
       c.employer_name, c.address_line1, c.address_line2, c.address_line3,
       c.city, c.county, c.postcode, c.phone, c.email,
       c.status, c.ml_contribution_id, c.ml_recorded_at, c.note,
       c.ml_status, c.ml_status_at, c.cancelled_at, c.cancelled_reason,
       c.created_at, c.updated_at,
       a.name AS account_name, a.uid AS account_uid,
       COALESCE(a.custodian::text, (
         SELECT l.provider::text FROM platform.links l
          WHERE l.tenant_id = a.tenant_id
            AND l.entity_type = 'account' AND l.entity_id = a.id
            AND l.link_type = 'primary' AND l.provider = ANY(:custodians)
          ORDER BY l.provider
          LIMIT 1)) AS custodian,
       p.product_type, COALESCE(p.is_pension, false) AS is_pension,
       COALESCE(NULLIF(TRIM(CONCAT_WS(' ', ct.first_name, ct.surname)), ''), ct.name)
         AS client_name,
       -- THE CLIENT'S PUBLIC HANDLE, so a queue row can link to the person it names.
       --
       -- This projection carried `account_uid` and no client counterpart, which is the third
       -- time that exact shape has bitten: `docs/api.md` -> "Addressing" records it for the
       -- transactions projection and for `GET /documents/requests`, both of which linked the
       -- SERIAL as a result. A portal URL never carries one (`contact_id` is what the joins and
       -- filters key on; `uid` is the address), so a surface holding only the id either cannot
       -- link at all or links wrongly.
       ct.uid AS contact_uid,
       -- The client's own employer (0126) -- the DEFAULT for an employer contribution's
       -- contributor name. Read alongside `employer_name` rather than instead of it: the
       -- contribution's own copy is what was actually filed for that payment, and a client
       -- changing employer in March must not restate what was sent in February.
       ct.employer_name AS client_employer_name,
       e.uid AS expectation_uid, e.status AS expectation_status,
       e.due_date AS expectation_due_date, e.provider_reference AS expectation_reference,
       e.error AS expectation_error, e.amount::float8 AS expectation_amount,
       e.matched_at AS expectation_matched_at,
       e.matched_by IS NOT NULL AS matched_by_hand,
       -- The realised credit. Nullable on both paths: booked-ahead has no transaction yet,
       -- and a credit classified from the queue has one but never had an expectation.
       x.amount::float8 AS transaction_amount, x.transaction_date,
       x.currency AS transaction_currency,
       x.source_system AS transaction_source, x.source_transaction_id AS transaction_reference,
       x.source_type AS transaction_type_label,
       x.source_subtype AS transaction_description_code,
       -- HOW THE MONEY ARRIVED, in the custodian's own words: "BACS Credit", "Direct Debit",
       -- "Bank Transfer". Seccl publishes it on the raw row and SEI does not, so it is read out
       -- of the retained upstream object rather than given a column — NULL simply means the
       -- custodian did not say, and the panel omits the segment rather than printing "unknown".
       x.data ->> 'method' AS transaction_method,
       -- The adviser role is unique per contact (0245), so no tie-break ordering is needed.
       (SELECT u.name FROM crm.client_advisers ca
          JOIN auth.users u ON u.id = ca.adviser_id
         WHERE ca.contact_id = c.contact_id AND ca.role = 'adviser') AS adviser
FROM platform.contributions c
  LEFT JOIN platform.accounts a ON a.id = c.account_id
  LEFT JOIN platform.products p ON p.id = a.product_id
  LEFT JOIN crm.contacts ct ON ct.id = c.contact_id AND ct.deleted_at IS NULL
  LEFT JOIN platform.transaction_expectations e ON e.id = c.expectation_id
  LEFT JOIN platform.transactions x ON x.id = c.transaction_id
"""


async def list_contributions(
    session: AsyncSession,
    *,
    tenant_id: int,
    account_id: int | None = None,
    contact_id: int | None = None,
) -> list[dict[str, Any]]:
    """The firm's contributions, newest first, each carrying its reconciliation position.

    The three-way compare and the relief position are attached here rather than left to the
    portal: both are money arithmetic, and ``docs/contributions.md`` §3 already establishes
    that the client is not trusted with numbers that determine an HMRC reclaim. The portal
    renders what this says.
    """
    from app import matching

    clauses = []
    if account_id is not None:
        clauses.append("c.account_id = :a")
    if contact_id is not None:
        clauses.append("c.contact_id = :c")
    clause = "".join(f" AND {item}" for item in clauses)
    rows = (
        await session.execute(
            text(f"{_SELECT} WHERE c.tenant_id = :t{clause} ORDER BY c.created_at DESC"),
            {
                "t": tenant_id,
                "a": account_id,
                "c": contact_id,
                "custodians": list(custodians()),
            },
        )
    ).mappings()
    records = [dict(r) for r in rows]

    # Relief is paid by SEI as a monthly batch against the scheme, not per contribution, so it
    # is summed per account and read at that grain -- see matching.relief_credits.
    received = await matching.relief_credits(
        session,
        tenant_id=tenant_id,
        account_ids=sorted({int(r["account_id"]) for r in records if r.get("account_id")}),
    )
    for record in records:
        account = record.get("account_id")
        record["relief_received"] = (
            float(received[int(account)]) if account and int(account) in received else None
        )
        record["reconciliation"] = matching.reconcile(record)
        record["relief"] = matching.relief_position(record)
    return records


async def get_contribution(
    session: AsyncSession, *, tenant_id: int, uid: str
) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text(f"{_SELECT} WHERE c.tenant_id = :t AND c.uid = :u"),
                {"t": tenant_id, "u": uid, "custodians": list(custodians())},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


# ---- SEI context ---------------------------------------------------------------------------


async def sei_context(
    session: AsyncSession, *, tenant_id: int, account_id: int, category: str
) -> dict[str, Any]:
    """Everything SEI needs about this account, resolved from the links + provider config.

    The SEI account number and portfolio id live in ``platform.links`` (provider
    ``sei``); the firm id and the description/treatment codes live in the tenant's
    ``platform.tenant_platforms.config``. Any of them may be absent — that is what
    ``sei.missing_fields`` reports.
    """
    row = (
        (
            await session.execute(
                text(
                    "SELECT "
                    "  (SELECT external_id FROM platform.links "
                    "    WHERE provider = 'sei' AND entity_type = 'account' AND entity_id = :a "
                    "      AND link_type = 'primary' AND tenant_id = :t) AS sei_account_number, "
                    "  COALESCE( "
                    "    (SELECT external_id FROM platform.links "
                    "      WHERE provider = 'sei' AND entity_type = 'account' AND entity_id = :a "
                    "        AND link_type = 'portfolio' AND tenant_id = :t), "
                    # The portfolio lives on platform.portfolios. A link of type 'portfolio' is
                    # the older shape; the principal portfolio is the one a receipt is addressed to.
                    "    (SELECT external_id FROM platform.portfolios "
                    "      WHERE tenant_id = :t AND account_id = :a AND provider = 'sei' "
                    "      ORDER BY is_principal DESC NULLS LAST, id LIMIT 1) "
                    "  ) AS sei_portfolio_id, "
                    "  (SELECT config FROM platform.tenant_platforms "
                    "    WHERE tenant_id = :t AND provider = 'sei') AS config, "
                    "  (SELECT p.product_type::text FROM platform.accounts a "
                    "    JOIN platform.products p ON p.id = a.product_id "
                    "    WHERE a.id = :a AND a.tenant_id = :t) AS product_type, "
                    "  (SELECT COALESCE(p.is_pension, false) FROM platform.accounts a "
                    "    JOIN platform.products p ON p.id = a.product_id "
                    "    WHERE a.id = :a AND a.tenant_id = :t) AS is_pension"
                ),
                {"t": tenant_id, "a": account_id},
            )
        )
        .mappings()
        .one()
    )
    portfolio = row["sei_portfolio_id"]
    entity = portfolio_entity_id(portfolio)
    return {
        "sei_account_number": row["sei_account_number"],
        "sei_portfolio_id": entity if entity is not None else portfolio,
        "product_type": row["product_type"],
        "is_pension": bool(row["is_pension"]),
        **sei_integration.resolve_codes(
            row["config"],
            category,
            product_type=row["product_type"],
            is_pension=bool(row["is_pension"]),
        ),
    }


async def _record_seccl_expectation(
    session: AsyncSession,
    *,
    tenant_id: int,
    contribution: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Stamp the expectation from a Seccl dispatch.

    The vendor call itself is ``outbound.dispatch``.
    """
    expectation_id = contribution["expectation_id"]
    if result["status"] == "submitted":
        status = "instructed"
    elif result["status"] == "failed":
        status = "failed"
    else:
        status = "expected"
    await session.execute(
        text(
            "UPDATE platform.transaction_expectations SET status = :s, error = :e, "
            "  provider = 'seccl', provider_reference = COALESCE(:ref, provider_reference), "
            "  instructed_at = CASE WHEN :s = 'instructed' THEN now() ELSE instructed_at END "
            "WHERE id = :i AND tenant_id = :t"
        ),
        {
            "s": status,
            "e": None if status == "instructed" else result.get("detail"),
            "ref": result.get("id"),
            "i": expectation_id,
            "t": tenant_id,
        },
    )
    if result["status"] == "submitted":
        return {"status": "instructed", "activity_id": result.get("id"), "warnings": []}
    if result["status"] == "failed":
        return {"status": "failed", "detail": result.get("detail")}
    return {"status": "skipped", "detail": result.get("detail")}


async def lodge_expectation(
    session: AsyncSession, *, tenant_id: int, contribution: dict[str, Any]
) -> dict[str, Any]:
    """Instruct the account's custodian to expect this contribution, and record the outcome.

    Seccl accounts get a portfolio-transaction group; SEI accounts get an Anticipated Cash
    Receipt. One rail, the one on the account. Returns a report —
    ``{"status": instructed | blocked | unavailable | failed | skipped, ...}`` — rather than
    raising, so a caller that is creating a contribution can carry on. The explicit retry
    endpoint inspects the report and maps it onto a status code.
    """
    expectation_id = contribution.get("expectation_id")
    if not expectation_id:
        return {"status": "failed", "detail": "This contribution has no expectation to lodge."}

    dispatched = await outbound.dispatch(
        session,
        tenant_id=tenant_id,
        origin="contribution",
        origin_uid=str(contribution["uid"]),
        account_id=int(contribution["account_id"]),
    )
    if dispatched.get("rail") == "seccl":
        return await _record_seccl_expectation(
            session, tenant_id=tenant_id, contribution=contribution, result=dispatched
        )
    if dispatched.get("status") != "lodge":
        detail = dispatched.get("detail")
        rail = dispatched.get("rail")
        if dispatched.get("status") == "skipped" and rail and rail != "sei":
            detail = f"No contribution lodge for custodian {rail}."
        return {"status": dispatched.get("status", "skipped"), "detail": detail}

    context = await sei_context(
        session,
        tenant_id=tenant_id,
        account_id=int(contribution["account_id"]),
        category=str(contribution["category"]),
    )
    context.update(
        amount=contribution["amount"],
        currency=contribution.get("currency") or "GBP",
        tax_year=contribution.get("tax_year"),
        due_date=contribution.get("expectation_due_date") or contribution.get("contribution_date"),
        reference=contribution["uid"],
        category=contribution["category"],
    )

    async def _record(
        status: str,
        *,
        error: str | None,
        response: Any = None,
        reference: str | None = None,
        request: Any = None,
    ) -> None:
        await session.execute(
            text(
                "UPDATE platform.transaction_expectations SET status = :s, error = :e, "
                "  provider = 'sei', provider_reference = COALESCE(:ref, provider_reference), "
                "  request_payload = COALESCE(CAST(:req AS jsonb), request_payload), "
                "  response_payload = CAST(:resp AS jsonb), "
                "  instructed_at = CASE WHEN :s = 'instructed' THEN now() ELSE instructed_at END "
                "WHERE id = :i AND tenant_id = :t"
            ),
            {
                "s": status,
                "e": error,
                "ref": reference,
                "req": _json(request),
                "resp": _json(response),
                "i": expectation_id,
                "t": tenant_id,
            },
        )

    try:
        result = await sei_integration.create_anticipated_receipt(context)
    except ValueError as exc:  # missing reference data — a configuration gap, not a failure
        await _record("expected", error=str(exc))
        return {
            "status": "blocked",
            "detail": str(exc),
            "missing": sei_integration.missing_fields(context),
        }
    except sei_integration.SeiNotConfigured as exc:
        await _record("expected", error=str(exc))
        return {"status": "unavailable", "detail": str(exc)}
    except sei_integration.SeiUnavailable as exc:
        await _record("failed", error=str(exc))
        await outbound.on_sei_lodged(
            session,
            tenant_id=tenant_id,
            origin="contribution",
            origin_uid=str(contribution["uid"]),
            activity_id=None,
            failed=True,
            error=str(exc),
        )
        return {"status": "failed", "detail": str(exc)}

    await _record(
        "instructed",
        error=None,
        response=result,
        reference=result.get("activity_id"),
        request=result.get("request"),
    )
    await outbound.on_sei_lodged(
        session,
        tenant_id=tenant_id,
        origin="contribution",
        origin_uid=str(contribution["uid"]),
        activity_id=result.get("activity_id"),
    )
    sei_status = None
    if result.get("activity_id"):
        from app.integrations.sei.receipt_status import note_lodged

        sei_status = await note_lodged(
            session,
            activity_id=str(result["activity_id"]),
            event_id=str(result["event_id"]) if result.get("event_id") else None,
        )
    return {
        "status": "instructed",
        "activity_id": result.get("activity_id"),
        "warnings": result.get("warnings") or [],
        "sei": sei_status,
    }


def _json(value: Any) -> str | None:
    """Serialise a jsonb column value. ``default=str`` because the SEI payload carries dates
    and Decimals, and a serialisation error here would lose the audit trail of what we sent."""
    return None if value is None else json.dumps(value, default=str)


# ---- writes --------------------------------------------------------------------------------


async def _account(session: AsyncSession, *, tenant_id: int, account_id: int) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                text(
                    "SELECT a.id, a.contact_id, a.currency, p.product_type, p.is_pension, "
                    # The owning client's employer (0126), so booking an employer contribution
                    # can name the contributor without asking for it a second time. Joined on
                    # `deleted_at IS NULL` like every other read of this table.
                    "       ct.employer_name AS client_employer_name "
                    "FROM platform.accounts a "
                    "  LEFT JOIN platform.products p ON p.id = a.product_id "
                    "  LEFT JOIN crm.contacts ct "
                    "    ON ct.id = a.contact_id AND ct.deleted_at IS NULL "
                    "WHERE a.id = :a AND a.tenant_id = :t"
                ),
                {"a": account_id, "t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise LookupError("Account not found")
    product = str(row["product_type"] or "").upper()
    # THE SHELF FIRST, then the wrapper set — the same order the SIPP Payment drawer uses, so
    # what can be SAVED is what was SHOWN. A ticked `is_pension` settles it; the set decides
    # only for a shelf item that states nothing.
    #
    # An UNMAPPED product is allowed through, deliberately (same as beneficiaries._account):
    # `product_id` is NULL for any account the pipeline could not resolve a product for, and
    # refusing there would mean a real SIPP whose mapping is missing cannot be booked into.
    if row["is_pension"]:
        return dict(row)
    if product and product not in PENSION_PRODUCTS:
        raise ValueError("Contributions apply to pension accounts only.")
    return dict(row)


async def create_contribution(
    session: AsyncSession,
    *,
    tenant_id: int,
    created_by: str | None,
    account_id: int,
    category: str,
    amount: float | Decimal,
    frequency: str = "single",
    paid_by: str = "client",
    tax_year: str | None = None,
    contribution_date: date | None = None,
    due_date: date | None = None,
    prefunded_ras_amount: float | Decimal | None = None,
    employer: dict[str, Any] | None = None,
    note: str | None = None,
    lodge: bool = True,
) -> dict[str, Any]:
    """Book a contribution, raise its expectation, and (optionally) lodge it at SEI.

    Raises ``ValueError`` for a bad request (→ 400) and ``LookupError`` if the account is not
    the caller's firm's (→ 404). The SEI outcome is reported in the returned ``sei`` block, not
    raised — see the module docstring.
    """
    if category not in CATEGORIES:
        raise ValueError(f"Unknown contribution category {category!r}.")
    if frequency not in FREQUENCIES:
        raise ValueError(f"Unknown frequency {frequency!r}.")
    if paid_by not in PAID_BY:
        raise ValueError(f"Unknown payer {paid_by!r}.")
    amount_dec = _dec(amount)
    if amount_dec <= 0:
        raise ValueError("Enter an amount greater than zero.")

    account = await _account(session, tenant_id=tenant_id, account_id=account_id)

    employer = employer or {}
    # The client's own employer is the default (0126), exactly as in `categorise`: who somebody
    # works for is a fact about them, so it is stated once on their record rather than retyped
    # into every booking. An explicit block still wins — a one-off employer contribution from a
    # company the client does not work for is a real thing.
    employer_name = employer.get("employer_name") or account.get("client_employer_name")
    if category == "employer_contribution" and not employer_name:
        raise ValueError(
            "An employer contribution needs the employer's name, and this client has none on "
            "file — add it under Employment on their client record."
        )

    # `on` is the basis for the tax year and the expectation's due date, and today is the right
    # basis for both: the tax year a booking belongs to is the one it is made in, and money with no
    # stated due date is wanted now. The COLUMN, though, keeps whatever the caller gave — NULL
    # included. Storing today's date as the contribution date would be inventing a fact: on a
    # book-ahead the payment has not been made yet, and `matching.link` stamps the real date off
    # the credit when it lands. The drawer therefore stops asking (docs/contributions.md §2).
    on = contribution_date or date.today()
    tax_year = tax_year or tax_year_of(on)
    contact_id = account["contact_id"]
    currency = account["currency"] or "GBP"

    # The expectation first: the contribution references it, and a contribution without a
    # forecast cannot be matched to the credit when it lands.
    expectation_id = (
        await session.execute(
            text(
                "INSERT INTO platform.transaction_expectations "
                "  (uid, tenant_id, account_id, contact_id, kind, direction, amount, currency, "
                "   due_date, status, provider, created_by) "
                "VALUES (:uid, :t, :a, :c, 'contribution', 'in', :amt, :cur, :due, 'expected', "
                "        'sei', (SELECT id FROM auth.users WHERE workos_user_id = :by)) "
                "RETURNING id"
            ),
            {
                "uid": new_id(),
                "t": tenant_id,
                "a": account_id,
                "c": contact_id,
                "amt": amount_dec,
                "cur": currency,
                "due": due_date or on,
                "by": created_by,
            },
        )
    ).scalar_one()

    uid = new_id()
    await session.execute(
        text(
            "INSERT INTO platform.contributions "
            "  (uid, tenant_id, account_id, contact_id, expectation_id, category, amount, "
            "   gross_amount, relief_amount, prefunded_ras_amount, currency, tax_year, "
            "   frequency, paid_by, contribution_date, employer_name, address_line1, "
            "   address_line2, address_line3, city, county, postcode, phone, email, "
            "   status, note, created_by) "
            "VALUES (:uid, :t, :a, :c, :exp, :cat, :amt, :gross, :relief, :ras, :cur, :ty, "
            "        :freq, :paid_by, :on, :emp, :l1, :l2, :l3, :city, :county, :pc, :phone, "
            "        :email, 'awaiting_confirmation', :note, "
            "        (SELECT id FROM auth.users WHERE workos_user_id = :by))"
        ),
        {
            "uid": uid,
            "t": tenant_id,
            "a": account_id,
            "c": contact_id,
            "exp": expectation_id,
            "cat": category,
            "amt": amount_dec,
            "gross": gross_of(amount_dec, category),
            "relief": relief_of(amount_dec, category),
            "ras": _dec(prefunded_ras_amount) if prefunded_ras_amount else None,
            "cur": currency,
            "ty": tax_year,
            "freq": frequency,
            "paid_by": paid_by,
            "on": contribution_date,
            "emp": employer_name,
            "l1": employer.get("address_line1"),
            "l2": employer.get("address_line2"),
            "l3": employer.get("address_line3"),
            "city": employer.get("city"),
            "county": employer.get("county"),
            "pc": employer.get("postcode"),
            "phone": employer.get("phone"),
            "email": employer.get("email"),
            "note": note,
            "by": created_by,
        },
    )

    record = await get_contribution(session, tenant_id=tenant_id, uid=uid)
    assert record is not None  # noqa: S101 - just inserted, same transaction
    await outbound.record_contribution(session, tenant_id=tenant_id, contribution=record)
    report: dict[str, Any] = {"status": "skipped", "detail": "Not lodged at SEI."}
    if lodge:
        report = await lodge_expectation(session, tenant_id=tenant_id, contribution=record)
        record = await get_contribution(session, tenant_id=tenant_id, uid=uid)
        assert record is not None  # noqa: S101
    return {**record, "sei": report}


async def categorise(
    session: AsyncSession,
    *,
    tenant_id: int,
    uid: str,
    category: str | None = None,
    tax_year: str | None = None,
    frequency: str | None = None,
    paid_by: str | None = None,
    employer: dict[str, Any] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Set or amend the HMRC classification, recomputing the relief arithmetic.

    Only up to ``confirmed``: past that the classification has been acted on downstream and
    changing it here would leave the back office holding a different answer from us.
    """
    current = await get_contribution(session, tenant_id=tenant_id, uid=uid)
    if current is None:
        raise LookupError("Contribution not found")
    # A DISPOSAL IS ALWAYS REVERSIBLE. `not_a_contribution` is written AND cancelled in the same
    # call, so it lands on a terminal status that `CLASSIFIABLE` excludes — which made an adviser's
    # mis-click permanent: the credit was gone from the queue (that is the point) and the only way
    # back was SQL. `transfer_in` has the same shape one step later.
    #
    # Reopening one is safe in a way reopening a recorded contribution is not, and for a stated
    # reason rather than a convenient one: nothing downstream ever held it. No `cam_contribution`
    # exists (`UNPUSHABLE`), no expectation was lodged against it, and no relief was reclaimed — so
    # there is no back-office record to contradict, which is the entire argument the status gate is
    # built on. Belt as well as braces: a row that somehow reached Morgan Lloyd is refused anyway.
    reopening = current["category"] in DISPOSALS and not current["ml_contribution_id"]
    if current["status"] not in CLASSIFIABLE and not reopening:
        raise ContributionConflict(
            f"This contribution is at '{current['status']}' — past classification. "
            "Reverse the downstream record before reclassifying it."
        )
    if category is not None and category not in CATEGORIES:
        raise ValueError(f"Unknown contribution category {category!r}.")
    if frequency is not None and frequency not in FREQUENCIES:
        raise ValueError(f"Unknown frequency {frequency!r}.")
    if paid_by is not None and paid_by not in PAID_BY:
        raise ValueError(f"Unknown payer {paid_by!r}.")

    employer = employer or {}
    resolved_category = category or current["category"]
    # THE CLIENT'S OWN EMPLOYER IS THE DEFAULT (0126). The caller's block wins where it gives one,
    # then whatever was already filed for this payment, then the client record. The classify panel
    # no longer sends a block at all — an employer is a fact about the PERSON, so it is stated once
    # on their record instead of retyped per credit — and this is what makes that safe: the DB
    # CHECK (`contributions_employer_named`) still demands a name at `confirmed`, and it is
    # satisfied from the place the name actually lives rather than refused for want of a form.
    employer_name = (
        employer.get("employer_name")
        or current["employer_name"]
        or current.get("client_employer_name")
    )
    if resolved_category == "employer_contribution" and not employer_name:
        raise ValueError(
            "An employer contribution needs the employer's name, and this client has none on "
            "file — add it under Employment on their client record."
        )

    await session.execute(
        text(
            "UPDATE platform.contributions SET "
            "  category = :cat, tax_year = COALESCE(:ty, tax_year), "
            "  frequency = COALESCE(:freq, frequency), paid_by = COALESCE(:paid_by, paid_by), "
            "  gross_amount = :gross, relief_amount = :relief, "
            "  employer_name = :emp, address_line1 = :l1, address_line2 = :l2, "
            "  address_line3 = :l3, city = :city, county = :county, postcode = :pc, "
            "  phone = :phone, email = :email, note = COALESCE(:note, note), "
            # CLASSIFYING AS A DISPOSAL CANCELS IT, and classifying back out UN-cancels it.
            #
            # `classify_transaction` already did the first half on CREATE — it is what stops a
            # `not_a_contribution` credit resurfacing in the queue for ever — and `categorise` did
            # neither, so reclassifying an existing row TO `not_a_contribution` left it sitting in
            # the pipeline as live work, and reclassifying one back OUT left `cancelled_at` and the
            # reason standing on a row now reading `confirmed`. The panel renders that reason, so
            # the record said "Cancelled — classified as not a contribution" about a personal
            # contribution somebody had just corrected it to.
            "  status = CASE WHEN :disposal THEN 'cancelled' ELSE 'confirmed' END, "
            "  cancelled_at = CASE WHEN :disposal THEN COALESCE(cancelled_at, now()) END, "
            "  cancelled_reason = CASE WHEN :disposal "
            "                          THEN COALESCE(cancelled_reason, :disposal_reason) END "
            "WHERE uid = :u AND tenant_id = :t"
        ),
        {
            "cat": resolved_category,
            "ty": tax_year,
            "freq": frequency,
            "paid_by": paid_by,
            "gross": gross_of(current["amount"], resolved_category),
            "relief": relief_of(current["amount"], resolved_category),
            "emp": employer_name,
            "l1": employer.get("address_line1", current["address_line1"]),
            "l2": employer.get("address_line2", current["address_line2"]),
            "l3": employer.get("address_line3", current["address_line3"]),
            "city": employer.get("city", current["city"]),
            "county": employer.get("county", current["county"]),
            "pc": employer.get("postcode", current["postcode"]),
            "phone": employer.get("phone", current["phone"]),
            "email": employer.get("email", current["email"]),
            "note": note,
            "disposal": resolved_category in DISPOSALS,
            "disposal_reason": (
                "Classified as not a contribution by the adviser."
                if resolved_category == "not_a_contribution"
                else "Classified as a transfer in by the adviser."
            ),
            "u": uid,
            "t": tenant_id,
        },
    )
    # A DISPOSAL CLOSES ITS FORECAST TOO, for the reason `cancel()` states about its own: "an open
    # expectation for money that is no longer coming keeps showing in the matcher's queue and in
    # 'awaiting payment' totals forever." This branch cancels the contribution inline rather than
    # going through `cancel()`, and it took the row and left the expectation — so classifying a
    # credit as not-a-contribution retired it from the queue and kept it in the forecast.
    if resolved_category in DISPOSALS and current.get("expectation_id"):
        await session.execute(
            text(
                "UPDATE platform.transaction_expectations SET status = 'cancelled' "
                "WHERE id = :e AND tenant_id = :t AND status IN ('expected', 'instructed')"
            ),
            {"e": current["expectation_id"], "t": tenant_id},
        )
    # The Graphene pending deposit is the same live work. `cancel()` already calls
    # `outbound.cancel_origin`; this path skipped it, so a booked contribution reclassified as
    # not-a-contribution left a deposit on the ledger with no staff path to clear it (`cancel()`
    # then 409s because the contribution is already cancelled). Classifying back OUT restores it.
    if resolved_category in DISPOSALS:
        await outbound.cancel_origin(
            session, tenant_id=tenant_id, origin="contribution", origin_uid=uid
        )
    elif reopening:
        await outbound.restore_cancelled(
            session, tenant_id=tenant_id, origin="contribution", origin_uid=uid
        )
    record = await get_contribution(session, tenant_id=tenant_id, uid=uid)
    assert record is not None  # noqa: S101 - existed a statement ago, same transaction
    return record


async def classify_transaction(
    session: AsyncSession,
    *,
    tenant_id: int,
    created_by: str | None,
    transaction_id: int,
    category: str,
    frequency: str = "single",
    paid_by: str = "client",
    tax_year: str | None = None,
    prefunded_ras_amount: float | Decimal | None = None,
    employer: dict[str, Any] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Turn a credit that already landed into a contribution — path A of the flow.

    The mirror image of :func:`create_contribution`: there the contribution comes first and the
    money is forecast, here the money came first and the domain record is created against it.
    So there is deliberately **no expectation** — inventing a forecast for money that has
    already arrived would put a row in the matcher's queue that can never be anything but
    self-fulfilled, and would make the "awaiting payment" count wrong.

    The amount is the credit's, not the caller's. An adviser classifies what arrived; they do
    not get to say a £450 credit was a £500 contribution, because that is the number a
    basic-rate reclaim is computed from.

    Raises ``ValueError`` for a bad request (→ 400) and ``LookupError`` when the transaction is
    not the caller's firm's or is already claimed (→ 404 / 409 at the router).
    """
    from app import matching

    if category not in CATEGORIES:
        raise ValueError(f"Unknown contribution category {category!r}.")
    if frequency not in FREQUENCIES:
        raise ValueError(f"Unknown frequency {frequency!r}.")
    if paid_by not in PAID_BY:
        raise ValueError(f"Unknown payer {paid_by!r}.")

    credit = await matching.get_transaction(
        session, tenant_id=tenant_id, transaction_id=transaction_id
    )
    if credit is None:
        raise LookupError("Transaction not found")

    claimed = (
        await session.execute(
            text(
                "SELECT uid FROM platform.contributions "
                "WHERE transaction_id = :x AND tenant_id = :t"
            ),
            {"x": transaction_id, "t": tenant_id},
        )
    ).scalar_one_or_none()
    if claimed:
        raise AlreadyClassified(f"This credit is already recorded as contribution {claimed}.")

    amount_dec = _dec(credit["amount"])
    if amount_dec <= 0:
        raise ValueError("Only incoming money can be classified as a contribution.")

    employer = employer or {}
    # The client's own employer, for the same reason as `categorise` (0126): a credit classified
    # from the queue is money that has already arrived, and asking who the payer was — once per
    # payment, of an adviser who is looking at a bank reference — is asking the wrong person a
    # question the client record already answers.
    employer_name = employer.get("employer_name") or credit.get("client_employer_name")
    if category == "employer_contribution" and not employer_name:
        raise ValueError(
            "An employer contribution needs the employer's name, and this client has none on "
            "file — add it under Employment on their client record."
        )

    on = credit.get("transaction_date") or date.today()
    tax_year = tax_year or tax_year_of(on)

    # A DISPOSAL is the adviser saying this payment should never have been in the contribution
    # queue. It still gets a row -- that is the whole point, so the credit stops resurfacing --
    # but it goes straight to the cancelled terminal rather than sitting in the pipeline
    # pretending to be work.
    #
    # BOTH members, matching `categorise`. This used to cancel only `not_a_contribution`, so a
    # credit classified as `transfer_in` landed `confirmed` / `not_lodged`, inflated the KPI,
    # and could not be pushed (`UNPUSHABLE`).
    disposed = category in DISPOSALS
    uid = new_id()
    await session.execute(
        text(
            "INSERT INTO platform.contributions "
            "  (uid, tenant_id, account_id, contact_id, transaction_id, category, amount, "
            "   gross_amount, relief_amount, prefunded_ras_amount, currency, tax_year, "
            "   frequency, paid_by, contribution_date, employer_name, address_line1, "
            "   address_line2, address_line3, city, county, postcode, phone, email, "
            "   status, cancelled_at, cancelled_reason, note, created_by) "
            "VALUES (:uid, :t, :a, :c, :x, :cat, :amt, :gross, :relief, :ras, :cur, :ty, "
            "        :freq, :paid_by, :on, :emp, :l1, :l2, :l3, :city, :county, :pc, :phone, "
            "        :email, :status, "
            # now() as SQL, not as a bound string: 'now()' is not a timestamptz literal
            # ('now' is, 'now()' is not) and would fail the cast at insert time.
            "        CASE WHEN :disposed THEN now() END, :cancelled_reason, :note, "
            "        (SELECT id FROM auth.users WHERE workos_user_id = :by))"
        ),
        {
            "uid": uid,
            "t": tenant_id,
            "a": credit["account_id"],
            "c": credit.get("account_contact_id"),
            "x": transaction_id,
            "cat": category,
            "amt": amount_dec,
            "gross": gross_of(amount_dec, category),
            "relief": relief_of(amount_dec, category),
            "ras": _dec(prefunded_ras_amount) if prefunded_ras_amount else None,
            "cur": credit.get("currency") or "GBP",
            "ty": tax_year,
            "freq": frequency,
            "paid_by": paid_by,
            "on": on,
            "emp": employer_name,
            "l1": employer.get("address_line1"),
            "l2": employer.get("address_line2"),
            "l3": employer.get("address_line3"),
            "city": employer.get("city"),
            "county": employer.get("county"),
            "pc": employer.get("postcode"),
            "phone": employer.get("phone"),
            "email": employer.get("email"),
            "status": "cancelled" if disposed else "confirmed",
            "disposed": disposed,
            "cancelled_reason": (
                "Classified as not a contribution by the adviser."
                if category == "not_a_contribution"
                else "Classified as a transfer in by the adviser."
                if disposed
                else None
            ),
            "note": note,
            "by": created_by,
        },
    )
    record = await get_contribution(session, tenant_id=tenant_id, uid=uid)
    assert record is not None  # noqa: S101 - just inserted, same transaction
    return record


async def cancel(session: AsyncSession, *, tenant_id: int, uid: str, reason: str) -> dict[str, Any]:
    """Stop a contribution, recording why.

    Refused once the relief has been claimed: past ``ras_applied`` there is a reclaim in flight
    at HMRC, and cancelling our record without reversing that leaves the two disagreeing. The
    Morgan Lloyd side is stopped separately (``POST …/stop-morgan-lloyd``) because it can fail
    independently and the local record must not be held hostage to it.
    """
    if not (reason or "").strip():
        raise ValueError("Say why the contribution is being cancelled.")
    current = await get_contribution(session, tenant_id=tenant_id, uid=uid)
    if current is None:
        raise LookupError("Contribution not found")
    if current["status"] == "cancelled":
        raise AlreadyCancelled("This contribution is already cancelled.")
    if pipeline_index(current["status"]) >= STATUSES.index("ras_applied"):
        raise ContributionConflict(
            f"This contribution is at '{current['status']}' — a relief reclaim is already in "
            "flight. Reverse it with the scheme administrator before cancelling."
        )

    await session.execute(
        text(
            "UPDATE platform.contributions "
            "SET status = 'cancelled', cancelled_at = now(), cancelled_reason = :why "
            "WHERE uid = :u AND tenant_id = :t"
        ),
        {"why": reason.strip(), "u": uid, "t": tenant_id},
    )
    # The forecast goes with it: an open expectation for money that is no longer coming keeps
    # showing in the matcher's queue and in "awaiting payment" totals forever.
    if current.get("expectation_id"):
        await session.execute(
            text(
                "UPDATE platform.transaction_expectations SET status = 'cancelled' "
                "WHERE id = :e AND tenant_id = :t AND status IN ('expected', 'instructed')"
            ),
            {"e": current["expectation_id"], "t": tenant_id},
        )
    await outbound.cancel_origin(
        session, tenant_id=tenant_id, origin="contribution", origin_uid=uid
    )
    record = await get_contribution(session, tenant_id=tenant_id, uid=uid)
    assert record is not None  # noqa: S101
    return record


async def set_amount(
    session: AsyncSession, *, tenant_id: int, uid: str, amount: float | Decimal
) -> dict[str, Any]:
    """Restate the amount, recomputing gross and relief.

    Only reachable behind an accepted ``cam_updatecontributionapplication``: the vendor is the
    one that decides whether an application may still be amended, so this deliberately does not
    re-check the pipeline stage. Recomputing the pair is the point — leaving ``gross_amount``
    and ``relief_amount`` at the old figures is how a reclaim ends up computed from a number
    nobody holds any more.
    """
    amount_dec = _dec(amount)
    if amount_dec <= 0:
        raise ValueError("Enter an amount greater than zero.")
    current = await get_contribution(session, tenant_id=tenant_id, uid=uid)
    if current is None:
        raise LookupError("Contribution not found")
    await session.execute(
        text(
            "UPDATE platform.contributions "
            "SET amount = :amt, gross_amount = :gross, relief_amount = :relief "
            "WHERE uid = :u AND tenant_id = :t"
        ),
        {
            "amt": amount_dec,
            "gross": gross_of(amount_dec, current["category"]),
            "relief": relief_of(amount_dec, current["category"]),
            "u": uid,
            "t": tenant_id,
        },
    )
    # The forecast follows: an expectation still quoting the old figure would make the matcher
    # look for money that is not coming, and the three-way reconciliation report a false break.
    if current.get("expectation_id"):
        await session.execute(
            text(
                "UPDATE platform.transaction_expectations SET amount = :amt "
                "WHERE id = :e AND tenant_id = :t AND status IN ('expected', 'instructed')"
            ),
            {"amt": amount_dec, "e": current["expectation_id"], "t": tenant_id},
        )
    record = await get_contribution(session, tenant_id=tenant_id, uid=uid)
    assert record is not None  # noqa: S101
    return record


async def record_ml_status(
    session: AsyncSession, *, tenant_id: int, uid: str, ml_status: int
) -> dict[str, Any]:
    """Mirror Morgan Lloyd's ``cam_status`` and advance the pipeline to match.

    **Monotonic.** A provider read that arrives late must not drag a contribution backwards —
    a nightly refresh returning Application on a contribution we have already seen Received
    would otherwise undo real progress. So the pipeline only moves forward; the mirror itself
    always records what ML actually said, because that is the point of a mirror.

    Stopped is the exception and moves the record to ``cancelled`` regardless of position: the
    scheme administrator stopping a contribution is authoritative, not a stale read.
    """
    if ml_status not in ML_STATUS_LABELS:
        raise ValueError(f"Unknown Morgan Lloyd contribution status {ml_status!r}.")
    current = await get_contribution(session, tenant_id=tenant_id, uid=uid)
    if current is None:
        raise LookupError("Contribution not found")

    target = ML_STATUS_TO_PIPELINE.get(ml_status)
    status = current["status"]
    if target == "cancelled":
        status = "cancelled"
    elif target and pipeline_index(target) > pipeline_index(status):
        status = target

    cancelling = status == "cancelled" and current["status"] != "cancelled"
    await session.execute(
        text(
            "UPDATE platform.contributions "
            "SET ml_status = :ml, ml_status_at = now(), status = :s, "
            "    cancelled_at = CASE WHEN :cancelling THEN now() ELSE cancelled_at END, "
            "    cancelled_reason = CASE WHEN :cancelling THEN :why ELSE cancelled_reason END "
            "WHERE uid = :u AND tenant_id = :t"
        ),
        {
            "ml": ml_status,
            "s": status,
            "cancelling": cancelling,
            "why": "Stopped at Morgan Lloyd.",
            "u": uid,
            "t": tenant_id,
        },
    )
    record = await get_contribution(session, tenant_id=tenant_id, uid=uid)
    assert record is not None  # noqa: S101
    return record


async def set_status(
    session: AsyncSession, *, tenant_id: int, uid: str, status: str
) -> dict[str, Any]:
    """Move a contribution to another pipeline stage (hold, flag for manual, reconcile)."""
    if status not in ALL_STATUSES:
        raise ValueError(f"Unknown status {status!r}.")
    if status == "cancelled":
        # The CHECK requires a reason, and this endpoint has nowhere to carry one.
        raise ValueError("Cancel a contribution with the cancel action, which records a reason.")
    updated = (
        await session.execute(
            text(
                "UPDATE platform.contributions SET status = :s "
                "WHERE uid = :u AND tenant_id = :t RETURNING id"
            ),
            {"s": status, "u": uid, "t": tenant_id},
        )
    ).scalar_one_or_none()
    if updated is None:
        raise LookupError("Contribution not found")
    record = await get_contribution(session, tenant_id=tenant_id, uid=uid)
    assert record is not None  # noqa: S101
    return record


async def ml_context(
    session: AsyncSession, *, tenant_id: int, contribution: dict[str, Any]
) -> dict[str, Any]:
    """The contribution plus the Morgan Lloyd ids it must be addressed by.

    The scheme reference hangs off the account and the contact GUID off the client, both in
    ``platform.links`` — the same resolution ``crm.py::account_drawdown_session``
    does for drawdown.
    """
    row = (
        (
            await session.execute(
                text(
                    "SELECT "
                    "  (SELECT external_id FROM platform.links "
                    "    WHERE provider = 'morgan-lloyd' AND entity_type = 'account' "
                    "      AND entity_id = :a AND link_type = 'primary' AND tenant_id = :t"
                    "  ) AS ml_scheme_ref, "
                    "  (SELECT external_id FROM platform.links "
                    "    WHERE provider = 'morgan-lloyd' AND entity_type = 'contact' "
                    "      AND entity_id = :c AND link_type = 'primary' AND tenant_id = :t"
                    "  ) AS ml_contact_id"
                ),
                {"a": contribution["account_id"], "c": contribution["contact_id"], "t": tenant_id},
            )
        )
        .mappings()
        .one()
    )
    return {
        **contribution,
        "ml_scheme_ref": row["ml_scheme_ref"],
        "ml_contact_id": row["ml_contact_id"],
        "investor_ref": contribution.get("account_uid"),
    }


async def mark_ml_recorded(
    session: AsyncSession,
    *,
    tenant_id: int,
    uid: str,
    ml_contribution_id: str,
    ml_status: int = ML_STATUS_APPLICATION,
) -> dict[str, Any]:
    """Record the Morgan Lloyd GUID and the ``cam_status`` the record was created at.

    ``ml_status`` is not assumed to be Application: ``build_contribution`` sends Received for a
    contribution already matched to a credit ("For an Investment Contribution set status
    Received + frequency Single"), and mirroring Application for one of those would make the
    workbench offer an amend that Morgan Lloyd will refuse.

    The pipeline stage follows from the status rather than being hardcoded to ``ml_recorded``,
    so a contribution created at Received lands on ``ras_received`` in one step instead of
    waiting for a refresh to catch up with what we ourselves sent.
    """
    stage = ML_STATUS_TO_PIPELINE.get(ml_status, "ml_recorded")
    await session.execute(
        text(
            "UPDATE platform.contributions SET ml_contribution_id = :ml, "
            "  ml_recorded_at = now(), ml_status = :mls, ml_status_at = now(), status = :s "
            "WHERE uid = :u AND tenant_id = :t"
        ),
        {
            "ml": ml_contribution_id,
            "mls": ml_status,
            "s": stage,
            "u": uid,
            "t": tenant_id,
        },
    )
    record = await get_contribution(session, tenant_id=tenant_id, uid=uid)
    assert record is not None  # noqa: S101
    await outbound.on_ml_recorded(
        session,
        tenant_id=tenant_id,
        origin="contribution",
        origin_uid=uid,
        ml_id=ml_contribution_id,
    )
    return record
