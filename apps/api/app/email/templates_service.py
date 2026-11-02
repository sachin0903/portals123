"""Email-template persistence — the SINGLE source of the ``email.templates`` / ``template_versions``
SQL.

Both the firm-facing router (``app/routers/email_templates.py``, ``tenant_session`` / RLS
``authenticated``) and the admin per-tenant authoring router (``app/routers/admin.py``,
``admin_session`` / ``service_role``) call these functions, so the authoring logic — the
version-roll, the publish gates, restore, the clone — exists ONCE rather than in two copies
that drift.

Every function takes an EXPLICIT ``tenant_id`` and ``actor_id`` instead of the SQL claim helpers
(``auth.tenant_id()`` / ``auth.uid()``), because ``service_role`` has no claim-derived identity:
the firm router passes the RLS-derived values, the admin router the path tenant + operator uuid.
All statements are tenant-scoped by ``tenant_id`` explicitly, so a ``service_role`` caller cannot
cross tenants by accident and an RLS caller's ``WITH CHECK`` still enforces isolation on top.

HTTP-free by design: failures raise the domain errors below; the routers map them to responses.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.email import catalog as email_catalog
from app.email import content as email_content
from app.email import defaults as email_defaults
from app.email import preview as email_preview
from app.email import transport as email_mod
from app.ids import new_id
from app.render import jinja_engine

# The authoring SIDE (0051), stamped at WRITE time: `created_by` names a person and so cannot say
# that Graphene operations wrote this. Declared per call site, never derived — membership moves.
AuthorScope = Literal["firm", "operations"]


class EmailTemplateError(Exception):
    """Base for authoring failures the routers translate into HTTP responses."""


class DuplicateCode(EmailTemplateError):
    """A template with this ``code`` already exists for the tenant (UNIQUE(tenant_id, code))."""


class TemplateNotFound(EmailTemplateError):
    """No template with this uid in this tenant."""


class VersionNotFound(EmailTemplateError):
    """No such version for this template."""


class NotADraft(EmailTemplateError):
    """Only a draft version can be published."""


class PublishedImmutable(EmailTemplateError):
    """The head version was published between this save's read and its write.

    ``save_version`` decides in-place-vs-roll from an UNLOCKED read of the head's status, so a
    publish committing in that window leaves the UPDATE aiming at a now-published row — which
    ``0062``'s ``reject_published_edit`` trigger refuses (SQLSTATE 23001). That is a lost update,
    i.e. a conflict, not a server fault: the author's editor is holding a version that is no longer
    the draft. The remedy is to reload and save again, which rolls a fresh ``v(n+1)``.

    Raised only from the in-place branch; every other version write INSERTs and cannot trip it.
    """


class PublishBlocked(EmailTemplateError):
    """A publish gate failed (missing subject, empty body, or leftover editor placeholder copy).
    ``reason`` is client-safe."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class VersionConflict(EmailTemplateError):
    """A concurrent write took the next ``version_number`` first.

    ``save_version``'s roll-a-new-draft branch and ``restore_version`` both compute the next number
    from an UNLOCKED ``MAX(version_number)+1`` and then INSERT under ``UNIQUE(template_id,
    version_number)``. Two writes racing for the same template (a double-clicked Restore, two tabs
    autosaving onto a stale/published head, a retried POST) can compute the same number; the loser's
    INSERT breaches the constraint. That is a conflict, not a server fault — reload and retry."""


