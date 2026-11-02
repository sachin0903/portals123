"""A firm's own current holdings, from the conformed layer — what a money control is checked on.

THIS REPLACES A FIXTURE. `app/lake/seccl.py` reads `docs/seccl.duckdb`, an anonymised, downsampled
dev extract committed to this repository, and the withdrawal gate was checked against it: the same
file, for every firm, in every deployment. Both ways of being wrong are bad and neither is visible:

* the account's provider id is absent from the extract, so the gate finds no position and REFUSES
  every withdrawal on that account, for ever;
* the id IS present — the extract keeps real Seccl ids — and the gate then permits or refuses
  against someone else's anonymised numbers.

It reads `tenant_{uid}.positions`, the firm's own slice, computed by the provider's own chain
(`docs/provider-pipeline.md`). The SCHEMA is the narrowing, so there is no predicate here to get
wrong, and the console and the money control cannot disagree about what a firm's slice is — the
same reasoning `platform_entities` records for reading the same relations.

**Cash is the vendor's word, not ours.** The conformed layer publishes `position_type`, which is
Seccl's own `positionType` — `Cash` or `Stock`, and a cash position carries no asset fields at all.
That is mapped into the `asset_type` slot `instructions.is_cash_holding` already reads, so the
single definition of "what counts as cash" is unchanged and the money-market funds that are cash by
NAME rather than by type keep counting. Changing that rule here would let the Cash a screen shows
and the cash a withdrawal is checked against drift apart, which is what having one definition
exists to prevent.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace

from app.ids import tenant_model_key
from app.lake import core as lake_core

logger = logging.getLogger(__name__)

# A tenant uid lands in an IDENTIFIER position (`tenant_{uid}.positions`), which no bind parameter
# can carry, so it is checked rather than trusted — the rule `platform_entities.resolve` sets.
_UID = re.compile(r"^[A-Za-z0-9_]+$")

# The conformed column set, renamed to what `instructions.funding_basis` and the portal's holdings
# view already consume. Renaming HERE rather than at the callers keeps the vocabulary change in one
# place: `market_value` is the book's word for what those readers call `value`.
_HOLDINGS_SQL = """
SELECT p.account_external_id,
       p.instrument_key            AS asset_id,
       p.asset_name,
       p.isin,
       i.sedol,
       p.currency,
       -- Seccl's own classification. `platform.positions` carries no `asset_type`; the fixture's
       -- came from a mart column, and this is the vendor's statement rather than a derived one.
       p.position_type             AS asset_type,
       p.market_value              AS value,
       p.quantity,
       p.price,
       p.book_cost,
       p.unrealised_gain           AS growth,
       p.unrealised_gain_percent   AS growth_pct,
       p.allocation,
       p.as_of_date                AS as_of
FROM {relation} p
LEFT JOIN {instruments} i ON i.external_id = p.instrument_key
WHERE p.account_external_id IN (SELECT UNNEST(?::VARCHAR[]))
ORDER BY p.account_external_id, p.market_value DESC
"""


def contains_pattern(q: str) -> str:
    """A substring pattern for ``ILIKE … ESCAPE '\\'`` that treats ``%`` and ``_`` as letters.

    Shared with the demo-lake search. A second spelling of the escape would let one reader match
    a typed underscore as "any character" and the other match it as itself.
    """
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# Search has a materially different job from the holdings listing: it needs the first handful of
# matching names, not every position for the client so Python can throw almost all of them away.
# Push both the text predicate and cap into DuckDB. A client with hundreds of positions (the common
# trust case) now transfers at most one palette group across the lake boundary.
_SEARCH_HOLDINGS_SQL = """
SELECT p.account_external_id,
       p.instrument_key AS asset_id,
       p.asset_name,
       p.isin,
       p.market_value AS value
FROM {relation} p
WHERE p.account_external_id IN (SELECT UNNEST(?::VARCHAR[]))
  AND (p.quantity IS NULL OR p.quantity >= 0)
  AND (p.asset_name ILIKE ? ESCAPE '\\' OR p.isin ILIKE ? ESCAPE '\\')
ORDER BY p.market_value DESC NULLS LAST, p.instrument_key
LIMIT ?
"""


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dt.date | dt.datetime):
        return value.isoformat()
    return value


class HoldingsUnavailable(RuntimeError):
    """The firm's positions could not be read. NOT the same as the firm holding none."""


