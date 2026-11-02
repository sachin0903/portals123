"""The firm-configured catalogue: products, models, fee packages, providers, terminology.

Reads and writes `0032_platform_catalogue`. Everything here is tenant-scoped, and every write
runs elevated (`service_role`) because the admin surface acts on a firm it is not a member of
-- the tenant id comes from the path, is checked against `auth.tenants`, and is then the only
scope. Nothing trusts a caller-supplied tenant on the row itself.

Validation lives here rather than in the router so the rules are testable without HTTP:
a fee `rule` must match its `fee_type`, and a model version's allocations must be a real
allocation (nothing negative, nothing over 100, and the lines plus cash within a tolerance of
100). Those are the two places a bad row would look plausible and quietly misprice or
misallocate.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import listing

# `providers_mod`, not `providers`: this module already has a function of that name (the
# third-party / transfer register reader), which is a different concept entirely.
from app import providers as providers_mod

# Money and percentages arrive from JSON as float; a tolerance keeps 33.33 x 3 from failing.
ALLOCATION_TOLERANCE = Decimal("0.05")

FEE_TYPES = ("fixed", "percentage", "tiered")
CHARGE_BASES = ("aum", "contribution", "flat", "transaction")


def completed_age(born: date, on: date | None = None) -> int:
    """Years of age completed on ``on`` (today when omitted)."""
    as_of = on or date.today()
    years = as_of.year - born.year
    if (as_of.month, as_of.day) < (born.month, born.day):
        years -= 1
    return years


#: Age bounds the LAW sets, not the firm. A Junior ISA belongs to a child: the owner must be under
#: 18, which is why the ceiling is 17 completed years.
#:
#: IT LIVES HERE BECAUSE A ROW CANNOT BE TRUSTED TO CARRY IT. `0162_product_ages` added the columns
#: and backfilled `max_age = 17` for every JISA that existed at the time — and that is all a
#: backfill can do. A JISA created afterwards (an operator adding one to the shelf, a later seeding
#: pass, a custodian-discovered shelf item) gets NULL, and every layer that enforces the rule opens
#: with `if min_age is None and max_age is None: return None` — so the kickoff wizard stops showing
#: the error, stops blocking Continue, and the API stops refusing, all at once and all silently.
#: The rule is not the firm's to configure, so it is not stored as the firm's configuration.
STATUTORY_MAX_AGE: dict[str, int] = {"JISA": 17}

#: Contact-type limits set by the wrapper rather than by the firm. Product configuration may
#: narrow these further, but it cannot make an organisation eligible for a person-only wrapper.
STATUTORY_CLIENT_TYPES: dict[str, frozenset[str]] = {
    "ISA": frozenset({"individual"}),
    "JISA": frozenset({"individual"}),
}


def effective_age_bounds(
    product_type: str | None, min_age: int | None, max_age: int | None
) -> tuple[int | None, int | None]:
    """The bounds to enforce: the product's own, narrowed by any statutory ceiling.

    Configuration may only TIGHTEN the law — a firm offering a JISA to under-16s is its own
    business, one offering it to a 25-year-old is not a JISA. So the statutory value applies when
    the row carries nothing, and wins when the row is wider.
    """
    ceiling = STATUTORY_MAX_AGE.get((product_type or "").upper())
    if ceiling is not None and (max_age is None or max_age > ceiling):
        max_age = ceiling
    return min_age, max_age


def _pg_enum_array(value: object) -> list[str]:
    """psycopg has no loader for a custom enum array, so ``RETURNING *`` yields ``{individual}``."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    text_value = str(value)
    if text_value.startswith("{") and text_value.endswith("}"):
        inner = text_value[1:-1]
        if not inner:
            return []
        return [part.strip().strip('"') for part in inner.split(",") if part.strip()]
    return [text_value]


def _product_out(row: dict[str, Any]) -> dict[str, Any]:
    # `RETURNING *` includes 0211's generated search text, which is not part of the product.
    row.pop("_search", None)
    for key in ("allowed_custodies", "allowed_client_types", "allowed_currencies"):
        if key in row:
            row[key] = _pg_enum_array(row[key])
    return row


def effective_client_types(
    product_type: str | None, allowed_client_types: list[str] | tuple[str, ...] | None
) -> list[str]:
    """Configured owner types, narrowed by any statutory wrapper rule."""
    configured = _pg_enum_array(allowed_client_types) or ["individual", "organisation"]
    statutory = STATUTORY_CLIENT_TYPES.get((product_type or "").upper())
    if statutory is None:
        return configured
    return [client_type for client_type in configured if client_type in statutory]


def product_age_error(
    *,
    name: str,
    min_age: int | None,
    max_age: int | None,
    dob: date | None,
    on: date | None = None,
) -> str | None:
    """Refuse an owner whose completed age is outside the product's bounds."""
    if min_age is None and max_age is None:
        return None
    label = name or "This product"
    if dob is None:
        return f"{label} needs the owner's date of birth."
    if dob > (on or date.today()):
        # A FUTURE date of birth clears both bounds: `age` comes out NEGATIVE, which is below no
        # floor worth setting and under every ceiling. A JISA has a ceiling of 17 and no floor, so
        # an owner not yet born was accepted — and a slipped year (2027 for 2017) is the ordinary
        # way to mistype one.
        return "Date of birth cannot be in the future."
    age = completed_age(dob, on)
    if min_age is not None and age < min_age:
        return f"{label} is only available from age {min_age}."
    if max_age is not None and age > max_age:
        return f"{label} is only available up to age {max_age}."
    return None


PAYERS = ("client", "firm", "provider")


class CatalogueError(ValueError):
    """A caller-fixable problem: the router turns it into a 422."""


# ---------------------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------------------


def validate_fee_rule(fee_type: str, rule: Any) -> dict[str, Any]:
    """Check a fee rule against its type, and return it normalised.

    The three shapes are the ones the reference implementation settled on and `0002` already
    matched. A rule that does not match its type is the failure mode worth catching: a
    `tiered` package holding `{"rate": 0.75}` reads as valid until it prices something.
    """
    if fee_type not in FEE_TYPES:
        raise CatalogueError(f"fee_type must be one of {', '.join(FEE_TYPES)}")
    if not isinstance(rule, dict):
        raise CatalogueError("rule must be an object")

    if fee_type == "fixed":
        amount = _number(rule.get("amount"), "rule.amount")
        if amount < 0:
            raise CatalogueError("rule.amount cannot be negative")
        return {"amount": float(amount)}

    if fee_type == "percentage":
        rate = _number(rule.get("rate"), "rule.rate")
        if not 0 <= rate <= 100:
            raise CatalogueError("rule.rate must be between 0 and 100")
        return {"rate": float(rate)}

    tiers = rule.get("tiers")
    if not isinstance(tiers, list) or not tiers:
        raise CatalogueError("a tiered rule needs at least one tier")
    normalised: list[dict[str, Any]] = []
    previous_to: Decimal | None = Decimal(0)
    for index, tier in enumerate(tiers):
        if not isinstance(tier, dict):
            raise CatalogueError(f"tier {index} must be an object")
        start = _number(tier.get("from"), f"tier {index} `from`")
        rate = _number(tier.get("rate"), f"tier {index} `rate`")
        end_raw = tier.get("to")
        end = None if end_raw is None else _number(end_raw, f"tier {index} `to`")
        if not 0 <= rate <= 100:
            raise CatalogueError(f"tier {index} rate must be between 0 and 100")
        if end is not None and end <= start:
            raise CatalogueError(f"tier {index} ends at or before it starts")
        # Bands must be contiguous and ordered. A gap silently charges nothing in it, and an
        # overlap charges twice -- both look like data until an invoice is wrong.
        if previous_to is None:
            raise CatalogueError("an open-ended tier must be the last one")
        if start != previous_to:
            raise CatalogueError(
                f"tier {index} starts at {start} but the previous band ended at {previous_to}"
            )
        previous_to = end
        normalised.append(
            {"from": float(start), "to": None if end is None else float(end), "rate": float(rate)}
        )
    return {"tiers": normalised}


