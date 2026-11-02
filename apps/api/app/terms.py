"""Legal terms catalogue: authoring, publish (immutable versions), outstanding, accept.

Distinct from ``l10n.terms`` (UI vocabulary). A published version is the legal snapshot;
acceptances pin that row. Markdown/HTML is sanitised through ``declaration_html`` at publish.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import as_service_role
from app.flows.kyc import KIND_TERMS, compute_valid_until
from app.flows.terms_content import declaration_html
from app.ids import new_id

Owner = Literal["graphene", "tenant"]
Status = Literal["draft", "published", "archived"]
Audience = Literal["client", "staff", "all"]
BodyKind = Literal["html", "pdf"]
Channel = Literal["portal", "in_person"]
RequiredAt = Literal["onboarding", "login"]
SubjectKind = Literal["contact", "user"]

_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,62}$")
REQUIRED_AT = frozenset({"onboarding", "login"})
AUDIENCES = frozenset({"client", "staff", "all"})
BODY_KINDS = frozenset({"html", "pdf"})


class TermsError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _require_row(row: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        raise TermsError(500, "Terms row missing after write.")
    return row


def slugify_code(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:48]
    return s or "terms"


def content_hash(*, body_kind: str, body_html: str | None, file_key: str | None) -> str:
    raw = f"{body_kind}\n{body_html or ''}\n{file_key or ''}".encode()
    return hashlib.sha256(raw).hexdigest()


def applies(
    applicability: dict[str, Any] | None,
    *,
    product: str | None = None,
    has_transfer: bool | None = None,
) -> bool:
    """Whether a term applies to this account context.

    ``always`` wins. Otherwise any matching axis is enough (ISA product, or a transfer).
    An empty object counts as always — a term with no scope is required everywhere it is listed.
    """
    spec = applicability if isinstance(applicability, dict) else {}
    if spec.get("always") is True:
        return True
    products = [str(p) for p in (spec.get("products") or []) if p]
    transfer = spec.get("has_transfer")
    if not products and transfer is not True:
        return True
    if products and product in products:
        return True
    if transfer is True and has_transfer is True:
        return True
    return False


def audience_matches(audience: str, subject: SubjectKind) -> bool:
    if audience == "all":
        return True
    if subject == "contact":
        return audience == "client"
    return audience == "staff"


def _as_applicability(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"always": True}
        return parsed if isinstance(parsed, dict) else {"always": True}
    return {"always": True}


def _validate_code(code: str) -> str:
    value = code.strip().lower()
    if not _CODE_RE.match(value):
        raise TermsError(
            422, "Code must start with a letter and use lowercase letters, digits and underscores."
        )
    return value


def _validate_required_at(values: list[str] | None) -> list[str]:
    required = list(values or ["onboarding"])
    if not required or any(v not in REQUIRED_AT for v in required):
        raise TermsError(422, "required_at must be onboarding and/or login.")
    return sorted(set(required))


def _validate_audience(audience: str | None) -> str:
    value = audience or "client"
    if value not in AUDIENCES:
        raise TermsError(422, "Unknown audience.")
    return value


def _validate_kind(kind: str | None) -> str:
    value = kind or "html"
    if value not in BODY_KINDS:
        raise TermsError(422, "Body must be written copy or a PDF.")
    return value


_TERM_COLS = (
    "t.id, t.uid, t.owner::text AS owner, t.tenant_id, t.code, t.title, "
    "t.audience::text AS audience, t.required_at, t.applicability, "
    "t.status::text AS status, t.body_kind::text AS body_kind, t.body_md, "
    "t.file_key, t.file_content_type, t.file_size, t.published_at, t.sort_order, "
    "t.created_at, t.updated_at, t.current_published_version_id, "
    "v.uid AS current_version_uid, v.version_number AS current_version_number, "
    "v.published_at AS current_version_published_at, "
    "v.body_html AS published_body_html, v.file_key AS published_file_key, "
    "v.file_content_type AS published_file_content_type, v.body_kind::text AS published_body_kind"
)


async def list_terms(
    session: AsyncSession,
    *,
    owner: Owner | None = None,
    tenant_id: int | None = None,
    status: str | None = None,
    audience: str | None = None,
    q: str | None = None,
) -> list[dict[str, Any]]:
    """The catalogue, filtered.

    ``tenant_id`` is the firm's own rows plus Graphene's platform-wide ones — the same slice RLS
    grants a firm session. Pass it whenever the caller runs ELEVATED: the orchestrator's gates and
    the projection read this on a service-role session, where RLS is bypassed, and without an
    explicit scope they would count every other firm's terms as outstanding against this firm's
    clients. Leave it None only where RLS is doing the scoping (the request-session routers).
    """
    clauses = ["TRUE"]
    params: dict[str, Any] = {}
    if tenant_id is not None:
        clauses.append("(t.tenant_id = :tenant_id OR t.owner = 'graphene')")
        params["tenant_id"] = tenant_id
    if owner:
        clauses.append("t.owner = CAST(:owner AS terms.owner)")
        params["owner"] = owner
    if status:
        clauses.append("t.status = CAST(:status AS terms.status)")
        params["status"] = status
    if audience:
        clauses.append("t.audience = CAST(:audience AS terms.audience)")
        params["audience"] = audience
    if q and q.strip():
        clauses.append("(t.title ILIKE :q OR t.code ILIKE :q)")
        params["q"] = f"%{q.strip()}%"
    where = " AND ".join(clauses)
    rows = (
        (
            await session.execute(
                text(
                    f"SELECT {_TERM_COLS} "  # noqa: S608
                    "FROM terms.terms t "
                    "LEFT JOIN terms.term_versions v ON v.id = t.current_published_version_id "
                    f"WHERE {where} "
                    "ORDER BY t.sort_order, t.title"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def get_term(session: AsyncSession, uid: str) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text(
                    f"SELECT {_TERM_COLS} "  # noqa: S608
                    "FROM terms.terms t "
                    "LEFT JOIN terms.term_versions v ON v.id = t.current_published_version_id "
                    "WHERE t.uid = :uid"
                ),
                {"uid": uid},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def get_term_by_code(
    session: AsyncSession, *, tenant_id: int | None, code: str
) -> dict[str, Any] | None:
    if tenant_id is None:
        clause = "t.owner = 'graphene' AND t.code = :c"
        params: dict[str, Any] = {"c": code}
    else:
        clause = "t.tenant_id = :t AND t.code = :c"
        params = {"t": tenant_id, "c": code}
    row = (
        (
            await session.execute(
                text(
                    f"SELECT {_TERM_COLS} FROM terms.terms t "  # noqa: S608
                    "LEFT JOIN terms.term_versions v ON v.id = t.current_published_version_id "
                    f"WHERE {clause}"
                ),
                params,
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def list_versions(session: AsyncSession, term_uid: str) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                text(
                    "SELECT v.uid, v.version_number, v.title, v.body_kind::text AS body_kind, "
                    "       v.body_html, v.file_key, v.file_content_type, v.file_size, "
                    "       v.content_hash, v.published_at "
                    "FROM terms.term_versions v "
                    "JOIN terms.terms t ON t.id = v.term_id "
                    "WHERE t.uid = :u "
                    "ORDER BY v.version_number DESC"
                ),
                {"u": term_uid},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def create_term(
    session: AsyncSession,
    *,
    owner: Owner,
    tenant_id: int | None,
    actor_id: str | None,
    title: str,
    code: str | None = None,
    audience: str = "client",
    required_at: list[str] | None = None,
    applicability: dict[str, Any] | None = None,
    body_kind: str = "html",
    body_md: str | None = None,
) -> dict[str, Any]:
    title = title.strip()
    if not title:
        raise TermsError(422, "Title is required.")
    if owner == "graphene" and tenant_id is not None:
        raise TermsError(422, "Graphene terms cannot belong to a firm.")
    if owner == "tenant" and tenant_id is None:
        raise TermsError(422, "Firm terms need a tenant.")
    uid = new_id()
    try:
        await session.execute(
            text(
                "INSERT INTO terms.terms "
                "  (uid, owner, tenant_id, code, title, audience, required_at, applicability, "
                "   body_kind, body_md, created_by) "
                "VALUES (:uid, CAST(:owner AS terms.owner), :t, :code, :title, "
                "        CAST(:audience AS terms.audience), CAST(:req AS text[]), "
                "        CAST(:app AS jsonb), CAST(:kind AS terms.body_kind), :md, "
                "        CAST(:actor AS uuid))"
            ),
            {
                "uid": uid,
                "owner": owner,
                "t": tenant_id,
                "code": _validate_code(code or slugify_code(title)),
                "title": title,
                "audience": _validate_audience(audience),
                "req": _validate_required_at(required_at),
                "app": json.dumps(applicability or {"always": True}),
                "kind": _validate_kind(body_kind),
                "md": body_md,
                "actor": actor_id,
            },
        )
    except Exception as exc:
        raise TermsError(409, "A term with that code already exists.") from exc
    return _require_row(await get_term(session, uid))


async def update_term(
    session: AsyncSession,
    uid: str,
    *,
    title: str | None = None,
    audience: str | None = None,
    required_at: list[str] | None = None,
    applicability: dict[str, Any] | None = None,
    body_kind: str | None = None,
    body_md: str | None = None,
    clear_file: bool = False,
) -> dict[str, Any]:
    current = await get_term(session, uid)
    if current is None:
        raise TermsError(404, "Not found.")
    if current["status"] == "archived":
        raise TermsError(409, "Archived terms cannot be edited.")
    sets = ["updated_at = now()"]
    params: dict[str, Any] = {"uid": uid}
    if title is not None:
        title = title.strip()
        if not title:
            raise TermsError(422, "Title is required.")
        sets.append("title = :title")
        params["title"] = title
    if audience is not None:
        sets.append("audience = CAST(:audience AS terms.audience)")
        params["audience"] = _validate_audience(audience)
    if required_at is not None:
        sets.append("required_at = CAST(:req AS text[])")
        params["req"] = _validate_required_at(required_at)
    if applicability is not None:
        sets.append("applicability = CAST(:app AS jsonb)")
        params["app"] = json.dumps(applicability)
    kind = _validate_kind(body_kind) if body_kind is not None else None
    if kind is not None:
        sets.append("body_kind = CAST(:kind AS terms.body_kind)")
        params["kind"] = kind
        if kind == "html":
            sets.append("file_key = NULL")
            sets.append("file_content_type = NULL")
            sets.append("file_size = NULL")
            sets.append("document_id = NULL")
    # ONE assignment to body_md, whatever the caller sent. A PDF term has no markdown body, so the
    # kind decides the column and copy posted alongside it is dropped. Emitting the clear AND the
    # assignment is `multiple assignments to same column`, which Postgres refuses outright — and
    # the editor always posts `body_md`, so every save or publish of a PDF term was a 500.
    if kind == "pdf":
        sets.append("body_md = NULL")
    elif body_md is not None:
        sets.append("body_md = :md")
        params["md"] = body_md
    if clear_file:
        sets.append("file_key = NULL")
        sets.append("file_content_type = NULL")
        sets.append("file_size = NULL")
        sets.append("document_id = NULL")
    await session.execute(
        text(f"UPDATE terms.terms SET {', '.join(sets)} WHERE uid = :uid"),  # noqa: S608
        params,
    )
    return _require_row(await get_term(session, uid))


async def set_pdf_file(
    session: AsyncSession,
    uid: str,
    *,
    file_key: str,
    content_type: str,
    size: int,
) -> dict[str, Any]:
    result = await session.execute(
        text(
            "UPDATE terms.terms SET body_kind = 'pdf', file_key = :k, file_content_type = :ct, "
            "file_size = :sz, body_md = NULL, updated_at = now() "
            "WHERE uid = :uid AND status <> 'archived'"
        ),
        {"k": file_key, "ct": content_type, "sz": size, "uid": uid},
    )
    if int(getattr(result, "rowcount", 0) or 0) == 0:
        raise TermsError(404, "Not found.")
    return _require_row(await get_term(session, uid))


async def publish_term(session: AsyncSession, uid: str, actor_id: str | None) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, tenant_id, title, status::text AS status, "
                    "       body_kind::text AS body_kind, body_md, file_key, "
                    "       file_content_type, file_size, document_id "
                    "FROM terms.terms WHERE uid = :uid FOR UPDATE"
                ),
                {"uid": uid},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise TermsError(404, "Not found.")
    if row["status"] == "archived":
        raise TermsError(409, "Archived terms cannot be published.")
    kind = row["body_kind"]
    html: str | None = None
    if kind == "html":
        html = declaration_html(row["body_md"] or "")
        if not html:
            raise TermsError(422, "Write the terms before publishing.")
    elif not row["file_key"] and not row["document_id"]:
        raise TermsError(422, "Upload a PDF before publishing.")
    n = int(
        (
            await session.execute(
                text(
                    "SELECT COALESCE(MAX(version_number), 0) "
                    "FROM terms.term_versions WHERE term_id = :id"
                ),
                {"id": row["id"]},
            )
        ).scalar_one()
    )
    version_uid = new_id()
    digest = content_hash(body_kind=kind, body_html=html, file_key=row["file_key"])
    version_id = (
        await session.execute(
            text(
                "INSERT INTO terms.term_versions "
                "  (uid, tenant_id, term_id, version_number, title, body_kind, body_html, "
                "   file_key, file_content_type, file_size, document_id, content_hash, "
                "   published_by) "
                "VALUES (:uid, :t, :term, :n, :title, CAST(:kind AS terms.body_kind), :html, "
                "        :fk, :ct, :sz, :doc, :hash, CAST(:actor AS uuid)) "
                "RETURNING id"
            ),
            {
                "uid": version_uid,
                "t": row["tenant_id"],
                "term": row["id"],
                "n": n + 1,
                "title": row["title"],
                "kind": kind,
                "html": html,
                "fk": row["file_key"],
                "ct": row["file_content_type"],
                "sz": row["file_size"],
                "doc": row["document_id"],
                "hash": digest,
                "actor": actor_id,
            },
        )
    ).scalar_one()
    await session.execute(
        text(
            "UPDATE terms.terms SET status = 'published', "
            "current_published_version_id = :vid, published_at = COALESCE(published_at, now()), "
            "updated_at = now() WHERE id = :id"
        ),
        {"vid": version_id, "id": row["id"]},
    )
    return _require_row(await get_term(session, uid))


async def archive_term(session: AsyncSession, uid: str) -> dict[str, Any]:
    result = await session.execute(
        text(
            "UPDATE terms.terms SET status = 'archived', updated_at = now() "
            "WHERE uid = :uid AND status <> 'archived'"
        ),
        {"uid": uid},
    )
    if int(getattr(result, "rowcount", 0) or 0) == 0:
        existing = await get_term(session, uid)
        if existing is None:
            raise TermsError(404, "Not found.")
    return _require_row(await get_term(session, uid))


async def ensure_published_term(
    session: AsyncSession,
    *,
    owner: Owner,
    tenant_id: int | None,
    code: str,
    title: str,
    content: str,
    audience: str,
    required_at: list[str],
    applicability: dict[str, Any],
) -> str:
    """Idempotent seed: return the uid, creating and publishing once if missing.

    A term that already exists is left alone when it has a published body. An empty
    publication is republished from ``content``: the seed carries the declaration, and
    an earlier publish that stored no HTML left the client with a tick and nothing to read.
    """
    existing = await get_term_by_code(session, tenant_id=tenant_id, code=code)
    if existing is not None:
        published_html = str(existing.get("published_body_html") or "").strip()
        if (
            existing.get("status") != "archived"
            and content.strip()
            and not published_html
            and str(existing.get("body_kind") or "html") == "html"
        ):
            await session.execute(
                text(
                    "UPDATE terms.terms SET body_md = :body, body_kind = 'html', "
                    "updated_at = now() WHERE uid = :uid"
                ),
                {"body": content, "uid": existing["uid"]},
            )
            await publish_term(session, str(existing["uid"]), None)
        return str(existing["uid"])
    created = await create_term(
        session,
        owner=owner,
        tenant_id=tenant_id,
        actor_id=None,
        title=title,
        code=code,
        audience=audience,
        required_at=required_at,
        applicability=applicability,
        body_kind="html",
        body_md=content,
    )
    published = await publish_term(session, str(created["uid"]), None)
    return str(published["uid"])


@dataclass(frozen=True)
class OutstandingContext:
    required_at: RequiredAt
    subject: SubjectKind
    contact_id: int | None = None
    user_id: str | None = None
    product: str | None = None
    has_transfer: bool | None = None
    #: Set this from an elevated caller — see ``list_terms``.
    tenant_id: int | None = None
    #: Restrict to one side of the catalogue. Only the onboarding gate sets it — see
    #: ``onboarding_outstanding_empty``.
    owner: Owner | None = None


async def outstanding_terms(session: AsyncSession, ctx: OutstandingContext) -> list[dict[str, Any]]:
    """Published terms this subject still needs to accept (current version only)."""
    rows = await list_terms(session, status="published", tenant_id=ctx.tenant_id, owner=ctx.owner)
    accepted = await _accepted_version_ids(
        session, contact_id=ctx.contact_id, user_id=ctx.user_id, subject=ctx.subject
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        if ctx.required_at not in (row.get("required_at") or []):
            continue
        if not audience_matches(str(row["audience"]), ctx.subject):
            continue
        if not applies(
            _as_applicability(row.get("applicability")),
            product=ctx.product,
            has_transfer=ctx.has_transfer,
        ):
            continue
        version_id = row.get("current_published_version_id")
        if version_id is None or int(version_id) in accepted:
            continue
        out.append(row)
    return out


async def _accepted_version_ids(
    session: AsyncSession,
    *,
    contact_id: int | None,
    user_id: str | None,
    subject: SubjectKind,
) -> set[int]:
    if subject == "contact":
        if contact_id is None:
            return set()
        rows = (
            await session.execute(
                text("SELECT term_version_id FROM terms.acceptances WHERE contact_id = :c"),
                {"c": contact_id},
            )
        ).scalars()
        return {int(v) for v in rows}
    if user_id is None:
        return set()
    rows = (
        await session.execute(
            text(
                "SELECT term_version_id FROM terms.acceptances "
                "WHERE contact_id IS NULL AND user_id = CAST(:u AS uuid)"
            ),
            {"u": user_id},
        )
    ).scalars()
    return {int(v) for v in rows}


async def onboarding_outstanding(
    session: AsyncSession,
    *,
    tenant_id: int,
    contact_ids: list[int],
    products: list[str | None],
    has_transfer: bool,
) -> list[dict[str, Any]]:
    """The firm's current onboarding terms some listed contact has still to accept, one row per
    term, in catalogue order.

    ``tenant_id`` is required rather than optional: the callers are the flow engine, which runs
    elevated, and this answer decides whether an account may be opened.

    **The firm's OWN terms only.** Graphene's platform-wide terms are not a firm journey's to
    collect — no seeded or authored ``accept_terms`` step pins one. Counting them here deadlocked
    every application: the privacy policy is ``required_at`` onboarding AND login, so an adviser
    completing a journey with the client in the room had no step through which to clear it, and a
    client who never logs in never would. This gate asks the question the journey is answerable
    for.
    """
    if not contact_ids:
        return []
    axes = products or [None]
    out: dict[str, dict[str, Any]] = {}
    for contact_id in contact_ids:
        for product in axes:
            leftover = await outstanding_terms(
                session,
                OutstandingContext(
                    required_at="onboarding",
                    subject="contact",
                    contact_id=contact_id,
                    product=product,
                    has_transfer=has_transfer,
                    tenant_id=tenant_id,
                    owner="tenant",
                ),
            )
            for row in leftover:
                out.setdefault(str(row["uid"]), row)
    return list(out.values())


async def onboarding_outstanding_empty(
    session: AsyncSession,
    *,
    tenant_id: int,
    contact_ids: list[int],
    products: list[str | None],
    has_transfer: bool,
) -> bool:
    """True when every listed contact has accepted every current onboarding term that applies.
    See ``onboarding_outstanding`` for what counts."""
    return not await onboarding_outstanding(
        session,
        tenant_id=tenant_id,
        contact_ids=contact_ids,
        products=products,
        has_transfer=has_transfer,
    )


async def has_accepted_current(
    session: AsyncSession,
    *,
    term_uid: str,
    contact_id: int | None = None,
    user_id: str | None = None,
) -> bool:
    term = await get_term(session, term_uid)
    if term is None or term.get("current_published_version_id") is None:
        return False
    version_id = int(term["current_published_version_id"])
    if contact_id is not None:
        found = (
            await session.execute(
                text(
                    "SELECT 1 FROM terms.acceptances WHERE contact_id = :c AND term_version_id = :v"
                ),
                {"c": contact_id, "v": version_id},
            )
        ).first()
        return found is not None
    if user_id is None:
        return False
    found = (
        await session.execute(
            text(
                "SELECT 1 FROM terms.acceptances "
                "WHERE contact_id IS NULL AND user_id = CAST(:u AS uuid) "
                "  AND term_version_id = :v"
            ),
            {"u": user_id, "v": version_id},
        )
    ).first()
    return found is not None


async def accept_term(
    session: AsyncSession,
    uid: str,
    *,
    subject: SubjectKind,
    tenant_id: int | None,
    contact_id: int | None,
    user_id: str | None,
    accepted_by: str | None,
    channel: Channel = "portal",
    application_id: int | None = None,
    attested_by: str | None = None,
    version_id: int | None = None,
    # The signed copy an in-person acceptance was recorded against (``documents.documents.id``).
    # A portal acceptance carries none: the client's own session is its evidence.
    evidence_document_id: str | None = None,
) -> dict[str, Any]:
    term = await get_term(session, uid)
    if term is None or term["status"] != "published":
        raise TermsError(404, "Not found.")
    if not audience_matches(str(term["audience"]), subject):
        raise TermsError(422, "These terms are not for this audience.")
    pin = version_id or term.get("current_published_version_id")
    if pin is None:
        raise TermsError(409, "This term has no published version.")
    if subject == "contact":
        if contact_id is None or tenant_id is None:
            raise TermsError(422, "A client acceptance needs a contact.")
    elif user_id is None:
        raise TermsError(422, "A staff acceptance needs a user.")
    existing = (
        await session.execute(
            text(
                "SELECT uid FROM terms.acceptances "
                "WHERE term_version_id = :v AND ("
                "  (CAST(:c AS int) IS NOT NULL AND contact_id = CAST(:c AS int)) "
                "  OR (CAST(:c AS int) IS NULL AND contact_id IS NULL "
                "      AND user_id = CAST(:u AS uuid))"
                ")"
            ),
            {"v": int(pin), "c": contact_id, "u": user_id},
        )
    ).scalar_one_or_none()
    if existing:
        row = await get_acceptance(session, str(existing))
        return _require_row(row)
    acc_uid = new_id()
    try:
        await session.execute(
            text(
                "INSERT INTO terms.acceptances "
                "  (uid, tenant_id, contact_id, user_id, term_id, term_version_id, "
                "   application_id, accepted_by, channel, attested_by, evidence_document_id) "
                "VALUES (:uid, :t, :c, CAST(:u AS uuid), :term, :v, :app, "
                "        CAST(:by AS uuid), CAST(:ch AS terms.channel), CAST(:att AS uuid), "
                "        CAST(:evidence AS uuid))"
            ),
            {
                "uid": acc_uid,
                "t": tenant_id if subject == "contact" else None,
                "c": contact_id if subject == "contact" else None,
                "u": user_id if subject == "user" else accepted_by,
                "term": term["id"],
                "v": int(pin),
                "app": application_id,
                "by": accepted_by,
                "ch": channel,
                "att": attested_by,
                "evidence": evidence_document_id,
            },
        )
    except Exception as exc:
        raise TermsError(409, "These terms have already been accepted.") from exc
    if subject == "contact" and contact_id is not None and tenant_id is not None:
        await refresh_terms_kyc(
            session, tenant_id=tenant_id, contact_id=contact_id, product=None, has_transfer=None
        )
    return _require_row(await get_acceptance(session, acc_uid))


async def get_acceptance(session: AsyncSession, uid: str) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text(
                    "SELECT a.uid, a.accepted_at, a.channel::text AS channel, "
                    "       t.uid AS term_uid, t.title, t.code, t.owner::text AS owner, "
                    "       v.version_number, v.uid AS version_uid, "
                    "       ed.uid AS evidence_document_uid "
                    "FROM terms.acceptances a "
                    "JOIN terms.terms t ON t.id = a.term_id "
                    "JOIN terms.term_versions v ON v.id = a.term_version_id "
                    "LEFT JOIN documents.documents ed ON ed.id = a.evidence_document_id "
                    "WHERE a.uid = :u"
                ),
                {"u": uid},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def get_contact_acceptance(
    session: AsyncSession, contact_id: int, acceptance_uid: str
) -> dict[str, Any] | None:
    """One of the contact's acceptances, with the very version they accepted: its text, or whether
    it is a file. None when it is not this contact's."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT a.uid, a.accepted_at, a.channel::text AS channel, "
                    "       t.uid AS term_uid, t.title, t.code, t.owner::text AS owner, "
                    "       v.version_number, v.uid AS version_uid, v.title AS version_title, "
                    "       v.body_kind::text AS body_kind, v.body_html, v.file_key, "
                    "       v.file_content_type, ed.uid AS evidence_document_uid "
                    "FROM terms.acceptances a "
                    "JOIN terms.terms t ON t.id = a.term_id "
                    "JOIN terms.term_versions v ON v.id = a.term_version_id "
                    "LEFT JOIN documents.documents ed ON ed.id = a.evidence_document_id "
                    "WHERE a.contact_id = :c AND a.uid = :a"
                ),
                {"c": contact_id, "a": acceptance_uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


async def list_contact_acceptances(session: AsyncSession, contact_id: int) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                text(
                    "SELECT a.uid, a.accepted_at, a.channel::text AS channel, "
                    "       t.uid AS term_uid, t.title, t.code, t.owner::text AS owner, "
                    "       v.version_number, v.uid AS version_uid, "
                    # The signed copy, so the client record can open it from the acceptance.
                    "       ed.uid AS evidence_document_uid "
                    "FROM terms.acceptances a "
                    "JOIN terms.terms t ON t.id = a.term_id "
                    "JOIN terms.term_versions v ON v.id = a.term_version_id "
                    "LEFT JOIN documents.documents ed ON ed.id = a.evidence_document_id "
                    "WHERE a.contact_id = :c "
                    "ORDER BY a.accepted_at DESC"
                ),
                {"c": contact_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def refresh_terms_kyc(
    session: AsyncSession,
    *,
    tenant_id: int,
    contact_id: int,
    product: str | None,
    has_transfer: bool | None,
) -> None:
    """Pass the Compliance tick when this contact has no outstanding onboarding terms.

    Scoped to the firm's own terms for the same reason as ``onboarding_outstanding_empty``: this is
    the firm's statement about its own onboarding, and Graphene's platform terms are not collected
    on that journey.
    """
    leftover = await outstanding_terms(
        session,
        OutstandingContext(
            required_at="onboarding",
            subject="contact",
            contact_id=contact_id,
            product=product,
            has_transfer=has_transfer,
            tenant_id=tenant_id,
            owner="tenant",
        ),
    )
    if leftover:
        return
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    accepted = await list_contact_acceptances(session, contact_id)
    # `default=str` because each acceptance carries `accepted_at` — the acceptance timestamp is the
    # evidence, so it has to survive into the check's detail rather than raise on the way.
    payload = json.dumps({"accepted": accepted, "source": "terms"}, default=str)
    valid_until = compute_valid_until(now, KIND_TERMS)
    async with as_service_role(session):
        existing = (
            await session.execute(
                text(
                    "SELECT id FROM crm.kyc_checks "
                    "WHERE tenant_id = :t AND contact_id = :c AND kind = :k "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"t": tenant_id, "c": contact_id, "k": KIND_TERMS},
            )
        ).scalar_one_or_none()
        if existing is not None:
            await session.execute(
                text(
                    "UPDATE crm.kyc_checks SET status = 'passed', provider = 'manual', "
                    "  verified_at = :v, valid_until = :vu, raw = raw || CAST(:raw AS jsonb) "
                    "WHERE id = :id"
                ),
                {"v": now, "vu": valid_until, "raw": payload, "id": int(existing)},
            )
            return
        await session.execute(
            text(
                "INSERT INTO crm.kyc_checks "
                "  (uid, tenant_id, contact_id, kind, status, provider, verified_at, valid_until, "
                "   raw) "
                "VALUES (:u, :t, :c, :k, 'passed', 'manual', :v, :vu, CAST(:raw AS jsonb))"
            ),
            {
                "u": new_id(),
                "t": tenant_id,
                "c": contact_id,
                "k": KIND_TERMS,
                "v": now,
                "vu": valid_until,
                "raw": payload,
            },
        )