def require_uid(tenant_uid: str) -> None:
    """PUBLIC because `lake/staff.py` reads a different relation of the same slice. These three
    (`require_uid`, `guarded`, `read_slice`) are the slice-reading plumbing rather than anything
    about holdings, and reaching across an underscore for them would have been the smell that
    says so."""
    if not _UID.match(tenant_uid):
        raise HoldingsUnavailable("Holdings are temporarily unavailable.")


async def guarded(provider: str, awaitable: Any) -> list[dict[str, Any]]:
    """Run a warehouse read, converting every failure into `HoldingsUnavailable`.

    ONE conversion for all three readers. They must fail identically, because the whole point of
    raising rather than returning `[]` is that a caller can tell "could not read" from "holds
    nothing" — and a reader that quietly returned `[]` on one error path would put that back.
    """
    try:
        return await awaitable  # type: ignore[no-any-return]
    except lake_core.CoreUnavailable as exc:
        # The MESSAGE is what a firm reads, so it names no configuration; the reason is logged
        # instead, because an unreadable warehouse is something only an operator can fix and
        # dropping it would leave nothing but a traceback nobody sees.
        logger.warning("warehouse unreadable for %s: %s", provider, exc)
        raise HoldingsUnavailable("Holdings are temporarily unavailable.") from exc
    except HoldingsUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - an unbuilt slice is a state, not a crash
        # A firm whose chain has never run has no `tenant_{uid}` schema at all, which DuckDB
        # reports as a catalog error. Distinguishable from "no rows".
        logger.warning("slice unreadable for %s: %s", provider, exc)
        raise HoldingsUnavailable(
            "Holdings from this custodian are temporarily unavailable."
        ) from exc


async def firm_holdings(
    provider: str, tenant_uid: str, external_account_ids: list[str]
) -> list[dict[str, Any]]:
    """Current holdings for these provider account ids, from the firm's own slice.

    RAISES rather than returning `[]` when the warehouse cannot be read, and this is the whole
    point of the module. The fixture reader returns `[]` for a missing file, so "the lake is not
    there" and "this account holds nothing" arrive at the caller as the same value — and a money
    control that cannot tell them apart is deciding on an absence it has not understood. The caller
    refuses either way; only one of them is the account's fault, and only one is worth paging
    somebody about.

    Authorisation happened BEFORE this: the caller resolved the account through RLS and passes the
    provider ids it already established the principal may see. The slice schema narrows to the
    firm, so a leak would need both a wrong uid and a wrong id.
    """
    if not external_account_ids:
        return []
    require_uid(tenant_uid)
    return await guarded(
        provider,
        asyncio.to_thread(
            read_slice,
            provider,
            tenant_uid,
            _HOLDINGS_SQL,
            [external_account_ids],
            statement="positions",
            relation="positions",
            instruments="instruments",
        ),
    )


# Word boundaries, the same pattern ``instructions.is_cash_holding`` used to compile itself.
# A substring ``cash`` counted "Cashel Capital …" as withdrawable cash. The summary SQL
# splices this constant in, so the listing and the aggregate cannot grow a second spelling.
CASH_NAME_NEEDLE = r"\b(?:cash|money[ -]?market|liquidity)\b"
_CASH_NAME = re.compile(CASH_NAME_NEEDLE)

# SEI's currency holding. Named by the currency ("GBP"), so the word pattern never sees it.
# A code, compared exactly: folding it into the word pattern would match an identifier.
_CURRENCY_POSITION_TYPE = "mrcxxxxxxx"

# The non-cash classes, in the order the summary CASE tests them. Same needles as
# ``classify_holding`` — spliced into the SQL below, not copied.
_FIXED_INCOME_NEEDLE = "bond|gilt|treasury|fixed income|credit"
_PROPERTY_NEEDLE = "property|real estate|reit|infrastructure"
_ALTERNATIVES_NEEDLE = "gold|commodit|carbon|precious|metal"
_CLASS_NEEDLES: tuple[tuple[str, str], ...] = (
    ("Fixed income", _FIXED_INCOME_NEEDLE),
    ("Property & infra", _PROPERTY_NEEDLE),
    ("Alternatives", _ALTERNATIVES_NEEDLE),
)


