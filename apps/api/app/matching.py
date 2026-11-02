"""Matching realised money to the forecast, and finding the credits nobody has classified.

The piece ``docs/contributions.md`` §7 listed as missing: "Nothing sets
``transactions.expectation_id`` — the credit arrives in the lake, and the matcher (amount/date
tolerance against open expectations) is the next piece." Without it neither half of the flow
could complete:

    A. client sends money -> SEI transaction -> adviser CLASSIFIES it -> ...
    B. adviser books a contribution (SEI ACR) -> client sends money -> transaction MATCHED -> ...

Path A needs a queue of credits with no domain record against them; path B needs the join from
an open expectation to the credit that fulfilled it. Both are here because they are the same
question asked from opposite ends, and running them off two different definitions of "already
accounted for" is how a credit ends up both matched and sitting in the queue.

**Auto-matching is exact-amount only, deliberately.** The tempting design is a tolerance band —
match anything within a pound or a percent. This does not do that. A near-match is *offered*
(:func:`candidates_for`) and a human confirms it; only an unambiguous exact-amount credit
inside the date window is applied automatically. The reason is that the consequence of a wrong
match is not a wrong number on a screen: the contribution advances, gets recorded to Morgan
Lloyd as Received, and a basic-rate relief reclaim is made to HMRC against a payment that was
actually something else. Ambiguity is cheap to resolve and expensive to guess.

**One writer for `expectation_id` on inbound credits.** ``platform.transactions`` is otherwise
an immutable synced mirror (the materialiser upserts it keyed on ``source_system +
source_transaction_id`` and never touches ``expectation_id``), so this module is the only thing
that writes that column on a feed row. Graphene-originated pending rows (``source_system =
graphene``, ``app.outbound``) set it at insert so they cannot match themselves. That is what
keeps a re-sync from clearing a match.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import outbound, providers
from app.integrations.sei import reference as sei_reference

#: How far either side of an expectation's due date a credit may land and still be considered
#: the same money. BACS is three working days, a client who pays "on the 1st" may pay on the
#: 3rd, and a due date set for a Friday settles on the Monday. Fourteen days covers all of that
#: without spanning a monthly contribution's neighbour — which is the real bound, because two
#: identical monthly contributions a month apart are exactly the ambiguity this must not
#: resolve by guessing.
MATCH_WINDOW_DAYS = 14

#: Transaction types that are money arriving. ``deposit`` is SEI's Cash Receipt (type 6) and
#: Seccl's Payment/In; ``transfer_in`` is a ceding scheme's assets landing.
INCOMING_TYPES = ("deposit", "transfer_in")

#: Product types where an incoming credit could be a pension contribution at all. Mirrors
#: ``app.contributions.PENSION_PRODUCTS``.
PENSION_PRODUCTS = ("SIPP", "PENSION", "SSAS", "TPPENSION")


def _dec(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


# ---- the unclassified queue (path A) --------------------------------------------------------

#: A credit is "unclassified" when no domain record claims it AND no expectation was matched to
#: it. Both halves are needed: a contribution booked ahead (path B) is matched via the
#: expectation and has `transaction_id` set by this module, while one classified from the queue
#: (path A) has `transaction_id` set directly and never had an expectation.
_UNCLASSIFIED = """
SELECT t.id, t.source_system, t.source_transaction_id, t.type,
       t.source_type, t.source_subtype,
       t.amount::float8 AS amount, t.currency,
       t.transaction_date, t.settlement_date, t.posted_at, t.status,
       t.account_id, t.data,
       -- How the money arrived, in the custodian's own words ("BACS Credit", "Direct Debit").
       -- Seccl publishes it on the raw row and SEI does not, so NULL means "the custodian did not
       -- say" and the panel omits the segment rather than printing "unknown".
       t.data ->> 'method' AS method,
       a.uid AS account_uid, a.name AS account_name,
       p.product_type,
       a.contact_id,
       ct.uid AS contact_uid,
       COALESCE(NULLIF(TRIM(CONCAT_WS(' ', ct.first_name, ct.surname)), ''), ct.name)
         AS client_name,
       -- The client's own employer (0126). Read here so the classify panel can say "this client
       -- has no employer on file" BEFORE an employer classification is attempted, rather than the
       -- adviser discovering it as a 400 -- and so the fix it names (the client record) is the
       -- place the value actually lives.
       ct.employer_name AS client_employer_name,
       -- The adviser role is unique per contact (0245), so no tie-break ordering is needed.
       (SELECT u.name FROM crm.client_advisers ca
          JOIN auth.users u ON u.id = ca.adviser_id
         WHERE ca.contact_id = a.contact_id AND ca.role = 'adviser') AS adviser
FROM platform.transactions t
  JOIN platform.accounts a ON a.id = t.account_id AND a.tenant_id = t.tenant_id
  LEFT JOIN platform.products p ON p.id = a.product_id
  LEFT JOIN crm.contacts ct ON ct.id = a.contact_id AND ct.deleted_at IS NULL
WHERE t.tenant_id = :t
  AND t.type = ANY (:types)
  AND t.amount > 0
  AND t.source_system = ANY(:feed)
  AND t.expectation_id IS NULL
  AND NOT EXISTS (SELECT 1 FROM platform.contributions c WHERE c.transaction_id = t.id)
  AND NOT EXISTS (SELECT 1 FROM platform.transfers tr WHERE tr.transaction_id = t.id)