def validate_allocations(
    allocations: list[dict[str, Any]], cash_allocation: Any = None
) -> tuple[list[dict[str, Any]], Decimal]:
    """Check a model version's allocation lines, and return them with the cash figure.

    A model that does not add up is the whole point of storing allocations rather than a
    number: 97% invested is either a 3% cash position or a missing line, and the difference
    matters to every account tracking it. Cash is explicit, so the total must reach 100.
    """
    if not allocations:
        raise CatalogueError("a version needs at least one allocation line")
    cash = Decimal(0) if cash_allocation in (None, "") else _number(cash_allocation, "cash")
    if cash < 0:
        raise CatalogueError("cash allocation cannot be negative")

    seen: set[str] = set()
    lines: list[dict[str, Any]] = []
    total = cash
    for index, line in enumerate(allocations):
        if not isinstance(line, dict):
            raise CatalogueError(f"allocation {index} must be an object")
        percentage = _number(line.get("percentage"), f"allocation {index} percentage")
        if percentage <= 0:
            raise CatalogueError(f"allocation {index} must be greater than zero")
        # The identity of the holding: our instrument when we have one, else whatever the
        # provider called it. A line with neither cannot be reconciled to a position.
        key = str(
            line.get("instrument_id") or line.get("external_asset_id") or line.get("isin") or ""
        ).strip()
        if not key:
            raise CatalogueError(
                f"allocation {index} needs an instrument_id, external_asset_id or isin"
            )
        if key in seen:
            raise CatalogueError(f"allocation {index} repeats {key}")
        seen.add(key)
        total += percentage
        lines.append(
            {
                "instrument_id": line.get("instrument_id"),
                "external_asset_id": line.get("external_asset_id"),
                "isin": line.get("isin"),
                "name": line.get("name"),
                "percentage": float(percentage),
                "sort_order": index,
            }
        )
    if abs(total - Decimal(100)) > ALLOCATION_TOLERANCE:
        raise CatalogueError(f"allocations plus cash total {total}%, not 100%")
    return lines, cash


def _number(value: Any, label: str) -> Decimal:
    if value is None or value == "":
        raise CatalogueError(f"{label} is required")
    try:
        return Decimal(str(value))
    except Exception as error:  # noqa: BLE001 - any parse failure is the same answer
        raise CatalogueError(f"{label} must be a number") from error


# ---------------------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------------------


async def _rows(session: AsyncSession, sql: str, **params: Any) -> list[dict[str, Any]]:
    result = await session.execute(text(sql), params)
    return [dict(row) for row in result.mappings()]


def platforms() -> list[dict[str, Any]]:
    """The custodians a reference can point at.

    FROM CODE, not from a table (0131). `platform.platforms` existed to be a foreign-key target
    for a member of a closed vocabulary; the vocabulary is `providers.PROVIDERS` and the database
    admits it as `platform.provider_code`, so there is nothing to read and no session to take. The
    `id` every caller keyed on IS the provider code now, which is why the two are returned as the
    same value: a shelf reference that used to be an integer nobody could read is the word
    `seccl`.
    """
    return [
        {"id": code, "provider": code, "name": providers_mod.meta(code)["name"]}
        for code in sorted(providers_mod.PROVIDERS, key=lambda c: providers_mod.meta(c)["name"])
    ]


async def instruments(session: AsyncSession, limit: int = 500) -> list[dict[str, Any]]:
    """The securities a model allocation can name — the PICKER's slice, first `limit` by name.

    Global (0041: an ISIN is not tenant data), so no tenant filter, and capped because this feeds
    a picker: a firm with 4,000 instruments needs a search box, not 4,000 options in the DOM.
    Ordered by name so the cap takes a stable slice rather than whatever the heap returns.

    NOT what the Instruments TABLE reads — see `instruments_page`. A capped list behind a search
    box answers "we do not hold that" for anything outside the cap, which is a wrong answer rather
    than a slow one.
    """
    return await _rows(
        session,
        """
        SELECT id, isin, sedol, name, asset_type, currency, exchange_code
        FROM platform.instruments
        ORDER BY name
        LIMIT :limit
        """,
        limit=limit,
    )


#: Sort keys the instruments table offers, mapped to the column each one orders by. An allowlist,
#: not interpolation: `sort` reaches SQL in an identifier position.
_INSTRUMENT_SORTS: dict[str, str] = {
    "name": "name",
    "isin": "isin",
    "sedol": "sedol",
    "asset_type": "asset_type",
    "currency": "currency",
    "exchange_code": "exchange_code",
}


#: The instrument columns a caller may narrow by, and the bound parameter each one reads. A CLOSED
#: SET for the same reason ``_INSTRUMENT_SORTS`` is: the keys arrive from a query string, so a name
#: not listed here is ignored rather than reaching the WHERE clause.
_INSTRUMENT_FILTERS: dict[str, str] = {
    "asset_type": "asset_type",
    "currency": "currency",
    "exchange_code": "exchange_code",
}


def _instrument_where(
    active: dict[str, list[str]], searching: bool, *, skip: str | None = None
) -> str:
    """The WHERE clause, optionally with ONE filter lifted — which is what a facet is.

    A facet computed over rows already narrowed by its own filter is a one-way door: pick "USD"
    and USD becomes the only currency on offer, with no way back except clearing by hand. The
    same rule ``app/listing.py`` states for the in-Python listings, applied here in SQL.

    Each filter is now a LIST, matched with ``= ANY(...)``: "USD or GBP" is a real question this
    endpoint can answer in one request, the same OR ``admin.py::list_audit`` already gives its own
    multi-valued filters. Several values chosen for one filter still leaves every OTHER filter's
    own clause lifted when that other filter computes its facet — the one-way-door rule holds
    column by column, not just value by value.

    The search is PARENTHESISED. It is a three-way OR, and ``AND`` binds tighter than ``OR``, so
    an unbracketed ``name ILIKE :q OR isin LIKE :q OR sedol LIKE :q AND currency = :currency``
    would apply the currency filter to the SEDOL branch alone and return every row matching the
    name — a filter that silently does nothing on two thirds of the search.
    """
    clauses: list[str] = []
    if searching:
        # The three identifiers a person actually searches by. ISIN/SEDOL are compared upper-cased
        # rather than with ILIKE on both sides — they are stored upper and a lowercased needle
        # would match nothing, which is the same silent-empty failure in miniature.
        clauses.append(
            "(name ILIKE :q ESCAPE '\\' "
            "OR upper(coalesce(isin, '')) LIKE upper(:q) ESCAPE '\\' "
            "OR upper(coalesce(sedol, '')) LIKE upper(:q) ESCAPE '\\')"
        )
    for key, column in _INSTRUMENT_FILTERS.items():
        if key != skip and key in active:
            clauses.append(f"{column} = ANY(CAST(:{key} AS text[]))")
    return f" WHERE {' AND '.join(clauses)}" if clauses else ""