class InvalidSubjects(EmailTemplateError):
    """A version's declared subjects were not a list, or named a kind no send could resolve.
    ``reason`` is client-safe and names the offending kind."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ── read projections (mirror the firm router's shapes) ─────────────────────────
_SELECT = (
    "SELECT t.uid, t.code, t.name, t.category, t.description, t.archived_at, t.created_at, "
    "       u.name AS created_by, "
    "       (SELECT count(*) FROM email.template_versions v WHERE v.template_id = t.id) "
    "         AS version_count, "
    "       lv.version_number AS current_version_number, "
    "       lv.status::text AS current_version_status "
    "FROM email.templates t "
    "LEFT JOIN auth.users u ON u.id = t.created_by "
    "LEFT JOIN LATERAL ("
    "  SELECT version_number, status FROM email.template_versions v "
    "  WHERE v.template_id = t.id ORDER BY version_number DESC LIMIT 1"
    ") lv ON true "
)

_VERSION_COLS = (
    "v.uid, v.version_number, v.status::text AS status, v.subject, v.preheader, "
    "v.created_at, cu.name AS created_by, v.published_at, pu.name AS published_by, "
    # NULL for rows written before 0051 — honestly unknown, not back-dated to 'firm'.
    "v.authored_by_scope"
)
_VERSION_FROM = (
    "FROM email.template_versions v "
    "LEFT JOIN auth.users cu ON cu.id = v.created_by "
    "LEFT JOIN auth.users pu ON pu.id = v.published_by "
)


# SQLSTATE 23001 (restrict_violation) — 0062's trigger; matched on the code, never the message.
_RESTRICT_VIOLATION = "23001"


def _is_published_immutable(exc: IntegrityError) -> bool:
    """Whether an IntegrityError is 0062's published-version refusal rather than a real constraint
    breach. ``exc.orig.sqlstate`` is psycopg 3's spelling; ``pgcode`` is psycopg 2's, and SQLAlchemy
    exposes whichever driver raised — so read both rather than assume the driver."""
    orig = getattr(exc, "orig", None)
    code = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return code == _RESTRICT_VIOLATION


# SQLSTATE 23505 (unique_violation) — the version-number race in save/restore (see VersionConflict).
_UNIQUE_VIOLATION = "23505"


def _is_unique_violation(exc: IntegrityError) -> bool:
    """Whether an IntegrityError is a UNIQUE breach, read the same driver-agnostic way as
    :func:`_is_published_immutable`."""
    orig = getattr(exc, "orig", None)
    code = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return code == _UNIQUE_VIOLATION


# 0042's UNIQUE (tenant_id, code), by Postgres's default name off the table-local `templates`.
_CODE_UNIQUE = "templates_tenant_id_code_key"


def _is_duplicate_code(exc: IntegrityError) -> bool:
    """Whether an IntegrityError is the CODE already being taken. The table carries a second unique
    (`templates_uid_key`) plus the two foreign keys, so the sqlstate alone cannot answer this — and
    "an email template with this code already exists" is a verdict on the code, not on whatever
    else the write breached."""
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    return _is_unique_violation(exc) and getattr(diag, "constraint_name", None) == _CODE_UNIQUE


def _new_template_uid() -> str:
    return new_id()


def _new_version_uid() -> str:
    return new_id()


async def _template_id(session: AsyncSession, tenant_id: int, uid: str) -> int | None:
    """Internal id for a template uid within a tenant (None if absent)."""
    return (
        await session.execute(
            text("SELECT id FROM email.templates WHERE uid = :uid AND tenant_id = :t"),
            {"uid": uid, "t": tenant_id},
        )
    ).scalar_one_or_none()


async def fetch(session: AsyncSession, tenant_id: int, uid: str) -> dict[str, Any] | None:
    """One template's list-row projection, tenant-scoped (None if absent)."""
    row = (
        (
            await session.execute(
                text(_SELECT + "WHERE t.uid = :uid AND t.tenant_id = :t"),
                {"uid": uid, "t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


async def list_templates(session: AsyncSession, tenant_id: int) -> list[dict[str, Any]]:
    """A tenant's templates, newest first — all of them.

    There is no archived half to select any more: a template a firm has finished with is DELETED
    (:func:`delete_template`), which gives its code back. ``email.templates.archived_at`` survives
    as a column nothing writes, to be dropped by a later contract migration.
    """
    rows = (
        (
            await session.execute(
                text(_SELECT + "WHERE t.tenant_id = :t ORDER BY t.created_at DESC, t.id DESC"),
                {"t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


#: What an EMAIL may be about. Named for the email side because `flows/triggers.py` declares a bare
#: `SUBJECT_KINDS` — a DIFFERENT set (it adds `application` and `case`, and has no `document`) for a
#: different question, which is what a trigger's subject can be. They shared a name and only three
#: members, so a grep-driven wiring onto the wrong one drops `document` and un-claims every template
#: about a document, or offers a kind no `SCOPE_FIELDS` entry can resolve.
#:
#: A template says which apply so the editor lists only the event details a real send could resolve,
#: instead of every field any event might carry.
#:
#: ORDER IS A STORAGE GUARANTEE, not presentation: `normalise_subjects` returns the wanted kinds IN
#: THIS ORDER, so two templates claiming the same set store identical bytes.
EMAIL_SUBJECT_KINDS: tuple[str, ...] = ("contact", "account", "task", "document")


def normalise_subjects(raw: Any) -> list[str]:
    """Validate a template's declared subjects, in :data:`EMAIL_SUBJECT_KINDS` order.

    Order is imposed rather than preserved so two templates claiming the same set store the same
    bytes; duplicates collapse. An unknown kind is REFUSED and named — the author picked from a
    fixed list, so anything else is a client bug rather than a typo to tolerate.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise InvalidSubjects("The declared subjects must be a list.")
    wanted = {str(k).strip() for k in raw if str(k).strip()}
    unknown = sorted(wanted - set(EMAIL_SUBJECT_KINDS))
    if unknown:
        raise InvalidSubjects(
            f"'{unknown[0]}' is not something an email can be about — "
            f"use one of {', '.join(EMAIL_SUBJECT_KINDS)}."
        )
    return [k for k in EMAIL_SUBJECT_KINDS if k in wanted]


def _with_normalised_subjects(schema: dict[str, Any]) -> dict[str, Any]:
    """``schema`` with its ``subjects`` validated — and the key REMOVED when nothing is claimed.

    Removed rather than stored as ``[]`` so a template claiming nothing carries no such key at all,
    which is what makes "absent" and "claims none" one state instead of two. THE SAME OBJECT when it
    mentions no subjects: every other writer of this jsonb (restore, clone, the model preset) relies
    on being left alone, and a test pins the identity.
    """
    if "subjects" not in schema:
        return schema
    out = dict(schema)
    subjects = normalise_subjects(schema["subjects"])
    if subjects:
        out["subjects"] = subjects
    else:
        out.pop("subjects", None)
    return out


async def create_template(
    session: AsyncSession,
    tenant_id: int,
    actor_id: str,
    *,
    code: str,
    name: str,
    category: str,
    description: str | None,
    scope: AuthorScope,
    subjects: Any = None,
) -> dict[str, Any]:
    """Create a template with an initial empty draft (v1). ``scope`` records which side authored
    that first draft (see :data:`AuthorScope`). Raises :class:`DuplicateCode`.

    ``subjects`` is what the email is ABOUT, asked at creation because it decides which event
    details the editor may list — an author who has not said cannot be shown a narrowed list."""
    template_uid = _new_template_uid()
    try:
        template_id = (
            await session.execute(
                text(
                    "INSERT INTO email.templates "
                    "  (uid, tenant_id, code, name, category, description, created_by) "
                    "VALUES (:uid, :t, :code, :name, :cat, :desc, :actor) RETURNING id"
                ),
                {
                    "uid": template_uid,
                    "t": tenant_id,
                    "code": code,
                    "name": name,
                    "cat": category,
                    "desc": description,
                    "actor": actor_id,
                },
            )
        ).scalar_one()
    except IntegrityError as exc:
        if _is_duplicate_code(exc):
            raise DuplicateCode(str(exc)) from exc
        raise
    await session.execute(
        text(
            "INSERT INTO email.template_versions "
            "  (uid, tenant_id, template_id, version_number, status, schema, created_by, "
            "   authored_by_scope) "
            "VALUES (:uid, :t, :tid, 1, 'draft', CAST(:schema AS jsonb), :actor, :scope)"
        ),
        {
            "uid": _new_version_uid(),
            "t": tenant_id,
            "tid": template_id,
            "actor": actor_id,
            "scope": scope,
            # `{}` for a template about nothing in particular — the key is absent, not empty.
            "schema": json.dumps(
                {"subjects": kinds} if (kinds := normalise_subjects(subjects)) else {}
            ),
        },
    )
    created = await fetch(session, tenant_id, template_uid)
    assert created is not None  # noqa: S101 — just inserted in this txn
    return created


async def update_template(
    session: AsyncSession,
    tenant_id: int,
    uid: str,
    *,
    name: str | None,
    code: str | None,
    category: str | None,
    description: str | None,
    description_set: bool = False,
) -> dict[str, Any]:
    """Update a template's metadata (partial). Raises ``DuplicateCode`` / ``TemplateNotFound``.

    ``description`` is the one CLEARABLE field, so it needs two bits and not one. ``COALESCE`` alone
    cannot express "delete this": a null read as "leave it alone" meant deleting a description
    restored the old one and reported success, on both list surfaces (each posts
    ``trim() || null``). So presence is passed separately — ``description_set`` is the caller's
    ``'description' in model_fields_set`` — and an explicitly-sent blank clears the column while an
    ABSENT key still preserves it. The same distinction ``routers/templates.py`` draws for
    ``input_form_code``; the blank is normalised to NULL rather than stored as ``''``, so a cleared
    description is one value and not two."""
    try:
        row = (
            await session.execute(
                text(
                    "UPDATE email.templates SET "
                    "  name = COALESCE(:name, name), "
                    "  code = COALESCE(:code, code), "
                    "  category = COALESCE(:cat, category), "
                    "  description = CASE WHEN :desc_set "
                    "    THEN NULLIF(BTRIM(CAST(:desc AS text)), '') ELSE description END "
                    "WHERE uid = :uid AND tenant_id = :t RETURNING id"
                ),
                {
                    "name": name,
                    "code": code,
                    "cat": category,
                    "desc": description,
                    "desc_set": description_set,
                    "uid": uid,
                    "t": tenant_id,
                },
            )
        ).first()
    except IntegrityError as exc:
        if _is_duplicate_code(exc):
            raise DuplicateCode(str(exc)) from exc
        raise
    if row is None:
        raise TemplateNotFound(uid)
    updated = await fetch(session, tenant_id, uid)
    assert updated is not None  # noqa: S101
    return updated


async def delete_template(session: AsyncSession, tenant_id: int, uid: str) -> dict[str, Any]:
    """Delete a template and its whole version history. Irreversible; returns the row it destroyed.

    ONE statement, against the PARENT only. ``template_versions.template_id`` is
    ``ON DELETE CASCADE``, and the circular ``current_published_version_id`` FK is
    ``DEFERRABLE INITIALLY DEFERRED``, so the check runs at commit — by which point the referencing
    row is gone too. Deleting versions first is the trap: that FK then fails at COMMIT, outside
    any handler here, and nulling the pointer first would fire the immutability trigger for nothing.

    A firm gets its ``code`` back, which soft-archiving could never give it: ``UNIQUE(tenant_id,
    code)`` has no archived predicate, so an archived template kept squatting its own name.

    Raises :class:`TemplateNotFound`, and NOTHING ELSE — a template a firm asks to delete is
    deleted. What still names its ``code`` is the author's business and is put to them in the
    confirmation, which lists the notification rules that render it; a rule on a code with a shipped
    default simply falls back to it. The accepted consequence, stated so it is not rediscovered: a
    PUBLISHED journey whose ``send_email`` names this code has no such fallback, so it will park
    that step until someone re-points it.

    Recovery after the fact is the ``audit.record`` trigger, which keeps the destroyed row — and it
    is reachable only with database access.
    """
    if await _template_id(session, tenant_id, uid) is None:
        # Asked before deleting, and never inferred from the rowcount: under RLS an absent row and
        # another tenant's row are the same zero, and a second delete must answer 404 rather than
        # the cheerful 200 the archive it replaces gave an already-archived template.
        raise TemplateNotFound(uid)
    row = await fetch(session, tenant_id, uid)
    if row is None:  # pragma: no cover — _template_id just found it in this transaction
        raise TemplateNotFound(uid)
    await session.execute(
        text("DELETE FROM email.templates WHERE uid = :uid AND tenant_id = :t"),
        {"uid": uid, "t": tenant_id},
    )
    return row


async def list_versions(session: AsyncSession, tenant_id: int, uid: str) -> list[dict[str, Any]]:
    """A template's version history, newest first. Raises :class:`TemplateNotFound`."""
    tid = await _template_id(session, tenant_id, uid)
    if tid is None:
        raise TemplateNotFound(uid)
    rows = (
        (
            await session.execute(
                text(
                    f"SELECT {_VERSION_COLS} {_VERSION_FROM} "
                    "WHERE v.template_id = :tid ORDER BY v.version_number DESC"
                ),
                {"tid": tid},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def get_version(session: AsyncSession, tenant_id: int, uid: str, vid: str) -> dict[str, Any]:
    """One version incl. its editor content (``schema``). Raises :class:`VersionNotFound`."""
    row = (
        (
            await session.execute(
                text(
                    f"SELECT {_VERSION_COLS}, v.schema AS schema_content {_VERSION_FROM} "
                    "JOIN email.templates t ON t.id = v.template_id "
                    "WHERE t.uid = :uid AND t.tenant_id = :t AND v.uid = :vid"
                ),
                {"uid": uid, "t": tenant_id, "vid": vid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise VersionNotFound(vid)
    data = dict(row)
    data["schema_content"] = data.get("schema_content") or {}
    return data


async def _version_detail_by_id(session: AsyncSession, version_id: int) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                text(
                    f"SELECT {_VERSION_COLS}, v.schema AS schema_content "
                    f"{_VERSION_FROM} WHERE v.id = :id"
                ),
                {"id": version_id},
            )
        )
        .mappings()
        .one()
    )
    data = dict(row)
    data["schema_content"] = data.get("schema_content") or {}
    return data


async def save_version(
    session: AsyncSession,
    tenant_id: int,
    actor_id: str,
    uid: str,
    *,
    subject: str,
    preheader: str,
    schema: dict[str, Any],
    scope: AuthorScope,
) -> dict[str, Any]:
    """Save the editor's content. Updates the head draft in place while it is a fresh draft; rolls
    to a new draft ``v(n+1)`` once the head is published or its 30-minute bucket elapses.

    ``scope`` records the writing side (:data:`AuthorScope`) on the version. On the in-place branch
    it is STICKY at ``'operations'``: a firm save landing on a draft Graphene already wrote into
    must not launder that fact away — the whole point is that the firm can tell. (``created_by``
    keeps its existing meaning, the version's originator, and is deliberately untouched here.)

    Raises :class:`TemplateNotFound`, and :class:`InvalidSubjects` for a claim that names a kind no
    send could resolve — checked HERE rather than at publish, so an author is told at the save they
    made it in. Both authoring doors (the firm router and the admin per-tenant one) come through
    here, so neither can store an invalid claim.
    """
    tid = await _template_id(session, tenant_id, uid)
    if tid is None:
        raise TemplateNotFound(uid)
    schema = _with_normalised_subjects(schema)
    latest = (
        (
            await session.execute(
                text(
                    "SELECT id, version_number, status::text AS status, "
                    # A draft is stale when it holds WORK from an earlier session — so the claim is
                    # subtracted first. `subjects` is a SETTING, written into v1 at creation, and
                    # counting it as content made a virgin draft look worked-on: the author's first
                    # body save 30 minutes later rolled v2 and stranded an empty v1.
                    "  (status = 'draft' AND (schema - 'subjects') <> '{}'::jsonb "
                    "   AND now() - created_at >= interval '30 minutes') AS stale "
                    "FROM email.template_versions "
                    "WHERE template_id = :tid ORDER BY version_number DESC LIMIT 1"
                ),
                {"tid": tid},
            )
        )
        .mappings()
        .one_or_none()
    )
    schema_json = json.dumps(schema)
    if latest is not None and latest["status"] == "draft" and not latest["stale"]:
        # Read without a lock, so a publish can land first; 0062 refuses it. That SQLSTATE only.
        try:
            version_id = (
                await session.execute(
                    text(
                        "UPDATE email.template_versions "
                        "SET schema = CAST(:schema AS jsonb), subject = :subject, "
                        "preheader = :preheader, "
                        "authored_by_scope = CASE "
                        "  WHEN :scope = 'operations' OR authored_by_scope = 'operations' "
                        "    THEN 'operations' ELSE :scope END "
                        "WHERE id = :id RETURNING id"
                    ),
                    {
                        "schema": schema_json,
                        "subject": subject,
                        "preheader": preheader,
                        "scope": scope,
                        "id": latest["id"],
                    },
                )
            ).scalar_one()
        except IntegrityError as exc:
            if _is_published_immutable(exc):
                raise PublishedImmutable(uid) from exc
            raise
    else:
        next_number = (latest["version_number"] + 1) if latest is not None else 1
        # next_number came from an unlocked read, so the loser's INSERT breaches UNIQUE — a 409.
        try:
            version_id = (
                await session.execute(
                    text(
                        "INSERT INTO email.template_versions "
                        "  (uid, tenant_id, template_id, version_number, status, subject, "
                        "   preheader, schema, created_by, authored_by_scope) "
                        "VALUES (:uid, :t, :tid, :num, 'draft', :subject, :preheader, "
                        "  CAST(:schema AS jsonb), :actor, :scope) RETURNING id"
                    ),
                    {
                        "uid": _new_version_uid(),
                        "t": tenant_id,
                        "tid": tid,
                        "num": next_number,
                        "subject": subject,
                        "preheader": preheader,
                        "schema": schema_json,
                        "actor": actor_id,
                        "scope": scope,
                    },
                )
            ).scalar_one()
        except IntegrityError as exc:
            if _is_unique_violation(exc):
                raise VersionConflict(uid) from exc
            raise
    return await _version_detail_by_id(session, version_id)


def _has_content(schema: Any) -> bool:
    """A version is publishable when its ``schema['html']`` renders something visible — substance,
    not bytes (see ``app/email/content.py``). Judged on ``html`` alone: that's the only thing the
    send path renders, so a project with no exported ``html`` would send blank."""
    if not isinstance(schema, dict):
        return False
    html = schema.get("html")
    return isinstance(html, str) and email_content.has_renderable_content(html)


def _placeholder_copy(schema: Any) -> str | None:
    """The first un-edited palette placeholder still in ``schema['html']``, or None — see
    ``app/email/content.py::placeholder_copy``. Judged on ``html`` alone for the same reason
    :func:`_has_content` is: that is the only thing the send path renders."""
    if not isinstance(schema, dict):
        return None
    html = schema.get("html")
    if not isinstance(html, str):
        return None
    return email_content.placeholder_copy(html)


def _render_failure(row: Mapping[str, Any] | Any, code: str) -> str | None:
    """The Jinja error a publish would ship, or None when the template renders.

    PUBLISH CHECKED ONLY THAT THE BODY WAS NOT EMPTY, and nothing on the send path catches a
    raise — so an unrenderable template published clean and then POISONED THE CONSUMER: it reads a
    batch and advances its cursor in ONE transaction, so a raise rolled the cursor back, the next
    pass re-read the same event, and no notification email sent for ANY tenant until someone found
    the template. The cursor is a single global row; one firm's typo stopped everyone's mail.

    Two ways to reach it, both new: the editable HTML view lets an author type arbitrary Jinja, and
    a variable name may contain a hyphen, which Jinja reads as subtraction (`{{ client-ref }}` →
    "'client' is undefined").

    Rendered against the SAME sample context the preview uses, so an author cannot be refused here
    for something Preview showed working. A blank value is not a failure — only a raise is.
    """
    schema = row["schema"] if isinstance(row["schema"], dict) else {}
    context = email_preview.sample_context(email_mod.GRAPHENE_BRAND, code)

    # EVERY PART THE SEND RENDERS, and the preheader is one of them — it was missing, and its
    # omission is not cosmetic: `render_or_default` calls `render_published_email` with no
    # try/except and falls back only on a None ROW, so a Jinja error in the preview line propagates
    # out of the send site. On `invite` that is `provisioning`, which does not catch it either: a
    # published template with `{% if x %}` and no `{% endif %}` in its preview line answers 500 and
    # sends no invite at all, for every member from then on.
    for part in (
        str(row["subject"] or ""),
        str(row["preheader"] or ""),
        str(schema.get("html") or ""),
    ):
        try:
            jinja_engine.render(part, context)
        except Exception as exc:  # noqa: BLE001 — any Jinja error is the same answer to the author
            return str(exc)
    return None


def unsourced_tokens(row: Mapping[str, Any] | Any, code: str) -> list[str]:
    """Merge tokens in this version that nothing supplies — they render BLANK on every send.

    A WARNING, NEVER A REFUSAL, for two reasons that each stand alone. An unknown token publishing
    clean is a decision the suite pins (`_render_failure`'s own "a blank value is not a failure").
    And the codebase already answers this class of drift the same way — the flow tier names an
    unsourced token in the action detail rather than refusing the send, and the notification
    consumer drops a stale rule value rather than stopping the notice.
    `app/routers/templates.py` returns the document side's equivalent as a non-blocking warning too.

    INCOMPLETE BY CONSTRUCTION, so the wording an author reads must say what it checked rather than
    pronounce the template clean: a token behind `{% if %}` is never evaluated, and a token whose
    value is present-but-null is DEFINED, so neither is reported.

    NOTHING for a code the catalogue does not know. `sample_context` answers a custom code with
    EVERY sample key it has, which is the preview being permissive rather than a claim about a send
    site — such a code sends through the flow action against flow facts instead, so a list derived
    from the sample context would be noise in both directions.

    Rendered separately from `_render_failure` rather than folded into it: that function's contract
    is "does this raise", four tests hold it to a `str | None`, and one more sample render at
    publish costs about five milliseconds.
    """
    if code not in email_preview.scoped_codes():
        return []
    schema = row["schema"] if isinstance(row["schema"], dict) else {}
    context = email_preview.sample_context(email_mod.GRAPHENE_BRAND, code)
    missing: set[str] = set()
    # The subject with autoescape OFF, as the send renders it (`render_text`); a header is plain
    # text on the wire, and matching the send is what keeps this list honest about that part.
    for part, autoescape in (
        (str(row["subject"] or ""), False),
        (str(row["preheader"] or ""), True),
        (str(schema.get("html") or ""), True),
    ):
        try:
            _, part_missing = jinja_engine.render_with_diagnostics(
                part, context, autoescape=autoescape
            )
        # A raise is `_render_failure`'s answer and it has already refused the publish by the time
        # this runs, so there is nothing to log and nothing to report about this part.
        except Exception:  # noqa: BLE001, S112
            continue
        missing.update(part_missing)
    return sorted(missing)


async def publish_version(
    session: AsyncSession,
    tenant_id: int,
    actor_id: str,
    uid: str,
    vid: str,
    *,
    scope: AuthorScope,
) -> dict[str, Any]:
    """Publish a draft: require a subject, a visible body and no leftover editor placeholder copy,
    archive the prior published version, mark this published, point the template at it.

    ``scope`` is the publishing side, applied with the same stickiness as
    :func:`save_version`: Graphene deciding to make a version LIVE on a firm's tenant is exactly
    the act the firm must be able to see, so an ops publish marks the version ``'operations'`` even
    when the firm authored its content (``published_by`` still names the person). A firm publishing
    its own draft never overwrites an ``'operations'`` stamp already on it.

    Raises :class:`VersionNotFound` / :class:`NotADraft` / :class:`PublishBlocked`."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT v.id, v.template_id, v.status::text AS status, v.subject, "
                    # `preheader` is SELECTed for the render gate below, not for the update: it is
                    # Jinja too, and it is rendered on every send
                    # (`email/render.py::render_published_email`).
                    "       v.preheader, v.schema, "
                    "       t.code "
                    "FROM email.template_versions v "
                    "JOIN email.templates t ON t.id = v.template_id "
                    "WHERE t.uid = :uid AND t.tenant_id = :t AND v.uid = :vid"
                ),
                {"uid": uid, "t": tenant_id, "vid": vid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise VersionNotFound(vid)
    if row["status"] != "draft":
        raise NotADraft(vid)
    if not (row["subject"] or "").strip():
        raise PublishBlocked("Add a subject line before publishing.")
    if not _has_content(row["schema"]):
        raise PublishBlocked(
            "This email has no content — clients would receive a blank message. "
            "Add some text or an image before publishing."
        )
    # REFUSED IF IT CANNOT RENDER — see `_render_failure` for what publishing one used to cost.
    failure = _render_failure(row, str(row["code"] or ""))
    if failure is not None:
        raise PublishBlocked(
            "This email cannot be rendered, so it would fail to send: "
            f"{failure}. Fix the merge fields or the HTML, then publish."
        )
    # Refused HERE, never on save: a half-built draft may keep the palette's copy until publish.
    placeholder = _placeholder_copy(row["schema"])
    if placeholder is not None:
        raise PublishBlocked(
            f'This email still has placeholder text in it — "{placeholder}" — which clients '
            "would receive as written. Replace it with your own words before publishing."
        )
    await session.execute(
        text(
            "UPDATE email.template_versions SET status = 'archived' "
            "WHERE template_id = :tid AND status = 'published' AND id <> :id"
        ),
        {"tid": row["template_id"], "id": row["id"]},
    )
    await session.execute(
        text(
            "UPDATE email.template_versions "
            "SET status = 'published', published_at = now(), published_by = :actor, "
            "authored_by_scope = CASE "
            "  WHEN :scope = 'operations' OR authored_by_scope = 'operations' "
            "    THEN 'operations' ELSE :scope END "
            "WHERE id = :id"
        ),
        {"id": row["id"], "actor": actor_id, "scope": scope},
    )
    await session.execute(
        text("UPDATE email.templates SET current_published_version_id = :id WHERE id = :tid"),
        {"id": row["id"], "tid": row["template_id"]},
    )
    detail = await _version_detail_by_id(session, row["id"])
    # Reported, never refused — see `unsourced_tokens`. It rides on the publish answer because that
    # is the moment an author is looking: the same list has always been reachable through the
    # Preview dialog and nowhere else, so a template published without opening Preview shipped its
    # blanks unremarked. Computed after the status flip so a failure here cannot block a publish.
    detail["unsourced"] = unsourced_tokens(row, str(row["code"] or ""))
    return detail


async def restore_version(
    session: AsyncSession,
    tenant_id: int,
    actor_id: str,
    uid: str,
    vid: str,
    *,
    scope: AuthorScope,
) -> dict[str, Any]:
    """Copy an earlier version's content into a NEW draft (never overwrites / auto-publishes).
    ``scope`` is the restoring side: the new draft is authored by whoever reached for the roll-back,
    not by whoever wrote the version being copied.
    Raises :class:`TemplateNotFound` / :class:`VersionNotFound`."""
    tid = await _template_id(session, tenant_id, uid)
    if tid is None:
        raise TemplateNotFound(uid)
    source = (
        (
            await session.execute(
                text(
                    "SELECT v.subject, v.preheader, v.schema "
                    "FROM email.template_versions v "
                    "JOIN email.templates t ON t.id = v.template_id "
                    "WHERE t.uid = :uid AND t.tenant_id = :t AND v.uid = :vid"
                ),
                {"uid": uid, "t": tenant_id, "vid": vid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if source is None:
        raise VersionNotFound(vid)
    next_number = (
        await session.execute(
            text(
                "SELECT COALESCE(MAX(version_number), 0) + 1 "
                "FROM email.template_versions WHERE template_id = :tid"
            ),
            {"tid": tid},
        )
    ).scalar_one()
    schema_json = json.dumps(source["schema"] if isinstance(source["schema"], dict) else {})
    # Unlocked MAX+1, so the loser's INSERT breaches UNIQUE — a 409, not a 500 (double-click).
    try:
        version_id = (
            await session.execute(
                text(
                    "INSERT INTO email.template_versions "
                    "  (uid, tenant_id, template_id, version_number, status, subject, preheader, "
                    "   schema, created_by, authored_by_scope) "
                    "VALUES (:uid, :t, :tid, :num, 'draft', :subject, :preheader, "
                    "  CAST(:schema AS jsonb), :actor, :scope) RETURNING id"
                ),
                {
                    "uid": _new_version_uid(),
                    "t": tenant_id,
                    "tid": tid,
                    "num": next_number,
                    "subject": source["subject"],
                    "preheader": source["preheader"],
                    "actor": actor_id,
                    "scope": scope,
                    "schema": schema_json,
                },
            )
        ).scalar_one()
    except IntegrityError as exc:
        if _is_unique_violation(exc):
            raise VersionConflict(uid) from exc
        raise
    return await _version_detail_by_id(session, version_id)


async def _insert_published(
    session: AsyncSession,
    *,
    tenant_id: int,
    actor_id: str,
    code: str,
    name: str,
    category: str,
    description: str | None,
    subject: str,
    preheader: str,
    schema: dict[str, Any],
    scope: AuthorScope,
) -> bool:
    """Insert a template + a single PUBLISHED v1 pointing at it, in one txn (deferred circular FK).
    Skips (returns False) when the tenant already has a template for ``code`` (idempotent seed /
    clone / backfill). Used by :func:`load_defaults` and :func:`clone_published`."""
    template_id = (
        await session.execute(
            text(
                "INSERT INTO email.templates "
                "  (uid, tenant_id, code, name, category, description, created_by) "
                "VALUES (:uid, :t, :code, :name, :cat, :desc, :actor) "
                "ON CONFLICT (tenant_id, code) DO NOTHING RETURNING id"
            ),
            {
                "uid": _new_template_uid(),
                "t": tenant_id,
                "code": code,
                "name": name,
                "cat": category,
                "desc": description,
                "actor": actor_id,
            },
        )
    ).scalar_one_or_none()
    if template_id is None:
        return False  # code already present for this tenant — leave the firm's own copy untouched
    version_id = (
        await session.execute(
            text(
                "INSERT INTO email.template_versions "
                "  (uid, tenant_id, template_id, version_number, status, subject, preheader, "
                "   schema, created_by, published_by, published_at, authored_by_scope) "
                "VALUES (:uid, :t, :tid, 1, 'published', :subject, :preheader, "
                "  CAST(:schema AS jsonb), :actor, :actor, now(), :scope) RETURNING id"
            ),
            {
                "uid": _new_version_uid(),
                "t": tenant_id,
                "tid": template_id,
                "subject": subject,
                "preheader": preheader,
                "schema": json.dumps(schema),
                "actor": actor_id,
                "scope": scope,
            },
        )
    ).scalar_one()
    await session.execute(
        text("UPDATE email.templates SET current_published_version_id = :v WHERE id = :tid"),
        {"v": version_id, "tid": template_id},
    )
    return True


async def refresh_defaults(
    session: AsyncSession, tenant_id: int, actor_id: str, *, force: bool = False
) -> dict[str, int]:
    """Bring a tenant's UNTOUCHED presets up to the shipped defaults, and report what it did.

    WHY THIS EXISTS. :func:`load_defaults` is idempotent BY SKIPPING — "skips codes the tenant
    already has" — so a seeded row is a frozen snapshot of whatever shipped the day it ran, and
    nothing in the estate could ever move it. That is right for a firm's own authoring and wrong for
    the MODEL tenant, whose presets are cloned into every new firm (:func:`clone_published`): the
    model tenant's rows are the version stamp for every firm created afterwards. Measured on a real
    deployment, its seven presets were three weeks older than the shipped defaults and carried no
    editor ``source`` at all — so a new firm's canvas held loose Jinja where the current default
    holds a Brand logo component. Improving ``email/defaults.py`` reached nobody.

    UNTOUCHED IS THE WHOLE SAFETY RULE, and it is read from the data rather than assumed: every
    version of the template must carry ``authored_by_scope = 'operations'``. One firm-authored
    version anywhere in the history and the preset is theirs — reported as ``authored`` and left
    exactly as it is. Overwriting a firm's published client-facing copy because an operator pressed
    a button labelled "load defaults" is the one outcome this must never have.

    A refresh INSERTS a new published version (0062 makes a published one immutable) and repoints
    the template at it, so the previous text stays in the history and the change is attributable.
    A preset that already matches is reported as ``current`` and costs nothing — the comparison is
    on the four fields a send actually reads, not on a timestamp.

    ``force`` republishes an authored preset as well, and **no route passes it**: it exists for a
    development box being reset to the shipped set, where the "firm authoring" is a developer's own
    test edit. It is a parameter rather than a second function so the two paths cannot drift, and it
    is deliberately absent from ``admin.py``'s query flag — a force switch one URL away from an
    operator is the safety rule with a hole in it, and the audit row would say "load defaults" about
    an act that replaced a firm's own words. Nothing is lost either way: the previous version stays
    in the history and can be restored from the editor.
    """
    counts = {"created": 0, "refreshed": 0, "authored": 0, "current": 0}
    for entry in email_catalog.KNOWN_CODES:
        default = email_defaults.default_for(entry.code)
        if default is None:
            continue
        row = (
            (
                await session.execute(
                    text(
                        "SELECT t.id AS template_id, "
                        "  v.subject, v.preheader, "
                        "  v.schema ->> 'html' AS html, v.schema ->> 'source' AS source, "
                        "  (SELECT count(*) FROM email.template_versions a "
                        "     WHERE a.template_id = t.id "
                        "       AND a.authored_by_scope IS DISTINCT FROM 'operations') AS authored "
                        "FROM email.templates t "
                        "LEFT JOIN email.template_versions v "
                        "  ON v.id = t.current_published_version_id "
                        "WHERE t.tenant_id = :t AND t.code = :code"
                    ),
                    {"t": tenant_id, "code": entry.code},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            if await _insert_published(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                code=entry.code,
                name=entry.label,
                category=entry.category,
                description=entry.description,
                subject=default.subject,
                preheader=default.preheader,
                schema={"html": default.html, "css": default.css, "source": default.source},
                scope="operations",
            ):
                counts["created"] += 1
            continue
        if row["authored"] and not force:
            counts["authored"] += 1
            continue
        same = (
            row["html"] == default.html
            and row["source"] == default.source
            and row["subject"] == default.subject
            and row["preheader"] == default.preheader
        )
        if same:
            counts["current"] += 1
            continue
        version_id = (
            await session.execute(
                text(
                    "INSERT INTO email.template_versions "
                    "  (uid, tenant_id, template_id, version_number, status, subject, preheader, "
                    "   schema, created_by, published_by, published_at, authored_by_scope) "
                    "SELECT :uid, :t, :tid, COALESCE(max(version_number), 0) + 1, 'published', "
                    "  :subject, :preheader, CAST(:schema AS jsonb), :actor, :actor, now(), "
                    "  'operations' "
                    "FROM email.template_versions WHERE template_id = :tid "
                    "RETURNING id"
                ),
                {
                    "uid": _new_version_uid(),
                    "t": tenant_id,
                    "tid": row["template_id"],
                    "subject": default.subject,
                    "preheader": default.preheader,
                    "schema": json.dumps(
                        {"html": default.html, "css": default.css, "source": default.source}
                    ),
                    "actor": actor_id,
                },
            )
        ).scalar_one()
        await session.execute(
            text("UPDATE email.templates SET current_published_version_id = :v WHERE id = :tid"),
            {"v": version_id, "tid": row["template_id"]},
        )
        counts["refreshed"] += 1
    return counts


async def load_defaults(session: AsyncSession, tenant_id: int, actor_id: str) -> int:
    """Seed a tenant with the built-in defaults for every catalogue code that has one, as PUBLISHED
    v1s (idempotent — skips codes the tenant already has). This is how an operator populates the
    MODEL tenant (attributed to them; there is no system user to seed at startup). Returns the
    number of templates created.

    Seeding is reachable only through the admin door, so the seeded versions are stamped
    ``'operations'`` — a firm looking at a preset it never wrote should see who put it there."""
    created = 0
    for entry in email_catalog.KNOWN_CODES:
        default = email_defaults.default_for(entry.code)
        if default is None:
            continue
        if await _insert_published(
            session,
            tenant_id=tenant_id,
            actor_id=actor_id,
            code=entry.code,
            name=entry.label,
            category=entry.category,
            description=entry.description,
            subject=default.subject,
            # The shipped preview line, not "": the preset is cloned into every new firm.
            preheader=default.preheader,
            # BOTH shapes: without `source` the canvas holds loose Jinja (round-trip.test.ts).
            schema={"html": default.html, "css": default.css, "source": default.source},
            scope="operations",
        ):
            created += 1
    return created


async def clone_published(
    session: AsyncSession, *, src_tenant_id: int, dst_tenant_id: int, actor_id: str
) -> int:
    """Copy every PUBLISHED, non-archived template from ``src_tenant_id`` into ``dst_tenant_id`` as
    that tenant's own published v1 (idempotent — skips codes the destination already has). Copies
    only the published version (render needs only the pointer; history is optional). Returns the
    number of templates cloned.

    The clone is an operations act (tenant creation / the admin backfill door), so the copies are
    stamped ``'operations'`` regardless of who authored the SOURCE version — the destination firm
    did not write them."""
    rows = (
        (
            await session.execute(
                text(
                    "SELECT t.code, t.name, t.category, t.description, "
                    "       v.subject, v.preheader, v.schema "
                    "FROM email.templates t "
                    "JOIN email.template_versions v ON v.id = t.current_published_version_id "
                    "WHERE t.tenant_id = :src AND t.archived_at IS NULL"
                ),
                {"src": src_tenant_id},
            )
        )
        .mappings()
        .all()
    )
    cloned = 0
    for r in rows:
        schema = r["schema"] if isinstance(r["schema"], dict) else {}
        if await _insert_published(
            session,
            tenant_id=dst_tenant_id,
            actor_id=actor_id,
            code=r["code"],
            name=r["name"],
            category=r["category"],
            description=r["description"],
            subject=r["subject"] or "",
            preheader=r["preheader"] or "",
            schema=schema,
            scope="operations",
        ):
            cloned += 1
    return cloned
