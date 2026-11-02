"""Reading the outbound-email ledger — the query behind the platform portal's Activity feed.

The read half of ``docs/comms.md``. Structured as ``app/audit_stream.py`` is, and for the same
reasons: one query serves the listing and its facets, the filters compose by ANDing so a filter can
only ever narrow, and the facet vocabulary is computed independently of what is currently filtered
(choosing a status must not shrink the template dropdown).

**Scope is a parameter, not a policy.** ``firm_session`` runs as ``service_role`` — RLS is bypassed
— so the ``tenant_id = :tenant`` predicate below is the ONLY thing keeping one firm out of
another's ledger. The RLS policy in ``0111`` is a second line for other readers, not for this path.
Every function here therefore takes ``tenant_id`` and applies it first; none of them accepts a
caller-supplied tenant.

**The AML rail travels with it.** ``exclude_event_prefixes`` is applied after every other filter, to
rows and to facets alike, because an OPTION is a disclosure too — the reasoning
``routers/firm.py::FIRM_EXCLUDED_EVENT_PREFIXES`` records for the action stream.
"""

from __future__ import annotations

from typing import Any, TypedDict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit_stream import FACET_WINDOW, like_escape, parse_instant

#: The columns a feed ROW carries. Deliberately not `html` — a page of 25 rendered emails is
#: megabytes, and the body is only ever read one at a time by the detail pane.
_ROW_COLUMNS = """
    m.uid, m.status, m.subject, m.to_address, m.to_domain, m.recipient_kind,
    m.recipient_contact_id, m.template_code, m.event_type, m.rule_uid, m.category,
    m.source,
    CASE
      WHEN m.error IS NULL THEN NULL
      WHEN m.status IN ('suppressed', 'unconfigured') THEN 'This email was not sent.'
      ELSE 'This email could not be delivered.'
    END AS error,
    m.provider_message_id, m.created_at, m.sent_at, m.delayed_at,
    m.delivered_at,
    m.bounced_at, m.complained_at, m.id
"""

#: The recipient's own name, when the ledger recorded which client it was. LEFT JOIN, because a
#: 'user'/'external' recipient has no contact and a deleted contact leaves the id NULL — a message
#: whose recipient has since been removed must still appear in the feed.
_CONTACT_JOIN = " LEFT JOIN crm.contacts c ON c.id = m.recipient_contact_id "

#: The name AND the uid, because a row that names a client also links to them. The UID is what a
#: client record is addressed by — the serial `recipient_contact_id` is internal and the routes that
#: once accepted one are gone, so linking on the id builds a URL that cannot resolve.
#: The envelope, read out of the stored wire request rather than from columns of its own.
#:
#: `comms.messages` has no `from_address`/`reply_to`, and deliberately gains none: `request` already
#: holds the exact JSON that was POSTed, so a column would be a second copy of the same fact that
#: could disagree with it. What the detail pane wants is a LABELLED field rather than a line for the
#: reader to find in the Raw tab, which is what this projection is for.
#:
#: NULL for a row with no request — the suppressed and unconfigured paths record one, but a row
#: written before `0118_comms_request` has none, and the reader omits the field rather than showing
#: a blank one.
#:
#: READ BY THE LISTING AS WELL AS THE DETAIL, and that is a deliberate cost. `request` is jsonb and
#: is TOASTed out of line, so extracting two keys detoasts it once per row — which the listing had
#: avoided by projecting neither `html` nor `request`. It is paid because "who did this come from"
#: belongs on the row: on this platform the sender is not a constant (`resolve_sender` sends as the
#: firm, or as the adviser for a manual send), so a feed that shows only the recipient cannot answer
#: it without opening every message. A page is `listing.DEFAULT_LIMIT` rows, not thousands.
_ENVELOPE_SQL = """
    m.request->'body'->>'from'     AS from_address,
    m.request->'body'->>'reply_to' AS reply_to
"""

_NAME_SQL = (
    "NULLIF(trim(COALESCE(c.first_name, '') || ' ' || COALESCE(c.surname, '')), '') "
    "AS recipient_name, c.uid AS recipient_contact_uid"
)


_SORTS: dict[str, str] = {
    "to": "COALESCE(NULLIF(trim(COALESCE(c.first_name, '') || ' ' "
    "|| COALESCE(c.surname, '')), ''), m.to_address)",
    "from": "m.request->'body'->>'from'",
    "status": "m.status",
    "subject": "m.subject",
    "sent": "m.created_at",
}


class MessagesPage(TypedDict):
    items: list[dict[str, Any]]
    has_more: bool


class MessageFacets(TypedDict):
    """The toolbar's vocabulary for THIS firm's recent ledger — what it actually sends, rather
    than every value the schema allows. A firm that has never sent a `task_notice` should not be
    offered one as a filter."""

    statuses: list[str]
    template_codes: list[str]
    event_types: list[str]
    categories: list[str]