async def instruments_page(
    session: AsyncSession,
    *,
    limit: int = 100,
    offset: int = 0,
    sort: str = "name",
    dir: str = "asc",
    q: str | None = None,
    filters: dict[str, str | Sequence[str] | None] | None = None,
) -> dict[str, Any]:
    """The instruments TABLE: one window, the full count, and the filter vocabularies.

    THE DEFECT THIS REPLACES. The table read `instruments()` — the picker's first 500 by name —
    and filtered that in the browser. `platform.instruments` is global and un-scoped, so it holds
    every tenant's securities: SEI's first sync alone landed 6,170. Searching for one outside the
    first 500 by name found nothing, and an empty table under a search box reads as "we do not hold
    that security". Silent truncation is worse than a slow page, because there is no symptom.

    `{items, total, facets}` with limit/offset, matching `providers()` and `/crm/accounts` — one
    pagination contract in this API, not a second one invented here. `total` counts before the
    window, which is the number the caller needs to say "100 of 6,170"; counting the window would
    make every listing exactly one window long.

    Sorting is server-side because the window is: ordering one window in the browser sorts 100 of
    6,170 rows and presents it as the order of the whole universe. `filters` is server-side for
    exactly the same reason — narrowing the window to "USD" would answer "we hold 12 USD
    securities" when it means "12 of the 200 on this screen".

    `facets` are the values each filter may take, computed over the match set with that filter's
    own clause lifted (see `_instrument_where`), so the options describe the universe rather than
    what the current window happens to contain.
    """
    order = _INSTRUMENT_SORTS.get(sort, "name")
    direction = "DESC" if str(dir).lower() == "desc" else "ASC"
    active = {
        key: cleaned
        for key in _INSTRUMENT_FILTERS
        if (cleaned := listing.clean_many((filters or {}).get(key))) is not None
    }
    searching = bool(q and q.strip())
    params: dict[str, Any] = {
        "limit": max(1, min(limit, 500)),
        "offset": max(0, offset),
        **active,
    }
    if searching:
        # Escaped before the wildcards are added, or a security literally named "A% Fund" would
        # match the entire universe and the search would look broken rather than broad.
        params["q"] = f"%{listing.escape_like(q.strip())}%"  # type: ignore[union-attr]
    where = _instrument_where(active, searching)
    total = (
        await session.execute(
            text(f"SELECT count(*) FROM platform.instruments{where}"),  # noqa: S608
            params,
        )
    ).scalar_one()
    items = await _rows(
        session,
        # `order`/`direction` come from the allowlist above, never from the caller's string.
        # A tie-break on the primary key, so paging is stable: without it two rows sharing a name
        # can swap between windows and one of them is never seen at all.
        f"""
        SELECT id, isin, sedol, name, asset_type, currency, exchange_code
        FROM platform.instruments{where}
        ORDER BY {order} {direction} NULLS LAST, id {direction}
        LIMIT :limit OFFSET :offset
        """,  # noqa: S608
        **params,
    )
    facets: dict[str, list[str]] = {}
    for key, column in _INSTRUMENT_FILTERS.items():
        lifted = _instrument_where(active, searching, skip=key)
        # `column` is a VALUE of the closed `_INSTRUMENT_FILTERS` map and `lifted` is built from
        # the same map, so neither carries caller text; the filter values stay bound parameters.
        sql = (
            f"SELECT DISTINCT {column} AS value FROM platform.instruments{lifted}"  # noqa: S608
            f"{' AND' if lifted else ' WHERE'} {column} IS NOT NULL AND {column} <> '' "
            "ORDER BY value"
        )
        rows = (await session.execute(text(sql), params)).all()
        facets[key] = [str(value) for (value,) in rows]
    return {"items": items, "total": int(total or 0), "facets": facets}


async def products(session: AsyncSession, tenant_id: int) -> list[dict[str, Any]]:
    """The firm's product shelf, each with the reference every platform knows it by."""
    return await _rows(
        session,
        """
        SELECT p.id, p.uid, p.tenant_id, p.product_type, p.name, p.code, p.description, p.currency,
               p.is_pension, p.enabled, p.sort_order,
               p.allowed_custodies::text[] AS allowed_custodies, p.max_parties,
               p.allowed_client_types::text[] AS allowed_client_types, p.min_age, p.max_age,
               p.bank_details_allowed, p.allowed_currencies,
               coalesce(
                 (SELECT json_agg(json_build_object(
                     'id', pp.id, 'provider', pp.provider,
                     'external_id', pp.external_id, 'external_name', pp.external_name,
                     'account_open_reference', pp.account_open_reference,
                     'role', pp.role, 'enabled', pp.enabled)
                     ORDER BY (pp.role = 'custodian') DESC NULLS LAST, pp.provider)
                  FROM platform.product_platforms pp
                  WHERE pp.product_id = p.id), '[]'::json) AS platforms
        FROM platform.products p
        WHERE p.tenant_id = :tenant_id
        ORDER BY p.sort_order, p.name
        """,
        tenant_id=tenant_id,
    )


async def models(session: AsyncSession, tenant_id: int) -> list[dict[str, Any]]:
    """Models with their published version, so the listing can show what is actually live."""
    return await _rows(
        session,
        """
        SELECT m.id, m.uid, m.name, m.code, m.model_type, m.aim, m.target_return,
               m.target_return_basis, m.target_return_horizon_years, m.benchmark,
               m.risk_rating, m.risk_profile, m.investment_decision, m.model_charge,
               m.vatable, m.suspend_rebalances, m.cash_allocation, m.currency, m.status,
               m.enabled,
               -- The wrappers the model may be held in (0002's `products`, populated by the
               -- materialiser from the provider). What the catalogue page filters by.
               --
               -- CAST TO text[], and it is not cosmetic. `products` is `platform.product_type[]`,
               -- a custom enum array psycopg has no loader for, so it comes back as the raw
               -- Postgres literal STRING '{GIA,ISA,JISA,SIPP}'. The page then called `.some()` on
               -- a string and threw, so the whole models listing rendered nothing. text[] has a
               -- native loader and arrives as a real list.
               m.products::text[] AS products,
               v.version AS live_version, v.effective_from AS live_effective_from,
               (SELECT count(*) FROM platform.model_versions mv WHERE mv.model_id = m.id)
                 AS version_count,
               (SELECT count(*) FROM platform.model_commentary mc WHERE mc.model_id = m.id)
                 AS commentary_count
        FROM platform.models m
        LEFT JOIN LATERAL (
            SELECT mv.version, mv.effective_from FROM platform.model_versions mv
            WHERE mv.model_id = m.id AND mv.published_at IS NOT NULL
            ORDER BY mv.version DESC LIMIT 1
        ) v ON true
        WHERE m.tenant_id = :tenant_id
        ORDER BY m.name
        """,
        tenant_id=tenant_id,
    )


async def model_detail(
    session: AsyncSession, tenant_id: int, model_id: int
) -> dict[str, Any] | None:
    """One model with its whole allocation history and its commentary."""
    found = await _rows(
        session,
        """
        SELECT m.*, coalesce(
          (SELECT json_agg(json_build_object('provider', mp.provider,
                                             'external_id', mp.external_id) ORDER BY mp.provider)
           FROM platform.model_platforms mp
           WHERE mp.model_id = m.id), '[]'::json) AS platforms
        FROM platform.models m WHERE m.id = :model_id AND m.tenant_id = :tenant_id
        """,
        model_id=model_id,
        tenant_id=tenant_id,
    )
    if not found:
        return None
    model = found[0]
    model["versions"] = await _rows(
        session,
        """
        SELECT v.id, v.uid, v.version, v.effective_from, v.cash_allocation, v.asset_mix, v.note,
               v.published_at, v.created_at,
               coalesce(
                 (SELECT json_agg(json_build_object(
                     'id', a.id, 'instrument_id', a.instrument_id,
                     'external_asset_id', a.external_asset_id, 'isin', a.isin,
                     'name', a.name, 'percentage', a.percentage) ORDER BY a.sort_order)
                  FROM platform.model_allocations a WHERE a.model_version_id = v.id),
                 '[]'::json) AS allocations
        FROM platform.model_versions v
        WHERE v.model_id = :model_id AND v.tenant_id = :tenant_id
        ORDER BY v.version DESC
        """,
        model_id=model_id,
        tenant_id=tenant_id,
    )
    model["commentary"] = await _rows(
        session,
        """
        SELECT c.id, c.uid, c.title, c.body, c.period_start, c.period_end, c.model_version_id,
               c.published_at, c.created_at, c.updated_at
        FROM platform.model_commentary c
        WHERE c.model_id = :model_id AND c.tenant_id = :tenant_id
        ORDER BY coalesce(c.published_at, c.created_at) DESC
        """,
        model_id=model_id,
        tenant_id=tenant_id,
    )
    return model


