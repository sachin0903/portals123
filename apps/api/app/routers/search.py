"""Cross-entity search — the one backend behind every portal's search bar.

Scoped **by construction**, not by filtering afterwards:

- It runs on ``tenant_session`` (the ``authenticated`` RLS role) with the caller's OWN claims, so
  tenant isolation and book scoping are the database's answer, not this module's. Nothing here
  fabricates or widens claims — contrast ``app/render/resolve.py``, whose injected
  ``contacts:read_all`` is exactly the escalation this endpoint must not repeat.
- Each entity group is additionally gated on the permission its own detail endpoint requires, so a
  hit can never appear for something the caller could not then open. Where the detail endpoint is
  RLS-only (clients, documents, reviews) the group is RLS-only too; where it is gated (tasks,
  cases) the same gate applies here. Reviews are open to any staff member: who may decide one is
  the role on the action, not a permission. Client accounts/transactions remain RLS-scoped;
  holdings use the same lake display seam as their page; insights use the same audience eligibility
  as their detail.

Each searched table carries a generated ``_search`` column (``0211_search_column``): its
searchable fields folded by ``search.normalise`` (``0182_spotlight_search``), trigram GIN-indexed.
Exact identifiers and field prefixes rank first; fuzzy word matching against ``_search`` starts at
three characters, while long task/message/review prose uses ``simple`` full-text search.
A hard per-group cap and short ``statement_timeout`` remain as backstops.

Deliberately **not** returning hrefs: the same entity lives at a different path in each portal, so
the API returns typed handles and each portal maps ``type`` → route.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import content as content_svc
from app import ratelimit
from app.auth import Principal, get_current_principal, is_system, require_permission
from app.config import settings
from app.db import admin_session
from app.db import target_tenant_session as tenant_session
from app.journey_scope import IN_BOOK_SQL, sees_whole_firm
from app.permissions import (
    CASES_READ,
    MESSAGES_READ,
    TASKS_READ,
    TENANTS_READ,
    Permission,
    satisfies,
)

router = APIRouter(prefix="/search", tags=["search"])

# A one-character query matches most of a firm's book, which is neither useful nor cheap without an
# index. The portals don't query below this either — kept in one place so both agree.
MIN_QUERY_LENGTH = 2
DEFAULT_GROUP_LIMIT = 5
MAX_GROUP_LIMIT = 20
# Even indexed interactive search needs a firm upper bound if a planner or data distribution is bad.
STATEMENT_TIMEOUT = "3s"
# The Postgres timeout above cannot govern DuckDB. Holdings are useful but must not hold every other
# group hostage if a tenant's lake connection is cold or unhealthy.
HOLDINGS_TIMEOUT_SECONDS = 2.5
SearchScope = Literal[
    "clients",
    "accounts",
    "documents",
    "tasks",
    "journeys",
    "transactions",
    "messages",
    "reviews",
]
_SEARCH_SCOPES: tuple[SearchScope, ...] = (
    "clients",
    "accounts",
    "documents",
    "tasks",
    "journeys",
    "transactions",
    "messages",
    "reviews",
)

_STAFF_GROUPS: tuple[tuple[str, str], ...] = (
    ("client", "Clients"),
    ("account", "Accounts"),
    ("document", "Documents"),
    ("task", "Tasks"),
    ("case", "Journeys"),
    ("transaction", "Transactions"),
    ("message", "Messages"),
    ("review", "Reviews"),
)

logger = logging.getLogger(__name__)


class SearchHit(BaseModel):
    """One match. ``id`` is its stable selection identity (normally the record's public uid).
    ``target`` is set only when the result has no client-facing detail route of its own: holdings
    and transactions point at their owning account's public uid. A URL never carries a serial id."""

    type: str
    id: str
    target: str | None = None
    title: str
    subtitle: str | None = None
    meta: str | None = None
    # Staff search assigns one order across entity types. Other search surfaces leave this null;
    # their UIs retain the API's existing group order.
    rank: int | None = None


class SearchGroup(BaseModel):
    type: str
    label: str
    hits: list[SearchHit]


class SearchResults(BaseModel):
    query: str
    groups: list[SearchGroup]
    # True when a group was capped, so the UI can say the list is only the first few.
    truncated: bool


def _pattern(q: str) -> str:
    r"""``q`` as an ILIKE pattern with the wildcards neutralised, so a user typing ``%`` or ``_``
    searches for that character instead of matching everything. Paired with ``ESCAPE '\'``."""
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _prefix_pattern(q: str) -> str:
    """``q`` as a literal LIKE prefix, using the same escaping contract as ``_pattern``."""
    return _pattern(q)[1:]


def _phone_patterns(q: str) -> list[str]:
    """Digit-only search patterns, including the equivalent UK national/international spelling."""
    digits = re.sub(r"\D", "", q)
    if len(digits) < MIN_QUERY_LENGTH:
        return []
    variants = {digits}
    if digits.startswith("0") and len(digits) > 1:
        variants.add(f"44{digits[1:]}")
    elif digits.startswith("44") and len(digits) > 2:
        variants.add(f"0{digits[2:]}")
    return [f"%{value}%" for value in sorted(variants)]


def _holds(principal: Principal, permission: Permission) -> bool:
    """Boolean form of ``require_permission`` (system bypasses; write implies read) — search needs
    to *omit* a group, not 403 the whole request, when the caller lacks its permission."""
    return is_system(principal) or satisfies(set(principal.permissions), permission.slug)


def _hits(kind: str, rows: Sequence[Any], limit: int) -> list[SearchHit]:
    return [
        SearchHit(
            type=kind,
            id=str(r["id"]),
            target=None if r.get("target") is None else str(r["target"]),
            title=str(r["title"]),
            subtitle=None if r["subtitle"] is None else str(r["subtitle"]),
            meta=None if r["meta"] is None else str(r["meta"]),
            rank=None if r.get("relevance_rank") is None else int(r["relevance_rank"]),
        )
        for r in rows[:limit]
    ]


def _empty(q: str) -> SearchResults:
    return SearchResults(query=q, groups=[], truncated=False)


async def _prepare(session: AsyncSession) -> None:
    """Bound the cost of an unindexed scan for this transaction only."""
    await session.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'"))
    # PostgreSQL's default word threshold (0.6) rejects ordinary one-letter misspellings such as
    # "Margret" for "Margaret". Keep the index-backed `<%` operator, with a useful typo threshold.
    await session.execute(text("SET LOCAL pg_trgm.word_similarity_threshold = 0.3"))


_STAFF_SEARCH_SQL = r"""
WITH candidates AS (
  SELECT 'client'::text AS type, 1 AS group_order, c.uid AS id, NULL::text AS target,
         COALESCE(NULLIF(btrim(concat_ws(' ', c.first_name, c.surname)), ''), c.name, c.uid)
           AS title,
         c.email AS subtitle,
         CASE c.type::text WHEN 'organisation' THEN 'Organisation' END AS meta,
         CASE
           WHEN lower(c.uid) = :q_norm OR lower(COALESCE(c.email, '')) = :q_norm
             OR regexp_replace(COALESCE(c.mobile, ''), '[^0-9]', '', 'g')
                  = ANY(CAST(:phone_exact AS text[]))
             OR regexp_replace(COALESCE(c.phone, ''), '[^0-9]', '', 'g')
                  = ANY(CAST(:phone_exact AS text[])) THEN 0
           WHEN search.has_prefix(
                  :prefix, c.uid, c.name, c.first_name, c.surname, c.email
                ) THEN 1
           WHEN regexp_replace(COALESCE(c.mobile, ''), '[^0-9]', '', 'g')
                  LIKE ANY(CAST(:phone_prefixes AS text[]))
             OR regexp_replace(COALESCE(c.phone, ''), '[^0-9]', '', 'g')
                  LIKE ANY(CAST(:phone_prefixes AS text[])) THEN 1
           ELSE 2
         END AS rank_band,
         CASE WHEN :fuzzy THEN word_similarity(:q_norm, c._search) ELSE 0 END AS fuzzy_score,
         0::real AS prose_score, c.updated_at AS recency
    FROM crm.contacts c
   WHERE :include_clients AND c.deleted_at IS NULL
     AND (
       lower(c.uid) = :q_norm OR lower(COALESCE(c.email, '')) = :q_norm
       OR regexp_replace(COALESCE(c.mobile, ''), '[^0-9]', '', 'g')
            = ANY(CAST(:phone_exact AS text[]))
       OR regexp_replace(COALESCE(c.phone, ''), '[^0-9]', '', 'g')
            = ANY(CAST(:phone_exact AS text[]))
       OR search.has_prefix(:prefix, c.uid, c.name, c.first_name, c.surname, c.email)
       OR regexp_replace(COALESCE(c.mobile, ''), '[^0-9]', '', 'g')
            LIKE ANY(CAST(:phone_prefixes AS text[]))
       OR regexp_replace(COALESCE(c.phone, ''), '[^0-9]', '', 'g')
            LIKE ANY(CAST(:phone_prefixes AS text[]))
       OR (:fuzzy AND :q_norm <% c._search)
     )

  UNION ALL
  SELECT 'account', 2, a.uid, NULL::text,
         COALESCE(NULLIF(a.name, ''), p.name, p.product_type::text, a.uid),
         COALESCE(p.name, p.product_type::text),
         initcap(replace(a.status::text, '_', ' ')),
         CASE WHEN lower(a.uid) = :q_norm
                    OR lower(COALESCE(a.platform_identifier, '')) = :q_norm THEN 0
              WHEN search.has_prefix(
                     :prefix, a.uid, a.name, a.platform_identifier, p.uid, p.code, p.name
                   ) THEN 1 ELSE 2 END,
         CASE WHEN :fuzzy THEN greatest(
           word_similarity(:q_norm, a._search), word_similarity(:q_norm, p._search)
         ) ELSE 0 END,
         0::real, COALESCE(a.opened_at, a.updated_at)
    FROM platform.accounts a
    LEFT JOIN platform.products p ON p.id = a.product_id
   WHERE :include_accounts AND (
     lower(a.uid) = :q_norm OR lower(COALESCE(a.platform_identifier, '')) = :q_norm
     OR search.has_prefix(
          :prefix, a.uid, a.name, a.platform_identifier, p.uid, p.code, p.name
        )
     OR (:fuzzy AND (:q_norm <% a._search OR :q_norm <% p._search))
   )

  UNION ALL
  SELECT 'document', 3, d.uid, NULL::text, COALESCE(d.name, d.uid), d.document_type,
         to_char(d.created_at, 'DD Mon YYYY'),
         CASE WHEN lower(d.uid) = :q_norm THEN 0
              WHEN search.has_prefix(:prefix, d.uid, d.name, d.document_type, d.source)
                THEN 1 ELSE 2 END,
         CASE WHEN :fuzzy THEN word_similarity(:q_norm, d._search) ELSE 0 END,
         0::real, d.created_at
    FROM documents.documents d
   WHERE :include_documents AND d.status <> 'archived' AND (
     lower(d.uid) = :q_norm
     OR search.has_prefix(:prefix, d.uid, d.name, d.document_type, d.source)
     OR (:fuzzy AND :q_norm <% d._search)
   )

  UNION ALL
  SELECT 'task', 4, t.uid, NULL::text, t.title, t.description,
         initcap(replace(t.status, '_', ' ')),
         CASE WHEN lower(t.uid) = :q_norm OR lower(COALESCE(t.source_ref, '')) = :q_norm THEN 0
              WHEN search.has_prefix(
                     :prefix, t.uid, t.title, t.category, t.subject_id, t.source_ref
                   ) THEN 1
              WHEN :fuzzy AND :q_norm <% t._search THEN 2 ELSE 3 END,
         CASE WHEN :fuzzy THEN word_similarity(:q_norm, t._search) ELSE 0 END,
         CASE WHEN :fuzzy THEN ts_rank(
           to_tsvector('simple', COALESCE(t.description, '')), plainto_tsquery('simple', :q_norm)
         ) ELSE 0 END,
         t.updated_at
    FROM tasks.tasks t
   WHERE :include_tasks AND (
     lower(t.uid) = :q_norm OR lower(COALESCE(t.source_ref, '')) = :q_norm
     OR search.has_prefix(:prefix, t.uid, t.title, t.category, t.subject_id, t.source_ref)
     OR (:fuzzy AND (
       :q_norm <% t._search
       OR to_tsvector('simple', COALESCE(t.description, ''))
            @@ plainto_tsquery('simple', :q_norm)
     ))
   )

  UNION ALL
  SELECT 'case', 5, e.uid, NULL::text, f.name,
         COALESCE(NULLIF(btrim(concat_ws(' ', c.first_name, c.surname)), ''), c.name),
         initcap(replace(e.status, '_', ' ')),
         CASE WHEN lower(e.uid) = :q_norm THEN 0
              WHEN search.has_prefix(
                     :prefix, e.uid, f.uid, f.code, f.name, c.name, c.first_name, c.surname
                   ) THEN 1 ELSE 2 END,
         CASE WHEN :fuzzy THEN greatest(
           word_similarity(:q_norm, e._search),
           word_similarity(:q_norm, f._search),
           word_similarity(:q_norm, c._search)
         ) ELSE 0 END,
         0::real, e.created_at
    FROM flows.executions e
    JOIN flows.flows f ON f.id = e.flow_id
    LEFT JOIN crm.applications a ON a.id = e.crm_application_id
    LEFT JOIN crm.contacts c ON c.id = e.subject_id AND e.subject_type = 'contact'
   WHERE :include_journeys AND __JOURNEY_BOOK__ AND (
     lower(e.uid) = :q_norm
     OR search.has_prefix(
          :prefix, e.uid, f.uid, f.code, f.name, c.name, c.first_name, c.surname
        )
     OR (:fuzzy AND (:q_norm <% e._search OR :q_norm <% f._search OR :q_norm <% c._search))
   )

  UNION ALL
  SELECT 'transaction', 6, t.source_system::text || ':' || t.source_transaction_id, a.uid,
         COALESCE(NULLIF(t.source_subtype, ''), NULLIF(t.source_type, ''),
                  initcap(replace(t.type::text, '_', ' '))),
         NULLIF(concat_ws(' · ', t.source_transaction_id, NULLIF(a.name, ''),
                          NULLIF(t.asset_name, ''), NULLIF(t.isin, '')), ''),
         concat_ws(' · ',
           to_char(COALESCE(t.transaction_date, t.value_date, t.posted_at::date,
                            t.synced_at::date), 'DD Mon YYYY'),
           CASE WHEN COALESCE(t.currency, 'GBP') = 'GBP'
                THEN (CASE WHEN t.amount < 0 THEN '-£' ELSE '£' END)
                     || to_char(abs(t.amount), 'FM999G999G999G990D00')
                ELSE t.currency || ' ' || to_char(t.amount, 'FM999G999G999G990D00') END),
         CASE WHEN lower(t.source_transaction_id) = :q_norm THEN 0 ELSE 1 END,
         0::real,
         0::real, COALESCE(t.transaction_date::timestamptz, t.value_date::timestamptz,
                           t.posted_at, t.synced_at)
    FROM platform.transactions t
    JOIN platform.accounts a ON a.id = t.account_id
   WHERE :include_transactions
     AND lower(t.source_transaction_id) LIKE :prefix ESCAPE '\'

  UNION ALL
  SELECT 'message', 7, c.uid, c.uid,
         COALESCE(NULLIF(btrim(concat_ws(' ', ct.first_name, ct.surname)), ''), ct.name, ct.uid),
         left(best.body, 160), 'Conversation',
         CASE WHEN lower(c.uid) = :q_norm OR lower(COALESCE(ct.email, '')) = :q_norm THEN 0
              WHEN search.has_prefix(
                     :prefix, c.uid, ct.name, ct.first_name, ct.surname, ct.email
                   ) THEN 1
              WHEN :fuzzy AND :q_norm <% ct._search THEN 2 ELSE 3 END,
         CASE WHEN :fuzzy THEN word_similarity(:q_norm, ct._search) ELSE 0 END,
         COALESCE(best.prose_score, 0), COALESCE(best.created_at, c.updated_at)
    FROM messaging.conversations c
    JOIN crm.contacts ct ON ct.id = c.contact_id
    LEFT JOIN LATERAL (
      SELECT m.body, m.created_at,
             ts_rank(to_tsvector('simple', COALESCE(m.body, '')),
                     plainto_tsquery('simple', :q_norm)) AS prose_score
        FROM messaging.messages m
       WHERE m.conversation_id = c.id AND m.group_id IS NULL AND :fuzzy
         AND to_tsvector('simple', COALESCE(m.body, ''))
               @@ plainto_tsquery('simple', :q_norm)
       ORDER BY prose_score DESC, m.created_at DESC
       LIMIT 1
    ) best ON true
   WHERE :include_messages AND (
     lower(c.uid) = :q_norm OR lower(COALESCE(ct.email, '')) = :q_norm
     OR search.has_prefix(:prefix, c.uid, ct.name, ct.first_name, ct.surname, ct.email)
     OR (:fuzzy AND (:q_norm <% ct._search OR best.body IS NOT NULL))
   )

  UNION ALL
  SELECT 'message', 7, g.uid, 'group:' || g.uid, g.name, left(best.body, 160), 'Group',
         CASE WHEN lower(g.uid) = :q_norm THEN 0
              WHEN search.has_prefix(:prefix, g.uid, g.name) THEN 1
              WHEN :fuzzy AND :q_norm <% g._search THEN 2 ELSE 3 END,
         CASE WHEN :fuzzy THEN word_similarity(:q_norm, g._search) ELSE 0 END,
         COALESCE(best.prose_score, 0), COALESCE(best.created_at, g.updated_at)
    FROM messaging.groups g
    LEFT JOIN LATERAL (
      SELECT m.body, m.created_at,
             ts_rank(to_tsvector('simple', COALESCE(m.body, '')),
                     plainto_tsquery('simple', :q_norm)) AS prose_score
        FROM messaging.messages m
       WHERE m.group_id = g.id AND :fuzzy
         AND to_tsvector('simple', COALESCE(m.body, ''))
               @@ plainto_tsquery('simple', :q_norm)
       ORDER BY prose_score DESC, m.created_at DESC
       LIMIT 1
    ) best ON true
   WHERE :include_messages AND (
     lower(g.uid) = :q_norm OR search.has_prefix(:prefix, g.uid, g.name)
     OR (:fuzzy AND (
       :q_norm <% g._search OR best.body IS NOT NULL
     ))
   )

  UNION ALL
  SELECT 'review', 8, r.uid, NULL::text,
         'RV-' || r.case_no || ' · ' || initcap(replace(r.action_key, '_', ' ')),
         NULLIF(concat_ws(' · ',
           COALESCE(NULLIF(btrim(concat_ws(' ', c.first_name, c.surname)), ''), c.name),
           COALESCE(NULLIF(a.name, ''), p.name, a.uid,
                    NULLIF(ai.name, ''), pi.name, ai.uid)), ''),
         CASE
           WHEN r.state_type = 'paused' THEN 'Pending'
           WHEN r.state_type = 'completed' THEN 'Approved'
           WHEN r.state_type = 'cancelled' AND r.state_name = 'Rejected' THEN 'Rejected'
           WHEN r.state_type = 'failed' THEN 'Needs attention'
           ELSE initcap(r.state_type)
         END,
         CASE WHEN lower(r.uid) = :q_norm OR lower('rv-' || r.case_no) = :q_norm
                    OR r.case_no::text = :q_norm THEN 0
              WHEN search.has_prefix(
                     :prefix, r.uid, r.case_no::text, 'rv-' || r.case_no::text,
                     r.action_key, r.meta ->> 'event_type', r.subject_kind, r.subject_uid,
                     c.name, c.first_name, c.surname, a.name, ai.name
                   ) THEN 1
              WHEN :fuzzy AND (
                :q_norm <% r._search OR :q_norm <% c._search
                OR :q_norm <% search.normalise(a.name, ai.name)
              ) THEN 2 ELSE 3 END,
         CASE WHEN :fuzzy THEN greatest(
           word_similarity(:q_norm, r._search),
           word_similarity(:q_norm, c._search),
           word_similarity(:q_norm, search.normalise(a.name, ai.name))
         ) ELSE 0 END,
         CASE WHEN :fuzzy THEN ts_rank(
           to_tsvector('simple',
             COALESCE(r.justification, '') || ' ' || COALESCE(r.decision_note, '')),
           plainto_tsquery('simple', :q_norm)
         ) ELSE 0 END,
         r.updated_at
    FROM baton.runs r
    LEFT JOIN crm.contacts c ON c.id = r.contact_id
    LEFT JOIN platform.accounts a
      ON a.tenant_id = r.tenant_id AND (
        (r.subject_kind = 'account' AND a.uid = r.subject_uid)
        OR ((r.params ->> 'account_id') ~ '^[0-9]+$'
            AND a.id = (r.params ->> 'account_id')::int)
      )
    LEFT JOIN platform.products p ON p.id = a.product_id
    LEFT JOIN platform.instructions inst
      ON inst.tenant_id = r.tenant_id
     AND (inst.uid = r.subject_uid OR inst.uid = r.params ->> 'instruction_uid')
    LEFT JOIN platform.accounts ai
      ON ai.id = inst.account_id AND ai.tenant_id = r.tenant_id
    LEFT JOIN platform.products pi ON pi.id = ai.product_id
   WHERE :include_reviews AND r.parent_run_id IS NULL AND r.case_no IS NOT NULL AND (
     lower(r.uid) = :q_norm OR lower('rv-' || r.case_no) = :q_norm
     OR r.case_no::text = :q_norm
     OR search.has_prefix(
          :prefix, r.uid, r.case_no::text, 'rv-' || r.case_no::text,
          r.action_key, r.meta ->> 'event_type', r.subject_kind, r.subject_uid,
          c.name, c.first_name, c.surname, a.name, ai.name
        )
     OR (:fuzzy AND (
       :q_norm <% r._search OR :q_norm <% c._search
       OR :q_norm <% search.normalise(a.name, ai.name)
       OR to_tsvector('simple',
            COALESCE(r.justification, '') || ' ' || COALESCE(r.decision_note, ''))
            @@ plainto_tsquery('simple', :q_norm)
     ))
   )
), ranked AS (
  SELECT candidates.*,
         row_number() OVER (
           PARTITION BY type
           ORDER BY rank_band, fuzzy_score DESC, prose_score DESC, recency DESC NULLS LAST, id
         ) AS rn
    FROM candidates
), relevance_ordered AS (
  SELECT ranked.*,
         row_number() OVER (
           ORDER BY rank_band, fuzzy_score DESC, prose_score DESC,
                    recency DESC NULLS LAST, group_order, id
         ) AS relevance_rank
    FROM ranked
   WHERE rn <= :n
)
SELECT type, id, target, title, subtitle, meta, rn, relevance_rank
  FROM relevance_ordered
 ORDER BY relevance_rank
""".replace("__JOURNEY_BOOK__", IN_BOOK_SQL)


async def _staff_search(
    session: AsyncSession,
    principal: Principal,
    *,
    q: str,
    limit: int,
    scope: SearchScope | None,
) -> SearchResults:
    """One permission-pruned statement over the eight staff source-table branches."""
    requested = {scope} if scope else set(_SEARCH_SCOPES)
    authorised = {
        "clients": True,
        "accounts": True,
        "documents": True,
        "tasks": _holds(principal, TASKS_READ),
        "journeys": _holds(principal, CASES_READ),
        "transactions": True,
        "messages": _holds(principal, MESSAGES_READ),
        "reviews": True,
    }
    enabled = {kind for kind in requested if authorised[kind]}
    phone_exact = [pattern.strip("%") for pattern in _phone_patterns(q)]
    rows = (
        (
            await session.execute(
                text(_STAFF_SEARCH_SQL),
                {
                    "q_norm": q.lower(),
                    "prefix": _prefix_pattern(q.lower()),
                    "phone_exact": phone_exact,
                    "phone_prefixes": [f"{value}%" for value in phone_exact],
                    # Treat punctuation literally. Besides preserving LIKE escaping, this avoids
                    # trigram interpreting a typed wildcard-like token (`c_le`) as a typo of Cole.
                    "fuzzy": len(q) >= 3
                    and all(character.isalnum() or character.isspace() for character in q),
                    "n": limit + 1,
                    # The journeys branch is the caller's book, the same rule as GET /applications.
                    "see_all": sees_whole_firm(principal),
                    **{f"include_{kind}": kind in enabled for kind in _SEARCH_SCOPES},
                },
            )
        )
        .mappings()
        .all()
    )
    by_type: dict[str, list[Any]] = {}
    for row in rows:
        by_type.setdefault(str(row["type"]), []).append(row)
    groups: list[SearchGroup] = []
    truncated = False
    for kind, label in _STAFF_GROUPS:
        kind_rows = by_type.get(kind, [])
        if kind_rows:
            truncated = truncated or len(kind_rows) > limit
            groups.append(SearchGroup(type=kind, label=label, hits=_hits(kind, kind_rows, limit)))
    return SearchResults(query=q, groups=groups, truncated=truncated)


@router.get("", name="search")
async def search(
    q: str = Query(default="", description="The search term (min 2 characters)."),
    limit: int = Query(default=DEFAULT_GROUP_LIMIT, ge=1, le=MAX_GROUP_LIMIT),
    # Annotated, NOT `= Query(...)`: the latter makes the DEFAULT the `Query(...)` sentinel, so a
    # direct call to this coroutine (every test in tests/test_search.py does exactly that) binds a
    # `Query` object into the SQL and psycopg refuses to adapt it. The same trap
    # `routers/notifications.py` and `routers/aml.py` each record.
    contact_id: Annotated[
        int | None, Query(description="Client portal only: narrow to the active linked contact.")
    ] = None,
    scope: Annotated[
        SearchScope | None,
        Query(description="Platform portal only: search one entity group."),
    ] = None,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> SearchResults:
    """Search the caller's reachable records; clients also get their financial world and insights.

    Every row comes back through RLS under the caller's claims, so a book-scoped adviser searches
    their book, a client searches only their active person's accounts, holdings, transactions,
    documents, published insights and action items, and another firm's data is unreachable.
    Below ``MIN_QUERY_LENGTH`` characters the result is empty (not an error) — the bar is still
    being typed into.

    ``contact_id`` is the client portal's active-contact selector, and it applies to the CLIENT
    branches only. A login linked to more than one client picks one in the nav switcher and every
    other read on that portal narrows to it; search did not, so searching while viewing client A
    offered client B's documents and action items with nothing on the hit to say whose they were.
    A staff caller never sends it and it is ignored for them: their groups are book-wide by design.
    """
    q = q.strip()
    if len(q) < MIN_QUERY_LENGTH:
        return _empty(q)

    # Per user: a keystroke each from the portals' ⌘K bars is the honest peak, and the query below
    # fans out across every group — a scripted session or a retry loop should not get to run it
    # at machine speed.
    await ratelimit.enforce(
        ratelimit.SEARCH,
        ratelimit.per_user_limits(
            ratelimit.SEARCH,
            user_id=principal.user_id,
            per_minute=settings.rate_limit_search_per_minute,
        ),
        detail="Too many searches. Wait a moment and try again.",
    )

    await _prepare(session)
    # `n` is limit+1 so a full page tells us there is more without a second count query.
    params: dict[str, object] = {
        "p": _pattern(q),
        "phone_patterns": _phone_patterns(q),
        "n": limit + 1,
        "cid": contact_id,
        "see_all": sees_whole_firm(principal),
    }
    # Is this a client end-user? Derived in SQL from the caller's own claims (never a request
    # parameter); it decides whether the "my action items" branch or the staff task list applies,
    # and whether the firm-wide Clients group is offered at all.
    #
    # Belt AND braces, exactly as `0004_portal_scope.sql` writes its own policies
    # (`auth.portal() = 'client' OR auth.role() = 'client'`): `auth.portal()` is NULL whenever the
    # tenant's `auth.tenant_environments` classification is missing, and on a NULL portal claim a
    # client session would otherwise fall through to the staff branches.
    portal_scope = (
        (await session.execute(text("SELECT auth.portal() AS portal, auth.role() AS role")))
        .mappings()
        .one()
    )
    is_client = portal_scope["portal"] == "client" or portal_scope["role"] == "client"
    if not is_client:
        return await _staff_search(
            session,
            principal,
            q=q,
            limit=limit,
            scope=scope,
        )

    groups: list[SearchGroup] = []
    truncated = False

    def _add(kind: str, label: str, rows: Sequence[Any]) -> None:
        nonlocal truncated
        if not rows:
            return
        truncated = truncated or len(rows) > limit
        groups.append(SearchGroup(type=kind, label=label, hits=_hits(kind, rows, limit)))

    # ---- The client's financial world. These groups deliberately sit on the client branch:
    # the staff portal's global search has its own record routes and can grow them independently,
    # while this surface promises that every hit belongs to the active person in the client
    # switcher. RLS remains the first boundary; the `cid` clauses only narrow it further. ----
    if is_client:
        account_scope = (
            " AND (a.contact_id = CAST(:cid AS int) "
            "      OR EXISTS (SELECT 1 FROM crm.account_contacts ac "
            "                 WHERE ac.tenant_id = a.tenant_id AND ac.account_id = a.id "
            "                   AND ac.contact_id = CAST(:cid AS int) "
            "                   AND ac.role = 'registered_contact')) "
            if contact_id is not None
            else ""
        )
        accounts = (
            (
                await session.execute(
                    text(
                        "SELECT a.uid AS id, NULL::text AS target, "
                        "       COALESCE(NULLIF(a.name, ''), p.name, p.product_type::text, a.uid) "
                        "         AS title, "
                        "       COALESCE(p.name, p.product_type::text) AS subtitle, "
                        "       initcap(replace(a.status::text, '_', ' ')) AS meta "
                        "FROM platform.accounts a "
                        "LEFT JOIN platform.products p ON p.id = a.product_id "
                        "WHERE (a.name ILIKE :p ESCAPE '\\' OR a.uid ILIKE :p ESCAPE '\\' "
                        "       OR p.name ILIKE :p ESCAPE '\\' "
                        "       OR p.code ILIKE :p ESCAPE '\\' "
                        "       OR p.product_type::text ILIKE :p ESCAPE '\\') "
                        + account_scope
                        + "ORDER BY a.opened_at DESC NULLS LAST, a.id DESC LIMIT :n"
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        _add("account", "Accounts", accounts)

        # Holdings live in the firm's conformed lake rather than an OLTP table. This uses the same
        # RLS-scoped account resolver and display fallback as GET /crm/holdings, but a search-shaped
        # reader pushes the query and cap into DuckDB instead of fetching the complete portfolio.
        from app.routers import crm as crm_router

        try:
            holding_items = await asyncio.wait_for(
                crm_router.search_holdings(
                    session,
                    contact_id=contact_id,
                    q=q,
                    limit=limit + 1,
                ),
                timeout=HOLDINGS_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            # Partial search is preferable to an eternal spinner. The common path is now capped in
            # DuckDB and completes quickly; this branch is the safety net for an unhealthy lake.
            logger.warning("holdings search exceeded %.1fs", HOLDINGS_TIMEOUT_SECONDS)
            holding_items = []
        holding_rows: list[dict[str, object | None]] = []
        for index, holding in enumerate(holding_items):
            account_uid = holding.get("account_uid")
            asset_name = holding.get("asset_name") or holding.get("isin") or "Holding"
            details = [
                str(value) for value in (holding.get("account_name"), holding.get("isin")) if value
            ]
            raw_value = holding.get("value")
            value = (
                f"£{float(raw_value):,.2f}"
                if isinstance(raw_value, int | float | Decimal)
                else None
            )
            # The instrument key is shared by every account that holds it. Keying the hit on
            # that alone made two copies of one fund the same row, and the palette's list
            # threw and stayed on "Searching…".
            asset_key = holding.get("asset_id") or holding.get("isin") or index
            holding_rows.append(
                {
                    "id": (
                        f"{account_uid}:{asset_key}"
                        if isinstance(account_uid, str) and account_uid
                        else str(asset_key)
                    ),
                    "target": account_uid,
                    "title": str(asset_name),
                    "subtitle": " · ".join(details) or None,
                    "meta": value,
                }
            )
        _add("holding", "Holdings", holding_rows)

        transaction_scope = (
            " AND (a.contact_id = CAST(:cid AS int) "
            "      OR EXISTS (SELECT 1 FROM crm.account_contacts ac "
            "                 WHERE ac.tenant_id = a.tenant_id AND ac.account_id = a.id "
            "                   AND ac.contact_id = CAST(:cid AS int) "
            "                   AND ac.role = 'registered_contact')) "
            if contact_id is not None
            else ""
        )
        transactions = (
            (
                await session.execute(
                    text(
                        "SELECT t.source_system || ':' || t.source_transaction_id AS id, "
                        "       a.uid AS target, "
                        "       COALESCE(NULLIF(t.source_subtype, ''), NULLIF(t.source_type, ''), "
                        "         initcap(replace(t.type::text, '_', ' '))) AS title, "
                        "       NULLIF(concat_ws(' · ', NULLIF(a.name, ''), "
                        "         NULLIF(t.asset_name, ''), NULLIF(t.isin, '')), '') AS subtitle, "
                        "       concat_ws(' · ', "
                        "         to_char(COALESCE(t.transaction_date, t.value_date, "
                        "                  t.posted_at::date, t.synced_at::date), 'DD Mon YYYY'), "
                        "         CASE WHEN t.amount IS NULL THEN NULL "
                        "              WHEN COALESCE(t.currency, 'GBP') = 'GBP' "
                        "                THEN (CASE WHEN t.amount < 0 THEN '-£' ELSE '£' END) "
                        "                     || to_char(abs(t.amount), 'FM999G999G999G990D00') "
                        "              ELSE t.currency || ' ' "
                        "                     || to_char(t.amount, 'FM999G999G999G990D00') END"
                        "       ) AS meta "
                        "FROM platform.transactions t "
                        "JOIN platform.accounts a ON a.id = t.account_id "
                        "WHERE (t.source_transaction_id ILIKE :p ESCAPE '\\' "
                        "       OR t.source_type ILIKE :p ESCAPE '\\' "
                        "       OR t.source_subtype ILIKE :p ESCAPE '\\' "
                        "       OR t.type::text ILIKE :p ESCAPE '\\' "
                        "       OR t.asset_name ILIKE :p ESCAPE '\\' "
                        "       OR t.isin ILIKE :p ESCAPE '\\' "
                        "       OR a.name ILIKE :p ESCAPE '\\' "
                        "       OR CAST(t.amount AS text) ILIKE :p ESCAPE '\\') "
                        + transaction_scope
                        + "ORDER BY COALESCE(t.transaction_date, t.value_date, "
                        "                  t.posted_at::date, t.synced_at::date) DESC NULLS LAST, "
                        "         t.id DESC LIMIT :n"
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        _add("transaction", "Transactions", transactions)

    # ---- Clients (crm.contacts). RLS-only, exactly like GET /crm/contacts/{id}. Staff only: a
    # client's own contact row IS visible to them under contact_scope, but "Clients: yourself" is
    # not a search result anyone wants, and the client portal has no client record to open. ----
    if not is_client:
        clients = (
            (
                await session.execute(
                    text(
                        # `uid`, not the serial: a hit's id is what the portal turns into a URL,
                        # and every other group here already returns one -- documents, tasks,
                        # journeys and the admin firm search all project `uid AS id`. Clients were
                        # the odd one out, so selecting one opened `/clients/42`.
                        "SELECT c.uid AS id, "
                        "       COALESCE("
                        "         NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.surname)), ''), "
                        "         c.name, c.uid) AS title, "
                        "       c.email AS subtitle, "
                        "       CASE c.type::text WHEN 'organisation' THEN 'Organisation' END "
                        "         AS meta "
                        "FROM crm.contacts c "
                        "WHERE c.deleted_at IS NULL "
                        "  AND (c.name ILIKE :p ESCAPE '\\' OR c.first_name ILIKE :p ESCAPE '\\' "
                        "       OR c.surname ILIKE :p ESCAPE '\\' OR c.email ILIKE :p ESCAPE '\\' "
                        # Both numbers. Phone queries compare digits, so the compact value
                        # stored by the API also matches a spaced or national spelling.
                        "       OR regexp_replace(COALESCE(c.mobile, ''), '[^0-9]', '', 'g') "
                        "            LIKE ANY(CAST(:phone_patterns AS text[])) "
                        "       OR regexp_replace(COALESCE(c.phone, ''), '[^0-9]', '', 'g') "
                        "            LIKE ANY(CAST(:phone_patterns AS text[])) "
                        "       OR c.uid ILIKE :p ESCAPE '\\') "
                        "ORDER BY c.surname NULLS LAST, c.name NULLS LAST, c.id "
                        "LIMIT :n"
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        _add("client", "Clients", clients)

    # ---- Documents. RLS-only, exactly like GET /documents/{uid}; archived excluded so a hit is
    # always something the caller can actually open. A client who named their active contact is
    # narrowed to what is FILED under it — `documents.document_contacts`, the same link that
    # portal's own documents list reads; the un-narrowed answer is another of their clients' post.
    # ----
    client_docs = (
        "  AND EXISTS (SELECT 1 FROM documents.document_contacts dc "
        "              WHERE dc.document_id = d.id AND dc.contact_id = CAST(:cid AS int)) "
        if is_client and contact_id is not None
        else ""
    )
    docs = (
        (
            await session.execute(
                text(
                    "SELECT d.uid AS id, COALESCE(d.name, d.uid) AS title, "
                    "       d.document_type AS subtitle, "
                    "       to_char(d.created_at, 'DD Mon YYYY') AS meta "
                    "FROM documents.documents d "
                    "WHERE d.status <> 'archived' "
                    "  AND (d.name ILIKE :p ESCAPE '\\' OR d.document_type ILIKE :p ESCAPE '\\' "
                    "       OR d.uid ILIKE :p ESCAPE '\\') "
                    + client_docs
                    + "ORDER BY d.created_at DESC "
                    "LIMIT :n"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    _add("document", "Documents", docs)

    # ---- Published insights for this client. The content service owns audience/service/sub-type
    # eligibility; searching the table directly would advertise an article the detail route then
    # hides. Client-only because the staff portal has no /insights record route. ----
    if is_client:
        audience = await content_svc.contact_audience(session, contact_id)
        insight_rows, _insight_total = await content_svc.published_for_contact(
            session,
            audience,
            q=q,
            limit=limit + 1,
            offset=0,
        )
        insights = [
            {
                "id": row["uid"],
                "target": None,
                "title": row["title"],
                "subtitle": row.get("summary"),
                "meta": str(row["kind"]).replace("_", " ").capitalize(),
            }
            for row in insight_rows
        ]
        _add("insight", "Insights", insights)

    # ---- Tasks. Mirrors the /tasks router's own split: the client portal's "my action items"
    # carries no permission (RLS already narrows it to the caller's contacts), the staff list needs
    # tasks:read. A client only ever searches their OPEN items, like GET /tasks/me. ----
    if is_client or _holds(principal, TASKS_READ):
        open_only = "  AND t.status IN ('open', 'in_progress') " if is_client else ""
        # The same narrowing `GET /tasks/me` takes — this group IS the search over that list.
        if is_client and contact_id is not None:
            open_only += "  AND t.assignee_contact_id = CAST(:cid AS int) "
        tasks = (
            (
                await session.execute(
                    text(
                        "SELECT t.uid AS id, t.title, t.description AS subtitle, "
                        "       initcap(replace(t.status, '_', ' ')) AS meta "
                        "FROM tasks.tasks t "
                        "WHERE (t.title ILIKE :p ESCAPE '\\' "
                        "       OR t.description ILIKE :p ESCAPE '\\' "
                        "       OR t.uid ILIKE :p ESCAPE '\\') "
                        + open_only
                        + "ORDER BY t.due_date NULLS LAST, t.id "
                        "LIMIT :n"
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        _add("task", "Tasks", tasks)

    # ---- Cases (flows.executions — the platform's "journeys" surface). Gated on cases:read and
    # the caller's book, the same rule as GET /applications (journey_scope.IN_BOOK_SQL), so a hit
    # here is always openable. It is deliberately NOT gated on
    # `flows_orchestrator_v2` — that flag guards no read path, is set nowhere in the deploy
    # compose, and checking it here made the whole Journeys group invisible on every real
    # deployment while /journeys/{uid} worked. ----
    if _holds(principal, CASES_READ):
        cases = (
            (
                await session.execute(
                    text(
                        "SELECT e.uid AS id, f.name AS title, "  # noqa: S608 - IN_BOOK_SQL is a module constant
                        "       COALESCE("
                        "         NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.surname)), ''), "
                        "         c.name) AS subtitle, "
                        "       initcap(replace(e.status, '_', ' ')) AS meta "
                        "FROM flows.executions e "
                        "  JOIN flows.flows f ON f.id = e.flow_id "
                        "  LEFT JOIN crm.applications a ON a.id = e.crm_application_id "
                        "  LEFT JOIN crm.contacts c "
                        "    ON c.id = e.subject_id AND e.subject_type = 'contact' "
                        f"WHERE {IN_BOOK_SQL} AND ("
                        "f.name ILIKE :p ESCAPE '\\' OR e.uid ILIKE :p ESCAPE '\\' "
                        "   OR c.name ILIKE :p ESCAPE '\\' OR c.first_name ILIKE :p ESCAPE '\\' "
                        "   OR c.surname ILIKE :p ESCAPE '\\') "
                        "ORDER BY e.created_at DESC "
                        "LIMIT :n"
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        _add("case", "Journeys", cases)

    return SearchResults(query=q, groups=groups, truncated=truncated)


@router.get("/tenants", name="search-tenants")
async def search_tenants(
    q: str = Query(default="", description="The search term (min 2 characters)."),
    limit: int = Query(default=DEFAULT_GROUP_LIMIT, ge=1, le=MAX_GROUP_LIMIT),
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(admin_session),
) -> SearchResults:
    """Firm search for the Graphene admin (Internal Operations) bar. ``admin_session`` is the
    coarse operations gate; ``tenants:read`` is the granular one — the same pair that guards
    ``GET /admin/tenants``. The model tenant is hidden here as it is there."""
    require_permission(principal, TENANTS_READ)
    q = q.strip()
    if len(q) < MIN_QUERY_LENGTH:
        return _empty(q)

    await _prepare(session)
    rows = (
        (
            await session.execute(
                text(
                    # The UID, not the serial id: the admin portal's tenant routes are addressed
                    # by uid (`/tenants/ten_1743d0…`), and the bar navigates straight to one.
                    "SELECT t.uid AS id, t.name AS title, t.alias AS subtitle, "
                    "       CASE WHEN NOT t.enabled THEN 'Disabled' END AS meta "
                    "FROM auth.tenants t "
                    # `_search` is already lower-cased with whitespace collapsed (0211), so the
                    # pattern is folded the same way; one trigram-indexed LIKE covers every field.
                    "WHERE NOT t.is_model AND t._search LIKE :p ESCAPE '\\' "
                    "ORDER BY t.name, t.id "
                    "LIMIT :n"
                ),
                {"p": _pattern(" ".join(q.lower().split())), "n": limit + 1},
            )
        )
        .mappings()
        .all()
    )
    hits = _hits("tenant", rows, limit)
    return SearchResults(
        query=q,
        groups=[SearchGroup(type="tenant", label="Firms", hits=hits)] if hits else [],
        truncated=len(rows) > limit,
    )
