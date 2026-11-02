"""CRM read surface -- contacts (the client) and their accounts.

These are the client-scoped read hot path: they run on ``tenant_session`` (the
``authenticated`` RLS role), so **row visibility is enforced by the database**, not here.
A ``contacts:read_all`` staff member sees the whole firm; a scope-limited adviser sees
only their assigned book; a client end-user sees only their own party -- all from the same
query, because the two-layer RLS on ``crm.contacts`` / ``platform.accounts`` resolves the
caller's ``contact_ids`` / ``account_ids`` claims (minted by ``auth.resolve_claims``).
So there is deliberately no ``require_permission`` gate: RLS is the gate.

**Money provenance.** Every value figure here is ``platform.accounts.value`` -- a *batch
snapshot* copied from the provider extract by ``scripts/ingest_seccl.py``; there is no live
valuation feed yet (see "Next steps"). So each surface returns its own ``*_as_of`` stamp
(``data.valued_at`` if the ingest stamped one, else the row's ``updated_at``) and its
``value_source`` (the linked provider, from ``platform.links``), and the portals
must label both wherever a figure is shown.
Figures are NEVER rescaled or anchored to each other: a number is either the source's own
or it is absent.

**Two writes live here** (marked below), on ``firm_session``/``service_role`` rather than
``tenant_session``: a client's bank details and an account's mandate. Both are properties of a row
this module already reads and renders, and neither has a home on ``/firm`` (which is the *people and
firm-settings* surface). They carry an explicit ``contacts:write`` gate, because ``service_role``
bypasses RLS — the gate is the authorisation, not the database.
"""

import asyncio
import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, TypedDict

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import JSONResponse
from intelliflo import client as intelliflo_client
from intelliflo.client import IntellifloApiError, IntellifloAuthError
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import bank_accounts as bank_accounts_mod
from app import beneficiaries as beneficiaries_mod
from app import contacts as contacts_mod
from app import fatca, listing, providers, statement
from app import instructions as instructions_mod
from app import regular_movements as regular_movements_mod
from app import terms as terms_svc
from app.actions import runs as action_runs
from app.actions import service as action_service
from app.addressing import (
    book_account_id,
    book_contact_id,
    book_write_account_id,
    book_write_contact_id,
    tenant_account_id,
    tenant_contact_id,
)
from app.auth import Principal, get_current_principal, require_any_permission, require_permission
from app.complycube.outcomes import monitoring_covers
from app.config import settings
from app.contacts import phone_expr
from app.contributions import PENSION_PRODUCTS
from app.countries import catalogue as country_catalogue
from app.db import (
    FirmContext,
    as_service_role,
    book_session,
    firm_book_write_session,
    system_transaction,
)
from app.db import (
    target_tenant_session as tenant_session,
)
from app.flows import client_journeys
from app.integrations import morgan_lloyd
from app.integrations.morgan_lloyd import valuations as ml_valuations
from app.lake import demo as lake_demo
from app.lake import fixture as lake_fixture
from app.lake import holdings as lake_holdings
from app.permissions import (
    CASES_READ,
    CONTACTS_SYNC_MORGAN_LLOYD,
    CONTACTS_WRITE,
    INSTRUCTIONS_WRITE,
    SCREENING_READ,
    TENANT_PORTAL_READ,
)
from app.reviews.hold import idempotency_key, maybe_hold, pending_response, raise_review
from app.reviews.service import ReviewError
from app.routers import documents as documents_router
from app.transaction_fields import present_transaction

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/crm", tags=["crm"])

# Only accounts that can actually hold value count towards AUM: a 'draft' account isn't open
# yet and a 'closed' one has been paid away (platform.account_status). Both used to be summed
# into every AUM figure on the platform, so a firm's headline included money it doesn't hold.
_VALUED = "a.status NOT IN ('draft','closed')"

# When an account's value was actually sourced. The legacy ingest stamps `data.valued_at`; the
# provider pipeline stamps nothing on the account row but writes `platform.valuations` (one row per
# account per day, `as_of_date`), and `platform.accounts.value` IS the latest of those — so the
# latest valuation's date is the value's provenance. `updated_at` is the last resort, for rows
# written by neither path. Deliberately not updated_at alone, and not before the valuations: any
# unrelated write to the row bumps it, which made a months-old snapshot read as today's valuation
# ("as at 28 Sept 2026" over a book whose holdings were dated a year earlier).
_VALUED_AT = (
    "COALESCE((a.data->>'valued_at')::timestamptz, "
    "         (SELECT max(pv.as_of_date)::timestamptz FROM platform.valuations pv "
    "           WHERE pv.account_id = a.id), "
    "         a.updated_at)"
)


# ── The paged listings' toolbar, in SQL ──────────────────────────────────────────────────────
# The three book listings below filter, search, sort and page entirely in SQL, so they build
# `{items, total, facets, facet_counts, bounds}` by hand rather than through `listing.page`.
# These are the parts all three share; the rules they implement are `app/listing.py`'s.


def _facet_sql(value: str, where: str, *, source: str = "searched", rows: str = "*") -> str:
    """One facet's ``{value: count}``, over the searched set with this facet's own filter lifted.

    The options and the tally come out of the ONE aggregate, so a count can never answer a
    different question from the option it sits beside. ``rows='DISTINCT id'`` where a single row
    carries several values (an unnested array, or the two columns a client's type spans) — with
    ``*`` a row holding the same word twice would be counted twice.
    """
    return (
        "(SELECT coalesce(jsonb_object_agg(v, n), '{}'::jsonb) FROM ("  # noqa: S608
        f" SELECT {value} AS v, count({rows}) AS n FROM {source}"
        f" WHERE {where} AND ({value}) IS NOT NULL GROUP BY 1) f)"
    )


def _listing_page_sql(cte: str, order: str, rest: str) -> str:
    """Page, total and facets as ONE statement, rows in the driver's own types.

    ``searched`` inside ``cte`` has to be ``MATERIALIZED``. Left to inline, a paged request
    recomputes the whole join once per reference — the page, the count and the facet row, as
    three round trips. On the ledger that was ~800ms–2.5s each. ``rest`` is the facet
    select-list and every alias is ``facet_<name>``, so it cannot collide with a page column.
    The aggregate is one row joined onto the page, which keeps the total when the page is empty.
    """
    return (
        cte  # noqa: S608 - order is our own SQL, never request text
        + " SELECT meta.*, page.* FROM ("
        + "SELECT (SELECT count(*) FROM filtered) AS total, "
        + rest
        + ") meta LEFT JOIN LATERAL ("
        + "SELECT * FROM filtered ORDER BY "
        + order
        + " LIMIT :lim OFFSET :off"
        + ") page ON true ORDER BY "
        + order
    )


def _read_listing_page(
    rows: Sequence[Mapping[Any, Any]],
    *,
    facets: Sequence[str],
    ranges: Sequence[str] = (),
    drop: Sequence[str] = (),
) -> tuple[list[dict[str, object]], int, dict[str, object]]:
    """Split the combined row into items, a total, and the toolbar's facet row."""
    meta_keys = {
        "total",
        *(f"facet_{key}" for key in facets),
        *(f"facet_{key}_bounds" for key in ranges),
    }
    hidden = meta_keys | set(drop)
    source: Mapping[Any, Any] = rows[0] if rows else {}
    if not rows or source.get("id") is None:
        items: list[dict[str, object]] = []
    else:
        items = [{key: value for key, value in row.items() if key not in hidden} for row in rows]
    toolbar_row: dict[str, object] = {key: source.get(f"facet_{key}") for key in facets}
    for key in ranges:
        toolbar_row[f"{key}_bounds"] = source.get(f"facet_{key}_bounds")
    total = source.get("total")
    return items, int(total) if total is not None else 0, toolbar_row


def _bounds_sql(expr: str, where: str, *, source: str = "searched") -> str:
    """A range column's real ends as ``[min, max]`` — what the portal draws a slider's track from.

    Over the searched set with that range's OWN filter lifted, exactly as a facet is: dragging a
    handle must not shrink the track it is being dragged along, and the other filters still apply
    so the track spans figures the reader could actually land on.
    """
    # NULL, not `[null, null]`, when the column has no numbers: an empty span is not a track,
    # and a caller can COALESCE a wider one in its place.
    return (
        "(SELECT CASE WHEN min(e) IS NULL OR max(e) IS NULL THEN NULL "  # noqa: S608
        "ELSE jsonb_build_array(min(e), max(e)) END FROM ("
        f" SELECT ({expr})::float8 AS e FROM {source} WHERE {where}) b)"
    )


def _toolbar(
    row: Mapping[Any, Any], facets: Sequence[str], ranges: Sequence[str] = ()
) -> dict[str, object]:
    """The three toolbar keys of the envelope, read off the one facet row."""
    counts: dict[str, dict[str, int]] = {key: dict(row[key] or {}) for key in facets}
    bounds: dict[str, list[float]] = {}
    for key in ranges:
        pair = list(row[f"{key}_bounds"] or [])
        # A column with nothing in it has no ends, and is absent rather than [0, 0] — a slider
        # from zero to zero is a control that cannot be moved.
        if len(pair) == 2 and None not in pair:
            bounds[key] = [float(pair[0]), float(pair[1])]
    return {
        "facets": {key: sorted(tally, key=str.casefold) for key, tally in counts.items()},
        "facet_counts": counts,
        "bounds": bounds,
    }


async def adviser_directory(session: AsyncSession) -> list[str]:
    """Every adviser in this firm's lineage, by the name the listings filter on.

    The facet only names advisers who already have a row in the match set. The filter is a
    directory, so the rest are added beside them and the browser narrows the list.

    The membership predicate is the same one ``auth_users_scoped_read`` uses. Contributions
    and transfers also call this on ``target_firm_session``, which is ``service_role`` and
    bypasses that policy, so the predicate has to be in the query.
    """
    rows = (
        await session.execute(
            text(
                "SELECT u.name FROM auth.users u "
                "WHERE u.is_adviser AND u.deleted_at IS NULL "
                "AND u.name IS NOT NULL AND btrim(u.name) <> '' "
                "AND EXISTS ("
                "  SELECT 1 FROM auth.organisation_memberships m "
                "  WHERE m.user_id = u.id "
                "    AND m.tenant_id = ANY (auth.tenant_lineage())"
                ")"
            )
        )
    ).scalars()
    return [str(name) for name in rows]


def _with_advisers(toolbar: dict[str, object], names: list[str]) -> dict[str, object]:
    facets = toolbar.get("facets")
    if isinstance(facets, dict):
        listing.union_facet(facets, "adviser", names)
    return toolbar


def _iso_date(ts: object) -> str | None:
    """A ``_VALUED_AT`` timestamp as the date-only ISO stamp the client portal renders
    (``as at {date}``). ``None`` passes through: an unvalued book has no date to claim."""
    return ts.date().isoformat() if isinstance(ts, datetime) else None


async def _value_source(session: AsyncSession) -> str | None:
    """Which provider(s) the caller's visible account values came from (RLS-scoped
    ``platform.links``). ``None`` when no visible account is linked -- the values are
    then whatever the firm keyed in by hand, and the portal says so."""
    return (
        await session.execute(
            text(
                # `::text`: `provider` is `platform.provider_code` since 0131, and string_agg has no
                # overload for an enum — a cast here rather than a text column, because the
                # enum is what stops a misspelt provider being stored at all.
                "SELECT string_agg(DISTINCT pl.provider::text, ', ') FROM platform.links pl "
                "WHERE pl.entity_type = 'account'"
            )
        )
    ).scalar_one_or_none()


# Pension wrappers: drawdown, contribution categorisation and illustrations only apply here.
# Mirrors PENSION_PRODUCTS in the platform portal (lib/transactions/types.ts).
_PENSION_PRODUCTS = frozenset({"SIPP", "PENSION", "SSAS"})


#: Provider words that mean the custodian has suspended the person. Seccl publishes
#: ``suspended``; Morgan Lloyd maps Dataverse inactive onto ``inactive``.
_SUSPENDED = frozenset({"suspended", "inactive"})


def _contact_status(row: dict[str, object]) -> str:
    """The status the clients listing SHOWS — the exact rule in
    ``pkgs/ui/.../book/clients/status.ts::clientStatus``:

        deleted_at set                         -> closed
        provider_status in {suspended, inactive} -> suspended
        0 owned or represented accounts        -> lead
        else                                   -> active

    Duplicated deliberately, and pinned by ``test_crm_listing_filters.py`` against the same
    cases the TS side tests. Deriving it differently here would make the filter disagree
    with the badge beside it: a lead filed under "Active" and absent from "Lead" — with no
    way to tell from the screen which of the two was wrong.

    ``enabled`` is deliberately not consulted: Deactivate is gone, and a disabled flag that
    still painted Inactive would resurrect the status the product no longer offers.
    """
    if row.get("deleted_at"):
        return "closed"
    raw = row.get("provider_status")
    if isinstance(raw, str) and raw.strip().lower() in _SUSPENDED:
        return "suspended"
    return (
        "lead"
        if not row.get("account_count") and not row.get("represented_account_count")
        else "active"
    )


#: The clients listing's filters. Advisers are multi-valued (a client can have several), so that
#: one matches on any.
def _contact_type_values(row: dict[str, object]) -> list[str]:
    """BOTH levels of "what kind of client is this", broadest first.

    The filter and the facet are built off this, so ``organisation`` still selects a trust while
    ``trust`` narrows to it — the two are a hierarchy, not alternatives, and offering only the
    specific value would take away the broad question a firm actually asks ("show me the
    non-individuals"). Deduplicated, because an organisation with no sub-type answers both with
    the same word.
    """
    values = [row.get("type"), row.get("client_type")]
    return list(dict.fromkeys(str(value) for value in values if value))


def _contact_value(row: dict[str, object]) -> float | None:
    """The client's portfolio value, or None when there is nothing to value.

    `portfolio_value` is ``COALESCE(..., 0)`` in the query above, because a NULL total would break
    every caller that sums or sorts it. For a FILTER that zero is a lie: a client with no valued
    account is UNKNOWN, not worth nothing, and reading the coalesced zero would sweep the whole
    unpriced tail into "under £10,000" — telling a firm it holds clients whose value it cannot
    actually see. `valued_account_count` is the column that knows the difference.

    The portal's twin makes the same call (`dashboard/book.ts::filterBook`), so a value question
    asked on the dashboard and on /clients returns the same people.
    """
    if not (row.get("valued_account_count") or 0):
        return None
    value = row.get("portfolio_value")
    return float(value) if isinstance(value, int | float) else None


_CONTACT_FILTERS: listing.Filters = {
    "status": lambda row, value: listing.matches(value, _contact_status(row)),
    "adviser": lambda row, value: listing.matches_any(value, row.get("advisers") or []),
    "type": lambda row, value: listing.matches_any(value, _contact_type_values(row)),
    # The WRAPPERS the client holds. `matches_any` like `adviser`, because this is a set: a client
    # with a SIPP and an ISA is a match for either, and a client with none matches no value at all
    # rather than matching everything.
    "account_type": lambda row, value: listing.matches_any(value, row.get("account_types") or []),
    # Any of the client's accounts. The value is the provider code, the same one accounts and
    # transactions filter on, so "Seccl" means one thing on all three listings.
    "custodian": lambda row, value: listing.matches_any(value, row.get("custodians") or []),
    "value": lambda row, value: listing.within_range(value, _contact_value(row)),
}
_CONTACT_BOUNDS: dict[str, Callable[[dict[str, object]], float | None]] = {
    "value": _contact_value,
}


def _contact_facets(
    rows: list[dict[str, object]],
    active: dict[str, str | Sequence[str] | None],
) -> tuple[dict[str, list[str]], dict[str, dict[str, int]]]:
    """The facet values AND their counts, in one pass over the same rows."""
    return listing.facets_and_counts(rows, _CONTACT_FACETS, active, _CONTACT_FILTERS)


_CONTACT_FACETS: listing.Facets = [
    ("status", _contact_status),
    ("adviser", lambda row: row.get("advisers") or []),
    ("type", _contact_type_values),
    ("account_type", lambda row: row.get("account_types") or []),
    ("custodian", lambda row: row.get("custodians") or []),
]
# The columns the listing may be ORDERED by, keyed as the table's own column keys. A CLOSED SET:
# `sort` arrives from a query string, and anything not named here orders by the endpoint's default
# instead of reaching the ordering at all.
#
# Sorting is SERVER-SIDE for the same reason the filters are. This listing pages, so ordering the
# 25 rows in hand reorders the page and calls it "by value" — while the largest client in the book
# may be three pages away and never moves. `name` reads the composed display name rather than the
# `name` column, so the order matches what the cell shows.
_CONTACT_SORTS: listing.Sorts = {
    "name": lambda row: (
        f"{row.get('first_name') or ''} {row.get('surname') or ''}".strip() or row.get("name")
    ),
    "value": lambda row: row.get("portfolio_value"),
    # Still the COUNT, though the column now shows the wrappers as tags: how many accounts a client
    # holds is the only total order a set of tags has, and it is the question the ordering answered
    # before. Sorting alphabetically by whichever wrapper happened to sort first would order the
    # book by an accident of the alphabet.
    "accounts": lambda row: row.get("account_count"),
    "type": lambda row: row.get("type"),
    "client_type": lambda row: row.get("client_type"),
    "status": _contact_status,
    "email": lambda row: row.get("email"),
    "currency": lambda row: row.get("currency"),
    # BOTH SPELLINGS. The clients table keys this column `adviser` (singular), so every click on
    # that header sent a key this map did not hold and the header did nothing; the plural is what
    # the filter key and the facet are called, and a link already in a history must keep working.
    "advisers": lambda row: (row.get("advisers") or [None])[0],
    "adviser": lambda row: (row.get("advisers") or [None])[0],
}

# SQL twins of the Python sorts above. Closed set — `sort` is a query string. Expressions
# refer to the shaped CTE's output names (see list_contacts), never request text.
_CONTACT_SQL_SORTS = {
    "name": "COALESCE(NULLIF(TRIM(CONCAT_WS(' ', first_name, surname)), ''), name)",
    "value": "portfolio_value",
    "accounts": "account_count",
    "type": "type::text",
    "client_type": "client_type",
    "status": "derived_status",
    "email": "email",
    "advisers": "(advisers)[1]",
    "currency": "currency",
    "cash": "cash",
    "growth": "growth",
}
_CONTACT_SQL_DEFAULT_ORDER = "surname NULLS LAST, name NULLS LAST, id"


# The SQL twin of `_contact_value`: the UNCOALESCED figure, NULL where the book cannot see one.
# `portfolio_value` is COALESCE(..., 0) for the callers that sum it, and filtering on that zero
# would file every unvalued client under "worth less than a million".
_CONTACT_VALUE_SQL = "CASE WHEN valued_account_count > 0 THEN portfolio_value END"

# Cash and growth for every account, computed ONCE per request.
#
# Cash SUMS across source systems. The custodian's uninvested balance and the scheme
# administrator's bank balance are different pots (0117): take the latest row of each and add
# them. Growth does not sum. A custodian's stated unrealised gain wins; an administrator's
# figure is used only when the custodian stated none (a seeded book tagged as the
# administrator, which never had a custodian row). Adding the two would double-count. A NULL
# is nobody having stated one. Zero would claim the position was flat.
#
# A per-account LATERAL re-entered `platform.valuations` (and its RLS policy) once per account,
# and the listing then ran that whole query again for the count and again for the facets. This
# is one scan, joined below. `:custodians` is the same bind the custodian filter already sends.
_ACCOUNT_VALUATIONS_CTE = (
    "account_valuations AS MATERIALIZED ("
    "  SELECT latest.account_id, sum(latest.cash_value) AS cash_value, "
    "         gain.unrealised_gain "
    "  FROM ("
    "    SELECT DISTINCT ON (account_id, source_system) account_id, cash_value "
    "    FROM platform.valuations "
    "    ORDER BY account_id, source_system, as_of_date DESC"
    "  ) latest "
    "  LEFT JOIN ("
    "    SELECT DISTINCT ON (account_id) account_id, unrealised_gain "
    "    FROM platform.valuations "
    "    WHERE unrealised_gain IS NOT NULL "
    "    ORDER BY account_id, (source_system = ANY(:custodians)) DESC, as_of_date DESC"
    "  ) gain ON gain.account_id = latest.account_id "
    "  GROUP BY latest.account_id, gain.unrealised_gain"
    ") "
)
_ACCOUNT_VALUATION_JOIN = "LEFT JOIN account_valuations valuation ON valuation.account_id = a.id "

# The primary custodian link, once per account. The same `ORDER BY provider LIMIT 1` as
# `_RESOLVED_CUSTODIAN_SQL`, but not as a correlated subquery: that form re-ran `links`' policy
# once per outer row (measured on the ledger, ~1.1s to answer a few dozen accounts).
_ACCOUNT_CUSTODIANS_CTE = (
    "account_custodians AS MATERIALIZED ("
    "  SELECT DISTINCT ON (l.tenant_id, l.entity_id) "
    "         l.tenant_id, l.entity_id AS account_id, l.provider "
    "  FROM platform.links l "
    "  WHERE l.entity_type = 'account' AND l.link_type = 'primary' "
    "    AND l.provider = ANY(:custodians) "
    "  ORDER BY l.tenant_id, l.entity_id, l.provider"
    ") "
)
_LISTING_BOOK_CTES = _ACCOUNT_VALUATIONS_CTE + ", " + _ACCOUNT_CUSTODIANS_CTE
_LISTED_CUSTODIAN_SQL = "COALESCE(a.custodian, cust.provider)"


# Prefer the account's stated custodian. Where an older provider-fed row has none, the same
# deterministic primary-link fallback `get_account` uses, so the listing filter and the drawer
# name one provider. `a` is the accounts alias in every query this is interpolated into.
_RESOLVED_CUSTODIAN_SQL = (
    "COALESCE(a.custodian, ("
    " SELECT l.provider FROM platform.links l "
    "  WHERE l.tenant_id = a.tenant_id "
    "    AND l.entity_type = 'account' AND l.entity_id = a.id "
    "    AND l.link_type = 'primary' AND l.provider = ANY(:custodians) "
    "  ORDER BY l.provider LIMIT 1"
    "))"
)


def _contacts_filter_sql(*, skip: str | None = None) -> str:
    """AND-ed SQL for every contacts listing filter except ``skip`` — the facet's own door."""
    parts = {
        "status": "(CAST(:status AS text[]) IS NULL OR derived_status = ANY(:status))",
        "adviser": "(CAST(:adviser AS text[]) IS NULL OR advisers && CAST(:adviser AS text[]))",
        "type": (
            "(CAST(:type AS text[]) IS NULL OR type::text = ANY(:type) OR client_type = ANY(:type))"
        ),
        "account_type": (
            "(CAST(:account_type AS text[]) IS NULL "
            "OR account_types && CAST(:account_type AS text[]))"
        ),
        "custodian": (
            "(CAST(:custodian AS text[]) IS NULL OR custodians && CAST(:custodian AS text[]))"
        ),
        "value": listing.range_sql("value", _CONTACT_VALUE_SQL),
    }
    return " AND ".join(sql for key, sql in parts.items() if key != skip)