async def fee_packages(session: AsyncSession, tenant_id: int) -> list[dict[str, Any]]:
    return await _rows(
        session,
        """
        SELECT f.id, f.uid, f.name, f.category, f.description, f.external_id,
               f.frequency, f.fee_type, f.rule,
               f.access, f.charge_basis, f.payer, f.vatable, f.minimum_amount,
               f.maximum_amount, f.applies_to, f.effective_from, f.effective_to, f.currency,
               f.enabled, f.is_default, f.optional, f.sort_order,
               f.product_id, f.model_id, p.name AS product_name, m.name AS model_name
        FROM platform.fee_packages f
        LEFT JOIN platform.products p ON p.id = f.product_id
        LEFT JOIN platform.models m ON m.id = f.model_id
        WHERE f.tenant_id = :tenant_id
        ORDER BY f.sort_order, f.name
        """,
        tenant_id=tenant_id,
    )


# Columns a provider listing may be ordered by. An allowlist, not the caller's string: `sort`
# reaches an ORDER BY, so accepting it verbatim is an injection and accepting an unknown column
# is a 500 on a page load. Anything else falls back to name.
PROVIDER_SORTS = {
    "name": "p.name",
    "enabled": "p.enabled",
    "status": "p.status",
    "masterparty_id": "p.masterparty_id",
    "origo_id": "p.origo_id",
}


async def provider_facets(session: AsyncSession, tenant_id: int) -> dict[str, list[str]]:
    """The distinct products and rails present in the WHOLE register.

    Deliberately unfiltered, and deliberately not derived from the page: the options must not
    change as the operator narrows, or choosing a product removes the option that would widen the
    search again and the filter becomes a one-way door. It is also not the static product enum —
    offering JISA when no provider in this firm's register can transfer one invites a search that
    can only come back empty.
    """
    products = (
        await session.execute(
            text(
                """
                SELECT DISTINCT unnest(products) AS product
                  FROM platform.transfer_providers
                 WHERE tenant_id = :tenant_id
                 ORDER BY product
                """
            ),
            {"tenant_id": tenant_id},
        )
    ).scalars()
    rails = (
        await session.execute(
            text(
                """
                SELECT DISTINCT rail ->> 'rail' AS rail
                  FROM platform.transfer_providers,
                       jsonb_array_elements(rails) AS rail
                 WHERE tenant_id = :tenant_id AND rail ->> 'rail' IS NOT NULL
                 ORDER BY rail
                """
            ),
            {"tenant_id": tenant_id},
        )
    ).scalars()
    return {"products": list(products), "rails": list(rails)}


async def providers(
    session: AsyncSession,
    tenant_id: int,
    kind: str,
    *,
    limit: int | None = None,
    offset: int = 0,
    sort: str = "name",
    dir: str = "asc",
    q: str | None = None,
    product: str | Sequence[str] | None = None,
    rail: str | Sequence[str] | None = None,
) -> dict[str, Any]:
    """Either registry, as one page plus the full count.

    The envelope is `{items, total}` with limit/offset -- the shape /crm/accounts and
    /crm/holdings already use -- so there is ONE pagination contract in the API rather than a
    second one invented here. `total` is the count before paging, which is the number the pager
    needs; counting the page would make every listing one page long.

    Sorting is server-side because paging is: ordering one page client-side sorts 25 of 929 rows
    and presents it as the order of the whole register.
    """
    table = _provider_table(kind)
    # rails/status/products only exist on the transfer table (0046). The third-party register
    # states ONE product (0130) where a ceding scheme takes several, so the two are projected
    # under one key the caller can read either way round.
    extra = (
        ", p.masterparty_id, p.origo_id, p.atg_reference, p.status, p.rails,"
        " p.products AS product_types"
        if kind == "transfer"
        else ", p.product, CASE WHEN p.product IS NULL THEN '{}'::text[]"
        " ELSE ARRAY[p.product] END AS product_types"
    )
    order = PROVIDER_SORTS.get(sort, "p.name")
    if kind != "transfer" and sort in ("masterparty_id", "origo_id"):
        # Those two columns only exist on transfer_providers; ordering the other table by them
        # would be a 500 rather than a fallback.
        order = "p.name"
    direction = "DESC" if str(dir).lower() == "desc" else "ASC"

    # Filters go in the WHERE, not into a list comprehension over the page: searching 25 of 661
    # rows would find nothing on page 1 and something different on page 2. `total` is counted
    # under the same predicate so the pager describes the filtered set.
    where = ["p.tenant_id = :tenant_id"]
    params: dict[str, Any] = {"tenant_id": tenant_id}
    if q:
        # Name OR either reference: an operator holding an Origo id from a transfer form needs to
        # find the provider by it, and the field says so. Searching only the name would make the
        # placeholder a lie.
        if kind == "transfer":
            where.append("(p.name ILIKE :q OR p.origo_id ILIKE :q OR p.atg_reference ILIKE :q)")
        else:
            where.append("p.name ILIKE :q")
        params["q"] = f"%{q.strip()}%"
    if kind == "transfer":
        chosen_products = listing.clean_many(product)
        if chosen_products:
            # `&&` (overlap), not repeated `= ANY`: it asks whether two arrays share a
            # member, which is the OR this needs, and products[] is GIN-indexed (0046).
            where.append("p.products && CAST(:product AS text[])")
            params["product"] = [value.strip().upper() for value in chosen_products]
        chosen_rails = listing.clean_many(rail)
        if chosen_rails:
            # `rails` is `[{rail, ...}, ...]` per provider, so this is an EXISTS over the unpacked
            # elements: jsonb containment reads one exact object and cannot mean "any of several".
            where.append(
                "EXISTS (SELECT 1 FROM jsonb_array_elements(p.rails) AS rail_elem "
                "WHERE rail_elem ->> 'rail' = ANY(CAST(:rail AS text[])))"
            )
            params["rail"] = [value.strip() for value in chosen_rails]
    predicate = " AND ".join(where)

    total = (
        await session.execute(
            text(
                f"SELECT count(*) FROM platform.{table} p WHERE {predicate}"  # noqa: S608
            ),
            params,
        )
    ).scalar_one()

    # A tie-break on id keeps paging stable: without it two rows with the same name can swap
    # between pages and one of them is never seen.
    items = await _rows(
        session,
        f"""
        SELECT p.id, p.uid, p.name, p.description, p.enabled{extra}
        FROM platform.{table} p
        WHERE {predicate}
        ORDER BY {order} {direction} NULLS LAST, p.id
        LIMIT :limit OFFSET :offset
        """,  # noqa: S608 - table, order and predicate are built from allowlists, never the caller
        limit=limit,
        offset=max(0, offset),
        **params,
    )
    return {"items": items, "total": int(total)}


def _provider_table(kind: str) -> str:
    """The register a `kind` names. There is no companion join table any more (0130): each
    register states its own products — `transfer_providers.products` (a `text[]`, GIN-indexed by
    0046 for exactly this filter) and `third_party_providers.product` — and the two join tables
    that modelled the same fact a second time held 0 rows on every deployment."""
    if kind == "third_party":
        return "third_party_providers"
    if kind == "transfer":
        return "transfer_providers"
    raise CatalogueError("kind must be 'third_party' or 'transfer'")