"""


def _shape_credit(row: dict[str, Any]) -> dict[str, Any]:
    """One credit, plus what SEI's own reference codes say about it.

    The suggestion is presentational: it pre-selects a category in the classify panel so the
    common case is one click, and it is never stored without the adviser confirming. SEI's
    description code says how the custodian labelled the money; the HMRC decision is ours.
    """
    credit = dict(row)
    credit["is_relief_payment"] = sei_reference.is_relief_payment(credit)
    credit["suggested_category"] = sei_reference.suggested_category(credit)
    credit["provider_description"] = sei_reference.describe(credit)
    return credit


async def unclassified_credits(
    session: AsyncSession,
    *,
    tenant_id: int,
    account_id: int | None = None,
    pension_only: bool = True,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Incoming money with no domain record against it — the front of the workbench.

    ``pension_only`` keeps the queue to accounts where a credit could be a contribution. It is
    a parameter rather than a constant because the same queue serves transfers, where an ISA or
    GIA credit is perfectly legitimate.
    """
    clauses = ""
    if pension_only:
        # An account with no resolved product is included, not excluded: `product_id` was NULL
        # on every account until 0055 fixed the platforms registry, and a queue that silently
        # drops those is indistinguishable from one with no work in it.
        # `::text` because product_type is the platform.product_type ENUM, and upper() has no
        # overload for it -- an uncast call is an UndefinedFunction at runtime, not a plan-time
        # type error, so it only shows when the query actually runs.
        clauses += " AND (p.product_type IS NULL OR UPPER(p.product_type::text) = ANY (:products))"
    if account_id is not None:
        clauses += " AND t.account_id = :a"
    rows = (
        await session.execute(
            text(
                f"{_UNCLASSIFIED}{clauses} "
                "ORDER BY COALESCE(t.transaction_date, t.synced_at::date) DESC, t.id DESC "
                "LIMIT :limit"
            ),
            {
                "t": tenant_id,
                "types": list(INCOMING_TYPES),
                "products": list(PENSION_PRODUCTS),
                "a": account_id,
                "limit": max(1, min(limit, 1000)),
                "feed": list(providers.PROVIDERS),
            },
        )
    ).mappings()
    return [_shape_credit(dict(row)) for row in rows]


# ---- detected transfers ---------------------------------------------------------------------
#
# The transfers workbench showed nothing, and the reason was not that there is no transfer data:
# `platform.transfers` only ever holds transfers WE booked, and nobody has booked one. Meanwhile
# Seccl's conformed model publishes `is_pension_transfer`, `transfer_status`,
# `previous_provider` / `previous_provider_name` and maps `Product|Stock|Cash Transfer` +
# movement `In` to `type = 'transfer_in'` -- and the materialiser keeps the whole upstream row in
# `platform.transactions.data`. So the custodian's own transfers were in OLTP the whole time,
# just not on a surface that read them.
#
# These are DETECTED, not booked: read-only rows the workbench shows beside the real saga so a
# firm can see the transfer traffic it already has. Booking one from an account still writes a
# `platform.transfers` row with an expectation, which is what gets a stage rail and a
# reconciliation.