@router.get("/contacts", name="list-contacts")
async def list_contacts(
    archived: bool = False,
    q: str | None = None,
    # MULTI-VALUED: `?status=active&status=lead`. A list because the filter bar offers
    # several, and "Active or Lead" is how a book is actually read. One value still works —
    # FastAPI parses a single occurrence into a one-element list — so an older caller's URL is
    # unchanged in meaning.
    status: Annotated[list[str] | None, Query()] = None,
    adviser: Annotated[list[str] | None, Query()] = None,
    type: Annotated[list[str] | None, Query()] = None,  # noqa: A002 - the query name the portal filters on
    account_type: Annotated[list[str] | None, Query()] = None,
    # Provider codes (`seccl`, `sei`). A client matches when any of their accounts is held there.
    custodian: Annotated[list[str] | None, Query()] = None,
    # A PAIR, positionally: `?value=&value=250000` is "up to 250,000". Not `clean_many`, which
    # would drop the blank and turn that into "from 250,000" — see `listing.clean_range`.
    value: Annotated[list[str] | None, Query()] = None,
    sort: str | None = None,
    dir: str | None = None,  # noqa: A002 - the wire name the portal sends
    limit: int | None = None,
    offset: int = 0,
    session: AsyncSession = Depends(book_session),
) -> list[dict[str, object]] | dict[str, object]:
    """The clients the caller may see, each with their portfolio value + assigned advisers.
    RLS scopes the rows; the accounts/advisers behind the aggregates are scoped too, so they
    reflect only what the caller is entitled to. ``portfolio_value`` counts only valued
    accounts and carries its own ``value_as_of`` (see the module note on provenance).

    ``archived=true`` returns the closed rows INSTEAD of the live ones (never both): a client
    can be closed (``DELETE /firm/contacts/{id}``), and a close with no way to see or
    reopen what is in it is a one-way door. The record itself stays loadable — Closed is a
    status, not a 404.

    The account aggregates come from a LATERAL, not a second LEFT JOIN: joining accounts AND
    advisers in one grouped query fans out, so a client with two advisers had their
    ``portfolio_value`` doubled (three advisers, tripled). ``count(DISTINCT a.id)`` hid it on
    the account count while ``SUM`` was silently wrong.

    Search, filters, sort, ``COUNT(*)`` and the page cut all run in SQL so a 25-row screen does
    not ship the book. ``cash`` and ``growth`` come from the latest ``platform.valuations`` row
    of each valued account (cash summed across source systems; growth the custodian's stated
    unrealised gain, or the administrator's when the custodian stated none), summed onto the
    client. A NULL is a figure nobody stated."""
    active: dict[str, str | Sequence[str] | None] = {
        "status": listing.clean_many(status),
        "adviser": listing.clean_many(adviser),
        "type": listing.clean_many(type),
        "account_type": listing.clean_many(account_type),
        "custodian": listing.clean_many(custodian),
        "value": listing.clean_range(value),
    }
    params: dict[str, object] = {
        "archived": archived,
        **listing.search_params(q),
        "status": active["status"],
        "adviser": active["adviser"],
        "type": active["type"],
        "account_type": active["account_type"],
        "custodian": active["custodian"],
        "custodians": list(providers.custodians()),
        **listing.range_params("value", value),
    }
    # Only the fixed _VALUED predicate, phone_expr, and the closed-set ORDER BY are interpolated —
    # never a request string.
    cte = (
        "WITH " + _LISTING_BOOK_CTES + ", shaped AS ("  # noqa: S608
        "SELECT c.id, c.uid, c.type, c.sub_type, c.service_type, c.enabled, "
        "       c.provider_status, c.currency, "
        "       COALESCE(c.sub_type::text, c.type::text) AS client_type, "
        "       c.name, c.title, c.first_name, c.surname, c.email, "
        "       " + phone_expr("c.mobile") + " AS mobile, "
        "       " + phone_expr("c.phone") + " AS phone, "
        # The address, for the line under the name when a client has no date of birth (an
        # organisation): the listing prints the town and postcode where the record's header
        # prints the whole thing.
        "       c.address, "
        # The date of birth is what tells a child from an adult in the list: the portal marks a
        # JISA holder with the child glyph by age, the same rule kickoff applies.
        "       c.dob, "
        "       COALESCE(acc.portfolio_value, 0)::float8 AS portfolio_value, "
        "       COALESCE(acc.account_count, 0) AS account_count, "
        "       (SELECT count(DISTINCT ac.account_id) "
        "          FROM crm.account_contacts ac "
        "         WHERE ac.tenant_id = c.tenant_id AND ac.contact_id = c.id "
        "           AND ac.role = 'registered_contact') "
        "         AS represented_account_count, "
        "       COALESCE(acc.valued_account_count, 0) AS valued_account_count, "
        "       COALESCE(acc.account_types, '{}') AS account_types, "
        "       COALESCE(acc.custodians, '{}') AS custodians, "
        "       acc.value_as_of, "
        # Not coalesced to 0. A NULL is "no valued account has a stated figure"; a zero is a
        # measured flat position. Same rule as the account row these are summed from.
        "       acc.cash::float8 AS cash, "
        "       acc.growth::float8 AS growth, "
        "       c.deleted_at, "
        # `role = 'adviser'` is unique per contact (0245), so this array now holds at most one
        # name — kept as a text[] (not a scalar) so the listing's filter/facet/export machinery
        # (`advisers && :adviser`) keeps working unchanged.
        "       COALESCE(array_agg(DISTINCT u.name) "
        "         FILTER (WHERE u.name IS NOT NULL), '{}') AS advisers "
        "FROM crm.contacts c "
        "LEFT JOIN LATERAL ( "
        "  SELECT SUM(a.value) FILTER (WHERE " + _VALUED + ") AS portfolio_value, "
        "         count(*) AS account_count, "
        "         count(*) FILTER (WHERE " + _VALUED + ") AS valued_account_count, "
        "         max(" + _VALUED_AT + ") FILTER (WHERE " + _VALUED + ") "
        "           AS value_as_of, "
        "         COALESCE(array_agg(DISTINCT p.product_type::text ORDER BY "
        "                            p.product_type::text) "
        "           FILTER (WHERE p.product_type IS NOT NULL), '{}') "
        "           AS account_types, "
        # `a.custodian` is the provider-code enum. Cast here, or the overlap below is
        # `provider_code[] && text[]`, which Postgres will not compare.
        "         COALESCE(array_agg(DISTINCT (" + _LISTED_CUSTODIAN_SQL + ")::text) "
        "           FILTER (WHERE (" + _LISTED_CUSTODIAN_SQL + ") IS NOT NULL), '{}') "
        "           AS custodians, "
        # Draft and closed accounts are out, the same cut as the portfolio value: a closed
        # account's cash is not money the firm holds for this client.
        "         SUM(valuation.cash_value) FILTER (WHERE " + _VALUED + ") AS cash, "
        "         SUM(valuation.unrealised_gain) FILTER (WHERE " + _VALUED + ") AS growth "
        "  FROM platform.accounts a "
        "  LEFT JOIN platform.products p ON p.id = a.product_id "
        + _ACCOUNT_VALUATION_JOIN
        + "  LEFT JOIN account_custodians cust "
        "    ON cust.account_id = a.id AND cust.tenant_id = a.tenant_id "
        "  WHERE a.contact_id = c.id "
        ") acc ON true "
        "LEFT JOIN crm.client_advisers ca ON ca.contact_id = c.id AND ca.role = 'adviser' "
        "LEFT JOIN auth.users u ON u.id = ca.adviser_id "
        "WHERE (CAST(:archived AS boolean) = (c.deleted_at IS NOT NULL)) "
        # The Spotlight bar's match over `_search` (names, email, uid, phones, national id),
        # plus the addressed name, which only this list shows. On the base row, so `_search`
        # never reaches the projection and the trigram index can serve it.
        "  AND "
        + listing.search_sql(
            "c._search", also=("TRIM(CONCAT_WS(' ', c.title, c.first_name, c.surname))",)
        )
        + " "
        "GROUP BY c.id, acc.portfolio_value,acc.account_count, "
        "         acc.valued_account_count, acc.account_types, acc.custodians, "
        "         acc.value_as_of, acc.cash, acc.growth"
        "), ranked AS ("
        " SELECT s.*, CASE"
        "   WHEN s.deleted_at IS NOT NULL THEN 'closed'"
        "   WHEN lower(COALESCE(s.provider_status, '')) IN ('suspended', 'inactive')"
        "     THEN 'suspended'"
        "   WHEN COALESCE(s.account_count, 0) = 0"
        "    AND COALESCE(s.represented_account_count, 0) = 0 THEN 'lead'"
        "   ELSE 'active' END AS derived_status FROM shaped s"
        # MATERIALIZED so the page, the total and every facet share this one join. See
        # `_listing_page_sql`. Search already ran in `shaped`, where `_search` can use its index.
        "), searched AS MATERIALIZED ("
        " SELECT * FROM ranked"
        "), filtered AS ("
        " SELECT * FROM searched WHERE " + _contacts_filter_sql() + ")"
    )
    await session.execute(text(listing.FUZZY_THRESHOLD_SQL))
    page_limit = listing.clamp_limit(limit)
    order = listing.order_sql(sort, dir, _CONTACT_SQL_SORTS, _CONTACT_SQL_DEFAULT_ORDER)
    # Equal cash (and every unstated figure, which is NULL) needs a total order. Without the id
    # a tied pair on the page boundary can show twice, or not at all, between two requests.
    if (sort or "").strip() in ("cash", "growth"):
        tie = "DESC" if (dir or "").strip().lower() == "desc" else "ASC"
        order += f", id {tie}"
    if page_limit is None:
        rows = (
            (
                await session.execute(
                    text(cte + " SELECT * FROM filtered ORDER BY " + order),  # noqa: S608
                    params,
                )
            )
            .mappings()
            .all()
        )
        items = []
        for row in rows:
            record = dict(row)
            record.pop("derived_status", None)
            items.append(record)
        return items
    params["lim"] = page_limit
    params["off"] = max(offset, 0)
    facet_rows = (
        (
            await session.execute(
                text(
                    _listing_page_sql(
                        cte,
                        order,
                        _facet_sql("derived_status", _contacts_filter_sql(skip="status"))
                        + " AS facet_status, "
                        + _facet_sql(
                            "x",
                            _contacts_filter_sql(skip="adviser"),
                            source="searched, unnest(advisers) AS x",
                            rows="DISTINCT id",
                        )
                        + " AS facet_adviser, "
                        + _facet_sql(
                            "x",
                            _contacts_filter_sql(skip="type"),
                            source="searched, unnest(ARRAY[type::text, client_type]) AS x",
                            rows="DISTINCT id",
                        )
                        + " AS facet_type, "
                        + _facet_sql(
                            "x",
                            _contacts_filter_sql(skip="account_type"),
                            source="searched, unnest(account_types) AS x",
                            rows="DISTINCT id",
                        )
                        + " AS facet_account_type, "
                        + _facet_sql(
                            "x",
                            _contacts_filter_sql(skip="custodian"),
                            source="searched, unnest(custodians) AS x",
                            rows="DISTINCT id",
                        )
                        + " AS facet_custodian, "
                        + _bounds_sql(_CONTACT_VALUE_SQL, _contacts_filter_sql(skip="value"))
                        + " AS facet_value_bounds",
                    )
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    items, total, toolbar_row = _read_listing_page(
        facet_rows,
        facets=("status", "adviser", "type", "account_type", "custodian"),
        ranges=("value",),
        drop=("derived_status",),
    )
    result: dict[str, object] = {
        "items": items,
        "total": total,
        **_with_advisers(
            _toolbar(
                toolbar_row, ("status", "adviser", "type", "account_type", "custodian"), ("value",)
            ),
            await adviser_directory(session),
        ),
    }
    return result


@router.get("/models", name="list-models")
async def list_models(session: AsyncSession = Depends(tenant_session)) -> list[dict[str, object]]:
    """The firm's enabled investment models, for pickers.

    RLS-scoped like every read here, so no tenant filter. `target_return` travels with each one
    because it is not decoration: it is the growth rate an illustration projects at, and a model
    without one cannot be illustrated against (see routers/illustrations.py). Returning it lets the
    picker say so before the adviser submits rather than after.

    `model_charge` travels for the same reason: it is the investment charge the projection is run
    net of (`FundSplit.management_charge`), so the picker can state what is being applied instead of
    the adviser having to trust that something is. Both numbers are percentage points (6.5 = 6.5%,
    0.2 = 0.2%), not Seccl's fraction.
    """
    rows = (
        (
            await session.execute(
                text(
                    """
                    SELECT m.id, m.name, m.code, m.model_type, m.risk_rating,
                           m.target_return, m.target_return_basis, m.model_charge
                      FROM platform.models m
                     WHERE m.enabled
                     ORDER BY m.name
                    """
                )
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


@router.get("/overview", name="overview")
async def overview(session: AsyncSession = Depends(book_session)) -> dict[str, object]:
    """Firm KPIs for the platform dashboard. RLS-scoped: a read-all adviser sees the whole firm,
    a scoped adviser their book.

    AUM counts only valued accounts (``_VALUED``) and is stamped with ``values_as_of`` +
    ``value_source``. There is deliberately **no performance series**: the only valuation
    history we hold is the provider extract's daily balances, whose absolute levels don't
    reconcile with these account values, and anchoring one to the other (which this endpoint
    used to do) manufactures a curve. Until a real valuation feed lands the dashboard states
    the snapshot and says so.

    There is also no ``top`` teaser: the dashboard paints the real paged book table, and the
    six-row AUM ranking (with a correlated adviser subquery per client) was paid on every
    home-page load and never rendered."""
    kpis = (
        (
            await session.execute(
                text(
                    "SELECT count(DISTINCT c.id) AS clients, "
                    "       count(a.id) AS accounts, "
                    "       count(a.id) FILTER (WHERE " + _VALUED + ") AS valued_accounts, "
                    "       COALESCE(SUM(a.value) FILTER (WHERE " + _VALUED + "), 0)::float8 "
                    "         AS aum, "
                    "       max(" + _VALUED_AT + ") FILTER (WHERE " + _VALUED + ") "
                    "         AS values_as_of, "
                    "       count(DISTINCT c.id) FILTER (WHERE c.service_type='advised') "
                    "         AS advised, "
                    "       count(DISTINCT c.id) FILTER (WHERE c.service_type='execution_only') "
                    "         AS execution_only, "
                    "       count(DISTINCT a.contact_id) FILTER (WHERE " + _VALUED + ") "
                    "         AS clients_funded "
                    "FROM crm.contacts c "
                    "LEFT JOIN platform.accounts a ON a.contact_id = c.id "
                    "WHERE c.deleted_at IS NULL"
                )
            )
        )
        .mappings()
        .one()
    )

    return {
        **dict(kpis),
        "value_source": await _value_source(session),
    }


async def _visible_accounts(
    session: AsyncSession,
    account_id: int | None = None,
    contact_id: int | None = None,
    valued_only: bool = False,
) -> dict[str, dict[str, object]]:
    """{provider external_id -> account metadata} for the accounts the caller may see
    (RLS-scoped), optionally narrowed to one account or one client's accounts. An account id the
    caller can't see simply isn't returned (RLS), so the lake never receives an unauthorised id.
    **Any CUSTODIAN, not just Seccl.** This filtered `pl.provider = 'seccl'` and its caller passed
    the literal `"seccl"` to every lake reader, so a firm custodied at SEI resolved to `{}`, read
    nothing, and showed an empty holdings table, an empty allocation donut and a flat chart beside
    an OLTP `value` that is real — indistinguishable, to the person reading it, from owning nothing.
    No leak was possible (the resolver returned nothing rather than the wrong thing) but the whole
    display path was structurally single-custodian. The account's ``custodian`` (and
    ``providers.custodians()``) is the narrowing: an account administered by Morgan Lloyd carries
    an `entity_type='account'` link too, and ML's project publishes no positions relation, so
    including it would raise `HoldingsUnavailable` and degrade the page to the dev extract. The
    join used to go through 0002's provider registry on a surrogate FK; 0141 replaced both with
    the account's own custodian code, and leaving the old SQL in place 500s every clients and
    accounts listing.

    ``valued_only`` applies the same ``_VALUED`` basis as AUM. Holdings pass it: without it a
    closed or draft account's positions were counted in the record's "Cash (provider)" figure and
    the allocation donut while being excluded from the AUM stated directly above them — two money
    figures on one band, on two different account bases. Transactions deliberately do NOT pass it:
    a closed account's movements are real history, not a valuation."""
    rows = (
        (
            await session.execute(
                text(
                    "SELECT a.id AS account_id, "  # noqa: S608 - only the fixed _VALUED predicate is interpolated
                    "       COALESCE(a.name, a.uid, 'Account') AS account_name, "
                    "       a.contact_id, a.uid AS account_uid, "
                    "       pl.provider, pl.external_id, "
                    "       COALESCE(pl.data->>'seeded_demo' = 'true', false) AS seeded_demo, "
                    # Which firm's slice these accounts live in. The lake reads are per FIRM
                    # (`tenant_{uid}.*`), so the uid has to travel with the ids — taken from the
                    # ACCOUNT rather than from the session, because it is the account's own
                    # tenant that owns the slice, and under `?tenant=` the two differ.
                    "       t.uid AS tenant_uid "
                    "FROM platform.accounts a "
                    "JOIN auth.tenants t ON t.id = a.tenant_id "
                    "JOIN platform.links pl "
                    "  ON pl.entity_type = 'account' AND pl.entity_id = a.id "
                    " AND pl.link_type = 'primary' "
                    " AND ("
                    "   (pl.provider = ANY(:custodians) "
                    "    AND (a.custodian IS NULL OR pl.provider = a.custodian)) "
                    # Demo seed may tag the book with a scheme administrator when that is the
                    # firm's only enabled provider. Those links are still the account's identity.
                    "   OR COALESCE(pl.data->>'seeded_demo' = 'true', false)"
                    " ) "
                    "WHERE (CAST(:aid AS int) IS NULL OR a.id = CAST(:aid AS int)) "
                    "  AND (CAST(:cid AS int) IS NULL OR a.contact_id = CAST(:cid AS int) "
                    "    OR EXISTS (SELECT 1 FROM crm.account_contacts ac "
                    "       WHERE ac.tenant_id = a.tenant_id AND ac.account_id = a.id "
                    "         AND ac.contact_id = CAST(:cid AS int) "
                    "         AND ac.role = 'registered_contact')) "
                    "  AND (NOT CAST(:valued AS boolean) OR " + _VALUED + ")"
                ),
                {
                    "aid": account_id,
                    "cid": contact_id,
                    "valued": valued_only,
                    "custodians": list(providers.custodians()),
                },
            )
        )
        .mappings()
        .all()
    )
    return {f"{r['provider']}:{r['external_id']}": dict(r) for r in rows}


async def _firm_read(
    reader: Any, accounts: Mapping[Any, Mapping[str, Any]], **kwargs: Any
) -> list[dict[str, Any]]:
    """Run one conformed lake read across every firm these accounts belong to.

    DEGRADES, unlike the withdrawal gate. These are DISPLAY reads — holdings, recent activity, a
    value chart — and a firm whose slice cannot be read shows empty figures beside an OLTP `value`
    that is still authoritative. Refusing the whole page would be a worse answer than a partial
    one, which is the opposite of the call `instructions.py` makes about the same warehouse: there
    the question is "may this money be paid away", and there "we can't tell" must never mean "yes".
    """
    grouped: dict[tuple[str, str, bool], list[str]] = {}
    for account in accounts.values():
        uid = account.get("tenant_uid")
        provider = account.get("provider")
        external_id = account.get("external_id")
        seeded_demo = account.get("seeded_demo") is True
        if (
            isinstance(uid, str)
            and uid
            and isinstance(provider, str)
            and provider
            and isinstance(external_id, str)
            and external_id
        ):
            grouped.setdefault((provider, uid, seeded_demo), []).append(external_id)
    if not grouped:
        return []
    reads = await asyncio.gather(
        *(
            (
                (lake_demo.reader_for(reader) or reader)(provider, uid, ids, **kwargs)
                if seeded_demo
                else reader(provider, uid, ids, **kwargs)
            )
            for (provider, uid, seeded_demo), ids in grouped.items()
        ),
        return_exceptions=True,
    )
    rows: list[dict[str, Any]] = []
    for (provider, uid, seeded_demo), read in zip(grouped, reads, strict=True):
        if isinstance(read, BaseException):
            # Named, not swallowed: an empty chart and an unreadable warehouse look identical on
            # screen, and only one of them is worth looking into.
            logger.warning("lake read unavailable for firm %s: %s", uid, read)
            # The committed fallback is one custodian's extract; it must never stand in for
            # another provider's unavailable slice. The fixture names whose — this file must
            # not, or the display-path tripwire in test_lake_holdings fails.
            if not seeded_demo and lake_fixture.covers(provider):
                fallback = await _from_dev_extract(
                    reader, grouped[(provider, uid, seeded_demo)], uid, **kwargs
                )
                for row in fallback:
                    row["_provider"] = provider
                rows.extend(fallback)
            continue
        for row in read:
            row["_provider"] = provider
        rows.extend(read)
    return rows


async def _from_dev_extract(
    reader: Any, external_ids: list[str], uid: str, **kwargs: Any
) -> list[dict[str, Any]]:
    """The committed dev extract, when a firm's own slice could not be read. Non-production only.

    A deployment whose provider chain has not run has no `tenant_{uid}` schema, so every display
    read above degrades to nothing and the portal shows an empty holdings table and a flat chart
    beside an OLTP `value` that is real — indistinguishable, to the person reading it, from owning
    nothing. `app/lake/fixture.py` answers the same three shapes off `docs/seccl.duckdb`, which
    ships in the image and keeps real Seccl account ids.

    This is the DISPLAY seam and only the display seam: `instructions.py` calls `firm_holdings`
    directly, so the withdrawal gate still refuses on an unreadable warehouse. That distinction is
    the whole reason the fallback lives here rather than inside the lake reader.
    """
    fallback = lake_fixture.reader_for(reader)
    if fallback is None or not lake_fixture.available():
        return []
    rows: list[dict[str, Any]] = await fallback(external_ids, **kwargs)
    if rows:
        logger.warning("firm %s: serving %d rows from the dev extract", uid, len(rows))
    return rows


def _is_current_holding(row: Mapping[str, object]) -> bool:
    """Is this row something the client HOLDS?

    A negative quantity says no. You cannot own minus 559,192 units of a fund, and the rows that
    carry one are not positions: they are the out-leg of a share-class migration the custodian has
    booked but not yet settled. On the dev book that is 140 of 473 rows, and rendering them as
    holdings is what put "-1,864%" in a client's own Return column.

    NETTING THEM AGAINST A POSITIVE TWIN WAS TRIED AND DOES NOT WORK: only 26 of the 140 have a
    same-name positive row to net against. The other 114 stand alone, so a netting rule would fix
    a fifth of the problem and leave the worst rows exactly as they are.

    QUANTITY, not value, so a genuinely overdrawn CASH balance still shows. Cash carries no
    quantity — it is worth what it is — and an adviser has to be able to see a negative one.

    THE COST: what is shown no longer sums to the account's own value, by the size of the
    unsettled legs (-5.21% of the dev book). That is the honest side of the trade. The account's
    value comes from OLTP and is authoritative either way; a holdings LIST is a list of holdings,
    and a row nobody can own is worse than a total that needs a footnote.
    """
    quantity = row.get("quantity")
    if quantity is None:
        return True
    if not isinstance(quantity, int | float | Decimal):
        # A provider that does not publish a numeric quantity has not stated a negative one.
        return True
    return quantity >= 0


async def _firm_holdings(
    accounts: Mapping[Any, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Current holdings for these accounts, from each firm's own conformed slice.

    DISPLAY ONLY. The filter below belongs here and NOT in `app/lake/holdings.py`, because
    `instructions.py` reads that module directly to decide whether money may be paid away — and
    there, dropping a negative position would make `funding_basis` report more available than the
    account has. A display read may simplify; the withdrawal gate may not.
    """
    rows = await _firm_read(lake_holdings.firm_holdings, accounts)
    return [row for row in rows if _is_current_holding(row)]


async def search_holdings(
    session: AsyncSession, *, contact_id: int | None, q: str, limit: int
) -> list[dict[str, object]]:
    """Small, search-shaped holdings read for the client portal's Spotlight palette.

    The listing endpoint intentionally reads and shapes the complete visible set before paging so
    its ``total`` is exact. Spotlight does not need that total; routing it through the listing made
    a two-character query fetch every position, annotate every IFSL share class in Postgres, then
    discard all but six. This path filters and caps in DuckDB and only adds the account labels the
    result rows display.
    """
    by_external = await _visible_accounts(session, contact_id=contact_id, valued_only=True)
    rows = await _firm_read(
        lake_holdings.search_firm_holdings,
        by_external,
        q=q,
        limit=limit,
    )
    return _tag(rows, by_external)


def _account_key(row: Mapping[str, object]) -> str | None:
    """The `provider:external_id` a lake row names, or None when it names neither.

    Same spelling `_visible_accounts` uses as its map key — a tuple here never matches that
    map, and a holding then silently contributes nothing to cash/growth.
    """
    provider, ext = row.get("_provider"), row.get("account_external_id")
    if isinstance(provider, str) and provider and isinstance(ext, str) and ext:
        return f"{provider}:{ext}"
    return None


def _series_return(values: list[float]) -> tuple[float, float | None]:
    """An account's return as MOVEMENT OVER ITS TRACKED VALUE SERIES — invested to now.

    THE ONE RULE, because it was two and they disagreed on screen: the accounts listing showed
    -11.6% for an account whose own record page said +856.3%, for the same account on the same
    day. The listing was right and the record page was deriving its own answer from book cost.

    NOT the lake's book-cost gain. The custodian's cost basis is unreliable in ways that make a
    percentage meaningless — a valuation stated against a cost booked at £1/unit yields +12,941%
    — so a headline derived from it is not a return, it is an artefact. The series is what the
    performance chart already draws, so this figure is also the one a client can check by eye.

    BOTH ENDS COME FROM THE SERIES. Taking "now" from the summed holdings instead would put the
    two ends on different bases — `_firm_holdings` drops unsettled negative legs and the series
    does not — so a filter applied to one end would read as performance. Same measure, both ends.

    ``(0.0, None)`` when there is no series, one point, or a non-positive start: a return needs
    something to be a return ON. Null is the honest answer where the basis is absent; zero would
    claim a measured flat return.
    """
    if len(values) < 2 or values[0] <= 0:
        return 0.0, None
    gain = round(values[-1] - values[0], 2)
    return gain, round(gain / values[0], 4)


async def _lake_metrics(
    by_external: dict[str, dict[str, object]],
) -> tuple[dict[int, dict[str, float]], dict[int, dict[str, float]]]:
    """Cash + unrealised growth per account and per contact, from the holdings summary.

    Cash is the Cash class; growth is the summed unrealised gain. The summary aggregates
    in DuckDB, so a listing does not pull every position to add two numbers. An account
    with no lake presence simply has no entry and reads as zero, which is honest: the OLTP
    ``value`` is authoritative for AUM, the lake only for what sits inside it. One read
    serves both listings so the accounts and clients pages can't disagree.

    This is a display read. The withdrawal gate still calls ``firm_holdings`` directly and
    must not be pointed at this summary.
    """
    if not by_external:
        return {}, {}
    by_account: dict[int, dict[str, float]] = {}
    by_contact: dict[int, dict[str, float]] = {}
    for holding in await _firm_read(lake_holdings.firm_holdings_summary, by_external):
        key = _account_key(holding)
        account = by_external.get(key) if key else None
        if account is None:
            continue
        cash = _num(holding.get("value")) if holding.get("klass") == "Cash" else 0.0
        growth = _num(holding.get("growth"))
        for bucket, bucket_key in (
            (by_account, account.get("account_id")),
            (by_contact, account.get("contact_id")),
        ):
            # The ids come off a mapping typed `object`; an account with no owning contact
            # (contact_id NULL) is simply not aggregated by client.
            if not isinstance(bucket_key, int):
                continue
            totals = bucket.setdefault(bucket_key, {"cash": 0.0, "growth": 0.0})
            totals["cash"] += cash
            totals["growth"] += growth

    def _round(bucket: dict[int, dict[str, float]]) -> dict[int, dict[str, float]]:
        return {k: {m: round(n, 2) for m, n in v.items()} for k, v in bucket.items()}

    return _round(by_account), _round(by_contact)


_NO_METRICS: dict[str, float] = {"cash": 0.0, "growth": 0.0}


def restrict_visible(
    by_external: dict[str, dict[str, object]],
    *,
    contact_ids: set[int] | None = None,
    account_ids: set[int] | None = None,
) -> dict[str, dict[str, object]]:
    """Narrow a `_visible_accounts` map to the ids a listing is about to return.

    The SQL that builds the map is cheap; the DuckDB holdings read is not. Listings filter and
    page in Python, so the lake must see only the paged slice — not every account the caller can
    see. Pure, so a test can pin it without a warehouse.
    """
    if not by_external or (contact_ids is None and account_ids is None):
        return by_external
    out: dict[str, dict[str, object]] = {}
    for ext, rec in by_external.items():
        # The ids come off a mapping typed `object`. A record whose id is missing or not an int
        # cannot be in an id-scoped slice, so it is dropped rather than coerced.
        if contact_ids is not None and _int_field(rec, "contact_id") not in contact_ids:
            continue
        if account_ids is not None and _int_field(rec, "account_id") not in account_ids:
            continue
        out[ext] = rec
    return out


def _int_field(row: dict[str, object], key: str) -> int | None:
    """A row's integer field, or None when it is absent or another type. Rows here are
    `dict[str, object]` (query mappings), so every id read needs the narrowing."""
    value = row.get(key)
    return value if isinstance(value, int) else None


# The largest page a listing will serve when the caller asks to page. Above this a page stops
# being a page (bounding the payload is the whole point of `limit`); below one it stops being a
# response at all — so out-of-range values are clamped into [1, _PAGE_LIMIT_MAX], not rejected.
_PAGE_LIMIT_MAX = listing.PAGE_LIMIT_MAX


# The longest search term a listing will honour. Anything longer is truncated, not rejected --
# same clamp-don't-error philosophy as `_page` -- because no asset name or account name the book
# actually carries is longer, so the tail can only be junk (or an attempt at one).
_SEARCH_Q_MAX = 80


def _search(
    rows: list[dict[str, object]], q: str | None, fields: tuple[str, ...]
) -> list[dict[str, object]]:
    """The rows whose named fields contain ``q``, case-insensitively (substring, not prefix --
    "emerging" must find "Fidelity Emerging Markets"). ``q`` is trimmed and capped at
    ``_SEARCH_Q_MAX``; blank or absent means no filtering at all, so an empty search box costs
    nothing and changes nothing. Runs on the SHAPED rows so a paged caller's ``total`` reflects
    the filtered count -- the "showing 12 matches" figure, not the unfiltered book."""
    term = (q or "").strip()[:_SEARCH_Q_MAX].lower()
    if not term:
        return rows
    return [r for r in rows if any(term in str(r.get(f) or "").lower() for f in fields)]


def _page(items: list[dict[str, object]], limit: int, offset: int) -> dict[str, object]:
    """The ``{items, total}`` envelope for a listing whose caller asked to page.

    ``total`` is the size of the FULL RLS-visible list — what the bare-list response would have
    contained — so the portal can say "showing 200 of 1,340" without a second request. ``limit``
    clamps into 1..``_PAGE_LIMIT_MAX`` and a negative ``offset`` to 0; a non-integer never gets
    here because FastAPI's typing 422s it at the wire."""
    start = max(offset, 0)
    size = min(max(limit, 1), _PAGE_LIMIT_MAX)
    return {"items": items[start : start + size], "total": len(items)}


def _tag(
    records: list[dict[str, object]], by_external: dict[str, dict[str, object]]
) -> list[dict[str, object]]:
    for r in records:
        key = _account_key(r)
        acct = by_external.get(key) if key else None
        r["account_id"] = acct["account_id"] if acct else None
        r["account_name"] = acct["account_name"] if acct else None
        r["account_uid"] = acct["account_uid"] if acct else None
    return records


async def _annotate_lookthrough(
    session: AsyncSession, records: list[dict[str, object]]
) -> list[dict[str, object]]:
    """Mark holdings whose normalised ISIN belongs to an imported IFSL share class."""
    isins = sorted(
        {
            re.sub(r"\s+", "", str(row.get("isin") or "")).upper()
            for row in records
            if row.get("isin")
        }
    )
    if not isins:
        return records
    matches = (
        (
            await session.execute(
                text(
                    """SELECT sc.isin, f.uid, f.name, latest.as_of_date
                         FROM lookthrough.share_classes sc
                         JOIN lookthrough.funds f ON f.id = sc.fund_id
                         JOIN LATERAL (
                           SELECT s.as_of_date
                             FROM lookthrough.snapshots s
                            WHERE s.fund_id = f.id
                            ORDER BY s.as_of_date DESC, s.imported_at DESC, s.id DESC
                            LIMIT 1
                         ) latest ON true
                        WHERE sc.isin = ANY(:isins)"""
                ),
                {"isins": isins},
            )
        )
        .mappings()
        .all()
    )
    by_isin = {row["isin"]: row for row in matches}
    for record in records:
        match = by_isin.get(re.sub(r"\s+", "", str(record.get("isin") or "")).upper())
        if match:
            record["lookthrough_uid"] = match["uid"]
            record["lookthrough_fund_name"] = match["name"]
            record["lookthrough_as_of"] = match["as_of_date"]
    return records


@router.get("/holdings/summary", name="holdings-summary")
async def holdings_summary(
    account_id: int | None = None,
    contact_id: int | None = None,
    session: AsyncSession = Depends(book_session),
) -> dict[str, object]:
    """Per-account, per-class totals for a client's overview donut and cash figure.

    DuckDB aggregates; the unpaged holdings list is not pulled. Cash matches
    ``classifyHolding`` so the summary and a later holdings tab cannot disagree."""
    by_external = await _visible_accounts(session, account_id, contact_id, valued_only=True)
    rows = _tag(
        await _firm_read(lake_holdings.firm_holdings_summary, by_external),
        by_external,
    )
    by_class: dict[str, float] = {}
    book = 0.0
    gain = 0.0
    legs: dict[int, dict[str, object]] = {}
    for row in rows:
        klass = str(row.get("klass") or "Equities")
        value = _num(row.get("value"))
        by_class[klass] = by_class.get(klass, 0.0) + value
        book += _num(row.get("book_cost"))
        gain += _num(row.get("growth"))
        aid = row.get("account_id")
        if isinstance(aid, int):
            acc = legs.setdefault(
                aid, {"account_id": aid, "cash": 0.0, "invested": 0.0, "as_of": None}
            )
            if klass == "Cash":
                acc["cash"] = _num(acc["cash"]) + value
            else:
                acc["invested"] = _num(acc["invested"]) + value
    return {
        "allocations": [{"label": label, "value": value} for label, value in by_class.items()],
        "cash": by_class.get("Cash", 0.0),
        "book_cost": book,
        "unrealised_gain": gain,
        "lake_total": sum(by_class.values()),
        "accounts": list(legs.values()),
    }


@router.get("/holdings", name="list-holdings")
async def list_holdings(
    account_id: int | None = None,
    contact_id: int | None = None,
    q: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    session: AsyncSession = Depends(book_session),
) -> list[dict[str, object]] | dict[str, object]:
    """Current holdings across the caller's visible accounts, optionally narrowed to one account
    (``account_id``) or one client (``contact_id`` — the adviser client-detail view), from the
    analytical lake. RLS scopes ``platform.accounts`` + the Seccl links, so only authorised
    provider ids reach the adapter. Empty when unlinked or the lake is absent.

    Only accounts on the AUM basis (``_VALUED``) contribute: these rows are what the portals turn
    into the cash figure and the allocation donut, so they must stand on the same accounts as the
    AUM shown beside them.

    ``q`` searches asset name and ISIN — the same two fields the legacy holdings page filtered
    client-side, done server-side because a paged caller only holds one page (searching a fetched
    page of a 300-holding trust would silently miss the rest of the book). It applies AFTER the
    shaping and BEFORE the page cut, so a paged ``total`` is the filtered match count and
    ``offset`` walks the filtered list; the bare list honours it too.

    Paged only on request: with no ``limit`` the bare list is the frozen shape every existing
    caller parses; with one, ``_page``'s ``{items, total}`` envelope. The page is cut AFTER the
    shaping so ``total`` is the true holdings count, taken from the one lake read already in hand
    — never a second read (the lake returns only the caller's own holdings anyway)."""
    by_external = await _visible_accounts(session, account_id, contact_id, valued_only=True)
    holdings = await _annotate_lookthrough(
        session, _tag(await _firm_holdings(by_external), by_external)
    )
    holdings = _search(holdings, q, ("asset_name", "isin"))
    # The donut's bucket, per row, so a client portal can list holdings UNDER the allocation they
    # make up. Named here (not re-derived in TypeScript) so a row and the slice it is drawn in can
    # never disagree about which class it is.
    for h in holdings:
        h["asset_class"] = _asset_class(h)
    if limit is None:
        return holdings
    # The lake orders by (account, value DESC), which leaves equal-value rows within an account
    # unordered — and an unstable order across page requests duplicates/loses rows at the seams.
    # So the paged branch pins the same display order with `asset_id` as the tiebreak (two
    # passes: Python's sort is stable, so the second keeps the first's order within its ties).
    holdings.sort(key=lambda h: str(h.get("asset_id") or ""))
    holdings.sort(key=lambda h: (str(h.get("account_external_id") or ""), -_num(h.get("value"))))
    return _page(holdings, limit, offset)


def _transaction_amount(row: dict[str, object]) -> float | None:
    value = row.get("amount")
    return float(value) if isinstance(value, int | float) else None


def _transaction_quantity(row: dict[str, object]) -> float | None:
    """The units moved, or None on a cash movement — it carries no asset and so no quantity, not
    a quantity of zero."""
    value = row.get("quantity")
    return float(value) if isinstance(value, int | float) else None


_TRANSACTION_FILTERS: listing.Filters = {
    "status": lambda row, value: listing.matches(value, row.get("status")),
    "adviser": lambda row, value: listing.matches(value, row.get("adviser")),
    "type": lambda row, value: listing.matches(value, row.get("type")),
    # The custodian's statement bucket (`report_group_kind`). The query takes the short keys
    # (`income`, `charges`, …) and the handler maps them onto the published names before SQL.
    "bucket": lambda row, value: listing.matches(value, row.get("report_group_kind")),
    # By NAME, not id, because a facet is a list of values the reader picks from and the account
    # name is what the row shows. `account_id` stays the exact narrowing a link uses; this is the
    # one a client's own ledger offers, where the useful question is "which of their accounts".
    "account": lambda row, value: listing.matches(value, row.get("account_name")),
    "currency": lambda row, value: listing.matches(value, row.get("currency")),
    "custodian": lambda row, value: listing.matches(value, row.get("custodian")),
    "amount": lambda row, value: listing.within_range(value, _transaction_amount(row)),
    "quantity": lambda row, value: listing.within_range(value, _transaction_quantity(row)),
}
_TRANSACTION_BOUNDS: dict[str, Callable[[dict[str, object]], float | None]] = {
    "amount": _transaction_amount,
    "quantity": _transaction_quantity,
}
_TRANSACTION_FACETS: listing.Facets = [
    ("status", lambda row: row.get("status")),
    ("adviser", lambda row: row.get("adviser")),
    ("type", lambda row: row.get("type")),
    ("bucket", lambda row: row.get("report_group_kind")),
    ("account", lambda row: row.get("account_name")),
    ("currency", lambda row: row.get("currency")),
    ("custodian", lambda row: row.get("custodian")),
]
# Sortable columns, keyed as the ledger keys them. Closed set — see _CONTACT_SORTS.
_TRANSACTION_SORTS: listing.Sorts = {
    "date": lambda row: row.get("traded_at") or row.get("settled_at"),
    "uid": lambda row: row.get("uid"),
    "client": lambda row: row.get("client_name"),
    "account": lambda row: row.get("account_name"),
    "type": lambda row: row.get("type"),
    "asset": lambda row: row.get("instrument_name"),
    "quantity": lambda row: row.get("quantity"),
    "isin": lambda row: row.get("isin"),
    "currency": lambda row: row.get("currency"),
    "amount": lambda row: row.get("amount"),
    "status": lambda row: row.get("status"),
}

_TRANSACTION_SQL_SORTS = {
    "date": "date",
    "uid": "uid",
    "client": "client_name",
    "account": "account_name",
    "type": "type",
    "asset": "asset",
    "quantity": "quantity",
    "isin": "isin",
    "currency": "currency",
    "amount": "amount",
    "status": "status",
}
_TRANSACTION_SQL_DEFAULT_ORDER = "date DESC NULLS LAST, id DESC"
# Fixed to UTC so this expression is IMMUTABLE and can be indexed (migration 0185). The previous
# bare `timestamptz::date` casts read the session timezone GUC and therefore could not back an
# expression index.
_TRANSACTION_DATE_SQL = (
    "COALESCE(t.transaction_date, t.value_date, "
    "(t.posted_at AT TIME ZONE 'UTC')::date, "
    "(t.synced_at AT TIME ZONE 'UTC')::date)"
)
# The word the ledger badge and the status filter use. Vendor progress stays on the column;
# a four-eye hold or refusal is the news until the money has moved (or, once approved, until
# it is still only pending). A failed send after approval shows Failed.
_TRANSACTION_STATUS_SQL = (
    "CASE "
    "WHEN t.review_status = 'awaiting_approval' THEN 'awaiting_approval' "
    "WHEN t.review_status = 'rejected' THEN 'rejected' "
    "WHEN t.review_status = 'approved' "
    " AND COALESCE(NULLIF(t.status, ''), 'settled') = 'pending' THEN 'approved' "
    "ELSE COALESCE(NULLIF(t.status, ''), 'settled') END"
)


def _transaction_filter_sql(*, skip: str | None = None) -> str:
    parts = {
        "status": "(CAST(:status AS text[]) IS NULL OR status = ANY(:status))",
        "adviser": "(CAST(:adviser AS text[]) IS NULL OR adviser = ANY(:adviser))",
        "type": "(CAST(:type AS text[]) IS NULL OR type = ANY(:type))",
        "bucket": "(CAST(:bucket AS text[]) IS NULL OR report_group_kind = ANY(:bucket))",
        "account": "(CAST(:account AS text[]) IS NULL OR account_name = ANY(:account))",
        "currency": "(CAST(:currency AS text[]) IS NULL OR currency = ANY(:currency))",
        "custodian": "(CAST(:custodian AS text[]) IS NULL OR custodian = ANY(:custodian))",
        "amount": listing.range_sql("amount", "amount"),
        # NULL on a cash movement — it carries no asset, so no quantity, and a quantity question
        # is not one it can answer either way.
        "quantity": listing.range_sql("quantity", "quantity"),
    }
    return " AND ".join(sql for key, sql in parts.items() if key != skip)


# The columns the statement strip and the clickable breakdown need. Projected as JSON so the
# paged envelope can total the MATCH set without a second round trip over the same join.
_STATEMENT_ROW_JSON = (
    "jsonb_build_object("
    "'signed_amount', signed_amount, 'type', type, 'status', status, "
    "'report_group_kind', report_group_kind, 'realised_gain', realised_gain)"
)


def _with_statement_family(
    rows: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    items = [dict(row) for row in (rows or [])]
    for entry in items:
        kind = entry.get("type")
        entry["family"] = statement.family(kind if isinstance(kind, str) else None)
    return items


@router.get("/transactions", name="list-transactions")
async def list_transactions(
    account_id: int | None = None,
    contact_id: int | None = None,
    q: str | None = None,
    # MULTI-VALUED, like the other listings: the bar offers several, and the question is an OR.
    status: Annotated[list[str] | None, Query()] = None,
    adviser: Annotated[list[str] | None, Query()] = None,
    type: Annotated[list[str] | None, Query()] = None,  # noqa: A002 - the query name the portal filters on
    family: Annotated[list[str] | None, Query()] = None,
    bucket: Annotated[list[str] | None, Query()] = None,
    isin: str | None = None,
    account: Annotated[list[str] | None, Query()] = None,
    currency: Annotated[list[str] | None, Query()] = None,
    custodian: Annotated[list[str] | None, Query()] = None,
    amount: Annotated[list[str] | None, Query()] = None,
    quantity: Annotated[list[str] | None, Query()] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    needs_action: bool = False,
    needs_categorisation: bool = False,
    sort: str | None = None,
    dir: str | None = None,  # noqa: A002 - the wire name the portal sends
    limit: int = listing.DEFAULT_LIMIT,
    offset: int = 0,
    paged: bool = False,
    session: AsyncSession = Depends(book_session),
) -> list[dict[str, object]] | dict[str, object]:
    """The firm's transactions, newest first — from ``platform.transactions``.

    **This reads OLTP, not the lake.** It used to call ``lake.seccl_transactions``, which meant
    the endpoint could only ever show one custodian's rows and carried none of the joins a book
    listing needs, so the portal shipped fixtures instead. ``platform.transactions`` is the
    conformed fact table the materialiser writes for EVERY provider, and it has the account,
    client, adviser and product beside each row — so the listing is real and provider-agnostic.
    RLS scopes it, exactly as it scopes accounts.

    ``account_id`` narrows to one account, ``contact_id`` to one client and ``isin`` to one
    instrument. ``date_from`` and ``date_to`` are inclusive value/transaction-date bounds.
    Search, filters, ``q`` and ``needs_action`` run in SQL; ``paged=true`` is a real page of
    the match set (not a 2000-row pre-fetch). The envelope is opted into with ``paged=true``
    rather than by the presence of ``limit``, because ``limit`` here has always meant "how
    many rows" and changing that would alter the shape under every existing caller.
    """
    core = (
        "SELECT t.id, "  # noqa: S608 - only the fixed `_TRANSACTION_DATE_SQL` is interpolated
        # The listing keys on `uid`; a synced transaction has no uid of its own, so
        # the provider's id namespaced by provider is the stable identity.
        "       t.source_system || ':' || t.source_transaction_id AS uid, "
        # This is spelled exactly as migration 0185's immutable UTC expression indexes. Keep the
        # SQL in `_TRANSACTION_DATE_SQL` so display, filters and indexes cannot drift.
        "       " + _TRANSACTION_DATE_SQL + " AS date, "
        # COALESCE, matching how `_visible_accounts` labels a HOLDING's account:
        # both listings carry an account filter whose value is this label, so a bare
        # `a.name` left an unnamed account blank here and named by its uid there —
        # one account under two spellings, and the filter matching neither.
        "       t.account_id, a.uid AS account_uid, "
        "       COALESCE(a.name, a.uid, 'Account') AS account_name, "
        "       a.contact_id AS client_id, c.uid AS client_uid, "
        "       COALESCE(NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.surname)), ''), "
        "                c.name) AS client_name, "
        "       t.type::text AS type, "
        # The custodian's own words are more useful in a listing than the canonical
        # bucket, which is already the `type` column beside it.
        "       COALESCE(NULLIF(t.source_subtype, ''), NULLIF(t.source_type, ''), "
        "                t.type::text) AS subtype, "
        "       t.data ->> 'method' AS method, "
        "       t.amount::float8 AS amount, "
        "       " + statement.SIGNED_SQL + " AS signed_amount, "
        "       t.dealing_charges::float8 AS dealing_charges, "
        "       t.other_charges::float8 AS other_charges, t.fee_type, "
        "       t.trade_date, t.transaction_date, "
        "       t.book_cost::float8 AS book_cost, t.realised_gain::float8 AS realised_gain, "
        "       t.equalisation_payment::float8 AS equalisation_payment, "
        "       t.subscription_amount::float8 AS subscription_amount, "
        "       t.report_group_kind, t.report_group, "
        "       t.asset_name AS asset, t.isin, "
        "       t.quantity::float8 AS quantity, "
        "       t.price::float8 AS price, "
        "       t.fee_amount::float8 AS fee_amount, "
        "       t.tax_amount::float8 AS tax_amount, "
        "       t.settlement_amount::float8 AS settlement_amount, "
        "       " + _TRANSACTION_STATUS_SQL + " AS status, "
        "       p.product_type::text AS product_type, "
        "       t.currency, a.currency AS base_currency, "
        # `_RESOLVED_CUSTODIAN_SQL`'s rule, read from the per-account `account_custodians` CTE
        # rather than as its correlated subquery: that subquery ran once per TRANSACTION row
        # (8,141 times, through `links`' policy each time, ~1.1s) to answer ~40 accounts.
        "       COALESCE(a.custodian, cust.provider)::text AS custodian, "
        "       t.settlement_date, t.expectation_id, "
        "       t.source_system, t.source_transaction_id, "
        # Money into a pension with nothing recorded against it is the contributions
        # workbench's queue. Flagged here too so the strip on a client/account page
        # agrees with it rather than deriving the rule a second time.
        "       (t.type = 'deposit' AND t.amount > 0 "
        "        AND t.source_system = ANY(:feed) "
        "        AND UPPER(COALESCE(p.product_type::text, '')) = ANY (:pension) "
        "        AND t.expectation_id IS NULL "
        "        AND NOT EXISTS (SELECT 1 FROM platform.contributions cc "
        "                         WHERE cc.transaction_id = t.id)) "
        "         AS needs_categorisation, "
        "       adv.name AS adviser "
        "FROM platform.transactions t "
        "  JOIN platform.accounts a "
        "    ON a.id = t.account_id AND a.tenant_id = t.tenant_id "
        "  LEFT JOIN platform.products p ON p.id = a.product_id "
        # A plain join against the `live_contacts`/`advisers` CTEs below, not a direct join to
        # `crm.contacts` or the LATERAL the adviser lookup used to be. Under RLS,
        # `contacts`/`client_advisers`/`accounts`'s `USING` clauses are dense with
        # `current_setting()`/`jsonb ->>` calls the planner has no statistics for, so it grossly
        # under-estimates the row count flowing through these joins — and with the plan looking
        # like "about 1 row" no matter how large the firm's book actually is, Postgres never
        # considers hashing either side: it re-runs the RLS-scoped lookup once per TRANSACTION row
        # instead of once per DISTINCT client (measured, firm-wide: 60,000 re-evaluations of
        # `contacts`' policy for a book of ~20 accounts). Each `MATERIALIZED` CTE computes its
        # RLS-scoped relation ONCE, ahead of the join, so the planner's bad estimate no longer
        # matters -- a Nested Loop into a small in-memory set costs about the same as a Hash Join
        # would have. Cheap because each stays small: one tenant's contacts, not the join's output.
        "  LEFT JOIN live_contacts c ON c.id = a.contact_id "
        "  LEFT JOIN advisers adv ON adv.contact_id = a.contact_id "
        "  LEFT JOIN account_custodians cust "
        "    ON cust.account_id = a.id AND cust.tenant_id = a.tenant_id "
        "WHERE (CAST(:account_id AS int) IS NULL OR t.account_id = :account_id) "
        "  AND (CAST(:contact_id AS int) IS NULL OR a.contact_id = :contact_id "
        "    OR EXISTS (SELECT 1 FROM crm.account_contacts ac "
        "       WHERE ac.tenant_id = a.tenant_id AND ac.account_id = a.id "
        "         AND ac.contact_id = CAST(:contact_id AS int) "
        "         AND ac.role = 'registered_contact')) "
        "  AND (CAST(:isin AS text) IS NULL OR t.isin = :isin) "
        "  AND (CAST(:date_from AS date) IS NULL OR " + _TRANSACTION_DATE_SQL + " >= :date_from) "
        "  AND (CAST(:date_to AS date) IS NULL OR " + _TRANSACTION_DATE_SQL + " <= :date_to)"
    )
    families = listing.clean_many(family)
    active: dict[str, str | Sequence[str] | None] = {
        "status": listing.clean_many(status),
        "adviser": listing.clean_many(adviser),
        "type": listing.clean_many(type),
        "bucket": listing.clean_many(bucket),
        "account": listing.clean_many(account),
        "currency": listing.clean_many(currency),
        "custodian": listing.clean_many(custodian),
        "amount": listing.clean_range(amount),
        "quantity": listing.clean_range(quantity),
    }
    active["type"] = statement.types_for(families, active["type"])
    buckets = active.get("bucket")
    if buckets:
        names = [name for name, key in statement.BREAKDOWN_GROUPS.items() if key in buckets]
        active["bucket"] = names or list(buckets)
    params: dict[str, object] = {
        "account_id": account_id,
        "contact_id": contact_id,
        "isin": listing.clean(isin),
        "date_from": date_from,
        "date_to": date_to,
        "pension": list(PENSION_PRODUCTS),
        "feed": list(providers.PROVIDERS),
        "q": listing.like_pattern(q),
        "status": active["status"],
        "adviser": active["adviser"],
        "type": active["type"],
        "bucket": active["bucket"],
        "account": active["account"],
        **statement.sql_params(),
        "currency": active["currency"],
        "custodian": active["custodian"],
        "custodians": list(providers.custodians()),
        **listing.range_params("amount", amount),
        **listing.range_params("quantity", quantity),
        "needs_action": needs_action or needs_categorisation,
    }
    cte = (
        # MATERIALIZED: forces each to be computed once, as its own small relation, rather than
        # inlined into `shaped` and re-evaluated per outer row — see the comment on the join below.
        "WITH account_custodians AS MATERIALIZED ("  # noqa: S608
        # One row per account: the first primary custodian link by provider code, exactly the
        # `ORDER BY l.provider LIMIT 1` of `_RESOLVED_CUSTODIAN_SQL`.
        "  SELECT DISTINCT ON (l.tenant_id, l.entity_id) "
        "         l.tenant_id, l.entity_id AS account_id, l.provider "
        "  FROM platform.links l "
        "  WHERE l.entity_type = 'account' AND l.link_type = 'primary' "
        "    AND l.provider = ANY(:custodians) "
        "  ORDER BY l.tenant_id, l.entity_id, l.provider"
        # `role = 'adviser'` is unique per contact (0245); DISTINCT ON is now belt-and-braces
        # rather than a tie-break.
        "), advisers AS MATERIALIZED ("
        "  SELECT DISTINCT ON (ca.contact_id) ca.contact_id, u.name "
        "  FROM crm.client_advisers ca JOIN auth.users u ON u.id = ca.adviser_id "
        "  WHERE ca.role = 'adviser' "
        "  ORDER BY ca.contact_id, u.name NULLS LAST"
        "), live_contacts AS MATERIALIZED ("
        "  SELECT id, uid, first_name, surname, name FROM crm.contacts WHERE deleted_at IS NULL"
        "), shaped AS (" + core + "), "
        # The running total is a window over the account's WHOLE history (this `shaped` set),
        # computed before search and filters so a charges-only view still shows what the row
        # actually had. Unscoped across the firm it is not a quantity.
        "with_totals AS ("
        " SELECT src.*, "
        + (
            f"SUM({statement.BALANCE_EFFECT_SQL}) OVER "
            "(ORDER BY src.date ASC NULLS FIRST, src.id ASC)"
            if account_id is not None
            else "NULL::float8"
        )
        + " AS running_total FROM shaped src"
        "), "
        # MATERIALIZED: `searched` is the one base every read below shares -- the page, the total,
        # and each facet's own count (which must see every OTHER filter applied but not its own,
        # hence each facet re-deriving `filtered` from `searched` rather than reusing it directly).
        # Left to inline, a paged request re-runs the whole `shaped` join once per reference --
        # measured, unfiltered: ~800ms-2.5s EACH for the page, the count and the facet row, issued
        # as three separate round trips. Pinning `searched` here and answering all of them from ONE
        # statement (below) means Postgres computes the join exactly once per request, not three
        # times sequentially.
        "searched AS MATERIALIZED ("
        " SELECT * FROM with_totals WHERE CAST(:q AS text) IS NULL"
        "    OR uid ILIKE :q ESCAPE '\\'"
        "    OR account_name ILIKE :q ESCAPE '\\'"
        "    OR client_name ILIKE :q ESCAPE '\\'"
        "    OR adviser ILIKE :q ESCAPE '\\'"
        "    OR subtype ILIKE :q ESCAPE '\\'"
        "    OR asset ILIKE :q ESCAPE '\\'"
        "    OR isin ILIKE :q ESCAPE '\\'"
        "    OR report_group ILIKE :q ESCAPE '\\'"
        "), filtered AS ("
        " SELECT * FROM searched WHERE "
        + _transaction_filter_sql()
        + " AND (NOT CAST(:needs_action AS boolean) OR needs_categorisation)"
        ")"
    )
    order = listing.order_sql(sort, dir, _TRANSACTION_SQL_SORTS, _TRANSACTION_SQL_DEFAULT_ORDER)
    size = listing.clamp_limit(limit) or listing.DEFAULT_LIMIT if paged else min(max(limit, 1), 500)
    params["lim"] = size
    params["off"] = max(offset, 0) if paged else 0
    # The custodian payload is joined only onto the rows this response returns. It does not
    # belong in `searched`: that relation is the whole match set, and a file row's payload is
    # the entire report line. The page needs it for the sterling line and the drawer.
    payload_join = (
        " SELECT page.*, src.data AS payload FROM ("  # noqa: S608
        "SELECT *, row_number() OVER (ORDER BY " + order + ") AS __sort_ordinal "
        "FROM filtered ORDER BY " + order + " LIMIT :lim OFFSET :off"
        ") page JOIN platform.transactions src ON src.id = page.id "
        "ORDER BY page.__sort_ordinal"
    )
    if not paged:
        select_sql = cte + payload_join  # noqa: S608
        rows = (await session.execute(text(select_sql), params)).mappings().all()
        return [present_transaction(dict(row)) for row in rows]

    # ONE statement, not three: see the MATERIALIZED comment on `searched` above. `to_jsonb` on the
    # page's rows costs nothing extra Postgres wasn't already going to do to hand the driver a
    # result set, and it's what lets the page, the total and every facet share that one computed
    # `searched` within a single round trip instead of three.
    combined_sql = (
        cte  # noqa: S608
        + " SELECT "
        + "(SELECT coalesce(jsonb_agg("
        "to_jsonb(shown) - '__sort_ordinal' ORDER BY shown.__sort_ordinal"
        "), '[]'::jsonb) FROM ("
        + payload_join
        + ") shown) AS items, "
        + "(SELECT count(*) FROM filtered) AS total, "
        + _facet_sql("status", _transaction_filter_sql(skip="status"))
        + " AS status, "
        + _facet_sql("adviser", _transaction_filter_sql(skip="adviser"))
        + " AS adviser, "
        + _facet_sql("type", _transaction_filter_sql(skip="type"))
        + " AS type, "
        + _facet_sql("account_name", _transaction_filter_sql(skip="account"))
        + " AS account, "
        + _facet_sql("currency", _transaction_filter_sql(skip="currency"))
        + " AS currency, "
        + _facet_sql("custodian", _transaction_filter_sql(skip="custodian"))
        + " AS custodian, "
        + _bounds_sql("amount", _transaction_filter_sql(skip="amount"))
        + " AS amount_bounds, "
        # The filtered span first, same as amount. Pending orders and cash movements carry no
        # quantity, and a page of only those has no ends — the slider would vanish while Amount
        # still has one. Fall back to every unit the search still contains, so the track stays.
        + "COALESCE("
        + _bounds_sql("quantity", _transaction_filter_sql(skip="quantity"))
        + ", "
        + _bounds_sql("quantity", "quantity IS NOT NULL")
        + ") AS quantity_bounds, "
        + "(SELECT coalesce(jsonb_agg("
        + _STATEMENT_ROW_JSON
        + "), '[]'::jsonb) FROM filtered) AS statement_rows, "
        # Before the bucket filter, so clicking a figure still shows the way back out of that
        # bucket. When no bucket is set this is the same set as `statement_rows`.
        + "(SELECT coalesce(jsonb_agg("
        + _STATEMENT_ROW_JSON
        + "), '[]'::jsonb) FROM searched WHERE "
        + _transaction_filter_sql(skip="bucket")
        + " AND (NOT CAST(:needs_action AS boolean) OR needs_categorisation)"
        ") AS unbucketed_rows"
    )
    row = (await session.execute(text(combined_sql), params)).mappings().one()
    items = [present_transaction(item) for item in row["items"]]
    match = _with_statement_family(row["statement_rows"])
    unbucketed = _with_statement_family(row["unbucketed_rows"])
    toolbar = _with_advisers(
        _toolbar(
            row,
            ("status", "adviser", "type", "account", "currency", "custodian"),
            ("amount", "quantity"),
        ),
        await adviser_directory(session),
    )
    facet_values = toolbar.get("facets")
    facets: dict[str, object] = dict(facet_values) if isinstance(facet_values, dict) else {}
    facets["family"] = sorted({str(entry["family"]) for entry in match})
    toolbar["facets"] = facets
    return {
        "items": items,
        "total": int(row["total"]),
        "totals": statement.totals(match),
        "breakdown": statement.breakdown(unbucketed),
        "capped": False,
        **toolbar,
    }


# ── Addressing ───────────────────────────────────────────────────────────────
# A client and an account are named by their `uid` — in the URL a person sees, and in the API path
# behind it. `GET /crm/{contacts,accounts}/by-uid/{uid}` used to sit here: the portals' routes
# carried the uid, these endpoints took an `int`, and the resolve bridged them. It cost a round
# trip on every form action and standalone `+server.ts`, each of which fetched a whole record to
# keep one integer out of it.
#
# The path takes the uid now (`app/addressing.py`), so there is nothing to bridge and the resolve
# is gone. The lookup still runs inside the caller's own RLS-bound session, so a uid outside their
# book resolves to nothing and 404s — indistinguishable from "no such record", which is the
# fail-closed behaviour by-uid was written with.


def _withdrawal_fees(custodian: object) -> dict[str, float] | None:
    """SEI's rail tariff, or null for a custodian that does not charge it."""
    if custodian != providers.code_named("SEI"):
        return None
    return {
        "faster_payment": settings.sei_faster_payment_fee,
        "swift": settings.sei_swift_fee,
    }


@router.get("/accounts/{account_uid}", name="get-account")
async def get_account(
    account_id: Annotated[int, Depends(book_account_id)],
    session: AsyncSession = Depends(book_session),
) -> dict[str, object]:
    """A single account the caller may see (RLS), with everything the detail page renders:
    the owning client, adviser, mandate, lake cash/growth and the fees / regular payments /
    withdrawals JSON. 404 if absent or not visible.

    ``bank_accounts`` is this account's own (jsonb array, ``[0]`` the default). Resolution is
    account first, client second (``app/bank_accounts.py``). The nested ``client.bank_account``
    is the fallback the card shows when this list is empty.
    """
    row = (
        (
            await session.execute(
                text(
                    "SELECT a.id, a.tenant_id, a.uid, a.name, a.status, a.currency, "  # noqa: S608 - only the fixed _VALUED_AT expression is interpolated
                    "       a.value::float8 AS value, " + _VALUED_AT + " AS value_as_of, "
                    "       a.allowance_used::float8 AS allowance_used, "
                    "       a.allowance_limit::float8 AS allowance_limit, "
                    "       a.allowance_remaining::float8 AS allowance_remaining, "
                    "       a.allowance_tax_year, "
                    "       a.contact_id, a.risk_level, a.product_id, "
                    "       a.custody, a.opened_at, a.closed_at, "
                    "       a.fees, a.payments, a.withdrawals, a.transfer, "
                    "       a.bank_accounts, "
                    "       a.platform_identifier, "
                    "       p.name AS product_name, p.code AS product_code, p.product_type, "
                    "       COALESCE(p.bank_details_allowed, true) AS bank_details_allowed, "
                    # The shelf's answer, for the same gate the client record uses it for — here
                    # it is what shows the Beneficiaries panel.
                    "       COALESCE(p.is_pension, false) AS is_pension, "
                    # WHO ADMINISTERS THE SCHEME, which is a different question from who holds
                    # the assets (`a.custodian`) and the one that decides whether this account has
                    # a drawdown journey at all: the custodian holds the money, the administrator
                    # runs the scheme, and only an administrator publishes a member journey.
                    #
                    # Derived from `providers.administrators()` rather than naming Morgan Lloyd, so
                    # a second administrator is not silently excluded -- the rule
                    # `provider_positions` below already follows for the same reason. `role` is
                    # PREFERRED and not required: 0130's backfill was the only thing that ever
                    # wrote it until 0140 made the sink stamp it, so a mapping created in between
                    # is a real administrator with a NULL role and demanding the column would hide
                    # it.
                    "       (SELECT pp.provider::text FROM platform.product_platforms pp "
                    "         WHERE pp.product_id = p.id AND pp.tenant_id = a.tenant_id "
                    "           AND pp.provider = ANY(:administrators) "
                    "         ORDER BY (pp.role = 'scheme_administrator') DESC NULLS LAST "
                    "         LIMIT 1) AS scheme_administrator, "
                    "       a.model_id, a.model_id IS NOT NULL AS discretionary, "
                    # The account's SERVICE TYPE, decided here so the column, the filter, the
                    # facet and the sort cannot disagree about one account. `investment_capacity`
                    # is the captured fact (onboarding asks it: advisory | discretionary |
                    # execution_only). A custodian that did not say — SEI leaves
                    # `is_discretionary` null — is not turned into "discretionary" because a
                    # model is attached. The model is its own column. Advisory and execution-only
                    # are indistinguishable from a missing capacity, and guessing would put a
                    # service agreement on the screen that nobody agreed to.
                    "       NULLIF(a.investment_capacity, '') AS service_type, "
                    "       m.name AS model_name, "
                    # THE CHARGE, not just the firm's label for it. This projected `name` alone,
                    # so the record's Fees card listed "Advised standard" and stated neither the
                    # figure nor how often it is taken — the catalogue holds both, and an adviser
                    # reading an account has to answer "what is this client paying?" from the
                    # account they are looking at rather than from the fee catalogue in another
                    # section. The rule itself is jsonb, so the reader formats it (one
                    # implementation, `book/journeys/fee-summary.ts`).
                    "       COALESCE((SELECT jsonb_agg(jsonb_build_object("
                    "           'id', fp.id, 'name', fp.name, 'sort_order', afp.sort_order, "
                    "           'fee_type', fp.fee_type, 'rule', fp.rule, "
                    "           'frequency', fp.frequency, 'currency', fp.currency, "
                    "           'charge_basis', fp.charge_basis, 'payer', fp.payer, "
                    "           'vatable', fp.vatable, 'minimum_amount', fp.minimum_amount, "
                    "           'maximum_amount', fp.maximum_amount, "
                    "           'description', fp.description"
                    "         ) ORDER BY afp.sort_order, fp.name, fp.id) "
                    "         FROM platform.account_fee_packages afp "
                    "         JOIN platform.fee_packages fp "
                    "           ON fp.id = afp.fee_package_id AND fp.tenant_id = afp.tenant_id "
                    "        WHERE afp.account_id = a.id AND afp.tenant_id = a.tenant_id"
                    "       ), '[]'::jsonb) AS fee_packages, "
                    # Prefer the account's stated custodian; for an older provider-fed row where
                    # it is absent, use the same deterministic primary-link fallback as the money
                    # gate. The tariff shown in the drawer and the fee reserved on submit must
                    # answer from one provider.
                    "       COALESCE(a.custodian, ("
                    "         SELECT l.provider FROM platform.links l "
                    "          WHERE l.tenant_id = a.tenant_id "
                    "            AND l.entity_type = 'account' AND l.entity_id = a.id "
                    "            AND l.link_type = 'primary' AND l.provider = ANY(:custodians) "
                    "          ORDER BY l.provider LIMIT 1"
                    "       )) AS custodian, "
                    "       (SELECT tr.ceding_provider FROM platform.transfers tr "
                    "         WHERE tr.account_id = a.id ORDER BY tr.created_at DESC LIMIT 1) "
                    "         AS third_party_pension_provider, "
                    # CASH HELD SOMEWHERE ELSE, AND THE CRYSTALLISATION SPLIT.
                    #
                    # Cash sums. The custodian's uninvested balance is `a.cash` (the lake). The
                    # scheme administrator's bank balance is a different pot — Morgan Lloyd, for
                    # a SIPP — and SEI does not know about cash that has been passed across. A
                    # custodian row in this list therefore carries cash NULL, so the screen
                    # cannot add the lake figure twice.
                    #
                    # The split does not sum. It is one fact about the fund. The administrator's
                    # latest row wins when it has stated crystallised or uncrystallised; otherwise
                    # the custodian's own split is shown (Seccl's portfolio group, or SEI's
                    # Accounts columns). `market_value` is deliberately not read: the
                    # administrator's fund figure is the valuation we pushed it.
                    #
                    # A pension, because that is the only wrapper with a crystallisation. The shelf
                    # flag is what includes TPPENSION: `_PENSION_PRODUCTS` deliberately omits it,
                    # because that set also gates drawdown and the Morgan Lloyd valuation push,
                    # and a third-party pension is not a scheme this platform administers. An ISA
                    # allowance travels on the account columns above, not in this list.
                    "       COALESCE((SELECT jsonb_agg(jsonb_build_object("
                    "           'source', v.source_system, 'cash', v.cash_value,"
                    "           'as_of', v.as_of_date,"
                    "           'crystallised', v.crystallised_value,"
                    "           'uncrystallised', v.uncrystallised_value"
                    "         ) ORDER BY v.source_system) "
                    "         FROM ("
                    # Parentheses are required: PostgreSQL rejects ORDER BY on a UNION arm
                    # that is not itself a sub-select.
                    "           (SELECT DISTINCT ON (source_system) "
                    "                  source_system, as_of_date, cash_value, "
                    "                  crystallised_value, uncrystallised_value "
                    "             FROM platform.valuations "
                    "            WHERE account_id = a.id "
                    "              AND source_system = ANY(:administrators) "
                    "            ORDER BY source_system, as_of_date DESC) "
                    "           UNION ALL "
                    "           SELECT c.source_system, c.as_of_date, NULL::numeric, "
                    "                  c.crystallised_value, c.uncrystallised_value "
                    "             FROM (SELECT DISTINCT ON (source_system) "
                    "                          source_system, as_of_date, "
                    "                          crystallised_value, uncrystallised_value "
                    "                     FROM platform.valuations "
                    "                    WHERE account_id = a.id "
                    "                      AND source_system = ANY(:custodians) "
                    "                      AND (crystallised_value IS NOT NULL "
                    "                           OR uncrystallised_value IS NOT NULL) "
                    "                    ORDER BY source_system, as_of_date DESC) c "
                    "            WHERE NOT EXISTS ("
                    "              SELECT 1 FROM ("
                    "                SELECT DISTINCT ON (source_system) "
                    "                       crystallised_value, uncrystallised_value "
                    "                  FROM platform.valuations "
                    "                 WHERE account_id = a.id "
                    "                   AND source_system = ANY(:administrators) "
                    "                 ORDER BY source_system, as_of_date DESC"
                    "              ) admin_split "
                    "              WHERE admin_split.crystallised_value IS NOT NULL "
                    "                 OR admin_split.uncrystallised_value IS NOT NULL"
                    "            )"
                    "         ) v "
                    "        WHERE (COALESCE(p.is_pension, false) "
                    "               OR upper(coalesce(p.product_type::text, '')) = ANY(:pensions)) "
                    "          AND (v.cash_value IS NOT NULL "
                    "               OR v.crystallised_value IS NOT NULL "
                    "               OR v.uncrystallised_value IS NOT NULL)"
                    "       ), '[]'::jsonb) AS provider_positions, "
                    "       c.uid AS c_uid, c.type AS c_type, c.name AS c_name, "
                    "       c.title AS c_title, c.first_name AS c_first_name, "
                    "       c.surname AS c_surname, c.address AS c_address, "
                    "       c.bank_account AS c_bank_account, "
                    "       c.relevant_uk_earnings::float8 AS c_earnings, "
                    "       c.pension_protection AS c_pension_protection, "
                    "       c.flexi_access_taken AS c_flexi_access_taken, "
                    # The owning client's vulnerability travels with the account, because the
                    # account page is a place an adviser acts on that person's money without
                    # necessarily having opened their record first (0054).
                    "       c.is_vulnerable AS c_is_vulnerable, "
                    "       c.vulnerability_reasons AS c_vulnerability_reasons, "
                    "       c.vulnerability_characteristics AS c_vulnerability_characteristics, "
                    "       c.vulnerability_is_temporary AS c_vulnerability_is_temporary, "
                    "       c.vulnerability_recorded_at AS c_vulnerability_recorded_at, "
                    "       c.vulnerability_notes AS c_vulnerability_notes, "
                    # The adviser role is unique per contact (0245), so no tie-break ordering is
                    # needed.
                    "       (SELECT u.name FROM crm.client_advisers ca "
                    "          JOIN auth.users u ON u.id = ca.adviser_id "
                    "         WHERE ca.contact_id = a.contact_id AND ca.role = 'adviser') "
                    "         AS adviser, "
                    # The custodian's own account number (SEI's, for a SEI account): the payment
                    # reference a client quotes so the credit reconciles. With that custodian's
                    # collection bank account, configured on the firm's SEI platform settings.
                    "       prim.external_id AS custodian_account_number, "
                    "       ptp.config ->> 'pay_in_account_name' AS pay_in_account_name, "
                    "       ptp.config ->> 'pay_in_sort_code' AS pay_in_sort_code, "
                    "       ptp.config ->> 'pay_in_account_number' AS pay_in_account_number "
                    "FROM platform.accounts a "
                    "LEFT JOIN platform.products p ON p.id = a.product_id "
                    "LEFT JOIN platform.models m ON m.id = a.model_id "
                    "LEFT JOIN crm.contacts c ON c.id = a.contact_id AND c.deleted_at IS NULL "
                    "LEFT JOIN LATERAL ("
                    "  SELECT l.provider, l.external_id FROM platform.links l "
                    "   WHERE l.tenant_id = a.tenant_id AND l.entity_type = 'account' "
                    "     AND l.entity_id = a.id AND l.link_type = 'primary' "
                    "     AND l.provider = ANY(:custodians) "
                    "   ORDER BY l.provider LIMIT 1) prim ON true "
                    "LEFT JOIN platform.tenant_platforms ptp "
                    "  ON ptp.tenant_id = a.tenant_id "
                    "  AND ptp.provider::text = COALESCE(a.custodian::text, prim.provider::text) "
                    "WHERE a.id = :aid"
                ),
                {
                    "aid": account_id,
                    # Bound, not interpolated, even though both come from our own registries —
                    # `= ANY(:param)` is the shape that needs no `noqa` and no reader has to
                    # check where the list came from.
                    "administrators": list(providers.administrators()),
                    "custodians": list(providers.custodians()),
                    "pensions": sorted(_PENSION_PRODUCTS),
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    record = dict(row)
    # NAMED SERVER-SIDE, from the one registry. `platform.platforms.name` used to supply this and
    # 0131 dropped the table; deriving it here rather than in each portal is the rule six drifted
    # hard-coded copies established — the one that mattered fell back to Morgan Lloyd's name for
    # anything unlisted, so every SEI surface carried the wrong custodian's name above SEI's own
    # data. `meta()` owns "unnamed beats misnamed", and an account with no custodian recorded
    # stays null rather than becoming a guess.
    record["platform_name"] = (
        providers.meta(str(record["custodian"]))["name"] if record.get("custodian") else None
    )
    # Same rule as the custodian: named server-side from the one registry, so a portal
    # cannot fall back to another provider's display name. Null when the product names
    # no administrator (every GIA/ISA, and a pension whose shelf item is not mapped).
    record["scheme_administrator_name"] = (
        providers.meta(str(record["scheme_administrator"]))["name"]
        if record.get("scheme_administrator")
        else None
    )
    client = {
        "id": record["contact_id"],
        "uid": record.pop("c_uid"),
        "type": record.pop("c_type"),
        "name": record.pop("c_name"),
        "title": record.pop("c_title"),
        "first_name": record.pop("c_first_name"),
        "surname": record.pop("c_surname"),
        "address": record.pop("c_address"),
        "bank_account": record.pop("c_bank_account"),
        "relevant_uk_earnings": record.pop("c_earnings"),
        "pension_protection": record.pop("c_pension_protection"),
        "flexi_access_taken": record.pop("c_flexi_access_taken"),
        "is_vulnerable": record.pop("c_is_vulnerable"),
        "vulnerability_reasons": record.pop("c_vulnerability_reasons"),
        "vulnerability_characteristics": record.pop("c_vulnerability_characteristics"),
        "vulnerability_is_temporary": record.pop("c_vulnerability_is_temporary"),
        "vulnerability_recorded_at": record.pop("c_vulnerability_recorded_at"),
        "vulnerability_notes": record.pop("c_vulnerability_notes"),
    }
    visible = await _visible_accounts(session, account_id)
    by_account, _ = await _lake_metrics(visible)
    metrics = by_account.get(account_id, _NO_METRICS)
    # THE LISTING'S CASH, not the positions summary. Positions keep only the latest file, so an
    # account that file omits has no cash row and the record showed £0 — and the withdrawal
    # drawer then refused — while the listing still showed the custodian's last stated balance.
    # Administrator cash stays out of this figure: `provider_positions` carries it, and the
    # record adds the two once.
    stated_cash = await instructions_mod.stated_custodian_cash(
        session, [account_id], tenant_id=int(record.pop("tenant_id"))
    )
    # THE SAME RETURN THE ACCOUNTS LISTING SHOWS, from the same rule (`_series_return`). The
    # record page used to derive its own from the holdings' book cost, so one account read
    # -11.6% on the listing and +856.3% on its own page, on the same day. Whichever figure is
    # more defensible, a client cannot be shown both.
    #
    # `gain_pct` is NULL rather than zero when there is no series to stand on, so the page can
    # say nothing instead of claiming a flat year.
    series = sorted(
        await _firm_read(lake_holdings.firm_value_series, visible),
        key=lambda row: str(row.get("date") or ""),
    )
    # No per-account filter: `_visible_accounts(session, account_id)` is already narrowed to this
    # one account, so the read returns its series and nothing else.
    values = [_num(row.get("value")) for row in series]
    gain, pct = _series_return(values)
    record["gain"] = gain
    record["gain_pct"] = pct
    # The series ITSELF, in `/crm/home`'s `performance` shape, so the account page can wear the
    # same header the portfolio does (its own trace, its own period change) instead of a plain
    # figure. Capped like home's; `performance_period` names the basis (None without a series).
    record["performance"] = [
        {"label": str(row.get("date") or ""), "value": round(_num(row.get("value")), 2)}
        for row in series
    ][-120:]
    record["performance_period"] = (
        {"from": record["performance"][0]["label"], "to": record["performance"][-1]["label"]}
        if record["performance"]
        else None
    )
    # The register the overview card renders. Older onboarding rows hold an object, not a list
    # (see `regular_movements`); projecting here means they still show rather than read as none.
    record["payments"] = regular_movements_mod.regular_movements_of(record.get("payments")) or None
    record["withdrawals"] = (
        regular_movements_mod.regular_movements_of(record.get("withdrawals")) or None
    )
    # Empty list and NULL are the same reading: this account has none of its own, so the card
    # falls back to the client's. Projected here so a save of the account's details can round-trip
    # — until it did, the surface always read `client.bank_account` and the fallback sentence
    # stayed up after a write that had nowhere to land on the read.
    record["bank_accounts"] = bank_accounts_mod.stored_entries(record.get("bank_accounts"))
    # SEI's own tariff, only on an account SEI custodies. Applying it to Seccl or a future
    # custodian would state a charge that provider never made and reduce the UI's apparent cash.
    record["withdrawal_fees"] = _withdrawal_fees(record.get("custodian"))
    # The account record's "IO Plans" card — pre-populated, no search: whichever plans Intelliflo
    # has for the client this ACCOUNT'S CONTACT is already linked to (platform.links), empty when
    # that client has never been linked. A vendor failure degrades to an empty dropdown rather
    # than failing the whole account page over a preview-only read.
    plans = await _io_plans_for_contact(session, record.get("contact_id"))
    record["io_plans"] = [p.model_dump() for p in plans]
    # This account's OWN persisted pick from that dropdown (platform.links, entity_type='account')
    # — read back the same way the client record's io_link rides along, so a reload shows the
    # real selection rather than an empty dropdown.
    io_account_link_row = (
        (
            await session.execute(
                text(
                    "SELECT data FROM platform.links "
                    "WHERE entity_type = 'account' AND entity_id = :id "
                    "  AND provider = 'intelliflo' AND link_type = 'primary'"
                ),
                {"id": account_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    # `data` is the picked plan's raw payload, stored verbatim — it already carries its own `id`/
    # `policyNumber`/`reference`, so (unlike the contact link, where `external_id` IS the IO
    # client id) there is no need to inject `external_id` back in: doing so here would overwrite
    # the real plan id with the policy number `link_account_to_io` now stores it as.
    record["io_link"] = (
        None
        if io_account_link_row is None
        else _io_plan_out(io_account_link_row["data"] or {}).model_dump(exclude={"raw"})
    )
    body = {**record, **metrics, "client": client}
    if (stated := stated_cash.get(account_id)) is not None:
        body["cash"] = stated[0]
    return body


@router.post("/contacts/{contact_uid}/morgan-lloyd", name="push-contact-to-morgan-lloyd")
async def push_contact_to_morgan_lloyd(
    contact_id: Annotated[int, Depends(tenant_contact_id)],
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, object]:
    """Create this client in Morgan Lloyd's Dynamics and record the link.

    A prerequisite for drawdown, which addresses the client by their Dynamics GUID. Idempotent:
    an existing link is returned untouched rather than creating a duplicate contact.

    422 lists the details Morgan Lloyd requires but we don't hold -- deliberately, rather than
    substituting placeholders into a real back-office record.
    """
    require_permission(principal, CONTACTS_SYNC_MORGAN_LLOYD)

    row = (
        (
            await session.execute(
                text(
                    # One projection, shared with `sync.py` — see `client_columns`. It used to be
                    # written out here as well, and the two had already drifted.
                    "SELECT " + morgan_lloyd.client_columns("c") + ", "
                    "       pl.external_id AS existing_link "
                    "FROM crm.contacts c "
                    "LEFT JOIN platform.links pl "
                    "  ON pl.entity_type = 'contact' AND pl.entity_id = c.id "
                    " AND pl.provider = 'morgan-lloyd' AND pl.link_type = 'primary' "
                    "WHERE c.id = :cid AND c.deleted_at IS NULL"
                ),
                {"cid": contact_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
    if row["existing_link"]:
        return {"external_id": row["existing_link"], "created": False}

    try:
        external_id = await morgan_lloyd.create_contact(dict(row))
    except ValueError as exc:  # missing required details
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except morgan_lloyd.MorganLloydNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except morgan_lloyd.MorganLloydUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    await session.execute(
        text(
            "INSERT INTO platform.links "
            "  (tenant_id, entity_type, entity_id, provider, link_type, external_id) "
            "VALUES (auth.tenant_id(), 'contact', :cid, 'morgan-lloyd', 'primary', :ext) "
            "ON CONFLICT (tenant_id, provider, entity_type, entity_id, link_type) "
            "DO UPDATE SET external_id = EXCLUDED.external_id"
        ),
        {"cid": contact_id, "ext": external_id},
    )
    return {"external_id": external_id, "created": True}


def _sync_pending(result: Any) -> JSONResponse:
    """A baton sync returns 202 with the run uid, the shape the Sync button already handles for a
    held run. The worker advances the run; a held run waits on its approval gate."""
    paused = result.run.state.type.value == "paused"
    return action_runs.pending_response(
        action_runs.Held(
            {
                "status": "awaiting_approval" if paused else "running",
                "run_uid": result.run.uid,
                "action_key": "client.sync",
            }
        )
    )


@router.post(
    "/contacts/{contact_uid}/sync-to-morgan-lloyd",
    name="sync-contact-to-morgan-lloyd",
    response_model=None,
)
async def sync_contact_to_morgan_lloyd(
    contact_id: Annotated[int, Depends(book_write_contact_id)],
    ctx: FirmContext = Depends(firm_book_write_session),
) -> dict[str, object] | JSONResponse:
    """Create whatever this client is missing in Morgan Lloyd, and record the links.

    Idempotent and resumable: the adviser, the contact, each pension account's scheme and policy,
    and that scheme's beneficiaries are created only when their identifier is not already
    recorded, so a second run reports `skipped` rather than duplicating a person in a real
    back-office system. Supersedes `POST /crm/contacts/{id}/morgan-lloyd`, which pushed the
    contact only.

    A per-step report comes back with 200 even when one account's scheme failed — the adviser
    needs to know which half worked. 422 lists details Morgan Lloyd requires and we do not hold.
    """
    require_permission(ctx.principal, CONTACTS_SYNC_MORGAN_LLOYD)
    # The 404 is the contact, not a missing tenant. Checking it here keeps a
    # bogus id from opening a run that the foreign key would refuse.
    found = (
        await ctx.session.execute(
            text("SELECT 1 FROM crm.contacts WHERE id = :id"), {"id": contact_id}
        )
    ).first()
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
    actor, _roles = await action_runs.caller_ids(ctx.session)
    try:
        result = await action_service.start_sync(
            ctx.session, tenant_id=ctx.tenant_id, contact_id=contact_id, requested_by=actor
        )
    except action_runs.ActionError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    return _sync_pending(result)


@router.post(
    "/accounts/{account_uid}/sync-to-morgan-lloyd",
    name="sync-account-to-morgan-lloyd",
    response_model=None,
)
async def sync_account_to_morgan_lloyd(
    account_id: Annotated[int, Depends(book_write_account_id)],
    ctx: FirmContext = Depends(firm_book_write_session),
) -> dict[str, object] | JSONResponse:
    """Push ONE SIPP to Morgan Lloyd: adviser, client, scheme, investment, beneficiaries.

    The account record's own door onto `sync_client`, which is the whole-client one. It is the same
    function with the second half narrowed, deliberately: the contact is still created first
    because a scheme is raised AGAINST a contact and Morgan Lloyd refuses one that does not exist,
    so "sync this account" always means "sync this client, then this account".

    FIVE THINGS ARE CREATED, and each can fail on its own: the client's adviser (kept on
    `auth.users.provider_refs`), the contact, the `MLSIPP/…` scheme every scoped action is
    addressed by, the policy number a valuation is filed against (`cam_createwrapper`, which the
    vendor titles Create Investment), and each beneficiary nomination on that scheme.

    Idempotent and resumable for the same reason the client-wide push is: each step re-reads its
    recorded identifier first and reports `skipped`, so a second press cannot put a second human
    being in a real back-office system. A per-step report comes back with 200 even when the policy
    failed and the scheme did not — a scheme with no policy is a usable record that simply cannot
    be valued yet, and collapsing the two would say the account never reached Morgan Lloyd.
    """
    require_permission(ctx.principal, CONTACTS_SYNC_MORGAN_LLOYD)
    contact_id = (
        await ctx.session.execute(
            text("SELECT contact_id FROM platform.accounts WHERE id = :a"),
            {"a": account_id},
        )
    ).scalar_one_or_none()
    # RLS has already decided the account is visible, so a NULL here is an account with no client
    # rather than one the caller may not see — which is a real state (the merge creates an account
    # before its contact resolves) and not something to push.
    if contact_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="This account has no client on the book, so there is nobody to create at "
            "Morgan Lloyd.",
        )
    actor, _roles = await action_runs.caller_ids(ctx.session)
    try:
        result = await action_service.start_sync(
            ctx.session,
            tenant_id=ctx.tenant_id,
            contact_id=int(contact_id),
            account_id=account_id,
            requested_by=actor,
        )
    except action_runs.ActionError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    return _sync_pending(result)


@router.post("/accounts/{account_uid}/drawdown-session", name="account-drawdown-session")
async def account_drawdown_session(
    account_id: Annotated[int, Depends(tenant_account_id)],
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, object]:
    """Mint a one-time DCube drawdown SSO link for a pension synced to Morgan Lloyd.

    RLS decides visibility (404 if the caller can't see the account). The journey is
    addressed by THIS account's ``platform.links`` scheme reference and the client's contact
    GUID — those rows are what ``Sync to Morgan Lloyd`` writes, so a SIPP whose shelf only
    names a custodian is still drawable once it has been synced. A client who already has an
    ML contact from another scheme must not start a session against this pot with
    ``scheme_ref=None``. 503 when credentials aren't configured, which is a deployment state,
    not a client error.

    ``cam_setmemberdetails`` also carries a CTC1 ``illustrationTemplate``. OpenAPI marks it
    optional and the mint succeeds without it; DCube loads it after SSO via
    ``DrawdownSetup/Illustration/IllustrationDocument``. Sex and date of birth are omitted when
    the client record has none — they are not invented. A 500 on the SSO URL itself is CTC
    ``SSOLogin`` (``docs/gotchas.md``), not a missing template.
    """
    # Minting a live drawdown SSO URL is an instruction on the account, not a read: without a
    # gate a client (or zero-permission staff) whose RLS scope covers the account could start a
    # drawdown at the vendor. Gated `instructions:write` — the money-movement family — since the
    # split out of `contacts:write` (this was the seam the split was planned at).
    require_permission(principal, INSTRUCTIONS_WRITE)

    row = (
        (
            await session.execute(
                text(
                    "SELECT a.id, a.uid, COALESCE(a.custodian::text, ("
                    "         SELECT l.provider::text FROM platform.links l "
                    "          WHERE l.tenant_id = a.tenant_id "
                    "            AND l.entity_type = 'account' AND l.entity_id = a.id "
                    "            AND l.link_type = 'primary' AND l.provider = ANY(:custodians) "
                    "          ORDER BY l.provider LIMIT 1)) AS custodian, "
                    "       a.value::float8 AS value, "
                    "       p.product_type, "
                    "       COALESCE(p.is_pension, false) AS is_pension, "
                    "       pl.external_id AS ml_contact_id, "
                    "       apl.external_id AS ml_scheme_ref, "
                    "       ppl.external_id AS ml_policy_ref, "
                    "       c.title, c.first_name, c.surname, c.dob, c.gender, "
                    "       c.marital_status, c.retirement_age, "
                    "       (SELECT v.cash_value::float8 FROM platform.valuations v "
                    "         WHERE v.account_id = a.id AND v.cash_value IS NOT NULL "
                    "         ORDER BY v.as_of_date DESC LIMIT 1) AS cash_value, "
                    "       (SELECT v.crystallised_value::float8 FROM platform.valuations v "
                    "         WHERE v.account_id = a.id AND v.crystallised_value IS NOT NULL "
                    "         ORDER BY v.as_of_date DESC LIMIT 1) AS crystallised_value "
                    "FROM platform.accounts a "
                    "LEFT JOIN platform.products p ON p.id = a.product_id "
                    "LEFT JOIN crm.contacts c ON c.id = a.contact_id AND c.deleted_at IS NULL "
                    "LEFT JOIN platform.links pl "
                    "  ON pl.entity_type = 'contact' AND pl.entity_id = a.contact_id "
                    " AND pl.provider = 'morgan-lloyd' AND pl.link_type = 'primary' "
                    "LEFT JOIN platform.links apl "
                    "  ON apl.entity_type = 'account' AND apl.entity_id = a.id "
                    " AND apl.provider = 'morgan-lloyd' AND apl.link_type = 'primary' "
                    "LEFT JOIN platform.links ppl "
                    "  ON ppl.entity_type = 'account' AND ppl.entity_id = a.id "
                    " AND ppl.provider = 'morgan-lloyd' AND ppl.link_type = 'policy' "
                    "WHERE a.id = :aid"
                ),
                {
                    "aid": account_id,
                    "custodians": list(providers.custodians()),
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    product_type = str(row["product_type"] or "").upper()
    # THE SHELF FIRST, then the wrapper set — the same order the account record uses to show
    # Drawdown. A ticked `is_pension` is a pension; the set decides only when the shelf is silent.
    is_pension = bool(row["is_pension"]) or product_type in _PENSION_PRODUCTS
    if not is_pension:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Drawdown applies to pension accounts only.",
        )
    if (row["custodian"] or "") != providers.morgan_lloyd_custodian():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Morgan Lloyd administers SEI SIPPs.",
        )
    # The scheme reference on this account is the fact, not a shelf tag. A real SIPP often
    # names only the custodian on product_platforms; refusing those 409'd every synced pot.
    ml_scheme_ref = row["ml_scheme_ref"]
    if not ml_scheme_ref:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This account is not linked to Morgan Lloyd, so a drawdown cannot be started.",
        )
    ml_contact_id = row["ml_contact_id"]
    if not ml_contact_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This client is not linked to Morgan Lloyd, so a drawdown cannot be started.",
        )

    try:
        return await morgan_lloyd.drawdown_session(
            str(ml_contact_id),
            **morgan_lloyd.drawdown_kwargs(
                dict(row),
                agent_name=settings.ctc_agent_name or "Graphene Platforms",
            ),
        )
    except morgan_lloyd.MorganLloydNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except morgan_lloyd.MorganLloydUnavailable as exc:
        # Vendor/Azure refused (e.g. Conditional Access blocking the source network). 502: the
        # request was fine, the upstream wasn't.
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post(
    "/accounts/{account_uid}/morgan-lloyd/valuation", name="account-morgan-lloyd-valuation"
)
async def account_morgan_lloyd_valuation(
    account_id: Annotated[int, Depends(tenant_account_id)],
    as_of: Annotated[date | None, Query(description="Valuation date; today if omitted.")] = None,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, object]:
    """File this account's valuation at Morgan Lloyd (``cam_createpolicyvaluation``).

    Closes the gap `services/morgan-lloyd/models/sink_valuations.py` documents about itself:
    nothing recorded a POLICY reference, so every ledger row on every nightly run read
    `skipped / no policy reference or fund splits`. The Morgan Lloyd sync records one now
    (`sync.LINK_POLICY`), and the splits come from the firm's own conformed slice.

    **The POLICY link, not the scheme link.** A valuation is filed against the policy number
    `cam_createwrapper` mints; a scheme reference in its place is refused as "not found".

    **The holdings read is the MONEY-CONTROL seam, not the display one.** `lake_holdings.
    firm_holdings` directly, so an unreadable warehouse RAISES — `_firm_read` degrades to the
    committed dev extract, and computing a client's real pension valuation from anonymised
    sample data and filing it at their scheme administrator is not a degraded answer, it is a
    wrong one that cannot be corrected (the vendor documents no update and denies us the read).

    409 without a policy or with nothing priceable, 502 when Dynamics refuses, 503 when the
    credentials or the firm GUID are unset.
    """
    # Filing a valuation is an instruction to the custodian about a client's money, not a read.
    # Gated with the money-movement family for the reason `account_drawdown_session` records.
    require_permission(principal, INSTRUCTIONS_WRITE)

    row = (
        (
            await session.execute(
                text(
                    "SELECT a.id, a.custodian::text AS custodian, p.product_type, "
                    "       t.uid AS tenant_uid, "
                    "       ppl.external_id AS ml_policy_ref, "
                    # The CUSTODIAN's own id for the account, whichever custodian that is: the
                    # valuation is computed from the positions in that custodian's slice, and this
                    # asked only Seccl, so a SEI-custodied SIPP answered "no custodian link".
                    "       spl.external_id AS custody_account_id, "
                    "       spl.provider     AS custody_provider "
                    "FROM platform.accounts a "
                    "JOIN auth.tenants t ON t.id = a.tenant_id "
                    "LEFT JOIN platform.products p ON p.id = a.product_id "
                    "LEFT JOIN platform.links ppl "
                    "  ON ppl.entity_type = 'account' AND ppl.entity_id = a.id "
                    " AND ppl.provider = 'morgan-lloyd' AND ppl.link_type = 'policy' "
                    # ONE ROW: `platform.links` is tenant-keyed (0066), so an account may hold a
                    # primary link per custodian and a plain join makes `.one_or_none()` below
                    # raise `MultipleResultsFound` — an unhandled 500 on the valuation push
                    # instead of a push. The account's own `custodian` decides which.
                    "LEFT JOIN LATERAL ( "
                    "  SELECT l.external_id, l.provider FROM platform.links l "
                    "   WHERE l.entity_type = 'account' AND l.entity_id = a.id "
                    "     AND l.provider = ANY(:custodians) AND l.link_type = 'primary' "
                    "   ORDER BY (l.provider = a.custodian) DESC NULLS LAST, l.provider "
                    "   LIMIT 1 "
                    ") spl ON true "
                    "WHERE a.id = :aid"
                ),
                {"aid": account_id, "custodians": list(providers.custodians())},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    if (row["custodian"] or row["custody_provider"] or "") != providers.morgan_lloyd_custodian():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Morgan Lloyd administers SEI SIPPs.",
        )
    if str(row["product_type"] or "").upper() not in _PENSION_PRODUCTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Morgan Lloyd holds policy valuations for pension accounts only.",
        )
    if not row["custody_account_id"]:
        # The positions relation is keyed by the custodian's account id. Without it there is
        # nothing to read, and that is a linkage gap rather than an empty account — said plainly,
        # because `NothingToValue` below would otherwise report it as "holds nothing".
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This account has no custodian link, so its holdings cannot be read and no "
                "valuation can be computed."
            ),
        )

    try:
        holdings = await lake_holdings.firm_holdings(
            str(row["custody_provider"]),
            str(row["tenant_uid"]),
            [str(row["custody_account_id"])],
        )
    except lake_holdings.HoldingsUnavailable as exc:
        # "We cannot tell" must never become a figure on a client's pension record.
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from (
            exc
        )

    try:
        return await ml_valuations.push_valuation(
            policy_reference=row["ml_policy_ref"], holdings=holdings, as_of=as_of
        )
    except (ml_valuations.NoPolicyRecorded, ml_valuations.NothingToValue) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except morgan_lloyd.MorganLloydNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except morgan_lloyd.MorganLloydUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


_ALLOC_PALETTE = [
    "var(--brand)",
    "#6366f1",
    "#22c55e",
    "#f59e0b",
    "#ec4899",
    "#06b6d4",
    "#a855f7",
    "#ef4444",
]


#: A movement kind, as words. TOTAL over `platform.transaction_type` — the conformed enum every
#: provider's rows are written in — so a canonical kind is always named and never bucketed into
#: a neighbour: an interest payment used to fall through to 'buy', and a client read "+£0.01
#: Buy" against their cash. Mirrors `ACTIVITY_LABELS` in pkgs/ui
#: (`components/investment/activity.ts`), which is in turn the vocabulary the ADVISER platform
#: puts on the button that raises one: a client reading "deposit" while their adviser says
#: "payment" is reading two firms. Kept in step by hand, like every cross-language map here.
_ACTIVITY_LABELS: dict[str, str] = {
    "deposit": "Payment",
    "withdrawal": "Withdrawal",
    "dividend": "Dividend",
    "interest": "Interest",
    "fee": "Fee",
    "tax": "Tax",
    "buy": "Buy",
    "sell": "Sell",
    "transfer_in": "Transfer in",
    "transfer_out": "Transfer out",
    "corporate_action": "Corporate action",
    "adjustment": "Adjustment",
}


def _activity_type(t: dict[str, object]) -> str:
    """Bucket a movement into one of the activity kinds (fallback 'buy').

    A canonical ``platform.transaction_type`` value on any of the three keys wins outright — the
    OLTP ``type`` column and the lake's conformed ``movement_type`` are both the enum. Only a
    custodian's own words (``source_type`` / ``sub_type``) go through the needle search.
    """
    keys = (t.get("movement_type"), t.get("type"), t.get("sub_type"))
    for key in keys:
        if isinstance(key, str) and key.lower() in _ACTIVITY_LABELS:
            return key.lower()
    s = " ".join(str(k or "") for k in keys).lower()
    for needle, kind in (
        ("div", "dividend"),
        ("interest", "interest"),
        ("fee", "fee"),
        ("charge", "fee"),
        ("withdraw", "withdrawal"),
        ("deposit", "deposit"),
        ("sell", "sell"),
        ("redem", "sell"),
        ("buy", "buy"),
        ("subscr", "buy"),
    ):
        if needle in s:
            return kind
    return "buy"


def _activity_detail(t: dict[str, object]) -> str | None:
    """The custodian's own words for the movement, when they say more than the kind does.

    "Platform Fee" under a row titled Fee, "Accumulation Dividend" under Dividend, "Employer"
    under Payment. Nothing when the subtype is absent, is a bare provider code (``20043``), or
    only repeats the kind — a detail column that echoes the title is the emptiness it was meant
    to fill. Sentence-cased, never raw (docs/design-language.md)."""
    raw = t.get("sub_type")
    words = str(raw).strip() if raw is not None else ""
    if not words or words.isdigit():
        return None
    detail = words[0].upper() + words[1:]
    kind = _activity_type(t)
    own = {_ACTIVITY_LABELS[kind].lower(), kind.replace("_", " ")}
    if detail.lower().replace("_", " ") in own:
        return None
    return detail


def _activity_description(t: dict[str, object]) -> str:
    """What the activity row is TITLED.

    The instrument where there is one; otherwise the movement said as words — which is the title
    for every payment, fee and dividend on the feed, since a cash movement names no instrument.
    Never the raw key: that put "deposit" and "transfer_in" in front of clients, directly under a
    subtitle naming the very same movement properly (docs/design-language.md)."""
    name = t.get("asset_name")
    if name:
        return str(name)
    if t.get("type") or t.get("movement_type"):
        return _ACTIVITY_LABELS[_activity_type(t)]
    return "Transaction"


def _signed(t: dict[str, object]) -> float | None:
    """The movement's signed cash figure, or None when the custodian never stated one.

    Prefers the listing's ``signed_amount`` (canonical type, both provider conventions). Lake
    home rows still arrive as a magnitude plus ``movement_type``/``type``, which is the same
    function applied to those keys — a regex over the haystack used to credit every Seccl
    withdrawal.
    """
    existing = t.get("signed_amount")
    if isinstance(existing, int | float | Decimal):
        return float(existing)
    amount = t.get("amount")
    if not isinstance(amount, int | float | Decimal):
        return None
    kind = t.get("type")
    if not isinstance(kind, str):
        kind = t.get("movement_type")
    return statement.signed_amount(kind if isinstance(kind, str) else None, float(amount))


def _num(x: object) -> float:
    """Coerce a lake/DB value (typed `object`) to a float, treating non-numerics as 0."""
    return float(x) if isinstance(x, int | float | Decimal) else 0.0


def _asset_class(h: dict[str, object]) -> str:
    """Bucket a holding into a portfolio asset class from its type/name (the lake's asset_type is
    coarse). Keeps the allocation donut meaningful (Equities / Fixed income / Alternatives / …).

    The classes are ``lake.holdings.classify_holding`` — the same rule as the summary SQL, whose
    cash test is ``instructions.is_cash_holding``. The cash a screen shows and the cash a
    withdrawal is checked against cannot drift apart."""
    asset_type = h.get("asset_type")
    asset_name = h.get("asset_name")
    return lake_holdings.classify_holding(
        None if asset_type is None else str(asset_type),
        None if asset_name is None else str(asset_name),
    )


# The client's cash ROW, deliberately narrower than ``is_cash_holding``. That one answers what a
# withdrawal may be paid from, and a money-market fund counts. This answers what the holdings list
# prints: uninvested currency is an amount, not "Pound Sterling, 693.644 units". A fund stays a
# fund. Kept in step with ``isCashHolding`` in apps/client/src/lib/holdings.ts.
_UNINVESTED_CASH_TYPES = frozenset({"CASH", "MRCXXXXXXX"})


def _is_uninvested_cash(h: dict[str, object]) -> bool:
    asset_type = str(h.get("asset_type") or "").strip().upper()
    if asset_type in _UNINVESTED_CASH_TYPES:
        return True
    isin = str(h.get("isin") or "").strip()
    name = str(h.get("asset_name") or "").strip().lower()
    return not isin and name == "cash"


def _client_holding(h: dict[str, object], wrapper: str) -> dict[str, object]:
    """One holding as the client portal prints it.

    Cash is the amount: no units, no price, no return. A fund keeps all three."""
    value = _num(h.get("value"))
    if _is_uninvested_cash(h):
        return {
            "name": "Cash",
            "ticker": None,
            "wrapper": wrapper,
            "units": 0,
            "price": 0,
            "value": value,
            "gainPct": None,
            "cash": True,
        }
    return {
        "name": h.get("asset_name") or "—",
        "ticker": h.get("isin"),
        "wrapper": wrapper,
        "units": _num(h.get("quantity")),
        "price": _num(h.get("price")),
        "value": value,
        "gainPct": _holding_return(h),
    }


def _holding_return(h: dict[str, object]) -> float | None:
    """A holding's return as a FRACTION (HoldingsTable multiplies by 100). Derived from
    growth/book_cost with the absurd-basis clamp — the lake's raw growth_pct is a percent that,
    fed to the formatter, reads as e.g. +6,303%."""
    cost = _num(h.get("book_cost"))
    if cost <= 0:
        return None
    pct = _num(h.get("growth")) / cost
    return None if abs(pct) > 10 else round(pct, 4)


async def _adviser_for_caller(
    session: AsyncSession, contact_id: int | None = None
) -> dict[str, object] | None:
    """The caller's assigned adviser (name / firm / email / phone), or ``None`` when they have
    no contact link or no adviser. Shared by ``home`` (the "Your adviser" card) and ``context``
    (the composed-page loader) so the two can never disagree about who the adviser is.

    A client can't read ``crm.client_advisers`` / other users under RLS (empty lineage), so
    resolve the adviser's public details via a short ``service_role`` lookup — keyed strictly
    on the caller's own ``auth.contact_scope()``, read from the RLS-bound session, so the
    elevated session only ever surfaces advisers of parties the caller may act for.
    ``None`` ⇒ the portal falls back to a placeholder.

    ``contact_id``, when given, narrows to that ONE linked contact rather than every contact
    this login can act for — the active-contact selector for a login linked to more than one
    client (docs/portal-customisation.md). It is ANDed on top of the real ``contact_ids``
    bound, never used alone, so an id outside the caller's actual scope can only narrow to
    nothing, never widen it."""
    contact_ids = (await session.execute(text("SELECT auth.contact_scope()"))).scalar()
    if not contact_ids:
        return None
    async with system_transaction() as sysx:
        adv_row = (
            (
                await sysx.execute(
                    text(
                        "SELECT u.name, u.email, t.name AS firm, "
                        # THE PROFILE (0118) — columns on `auth.users`, which is where a firm's own
                        # words about its staff belong: the WorkOS sync names its own columns and
                        # never touches these. All three adviser looks (`adviser-card`,
                        # `adviser-portrait`, `adviser-strip`) drew an initials disc, which was not
                        # a design choice but the only thing the schema could supply.
                        "       u.job_title, u.qualifications, u.bio, "
                        # The DIRECT LINE first, then the login's mobile. `u.mobile` is the number
                        # WorkOS sends security codes to, so it is the fallback rather than the
                        # answer — and `notifications/context.py` still says "no phone on
                        # auth.users", which has been wrong since 0001.
                        "       COALESCE(NULLIF(u.direct_phone, ''), u.mobile) AS phone, "
                        # The firm's own headshot, else the WorkOS picture — which has been
                        # mirrored into `u.avatar` all along and read by nothing.
                        "       COALESCE(NULLIF(u.photo_url, ''), u.avatar) AS photo_url "
                        "FROM crm.client_advisers ca "
                        "JOIN auth.users u ON u.id = ca.adviser_id "
                        "JOIN auth.tenants t ON t.id = ca.tenant_id "
                        "WHERE ca.contact_id = ANY(:cids) "
                        "  AND (CAST(:cid AS int) IS NULL OR ca.contact_id = CAST(:cid AS int)) "
                        # `role = 'adviser'` is unique per contact (0245), but not across a
                        # login's contacts: with no active contact the first by name is the
                        # same answer every time, not whichever row Postgres reaches first.
                        "  AND ca.role = 'adviser' "
                        "ORDER BY u.name NULLS LAST, ca.contact_id LIMIT 1"
                    ),
                    {"cids": list(contact_ids), "cid": contact_id},
                )
            )
            .mappings()
            .first()
        )
        if adv_row and adv_row["name"]:
            out: dict[str, object] = {
                "name": adv_row["name"],
                "firm": adv_row["firm"] or "",
                "email": adv_row["email"] or "",
                "phone": adv_row["phone"] or "",
            }
            # OMITTED when unset rather than sent as "", so the component can tell "this firm has
            # not filled it in" from "this adviser has no job title" — the card renders the line
            # only when there is one, and an empty string would draw an empty row.
            if adv_row["job_title"]:
                out["jobTitle"] = adv_row["job_title"]
            if adv_row["qualifications"]:
                out["qualifications"] = list(adv_row["qualifications"])
            if adv_row["bio"]:
                out["bio"] = adv_row["bio"]
            if adv_row["photo_url"]:
                out["photoUrl"] = adv_row["photo_url"]
            return out
    return None


@router.get("/contacts/mine", name="my-contacts")
async def my_contacts(session: AsyncSession = Depends(book_session)) -> dict[str, object]:
    """The linked contacts a client-portal login may act for, name-sorted, plus which one is
    the default when nothing has been explicitly selected yet — the active-contact switcher
    the client portal's nav bar renders (docs/portal-customisation.md). Almost every login is
    linked to exactly one contact, in which case ``contacts`` has one entry and the portal
    hides the switcher entirely; ``default_contact_id`` is that same entry's id.

    The default is the login's ``capacity='self'`` contact when one exists, then the
    alphabetically-first name. This matters for a JISA registered contact: claim scope correctly
    adds the child, but landing on the child by alphabet would hide the adult's own terms and
    identity work. The switcher still offers both people."""
    rows = (
        (
            await session.execute(
                text(
                    "SELECT c.id, c.uid, c.name, EXISTS ("
                    "  SELECT 1 FROM crm.contact_users cu "
                    "  WHERE cu.tenant_id = c.tenant_id AND cu.contact_id = c.id "
                    "    AND cu.user_id = auth.uid() AND cu.capacity = 'self'"
                    ") AS is_self "
                    "FROM crm.contacts c "
                    "WHERE c.id = ANY(auth.contact_scope()) AND c.deleted_at IS NULL "
                    "ORDER BY c.name NULLS LAST, c.id"
                )
            )
        )
        .mappings()
        .all()
    )
    # `uid` rides along with `id` because the two answer different questions: the serial is what
    # the `?contact_id=` slice filters take (they are named for what they take), and the uid is
    # what a PATH takes now that no URL carries a serial (docs/api.md -> "Addressing"). The
    # switcher selects by id; the portal's own record link needs the uid.
    # `is_self` is this login's own party (`auth.uid()` + capacity 'self'), not someone they
    # act for. The client profile used to rediscover that by comparing the session email to
    # `auth.users.email`. A WorkOS impersonation session keeps the impersonated user's id on
    # the token, so scope and onboarding still resolve, but the sealed session's `user.email`
    # need not equal the mirror — and the profile then said the firm held no record. The flag
    # is the same predicate `default_contact_id` already uses, published so a caller can tell
    # "this person" from "someone they may act for" without guessing by address.
    contacts = [
        {
            "id": r["id"],
            "uid": r["uid"],
            "name": r["name"] or "Unnamed client",
            "is_self": bool(r["is_self"]),
        }
        for r in rows
    ]
    return {
        "contacts": contacts,
        "default_contact_id": (
            next((r["id"] for r in rows if r["is_self"]), contacts[0]["id"]) if contacts else None
        ),
        "default_contact_uid": (
            next((r["uid"] for r in rows if r["is_self"]), contacts[0]["uid"]) if contacts else None
        ),
    }


# ``wrappers=auto`` flips /crm/home to its summary payload above this many VALUED accounts.
# The boundary is the UI's own: the composed client portal resolves its accounts block to the
# ``summary`` variant past VARIANT_THRESHOLDS.accountsCardsMax
# (pkgs/ui/src/lib/components/branding/variants.ts; this is accountsCardsMax + 1) — move one
# side and the other must move with it, or the portal renders a summary while the API still
# ships a sparkline per account, which is the exact payload this mode exists to shed.
_WRAPPERS_SUMMARY_OVER = 25

# A summarised home keeps only the biggest accounts as cards; everything else is represented
# in ``wrapper_groups``. Ten cards is a digest — three hundred is the wall the mode replaces.
_WRAPPERS_SUMMARY_TOP = 10


def _allowance_by_wrapper(
    accounts: list[dict[str, Any]],
) -> dict[tuple[object, str], tuple[float, float]]:
    """`(contact, wrapper) -> (used, limit)`, taking the MAX of each and never the sum.

    THE RULE THIS FUNCTION EXISTS FOR. An ISA allowance is a person's, per tax year — not an
    account's. SEI states it per client and Seccl per `clientProduct`, and the book repeats the
    figure onto every account of that client's wrapper, because the portal's card groups by wrapper
    and an account is what it has to hang the figure off.

    So summing is wrong in a way a client would notice: 60 of 921 client x wrapper pairs in the SEI
    drop hold more than one account, and adding a shared £5,000 subscription across two ISAs reports
    £10,000 used of a £40,000 limit. `max()` is right precisely because the value is a copy — every
    account of the group carries the same number, so the largest is that number, and an account the
    provider has said nothing about contributes nothing rather than dragging the group to zero.
    """
    out: dict[tuple[object, str], tuple[float, float]] = {}
    for account in accounts:
        used, limit = account.get("allowance_used"), account.get("allowance_limit")
        if used is None and limit is None:
            continue
        key = (account.get("contact_id"), str(account.get("product_type") or ""))
        seen_used, seen_limit = out.get(key, (0.0, 0.0))
        out[key] = (max(seen_used, _num(used)), max(seen_limit, _num(limit)))
    return out


def _allowance_card(
    allowance: dict[tuple[object, str], tuple[float, float]], account: Any
) -> dict[str, object]:
    """The two keys `wrapper-card.svelte` renders a subscription bar from, or neither.

    Omitted rather than zeroed when the provider has said nothing: the card renders no bar at all
    when `allowanceLimit` is absent, and a zero limit would draw a full one — reading as "you have
    used your whole allowance" to a client who has used none of it.
    """
    found = allowance.get((account.get("contact_id"), str(account.get("product_type") or "")))
    if not found or found[1] <= 0:
        return {}
    used, limit = found
    return {"allowanceUsed": round(used, 2), "allowanceLimit": round(limit, 2)}


@router.get("/home", name="home")
async def home(
    wrappers: Literal["full", "auto", "summary"] = "full",
    contact_id: int | None = None,
    session: AsyncSession = Depends(book_session),
) -> dict[str, object]:
    """Everything the client home needs, RLS-scoped and shaped for @graphene/ui ClientHome:
    totals, a portfolio value series, per-account cards (with sparklines), allocation by asset
    class, top holdings and recent activity. All derived from the lake for the caller's own
    accounts; empty structures when unlinked / no lake (the home then shows empty blocks).

    ``wrappers`` controls how the per-account cards come back. ``full`` (the default, and the
    frozen everyday shape — byte-identical to what this endpoint always returned) is a card per
    valued account, sparkline included. ``summary`` bounds the payload for the trust-scale book:
    only the top ``_WRAPPERS_SUMMARY_TOP`` cards by value, each WITHOUT its ``trend`` key (the
    sparkline series is the bloat), plus ``wrapper_groups`` (every valued account grouped by
    product type), ``wrappers_total`` (what the card count would have been) and
    ``wrappers_summarised: true`` — three keys that exist ONLY in summary mode. ``auto`` picks
    for the caller at ``_WRAPPERS_SUMMARY_OVER``, the same boundary the composed portal's
    accounts block flips at.

    Value stands on the SAME accounts as everywhere else (``_VALUED``): this query had no status
    predicate while ``/crm/holdings`` filtered on it, so a client with a closed linked account saw
    one total on their home and a different one on their holdings page, and the account still had a
    card here. And on the same FIGURE: each card's ``value`` and the ``totals.value`` are the OLTP
    ``platform.accounts.value`` the account record and the accounts listing show — not the sum of
    the lake's holdings rows, which this page used to prefer and which is a different number
    whenever the two snapshots are not the same day's. Activity deliberately keeps every linked
    account — a closed account's movements are real history (the same split as
    ``_visible_accounts``).

    **Provenance (additive, every mode):** ``as_of`` is the book's valuation date — the most
    recent of the valued accounts' own value timestamps (``_VALUED_AT``) and the last point of
    the value series; ``None`` for an unlinked/unvalued book. Each wrapper card carries its own
    ``value_as_of`` (kept in summary mode: it is one small key, not the trend bloat), and
    ``performance_period`` states the first/last date of the returned series (``None`` when
    there is no series) so the portal can label the chart's basis instead of implying
    "all time". All three are date-only ISO strings — the UI renders ``as at {date}``.

    ``contact_id``, when given, narrows to that ONE linked contact's accounts — the
    active-contact selector for a login linked to more than one client. ANDed on top of
    whatever RLS already allows, so an id outside the caller's real scope narrows to nothing
    rather than widening access."""
    accounts = (
        (
            await session.execute(
                text(
                    "SELECT a.id, a.uid, COALESCE(a.name, a.uid, 'Account') AS name, "  # noqa: S608 - only the fixed _VALUED/_VALUED_AT expressions and the CAST(:cid) narrowing are interpolated
                    "       a.value::float8 AS value, "
                    "       (" + _VALUED + ") AS valued, "
                    "       " + _VALUED_AT + " AS value_as_of, "
                    # FROM THE SHELF, like every sibling projection in this file. It read
                    # `a.data->>'seccl_product_type'`, a jsonb key written by nothing but the
                    # legacy `scripts/ingest_seccl.py` — `book_sink.entities._ACCOUNTS` stages
                    # `product_type` as a column and `data` as the conformed provider row — so on
                    # any pipeline-fed book every account keyed as the empty string.
                    # `_allowance_by_wrapper` takes `max()` per (contact, wrapper), so one bucket
                    # meant the max across DIFFERENT wrappers: an ISA's £5,000-of-£20,000 bar drawn
                    # on a GIA, which has no allowance at all, and the ISA's limit reported on a
                    # JISA whose own is £9,000.
                    "       p.product_type::text AS product_type, "
                    # The wrapper allowance, and `a.contact_id` because it is a (client, wrapper)
                    # fact rather than an account one — see `_allowance_by_wrapper`.
                    "       a.contact_id, "
                    "       a.allowance_used::float8  AS allowance_used, "
                    "       a.allowance_limit::float8 AS allowance_limit, "
                    "       pl.provider, pl.external_id, "
                    "       COALESCE(pl.seeded_demo, false) AS seeded_demo, "
                    # The firm whose slice holds these accounts: the lake reads are per firm
                    # (`tenant_{uid}.*`), so the uid travels with the ids.
                    "       t.uid AS tenant_uid "
                    "FROM platform.accounts a "
                    "JOIN auth.tenants t ON t.id = a.tenant_id "
                    "LEFT JOIN platform.products p ON p.id = a.product_id "
                    # ONE LINK ROW. `platform.links` is tenant-keyed (0066), so an account may
                    # hold a primary link per custodian; a plain JOIN then returns the account
                    # TWICE and the home page counts its value twice in every total.
                    # A journey-created account has no provider link until operations completes
                    # the custodian enrolment. Its controlled local_activated marker admits it to
                    # the portal meanwhile; arbitrary unlinked rows stay out, and the link narrows
                    # lake reads rather than deciding whether this onboarding account exists.
                    "LEFT JOIN LATERAL ( "
                    "  SELECT l.external_id, l.provider, "
                    "         COALESCE(l.data->>'seeded_demo' = 'true', false) AS seeded_demo "
                    "  FROM platform.links l "
                    "   WHERE l.entity_type = 'account' AND l.entity_id = a.id "
                    "     AND l.link_type = 'primary' "
                    "     AND (l.provider = ANY(:custodians) "
                    "          OR COALESCE(l.data->>'seeded_demo' = 'true', false)) "
                    "   ORDER BY (l.provider = a.custodian) DESC NULLS LAST, l.provider "
                    "   LIMIT 1 "
                    ") pl ON true "
                    "WHERE (pl.external_id IS NOT NULL "
                    "       OR a.data -> 'custodian_open' ->> 'local_activated' = 'true') "
                    "  AND (CAST(:cid AS int) IS NULL OR a.contact_id = CAST(:cid AS int) "
                    "    OR EXISTS (SELECT 1 FROM crm.account_contacts ac "
                    "       WHERE ac.tenant_id = a.tenant_id AND ac.account_id = a.id "
                    "         AND ac.contact_id = CAST(:cid AS int) "
                    "         AND ac.role = 'registered_contact'))"
                ),
                {"cid": contact_id, "custodians": list(providers.custodians())},
            )
        )
        .mappings()
        .all()
    )
    by_ext = {
        f"{a['provider']}:{a['external_id']}": dict(a)
        for a in accounts
        if a["provider"] is not None and a["external_id"] is not None
    }
    valued = [dict(a) for a in accounts if a["valued"]]

    # Fetch the three lake datasets concurrently (each on its own worker thread) rather than
    # serially — the home page waits on the slowest, not the sum.
    valued_by_ext = {key: account for key, account in by_ext.items() if account["valued"]}
    holdings, txns, series = await asyncio.gather(
        _firm_holdings(valued_by_ext),
        _firm_read(lake_holdings.firm_transactions, by_ext, limit=6),
        _firm_read(lake_holdings.firm_value_series, valued_by_ext),
    )

    # ---- per-account + portfolio value series (sparkline / performance chart) ----
    acc_series: dict[str, list[float]] = {}
    portfolio: dict[str, float] = {}
    for row in series:
        e, d, v = str(row["account_external_id"]), str(row["date"]), _num(row["value"])
        acc_series.setdefault(e, []).append(v)
        portfolio[d] = portfolio.get(d, 0.0) + v
    performance = [{"label": d, "value": round(portfolio[d], 2)} for d in sorted(portfolio)][-120:]

    # THE OLTP VALUE, like every other surface. `platform.accounts.value` is the provider's stated
    # balance (the pipeline refreshes it from the latest `platform.valuations` row on every run;
    # `docs/platform-model.md`), and it is what the account record, the accounts listing and the
    # adviser's own screens show. This page used to prefer the sum of the lake's holdings rows,
    # which is a different number whenever the two snapshots are not the same day's — a client
    # opened an account worth £2.24M on their home card and £452k on the page it linked to. The
    # lake still supplies what sits INSIDE the value (holdings, allocation, cash); it does not
    # restate the value. A locally-activated account has no lake presence and reads the same way.
    total_value = sum(_num(a["value"]) for a in valued)
    # Movement over the tracked value series, not the lake's book-cost gain — `_series_return`
    # carries the reasoning. One series point (or none) means no basis, so the helper's own
    # guard applies here too rather than this reading `performance[0]` unconditionally.
    total_g, total_pct = _series_return([_num(p["value"]) for p in performance])
    totals = {"value": round(total_value, 2), "gain": total_g, "gainPct": total_pct}

    # A (client, wrapper) fact, so it is resolved across the whole account list — including the
    # unvalued ones, because an ISA opened this year and not yet funded still has an allowance.
    # The per-row `dict()` stays: `RowMapping` does not satisfy `Mapping[str, Any]` for mypy, and
    # widening the signature to `Any` to save a copy would trade a real type for a micro-gain.
    allowance = _allowance_by_wrapper([dict(a) for a in accounts])

    cards: list[dict[str, object]] = []
    for a in valued:
        e = str(a["external_id"]) if a["external_id"] is not None else ""
        # Per-account return over its value series — `_series_return`, the same call the account
        # RECORD makes, so the listing and the record cannot show one account two returns.
        gain, pct = _series_return(acc_series.get(e, []))
        cards.append(
            {
                "type": a["product_type"] or "Account",
                "name": a["name"],
                "owner": "",
                "value": round(_num(a["value"]), 2),
                "value_as_of": _iso_date(a["value_as_of"]),
                "gain": gain,
                "gainPct": pct,
                "trend": [round(v, 2) for v in acc_series.get(e, [])[-60:]],
                # The UID, never the serial: this string is a URL, and the portal
                # route it lands on is `/accounts/[uid]`. `a.uid` is selected
                # beside the id for this one reason -- the COALESCE above uses it
                # as a display FALLBACK, which is not the same read.
                "href": f"/accounts/{a['uid']}",
                **_allowance_card(allowance, a),
            }
        )
    cards.sort(key=lambda w: _num(w["value"]), reverse=True)

    # ---- allocation by asset class ----
    # The lake asset_type is coarse ("Stock"), so bucket by fund name/type keywords into
    # meaningful classes — otherwise the donut is a single flat "Stock 100%".
    alloc: dict[str, float] = {}
    for h in holdings:
        alloc_key = _asset_class(h)
        alloc[alloc_key] = alloc.get(alloc_key, 0.0) + _num(h.get("value"))
    allocations = [
        {"label": k, "value": round(v, 2), "color": _ALLOC_PALETTE[i % len(_ALLOC_PALETTE)]}
        for i, (k, v) in enumerate(sorted(alloc.items(), key=lambda kv: kv[1], reverse=True))
    ]

    top_holdings = [
        _client_holding(h, str(by_ext.get(_account_key(h) or "", {}).get("name") or ""))
        for h in sorted(holdings, key=lambda x: _num(x.get("value")), reverse=True)[:5]
    ]

    activity = [
        {
            "date": t.get("date"),
            "type": _activity_type(t),
            "description": _activity_description(t),
            "amount": _signed(t),
            "wrapper": by_ext.get(_account_key(t) or "", {}).get("name") or "",
            "currency": t.get("currency") or "GBP",
            # The row's detail, for a feed wide enough to show it (pkgs/ui `activity-list`):
            # the custodian's words, and the units and price of a trade. The same keys the
            # portal's own loader projects from /crm/transactions, so the home slice and the
            # activity page draw one row the same way.
            "detail": _activity_detail(t),
            "quantity": _num(t.get("quantity")) if t.get("quantity") is not None else None,
            "price": _num(t.get("price")) if t.get("price") is not None else None,
            "isin": t.get("isin") or None,
        }
        for t in txns
    ]

    # The client's adviser (for the home "Your adviser" card) — the shared service_role
    # lookup, keyed on the caller's own contact scope (see _adviser_for_caller).
    adviser = await _adviser_for_caller(session, contact_id)

    # ---- provenance: the book's valuation date + the chart's basis ----
    # `as_of` is the most recent date across what the figures on this page actually stand on:
    # the VALUED accounts' own value timestamps and the last point of the value series. Series
    # labels are already ISO dates (str(row["date"]) above), so lexical max is date max.
    stamped = [d for d in (_iso_date(a["value_as_of"]) for a in valued) if d is not None]
    if performance:
        stamped.append(str(performance[-1]["label"]))
    as_of = max(stamped) if stamped else None
    performance_period = (
        {"from": str(performance[0]["label"]), "to": str(performance[-1]["label"])}
        if performance
        else None
    )

    out: dict[str, object] = {
        "as_of": as_of,
        "totals": totals,
        "performance": performance,
        "performance_period": performance_period,
        "wrappers": cards,
        "allocations": allocations,
        "holdings": top_holdings,
        "activity": activity,
        "adviser": adviser,
    }
    if wrappers == "full" or (wrappers == "auto" and len(cards) <= _WRAPPERS_SUMMARY_OVER):
        return out

    # Summary mode. Group EVERY valued account by product type (the cards' own 'Account'
    # fallback, and the same per-account value figure the cards carry) so the digest reconciles
    # exactly with what full mode would have shown: counts sum to `wrappers_total`, values sum
    # to the book. Sorted by value desc, like the cards.
    groups: dict[str, list[float]] = {}
    for card in cards:
        groups.setdefault(str(card["type"]), []).append(_num(card["value"]))
    out["wrapper_groups"] = [
        {"product_type": t, "count": len(vals), "value": round(sum(vals), 2)}
        for t, vals in sorted(groups.items(), key=lambda kv: sum(kv[1]), reverse=True)
    ]
    out["wrappers_total"] = len(cards)
    out["wrappers_summarised"] = True
    # Only the biggest accounts keep a card, and none keeps a sparkline: `trend` is the payload
    # bloat this mode exists to shed, so the key is OMITTED, not emptied — the UI treats its
    # presence as "render a sparkline".
    out["wrappers"] = [
        {k: v for k, v in card.items() if k != "trend"} for card in cards[:_WRAPPERS_SUMMARY_TOP]
    ]
    return out


@router.get("/context", name="context")
async def context(
    contact_id: int | None = None, session: AsyncSession = Depends(book_session)
) -> dict[str, object]:
    """Who the signed-in client is and how big their book is — the client portal's composed
    pages call this from their server loader on EVERY page load, so it must stay cheap: two
    small OLTP round-trips plus the shared adviser lookup, never the lake.

    The response shape is FROZEN (the SvelteKit loader and a projection test both pin the
    exact key set): contact_id / type / service_type / account_count / valued_account_count /
    adviser / as_of. ``type`` ('individual' | 'organisation') keys the per-audience page
    layouts in the branding pages contract (app/branding.py); the counts let volume-adaptive
    widgets pick a rendering before fetching data.

    ``as_of`` is the book's valuation date (date-only ISO, ``None`` for an unvalued book),
    derived from OLTP ``platform.accounts`` alone — the max ``_VALUED_AT`` over the valued
    accounts. Deliberately NOT the lake series component ``/crm/home`` folds into its own
    ``as_of`` (this endpoint must never read the lake), so it can lag home's stamp when the
    series' last point is newer than every account's value timestamp.

    ``contact_id`` is the caller's own primary party: an explicit ``?contact_id=`` override
    (the active-contact selector, when it's a member of ``auth.contact_scope()`` — otherwise
    ignored) if given; else the ``crm.contact_users`` link with ``capacity = 'self'`` if one
    exists (a delegate/POA login is linked to someone ELSE's contact too, and their own must
    win); else any contact_users link; else the first id in ``auth.contact_scope()``. All
    ``None`` for an unlinked principal (staff) — the counts then reflect whatever RLS lets
    them see, which for read_all staff is the whole book.

    The response SHAPE is unchanged by the override (same frozen key set) — only which contact
    ``contact_id``/``adviser`` resolve to can differ, and only when the override is honoured.
    """
    who = (
        (
            await session.execute(
                text(
                    # contact_users is readable under RLS for the caller's own rows
                    # (user_id = auth.uid()); the LEFT JOIN drops type/service_type to NULL
                    # when the contact row is archived (deleted_at) or invisible. `override`
                    # is exposed SEPARATELY from the fully-resolved `contact_id` (rather than
                    # folded into one COALESCE) because the two must drive different things
                    # below: a staff caller's `contact_id` still resolves to some arbitrary
                    # book contact via the existing fallback chain (unchanged), and narrowing
                    # `counts`/`adviser` by THAT would wrongly shrink a read_all staff
                    # member's whole-book figures to one client's. Only an EXPLICIT, validated
                    # override should ever narrow anything.
                    "WITH override AS ("
                    "  SELECT CAST(:cid AS int) AS contact_id"
                    "  WHERE CAST(:cid AS int) = ANY(auth.contact_scope())"
                    "), me AS ("
                    "  SELECT COALESCE("
                    "    (SELECT contact_id FROM override),"
                    "    (SELECT cu.contact_id FROM crm.contact_users cu"
                    "      WHERE cu.user_id = auth.uid()"
                    "      ORDER BY (cu.capacity = 'self') DESC, cu.contact_id"
                    "      LIMIT 1),"
                    "    (auth.contact_scope())[1]) AS contact_id"
                    ") "
                    "SELECT me.contact_id, "
                    "       (SELECT contact_id FROM override) AS override_contact_id, "
                    "       c.type::text AS type, c.service_type::text AS service_type "
                    "FROM me LEFT JOIN crm.contacts c "
                    "  ON c.id = me.contact_id AND c.deleted_at IS NULL"
                ),
                {"cid": contact_id},
            )
        )
        .mappings()
        .one()
    )
    override = who["override_contact_id"]
    # Counts stand on the SAME basis as every other AUM-adjacent figure (_VALUED), so context
    # and home can never disagree about how many accounts "carry value". Narrowed ONLY by an
    # explicit, validated override — never the full-resolution `contact_id` (see above).
    counts = (
        (
            await session.execute(
                text(
                    "SELECT count(*) AS account_count, "  # noqa: S608 - only the fixed _VALUED/_VALUED_AT expressions and the CAST(:cid) narrowing are interpolated
                    "       count(*) FILTER (WHERE " + _VALUED + ") AS valued_account_count, "
                    "       max(" + _VALUED_AT + ") FILTER (WHERE " + _VALUED + ") "
                    "         AS values_as_of "
                    "FROM platform.accounts a "
                    "WHERE (CAST(:cid AS int) IS NULL OR a.contact_id = CAST(:cid AS int) "
                    "  OR EXISTS (SELECT 1 FROM crm.account_contacts ac "
                    "     WHERE ac.tenant_id = a.tenant_id AND ac.account_id = a.id "
                    "       AND ac.contact_id = CAST(:cid AS int) "
                    "       AND ac.role = 'registered_contact'))"
                ),
                {"cid": override},
            )
        )
        .mappings()
        .one()
    )
    return {
        "contact_id": who["contact_id"],
        "type": who["type"],
        "service_type": who["service_type"],
        "account_count": counts["account_count"],
        "valued_account_count": counts["valued_account_count"],
        "adviser": await _adviser_for_caller(session, override),
        "as_of": _iso_date(counts["values_as_of"]),
    }


@router.get("/contacts/{contact_uid}/shell", name="get-contact-shell")
async def get_contact_shell(
    contact_id: Annotated[int, Depends(book_contact_id)],
    session: AsyncSession = Depends(book_session),
) -> dict[str, object]:
    """The client-record layout's lean payload: identity, accounts, team, KYC, unread.

    Overview and Details fetch the fat record themselves. This is what every other tab
    (documents, chat, transactions, people) needs to address the person without notes,
    bank, FATCA, relationships or lake metrics.

    `dob`, `retirement_age` and `marital_status` ride along: the illustration composer under the
    record reads its client off this payload and asks for whichever of the three is missing, so
    leaving them off meant it asked for all three of every client, recorded or not."""
    contact = (
        (
            await session.execute(
                text(
                    "SELECT id, uid, type, service_type, sub_type, enabled, provider_status, "  # noqa: S608
                    "       currency, deleted_at, title, first_name, middle_name, "
                    "       surname, name, email, dob::text AS dob, retirement_age, "
                    "       marital_status, "
                    "       " + phone_expr("mobile") + " AS mobile, "
                    "       " + phone_expr("phone") + " AS phone, "
                    "       is_vulnerable, vulnerability_reasons, "
                    "       vulnerability_characteristics, "
                    "       vulnerability_is_temporary, vulnerability_recorded_at "
                    "FROM crm.contacts WHERE id = :id"
                ),
                {"id": contact_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
    advisers = (
        (
            await session.execute(
                text(
                    "SELECT ca.adviser_id, ca.role, u.name, u.email, "
                    "       u.job_title, u.is_adviser "
                    "FROM crm.client_advisers ca LEFT JOIN auth.users u ON u.id = ca.adviser_id "
                    "WHERE ca.contact_id = :id "
                    "ORDER BY (ca.role = 'adviser') DESC, u.name NULLS LAST"
                ),
                {"id": contact_id},
            )
        )
        .mappings()
        .all()
    )
    users = (
        (
            await session.execute(
                text(
                    "SELECT cu.user_id, cu.capacity, u.name, u.email "
                    "FROM crm.contact_users cu LEFT JOIN auth.users u ON u.id = cu.user_id "
                    "WHERE cu.contact_id = :id ORDER BY u.name NULLS LAST"
                ),
                {"id": contact_id},
            )
        )
        .mappings()
        .all()
    )
    accounts = (
        (
            await session.execute(
                text(
                    "SELECT a.id, a.uid, a.name, a.status, a.value::float8 AS value, a.currency, "
                    "       p.product_type, COALESCE(p.is_pension, false) AS is_pension "
                    "FROM platform.accounts a "
                    "LEFT JOIN platform.products p ON p.id = a.product_id "
                    "WHERE a.contact_id = :id ORDER BY a.id"
                ),
                {"id": contact_id},
            )
        )
        .mappings()
        .all()
    )
    kyc = (
        (
            await session.execute(
                text(
                    "SELECT DISTINCT ON (k.kind) k.kind, k.status "
                    "FROM crm.kyc_checks k WHERE k.contact_id = :id "
                    "ORDER BY k.kind, k.updated_at DESC"
                ),
                {"id": contact_id},
            )
        )
        .mappings()
        .all()
    )
    chats_unread = bool(
        (
            await session.execute(
                text(
                    "SELECT EXISTS ("
                    "  SELECT 1 FROM messaging.conversations c "
                    "  JOIN messaging.messages m ON m.conversation_id = c.id "
                    " WHERE c.contact_id = :id AND m.sender_kind = 'client' "
                    "   AND m.created_at > COALESCE(c.staff_last_read_at, '-infinity'::timestamptz)"
                    ")"
                ),
                {"id": contact_id},
            )
        ).scalar_one()
    )
    return {
        "contact": dict(contact),
        "advisers": [dict(r) for r in advisers],
        "users": [dict(r) for r in users],
        "accounts": [dict(r) for r in accounts],
        "kyc": [dict(r) for r in kyc],
        "chats_unread": chats_unread,
        "notes": [],
        "notes_total": 0,
        "relationships": [],
        "value_source": None,
    }


@router.get("/contacts/{contact_uid}", name="get-contact")
async def get_contact(
    contact_id: Annotated[int, Depends(book_contact_id)],
    session: AsyncSession = Depends(book_session),
) -> dict[str, object]:
    """One client with their assigned advisers, linked logins and accounts. RLS-scoped:
    a 404 means the row is either absent or not visible to this caller (fail-closed).
    Closed clients (``deleted_at`` set) still resolve: Close is a status on the header,
    and the record has to load for that badge — and for Reopen — to exist."""
    contact = (
        (
            await session.execute(
                text(
                    # dob + nationality are here because the illustration flow shows (and sends)
                    # them: a pension projection is driven by date of birth, so an adviser needs
                    # to see the value that will be used before generating.
                    # bank_account is the client's own banking instruction (one set per person,
                    # used by every account they hold) — the platform record edits it, the account
                    # detail page renders it. RLS keeps it to this firm's staff and the client
                    # themselves.
                    # The vulnerability block is read on every load, not behind a tab: it
                    # changes how an adviser should conduct the whole conversation, so the
                    # profile header states it (0054).
                    # noqa on the next line: the only interpolation is `phone_expr`, a literal
                    # built in app.contacts from a column name of ours — no request value reaches
                    # it. Same posture as its other callers.
                    "SELECT id, uid, type, service_type, sub_type, enabled, provider_status, "  # noqa: S608
                    "       currency, deleted_at, title, "
                    "       first_name, middle_name, surname, name, email, "
                    # Both numbers are read defensively: Seccl publishes a number as a
                    # `{"number","isMobile","locale"}` object (app.contacts.phone_expr).
                    "       " + phone_expr("mobile") + " AS mobile, "
                    "       " + phone_expr("phone") + " AS phone, "
                    "       address, bank_account, dob, gender, nationality, "
                    "       marital_status, employment_status, retirement_age, "
                    "       source_of_wealth, flexi_access_taken, "
                    # 0116 — the identity answers onboarding has always collected and had
                    # nowhere to put, so they folded into `data` and reached a screen only as a
                    # dotted path under the generic overflow flattener. `occupation` is NOT
                    # `employment_status`: one is what they do, the other whether they work.
                    "       occupation, occupation_detail, country_of_birth, town_of_birth, "
                    "       source_of_funds, source_of_funds_detail, tax_relief, "
                    # Employment (0126 + 0051). `relevant_uk_earnings` has existed since 0051 and
                    # was never projected here, so the record could not show the figure the
                    # relievable-limit check runs on -- an adviser had no way to see, let alone
                    # correct, the input that decides whether a contribution is allowable.
                    # `::float8` for the same reason every other money column here is cast: the
                    # JSON encoder has no numeric, and a Decimal reaches the portal as a string.
                    "       employer_name, relevant_uk_earnings::float8 AS relevant_uk_earnings, "
                    # 0127 — the restructured record's sections. Every one of these is READ even
                    # when null: a section states its own fields whether or not they are filled in,
                    # because a field nobody can see is a field nobody fills in. That is how
                    # `relevant_uk_earnings` went five migrations with no editor while the HMRC
                    # relievable-limit check quietly ran on the £3,600 floor.
                    "       employer_contributions, employer_registration_number, "
                    "       employer_address, employer_bank_account, flexi_access_date, "
                    "       source_of_wealth_detail, "
                    # 0121's FATCA answers, projected for the first time — the Personal section
                    # asks the US-person question and could not previously show the answer. The
                    # passport pair is 0129's too and had NO reader at all; SEI enrolment asks for
                    # it and publishes it back, so the round trip exists and the record now shows
                    # what it holds.
                    "       us_person, fatca_status, fatca_certified_at, "
                    # The signed W-9 / W-8 behind `fatca_status` (0261). An archived file has left
                    # the client's documents, so the record does not offer it as the form held.
                    "       (SELECT jsonb_build_object('uid', fd.uid, 'name', fd.name) "
                    "          FROM documents.documents fd "
                    "         WHERE fd.id = crm.contacts.fatca_document_id "
                    "           AND fd.status <> 'archived') AS fatca_document, "
                    "       passport_number, passport_country, "
                    # Country NAMES are filled in below from the process cache of `ref.countries`.
                    # The columns store ISO codes; the record shows the name beside the code.
                    # Doing it here, rather than joining the reference table on every read, is
                    # the same answer the portals would get from `GET /ref/countries`.
                    # Written by CORE_MAPPING on every onboarding since the fact-find existed, and
                    # never once read back — absent here, so absent from every screen.
                    "       tax_residency, national_identifier, national_identifier_type, "
                    # The fact-find's overflow (offshore KYC, employer contributions): real answers
                    # with no column of their own. Returned so a record can SHOW what was gathered
                    # instead of it being write-only.
                    "       data, "
                    "       is_vulnerable, vulnerability_reasons, "
                    "       vulnerability_characteristics, "
                    "       vulnerability_is_temporary, vulnerability_recorded_at, "
                    "       vulnerability_notes "
                    "FROM crm.contacts WHERE id = :id"
                ),
                {"id": contact_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")

    advisers = (
        (
            await session.execute(
                text(
                    "SELECT ca.adviser_id, ca.role, u.name, u.email, "
                    # The expanded Team list on the client record: job title when the firm has
                    # filled one, and whether they are marked as an adviser (the tag). Everyone
                    # on this book already passed the assign gate, but the flag is still a
                    # property of the PERSON, not of the assignment.
                    "       u.job_title, u.is_adviser "
                    "FROM crm.client_advisers ca LEFT JOIN auth.users u ON u.id = ca.adviser_id "
                    "WHERE ca.contact_id = :id "
                    "ORDER BY (ca.role = 'adviser') DESC, u.name NULLS LAST"
                ),
                {"id": contact_id},
            )
        )
        .mappings()
        .all()
    )
    users = (
        (
            await session.execute(
                text(
                    "SELECT cu.user_id, cu.capacity, u.name, u.email "
                    "FROM crm.contact_users cu LEFT JOIN auth.users u ON u.id = cu.user_id "
                    "WHERE cu.contact_id = :id ORDER BY u.name NULLS LAST"
                ),
                {"id": contact_id},
            )
        )
        .mappings()
        .all()
    )
    # `value_as_of` is when the value was written, not when the row was last touched.
    # Fee package + risk level + currency are stored and were never surfaced.
    accounts = (
        (
            await session.execute(
                text(
                    "SELECT a.id, a.uid, a.name, a.status, a.value::float8 AS value, "  # noqa: S608 - only fixed _VALUED_AT is interpolated
                    "       " + _VALUED_AT + " AS value_as_of, a.currency, a.risk_level, "
                    "       a.custody, a.opened_at, "
                    "       p.name AS product_name, p.code AS product_code, p.product_type, "
                    # WHETHER THIS IS A PENSION, from the shelf rather than from a word. The
                    # client record's Pension and Employer sections gate on it: the wrapper set
                    # they used to test has nine copies across the estate, three of which omit
                    # TPPENSION, where `is_pension` is one column a firm can see and correct.
                    "       COALESCE(p.is_pension, false) AS is_pension, "
                    "       a.model_id, m.name AS model_name, "
                    # The same expression the listing and the detail publish — the client
                    # record shows these accounts in a table beside them, and a third rule
                    # for what "discretionary" means is how two screens come to disagree
                    # about one account.
                    "       NULLIF(a.investment_capacity, '') AS service_type, "
                    # The withdrawal dialog defaults to the method saved here, and SEI's rail
                    # fee applies only when this account is SEI-custodied.
                    "       a.bank_accounts, a.custodian, "
                    "       COALESCE((SELECT jsonb_agg(jsonb_build_object("
                    "           'id', fp.id, 'name', fp.name, 'sort_order', afp.sort_order"
                    "         ) ORDER BY afp.sort_order, fp.name, fp.id) "
                    "         FROM platform.account_fee_packages afp "
                    "         JOIN platform.fee_packages fp "
                    "           ON fp.id = afp.fee_package_id AND fp.tenant_id = afp.tenant_id "
                    "        WHERE afp.account_id = a.id AND afp.tenant_id = a.tenant_id"
                    "       ), '[]'::jsonb) AS fee_packages, "
                    # The account's STANDING arrangements, carried onto the client record because a
                    # regular payment is something the CLIENT has set up — they think of it as "my
                    # £100 a month", not as a property of one of their wrappers. The record's panel
                    # therefore lists them across every account, which needs them here rather than a
                    # fetch per account.
                    "       a.payments, a.withdrawals "
                    "FROM platform.accounts a "
                    "LEFT JOIN platform.products p ON p.id = a.product_id "
                    "LEFT JOIN platform.models m ON m.id = a.model_id "
                    "WHERE a.contact_id = :id ORDER BY a.id"
                ),
                {"id": contact_id},
            )
        )
        .mappings()
        .all()
    )
    by_account, _ = await _lake_metrics(await _visible_accounts(session, contact_id=contact_id))
    stated_cash = await instructions_mod.stated_custodian_cash(
        session, [int(row["id"]) for row in accounts]
    )
    shaped_accounts = []
    for row in accounts:
        account = {**dict(row), **by_account.get(int(row["id"]), _NO_METRICS)}
        if (stated := stated_cash.get(int(row["id"]))) is not None:
            account["cash"] = stated[0]
        account["bank_accounts"] = bank_accounts_mod.stored_entries(account.get("bank_accounts"))
        account["withdrawal_fees"] = _withdrawal_fees(account.get("custodian"))
        account["payments"] = regular_movements_mod.standing_movements_of(account.get("payments"))
        account["withdrawals"] = regular_movements_mod.standing_movements_of(
            account.get("withdrawals")
        )
        shaped_accounts.append(account)
    # Newest 50 notes + the true total, so the tab can say what it is not showing (there is no
    # notes endpoint to page through yet).
    notes = (
        (
            await session.execute(
                text(
                    # `uid` as well as `id`: a note is ADDRESSABLE now (its author may delete it),
                    # and a portal URL never carries a serial — `docs/api.md` -> "Addressing".
                    "SELECT n.id, n.uid, n.body, n.created_at, u.name AS author, "
                    "       count(*) OVER () AS total "
                    "FROM crm.contact_notes n LEFT JOIN auth.users u ON u.id = n.author_id "
                    "WHERE n.contact_id = :id ORDER BY n.created_at DESC LIMIT 50"
                ),
                {"id": contact_id},
            )
        )
        .mappings()
        .all()
    )
    # KYC is a property of the person (0026_kyc_checks): the latest attempt per kind. Written by
    # the flow engine / provider webhook; read-only here, and absent until a flow has run.
    kyc = (
        (
            await session.execute(
                text(
                    "SELECT DISTINCT ON (k.kind) k.kind, k.status, k.provider, "
                    "       k.verified_at, k.valid_until, k.updated_at "
                    "FROM crm.kyc_checks k WHERE k.contact_id = :id "
                    "ORDER BY k.kind, k.updated_at DESC"
                ),
                {"id": contact_id},
            )
        )
        .mappings()
        .all()
    )
    # Related parties, from whichever end the link was recorded. The join to crm.contacts is
    # RLS-scoped, so a relationship to a party outside the caller's book simply isn't returned.
    relationships = (
        (
            await session.execute(
                text(
                    "SELECT r.id, r.relationship_type, "
                    "       (r.from_contact_id = :id) AS outbound, "
                    "       o.id AS other_contact_id, o.type AS other_type, o.name AS other_name, "
                    "       o.title AS other_title, o.first_name AS other_first_name, "
                    "       o.surname AS other_surname, o.uid AS other_uid "
                    "FROM crm.relationships r "
                    "JOIN crm.contacts o ON o.id = CASE WHEN r.from_contact_id = :id "
                    "       THEN r.to_contact_id ELSE r.from_contact_id END "
                    "WHERE (r.from_contact_id = :id OR r.to_contact_id = :id) "
                    "  AND o.deleted_at IS NULL "
                    "ORDER BY r.relationship_type, o.surname NULLS LAST, o.id"
                ),
                {"id": contact_id},
            )
        )
        .mappings()
        .all()
    )
    # The "Link to IO" card's persisted state — an ordinary platform.links row, read back the
    # same way `kyc`/`term_acceptances` already ride along on this one load rather than a
    # separate fetch the card would have to make itself.
    io_link_row = (
        (
            await session.execute(
                text(
                    "SELECT external_id, data FROM platform.links "
                    "WHERE entity_type = 'contact' AND entity_id = :id "
                    "  AND provider = 'intelliflo' AND link_type = 'primary'"
                ),
                {"id": contact_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    io_link = (
        None
        if io_link_row is None
        else _io_client_out(
            {**(io_link_row["data"] or {}), "id": io_link_row["external_id"]}
        ).model_dump(exclude={"raw"})
    )
    # Names from the cached catalogue, in the stored order. An unknown code is omitted,
    # which is what the old join did; the code itself still travels on the row.
    countries = await country_catalogue()
    record = dict(contact)
    record["country_of_birth_name"] = countries.name_of(record.get("country_of_birth"))
    record["nationality_names"] = countries.names_of(record.get("nationality"))
    record["tax_residency_names"] = countries.names_of(record.get("tax_residency"))
    return {
        "contact": record,
        "advisers": [dict(r) for r in advisers],
        "users": [dict(r) for r in users],
        "accounts": shaped_accounts,
        "notes": [{k: v for k, v in r.items() if k != "total"} for r in notes],
        "notes_total": int(notes[0]["total"]) if notes else 0,
        "kyc": [dict(r) for r in kyc],
        "term_acceptances": await terms_svc.list_contact_acceptances(session, contact_id),
        "relationships": [dict(r) for r in relationships],
        "value_source": await _value_source(session),
        "io_link": io_link,
    }


#: The firm's screening vocabulary, from a portal kyc row (``crm.kyc_checks.status``). An expired
#: pass needs a new check, which reads as not started.
_FIRM_KYC_STATUS: dict[str, str] = {
    "pending": "pending",
    "passed": "passed",
    "referred": "referred",
    "failed": "failed",
    "expired": "not_started",
}

#: The same vocabulary from a ComplyCube check (``compliance.checks.status``). A check ComplyCube
#: could not run is not a verdict: it reads as pending, as the kyc row does. A ``refer`` reads as
#: referred only once it is decided (see ``get_contact_screening``).
_FIRM_CHECK_STATUS: dict[str, str] = {
    "pending": "pending",
    "clear": "passed",
    "refer": "referred",
    "rejected": "failed",
    "failed": "pending",
}

_SCREENING_KINDS = ("identity", "aml")


@router.get("/contacts/{contact_uid}/screening", name="get-contact-screening")
async def get_contact_screening(
    contact_id: Annotated[int, Depends(book_contact_id)],
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(book_session),
) -> dict[str, object]:
    """A firm's read-only window onto Graphene's screening of one of its clients: one status and
    one date per kind, from the latest kyc row or ComplyCube check, whichever moved last, and for
    AML whether ongoing monitoring covers the latest screen (``monitored``; null before any screen,
    false when that screen is a technical failure — the flag was stored at the start, and the
    poller does not ask about a screen that never ran).

    A portal check is read from its kyc row only, which moves when Graphene decides. A check with no
    kyc row (a partner check) reads ``refer`` as pending until it is decided: "referred" is the
    outcome of Graphene's review, and a possible match still under review must not reach the firm
    as one.

    AML is Graphene's regulatory responsibility as the platform provider, and telling a client they
    are under suspicion is tipping off (POCA 2002 s.333A). So a firm sees where a check stands,
    never what it found: no result, outcome, matches, match counts, monitoring alerts or decision
    comments. That monitoring is on or off is not an alert, and is shown. ``compliance.checks``
    grants a firm its status columns only (0221), so the query could not read more if it tried;
    the monitoring flag alone is read elevated.

    There is no companion write endpoint anywhere on the platform surface: adjudication requires
    ``aml:adjudicate``, an operations-portal permission no firm-scoped role can hold.

    Scoping is RLS (``tenant_session`` + the ``crm`` / ``compliance`` read policies), so a firm
    reaches only its own lineage. A client who has never been screened is a normal state: both
    kinds come back ``not_started``.
    """
    require_permission(principal, SCREENING_READ)

    latest: dict[str, tuple[str, datetime]] = {}

    def offer(kind: str, firm_status: str | None, updated_at: datetime | None) -> None:
        if firm_status is None or updated_at is None:
            return
        held = latest.get(kind)
        if held is None or updated_at > held[1]:
            latest[kind] = (firm_status, updated_at)

    kyc = (
        await session.execute(
            text(
                "SELECT DISTINCT ON (k.kind) k.kind, k.status, k.updated_at "
                "FROM crm.kyc_checks k WHERE k.contact_id = :c "
                "ORDER BY k.kind, k.updated_at DESC, k.id DESC"
            ),
            {"c": contact_id},
        )
    ).all()
    for row in kyc:
        offer(str(row.kind), _FIRM_KYC_STATUS.get(str(row.status)), row.updated_at)

    checks = (
        await session.execute(
            text(
                "SELECT DISTINCT ON (ch.kind) ch.kind, ch.status, ch.decided_at, ch.updated_at "
                "FROM compliance.checks ch "
                "JOIN compliance.clients cl ON cl.id = ch.client_id "
                "WHERE cl.contact_id = :c AND ch.kyc_check_id IS NULL "
                "ORDER BY ch.kind, ch.updated_at DESC, ch.id DESC"
            ),
            {"c": contact_id},
        )
    ).all()
    for row in checks:
        reviewing = row.status == "refer" and row.decided_at is None
        firm_status = "pending" if reviewing else _FIRM_CHECK_STATUS.get(str(row.status))
        offer(str(row.kind), firm_status, row.updated_at)

    # Whether Graphene's ongoing monitoring covers the client's latest AML screen. The column is not
    # granted to a firm, so it is read elevated, for this client only: RLS proved them in the book.
    # A technical failure stored the flag when the screen was started, and then never ran: the
    # poller does not ask about it, so the firm is not told monitoring is on.
    async with as_service_role(session):
        monitored_row = (
            await session.execute(
                text(
                    "SELECT ch.status, ch.monitoring FROM compliance.checks ch "
                    "JOIN compliance.clients cl ON cl.id = ch.client_id "
                    "WHERE cl.contact_id = :c AND ch.kind = 'aml' "
                    "ORDER BY ch.id DESC LIMIT 1"
                ),
                {"c": contact_id},
            )
        ).one_or_none()
    monitored = (
        None
        if monitored_row is None
        else monitoring_covers(str(monitored_row.status), bool(monitored_row.monitoring))
    )

    return {
        "checks": [
            {
                "kind": kind,
                "status": latest[kind][0] if kind in latest else "not_started",
                "updated_at": latest[kind][1] if kind in latest else None,
                "monitored": monitored if kind == "aml" else None,
            }
            for kind in _SCREENING_KINDS
        ]
    }


class IoClientOut(BaseModel):
    client_id_io: str
    display_name: str | None = None
    #: The vendor's own payload for this client, whole. Carried through the search results so
    #: the Link step can store it verbatim on `platform.links.data` — the only copy of it this
    #: read keeps, since the search itself never touches our tables.
    raw: dict[str, Any]


def _io_client_out(raw: dict[str, Any]) -> IoClientOut:
    # `name`/`externalReference` are top-level on the client object, NOT under `person` (person
    # only carries firstName/lastName/gender/etc) -- confirmed against a live /v2/clients
    # response.
    name = raw.get("name")
    external_reference = raw.get("externalReference")
    display_name = " - ".join(part for part in (name, external_reference) if part) or None
    return IoClientOut(client_id_io=str(raw.get("id")), display_name=display_name, raw=raw)


class IoPlanOut(BaseModel):
    plan_id_io: str
    #: The plan's own policy number — what `platform.links.external_id` is keyed on for an
    #: account-scoped intelliflo link (not `plan_id_io`; see `link_account_to_io`).
    policy_number: str | None = None
    #: The plan's own reference number, confirmed against a live Intelliflo response.
    reference: str | None = None
    #: The wrapper type's name (e.g. "ISA", "AVC") — `planType.name`, NOT a top-level field; what
    #: the "IO Plans" dropdown's option label is built from now, alongside `reference` and
    #: `portfolio_category` (0209).
    plan_type: str | None = None
    #: The wrapper type's portfolio category (e.g. "Pensions", "Mortgages") — `planType
    #: .portfolioCategory`, confirmed against a live Intelliflo response: `planType` is a nested
    #: object (`{id, name, portfolioCategory, ...}`), not a flat string.
    portfolio_category: str | None = None
    #: The vendor's own payload for this plan, whole. Carried through so the Link step can store
    #: it verbatim on `platform.links.data` — mirrors `IoClientOut.raw`.
    raw: dict[str, Any] = {}


def _io_plan_out(raw: dict[str, Any]) -> IoPlanOut:
    policy_number = str(raw.get("policyNumber") or "").strip() or None
    reference = raw.get("reference")
    plan_type_obj = raw.get("planType") or {}
    plan_type = plan_type_obj.get("name") if isinstance(plan_type_obj, dict) else None
    portfolio_category = (
        plan_type_obj.get("portfolioCategory") if isinstance(plan_type_obj, dict) else None
    )
    return IoPlanOut(
        plan_id_io=str(raw.get("id")),
        policy_number=policy_number,
        reference=str(reference) if reference is not None else None,
        plan_type=str(plan_type) if plan_type is not None else None,
        portfolio_category=str(portfolio_category) if portfolio_category is not None else None,
        raw=raw,
    )


async def _io_client_id_for_contact(session: AsyncSession, contact_id: object) -> str | None:
    """This contact's own Intelliflo Office client id (`platform.links`, `entity_type='contact'`),
    or `None` when the contact has never been linked to IO — shared by the plans READ
    (`_io_plans_for_contact`) and the policy-number PATCH write (`link_account_to_io`), since
    Intelliflo's plan endpoint is scoped `/clients/{client_id_io}/plans/{plan_id_io}`, not a bare
    `/plans/{id}`.
    """
    if contact_id is None:
        return None
    return (
        await session.execute(
            text(
                "SELECT external_id FROM platform.links "
                "WHERE entity_type = 'contact' AND entity_id = :id "
                "  AND provider = 'intelliflo' AND link_type = 'primary'"
            ),
            {"id": contact_id},
        )
    ).scalar_one_or_none()


async def _io_plans_for_contact(session: AsyncSession, contact_id: object) -> list[IoPlanOut]:
    """This account's client's Intelliflo plans, or an empty list when the client has never been
    linked (no `platform.links` row) or Intelliflo refuses the request — the "IO Plans" card has
    no search step to retry from, so a vendor outage degrades to an empty dropdown rather than
    failing the whole account page.

    Plans with no policy number are left out entirely: `external_id` on the link this dropdown
    feeds is keyed on `policyNumber`, so a plan without one cannot be linked to in the first
    place and has no business appearing as a choice.
    """
    client_id_io = await _io_client_id_for_contact(session, contact_id)
    if client_id_io is None:
        return []
    try:
        plans = await intelliflo_client.get_client_plans(str(client_id_io))
    except (IntellifloAuthError, IntellifloApiError) as exc:
        logger.warning("Intelliflo plans request failed for client %s: %s", client_id_io, exc)
        return []
    return [p for p in (_io_plan_out(p) for p in plans) if p.policy_number]


#: `crm.contacts.national_identifier_type` values that are a UK National Insurance number --
#: mirrors `app.integrations.sei.transfers._NINO_TYPES`, duplicated rather than imported since
#: that module is SEI-transfer-specific and this is an unrelated Intelliflo flow.
_NINO_TYPES = frozenset({"nino", "national_insurance_number"})


class _ClientSearchTier(TypedDict, total=False):
    """Exactly `get_clients`'s str-valued keyword params, never `settings` -- a plain
    `dict[str, str]` unpacked with `**` cannot rule out an accidental `settings` key, which is
    typed `IntellifloSettings | None` and so would not match a `str` value; a `TypedDict` lets
    mypy check the `**tier` call key by key instead of against the broadest value type."""

    ni_number: str
    first_name: str
    last_name: str
    corporate_name: str


def _io_client_search_tiers(contact: Mapping[str, Any]) -> list[_ClientSearchTier]:
    """The search tiers for this contact, narrowest first: NI number (when one is on file), then
    first + last name together. Each tier is tried in turn until one returns a result; a tier with
    no usable field is skipped rather than sent as an empty/always-matching filter.

    There is deliberately NO first-name-only or last-name-only tier. Intelliflo's `eq` filter is
    an exact match across the one process-wide tenant every firm shares, so "John" alone returns
    every John in it -- another firm's clients, with their raw vendor payloads -- and a pick from
    that list would link this firm's client (and later push its custody number) onto someone
    else's. A counterpart whose stored name differs (a nickname, a dropped middle name) is left
    unlinked rather than guessed at.

    Built ONLY from the contact's own stored identity, never from caller input: the two callers
    (this module's `search_io_clients`, `integrations.io_clients_for_contact`) both search FOR a
    specific already-identified contact, not a free-text vendor-wide directory search -- a caller
    who could supply arbitrary filters here could list or match any client in the single,
    process-wide Intelliflo tenant this deployment holds credentials for, which is not scoped per
    portals tenant at all."""
    ni_type = str(contact["national_identifier_type"] or "").strip().lower()
    ni_number = str(contact["national_identifier"] or "").strip()
    first_name = str(contact["first_name"] or "").strip()
    surname = str(contact["surname"] or "").strip()

    # A company is a `corporate` client in Intelliflo, named under `corporate.name`; a person filter
    # (`person.lastName`) can never match it, so it is searched by that name alone.
    if str(contact.get("type") or "") == "organisation":
        name = str(contact.get("name") or surname).strip()
        return [{"corporate_name": name}] if name else []

    tiers: list[_ClientSearchTier] = []
    if ni_number and ni_type in _NINO_TYPES:
        tiers.append({"ni_number": ni_number})
    if first_name and surname:
        tiers.append({"first_name": first_name, "last_name": surname})
    return tiers


async def _contact_identity_row(session: AsyncSession, contact_id: int) -> dict[str, Any]:
    """This book contact's own name/NINO -- the fields `_io_client_search_tiers` builds a search
    from. `book_contact_id` has already proven the caller's firm can see this row; RLS on the
    caller's own `book_session` scopes the read the same way every other query in this module
    already relies on it to."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT type, name, first_name, surname, national_identifier, "
                    "national_identifier_type FROM crm.contacts WHERE id = :id"
                ),
                {"id": contact_id},
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


async def _io_clients_for_book_contact(
    session: AsyncSession, contact_id: int
) -> list[dict[str, Any]]:
    """The vendor client candidates for THIS contact, and only this contact -- the search tiers
    are built entirely from the contact's own stored name/NINO (`_io_client_search_tiers`), never
    from caller-supplied filters. `get_clients()` with every filter blank is Intelliflo's whole
    directory, so a contact with no usable identity field at all gets `[]` here without a vendor
    call, rather than that.

    Shared by the search step (`search_io_clients`) and the link step's own verification
    (`link_contact_to_io`): the latter re-runs this same, unparameterised search rather than
    trusting whatever `client_id_io` the request body names, so a pick can only ever be one of
    THIS contact's own candidates."""
    contact = await _contact_identity_row(session, contact_id)
    for tier in _io_client_search_tiers(contact):
        clients = await intelliflo_client.get_clients(**tier)
        if clients:
            return clients
    return []


@router.get("/contacts/{contact_uid}/io-clients", name="search-io-clients")
async def search_io_clients(
    contact_id: Annotated[int, Depends(book_contact_id)],
    principal: Principal = Depends(get_current_principal),
    # Declared even though this handler never queries with it: `book_contact_id` itself resolves
    # against `book_session`, and FastAPI must get the identical `Depends(book_session)` call from
    # the endpoint too, or a future edit that adds a differently-scoped session here would silently
    # disagree with the resolver about whose book `contact_uid` was ever checked against.
    session: AsyncSession = Depends(book_session),
) -> list[IoClientOut]:
    """Search Intelliflo Office's own client directory for a match to link this client against —
    the "Link to IO" card's search step.

    Takes NO caller-supplied filter: the single Intelliflo credential behind this deployment is
    process-wide, not scoped per portals tenant, so a free-text filter here would let a
    `contacts:write` caller list or match any client in that vendor tenant, not just this book's
    own. `_io_clients_for_book_contact` builds the search entirely from this contact's own stored
    name/NINO instead -- `contact_id` only proves the caller's firm can see this client
    (`book_contact_id` 404s an out-of-book uid). The companion write is `POST .../io-link`, below.

    A vendor auth/API failure becomes a 502, never a raw 500 — the caller reached us fine, it is
    Intelliflo that failed.
    """
    require_permission(principal, CONTACTS_WRITE)
    try:
        clients = await _io_clients_for_book_contact(session, contact_id)
    except (IntellifloAuthError, IntellifloApiError) as exc:
        raise HTTPException(status_code=502, detail=f"Intelliflo request failed: {exc}") from exc
    return [_io_client_out(c) for c in clients]


class LinkIoClientBody(BaseModel):
    #: Which of THIS contact's own candidates (`_io_clients_for_book_contact`) the adviser picked.
    #: No `raw` here any more: a caller-supplied vendor payload was never checked against
    #: Intelliflo at all, so `link_contact_to_io` re-runs that same search and stores the result it
    #: finds there, not whatever the request claims.
    client_id_io: str


class IoUnlinkOut(BaseModel):
    """What an account unlink did, so the operator is told rather than left to infer it."""

    #: False when the account had no Intelliflo link (the call is idempotent, not an error).
    unlinked: bool
    plan_id_io: str | None = None
    #: True when the plan's policy number was blanked in Intelliflo.
    policy_number_cleared: bool = False
    #: Why the plan was left alone, when it was: changed in Intelliflo since, gone, or not ours.
    note: str | None = None


async def _io_linked_accounts(session: AsyncSession, tenant_id: int, contact_id: int) -> list[str]:
    """The client's accounts that carry an Intelliflo link, as "name (number)" for a message. The
    number is the custodian link's `external_id`, the same one the Intelliflo policy number is."""
    rows = (
        (
            await session.execute(
                text(
                    "SELECT a.uid, a.name, "
                    "  (SELECT sl.external_id FROM platform.links sl "
                    "    WHERE sl.tenant_id = a.tenant_id AND sl.entity_type = 'account' "
                    "      AND sl.entity_id = a.id AND sl.link_type = 'primary' "
                    "      AND sl.provider::text = ANY(:custodians) "
                    "    ORDER BY sl.provider LIMIT 1) AS account_number "
                    "FROM platform.accounts a "
                    "JOIN platform.links al ON al.tenant_id = a.tenant_id "
                    "  AND al.entity_type = 'account' AND al.entity_id = a.id "
                    "  AND al.provider = 'intelliflo' AND al.link_type = 'primary' "
                    "WHERE a.tenant_id = :t AND a.contact_id = :c ORDER BY a.id"
                ),
                {"t": tenant_id, "c": contact_id, "custodians": list(providers.custodians())},
            )
        )
        .mappings()
        .all()
    )
    return [
        f"{r['name']} ({r['account_number'] or r['uid']})"
        if r["name"]
        else str(r["account_number"] or r["uid"])
        for r in rows
    ]


async def _upsert_contact_io_link(
    session: AsyncSession, tenant_id: int, contact_id: int, client_id_io: str
) -> IoClientOut:
    """The core write behind `link_contact_to_io` -- an ordinary `platform.links` row
    (`entity_type='contact'`, `provider='intelliflo'`, `link_type='primary'`), the same shape
    every other provider link already uses (0208 adds `intelliflo` to the `provider_code` enum
    this column is typed on).

    `client_id_io` is checked against this contact's OWN search candidates
    (`_io_clients_for_book_contact`) before anything is written -- never trusted bare, and the
    vendor payload stored is the one THIS lookup returns, never a caller's claim. Enforced HERE
    rather than by each caller, so the property holds regardless of who calls this: both
    `link_contact_to_io` (below) and the admin "Clients and Accounts" tab
    (`integrations.link_client_and_account_to_io`) reach it, and a caller-supplied client id/raw
    that was never checked against Intelliflo is exactly the gap that let a `contacts:write` user
    attach an arbitrary vendor client (one found via a different contact, or guessed) to this one
    -- the single Intelliflo credential behind this deployment is process-wide, not scoped per
    portals tenant.

    An upsert (`ON CONFLICT ... DO UPDATE`), matching the Morgan Lloyd and outbound-transaction
    links: picking a different match later REPLACES this client's link rather than erroring,
    since the unique key (`tenant_id, provider, entity_type, entity_id, link_type`) allows only
    one per client.

    Takes a plain `(session, tenant_id, contact_id)` rather than a `FirmContext` so it is reusable
    outside the book-write session that endpoint depends on -- the admin tab calls this directly
    under its own `admin_session`, which never holds `book:write`.
    """
    # Changing which Intelliflo client this is while its accounts are mapped would leave those
    # account links on plans of the OLD client, and the sync would pair the new client with an old
    # plan. Not cleared for the operator: each account is unlinked on its own first (which also
    # blanks its plan's policy number in Intelliflo), so nothing is removed as a side effect.
    # Re-linking the SAME Intelliflo client is harmless and stays allowed.
    current_client = await _io_client_id_for_contact(session, contact_id)
    if current_client is not None and str(current_client) != str(client_id_io):
        mapped = await _io_linked_accounts(session, tenant_id, contact_id)
        if mapped:
            shown = ", ".join(mapped[:5]) + (
                f" and {len(mapped) - 5} more" if len(mapped) > 5 else ""
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Account mapping exists ({shown}). Unlink the accounts before changing the "
                    "client mapping."
                ),
            )
    try:
        candidates = await _io_clients_for_book_contact(session, contact_id)
    except (IntellifloAuthError, IntellifloApiError) as exc:
        raise HTTPException(status_code=502, detail=f"Intelliflo request failed: {exc}") from exc
    match = next((c for c in candidates if str(c.get("id")) == client_id_io), None)
    if match is None:
        raise HTTPException(
            status_code=400, detail="That client is not a match for this contact in Intelliflo."
        )
    raw = match
    await session.execute(
        text(
            "INSERT INTO platform.links "
            "  (tenant_id, entity_type, entity_id, provider, link_type, external_id, data) "
            "VALUES (:t, 'contact', :cid, 'intelliflo', 'primary', :ext, CAST(:data AS jsonb)) "
            "ON CONFLICT (tenant_id, provider, entity_type, entity_id, link_type) "
            "DO UPDATE SET external_id = EXCLUDED.external_id, data = EXCLUDED.data, "
            "  updated_at = now()"
        ),
        {"t": tenant_id, "cid": contact_id, "ext": client_id_io, "data": json.dumps(raw)},
    )
    return IoClientOut(
        client_id_io=client_id_io, display_name=_io_client_out(raw).display_name, raw=raw
    )


@router.post("/contacts/{contact_uid}/io-link", name="link-contact-to-io")
async def link_contact_to_io(
    contact_id: Annotated[int, Depends(book_write_contact_id)],
    body: LinkIoClientBody,
    ctx: FirmContext = Depends(firm_book_write_session),
) -> IoClientOut:
    """Record the adviser's pick from the "Link to IO" search as this client's Intelliflo Office
    link. See `_upsert_contact_io_link` for the write itself, including the verification against
    this contact's own search candidates."""
    require_permission(ctx.principal, CONTACTS_WRITE)
    return await _upsert_contact_io_link(ctx.session, ctx.tenant_id, contact_id, body.client_id_io)


class LinkIoPlanBody(BaseModel):
    plan_id_io: str
    #: The exact `IoPlanOut.raw` the adviser picked from the "IO Plans" dropdown — stored as-is
    #: on `platform.links.data`. Same posture as `LinkIoClientBody.raw`.
    raw: dict[str, Any]


async def _account_custody_external_id(
    session: AsyncSession, tenant_id: int, account_id: int
) -> str | None:
    """This account's own custody-provider account number (Seccl/SEI/Morgan Lloyd, whichever this
    account is actually linked to) — the number an adviser recognises against this account, which
    `link_account_to_io` pushes into Intelliflo as the picked plan's new `policyNumber`.

    An account carries at most one custodian link at a time, so the first match is the only one;
    `None` when the account has no custody link yet (nothing to push).
    """
    return (
        await session.execute(
            text(
                "SELECT external_id FROM platform.links "
                "WHERE tenant_id = :t AND entity_type = 'account' AND entity_id = :aid "
                "  AND link_type = 'primary' AND provider = ANY(:custodians) "
                "ORDER BY provider LIMIT 1"
            ),
            {"t": tenant_id, "aid": account_id, "custodians": list(providers.custodians())},
        )
    ).scalar_one_or_none()


async def _upsert_account_io_link(
    session: AsyncSession,
    tenant_id: int,
    account_id: int,
    plan_id_io: str,
    raw: dict[str, Any],
    *,
    verified_plan: dict[str, Any] | None = None,
    only_unassigned_plans: bool = False,
) -> IoPlanOut:
    """The core write behind `link_account_to_io` — an ordinary `platform.links` row, this time
    `entity_type='account'`, `entity_id` the account rather than the contact. Same upsert shape as
    `_upsert_contact_io_link`, same reason: one link per account per provider.

    Also pushes this account's own custody account number (Seccl/SEI/...) into Intelliflo as the
    picked plan's `policyNumber` — the two systems otherwise disagree about the number an adviser
    actually recognises against this account. Intelliflo's plan endpoint is scoped
    `/clients/{client_id_io}/plans/{plan_id_io}`, so this also needs the contact's own IO client
    id (same link `_io_plans_for_contact` reads); a bare `/plans/{id}` 403s. Skipped (not an
    error) when the account has no custody link, or the contact no IO client link, to source
    either half from; a vendor failure on the push itself IS an error (502), since the whole point
    is keeping both systems in sync, unlike the read-only dropdown above which degrades quietly.

    `external_id` is the plan's POLICY NUMBER, not `plan_id_io` — the field an adviser would
    actually recognise against this account, and what the Intelliflo side of this mapping is
    keyed on. Falls back to `plan_id_io` only if the picked plan's payload carries no
    `policyNumber` at all.

    Whenever the contact has an IO client (custody number or not), `plan_id_io` is checked against
    `get_client_plans(client_id_io)` -- the contact's own IO client, resolved server-side, never the
    caller's claim. Without this, a caller who had linked an arbitrary vendor client to some OTHER
    contact (see `link_contact_to_io`'s own check) could still name any of that client's plan ids
    here. The verified plan's own payload replaces whatever `raw` was passed in, for the same reason
    `link_contact_to_io` ignores the caller's. Only the policy-number push depends on the custody
    number. With no IO client link there is nothing to verify against, so the write is refused
    (409) rather than storing a plan id the caller merely named: the unique index on the plan id is
    global across tenants, so an unverified claim would reserve another firm's plan. `raw` is kept
    in the signature for the callers that pass it, and is never stored.

    Takes a plain `(session, tenant_id, account_id)` rather than a `FirmContext`, same reason as
    `_upsert_contact_io_link`: the admin "Clients and Accounts" tab calls this under
    `admin_session`, which never holds `book:write`.
    """
    contact_id = await _account_contact_id(session, tenant_id=tenant_id, account_id=account_id)
    client_id_io = await _io_client_id_for_contact(session, contact_id)
    if client_id_io is None:
        raise HTTPException(
            status_code=409,
            detail="Link this client to Intelliflo before linking one of its plans.",
        )
    # One Intelliflo plan maps to one account. A second account on the same plan would put two
    # accounts' valuations and transactions onto it, and its policy number could only be right for
    # one of them. This look-up only sees this tenant's links, and only gives a friendlier message:
    # the rule itself, across tenants and under a race, is the unique index
    # `links_intelliflo_plan_uq`.
    taken = (
        await session.execute(
            text(
                "SELECT a.uid FROM platform.links l JOIN platform.accounts a "
                "  ON a.id = l.entity_id AND a.tenant_id = l.tenant_id "
                "WHERE l.tenant_id = :t AND l.provider = 'intelliflo' "
                "  AND l.entity_type = 'account' AND l.link_type = 'primary' "
                "  AND l.data ->> 'id' = :plan AND l.entity_id <> :aid "
                "LIMIT 1"
            ),
            {"t": tenant_id, "plan": str(plan_id_io), "aid": account_id},
        )
    ).scalar_one_or_none()
    if taken is not None:
        raise HTTPException(
            status_code=409,
            detail=f"That Intelliflo plan is already linked to another account ({taken}).",
        )
    push: tuple[str, str] | None = None  # (client_id_io, account number) to write into the plan
    account_number = await _account_custody_external_id(session, tenant_id, account_id)
    # Verified whenever the contact has an IO client, with or without a custody number: the
    # plan we store is what valuations and transactions are later posted to, so it must be one
    # of this client's own plans, and what is stored is Intelliflo's payload, not the caller's.
    verified = verified_plan
    if verified is None:
        try:
            plans = await intelliflo_client.get_client_plans(client_id_io)
        except (IntellifloAuthError, IntellifloApiError) as exc:
            raise HTTPException(
                status_code=502, detail=f"Intelliflo request failed: {exc}"
            ) from exc
        verified = next((p for p in plans if str(p.get("id")) == plan_id_io), None)
    if verified is None:
        raise HTTPException(
            status_code=400,
            detail="That plan does not belong to this client's Intelliflo record.",
        )
    if only_unassigned_plans:
        # The admin mapping only takes plans Intelliflo holds no policy number for; one that
        # has a number belongs to something already. The exception is this account's OWN
        # number, which the plan holds once we have linked it, so a retry still goes through.
        held = str(verified.get("policyNumber") or "").strip()
        if held and held != (account_number or ""):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"That Intelliflo plan already has a policy number ({held}). "
                    "Only plans without one can be linked here."
                ),
            )
    raw = dict(verified)
    # Only the push needs the number; with no custody link there is nothing to push. It is
    # sent AFTER the row is written (below), but is stored as the plan's number from the start.
    if account_number is not None:
        push = (client_id_io, account_number)
        raw["policyNumber"] = account_number
    external_id = raw.get("policyNumber") or plan_id_io
    # Local row FIRST, then Intelliflo, both inside one savepoint. The unique index
    # (`links_intelliflo_plan_uq`, 0278) is the only thing that sees a plan linked in ANOTHER
    # tenant, so it has to answer before this account's number is written into the plan: pushing
    # first would overwrite another firm's plan in Intelliflo and only then fail the local write,
    # leaving nothing local to undo it. If the push fails the savepoint rolls the row back, so the
    # two sides still cannot disagree about whether the account is mapped.
    async with session.begin_nested():
        try:
            await session.execute(
                text(
                    "INSERT INTO platform.links "
                    "  (tenant_id, entity_type, entity_id, provider, link_type, external_id, data) "
                    "VALUES (:t, 'account', :aid, 'intelliflo', 'primary', :ext, "
                    "  CAST(:data AS jsonb)) "
                    "ON CONFLICT (tenant_id, provider, entity_type, entity_id, link_type) "
                    "DO UPDATE SET external_id = EXCLUDED.external_id, data = EXCLUDED.data, "
                    "  updated_at = now()"
                ),
                {
                    "t": tenant_id,
                    "aid": account_id,
                    "ext": str(external_id),
                    "data": json.dumps(raw),
                },
            )
        except IntegrityError as exc:
            # The plan is already on another account, in this tenant (a concurrent request) or
            # another one. Say nothing about whose.
            raise HTTPException(
                status_code=409,
                detail="That Intelliflo plan is already linked to another account.",
            ) from exc
        if push is not None:
            try:
                await intelliflo_client.update_plan_policy_number(push[0], plan_id_io, push[1])
            except (IntellifloAuthError, IntellifloApiError) as exc:
                raise HTTPException(
                    status_code=502, detail=f"Intelliflo request failed: {exc}"
                ) from exc
    return _io_plan_out(raw)


async def _unlink_account_io(
    session: AsyncSession, tenant_id: int, account_id: int, *, skip_intelliflo: bool = False
) -> IoUnlinkOut:
    """Remove this account's Intelliflo link, and blank its plan's policy number in Intelliflo.

    The plan is only touched when it is OURS to touch: the link was made by pushing this account's
    custodian number into it (the link's `external_id` is that number), and the plan still holds
    exactly that number. A plan whose number was changed in Intelliflo since, a plan that no longer
    exists, and a link made WITHOUT a push (so `external_id` is the plan's own number, which must
    not be blanked) are left alone and the local link is still removed.

    Intelliflo first, then the local row: if Intelliflo fails the link is KEPT and the call is a
    502, so the two sides cannot disagree about whether the account is mapped. Idempotent: an
    account with no Intelliflo link answers `unlinked=False`. `skip_intelliflo` removes only the
    local link, for a plan Intelliflo will not let us blank.
    """
    link = (
        (
            await session.execute(
                text(
                    "SELECT id, external_id, data FROM platform.links "
                    "WHERE tenant_id = :t AND entity_type = 'account' AND entity_id = :a "
                    "  AND provider = 'intelliflo' AND link_type = 'primary' FOR UPDATE"
                ),
                {"t": tenant_id, "a": account_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if link is None:
        return IoUnlinkOut(unlinked=False)
    plan_id = str((link["data"] or {}).get("id") or "").strip() or None
    pushed = str(link["external_id"] or "").strip()
    cleared = False
    note: str | None = None

    custody = await _account_custody_external_id(session, tenant_id, account_id)
    contact_id = await _account_contact_id(session, tenant_id=tenant_id, account_id=account_id)
    client_id_io = await _io_client_id_for_contact(session, contact_id)

    if skip_intelliflo:
        note = "Intelliflo was not contacted: only the local mapping was removed."
    elif not plan_id or not client_id_io:
        note = "No Intelliflo client link to reach the plan: only the local mapping was removed."
    elif custody is None or pushed != custody:
        note = "The plan's policy number was not set by this link, so it was left as it is."
    else:
        try:
            plans = await intelliflo_client.get_client_plans(client_id_io)
            plan = next((p for p in plans if str(p.get("id")) == plan_id), None)
            if plan is None:
                note = "The plan no longer exists in Intelliflo; nothing to clear."
            elif str(plan.get("policyNumber") or "").strip() != pushed:
                note = (
                    f"The plan's policy number was changed in Intelliflo "
                    f"({plan.get('policyNumber') or 'blank'}); it was left as it is."
                )
            else:
                await intelliflo_client.update_plan_policy_number(client_id_io, plan_id, "")
                cleared = True
        except (IntellifloAuthError, IntellifloApiError) as exc:
            raise HTTPException(
                status_code=502, detail=f"Intelliflo request failed: {exc}. The link was kept."
            ) from exc

    await session.execute(text("DELETE FROM platform.links WHERE id = :id"), {"id": link["id"]})
    return IoUnlinkOut(unlinked=True, plan_id_io=plan_id, policy_number_cleared=cleared, note=note)


class IoClientUnlinkOut(BaseModel):
    """What a client unlink did."""

    #: False when the client had no Intelliflo link (the call is idempotent, not an error).
    unlinked: bool
    client_id_io: str | None = None


async def _unlink_contact_io(
    session: AsyncSession, tenant_id: int, contact_id: int
) -> IoClientUnlinkOut:
    """Remove this client's link to its Intelliflo client: the one `platform.links` row
    (`entity_type='contact'`, `provider='intelliflo'`, `link_type='primary'`), and nothing else.

    REFUSED (409) while any of the client's accounts is still linked to an Intelliflo plan. Those
    account links point at plans of THIS Intelliflo client, so dropping the client link under them
    would leave plan links with no client to resolve them against. Not cleared for the operator,
    for the same reason `_upsert_contact_io_link` refuses to change the client: each account is
    unlinked on its own first (which also blanks the policy number it pushed into the plan).

    Nothing is sent to Intelliflo: linking a client only ever wrote the local row, so there is
    nothing there to undo. Idempotent: a client with no Intelliflo link answers `unlinked=False`.
    """
    link = (
        (
            await session.execute(
                text(
                    "SELECT id, external_id FROM platform.links "
                    "WHERE tenant_id = :t AND entity_type = 'contact' AND entity_id = :c "
                    "  AND provider = 'intelliflo' AND link_type = 'primary' FOR UPDATE"
                ),
                {"t": tenant_id, "c": contact_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if link is None:
        return IoClientUnlinkOut(unlinked=False)
    mapped = await _io_linked_accounts(session, tenant_id, contact_id)
    if mapped:
        shown = ", ".join(mapped[:5]) + (f" and {len(mapped) - 5} more" if len(mapped) > 5 else "")
        raise HTTPException(
            status_code=409,
            detail=(
                f"Account mapping exists ({shown}). Unlink the accounts before unlinking the "
                "client."
            ),
        )
    await session.execute(text("DELETE FROM platform.links WHERE id = :id"), {"id": link["id"]})
    return IoClientUnlinkOut(unlinked=True, client_id_io=str(link["external_id"] or "") or None)


@router.post("/accounts/{account_uid}/io-link", name="link-account-to-io")
async def link_account_to_io(
    account_id: Annotated[int, Depends(book_write_account_id)],
    body: LinkIoPlanBody,
    ctx: FirmContext = Depends(firm_book_write_session),
) -> IoPlanOut:
    """Record the adviser's pick from the "IO Plans" dropdown as this ACCOUNT's Intelliflo Office
    link. See `_upsert_account_io_link` for the write itself."""
    require_permission(ctx.principal, CONTACTS_WRITE)
    return await _upsert_account_io_link(
        ctx.session, ctx.tenant_id, account_id, body.plan_id_io, body.raw
    )


def _account_value(row: dict[str, object]) -> float | None:
    """The account's own value, or None when the custodian has not valued it.

    `a.value` is NOT coalesced in the SELECT below (unlike the contacts listing's summed
    `portfolio_value`), so a NULL here already means "unvalued" — no `valued_account_count`
    twin is needed the way `_contact_value` needs one."""
    value = row.get("value")
    return float(value) if isinstance(value, int | float) else None


#: The accounts listing's filters. Model uses the stable catalogue id rather than its editable
#: display name; the portal builds the human-readable options from the firm's enabled models.
#:
#: `cash` and `growth` are deliberately NOT filters. They are columns, and a range on them can
#: be added the same way `value` is; nothing asks for it today.
_ACCOUNT_FILTERS: listing.Filters = {
    "status": lambda row, value: listing.matches(value, row.get("status")),
    "adviser": lambda row, value: listing.matches(value, row.get("adviser")),
    "custodian": lambda row, value: listing.matches(value, row.get("custodian")),
    "type": lambda row, value: listing.matches(value, row.get("product_type")),
    # The service the firm provides on the account (advisory / discretionary / execution-only), as
    # the SELECT above decided it — never recomputed here, or "Discretionary" would mean one thing
    # in the column and another in the filter.
    "service": lambda row, value: listing.matches(value, row.get("service_type")),
    "model": lambda row, value: listing.matches(value, row.get("model_id")),
    "value": lambda row, value: listing.within_range(value, _account_value(row)),
}
_ACCOUNT_BOUNDS: dict[str, Callable[[dict[str, object]], float | None]] = {
    "value": _account_value,
}
_ACCOUNT_FACETS: listing.Facets = [
    ("status", lambda row: row.get("status")),
    ("adviser", lambda row: row.get("adviser")),
    ("custodian", lambda row: row.get("custodian")),
    ("type", lambda row: row.get("product_type")),
    ("service", lambda row: row.get("service_type")),
    ("model", lambda row: row.get("model_id")),
]
# Sortable columns, keyed as the accounts table keys them. Closed set — see _CONTACT_SORTS.
# `cash` and `growth` are the stored valuation. An account the custodian has not stated sorts
# last rather than as a zero, which would read as a real figure.
_ACCOUNT_SORTS: listing.Sorts = {
    "name": lambda row: row.get("name") or row.get("uid"),
    "client": lambda row: (row.get("client") or {}).get("name"),
    "adviser": lambda row: row.get("adviser"),
    "type": lambda row: row.get("product_type"),
    "service": lambda row: row.get("service_type"),
    "model": lambda row: row.get("model_name"),
    "currency": lambda row: row.get("currency"),
    "value": lambda row: row.get("value"),
    "cash": lambda row: row.get("cash"),
    "growth": lambda row: row.get("growth"),
    "status": lambda row: row.get("status"),
}

_ACCOUNT_SQL_SORTS = {
    "name": "COALESCE(name, uid)",
    "client": "c_name",
    "adviser": "adviser",
    "type": "product_type::text",
    "service": "service_type",
    "model": "model_name",
    "currency": "currency",
    "value": "value",
    "cash": "cash",
    "growth": "growth",
    "status": "status",
}
_ACCOUNT_SQL_DEFAULT_ORDER = "id"


def _account_filter_sql(*, skip: str | None = None) -> str:
    parts = {
        "status": "(CAST(:status AS text[]) IS NULL OR status::text = ANY(:status))",
        "adviser": "(CAST(:adviser AS text[]) IS NULL OR adviser = ANY(:adviser))",
        "custodian": "(CAST(:custodian AS text[]) IS NULL OR custodian = ANY(:custodian))",
        "type": "(CAST(:type AS text[]) IS NULL OR product_type::text = ANY(:type))",
        "service": "(CAST(:service AS text[]) IS NULL OR service_type = ANY(:service))",
        "model": "(CAST(:model AS text[]) IS NULL OR CAST(model_id AS text) = ANY(:model))",
        # `value` is NOT coalesced in the SELECT, so a NULL here already means "the custodian has
        # not valued it" — unknown, and so outside every bounded range.
        "value": listing.range_sql("value", "value"),
    }
    return " AND ".join(sql for key, sql in parts.items() if key != skip)


@router.get("/accounts", name="list-accounts")
async def list_accounts(
    q: str | None = None,
    # MULTI-VALUED, like the contacts listing: the filter bar offers several and the question is an
    # OR. One occurrence still parses to a one-element list, so an older caller's URL is unchanged.
    status: Annotated[list[str] | None, Query()] = None,
    adviser: Annotated[list[str] | None, Query()] = None,
    custodian: Annotated[list[str] | None, Query()] = None,
    # `type`, not `product_type`. The filter key, the facet key and the transactions endpoint's own
    # parameter are all `type`, so this was the one spelling that disagreed with the rest -- and the
    # portal writes `?type=` while both the loader's allowlist and this signature read
    # `product_type`, so the Type filter on /accounts silently did nothing. A filter that answers
    # 200 and ignores you is worse than one that errors.
    type: Annotated[list[str] | None, Query()] = None,  # noqa: A002 - the query name the portal filters on
    service: Annotated[list[str] | None, Query()] = None,
    model: Annotated[list[str] | None, Query()] = None,
    value: Annotated[list[str] | None, Query()] = None,
    contact_id: int | None = None,
    sort: str | None = None,
    dir: str | None = None,  # noqa: A002 - the wire name the portal sends
    limit: int | None = None,
    offset: int = 0,
    session: AsyncSession = Depends(book_session),
) -> list[dict[str, object]] | dict[str, object]:
    """The accounts the caller may see (RLS-scoped), with product, owning client, adviser and
    the stored valuation's cash and growth -- the columns the platform accounts listing renders.

    ``discretionary`` is simply "managed against a model": an account with a ``model_id`` is run
    to a model. ``service_type`` is only the captured ``investment_capacity``. A model is not a
    service agreement, so an account SEI linked to a model and left otherwise blank stays NULL
    and the listing prints an em dash. The owning client comes back nested so the portals can
    format it with the same
    ``clientName`` helper the CRM screens use, rather than a second name-assembly rule living in
    SQL.

    ``cash`` and ``growth`` are the account's latest valuation: cash summed across source systems,
    growth the custodian's stated unrealised gain, or the administrator's when the
    custodian stated none. A NULL is a figure that was not stated.

    ``q`` matches the way the search bar does, over the account's, product's and client's
    ``_search`` plus product type, model and adviser, with the same semantics as /crm/holdings:
    applied after shaping, before the page cut, so a paged ``total`` counts the matches and the
    bare list honours it too. Server-side for the same reason -- this listing pages, and a
    client-side search over one fetched page would silently lie about the rest of the book.

    Paged only on request: with no ``limit`` the bare list is the frozen shape the platform
    portal and legacy client pages parse; with one, ``_page``'s ``{items, total}`` envelope.
    ``ORDER BY a.id`` below is a total order, so pages never overlap or drop rows between
    requests.

    ``contact_id``, when given, narrows to that ONE client's accounts — the active-contact
    selector for a client-portal login linked to more than one contact. ANDed on top of
    whatever RLS already allows, so an id outside the caller's real scope narrows to nothing
    rather than widening access; harmless/unused for a staff caller who never passes it.
    """
    active: dict[str, str | Sequence[str] | None] = {
        "status": listing.clean_many(status),
        "adviser": listing.clean_many(adviser),
        "custodian": listing.clean_many(custodian),
        "type": listing.clean_many(type),
        "service": listing.clean_many(service),
        "model": listing.clean_many(model),
        "value": listing.clean_range(value),
    }
    params: dict[str, object] = {
        "cid": contact_id,
        **listing.search_params(q),
        "status": active["status"],
        "adviser": active["adviser"],
        "custodian": active["custodian"],
        "custodians": list(providers.custodians()),
        "type": active["type"],
        "service": active["service"],
        "model": active["model"],
        **listing.range_params("value", value),
    }
    # The account's adviser: shown in the row and matched by the search box. `role = 'adviser'`
    # is unique per contact (0245), so no tie-break ordering is needed.
    adviser_sql = (
        "(SELECT u.name FROM crm.client_advisers ca "
        "   JOIN auth.users u ON u.id = ca.adviser_id "
        "  WHERE ca.contact_id = a.contact_id AND ca.role = 'adviser')"
    )
    cte = (
        "WITH " + _LISTING_BOOK_CTES + ", shaped AS ("  # noqa: S608
        "SELECT a.id, a.uid, a.name, a.status, a.currency, "
        "       a.value::float8 AS value, " + _VALUED_AT + " AS value_as_of, "
        "       valuation.cash_value::float8 AS cash, "
        "       valuation.unrealised_gain::float8 AS growth, "
        "       a.contact_id, a.risk_level, "
        "       a.custody, a.opened_at, p.name AS product_name, "
        "       p.code AS product_code, p.product_type, "
        "       COALESCE(p.is_pension, false) AS is_pension, "
        "       a.model_id, a.model_id IS NOT NULL AS discretionary, "
        "       NULLIF(a.investment_capacity, '') AS service_type, "
        "       m.name AS model_name, "
        "       c.uid AS c_uid, c.type AS c_type, c.name AS c_name, "
        "       c.title AS c_title, c.first_name AS c_first_name, "
        "       c.surname AS c_surname, "
        "       " + adviser_sql + " AS adviser, "
        "COALESCE(a.custodian, cust.provider)::text AS custodian "
        "FROM platform.accounts a "
        "LEFT JOIN platform.products p ON p.id = a.product_id "
        "LEFT JOIN platform.models m ON m.id = a.model_id "
        "LEFT JOIN crm.contacts c ON c.id = a.contact_id AND c.deleted_at IS NULL "
        + _ACCOUNT_VALUATION_JOIN
        + "LEFT JOIN account_custodians cust "
        "  ON cust.account_id = a.id AND cust.tenant_id = a.tenant_id "
        "WHERE (CAST(:cid AS int) IS NULL OR a.contact_id = CAST(:cid AS int) "
        "  OR EXISTS (SELECT 1 FROM crm.account_contacts ac "
        "     WHERE ac.tenant_id = a.tenant_id AND ac.account_id = a.id "
        "       AND ac.contact_id = CAST(:cid AS int) "
        "       AND ac.role = 'registered_contact'))"
        # The Spotlight bar's match over the account's, product's and client's `_search`, plus
        # the labels only this list shows. On the base rows, so no `_search` is projected.
        "  AND "
        + listing.search_sql(
            "a._search",
            "p._search",
            "c._search",
            also=("p.product_type::text", "m.name", adviser_sql),
        )
        # MATERIALIZED so the page, the total and every facet share this one join.
        + "), searched AS MATERIALIZED ("
        " SELECT * FROM shaped"
        "), filtered AS ("
        " SELECT * FROM searched WHERE " + _account_filter_sql() + ")"
    )

    def nest(record: dict[str, object]) -> dict[str, object]:
        client = {
            "id": record["contact_id"],
            "uid": record.pop("c_uid"),
            "type": record.pop("c_type"),
            "name": record.pop("c_name"),
            "title": record.pop("c_title"),
            "first_name": record.pop("c_first_name"),
            "surname": record.pop("c_surname"),
        }
        return {**record, "client": client}

    await session.execute(text(listing.FUZZY_THRESHOLD_SQL))
    page_limit = listing.clamp_limit(limit)
    order = listing.order_sql(sort, dir, _ACCOUNT_SQL_SORTS, _ACCOUNT_SQL_DEFAULT_ORDER)
    # Same total order as the clients list: equal figures, and every unstated one, tie on id.
    if (sort or "").strip() in ("cash", "growth"):
        tie = "DESC" if (dir or "").strip().lower() == "desc" else "ASC"
        order += f", id {tie}"
    if page_limit is None:
        rows = (
            (
                await session.execute(
                    text(cte + " SELECT * FROM filtered ORDER BY " + order),  # noqa: S608
                    params,
                )
            )
            .mappings()
            .all()
        )
        return [nest(dict(row)) for row in rows]
    params["lim"] = page_limit
    params["off"] = max(offset, 0)
    page_rows = (
        (
            await session.execute(
                text(
                    _listing_page_sql(
                        cte,
                        order,
                        _facet_sql("status::text", _account_filter_sql(skip="status"))
                        + " AS facet_status, "
                        + _facet_sql("adviser", _account_filter_sql(skip="adviser"))
                        + " AS facet_adviser, "
                        + _facet_sql("custodian", _account_filter_sql(skip="custodian"))
                        + " AS facet_custodian, "
                        + _facet_sql("product_type::text", _account_filter_sql(skip="type"))
                        + " AS facet_type, "
                        + _facet_sql("service_type", _account_filter_sql(skip="service"))
                        + " AS facet_service, "
                        + _facet_sql("CAST(model_id AS text)", _account_filter_sql(skip="model"))
                        + " AS facet_model, "
                        + _bounds_sql("value", _account_filter_sql(skip="value"))
                        + " AS facet_value_bounds",
                    )
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    items, total, toolbar_row = _read_listing_page(
        page_rows,
        facets=("status", "adviser", "custodian", "type", "service", "model"),
        ranges=("value",),
    )
    return {
        "items": [nest(item) for item in items],
        "total": total,
        **_with_advisers(
            _toolbar(
                toolbar_row,
                ("status", "adviser", "custodian", "type", "service", "model"),
                ("value",),
            ),
            await adviser_directory(session),
        ),
    }


# ── Writes ───────────────────────────────────────────────────────────────────────────────────
# Two properties of the rows above that the record has to be able to edit. On
# ``firm_book_write_session`` (service_role; without ``?tenant=`` it IS ``firm_session`` — tenant
# resolved from the caller's verified identity, never from the path; with it, an operations
# principal holding ``book:write`` acts on the named firm's behalf), gated by ``contacts:write``.
# See the module docstring for why they live here.


class SetBankAccount(BaseModel):
    """A client's bank details, or every field blank / ``null`` to clear them. Validated and
    canonicalised by ``contacts.normalise_bank_account`` — this is deliberately loose at the wire so
    the error the adviser reads names the field ("Enter a 6-digit sort code") rather than being a
    pydantic type dump. ``scheme`` is ``bacs`` or ``iban`` when the form has chosen a rail; stored
    rows do not carry it. ``method`` is the UK rail's payment method (``bacs`` or
    ``faster_payment``) — BACS is the default and is not stored, so a blank/absent value reads as
    BACS; an international row has none, since it always reaches SEI over SWIFT."""

    scheme: str | None = None
    name: str | None = None
    bank: str | None = None
    sort_code: str | None = None
    account_number: str | None = None
    iban: str | None = None
    bic: str | None = None
    method: str | None = None
    justification: str | None = None


class SetVulnerability(BaseModel):
    """A client's vulnerability record, or ``is_vulnerable: false`` to clear it.

    Deliberately loose at the wire — the reasons are validated by
    ``contacts.normalise_vulnerability`` so the adviser reads "Unknown vulnerability reason(s)"
    rather than a pydantic enum dump, and so the API and the portal share one vocabulary."""

    is_vulnerable: bool = False
    reasons: list[str] | None = None
    is_temporary: bool | None = None
    notes: str | None = None
    # The reason, recorded when a firm's four-eye policy holds the change for review.
    justification: str | None = None


class SetAccountModel(BaseModel):
    """The account's mandate. ``null`` clears it (advisory / self-directed)."""

    model_id: int | None = None
    justification: str | None = None


class SetAccountFeePackages(BaseModel):
    """The account's equal fee packages. An empty list clears every assignment."""

    fee_package_ids: list[int]
    justification: str | None = None


class SetAccountRegularMovements(BaseModel):
    """The account's scheduled payments-in and withdrawals. An empty list clears that side.

    Untyped rows on purpose: ``regular_movements.normalise_movements`` is the allowlist
    (frequency, method, a positive amount), so a refusal names the field rather than dumping
    a pydantic type error, and so GET and PUT share one reader.
    """

    payments: list[dict[str, object]] | None = None
    withdrawals: list[dict[str, object]] | None = None
    justification: str | None = None


class SetAccountBankAccounts(BaseModel):
    """The account's own bank accounts, in the firm's order. `null` clears them.

    A LIST, always — `[0]` is the default a payment takes, and a bare object would read as one
    bank account and then fail every `[0]` in `app/bank_accounts.py`, which is the shape of a bug
    nobody notices until a payment. `app.bank_accounts.normalise_entries` refuses one with a
    sentence rather than leaving the database CHECK to name a constraint.

    Untyped entries on purpose: the shape is `bank_accounts.normalise_entry`'s, which validates
    the sort code and account number through the SAME normaliser the client-level column uses and
    then checks `method` and `direction` against their closed vocabularies. Restating those here
    would be a second allowlist, which is what `UpdateContact` and `contacts._UPDATABLE` cost.
    """

    bank_accounts: list[dict[str, object]] | None = None
    justification: str | None = None


@router.put(
    "/contacts/{contact_uid}/bank-account",
    name="set-contact-bank-account",
    response_model=None,
)
async def set_contact_bank_account(
    contact_uid: str,
    contact_id: Annotated[int, Depends(book_write_contact_id)],
    body: SetBankAccount,
    ctx: FirmContext = Depends(firm_book_write_session),
) -> dict[str, object] | JSONResponse:
    """Set or clear a client's bank details.

    Its own endpoint rather than a field on ``PATCH /firm/contacts/{id}``: bank data gets one
    validated door, and the audit row it writes is unambiguous about what changed. 400 carries the
    validation message; it never echoes the digits back. Book-enabled (``?tenant=<uid>`` +
    ``book:write``): ops set the details on the target firm's client, audit naming the operator.

    Gated ``instructions:write`` (the money-movement family): the bank account is where
    withdrawals are paid, so setting it is part of the money rails, not the client record."""
    require_permission(ctx.principal, INSTRUCTIONS_WRITE)
    payload = body.model_dump(exclude={"justification"})
    payload["contact_id"] = contact_id
    try:
        accepted = await contacts_mod.accept_bank_account(
            ctx.session,
            tenant_id=ctx.tenant_id,
            contact_id=contact_id,
            bank_account=payload,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if isinstance(accepted, dict):
        payload.update(accepted)
    try:
        hold = await maybe_hold(
            ctx.session,
            tenant_id=ctx.tenant_id,
            workos_user_id=ctx.principal.user_id,
            action_key="contact.bank_details_changed",
            subject_type="contact",
            subject_id=contact_uid,
            contact_id=contact_id,
            payload=payload,
            justification=body.justification,
            idempotency_key=idempotency_key(
                "contact.bank_details_changed", "contact", contact_uid, payload
            ),
        )
    except ReviewError as exc:
        raise_review(exc)
    if hold.held:
        return pending_response(hold)
    try:
        return await contacts_mod.set_bank_account(
            ctx.session,
            tenant_id=ctx.tenant_id,
            contact_id=contact_id,
            bank_account=accepted,
            directory=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put(
    "/contacts/{contact_uid}/vulnerability",
    name="set-contact-vulnerability",
    response_model=None,
)
async def set_contact_vulnerability(
    contact_uid: str,
    contact_id: Annotated[int, Depends(book_write_contact_id)],
    body: SetVulnerability,
    ctx: FirmContext = Depends(firm_book_write_session),
) -> dict[str, object] | JSONResponse:
    """Record or clear a client's vulnerability (FCA FG21/1 drivers).

    Its own endpoint for the same reason bank details have one: this is special-category-adjacent
    personal data, it gets one validated door, and the audit row it writes says exactly what
    changed. `crm.contacts` is already audit-attached (0003), so the trail is automatic.

    Four-eye enabled: when the firm turns approval on for this action the change is held for a
    second person (``maybe_hold`` → a baton review run) instead of being written straight away.

    Book-enabled like the sibling writes (``?tenant=<uid>`` + ``book:write``), so an operator can
    record what a firm has told them; without ``?tenant=`` this IS ``firm_session``. Gated
    ``contacts:write`` and not the money family: this is a fact about the person, not a rail."""
    require_permission(ctx.principal, CONTACTS_WRITE)
    vulnerability = body.model_dump(mode="json", exclude={"justification"})
    payload = {"contact_id": contact_id, "vulnerability": vulnerability}
    try:
        hold = await maybe_hold(
            ctx.session,
            tenant_id=ctx.tenant_id,
            workos_user_id=ctx.principal.user_id,
            action_key="client.vulnerability_updated",
            subject_type="contact",
            subject_id=contact_uid,
            contact_id=contact_id,
            payload=payload,
            justification=body.justification,
            idempotency_key=idempotency_key(
                "client.vulnerability_updated", "contact", contact_uid, payload
            ),
        )
    except ReviewError as exc:
        raise_review(exc)
    if hold.held:
        return pending_response(hold)
    try:
        return await contacts_mod.set_vulnerability(
            ctx.session,
            tenant_id=ctx.tenant_id,
            contact_id=contact_id,
            vulnerability=vulnerability,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


class FatcaDocumentOut(BaseModel):
    uid: str
    name: str


class FatcaDocumentUploaded(BaseModel):
    fatca_document: FatcaDocumentOut


async def upload_contact_fatca_document(
    contact_id: Annotated[int, Depends(book_write_contact_id)],
    file: UploadFile = File(...),
    ctx: FirmContext = Depends(firm_book_write_session),
) -> FatcaDocumentUploaded:
    """File the client's signed W-9 or W-8 and link it to their answer (``fatca_document_id``).

    The form is filed for the answer the record holds now, so a client whose ``fatca_status`` is
    not ``w9`` or ``w8`` is refused with 409. The link lasts while the answer does: a change of
    ``fatca_status`` clears it (0261's trigger), and the document stays in the client's documents.
    A PDF or an image only, as for any signed copy.

    The form also answers any request still open for it (``fatca.settle_open_requests``): a
    journey step asking the client for their W-9 / W-8 completes, so the client is not asked
    for a form staff already hold.

    Gated ``contacts:write`` and book-enabled, like ``PATCH /firm/contacts/{uid}``, which edits the
    answer itself."""
    require_permission(ctx.principal, CONTACTS_WRITE)
    # Locked, so the answer cannot change between this check and the link written below.
    held = (
        await ctx.session.execute(
            text(
                "SELECT fatca_status FROM crm.contacts WHERE id = :c AND tenant_id = :t FOR UPDATE"
            ),
            {"c": contact_id, "t": ctx.tenant_id},
        )
    ).one_or_none()
    if held is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
    name = fatca.FORM_NAMES.get(str(held.fatca_status))
    if name is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Choose W-9 or W-8 for this client before you upload the signed form.",
        )
    document_id, document_uid = await documents_router.store_contact_upload(
        ctx.session,
        tenant_id=ctx.tenant_id,
        contact_id=contact_id,
        file=file,
        document_type=fatca.FORM_DOCUMENT_TYPE,
        name=name,
        accept=documents_router.signed_copy_only,
        audit_action="document.uploaded",
        audit_detail={"fatca_status": held.fatca_status},
    )
    await fatca.link_signed_form(
        ctx.session, tenant_id=ctx.tenant_id, contact_id=contact_id, document_id=document_id
    )
    await fatca.settle_open_requests(
        ctx.session, tenant_id=ctx.tenant_id, contact_id=contact_id, document_id=document_id
    )
    return FatcaDocumentUploaded(fatca_document=FatcaDocumentOut(uid=document_uid, name=name))


# Registered imperatively for the single-document body ceiling, as the other evidence uploads are.
router.add_api_route(
    "/contacts/{contact_uid}/fatca-document",
    upload_contact_fatca_document,
    methods=["POST"],
    name="upload-contact-fatca-document",
    status_code=status.HTTP_201_CREATED,
    route_class_override=documents_router.SingleUploadRoute,
    responses={
        status.HTTP_409_CONFLICT: {
            "description": "The client's W-9 or W-8 answer is not W-9 or W-8.",
        }
    },
)


async def _account_contact_id(session: Any, *, tenant_id: int, account_id: int) -> int | None:
    raw = (
        await session.execute(
            text("SELECT contact_id FROM platform.accounts WHERE id = :a AND tenant_id = :t"),
            {"a": account_id, "t": tenant_id},
        )
    ).scalar_one_or_none()
    return int(raw) if raw is not None else None


@router.put(
    "/accounts/{account_uid}/bank-accounts",
    name="set-account-bank-accounts",
    response_model=None,
)
async def set_account_bank_accounts(
    account_uid: str,
    account_id: Annotated[int, Depends(book_write_account_id)],
    body: SetAccountBankAccounts,
    ctx: FirmContext = Depends(firm_book_write_session),
) -> dict[str, object] | JSONResponse:
    """Set or clear the account's own bank accounts.

    WHERE MONEY LEAVES FROM, at the account grain. Resolution is account first, client second
    (`app/bank_accounts.py`), and that order is not a preference — the two custodians sit on
    opposite sides of it: SEI holds pay-to profiles per ACCOUNT (682 over 283 accounts in the
    corpus), Seccl per CLIENT with no destination in a withdrawal body at all. Until now the
    account half had no writer anywhere — every write in the repo was a test fixture — so
    `resolve_for_account` always fell through to the client's details and the branch this endpoint
    feeds could not be taken.

    Its own endpoint rather than a key on the account PATCH, for the reason
    `set-contact-bank-account` gives: bank data gets one validated door, and the audit row it
    writes is unambiguous about what changed. 400 carries the validation message and never echoes
    the digits back.

    Gated ``instructions:write`` (the money-movement family), exactly as the client-level one is:
    this is where a withdrawal is paid, so it belongs to the money rails and not to the account
    record."""
    require_permission(ctx.principal, INSTRUCTIONS_WRITE)
    if body.bank_accounts:
        allowed = (
            await ctx.session.execute(
                text(
                    "SELECT COALESCE(p.bank_details_allowed, true) "
                    "FROM platform.accounts a "
                    "LEFT JOIN platform.products p "
                    "  ON p.id = a.product_id AND p.tenant_id = a.tenant_id "
                    "WHERE a.id = :account_id AND a.tenant_id = :tenant_id"
                ),
                {"account_id": account_id, "tenant_id": ctx.tenant_id},
            )
        ).scalar_one_or_none()
        if allowed is False:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Bank details are not allowed for this product.",
            )
    try:
        accepted_entries = bank_accounts_mod.normalise_entries(body.bank_accounts)
        accepted_entries = await bank_accounts_mod.confirm_entries(
            ctx.session,
            tenant_id=ctx.tenant_id,
            account_id=account_id,
            entries=accepted_entries,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    payload: dict[str, object] = {
        "account_id": account_id,
        "bank_accounts": accepted_entries,
    }
    contact_id = await _account_contact_id(
        ctx.session, tenant_id=ctx.tenant_id, account_id=account_id
    )
    try:
        hold = await maybe_hold(
            ctx.session,
            tenant_id=ctx.tenant_id,
            workos_user_id=ctx.principal.user_id,
            action_key="account.bank_details_changed",
            subject_type="account",
            subject_id=account_uid,
            contact_id=int(contact_id) if contact_id is not None else None,
            payload=payload,
            justification=body.justification,
            idempotency_key=idempotency_key(
                "account.bank_details_changed", "account", account_uid, payload
            ),
        )
    except ReviewError as exc:
        raise_review(exc)
    if hold.held:
        return pending_response(hold)
    try:
        return await bank_accounts_mod.set_for_account(
            ctx.session,
            tenant_id=ctx.tenant_id,
            account_id=account_id,
            entries=accepted_entries,
            directory=False,
        )
    except ValueError as exc:  # BankAccountError is one; the message names no digits
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put("/accounts/{account_uid}/model", name="set-account-model", response_model=None)
async def set_account_model(
    account_uid: str,
    account_id: Annotated[int, Depends(book_write_account_id)],
    body: SetAccountModel,
    ctx: FirmContext = Depends(firm_book_write_session),
) -> dict[str, object] | JSONResponse:
    """Associate a model with an account, change it, or clear it.

    This is what makes an account discretionary, and therefore what makes model top-up / model
    withdrawal available on it. 409 while the account has pending model instructions — re-pointing a
    mandate under a live claim would leave the claim describing a mandate the account no longer has;
    400 for a model that isn't the firm's — on the book path (``?tenant=<uid>`` + ``book:write``)
    "the firm" is the TARGET tenant, so another firm's model is refused for ops exactly as it is
    for the firm's own staff.

    Gated ``instructions:write``: the mandate decides where the account's money is managed and
    unlocks the model top-up/withdrawal instructions, so it belongs to the money-movement
    family, not ``contacts:write``."""
    require_permission(ctx.principal, INSTRUCTIONS_WRITE)
    model_name = None
    if body.model_id is not None:
        model_name = (
            await ctx.session.execute(
                text("SELECT name FROM platform.models WHERE id = :i AND tenant_id = :t"),
                {"i": body.model_id, "t": ctx.tenant_id},
            )
        ).scalar_one_or_none()
    payload = {
        "account_id": account_id,
        "model_id": body.model_id,
        "model_name": model_name,
    }
    contact_id = await _account_contact_id(
        ctx.session, tenant_id=ctx.tenant_id, account_id=account_id
    )
    try:
        hold = await maybe_hold(
            ctx.session,
            tenant_id=ctx.tenant_id,
            workos_user_id=ctx.principal.user_id,
            action_key="account.model_changed",
            subject_type="account",
            subject_id=account_uid,
            contact_id=int(contact_id) if contact_id is not None else None,
            payload=payload,
            justification=body.justification,
            idempotency_key=idempotency_key(
                "account.model_changed", "account", account_uid, payload
            ),
        )
    except ReviewError as exc:
        raise_review(exc)
    if hold.held:
        return pending_response(hold)
    try:
        updated = await instructions_mod.set_account_model(
            ctx.session,
            tenant_id=ctx.tenant_id,
            account_id=account_id,
            model_id=body.model_id,
        )
    except ValueError as exc:
        code = status.HTTP_409_CONFLICT if "pending" in str(exc) else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    # Push the mandate to the custodian as a TRACKED ACTION — SEI assigns the model to the
    # account's portfolio (sei.attach_model), Seccl sets the account's model — so changing a model
    # is a run in Actions rather than a silent local edit that never reaches the custodian. Only on
    # SET (clearing is a local removal). `role_ids=None`: the door already authorised this
    # (instructions:write plus the Change model approval), and a refusal to START the push
    # must not unwind the mandate just saved — it re-runs from the account's Change model.
    if body.model_id is not None:
        actor, _roles = await action_runs.caller_ids(ctx.session)
        try:
            await action_service.start_from_entity(
                ctx.session,
                action_key="model.attach",
                tenant_id=ctx.tenant_id,
                entity_type="account",
                entity_uid=account_uid,
                requested_by=actor,
                role_ids=None,
            )
        except action_runs.ActionError as exc:
            logger.warning(
                "set_account_model: could not start model.attach for %s: %s", account_uid, exc
            )
    return updated


@router.put(
    "/accounts/{account_uid}/fee-packages",
    name="set-account-fee-packages",
    response_model=None,
)
async def set_account_fee_packages(
    account_uid: str,
    account_id: Annotated[int, Depends(book_write_account_id)],
    body: SetAccountFeePackages,
    ctx: FirmContext = Depends(firm_book_write_session),
) -> dict[str, object] | JSONResponse:
    """Atomically assign enabled, compatible fee packages from the target firm's catalogue.

    Like the model mandate, this is book-enabled and gated on ``instructions:write``: the fee
    package changes what is taken from the account, not the client's identity. Product/model
    compatibility and tenant ownership are re-checked server-side.

    Four-eye enabled: approvers on **Change fee packages** (``fee.attach``) hold the change for
    a second person (``maybe_hold`` starts a baton review run) rather than writing it at once;
    the door holds on ``account.fee_packages_changed``. Once written — here, or by the worker
    after approval — the packages are attached at the custodian as a tracked ``fee.attach`` run.
    """
    require_permission(ctx.principal, INSTRUCTIONS_WRITE)
    contact_id = await _account_contact_id(
        ctx.session, tenant_id=ctx.tenant_id, account_id=account_id
    )
    payload = {"account_id": account_id, "fee_package_ids": body.fee_package_ids}
    try:
        hold = await maybe_hold(
            ctx.session,
            tenant_id=ctx.tenant_id,
            workos_user_id=ctx.principal.user_id,
            action_key="account.fee_packages_changed",
            subject_type="account",
            subject_id=account_uid,
            contact_id=int(contact_id) if contact_id is not None else None,
            payload=payload,
            justification=body.justification,
            idempotency_key=idempotency_key(
                "account.fee_packages_changed", "account", account_uid, payload
            ),
        )
    except ReviewError as exc:
        raise_review(exc)
    if hold.held:
        return pending_response(hold)
    try:
        updated = await instructions_mod.set_account_fee_packages(
            ctx.session,
            tenant_id=ctx.tenant_id,
            account_id=account_id,
            fee_package_ids=body.fee_package_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    # Attach the packages at the custodian as a TRACKED ACTION, the way the model door pushes a
    # mandate: the save is on the book, and the run in Actions is what reached (or did not reach)
    # SEI / Seccl. Only when packages are set — clearing is a local removal. `role_ids=None`: the
    # door already authorised this, and a refusal to START the push must not unwind the save.
    if body.fee_package_ids:
        actor, _roles = await action_runs.caller_ids(ctx.session)
        try:
            await action_service.start_from_entity(
                ctx.session,
                action_key="fee.attach",
                tenant_id=ctx.tenant_id,
                entity_type="account",
                entity_uid=account_uid,
                requested_by=actor,
                role_ids=None,
            )
        except action_runs.ActionError as exc:
            logger.warning(
                "set_account_fee_packages: could not start fee.attach for %s: %s", account_uid, exc
            )
    return updated


@router.put(
    "/accounts/{account_uid}/regular-movements",
    name="set-account-regular-movements",
    response_model=None,
)
async def set_account_regular_movements(
    account_uid: str,
    account_id: Annotated[int, Depends(book_write_account_id)],
    body: SetAccountRegularMovements,
    ctx: FirmContext = Depends(firm_book_write_session),
) -> dict[str, object] | JSONResponse:
    """Replace this account's regular payments and withdrawals.

    The jsonb columns had a reader and no writer — onboarding wrote an object dialect the
    overview card could not iterate, and nothing else populated them
    (``docs/api-integrations.md`` §4.3). This stores the register that card already renders.
    Empty lists clear. Gated ``instructions:write``: a standing order is money movement. It is
    not a four-eye door: the schedule is sent through ``payment.regular`` /
    ``withdrawal.regular``, and approvers on those hold the send.
    """
    require_permission(ctx.principal, INSTRUCTIONS_WRITE)
    try:
        regular_movements_mod.normalise_movements(body.payments, kind="payment")
        regular_movements_mod.normalise_movements(body.withdrawals, kind="withdrawal")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    try:
        return await regular_movements_mod.set_regular_movements(
            ctx.session,
            tenant_id=ctx.tenant_id,
            account_id=account_id,
            payments=body.payments,
            withdrawals=body.withdrawals,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


# ── beneficiary nominations (0127) ────────────────────────────────────────────────────────────
#
# Their own endpoints rather than a field on the account, for the reason bank details and
# vulnerability have their own doors: a nomination names a THIRD PARTY's identity and share, it is
# added and removed a row at a time, and one form's validation failure must not half-save another's.
#
# There is no PUT. A nomination is replaced by removing it and adding the new one, which is what an
# expression-of-wish form actually is — a member signs a fresh one, and an edit-in-place would let a
# share move under a name without the audit row saying a new nomination was made.


class AddBeneficiary(BaseModel):
    """One nomination. `proportion` is a percentage of the fund (`cam_percentageoffund`)."""

    name: str
    proportion: float
    title: str | None = None
    relationship: str | None = None
    nominated_on: date | None = None
    address: dict[str, Any] | None = None
    note: str | None = None
    justification: str | None = None


@router.get("/accounts/{account_uid}/beneficiaries", name="list-account-beneficiaries")
async def list_account_beneficiaries(
    account_id: Annotated[int, Depends(book_account_id)],
    session: AsyncSession = Depends(book_session),
) -> list[dict[str, Any]]:
    """The account's beneficiary nominations, largest share first.

    RLS-scoped: a 404 on the ACCOUNT means it is absent or not visible to this caller, and the
    nominations themselves carry the same `tenant_lineage() OR contact_scope()` policy the rest of
    the client's record does — so a client reading their own account sees who they nominated.
    """
    return await beneficiaries_mod.list_for_account(session, account_id=account_id)


@router.post(
    "/accounts/{account_uid}/beneficiaries",
    name="add-account-beneficiary",
    status_code=status.HTTP_201_CREATED,
    response_model=None,
)
async def add_account_beneficiary(
    account_uid: str,
    body: AddBeneficiary,
    account_id: Annotated[int, Depends(book_write_account_id)],
    ctx: FirmContext = Depends(firm_book_write_session),
) -> dict[str, Any] | JSONResponse:
    """Nominate a beneficiary against a pension account.

    409 when the set would exceed 100% of the fund — it names how much is left, because "that is
    too much" without the number is a message an adviser has to do arithmetic to act on. Falling
    SHORT is not refused: a set is built a row at a time, so demanding 100 would make the first
    insert fail.

    Not a four-eye door: the nomination is written here, and sending the set to the scheme
    administrator is ``beneficiary.replace``, whose approvers hold that send.

    Gated ``contacts:write`` and not the money family: a nomination is a fact about the person's
    wishes, not a rail money moves along.
    """
    require_permission(ctx.principal, CONTACTS_WRITE)
    fields = body.model_dump(mode="json", exclude={"justification"})
    try:
        return await beneficiaries_mod.add(
            ctx.session,
            tenant_id=ctx.tenant_id,
            account_id=account_id,
            created_by=ctx.principal.user_id,
            **fields,
        )
    except beneficiaries_mod.BeneficiaryConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.delete(
    "/accounts/{account_uid}/beneficiaries/{beneficiary_uid}",
    name="remove-account-beneficiary",
    response_model=None,
)
async def remove_account_beneficiary(
    account_uid: str,
    beneficiary_uid: str,
    account_id: Annotated[int, Depends(book_write_account_id)],
    ctx: FirmContext = Depends(firm_book_write_session),
) -> dict[str, Any] | JSONResponse:
    """Withdraw a nomination.

    A hard delete: a superseded expression of wish is not history to keep but a statement the
    member has withdrawn, and a retained row is one careless read from paying out to somebody they
    removed. The audit trigger keeps the deletion, so who removed what is still answerable.

    `account_id` SCOPES THE DELETE and is passed, not merely resolved: the URL naming a book the
    caller may write is only a control if the statement applies it, and this session is
    `service_role`, so RLS cannot refuse a uid belonging to another client's account.
    """
    require_permission(ctx.principal, CONTACTS_WRITE)
    try:
        await beneficiaries_mod.remove(
            ctx.session, tenant_id=ctx.tenant_id, account_id=account_id, uid=beneficiary_uid
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"removed": beneficiary_uid, "account_id": account_id}


@router.get("/contacts/{contact_uid}/journeys", name="list-contact-journeys")
async def list_contact_journeys(
    contact_id: Annotated[int, Depends(book_contact_id)],
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(book_session),
) -> dict[str, Any]:
    """The journeys this client is on.

    ``cases:read`` for the firm; an operator in the firm's workspace
    (``tenant_portal:read``) sees the same list. RLS still limits the rows to the
    caller's book.
    """
    require_any_permission(principal, CASES_READ, TENANT_PORTAL_READ)
    return {"journeys": await client_journeys.list_for_contact(session, contact_id)}


@router.get("/contacts/{contact_uid}/journeys/{execution_uid}", name="get-contact-journey")
async def get_contact_journey(
    execution_uid: str,
    contact_id: Annotated[int, Depends(book_contact_id)],
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(book_session),
) -> dict[str, Any]:
    """One journey as this client answered it: their forms, the letter, who else is on it.

    A 404 is either no such journey or this client is not a party to it.
    """
    require_any_permission(principal, CASES_READ, TENANT_PORTAL_READ)
    detail = await client_journeys.get_for_contact(session, contact_id, execution_uid)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Journey not found")
    return detail