def _deny_clause(prefixes: tuple[str, ...], params: dict[str, Any]) -> str:
    """Withhold whole event families. Applied to rows AND facets."""
    if not prefixes:
        return ""
    parts = []
    for i, prefix in enumerate(prefixes):
        key = f"deny{i}"
        params[key] = prefix + "%"
        # A NULL event_type is a transactional send (an invite has no event) and is never denied:
        # `NOT LIKE` on NULL is NULL, which would silently drop every one of them.
        parts.append(f"(m.event_type IS NULL OR m.event_type NOT LIKE :{key})")
    return " AND " + " AND ".join(parts)


def _filters(
    *,
    status: str | None,
    event_type: str | None,
    template_code: str | None,
    category: str | None,
    contact: int | None,
    query: str | None,
    since: str | None,
    until: str | None,
    params: dict[str, Any],
) -> str:
    """Every filter, ANDed onto the scope. Each one narrows; none can widen."""
    sql = ""
    if status:
        # Comma-separated, ORed — a set of alternatives, like the action stream's event types.
        values = [s.strip() for s in status.split(",") if s.strip()]
        if values:
            params["statuses"] = values
            sql += " AND m.status = ANY(:statuses)"
    if template_code:
        values = [s.strip() for s in template_code.split(",") if s.strip()]
        if values:
            params["template_codes"] = values
            sql += " AND m.template_code = ANY(:template_codes)"
    if category:
        values = [s.strip() for s in category.split(",") if s.strip()]
        if values:
            params["categories"] = values
            sql += " AND m.category = ANY(:categories)"
    if event_type:
        # PREFIXES, ORed — `event_type=document.` matches the whole family, and an exact type is
        # its own prefix. The same wire format the action stream uses, so one toolbar serves both.
        prefixes = [s.strip() for s in event_type.split(",") if s.strip()]
        if prefixes:
            ors = []
            for i, prefix in enumerate(prefixes):
                key = f"et{i}"
                params[key] = like_escape(prefix) + "%"
                ors.append(f"m.event_type LIKE :{key} ESCAPE '\\'")
            sql += " AND (" + " OR ".join(ors) + ")"
    if contact is not None:
        params["contact"] = contact
        sql += " AND m.recipient_contact_id = :contact"
    if query:
        params["q"] = "%" + like_escape(query) + "%"
        # What a reader recognises a row by. The recipient's ADDRESS is searchable because this is
        # the firm's own ledger of mail it sent — the same posture that lets the feed show it.
        sql += (
            " AND (m.subject ILIKE :q ESCAPE '\\' OR m.to_address ILIKE :q ESCAPE '\\'"
            " OR COALESCE(c.first_name, '') || ' ' || COALESCE(c.surname, '') ILIKE :q ESCAPE '\\')"
        )
    start = parse_instant(since, "since")
    if start is not None:
        params["since"] = start
        sql += " AND m.created_at >= :since"
    end = parse_instant(until, "until")
    if end is not None:
        params["until"] = end
        sql += " AND m.created_at <= :until"
    return sql