_DETECTED_TRANSFERS = """
SELECT t.id, t.source_system, t.source_transaction_id,
       -- A transfer's worth is `transfer_value`, NOT `amount`. Seccl publishes them separately
       -- and a stock/product transfer moves no cash, so `amount` is 0.00 on exactly the rows this
       -- query exists to find: requiring `amount > 0` hid every real transfer in the feed.
       COALESCE(
         NULLIF(t.data ->> 'transfer_value', '')::numeric,
         -- SEI stock transfers post with cash of 0. The cost is the book value, and one
         -- instruction is several free-receipt rows sharing RELATED_ACTIVITY_ID, so the
         -- worth is the sum of the group. A cash transfer's worth is the signed net,
         -- which lands on amount. Seccl's transfer_value above still wins for Seccl.
         CASE WHEN t.source_system = 'sei' THEN (
           SELECT SUM(COALESCE(
                    NULLIF(s.amount, 0),
                    NULLIF(s.data ->> 'local_book_value', '')::numeric,
                    CASE
                      WHEN NULLIF(s.data ->> 'BOOK_VALUE_IN_ASSET_CURRENCY', '') IS NOT NULL
                      THEN (CASE WHEN s.data ->> 'DEBIT_CREDIT_FLAG_12' = 'D' THEN -1 ELSE 1 END)
                           * (s.data ->> 'BOOK_VALUE_IN_ASSET_CURRENCY')::numeric
                    END
                  ))
           FROM platform.transactions s
           WHERE s.tenant_id = t.tenant_id
             AND s.account_id = t.account_id
             AND s.type = 'transfer_in'
             AND s.source_system = 'sei'
             AND (
               (
                 NULLIF(COALESCE(
                   t.data ->> 'RELATED_ACTIVITY_ID',
                   t.data ->> 'related_activity_id'
                 ), '') IS NOT NULL
                 AND COALESCE(
                   s.data ->> 'RELATED_ACTIVITY_ID',
                   s.data ->> 'related_activity_id'
                 ) = COALESCE(
                   t.data ->> 'RELATED_ACTIVITY_ID',
                   t.data ->> 'related_activity_id'
                 )
               )
               OR (
                 NULLIF(COALESCE(
                   t.data ->> 'RELATED_ACTIVITY_ID',
                   t.data ->> 'related_activity_id'
                 ), '') IS NULL
                 AND s.id = t.id
               )
             )
         ) END,
         NULLIF(t.amount, 0)
       )::float8                                            AS amount,
       t.amount::float8 AS cash_amount, t.currency,
       t.transaction_date, t.settlement_date, t.status, t.synced_at,
       t.account_id,
       -- Seccl's own words, out of the retained upstream row. `previous_provider_name` is the
       -- ceding scheme; `transfer_status` is its progress. The words stay on `ceding_status`.
       -- The consolidated stage is `synced_stage` of those words. A stock migration has no
       -- transfer status, so the transaction's own status decides. SEI publishes neither
       -- word: a posted ledger row has a blank status, and that is funds_sent.
       -- `adviser_note` is ours; the merge does not write it.
       t.adviser_note,
       COALESCE(NULLIF(t.data ->> 'previous_provider_name', ''),
                NULLIF(t.data ->> 'previous_provider', ''),
                -- The file names the counterparty. The transaction API does not.
                NULLIF(t.data ->> 'ORIGINATOR', ''),
                NULLIF(t.data ->> 'originator', ''))        AS ceding_provider,
       -- Seccl's previousProviderRef: the ceding scheme's policy or plan number. A stock
       -- line often has none; the product transfer on the same link does, and that is the
       -- reference the queue shows. Snake_case because the conformed row is to_json.
       COALESCE(
         NULLIF(t.data ->> 'previous_provider_ref', ''),
         (SELECT NULLIF(p.data ->> 'previous_provider_ref', '')
            FROM platform.transactions p
           WHERE p.tenant_id = t.tenant_id
             AND p.account_id = t.account_id
             AND p.type = 'transfer_in'
             AND NULLIF(t.data ->> 'link_id', '') IS NOT NULL
             AND p.data ->> 'link_id' = t.data ->> 'link_id'
             AND NULLIF(p.data ->> 'previous_provider_ref', '') IS NOT NULL
           ORDER BY p.id
           LIMIT 1)
       )                                                    AS policy_number,
       NULLIF(t.data ->> 'transfer_status', '')             AS ceding_status,
       (t.data ->> 'is_pension_transfer') = 'true'          AS is_pension_transfer,
       t.source_type, t.source_subtype,
       a.uid AS account_uid, a.name AS account_name, a.contact_id,
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
       -- Beside the name, and for the same reason `account_uid` sits beside `account_name`: the
       -- client record is addressed by uid, so a row carrying only `contact_id` can say WHO the
       -- transfer is for and offer no way to reach them. A detected row is a real transfer the
       -- workbench lists next to the recorded ones — it should not be the one that cannot link.
       ct.uid AS contact_uid,
       -- The adviser role is unique per contact (0245), so no tie-break ordering is needed.
       (SELECT u.name FROM crm.client_advisers ca
          JOIN auth.users u ON u.id = ca.adviser_id
         WHERE ca.contact_id = a.contact_id AND ca.role = 'adviser') AS adviser,
       (SELECT pp.provider::text FROM platform.product_platforms pp
         WHERE pp.product_id = p.id AND pp.tenant_id = t.tenant_id
           AND pp.provider = ANY(:administrators)
         ORDER BY (pp.role = 'scheme_administrator') DESC NULLS LAST
         LIMIT 1) AS scheme_administrator,
       -- In-specie holdings. Seccl: every stock transfer on this link. SEI: every free
       -- receipt or security transfer that shares RELATED_ACTIVITY_ID (the file's group;
       -- the transaction API does not return these movements). A line with neither key
       -- is its own holding.
       COALESCE((
         SELECT jsonb_agg(jsonb_build_object(
                  'name', COALESCE(
                    NULLIF(s.asset_name, ''),
                    NULLIF(s.data ->> 'asset_name', '')
                  ),
                  'code', COALESCE(
                    NULLIF(s.isin, ''),
                    NULLIF(s.data ->> 'INSTRUMENT_ID', ''),
                    NULLIF(s.data ->> 'instrument_id', ''),
                    NULLIF(s.data ->> 'isin', '')
                  ),
                  'quantity', s.quantity::float8,
                  'status', NULLIF(s.status, '')
                ) ORDER BY s.asset_name NULLS LAST, s.isin, s.id)
         FROM platform.transactions s
         WHERE s.tenant_id = t.tenant_id
           AND s.account_id = t.account_id
           AND s.type = 'transfer_in'
           AND (
             (
               t.source_system = 'seccl'
               AND s.source_type = 'Stock Transfer'
               AND (
                 (NULLIF(t.data ->> 'link_id', '') IS NOT NULL
                   AND s.data ->> 'link_id' = t.data ->> 'link_id')
                 OR (NULLIF(t.data ->> 'link_id', '') IS NULL AND s.id = t.id)
               )
             )
             OR (
               t.source_system = 'sei'
               AND s.source_system = 'sei'
               AND s.source_type IN (
                 'Free Receipt', 'Security Transfer -Transfer To'
               )
               AND (
                 (
                   NULLIF(COALESCE(
                     t.data ->> 'RELATED_ACTIVITY_ID',
                     t.data ->> 'related_activity_id'
                   ), '') IS NOT NULL
                   AND COALESCE(
                     s.data ->> 'RELATED_ACTIVITY_ID',
                     s.data ->> 'related_activity_id'
                   ) = COALESCE(
                     t.data ->> 'RELATED_ACTIVITY_ID',
                     t.data ->> 'related_activity_id'
                   )
                 )
                 OR (
                   NULLIF(COALESCE(
                     t.data ->> 'RELATED_ACTIVITY_ID',
                     t.data ->> 'related_activity_id'
                   ), '') IS NULL
                   AND s.id = t.id
                 )
               )
             )
           )
       ), '[]'::jsonb) AS instruments
FROM platform.transactions t
  JOIN platform.accounts a ON a.id = t.account_id AND a.tenant_id = t.tenant_id
  LEFT JOIN platform.products p ON p.id = a.product_id
  LEFT JOIN crm.contacts ct ON ct.id = a.contact_id AND ct.deleted_at IS NULL
WHERE t.tenant_id = :t
  AND t.type = 'transfer_in'
  AND t.source_system = ANY(:feed)
  -- Deliberately NO amount floor. An in-specie transfer settles as assets, not cash, so its
  -- `amount` is legitimately zero and its value lives in `transfer_value`.
  AND NOT EXISTS (SELECT 1 FROM platform.transfers tr WHERE tr.transaction_id = t.id)
  AND t.expectation_id IS NULL
  -- One transfer, many instruments. A Seccl stock line yields to the in-specie product
  -- transfer on its link_id; stock lines with no such parent collapse to the earliest.
  -- A cash product transfer is a different movement and stays its own row. SEI groups
  -- free receipts and security transfers that share RELATED_ACTIVITY_ID the same way.
  AND NOT EXISTS (
    SELECT 1 FROM platform.transactions h
    WHERE h.tenant_id = t.tenant_id
      AND h.account_id = t.account_id
      AND h.type = 'transfer_in'
      AND h.id <> t.id
      AND (
        (
          t.source_system = 'seccl'
          AND h.source_system = 'seccl'
          AND NULLIF(t.data ->> 'link_id', '') IS NOT NULL
          AND h.data ->> 'link_id' = t.data ->> 'link_id'
          AND (
            (
              t.source_type = 'Stock Transfer'
              AND h.source_type = 'Product Transfer'
              AND lower(replace(coalesce(h.source_subtype, ''), '-', ' ')) <> 'cash'
            )
            OR (
              t.source_type = 'Product Transfer'
              AND lower(replace(coalesce(t.source_subtype, ''), '-', ' ')) <> 'cash'
              AND h.source_type = 'Product Transfer'
              AND lower(replace(coalesce(h.source_subtype, ''), '-', ' ')) <> 'cash'
              AND h.id < t.id
            )
            OR (
              t.source_type = 'Stock Transfer'
              AND h.source_type = 'Stock Transfer'
              AND h.id < t.id
              AND NOT EXISTS (
                SELECT 1 FROM platform.transactions p
                WHERE p.tenant_id = t.tenant_id
                  AND p.account_id = t.account_id
                  AND p.type = 'transfer_in'
                  AND p.source_system = 'seccl'
                  AND p.source_type = 'Product Transfer'
                  AND lower(replace(coalesce(p.source_subtype, ''), '-', ' ')) <> 'cash'
                  AND p.data ->> 'link_id' = t.data ->> 'link_id'
              )
            )
          )
        )
        OR (
          t.source_system = 'sei'
          AND h.source_system = 'sei'
          AND h.id < t.id
          AND t.source_type IN ('Free Receipt', 'Security Transfer -Transfer To')
          AND h.source_type IN ('Free Receipt', 'Security Transfer -Transfer To')
          AND NULLIF(COALESCE(
            t.data ->> 'RELATED_ACTIVITY_ID',
            t.data ->> 'related_activity_id'
          ), '') IS NOT NULL
          AND COALESCE(
            h.data ->> 'RELATED_ACTIVITY_ID',
            h.data ->> 'related_activity_id'
          ) = COALESCE(
            t.data ->> 'RELATED_ACTIVITY_ID',
            t.data ->> 'related_activity_id'
          )
        )
      )
  )
ORDER BY COALESCE(t.transaction_date, t.synced_at::date) DESC, t.id DESC
LIMIT :limit
"""