async def terms(session: AsyncSession, tenant_id: int) -> list[dict[str, Any]]:
    """The overridable vocabulary, defaults merged with this firm's overrides.

    A LEFT JOIN from the catalogue, not a union: the catalogue decides which keys exist, so a
    stale override for a removed key cannot resurrect a term the portals no longer read.
    """
    return await _rows(
        session,
        """
        SELECT t.key, t.category, t.default_label, t.default_plural, t.description,
               t.sort_order, o.label, o.plural, o.updated_at,
               coalesce(o.label, t.default_label) AS effective_label,
               coalesce(o.plural, t.default_plural) AS effective_plural,
               (o.id IS NOT NULL) AS overridden
        FROM l10n.terms t
        LEFT JOIN l10n.overrides o ON o.term_key = t.key AND o.tenant_id = :tenant_id
        ORDER BY t.sort_order, t.key
        """,
        tenant_id=tenant_id,
    )


# ---------------------------------------------------------------------------------------
# Writes. Each returns the row it wrote, so the caller need not re-read.
# ---------------------------------------------------------------------------------------

_PRODUCT_FIELDS = (
    "product_type",
    "name",
    "code",
    "description",
    "currency",
    "is_pension",
    "allowed_custodies",
    "allowed_client_types",
    "allowed_currencies",
    "max_parties",
    "min_age",
    "max_age",
    "bank_details_allowed",
    "enabled",
    "sort_order",
)


async def upsert_product(
    session: AsyncSession, tenant_id: int, payload: dict[str, Any], product_id: int | None = None
) -> dict[str, Any]:
    fields = {key: payload[key] for key in _PRODUCT_FIELDS if key in payload}
    if (
        str(fields.get("product_type") or "").upper() in {"ISA", "JISA"}
        and "allowed_currencies" not in fields
    ):
        fields["allowed_currencies"] = ["GBP"]
    # A pension wrapper defaults to is_pension ON CREATE, rather than the column's false: a SIPP
    # created unflagged reads as a non-pension and hides the contribution, crystallisation and
    # safeguarding UI that gates on it. It stays editable — a wrapper whose answer differs (a
    # TPPENSION we do not administer) is unticked explicitly on the product.
    if product_id is None and "is_pension" not in fields:
        if str(fields.get("product_type") or "").upper() in {"SIPP", "SSAS", "PENSION"}:
            fields["is_pension"] = True
    if "allowed_custodies" in fields:
        raw = fields["allowed_custodies"]
        if not isinstance(raw, list) or not raw:
            raise CatalogueError("allowed_custodies must contain onshore, offshore, or both")
        custodies = list(dict.fromkeys(str(value).strip().lower() for value in raw))
        if any(value not in {"onshore", "offshore"} for value in custodies):
            raise CatalogueError("allowed_custodies must contain only onshore or offshore")
        fields["allowed_custodies"] = custodies
    if "allowed_client_types" in fields:
        raw = fields["allowed_client_types"]
        if not isinstance(raw, list) or not raw:
            raise CatalogueError(
                "allowed_client_types must contain individual, organisation, or both"
            )
        client_types = list(dict.fromkeys(str(value).strip().lower() for value in raw))
        if any(value not in {"individual", "organisation"} for value in client_types):
            raise CatalogueError(
                "allowed_client_types must contain only individual or organisation"
            )
        fields["allowed_client_types"] = client_types
    if "allowed_currencies" in fields:
        raw = fields["allowed_currencies"]
        if not isinstance(raw, list) or not raw:
            raise CatalogueError("allowed_currencies must contain at least one currency")
        currencies = list(dict.fromkeys(str(value).strip().upper() for value in raw))
        product_type = fields.get("product_type")
        if product_type is None and product_id is not None:
            product_type = (
                await session.execute(
                    text(
                        "SELECT product_type::text FROM platform.products "
                        "WHERE id = :id AND tenant_id = :t"
                    ),
                    {"id": product_id, "t": tenant_id},
                )
            ).scalar_one_or_none()
        if str(product_type or "").upper() in {"ISA", "JISA"} and currencies != ["GBP"]:
            raise CatalogueError(f"{product_type} products must allow GBP only.")
        tenant_currencies = set(
            (
                await session.execute(
                    text("SELECT unnest(allowed_currencies) FROM auth.tenants WHERE id = :t"),
                    {"t": tenant_id},
                )
            )
            .scalars()
            .all()
        )
        unsupported = [currency for currency in currencies if currency not in tenant_currencies]
        if unsupported:
            raise CatalogueError(
                f"Product currencies must be allowed by the firm: {', '.join(unsupported)}"
            )
        fields["allowed_currencies"] = currencies
    if "max_parties" in fields:
        try:
            maximum = int(fields["max_parties"])
        except (TypeError, ValueError) as exc:
            raise CatalogueError("max_parties must be a whole number") from exc
        if maximum < 1:
            raise CatalogueError("max_parties must be at least 1")
        fields["max_parties"] = maximum
    for age_key in ("min_age", "max_age"):
        if age_key not in fields:
            continue
        raw_age = fields[age_key]
        if raw_age in {None, ""}:
            fields[age_key] = None
            continue
        try:
            age = int(raw_age)
        except (TypeError, ValueError) as exc:
            raise CatalogueError(f"{age_key} must be a whole number") from exc
        if age < 0:
            raise CatalogueError(f"{age_key} cannot be negative")
        fields[age_key] = age
    min_age = fields.get("min_age")
    max_age = fields.get("max_age")
    if isinstance(min_age, int) and isinstance(max_age, int) and min_age > max_age:
        raise CatalogueError("min_age cannot be greater than max_age")
    if product_id is None:
        if not fields.get("name"):
            raise CatalogueError("name is required")
        if not fields.get("product_type"):
            raise CatalogueError("product_type is required")
        if fields.get("product_type") == "JISA" and "max_age" not in fields:
            fields["max_age"] = 17
        if fields.get("product_type") == "JISA" and "bank_details_allowed" not in fields:
            fields["bank_details_allowed"] = False
        statutory_types = STATUTORY_CLIENT_TYPES.get(str(fields["product_type"]).upper())
        if statutory_types is not None:
            proposed = set(fields.get("allowed_client_types") or statutory_types)
            if not proposed <= statutory_types:
                raise CatalogueError(
                    f"{fields['product_type']} can only be owned by an individual."
                )
            fields["allowed_client_types"] = sorted(proposed)
        columns = ", ".join(["tenant_id", *fields])
        values = ", ".join(
            [
                ":tenant_id",
                *(
                    "CAST(:allowed_custodies AS text[])::platform.custody_type[]"
                    if key == "allowed_custodies"
                    else "CAST(:allowed_client_types AS text[])::crm.contact_type[]"
                    if key == "allowed_client_types"
                    else "CAST(:allowed_currencies AS text[])"
                    if key == "allowed_currencies"
                    else f":{key}"
                    for key in fields
                ),
            ]
        )
        sql = (
            f"INSERT INTO platform.products ({columns}) VALUES ({values}) "  # noqa: S608
            f"RETURNING *"
        )
        rows = await _rows(session, sql, tenant_id=tenant_id, **fields)
        return await _finish_product(session, tenant_id, rows[0])
    if not fields:
        raise CatalogueError("nothing to update")
    if "allowed_client_types" in fields:
        product_type = fields.get("product_type")
        if product_type is None:
            product_type = (
                await session.execute(
                    text(
                        "SELECT product_type::text FROM platform.products "
                        "WHERE id = :id AND tenant_id = :t"
                    ),
                    {"id": product_id, "t": tenant_id},
                )
            ).scalar_one_or_none()
        statutory_types = STATUTORY_CLIENT_TYPES.get(str(product_type or "").upper())
        if (
            statutory_types is not None
            and not set(fields["allowed_client_types"]) <= statutory_types
        ):
            raise CatalogueError(f"{product_type} can only be owned by an individual.")
    assignments = ", ".join(
        ("allowed_custodies = CAST(:allowed_custodies AS text[])::platform.custody_type[]")
        if key == "allowed_custodies"
        else ("allowed_client_types = CAST(:allowed_client_types AS text[])::crm.contact_type[]")
        if key == "allowed_client_types"
        else ("allowed_currencies = CAST(:allowed_currencies AS text[])")
        if key == "allowed_currencies"
        else f"{key} = :{key}"
        for key in fields
    )
    # An UPDATE may not widen a statutory ceiling, or clear it. The create branch above defaults a
    # JISA to 17, which is only half the rule: a later PATCH carrying `max_age: 25` or `null` would
    # put the stored value at odds with what `effective_age_bounds` enforces, and a row that says
    # one thing while the product behaves another way is worse than one that never said anything.
    # `product_type` is read back when the payload does not carry it, because a PATCH normally
    # names only what it changes.
    if "max_age" in fields:
        product_type = fields.get("product_type")
        if product_type is None:
            product_type = (
                await session.execute(
                    text(
                        "SELECT product_type::text FROM platform.products "
                        "WHERE id = :id AND tenant_id = :t"
                    ),
                    {"id": product_id, "t": tenant_id},
                )
            ).scalar_one_or_none()
        ceiling = STATUTORY_MAX_AGE.get(str(product_type or "").upper())
        if ceiling is not None:
            proposed = fields["max_age"]
            if proposed is None or (isinstance(proposed, int) and proposed > ceiling):
                raise CatalogueError(
                    f"A {product_type} owner must be no older than {ceiling}; "
                    f"the limit can be lowered but not raised or removed."
                )

    sql = (
        f"UPDATE platform.products SET {assignments} "  # noqa: S608 - keys are from _PRODUCT_FIELDS
        f"WHERE id = :id AND tenant_id = :tenant_id RETURNING *"
    )
    rows = await _rows(session, sql, id=product_id, tenant_id=tenant_id, **fields)
    if not rows:
        raise CatalogueError("product not found")
    return await _finish_product(session, tenant_id, rows[0])