def is_cash_position(position_type: str | None, asset_name: str | None) -> bool:
    """Whether one position is cash.

    The single definition ``instructions.is_cash_holding`` and the summary SQL both use.
    ``position_type`` of cash is Seccl's word and matches the name pattern. SEI's currency
    code is :data:`_CURRENCY_POSITION_TYPE`. A fund whose name matches
    :data:`CASH_NAME_NEEDLE` counts too, which is how a money-market holding stays in cash.
    """
    asset_type = (position_type or "").strip()
    if asset_type.lower() == _CURRENCY_POSITION_TYPE:
        return True
    haystack = f"{asset_type} {asset_name or ''}".lower()
    return _CASH_NAME.search(haystack) is not None


def classify_holding(position_type: str | None, asset_name: str | None) -> str:
    """The summary's asset class for one position. Cash first, then the class needles."""
    if is_cash_position(position_type, asset_name):
        return "Cash"
    haystack = f"{asset_name or ''} {position_type or ''}".lower()
    for label, needle in _CLASS_NEEDLES:
        if re.search(needle, haystack):
            return label
    return "Equities"


def summarise_positions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Position rows in the summary shape: one row per account and class.

    The demo lake and the dev extract do not run the summary SQL. They aggregate here,
    with :func:`classify_holding`, so a fallback still answers cash and growth.
    """
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        external_id = row.get("account_external_id")
        if not isinstance(external_id, str) or not external_id:
            continue
        asset_type = row.get("asset_type")
        asset_name = row.get("asset_name")
        klass = classify_holding(
            None if asset_type is None else str(asset_type),
            None if asset_name is None else str(asset_name),
        )
        key = (external_id, klass)
        bucket = buckets.get(key)
        if bucket is None:
            bucket = {
                "account_external_id": external_id,
                "klass": klass,
                "value": 0.0,
                "book_cost": 0.0,
                "growth": 0.0,
            }
            buckets[key] = bucket
        bucket["value"] += _as_float(row.get("value"))
        bucket["book_cost"] += _as_float(row.get("book_cost"))
        bucket["growth"] += _as_float(row.get("growth"))
    return list(buckets.values())


def _as_float(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        return 0.0
    return float(value)


# Overview donut + cash: one grouped read instead of every position. Cash is
# :func:`is_cash_position` (word boundaries, plus SEI's currency code) so the summary
# and ``instructions.is_cash_holding`` cannot disagree. ``{relation}`` is filled by the
# slice reader. The needles are the same constants the Python classifier uses, spliced
# in here so the two cannot drift. They are not values from a request.
_HOLDINGS_SUMMARY_SQL = (
    """
SELECT p.account_external_id,
       CASE
         WHEN lower(trim(coalesce(p.position_type, ''))) = '__CURRENCY_TYPE__'
           OR regexp_matches(
                lower(trim(coalesce(p.position_type, '')) || ' ' || coalesce(p.asset_name, '')),
                '__CASH_NEEDLE__')
           THEN 'Cash'
         WHEN regexp_matches(
                lower(coalesce(p.asset_name, '') || ' ' || coalesce(p.position_type, '')),
                '__FIXED_INCOME__')
           THEN 'Fixed income'
         WHEN regexp_matches(
                lower(coalesce(p.asset_name, '') || ' ' || coalesce(p.position_type, '')),
                '__PROPERTY__')
           THEN 'Property & infra'
         WHEN regexp_matches(
                lower(coalesce(p.asset_name, '') || ' ' || coalesce(p.position_type, '')),
                '__ALTERNATIVES__')
           THEN 'Alternatives'
         ELSE 'Equities'
       END AS klass,
       SUM(p.market_value) AS value,
       SUM(p.book_cost) AS book_cost,
       SUM(p.unrealised_gain) AS growth
