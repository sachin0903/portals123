"""Firm-published client-portal content: sanitise, eligibility, and the staff write path.

Bodies are GrapesJS ``schema`` (html/css) on the draft row. Publish inlines CSS, runs them
through ``nh3``, and snapshots the result onto an immutable ``content.item_versions`` row.
Clients never read ``schema`` — only the sanitised ``body_html``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import nh3
from css_inline import inline as inline_css
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import listing
from app.ids import new_id

Kind = Literal["news", "article", "guide"]
Status = Literal["draft", "published", "archived"]
Audience = Literal["all", "individual", "organisation"]
ContactType = Literal["individual", "organisation"]

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_ALLOWED_TAGS = {
    "a",
    "blockquote",
    "br",
    "div",
    "em",
    "figcaption",
    "figure",
    "h1",
    "h2",
    "h3",
    "h4",
    "hr",
    "i",
    "img",
    "li",
    "ol",
    "p",
    "span",
    "strong",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
}
_ALLOWED_ATTRS = {
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "width", "height"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
    "*": {"class", "style"},
}

SERVICE_TYPES = frozenset({"advised", "execution_only", "discretionary"})
SUB_TYPES = frozenset({"trust", "company", "charity"})


class ContentError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _require_row(row: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        raise ContentError(500, "Content row missing after write.")
    return row


@dataclass(frozen=True)
class ContactAudience:
    type: ContactType | None
    service_type: str | None
    sub_type: str | None


async def contact_audience(session: AsyncSession, contact_id: int | None = None) -> ContactAudience:
    """Resolve the client whose content eligibility applies, narrowed to the caller's real scope.

    This is shared by the content endpoints and cross-entity search. Keeping it here means an
    insight cannot appear in Spotlight when the same person would be refused on opening it.
    """
    who = (
        (
            await session.execute(
                text(
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
                    "SELECT c.type::text AS type, c.service_type::text AS service_type, "
                    "       c.sub_type::text AS sub_type "
                    "FROM me LEFT JOIN crm.contacts c "
                    "  ON c.id = me.contact_id AND c.deleted_at IS NULL"
                ),
                {"cid": contact_id},
            )
        )
        .mappings()
        .one()
    )
    contact_type = who["type"] if who["type"] in ("individual", "organisation") else None
    return ContactAudience(
        type=contact_type,
        service_type=who["service_type"],
        sub_type=who["sub_type"],
    )


def slugify(title: str) -> str:
    s = _SLUG_RE.sub("-", title.lower()).strip("-")[:48]
    return s or "item"


def sanitise_html(html: str, css: str = "") -> str:
    """Inline CSS then strip everything outside the article allowlist.

    The client portal renders the result with ``{@html}`` — this function is the only door
    that markup may pass through. Scripts, event handlers and unknown tags are dropped.
    """
    source = html or ""
    if css.strip():
        wrapped = f"<html><head><style>{css}</style></head><body>{source}</body></html>"
        try:
            source = inline_css(wrapped)
        except Exception:
            source = html
        body = re.search(r"<body[^>]*>(.*)</body>", source, re.IGNORECASE | re.DOTALL)
        if body:
            source = body.group(1)
    cleaned = nh3.clean(
        source,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer",
    )
    return cleaned.strip()


def eligible(
    *,
    status: str,
    audience: str,
    service_types: list[str] | tuple[str, ...] | None,
    sub_types: list[str] | tuple[str, ...] | None,
    published_at: datetime | None,
    expires_at: datetime | None,
    contact: ContactAudience,
    now: datetime | None = None,
) -> bool:
    """Whether a published item may be shown to this contact. Drafts and archived never qualify."""
    if status != "published":
        return False
    instant = now or datetime.now(UTC)
    if published_at is not None and published_at > instant:
        return False
    if expires_at is not None and expires_at <= instant:
        return False
    if contact.type in ("individual", "organisation"):
        contact_type: ContactType = contact.type
    else:
        contact_type = "individual"
    if audience not in ("all", contact_type):
        return False
    types = list(service_types or [])
    if types and (contact.service_type is None or contact.service_type not in types):
        return False
    subs = list(sub_types or [])
    if contact_type == "organisation" and subs:
        if contact.sub_type is None or contact.sub_type not in subs:
            return False
    return True


def _schema_html(schema: dict[str, Any] | None) -> tuple[str, str]:
    raw = schema or {}
    html = raw.get("html")
    css = raw.get("css")
    return (html if isinstance(html, str) else "", css if isinstance(css, str) else "")


async def list_categories(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                text(
                    "SELECT uid, name, slug, sort_order, created_at "
                    "FROM content.categories ORDER BY sort_order, name"
                )
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def create_category(session: AsyncSession, tenant_id: int, name: str) -> dict[str, Any]:
    slug = slugify(name)
    uid = new_id()
    try:
        row = (
            (
                await session.execute(
                    text(
                        "INSERT INTO content.categories (uid, tenant_id, name, slug) "
                        "VALUES (:uid, :t, :name, :slug) "
                        "RETURNING uid, name, slug, sort_order, created_at"
                    ),
                    {"uid": uid, "t": tenant_id, "name": name.strip(), "slug": slug},
                )
            )
            .mappings()
            .one()
        )
    except Exception as exc:
        raise ContentError(409, "A category with that name already exists.") from exc
    return dict(row)


async def list_items(
    session: AsyncSession,
    *,
    kind: str | Sequence[str] | None = None,
    status: str | Sequence[str] | None = None,
    category_uid: str | None = None,
    q: str | None = None,
) -> list[dict[str, Any]]:
    clauses = ["TRUE"]
    params: dict[str, Any] = {}
    kinds = listing.clean_many(kind)
    statuses = listing.clean_many(status)
    if kinds:
        clauses.append("i.kind = ANY(CAST(:kind AS content.item_kind[]))")
        params["kind"] = kinds
    if statuses:
        clauses.append("i.status = ANY(CAST(:status AS content.item_status[]))")
        params["status"] = statuses
    if category_uid:
        clauses.append("c.uid = :cat")
        params["cat"] = category_uid
    if q and q.strip():
        clauses.append("(i.title ILIKE :q OR COALESCE(i.summary, '') ILIKE :q)")
        params["q"] = f"%{q.strip()}%"
    where = " AND ".join(clauses)
    rows = (
        (
            await session.execute(
                text(
                    "SELECT i.uid, i.kind::text AS kind, i.title, i.slug, i.summary, "  # noqa: S608
                    "       i.status::text AS status, i.audience::text AS audience, "
                    "       i.service_types, i.sub_types, i.published_at, i.expires_at, "
                    "       i.hero_url, i.file_key IS NOT NULL AS has_file, "
                    "       i.created_at, i.updated_at, "
                    "       c.uid AS category_uid, c.name AS category_name, "
                    "       u.first_name || ' ' || u.last_name AS created_by "
                    "FROM content.items i "
                    "LEFT JOIN content.categories c ON c.id = i.category_id "
                    "LEFT JOIN auth.users u ON u.id = i.created_by "
                    f"WHERE {where} "
                    "ORDER BY i.updated_at DESC"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def get_item(
    session: AsyncSession, uid: str, *, include_schema: bool
) -> dict[str, Any] | None:
    cols = (
        "i.uid, i.kind::text AS kind, i.title, i.slug, i.summary, "
        "i.status::text AS status, i.audience::text AS audience, "
        "i.service_types, i.sub_types, i.published_at, i.expires_at, "
        "i.hero_url, i.file_key, i.file_content_type, i.file_size, i.body_html, "
        "i.created_at, i.updated_at, c.uid AS category_uid, c.name AS category_name"
    )
    if include_schema:
        cols += ", i.schema"
    row = (
        (
            await session.execute(
                text(
                    f"SELECT {cols} FROM content.items i "  # noqa: S608
                    "LEFT JOIN content.categories c ON c.id = i.category_id "
                    "WHERE i.uid = :uid"
                ),
                {"uid": uid},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def _category_id(session: AsyncSession, uid: str | None) -> int | None:
    if not uid:
        return None
    cid = (
        await session.execute(text("SELECT id FROM content.categories WHERE uid = :u"), {"u": uid})
    ).scalar_one_or_none()
    if cid is None:
        raise ContentError(422, "Unknown category.")
    return int(cid)


def _validate_lists(service_types: list[str] | None, sub_types: list[str] | None) -> None:
    for value in service_types or []:
        if value not in SERVICE_TYPES:
            raise ContentError(422, f"Unknown service type '{value}'.")
    for value in sub_types or []:
        if value not in SUB_TYPES:
            raise ContentError(422, f"Unknown organisation sub-type '{value}'.")


async def _unique_slug(
    session: AsyncSession, tenant_id: int, title: str, exclude_uid: str | None = None
) -> str:
    base = slugify(title)
    slug = base
    n = 2
    while True:
        if exclude_uid is None:
            row = (
                await session.execute(
                    text("SELECT 1 FROM content.items WHERE tenant_id = :t AND slug = :s"),
                    {"t": tenant_id, "s": slug},
                )
            ).first()
        else:
            row = (
                await session.execute(
                    text(
                        "SELECT 1 FROM content.items "
                        "WHERE tenant_id = :t AND slug = :s AND uid <> :ex"
                    ),
                    {"t": tenant_id, "s": slug, "ex": exclude_uid},
                )
            ).first()
        if row is None:
            return slug
        slug = f"{base}-{n}"
        n += 1


async def create_item(
    session: AsyncSession,
    tenant_id: int,
    actor_id: str,
    *,
    kind: Kind,
    title: str,
) -> dict[str, Any]:
    title = title.strip()
    if not title:
        raise ContentError(422, "Title is required.")
    uid = new_id()
    slug = await _unique_slug(session, tenant_id, title)
    await session.execute(
        text(
            "INSERT INTO content.items (uid, tenant_id, kind, title, slug, created_by) "
            "VALUES (:uid, :t, CAST(:kind AS content.item_kind), :title, :slug, "
            "        CAST(:actor AS uuid))"
        ),
        {"uid": uid, "t": tenant_id, "kind": kind, "title": title, "slug": slug, "actor": actor_id},
    )
    return _require_row(await get_item(session, uid, include_schema=True))


async def update_item(
    session: AsyncSession,
    uid: str,
    tenant_id: int,
    *,
    title: str | None = None,
    summary: str | None = None,
    audience: Audience | None = None,
    service_types: list[str] | None = None,
    sub_types: list[str] | None = None,
    category_uid: str | None = None,
    clear_category: bool = False,
    expires_at: datetime | None = None,
    clear_expires: bool = False,
    schema: dict[str, Any] | None = None,
    hero_url: str | None = None,
) -> dict[str, Any]:
    current = await get_item(session, uid, include_schema=True)
    if current is None:
        raise ContentError(404, "Not found.")
    _validate_lists(service_types, sub_types)
    sets = ["updated_at = now()"]
    params: dict[str, Any] = {"uid": uid}
    if title is not None:
        title = title.strip()
        if not title:
            raise ContentError(422, "Title is required.")
        sets.append("title = :title")
        params["title"] = title
        sets.append("slug = :slug")
        params["slug"] = await _unique_slug(session, tenant_id, title, exclude_uid=uid)
    if summary is not None:
        sets.append("summary = :summary")
        params["summary"] = summary.strip() or None
    if audience is not None:
        sets.append("audience = CAST(:audience AS content.audience)")
        params["audience"] = audience
    if service_types is not None:
        sets.append("service_types = CAST(:st AS text[])")
        params["st"] = service_types
    if sub_types is not None:
        sets.append("sub_types = CAST(:sub AS text[])")
        params["sub"] = sub_types
    if clear_category:
        sets.append("category_id = NULL")
    elif category_uid is not None:
        sets.append("category_id = :cid")
        params["cid"] = await _category_id(session, category_uid)
    if clear_expires:
        sets.append("expires_at = NULL")
    elif expires_at is not None:
        sets.append("expires_at = :exp")
        params["exp"] = expires_at
    if schema is not None:
        sets.append("schema = CAST(:schema AS jsonb)")
        params["schema"] = json.dumps(schema)
    if hero_url is not None:
        sets.append("hero_url = :hero")
        params["hero"] = hero_url or None
    await session.execute(
        text(f"UPDATE content.items SET {', '.join(sets)} WHERE uid = :uid"),  # noqa: S608
        params,
    )
    return _require_row(await get_item(session, uid, include_schema=True))


async def publish_item(session: AsyncSession, uid: str, actor_id: str) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, tenant_id, kind::text AS kind, schema, file_key, "
                    "       status::text AS status "
                    "FROM content.items WHERE uid = :uid FOR UPDATE"
                ),
                {"uid": uid},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ContentError(404, "Not found.")
    if row["status"] == "archived":
        raise ContentError(409, "Archived content cannot be published.")
    kind = row["kind"]
    schema = row["schema"] if isinstance(row["schema"], dict) else {}
    html, css = _schema_html(schema)
    if kind in ("news", "article"):
        body = sanitise_html(html, css)
        if not body:
            raise ContentError(422, "Publish a body before going live.")
    else:
        if not row["file_key"]:
            raise ContentError(422, "Upload a PDF guide before publishing.")
        body = sanitise_html(html, css)
    n = int(
        (
            await session.execute(
                text(
                    "SELECT COALESCE(MAX(version_number), 0) "
                    "FROM content.item_versions WHERE item_id = :id"
                ),
                {"id": row["id"]},
            )
        ).scalar_one()
    )
    version_uid = new_id()
    version_id = (
        await session.execute(
            text(
                "INSERT INTO content.item_versions "
                "(uid, tenant_id, item_id, version_number, schema, body_html, published_by) "
                "VALUES (:uid, :t, :item, :n, CAST(:schema AS jsonb), :html, CAST(:actor AS uuid)) "
                "RETURNING id"
            ),
            {
                "uid": version_uid,
                "t": row["tenant_id"],
                "item": row["id"],
                "n": n + 1,
                "schema": json.dumps(schema),
                "html": body,
                "actor": actor_id,
            },
        )
    ).scalar_one()
    await session.execute(
        text(
            "UPDATE content.items SET status = 'published', body_html = :html, "
            "current_published_version_id = :vid, published_at = COALESCE(published_at, now()), "
            "updated_at = now() WHERE id = :id"
        ),
        {"html": body, "vid": version_id, "id": row["id"]},
    )
    return _require_row(await get_item(session, uid, include_schema=True))


async def archive_item(session: AsyncSession, uid: str) -> dict[str, Any]:
    result = await session.execute(
        text(
            "UPDATE content.items SET status = 'archived', updated_at = now() "
            "WHERE uid = :uid AND status <> 'archived'"
        ),
        {"uid": uid},
    )
    if int(getattr(result, "rowcount", 0) or 0) == 0:
        existing = await get_item(session, uid, include_schema=False)
        if existing is None:
            raise ContentError(404, "Not found.")
    return _require_row(await get_item(session, uid, include_schema=True))


async def set_guide_file(
    session: AsyncSession,
    uid: str,
    *,
    file_key: str,
    content_type: str,
    size: int,
) -> dict[str, Any]:
    result = await session.execute(
        text(
            "UPDATE content.items SET file_key = :k, file_content_type = :ct, file_size = :sz, "
            "updated_at = now() WHERE uid = :uid AND kind = 'guide'"
        ),
        {"k": file_key, "ct": content_type, "sz": size, "uid": uid},
    )
    if int(getattr(result, "rowcount", 0) or 0) == 0:
        raise ContentError(404, "Guide not found.")
    return _require_row(await get_item(session, uid, include_schema=True))


async def published_for_contact(
    session: AsyncSession,
    contact: ContactAudience,
    *,
    kind: str | None = None,
    category_uid: str | None = None,
    q: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    clauses = ["i.status = 'published'"]
    params: dict[str, Any] = {}
    if kind:
        clauses.append("i.kind = CAST(:kind AS content.item_kind)")
        params["kind"] = kind
    if category_uid:
        clauses.append("c.uid = :cat")
        params["cat"] = category_uid
    if q and q.strip():
        clauses.append("(i.title ILIKE :q OR COALESCE(i.summary, '') ILIKE :q)")
        params["q"] = f"%{q.strip()}%"
    where = " AND ".join(clauses)
    rows = [
        dict(r)
        for r in (
            await session.execute(
                text(
                    "SELECT i.uid, i.kind::text AS kind, i.title, i.slug, i.summary, "  # noqa: S608
                    "       i.audience::text AS audience, i.service_types, i.sub_types, "
                    "       i.published_at, i.expires_at, i.hero_url, i.body_html, "
                    "       i.file_key IS NOT NULL AS has_file, "
                    "       c.uid AS category_uid, c.name AS category_name "
                    "FROM content.items i "
                    "LEFT JOIN content.categories c ON c.id = i.category_id "
                    f"WHERE {where} "
                    "ORDER BY i.published_at DESC NULLS LAST, i.created_at DESC"
                ),
                params,
            )
        )
        .mappings()
        .all()
    ]
    matched = [
        r
        for r in rows
        if eligible(
            status="published",
            audience=r["audience"],
            service_types=list(r["service_types"] or []),
            sub_types=list(r["sub_types"] or []),
            published_at=r["published_at"],
            expires_at=r["expires_at"],
            contact=contact,
        )
    ]
    total = len(matched)
    return matched[offset : offset + limit], total


async def published_detail(
    session: AsyncSession, uid: str, contact: ContactAudience
) -> dict[str, Any] | None:
    row = await get_item(session, uid, include_schema=False)
    if row is None:
        return None
    if not eligible(
        status=row["status"],
        audience=row["audience"],
        service_types=list(row["service_types"] or []),
        sub_types=list(row["sub_types"] or []),
        published_at=row["published_at"],
        expires_at=row["expires_at"],
        contact=contact,
    ):
        return None
    return row