async def _finish_product(
    session: AsyncSession, tenant_id: int, row: dict[str, Any]
) -> dict[str, Any]:
    """Shape the row, then attach any enabled scheme administrator this pension should name.

    A SIPP created AFTER Morgan Lloyd is enabled would otherwise sit on the shelf with only
    the custodian mapping the operator typed, and `scheme_administrator` would stay null
    until somebody opened the product and added the second platform by hand. The attach is
    a no-op for a GIA/ISA/TPPENSION and for a firm that has not enabled an administrator;
    on a TPPENSION it also drops a leftover Morgan Lloyd mapping the previous `is_pension`
    filter wrote. Enabling SEI does the same for GIA, ISA, JISA and SIPP: a wrapper
    created after the firm is connected still has to name SEI as custodian, or the
    journey opens it with no platform and the action is skipped.
    """
    out = _product_out(row)
    product_id = int(out["id"])
    await providers_mod.attach_scheme_administrators(session, tenant_id, product_id=product_id)
    await providers_mod.attach_custodians(session, tenant_id, product_id=product_id)
    return out


#: Per-platform product settings this table's `data` will accept, and how to read each.
#:
#: AN ALLOWLIST, NOT A FREE JSONB DOOR. `data` is what a provider write path reads to decide what
#: to SEND a custodian — `ml_scheme_type`/`ml_sipp_type` are the option-set pairing
#: `cam_createscheme` takes — so an unchecked blob here is a way to put an arbitrary value into a
#: vendor payload, and a typo in a key is a setting that silently never applies (the shape of
#: every "this product is not configured" bug in this area).
#:
#: `ml_scheme_type` / `ml_sipp_type`: ML's SIPP covers scheme types 16 AND 17 and the SIPP-type
#: values are documented with no labels at all, so the pairing belongs to the PRODUCT rather than
#: to the deployment — a firm offering two SIPP products cannot express it in one pair of
#: environment variables, and one deployment serves many firms.
PRODUCT_PLATFORM_SETTINGS: dict[str, str] = {
    "ml_scheme_type": "int",
    "ml_sipp_type": "int",
}


def _optional_text(payload: dict[str, Any], key: str) -> tuple[str | None, int]:
    """A nullable text column the caller may be editing, or leaving alone.

    Returns `(value, 1)` when `key` is in the payload — empty string becomes NULL, which is
    how "not assigned" is spelled — and `(None, 0)` when it is absent, which is "not editing
    it". The integer is the upsert's present-flag (1 = write this column): a rename that does
    not mention `account_open_reference` must not blank the enrolment template an operator
    attached, and a template save must not blank `external_id` the pipeline stamped.
    """
    if key not in payload:
        return None, 0
    raw = payload[key]
    if raw is None:
        return None, 1
    text = str(raw).strip()
    return (text or None), 1


def _product_platform_data(payload: dict[str, Any]) -> str | None:
    """The validated `data` object, or None to leave the stored one alone.

    Absent `data` means "not editing it" and clears nothing; `{}` clears it. That distinction is
    the same one `Artifact`'s capabilities and `catalogue`'s own partial updates make, and it
    matters because this endpoint is also how a platform's external_id is renamed — a rename must
    not wipe a scheme-type pairing that took a phone call to the vendor to establish.
    """
    raw = payload.get("data")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise CatalogueError("data must be an object")
    unknown = sorted(set(raw) - set(PRODUCT_PLATFORM_SETTINGS))
    if unknown:
        raise CatalogueError(
            f"unknown per-platform setting(s): {', '.join(unknown)}. "
            f"Known: {', '.join(sorted(PRODUCT_PLATFORM_SETTINGS))}"
        )
    cleaned: dict[str, Any] = {}
    for key, value in raw.items():
        if value is None or value == "":
            continue  # omitted rather than stored null: absent is how "not set" is spelled
        if PRODUCT_PLATFORM_SETTINGS[key] == "int":
            try:
                cleaned[key] = int(str(value).strip())
            except ValueError as exc:
                raise CatalogueError(f"{key} must be a whole number, not {value!r}") from exc
        else:  # pragma: no cover - no text settings yet; the branch is the extension point
            cleaned[key] = str(value).strip()
    return _json(cleaned)