FROM {relation} p
WHERE p.account_external_id IN (SELECT UNNEST(?::VARCHAR[]))
GROUP BY 1, 2
""".replace("__CASH_NEEDLE__", CASH_NAME_NEEDLE)
    .replace("__CURRENCY_TYPE__", _CURRENCY_POSITION_TYPE)
    .replace("__FIXED_INCOME__", _FIXED_INCOME_NEEDLE)
    .replace("__PROPERTY__", _PROPERTY_NEEDLE)
    .replace("__ALTERNATIVES__", _ALTERNATIVES_NEEDLE)
)


def _call_with_context(ctx: otel_context.Context, fn: Any, /, *args: Any, **kwargs: Any) -> Any:
    """Run ``fn`` on a worker thread inside the caller's trace context.

    ``asyncio.to_thread`` copies contextvars, and that copy has not been enough: the acquire and
    query spans started inside the thread were not children of the request, so the warehouse wait
    showed up as an empty gap on ``GET /crm/contacts``. Attaching the context captured on the event
    loop puts those spans under the holdings summary that wraps this call.
    """
    token = otel_context.attach(ctx)
    try:
        return fn(*args, **kwargs)
    finally:
        otel_context.detach(token)


async def firm_holdings_summary(
    provider: str, tenant_uid: str, external_account_ids: list[str]
) -> list[dict[str, Any]]:
    """Per-account, per-class totals for a client's overview — DuckDB aggregates, not the book.

    The span starts on the event loop, before the worker thread, so the wait itself is visible
    on the request. ``lake.core.acquire`` and ``lake.core.query`` nest under it.
    """
    if not external_account_ids:
        return []
    require_uid(tenant_uid)
    tracer = _tracer()
    with tracer.start_as_current_span("lake.holdings.summary") as span:
        _span_attrs(span, provider, "positions_summary")
        span.set_attribute("lake.accounts", len(external_account_ids))
        ctx = otel_context.get_current()
        return await guarded(
            provider,
            asyncio.to_thread(
                _call_with_context,
                ctx,
                read_slice,
                provider,
                tenant_uid,
                _HOLDINGS_SUMMARY_SQL,
                [external_account_ids],
                statement="positions_summary",
                relation="positions",
            ),
        )


async def search_firm_holdings(
    provider: str,
    tenant_uid: str,
    external_account_ids: list[str],
    *,
    q: str,
    limit: int,
) -> list[dict[str, Any]]:
    """First matching positions for Spotlight search, filtered and capped inside DuckDB."""
    if not external_account_ids:
        return []
    require_uid(tenant_uid)
    pattern = contains_pattern(q)
    return await guarded(
        provider,
        asyncio.to_thread(
            read_slice,
            provider,
            tenant_uid,
            _SEARCH_HOLDINGS_SQL,
            [external_account_ids, pattern, pattern, limit],
            statement="positions_search",
            relation="positions",
        ),
    )


# The recent-activity feed and the value chart, from the same conformed layer.
#
# `platform.valuations` is the account-level daily series the fixture derived by SUMMING asset
# valuations per account per day; the conformed layer publishes the account's own `market_value`
# per `as_of_date`, so this reads a published figure rather than re-deriving one.
_SERIES_SQL = """
SELECT v.account_external_id,
       v.as_of_date              AS date,
       CAST(v.market_value AS DOUBLE) AS value
FROM {relation} v
WHERE v.account_external_id IN (SELECT UNNEST(?::VARCHAR[]))
  AND v.market_value IS NOT NULL
ORDER BY v.as_of_date
"""

# `asset_name` is not on the conformed transaction — it is on the INSTRUMENT, which is the right
# place for it (one name per instrument, not one per movement). LEFT JOIN, so a transaction whose
# instrument has not been conformed yet keeps its row and loses only its label.
#
# `movement_type` has no conformed equivalent, and the canonical `type` is a better answer than
# the one it replaced: `crm._activity_type` matches needles ("div", "fee", "withdraw", "sell",
# "buy") against the concatenation of movement_type/type/sub_type, and `crm._signed` now reads
# that same canonical slot. The canonical type IS that vocabulary — so it is passed in that
# slot rather than left blank, while `type` and `sub_type` keep the vendor's own words.
_TRANSACTIONS_SQL = """
SELECT t.account_external_id,
       t.transaction_date        AS date,
       t.source_type             AS type,
       t.source_subtype          AS sub_type,
       t.type                    AS movement_type,
       i.name                    AS asset_name,
       t.isin,
       CAST(coalesce(t.net_amount, t.gross_amount) AS DOUBLE) AS amount,
       t.quantity,
       t.unit_price              AS price,
       t.currency,
       t.status