async def fetch_messages(
    session: AsyncSession,
    *,
    tenant_id: int,
    limit: int = 25,
    before: int | None = None,
    after: int | None = None,
    status: str | None = None,
    event_type: str | None = None,
    template_code: str | None = None,
    category: str | None = None,
    contact: int | None = None,
    query: str | None = None,
    since: str | None = None,
    until: str | None = None,
    sort: str | None = None,
    direction: str | None = None,
    offset: int = 0,
    exclude_event_prefixes: tuple[str, ...] = (),
) -> MessagesPage:
    """One page of the firm's outbound mail, newest first.

    Keyset on ``id`` rather than OFFSET: the ledger grows at the head while someone is reading it,
    and an offset page would repeat or skip rows as it did. ``before`` walks backwards (load more),
    ``after`` asks "what has arrived since" for the live poll.

    **A CHOSEN SORT PAGES BY OFFSET INSTEAD.** A keyset cursor is a position in ONE order - it is
    the id, and the id only locates a row while the rows run by id. Ordering by Subject and then
    asking for "everything before id 4192" is a question about a different list, and it silently
    returns the wrong page rather than failing. So the two paging strategies follow the two
    orders: the default feed keeps its keyset, and a sort the reader chose walks by offset, which
    is what every other listing in the estate already does. The drift an offset page suffers as
    the ledger grows at the head is real and is the lesser problem: it costs a repeated row while
    reading, where the wrong cursor costs the whole page.

    ``sort`` is matched against ``_SORTS`` and ignored when unknown, so a stale link orders by
    the default rather than erroring.
    """
    capped = max(1, min(int(limit), 100))
    params: dict[str, Any] = {"tenant": tenant_id, "limit": capped + 1}
    where = "m.tenant_id = :tenant"
    # A chosen sort takes the offset path; the cursors belong to the default order and are
    # ignored on it, because a cursor read against a different order names the wrong page.
    column = _SORTS.get(sort or "")
    if column is None:
        if before is not None:
            params["before"] = before
            where += " AND m.id < :before"
        if after is not None:
            params["after"] = after
            where += " AND m.id > :after"
    where += _filters(
        status=status,
        event_type=event_type,
        template_code=template_code,
        category=category,
        contact=contact,
        query=query,
        since=since,
        until=until,
        params=params,
    )
    where += _deny_clause(exclude_event_prefixes, params)

    descending = "DESC" if (direction or "").lower() == "desc" else "ASC"
    if column is None:
        order = "m.id DESC"
        skip = ""
    else:
        order = f"{column} {descending} NULLS LAST, m.id DESC"
        skip = " OFFSET :offset"
        params["offset"] = max(0, int(offset))

    rows = (
        (
            await session.execute(
                text(
                    # noqa: S608 - only module constants and clause builders are interpolated
                    # (`_ROW_COLUMNS`, `_NAME_SQL`, `_CONTACT_JOIN`, and a `where` assembled
                    # here from literals). Every reader-supplied value binds as a parameter.
                    f"SELECT {_ROW_COLUMNS}, {_NAME_SQL}, "  # noqa: S608
                    f"  {_ENVELOPE_SQL} FROM comms.messages m"
                    f"{_CONTACT_JOIN} WHERE {where} ORDER BY {order} LIMIT :limit{skip}"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    items = [dict(r) for r in rows[:capped]]
    return {"items": items, "has_more": len(rows) > capped}


async def message_facets(
    session: AsyncSession,
    *,
    tenant_id: int,
    window: int = FACET_WINDOW,
    exclude_event_prefixes: tuple[str, ...] = (),
) -> MessageFacets:
    """The filter vocabulary, over this firm's RECENT ledger and independent of every filter.

    Deliberately filter-independent: a toolbar whose options shrink as you use it makes the next
    filter look unavailable rather than empty. Bounded by ``window`` for the same reason
    ``audit_stream`` bounds its own — the facets describe the recent stream, not all history, and
    scanning a year of sends to populate a dropdown is not a cost a page load should pay.
    """
    params: dict[str, Any] = {"tenant": tenant_id, "window": max(1, int(window))}
    deny = _deny_clause(exclude_event_prefixes, params)
    rows = (
        (
            await session.execute(
                text(
                    # noqa: S608 - `deny` is built here from a literal prefix tuple; the
                    # tenant and window bind as parameters.
                    "WITH recent AS ("  # noqa: S608
                    "  SELECT m.status, m.template_code, m.event_type, m.category"
                    "  FROM comms.messages m"
                    f"  WHERE m.tenant_id = :tenant{deny}"
                    "  ORDER BY m.id DESC LIMIT :window)"
                    " SELECT"
                    "   ARRAY(SELECT DISTINCT status FROM recent WHERE status IS NOT NULL"
                    "         ORDER BY status) AS statuses,"
                    "   ARRAY(SELECT DISTINCT template_code FROM recent"
                    "         WHERE template_code IS NOT NULL ORDER BY template_code) AS templates,"
                    "   ARRAY(SELECT DISTINCT event_type FROM recent WHERE event_type IS NOT NULL"
                    "         ORDER BY event_type) AS event_types,"
                    "   ARRAY(SELECT DISTINCT category FROM recent WHERE category IS NOT NULL"
                    "         ORDER BY category) AS categories"
                ),
                params,
            )
        )
        .mappings()
        .one()
    )
    return {
        "statuses": list(rows["statuses"] or []),
        "template_codes": list(rows["templates"] or []),
        "event_types": list(rows["event_types"] or []),
        "categories": list(rows["categories"] or []),
    }


async def fetch_message(
    session: AsyncSession,
    *,
    tenant_id: int,
    uid: str,
    exclude_event_prefixes: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    """One message with its rendered body, the wire request, and its provider callbacks, or None.

    The deny list is applied here too, not only to the listing: a withheld family must not be
    reachable by guessing a uid, which is the same rule the action stream's ``event`` deep-link
    follows.
    """
    params: dict[str, Any] = {"tenant": tenant_id, "uid": uid}
    where = "m.tenant_id = :tenant AND m.uid = :uid" + _deny_clause(exclude_event_prefixes, params)
    row = (
        (
            await session.execute(
                text(
                    # noqa: S608 - module constants plus a literal-built `where`; the tenant
                    # and uid bind as parameters.
                    f"SELECT {_ROW_COLUMNS}, m.html, m.request, {_NAME_SQL}, "  # noqa: S608
                    f"  {_ENVELOPE_SQL} FROM comms.messages m"
                    f"{_CONTACT_JOIN} WHERE {where}"
                ),
                params,
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    message = dict(row)
    events = (
        (
            await session.execute(
                text(
                    "SELECT event_type, provider_event_id, occurred_at, created_at "
                    "FROM comms.message_events "
                    "WHERE message_id = :m ORDER BY id"
                ),
                {"m": message["id"]},
            )
        )
        .mappings()
        .all()
    )
    # The provider's raw `payload` is deliberately NOT returned: it repeats what the columns
    # already carry and can hold recipient detail the feed has no reason to re-publish.
    message["events"] = [dict(e) for e in events]
    return message