async def detected_transfers(
    session: AsyncSession, *, tenant_id: int, limit: int = 200
) -> list[dict[str, Any]]:
    """Assets that arrived from a ceding scheme with no transfer record against them.

    Shaped to the same contract as a booked transfer so the workbench can render one list —
    with `uid` prefixed `detected:` so the surface (and the router) can tell them apart and
    keep the read-only ones read-only.
    """
    from app import providers

    rows = (
        await session.execute(
            text(_DETECTED_TRANSFERS),
            {
                "t": tenant_id,
                "limit": max(1, min(limit, 1000)),
                "feed": list(providers.PROVIDERS),
                "administrators": list(providers.administrators()),
                "custodians": list(providers.custodians()),
            },
        )
    ).mappings()
    return [_shape_detected(dict(row)) for row in rows]


# Each custodian names "this moved securities, not cash" in its own words, so the check is
# per source_system rather than one shared string set. Seccl's transaction TYPE is not enough:
# a Product Transfer is Cash or In Specie on `transactionSubType`, and treating every Product
# Transfer as in-specie liquidates nothing in the label but tells the adviser the ceding scheme
# sent assets when it sent cash. Stock Transfer is securities (subtype "Migration" included).
# Cash Transfer is cash. SEI's TRANSACTION_TYPE reference names it per-direction ("Free
# Receipt"/"Security Transfer -Transfer To" vs "...Cash Transfer ..."/"Currency Transfer To" —
# see services/sei/models/platform/transactions.sql). SEI's own "Complex Trade Transfer" is
# genuinely ambiguous there and deliberately left unclassified here (falls through to 'cash'
# below) rather than guessed — the same discipline that model applies to the raw code itself.
_IN_SPECIE_SOURCE_TYPES: dict[str, frozenset[str]] = {
    "seccl": frozenset({"Product Transfer", "Stock Transfer"}),
    "sei": frozenset(
        {
            "Free Receipt",
            "Free Delivery",
            "Security Transfer -Transfer To",
            "Security Transfer -Transfer From",
        }
    ),
}


def _fold_subtype(value: str | None) -> str:
    return (value or "").strip().lower().replace("-", " ").replace("_", " ")