FROM {relation} t
LEFT JOIN {instruments} i ON i.external_id = t.instrument_key
WHERE t.account_external_id IN (SELECT UNNEST(?::VARCHAR[]))
ORDER BY t.transaction_date DESC NULLS LAST
LIMIT ?
"""


def _tracer() -> trace.Tracer:
    return trace.get_tracer("portals-api")


def read_slice(
    provider: str,
    tenant_uid: str,
    sql: str,
    params: list[Any],
    *,
    statement: str | None = None,
    **relations: str,
) -> list[dict[str, Any]]:
    """Run one statement on the firm's slice of the provider's core catalogue.

    Borrows a pooled connection. ``statement`` is a low-cardinality name (``positions``,
    ``positions_summary``) for the trace — the SQL text and the tenant uid stay off the span.
    """
    tracer = _tracer()
    with tracer.start_as_current_span("lake.core.acquire") as span:
        _span_attrs(span, provider, statement)
        conn = lake_core.checkout(provider)
    # ``return`` inside ``try`` does not run ``else``, so the check-in has to be a
    # ``finally``. A query error discards the connection; a normal return puts it back.
    discard = False
    try:
        with tracer.start_as_current_span("lake.core.query") as span:
            _span_attrs(span, provider, statement)
            named = {
                key: f"tenant_{tenant_model_key(tenant_uid)}.{name}"
                for key, name in relations.items()
            }
            cursor = conn.execute(sql.format(**named), params)  # noqa: S608 - uid validated above
            names = [d[0] for d in cursor.description or []]
            return [
                {name: _jsonable(value) for name, value in zip(names, row, strict=True)}
                for row in cursor.fetchall()
            ]
    except BaseException:
        discard = True
        raise
    finally:
        lake_core.checkin(conn, discard=discard)


def _span_attrs(span: Any, provider: str, statement: str | None) -> None:
    span.set_attribute("lake.provider", provider)
    if statement:
        span.set_attribute("lake.statement", statement)


async def firm_value_series(
    provider: str, tenant_uid: str, external_account_ids: list[str]
) -> list[dict[str, Any]]:
    """The daily value series for these accounts, from the firm's own slice."""
    if not external_account_ids:
        return []
    require_uid(tenant_uid)
    return await guarded(
        provider,
        asyncio.to_thread(
            read_slice,
            provider,
            tenant_uid,
            _SERIES_SQL,
            [external_account_ids],
            statement="valuations",
            relation="valuations",
        ),
    )


async def firm_transactions(
    provider: str, tenant_uid: str, external_account_ids: list[str], *, limit: int = 50
) -> list[dict[str, Any]]:
    """Recent movements for these accounts, newest first, from the firm's own slice."""
    if not external_account_ids:
        return []
    require_uid(tenant_uid)
    return await guarded(
        provider,
        asyncio.to_thread(
            read_slice,
            provider,
            tenant_uid,
            _TRANSACTIONS_SQL,
            [external_account_ids, max(1, min(limit, 500))],
            statement="transactions",
            relation="transactions",
            instruments="instruments",
        ),
    )


def by_firm(accounts: Mapping[Any, Mapping[str, Any]]) -> dict[tuple[str, str], list[str]]:
    """Group provider account ids by the (provider, firm) whose slice holds them.

    Almost always one firm — a book is a tenant's. Not assumed to be, because `book_session`'s RLS
    scope is a LINEAGE: a parent firm reading its own book legitimately sees accounts belonging to
    more than one tenant, and reading them all out of the first uid encountered would silently show
    one firm's positions under another's accounts.

    The PROVIDER is part of the key for the same reason the uid is. A slice is
    `tenant_{uid}.<relation>` inside ONE provider's catalogue, so an id read against the wrong
    catalogue finds nothing — and a firm can hold accounts at two custodians. Grouping by uid alone
    only worked while the caller hardcoded the provider, which is what left a SEI-only firm with an
    empty portal beside a real OLTP `value`.
    """
    grouped: dict[tuple[str, str], list[str]] = {}
    for key, account in accounts.items():
        uid = account.get("tenant_uid")
        provider = account.get("provider")
        # FROM THE RECORD, not from the key. `external_id` is a PROVIDER's id for the account, so
        # a caller keying its map by that alone collapses two accounts held at two custodians
        # whose ids happen to collide as strings — and the map is what the reads are addressed by.
        # Callers key by `(provider, external_id)` now; the key is the fallback for one that
        # still keys by the bare id.
        external_id = account.get("external_id")
        if not isinstance(external_id, str) or not external_id:
            external_id = key[1] if isinstance(key, tuple) else key
        if isinstance(uid, str) and uid and isinstance(provider, str) and provider:
            grouped.setdefault((provider, uid), []).append(str(external_id))
    return grouped