async def set_product_platform(
    session: AsyncSession,
    tenant_id: int,
    product_id: int,
    provider: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """The reference one platform knows this product by, its per-platform settings, and the
    opening template a write sends (`account_open_reference`).

    Upsert, because the admin UI edits a row that may or may not exist yet. Addressed by the
    PROVIDER CODE since 0131 — the registry id it used to take was a surrogate for exactly this
    value, and resolving one to the other was a round trip through a table that could be empty.

    Keys absent from the payload are left alone, so renaming `external_id` does not blank the
    enrolment template, attaching a template does not blank the pipeline's reference, and a
    template save does not turn a disabled mapping back on.

    A save that does not name a role still stores the provider's declared one when the
    row has none. The products form posts only the enrolment template, and a null role
    is invisible to the journey (`product_platforms.role = 'custodian'`). An explicit
    role already on the row is left as it is.

    SQL comments are banned in this statement. SQLAlchemy's `text()` scans the whole string for
    colon-names, including comments: a note that said "do not write colon-set here" became a
    missing bind named `set`. CAST(:data AS jsonb) rather than a bind glued to a Postgres cast
    for the same reason — `text()` does not treat a bind followed by `::` as a parameter, so
    the placeholder would be left literal. `app/matching.py` records the cast trap; the
    executed tests in `TestProductPlatformSettings` are the guard.
    """
    if provider not in providers_mod.PROVIDERS:
        raise CatalogueError(f"unknown provider '{provider}'")
    data = _product_platform_data(payload)
    external_id, set_external_id = _optional_text(payload, "external_id")
    external_name, set_external_name = _optional_text(payload, "external_name")
    open_ref, set_open_ref = _optional_text(payload, "account_open_reference")
    set_enabled = 1 if "enabled" in payload else 0
    set_role = 1 if "role" in payload else 0
    declared_role = str(providers_mod.meta(provider).get("role") or "") or None
    role = payload.get("role") if set_role else declared_role
    rows = await _rows(
        session,
        """
        INSERT INTO platform.product_platforms
            (tenant_id, product_id, provider, external_id, external_name, role, enabled, data,
             account_open_reference)
        SELECT :tenant_id, p.id, CAST(:provider AS platform.provider_code),
               :external_id, :external_name, :role, coalesce(:enabled, true),
               CAST(:data AS jsonb), :account_open_reference
        FROM platform.products p WHERE p.id = :product_id AND p.tenant_id = :tenant_id
        ON CONFLICT (product_id, provider) DO UPDATE
           SET external_id = CASE WHEN :set_external_id = 1
                                  THEN excluded.external_id
                                  ELSE platform.product_platforms.external_id END,
               external_name = CASE WHEN :set_external_name = 1
                                    THEN excluded.external_name
                                    ELSE platform.product_platforms.external_name END,
               role = CASE WHEN :set_role = 1 THEN excluded.role
                           ELSE coalesce(platform.product_platforms.role, excluded.role) END,
               enabled = CASE WHEN :set_enabled = 1
                              THEN excluded.enabled
                              ELSE platform.product_platforms.enabled END,
               data = coalesce(excluded.data, platform.product_platforms.data),
               account_open_reference = CASE WHEN :set_account_open_reference = 1
                                             THEN excluded.account_open_reference
                                             ELSE platform.product_platforms.account_open_reference
                                        END,
               updated_at = now()
        RETURNING *
        """,
        tenant_id=tenant_id,
        product_id=product_id,
        provider=provider,
        external_id=external_id,
        set_external_id=set_external_id,
        external_name=external_name,
        set_external_name=set_external_name,
        role=role,
        set_role=set_role,
        enabled=payload.get("enabled"),
        set_enabled=set_enabled,
        data=data,
        account_open_reference=open_ref,
        set_account_open_reference=set_open_ref,
    )
    if not rows:
        raise CatalogueError("product not found")
    return rows[0]


_MODEL_FIELDS = (
    "name",
    "code",
    "model_type",
    "aim",
    "target_return",
    "target_return_basis",
    "target_return_horizon_years",
    "benchmark",
    "risk_rating",
    "risk_profile",
    "investment_decision",
    "model_charge",
    "vatable",
    "suspend_rebalances",
    "cash_allocation",
    "currency",
    "status",
    "enabled",
)


async def upsert_model(
    session: AsyncSession, tenant_id: int, payload: dict[str, Any], model_id: int | None = None
) -> dict[str, Any]:
    fields = {key: payload[key] for key in _MODEL_FIELDS if key in payload}
    if model_id is None:
        if not fields.get("name"):
            raise CatalogueError("name is required")
        columns = ", ".join(["tenant_id", *fields])
        values = ", ".join([":tenant_id", *(f":{key}" for key in fields)])
        sql = f"INSERT INTO platform.models ({columns}) VALUES ({values}) RETURNING *"  # noqa: S608
        return (await _rows(session, sql, tenant_id=tenant_id, **fields))[0]
    if not fields:
        raise CatalogueError("nothing to update")
    assignments = ", ".join(f"{key} = :{key}" for key in fields)
    sql = (
        f"UPDATE platform.models SET {assignments} "  # noqa: S608 - keys are from _MODEL_FIELDS
        f"WHERE id = :id AND tenant_id = :tenant_id RETURNING *"
    )
    rows = await _rows(session, sql, id=model_id, tenant_id=tenant_id, **fields)
    if not rows:
        raise CatalogueError("model not found")
    return rows[0]


async def create_model_version(
    session: AsyncSession, tenant_id: int, model_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    """A new allocation snapshot, as a draft.

    The version number is allocated here rather than by the caller: two people editing the
    same model would otherwise both claim the next number, and the unique constraint would
    turn the second save into an error instead of the next version.
    """
    lines, cash = validate_allocations(
        payload.get("allocations") or [], payload.get("cash_allocation")
    )
    version_rows = await _rows(
        session,
        """
        INSERT INTO platform.model_versions
            (tenant_id, model_id, version, effective_from, cash_allocation, asset_mix, note)
        SELECT :tenant_id, m.id,
               coalesce((SELECT max(v.version) FROM platform.model_versions v
                         WHERE v.model_id = m.id), 0) + 1,
               :effective_from, :cash_allocation, cast(:asset_mix AS jsonb), :note
        FROM platform.models m WHERE m.id = :model_id AND m.tenant_id = :tenant_id
        RETURNING *
        """,
        tenant_id=tenant_id,
        model_id=model_id,
        effective_from=payload.get("effective_from"),
        cash_allocation=float(cash),
        asset_mix=payload.get("asset_mix"),
        note=payload.get("note"),
    )
    if not version_rows:
        raise CatalogueError("model not found")
    version = version_rows[0]
    for line in lines:
        await session.execute(
            text(
                """
                INSERT INTO platform.model_allocations
                    (tenant_id, model_version_id, instrument_id, external_asset_id, isin,
                     name, percentage, sort_order)
                VALUES (:tenant_id, :version_id, :instrument_id, :external_asset_id, :isin,
                        :name, :percentage, :sort_order)
                """
            ),
            {"tenant_id": tenant_id, "version_id": version["id"], **line},
        )
    version["allocations"] = lines
    return version


async def publish_model_version(
    session: AsyncSession, tenant_id: int, model_id: int, version_id: int, user_id: str | None
) -> dict[str, Any]:
    """Publishing is what makes a draft the live allocation. Idempotent: re-publishing keeps
    the original timestamp rather than rewriting when it went live."""
    rows = await _rows(
        session,
        """
        UPDATE platform.model_versions
           SET published_at = coalesce(published_at, now()),
               published_by = coalesce(published_by, cast(:user_id AS uuid)),
               updated_at = now()
         WHERE id = :version_id AND model_id = :model_id AND tenant_id = :tenant_id
        RETURNING *
        """,
        version_id=version_id,
        model_id=model_id,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    if not rows:
        raise CatalogueError("version not found")
    return rows[0]


async def upsert_commentary(
    session: AsyncSession,
    tenant_id: int,
    model_id: int,
    payload: dict[str, Any],
    commentary_id: int | None = None,
    author_id: str | None = None,
) -> dict[str, Any]:
    body = str(payload.get("body") or "").strip()
    if not body:
        raise CatalogueError("commentary body is required")
    if commentary_id is not None:
        rows = await _rows(
            session,
            """
            UPDATE platform.model_commentary
               SET title = :title, body = :body, period_start = :period_start,
                   period_end = :period_end, model_version_id = :model_version_id,
                   published_at = CASE WHEN :publish THEN coalesce(published_at, now())
                                       ELSE published_at END,
                   updated_at = now()
             WHERE id = :id AND model_id = :model_id AND tenant_id = :tenant_id
            RETURNING *
            """,
            id=commentary_id,
            model_id=model_id,
            tenant_id=tenant_id,
            title=payload.get("title"),
            body=body,
            period_start=payload.get("period_start"),
            period_end=payload.get("period_end"),
            model_version_id=payload.get("model_version_id"),
            publish=bool(payload.get("publish")),
        )
        if not rows:
            raise CatalogueError("commentary not found")
        return rows[0]
    rows = await _rows(
        session,
        """
        INSERT INTO platform.model_commentary
            (tenant_id, model_id, model_version_id, title, body, period_start, period_end,
             author_id, published_at)
        SELECT :tenant_id, m.id, :model_version_id, :title, :body, :period_start, :period_end,
               cast(:author_id AS uuid), CASE WHEN :publish THEN now() END
        FROM platform.models m WHERE m.id = :model_id AND m.tenant_id = :tenant_id
        RETURNING *
        """,
        tenant_id=tenant_id,
        model_id=model_id,
        model_version_id=payload.get("model_version_id"),
        title=payload.get("title"),
        body=body,
        period_start=payload.get("period_start"),
        period_end=payload.get("period_end"),
        author_id=author_id,
        publish=bool(payload.get("publish")),
    )
    if not rows:
        raise CatalogueError("model not found")
    return rows[0]


_FEE_FIELDS = (
    "name",
    "category",
    "description",
    # The provider's own code for a hand-authored package: for SEI, the one-time `FEE_TYPE` code an
    # ad-hoc charge sends. Only written when the payload carries it, so editing a sync-stamped
    # package without resending it leaves the synced external_id alone.
    "external_id",
    "frequency",
    "fee_type",
    "access",
    "charge_basis",
    "payer",
    "vatable",
    "minimum_amount",
    "maximum_amount",
    "applies_to",
    "effective_from",
    "effective_to",
    "currency",
    "enabled",
    "is_default",
    "optional",
    "sort_order",
    "product_id",
    "model_id",
)


async def upsert_fee_package(
    session: AsyncSession, tenant_id: int, payload: dict[str, Any], fee_id: int | None = None
) -> dict[str, Any]:
    fields = {key: payload[key] for key in _FEE_FIELDS if key in payload}
    if fields.get("charge_basis") and fields["charge_basis"] not in CHARGE_BASES:
        raise CatalogueError(f"charge_basis must be one of {', '.join(CHARGE_BASES)}")
    if fields.get("payer") and fields["payer"] not in PAYERS:
        raise CatalogueError(f"payer must be one of {', '.join(PAYERS)}")

    rule = payload.get("rule")
    if fee_id is None:
        if not fields.get("name"):
            raise CatalogueError("name is required")
        if not fields.get("category"):
            raise CatalogueError("category is required")
        fee_type = str(fields.get("fee_type") or "")
        fields["rule"] = _json(validate_fee_rule(fee_type, rule))
        columns = ", ".join(["tenant_id", *fields])
        values = ", ".join(
            [":tenant_id", *(f"cast(:{k} AS jsonb)" if k == "rule" else f":{k}" for k in fields)]
        )
        sql = f"INSERT INTO platform.fee_packages ({columns}) VALUES ({values}) RETURNING *"  # noqa: S608
        return (await _rows(session, sql, tenant_id=tenant_id, **fields))[0]

    if rule is not None:
        # Changing the rule without its type, or the reverse, is how a mismatch happens; the
        # type in play is whichever the payload settles on.
        existing = await _rows(
            session,
            "SELECT fee_type FROM platform.fee_packages WHERE id = :id AND tenant_id = :tenant_id",
            id=fee_id,
            tenant_id=tenant_id,
        )
        if not existing:
            raise CatalogueError("fee package not found")
        fee_type = str(fields.get("fee_type") or existing[0]["fee_type"])
        fields["rule"] = _json(validate_fee_rule(fee_type, rule))
    if not fields:
        raise CatalogueError("nothing to update")
    assignments = ", ".join(
        f"{key} = cast(:{key} AS jsonb)" if key == "rule" else f"{key} = :{key}" for key in fields
    )
    sql = (
        f"UPDATE platform.fee_packages SET {assignments} "  # noqa: S608 - keys from _FEE_FIELDS
        f"WHERE id = :id AND tenant_id = :tenant_id RETURNING *"
    )
    rows = await _rows(session, sql, id=fee_id, tenant_id=tenant_id, **fields)
    if not rows:
        raise CatalogueError("fee package not found")
    return rows[0]


async def upsert_provider(
    session: AsyncSession,
    tenant_id: int,
    kind: str,
    payload: dict[str, Any],
    provider_id: int | None = None,
) -> dict[str, Any]:
    table = _provider_table(kind)
    allowed = ["name", "description", "enabled"]
    if kind == "transfer":
        allowed += ["masterparty_id", "origo_id", "atg_reference"]
    else:
        # The wrapper this provider provides (0130). Editable because the register's own
        # `wrapper_type` is Seccl's word for it and a firm may hold a provider Seccl does not
        # publish — the pipeline refreshes it for the ones it does.
        allowed += ["product"]
    fields = {key: payload[key] for key in allowed if key in payload}

    # A SUSPENDED provider cannot be switched on. Seccl marks 13 of the 661 suspended (Ascentric
    # among them), and a transfer addressed to one fails at the custodian -- after a client has
    # been told their transfer is under way. The guard lives HERE and not only on the switch,
    # because a disabled control is a hint and this is a rule: the form posts an id and a value,
    # and nothing else stops a stale page or a direct call from setting it.
    if provider_id is not None and kind == "transfer" and fields.get("enabled"):
        status = (
            await session.execute(
                text(
                    "SELECT status FROM platform.transfer_providers "
                    "WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {"id": provider_id, "tenant_id": tenant_id},
            )
        ).scalar_one_or_none()
        # Only a status the platform actively contradicts blocks it: an unknown or absent status
        # is a hand-added provider, which nobody has told us is unavailable.
        if status is not None and status.lower() != "active":
            raise CatalogueError(
                f"{status} at the platform — a suspended provider cannot be enabled"
            )

    if provider_id is None:
        if not fields.get("name"):
            raise CatalogueError("name is required")
        columns = ", ".join(["tenant_id", *fields])
        values = ", ".join([":tenant_id", *(f":{key}" for key in fields)])
        sql = f"INSERT INTO platform.{table} ({columns}) VALUES ({values}) RETURNING *"  # noqa: S608
        return (await _rows(session, sql, tenant_id=tenant_id, **fields))[0]
    if not fields:
        raise CatalogueError("nothing to update")
    assignments = ", ".join(f"{key} = :{key}" for key in fields)
    sql = (
        f"UPDATE platform.{table} SET {assignments} "  # noqa: S608 - table from _provider_table
        f"WHERE id = :id AND tenant_id = :tenant_id RETURNING *"
    )
    rows = await _rows(session, sql, id=provider_id, tenant_id=tenant_id, **fields)
    if not rows:
        raise CatalogueError("provider not found")
    return rows[0]


async def set_term(
    session: AsyncSession,
    tenant_id: int,
    key: str,
    label: str | None,
    plural: str | None,
    user_id: str | None,
) -> dict[str, Any] | None:
    """Set or clear one term. A blank label clears the override rather than storing an empty
    string, so "reset to default" and "call it nothing" cannot be confused."""
    cleaned = (label or "").strip()
    if not cleaned:
        await session.execute(
            text("DELETE FROM l10n.overrides WHERE tenant_id = :tenant_id AND term_key = :key"),
            {"tenant_id": tenant_id, "key": key},
        )
        return None
    rows = await _rows(
        session,
        """
        INSERT INTO l10n.overrides (tenant_id, term_key, label, plural, updated_by)
        SELECT :tenant_id, t.key, :label, :plural, cast(:user_id AS uuid)
        FROM l10n.terms t WHERE t.key = :key
        ON CONFLICT (tenant_id, term_key) DO UPDATE
           SET label = excluded.label, plural = excluded.plural,
               updated_by = excluded.updated_by, updated_at = now()
        RETURNING *
        """,
        tenant_id=tenant_id,
        key=key,
        label=cleaned,
        plural=(plural or "").strip() or None,
        user_id=user_id,
    )
    if not rows:
        raise CatalogueError(f"unknown term '{key}'")
    return rows[0]


def _json(value: Any) -> str:
    import json

    return json.dumps(value)