def _is_in_specie(
    source_system: str | None,
    source_type: str | None,
    source_subtype: str | None = None,
) -> bool:
    # The subtype is the custodian's own answer when it bothers to give one. Seccl's Product
    # Transfer carries "Cash" or "In Specie"; reading only the type marked every one in-specie.
    subtype = _fold_subtype(source_subtype)
    if subtype == "cash":
        return False
    if subtype == "in specie":
        return True
    return source_type in _IN_SPECIE_SOURCE_TYPES.get(source_system or "", frozenset())


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text_value = str(value).strip()
    return text_value or None


def _quantity(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _instrument_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        import json

        try:
            value = json.loads(value)
        except ValueError:
            return []
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def normalise_instruments(linked: Any, advised: Any = None) -> list[dict[str, Any]]:
    """Holdings on an in-specie transfer.

    Linked rows are Seccl stock transfers (name, ISIN, units, the transaction's own status).
    Advised rows are what was stored to send to SEI (``data.assets``): an identifier and units,
    and a status only when one was recorded. SEI's pending-transfer read has no per-holding status.
    """
    lines = _instrument_rows(linked)
    if not lines:
        lines = [
            {
                "name": asset.get("name") or asset.get("asset_name"),
                "code": asset.get("identifier") or asset.get("code") or asset.get("isin"),
                "quantity": asset.get("units")
                if asset.get("units") is not None
                else asset.get("quantity"),
                "status": asset.get("status"),
            }
            for asset in _instrument_rows(advised)
        ]
    cleaned: list[dict[str, Any]] = []
    for row in lines:
        item = {
            "name": _text_or_none(row.get("name")),
            "code": _text_or_none(row.get("code") or row.get("isin") or row.get("identifier")),
            "quantity": _quantity(row.get("quantity") if "quantity" in row else row.get("units")),
            "status": _text_or_none(row.get("status")),
        }
        if item["name"] or item["code"]:
            cleaned.append(item)
    return cleaned


def _shape_detected(row: dict[str, Any]) -> dict[str, Any]:
    """One detected transfer, in the shape `ApiTransfer` declares."""
    from app import transfers as transfers_mod

    value = row.get("amount")
    cash = row.get("cash_amount")
    stage = transfers_mod.synced_stage(
        row.get("source_system"), row.get("ceding_status"), row.get("status")
    )
    # Seccl publishes one figure, `transfer_value` (`amount` here). `amount`,
    # `executedAmount` and `consideration` are zero on every transfer-in we hold, and
    # `bookValue` is a CGT cost that is filled while the transfer is still open, so none
    # of them is a receipt. Received is that one figure once the custodian has booked
    # the movement, and unknown before — copying it onto an open row made Submitted
    # show Received equal to Requested.
    landed = stage in {"funds_sent", "completed"}
    received = (cash if cash else value) if landed else None
    in_specie = _is_in_specie(
        row.get("source_system"), row.get("source_type"), row.get("source_subtype")
    )
    shaped = {
        "id": row["id"],
        "uid": f"detected:{row['id']}",
        "detected": True,
        "account_id": row["account_id"],
        "contact_id": row.get("contact_id"),
        # `ApiTransfer` declares this, so it is emitted even when the join found nobody — an absent
        # key and a null are the same to the template's guard but not to the type.
        "contact_uid": row.get("contact_uid"),
        "expectation_id": None,
        "transaction_id": row["id"],
        # An unnamed ceding scheme is normal on a cash transfer, and saying so is better than
        # showing a blank cell that reads as a loading failure.
        "ceding_provider": row.get("ceding_provider") or "Unnamed ceding scheme",
        "policy_number": row.get("policy_number"),
        "transfer_type": (
            "pension" if row.get("is_pension_transfer") else "in_specie" if in_specie else "cash"
        ),
        "basis": "in_specie" if in_specie else "cash",
        "extent": None,
        "crystallised_value": None,
        "uncrystallised_value": None,
        "total_value": value,
        "currency": row.get("currency") or "GBP",
        # The provider's transfer status when the feed published one. A Seccl stock
        # migration has none, so the transaction status decides. An SEI ledger row has
        # none either, and a blank there means the movement has posted (funds_sent,
        # shown as Completed). Not the reconciled `completed`.
        "status": stage,
        "ml_status": None,
        "ml_status_label": None,
        "ml_status_at": None,
        "ceding_status": row.get("ceding_status"),
        "ml_transfer_id": None,
        "ml_recorded_at": None,
        "seccl_transfer_id": None,
        "sei_activity_id": None,
        "custodian": row.get("custodian"),
        "scheme_administrator": row.get("scheme_administrator"),
        "is_pension": bool(row.get("is_pension")),
        "expected_date": None,
        "completed_at": None,
        "note": row.get("adviser_note"),
        "created_at": str(row.get("transaction_date") or row.get("synced_at") or ""),
        "updated_at": str(row.get("synced_at") or ""),
        "account_name": row.get("account_name"),
        "account_uid": row.get("account_uid"),
        "product_type": row.get("product_type"),
        "client_name": row.get("client_name"),
        "adviser": row.get("adviser"),
        "expectation_uid": None,
        "expectation_status": None,
        "expectation_due_date": None,
        "expectation_amount": None,
        "expectation_matched_at": None,
        "matched_by_hand": None,
        # What the custodian booked, once it has. Null while the transfer is still open:
        # there is no second figure to show as Received.
        "transaction_amount": received,
        "transaction_date": row.get("transaction_date"),
        "transaction_source": row.get("source_system"),
        "transaction_reference": row.get("source_transaction_id"),
        "instruments": normalise_instruments(row.get("instruments")) if in_specie else [],
        # Nothing to reconcile: there was never a forecast to compare the credit against. Saying
        # "matched" would claim a check that was never made.
        "reconciliation": {
            "status": "pending",
            "legs": [{"key": "custodian", "label": "Received", "value": received}],
            "variance": None,
            "reason": (
                "Detected from the custodian feed — there is no Graphene transfer record to "
                "reconcile it against."
            ),
        },
    }
    # The feed names the custodian even when the account row has not been tagged yet.
    source = (row.get("source_system") or "").lower()
    if not shaped.get("custodian") and source in {"seccl", "sei"}:
        shaped["custodian"] = source
    transfers_mod.stamp_route(shaped)
    fact = transfers_mod.driving_fact(shaped)
    shaped["provider_party"] = fact["party"]
    shaped["provider_native"] = fact["native"]
    shaped["provider_stage"] = fact["stage"]
    return shaped


async def get_transaction(
    session: AsyncSession, *, tenant_id: int, transaction_id: int
) -> dict[str, Any] | None:
    """One transaction, scoped to the caller's firm. Used before attaching it to anything."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT t.id, t.account_id, t.contact_id, t.type, t.amount::float8 AS amount, "
                    "       t.currency, t.transaction_date, t.settlement_date, t.expectation_id, "
                    "       t.source_system, t.source_transaction_id, t.source_type, "
                    "       t.source_subtype, a.contact_id AS account_contact_id, "
                    # The owning client's employer (0126) — `classify_transaction` reads it as the
                    # default contributor name, so a credit can be classified as an employer
                    # contribution without the panel asking for a name it does not hold.
                    "       ct.employer_name AS client_employer_name "
                    "FROM platform.transactions t "
                    "  JOIN platform.accounts a "
                    "    ON a.id = t.account_id AND a.tenant_id = t.tenant_id "
                    "  LEFT JOIN crm.contacts ct "
                    "    ON ct.id = a.contact_id AND ct.deleted_at IS NULL "
                    "WHERE t.id = :i AND t.tenant_id = :t"
                ),
                {"i": transaction_id, "t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


# ---- the matcher (path B) -------------------------------------------------------------------


async def candidates_for(
    session: AsyncSession, *, tenant_id: int, expectation_id: int
) -> list[dict[str, Any]]:
    """Unmatched credits on this expectation's account that could be its money, best first.

    Ranked by how far the amount is out and then by how far the date is out, so the exact
    same-day credit sorts above the one that is a pound short a week later. Returned for a
    human to choose from — :func:`run` will only ever apply an unambiguous exact match.
    """
    rows = (
        await session.execute(
            text(
                """
                SELECT t.id, t.source_system, t.source_transaction_id, t.type,
                       t.source_type, t.source_subtype,
                       t.amount::float8 AS amount, t.currency, t.transaction_date,
                       abs(t.amount - e.amount)::float8 AS amount_delta,
                       CASE WHEN e.due_date IS NULL OR t.transaction_date IS NULL THEN NULL
                            ELSE abs(t.transaction_date - e.due_date) END AS days_delta
                FROM platform.transaction_expectations e
                  JOIN platform.transactions t
                    ON t.account_id = e.account_id
                   AND t.tenant_id = e.tenant_id
                   AND t.expectation_id IS NULL
                   AND t.source_system = ANY(:feed)
                   AND t.type = ANY (:types)
                   AND t.amount > 0
                   AND COALESCE(t.currency, 'GBP') = COALESCE(e.currency, 'GBP')
                   AND (e.due_date IS NULL OR t.transaction_date IS NULL
                        OR t.transaction_date BETWEEN e.due_date - :window
                                                  AND e.due_date + :window)
                WHERE e.id = :e AND e.tenant_id = :t
                ORDER BY amount_delta ASC, days_delta ASC NULLS LAST, t.id ASC
                LIMIT 20
                """
            ),
            {
                "e": expectation_id,
                "t": tenant_id,
                "types": list(INCOMING_TYPES),
                "window": timedelta(days=MATCH_WINDOW_DAYS),
                "feed": list(providers.PROVIDERS),
            },
        )
    ).mappings()
    return [dict(row) for row in rows]


async def _open_expectations(session: AsyncSession, *, tenant_id: int) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                "SELECT id, uid, account_id, amount, currency, due_date, kind "
                "FROM platform.transaction_expectations "
                "WHERE tenant_id = :t AND direction = 'in' "
                "  AND status IN ('expected', 'instructed') "
                "ORDER BY due_date NULLS LAST, id"
            ),
            {"t": tenant_id},
        )
    ).mappings()
    return [dict(row) for row in rows]


async def _exact_candidates(
    session: AsyncSession, *, tenant_id: int, expectation: dict[str, Any]
) -> list[int]:
    """Ids of unmatched credits whose amount equals the forecast, inside the window."""
    rows = (
        await session.execute(
            text(
                """
                SELECT t.id
                FROM platform.transactions t
                WHERE t.tenant_id = :t
                  AND t.account_id = :a
                  AND t.expectation_id IS NULL
                  AND t.source_system = ANY(:feed)
                  AND t.type = ANY (:types)
                  AND t.amount = :amount
                  AND COALESCE(t.currency, 'GBP') = COALESCE(:currency, 'GBP')
                  -- CAST(...) rather than `:due::date`: SQLAlchemy's text() does not
                  -- recognise a bind followed by `::`, so the parameter is left literal and
                  -- Postgres rejects the statement at execute time.
                  AND (CAST(:due AS date) IS NULL OR t.transaction_date IS NULL
                       OR t.transaction_date BETWEEN CAST(:due AS date) - :window
                                                 AND CAST(:due AS date) + :window)
                ORDER BY t.id
                """
            ),
            {
                "t": tenant_id,
                "a": expectation["account_id"],
                "types": list(INCOMING_TYPES),
                "amount": expectation["amount"],
                "currency": expectation["currency"],
                "due": expectation["due_date"],
                "window": timedelta(days=MATCH_WINDOW_DAYS),
                "feed": list(providers.PROVIDERS),
            },
        )
    ).scalars()
    return list(rows)


async def link(
    session: AsyncSession,
    *,
    tenant_id: int,
    expectation_id: int,
    transaction_id: int,
    matched_by: str | None = None,
) -> None:
    """Record that this credit fulfilled this forecast, on all three rows that care.

    ``matched_by`` is the acting operator's WorkOS id for a manual match and ``None`` for the
    automatic one — the column stays NULL rather than taking a sentinel, so an audit read can
    tell a tolerance from a person's decision.
    """
    await session.execute(
        text(
            "UPDATE platform.transactions SET expectation_id = :e, updated_at = now() "
            "WHERE id = :x AND tenant_id = :t"
        ),
        {"e": expectation_id, "x": transaction_id, "t": tenant_id},
    )
    await session.execute(
        text(
            "UPDATE platform.transaction_expectations "
            "SET status = 'matched', matched_at = now(), matched_transaction_id = :x, "
            "    matched_by = (SELECT id FROM auth.users WHERE workos_user_id = :by) "
            "WHERE id = :e AND tenant_id = :t"
        ),
        {"e": expectation_id, "x": transaction_id, "by": matched_by, "t": tenant_id},
    )
    await outbound.mark_posted_for_expectation(
        session, tenant_id=tenant_id, expectation_id=expectation_id, inbound_id=transaction_id
    )
    # The domain record on the other side of the same expectation. Both statements run because
    # an expectation belongs to exactly one of the two, and the no-op costs nothing.
    for table in ("contributions", "transfers"):
        await session.execute(
            text(
                f"UPDATE platform.{table} SET transaction_id = :x "  # noqa: S608 - fixed literals
                "WHERE expectation_id = :e AND tenant_id = :t AND transaction_id IS NULL"
            ),
            {"x": transaction_id, "e": expectation_id, "t": tenant_id},
        )
    # The contribution's date comes from the money, not from whoever booked it. A book-ahead is
    # made before the payment exists, so the drawer does not ask for a date and the column stays
    # NULL until the credit arrives — this is what fills it. Guarded on IS NULL: an explicitly
    # stated date (an adviser recording a payment they know the date of, or `from_transaction`,
    # which already sets it) is a fact and must not be overwritten by the settlement of the credit
    # we matched to it. `transaction_date` is when the money moved; settlement is when it cleared.
    await session.execute(
        text(
            "UPDATE platform.contributions c "
            "SET contribution_date = x.transaction_date, updated_at = now() "
            "FROM platform.transactions x "
            "WHERE x.id = :x AND c.expectation_id = :e AND c.tenant_id = :t "
            "  AND c.contribution_date IS NULL AND x.transaction_date IS NOT NULL"
        ),
        {"x": transaction_id, "e": expectation_id, "t": tenant_id},
    )


async def unlink(session: AsyncSession, *, tenant_id: int, expectation_id: int) -> None:
    """Undo a match. Returns the expectation to ``instructed`` rather than ``expected``: it was
    lodged at the custodian and still is — only our belief about which credit paid it changed.

    Only the matched inbound credit is detached. Graphene-originated pending rows keep the
    expectation_id they were born with, or they would become unclassified credits of their own
    amount.
    """
    await session.execute(
        text(
            "UPDATE platform.transactions SET expectation_id = NULL, updated_at = now() "
            "WHERE expectation_id = :e AND tenant_id = :t AND source_system = ANY(:feed)"
        ),
        {"e": expectation_id, "t": tenant_id, "feed": list(providers.PROVIDERS)},
    )
    await outbound.revert_posted_for_expectation(
        session, tenant_id=tenant_id, expectation_id=expectation_id
    )
    await session.execute(
        text(
            "UPDATE platform.transaction_expectations "
            "SET status = CASE WHEN instructed_at IS NULL THEN 'expected' ELSE 'instructed' END, "
            "    matched_at = NULL, matched_transaction_id = NULL, matched_by = NULL "
            "WHERE id = :e AND tenant_id = :t"
        ),
        {"e": expectation_id, "t": tenant_id},
    )
    # Undo the date `link` stamped, and ONLY that one: it is cleared where it still equals the
    # date of the credit being unlinked, so a date an adviser stated by hand survives. Ordered
    # before the transaction_id reset because it reads through it to find the credit.
    await session.execute(
        text(
            "UPDATE platform.contributions c SET contribution_date = NULL, updated_at = now() "
            "FROM platform.transactions x "
            "WHERE x.id = c.transaction_id AND c.expectation_id = :e AND c.tenant_id = :t "
            "  AND c.contribution_date = x.transaction_date"
        ),
        {"e": expectation_id, "t": tenant_id},
    )
    for table in ("contributions", "transfers"):
        await session.execute(
            text(
                f"UPDATE platform.{table} SET transaction_id = NULL "  # noqa: S608 - fixed
                "WHERE expectation_id = :e AND tenant_id = :t"
            ),
            {"e": expectation_id, "t": tenant_id},
        )


async def run(session: AsyncSession, *, tenant_id: int) -> dict[str, Any]:
    """Match every open expectation that has exactly one exact-amount credit in its window.

    Returns a report rather than raising: this runs on a schedule as well as on demand, and one
    ambiguous expectation must not stop the rest. ``ambiguous`` is the interesting number — it
    is work for a human, and a workbench that never showed it would leave money unreconciled
    with no sign that anything was pending.
    """
    matched: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    unmatched = 0

    for expectation in await _open_expectations(session, tenant_id=tenant_id):
        candidates = await _exact_candidates(session, tenant_id=tenant_id, expectation=expectation)
        if not candidates:
            unmatched += 1
            continue
        if len(candidates) > 1:
            # Two identical credits in the window — most often a client paying the same monthly
            # amount twice, or a duplicate feed row. Picking one would be a coin flip against a
            # pension, so both are left for the workbench to resolve.
            ambiguous.append(
                {
                    "expectation_uid": expectation["uid"],
                    "candidates": candidates,
                    "reason": (
                        f"{len(candidates)} credits of the same amount fall within "
                        f"{MATCH_WINDOW_DAYS} days of the due date."
                    ),
                }
            )
            continue
        await link(
            session,
            tenant_id=tenant_id,
            expectation_id=int(expectation["id"]),
            transaction_id=candidates[0],
        )
        matched.append({"expectation_uid": expectation["uid"], "transaction_id": candidates[0]})

    return {
        "matched": len(matched),
        "ambiguous": len(ambiguous),
        "unmatched": unmatched,
        "matches": matched,
        "needs_review": ambiguous,
    }


# ---- the derived three-way reconciliation ---------------------------------------------------

#: How far the realised credit may differ from the forecast before it is called a break. Zero:
#: the two are the same money, and any difference is a fact to explain rather than absorb. It
#: is a named constant rather than an inline 0 because "we tolerate nothing" is a decision.
RECONCILE_TOLERANCE = Decimal("0")


def reconcile(record: dict[str, Any]) -> dict[str, Any]:
    """Compare the three legs of one contribution: what we booked, what we forecast, what
    landed. Derived on read, not stored.

    ``docs/contributions.md`` §7 lists ``platform.reconciliation_breaks`` as unbuilt, and this
    deliberately does not build it. A break here is a *statement about the current rows* — the
    expectation is for £500 and £450 arrived — so persisting it creates a second copy that goes
    stale the moment either side is corrected, and then the table has to be reconciled with the
    ledger it was meant to reconcile. What a stored break would add is workflow (assignment,
    resolution notes), which is worth having only once there is someone assigned to work it.

    Returns ``status`` of ``pending`` (nothing has landed yet), ``matched`` (the legs agree) or
    ``break`` (they do not), plus the legs themselves so the UI never recomputes them.
    """
    booked = _dec(record.get("amount") or 0)
    expected = record.get("expectation_amount")
    realised = record.get("transaction_amount")

    legs = [
        {"key": "graphene", "label": "Booked", "value": float(booked)},
        {
            "key": "expectation",
            "label": "Forecast",
            "value": float(_dec(expected)) if expected is not None else None,
        },
        {
            "key": "custodian",
            "label": "Received",
            "value": float(_dec(realised)) if realised is not None else None,
        },
    ]

    if realised is None:
        return {
            "status": "pending",
            "legs": legs,
            "variance": None,
            "reason": "No credit has been matched to this contribution yet.",
        }

    variance = _dec(realised) - booked
    if abs(variance) <= RECONCILE_TOLERANCE:
        return {"status": "matched", "legs": legs, "variance": 0.0, "reason": None}
    direction = "more than" if variance > 0 else "less than"
    return {
        "status": "break",
        "legs": legs,
        "variance": float(variance),
        "reason": (
            f"The credit that landed is {abs(variance):,.2f} {direction} the contribution booked."
        ),
    }


def relief_position(record: dict[str, Any]) -> dict[str, Any]:
    """Relief expected vs relief received for one contribution.

    The RAS monitor's per-member row. ``received`` comes from a credit SEI stamped with a
    basic-rate-relief description code (21122/21126) — see
    ``app/integrations/sei/reference.py``; nothing else in the feed distinguishes it from the
    contribution itself, because both arrive as transaction type 6 on the same account.
    """
    expected = _dec(record.get("relief_amount") or 0)
    received = record.get("relief_received")
    if expected <= 0:
        return {"status": "not_applicable", "expected": 0.0, "received": 0.0, "outstanding": 0.0}
    if received is None:
        return {
            "status": "awaited",
            "expected": float(expected),
            "received": None,
            "outstanding": float(expected),
        }
    got = _dec(received)
    outstanding = expected - got
    return {
        "status": "received" if abs(outstanding) <= RECONCILE_TOLERANCE else "short",
        "expected": float(expected),
        "received": float(got),
        "outstanding": float(outstanding),
    }


async def relief_credits(
    session: AsyncSession, *, tenant_id: int, account_ids: list[int]
) -> dict[int, Decimal]:
    """Basic-rate relief SEI has paid in, summed per account.

    Keyed by account rather than by contribution because SEI pays relief in a monthly BATCH
    against the scheme, not per contribution — so attributing a relief credit to one
    contribution is an allocation, not a fact. The workbench shows the account-level position
    and leaves the split alone.
    """
    if not account_ids:
        return {}
    rows = (
        await session.execute(
            text(
                "SELECT account_id, SUM(amount) AS total "
                "FROM platform.transactions "
                "WHERE tenant_id = :t AND account_id = ANY (:ids) "
                "  AND type = 'deposit' AND amount > 0 "
                "  AND source_subtype = ANY (:codes) "
                "GROUP BY account_id"
            ),
            {
                "t": tenant_id,
                "ids": account_ids,
                "codes": sorted(sei_reference.RELIEF_DESCRIPTION_CODES),
            },
        )
    ).mappings()
    return {int(row["account_id"]): _dec(row["total"]) for row in rows}


def due_date_window(due: date | None) -> tuple[date | None, date | None]:
    """The window :func:`run` searches, exposed so the UI can say what it looked at."""
    if due is None:
        return (None, None)
    return (due - timedelta(days=MATCH_WINDOW_DAYS), due + timedelta(days=MATCH_WINDOW_DAYS))
