"""CRUD for authorable notification rules (``notifications.rules``) + the catalogue the builder
offers. Runs on a service_role session (the admin/firm paths); the app layer gates by permission
and, for the firm, by ``firm_editable``.

Every create/update is validated against the catalogue: an unknown trigger or an audience the
trigger does not offer is refused (the builder should only ever send valid pairs, but the service
is the backstop). A ``role`` audience requires a ``role_slug``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.email import catalog as email_catalog
from app.ids import new_id
from app.notifications import catalogue

_FIELDS = (
    "uid, tenant_id, trigger_key, audience, role_slug, label, title, body, link, priority, "
    "default_in_app, default_email, mandatory, firm_editable, authored_by, "
    "enabled, email_code, created_at, updated_at"
)

# The latest change's tier, so the UI can show who LAST edited a rule (not just who first made it).
# NULL for a rule with no versions yet (e.g. one predating the versions table).
_LAST_EDITOR = (
    "(SELECT v.authored_by_scope FROM notifications.rule_versions v "
    " WHERE v.rule_uid = notifications.rules.uid "
    " ORDER BY v.version_number DESC LIMIT 1) AS last_editor_scope"
)

# The rule fields captured in a version snapshot (a change audit — who changed the rule, to what).
_SNAPSHOT_FIELDS = (
    "trigger_key",
    "audience",
    "role_slug",
    "label",
    "title",
    "body",
    "link",
    "priority",
    "default_in_app",
    "default_email",
    "mandatory",
    "firm_editable",
    "enabled",
    "email_code",
)

_VERSION_FIELDS = (
    "uid, rule_uid, version_number, action, "
    + ", ".join(_SNAPSHOT_FIELDS)
    + ", authored_by_scope, created_by, created_at"
)


class RuleError(ValueError):
    """A validation failure the caller surfaces as 422."""


async def _snapshot_version(
    session: AsyncSession, rule: dict[str, Any], *, action: str, scope: str
) -> None:
    """Append a version row capturing the rule's current state + who made this change.

    ``scope`` is the TIER making the change (``'operations'`` = Graphene via the admin portal,
    ``'firm'`` = the firm's own staff), which is DISTINCT from the rule's original ``authored_by``
    — so a firm editing a Graphene-authored rule is attributed to the firm for that version.
    ``created_by`` is the acting user (``auth.uid()``, set on admin_session + firm_session)."""
    # `cols`/`vals` are the `_SNAPSHOT_FIELDS` constant (literal column names), not input — safe.
    cols = ", ".join(_SNAPSHOT_FIELDS)
    vals = ", ".join(f":{c}" for c in _SNAPSHOT_FIELDS)
    await session.execute(
        text(
            "INSERT INTO notifications.rule_versions "  # noqa: S608 — cols is a constant, not input
            f"  (uid, tenant_id, rule_uid, version_number, action, {cols}, "
            "   authored_by_scope, created_by) "
            "SELECT :uid, :t, :rule_uid, "
            "  COALESCE((SELECT max(version_number) FROM notifications.rule_versions "
            "            WHERE rule_uid = :rule_uid), 0) + 1, "
            f"  :action, {vals}, :scope, auth.uid()"
        ),
        {
            "uid": new_id(),
            "t": rule["tenant_id"],
            "rule_uid": rule["uid"],
            "action": action,
            "scope": scope,
            **{c: rule.get(c) for c in _SNAPSHOT_FIELDS},
        },
    )


def _scope_of(authored_by: str) -> str:
    """The version tier for an ``authored_by`` value ('graphene' ⇒ 'operations', else 'firm')."""
    return "operations" if authored_by == "graphene" else "firm"


async def list_rule_versions(session: AsyncSession, rule_uid: str) -> list[dict[str, Any]]:
    """A rule's change history, newest first — every create/update/delete with who made it + what
    it held. RLS scopes this to the caller's tenant (staff) / client-audience (client)."""
    rows = (
        await session.execute(
            text(
                f"SELECT {_VERSION_FIELDS} FROM notifications.rule_versions "  # noqa: S608
                "WHERE rule_uid = :u ORDER BY version_number DESC"
            ),
            {"u": rule_uid},
        )
    ).mappings()
    return [dict(r) for r in rows]


def email_templates_json() -> list[dict[str, str]]:
    """The email templates a rule may render — the builder's "Email template" dropdown options
    (code + label). The general ``notification`` wrapper is included and is the default."""
    return [{"code": c.code, "label": c.label} for c in email_catalog.notification_email_options()]


def catalogue_json() -> list[dict[str, Any]]:
    """The builder's menu: each trigger + the audiences it offers (with default copy)."""
    return [
        {
            "key": t.key,
            "label": t.label,
            "description": t.description,
            "category": t.category,
            # Raw event metadata — the underlying audit.actions event type(s) and the subject the
            # trigger fires on. Shown in the builder so an author can see exactly what event a rule
            # sits on, not just its friendly name.
            "event_types": list(t.event_types),
            "subject_kind": t.subject_kind,
            # Ready-made click destinations that resolve to the specific item (e.g. "The document" →
            # /documents/{document.uid}); the builder offers them so an author needn't type a path.
            "link_targets": catalogue.link_targets_for(t),
            "params": [
                {"key": p.key, "label": p.label, "example": p.example}
                for p in catalogue.params_for(t)
            ],
            "audiences": [
                {
                    "key": a.key,
                    "label": a.label,
                    "builtin": a.builtin,
                    "default_title": a.default_title,
                    "default_body": a.default_body,
                    "default_link": a.default_link,
                    "default_in_app": a.default_in_app,
                    "default_email": a.default_email,
                    # The email template this audience's notice renders by default (None → the
                    # general `notification` wrapper). Prefills the builder's template dropdown.
                    "default_email_code": a.email_code,
                }
                for a in t.audiences
            ],
        }
        for t in catalogue.CATALOGUE
    ]


def _validate(
    trigger_key: str, audience: str, role_slug: str | None, email_code: str | None = None
) -> None:
    trig = catalogue.trigger(trigger_key)
    if trig is None:
        raise RuleError(f"Unknown trigger '{trigger_key}'")
    if audience not in {a.key for a in trig.audiences}:
        raise RuleError(f"Trigger '{trigger_key}' does not offer the '{audience}' audience")
    if (audience == "role") != bool(role_slug):
        raise RuleError("A role audience needs a role_slug; other audiences must not carry one")
    # A NULL email_code means the general `notification` wrapper; any other value must be a real
    # selectable template, so a typo can't silently render nothing when a firm turns the email on.
    if email_code and email_code not in email_catalog.notification_email_codes():
        raise RuleError(f"Unknown email template '{email_code}'")


async def load_default_rules(
    session: AsyncSession, tenant_id: int, *, authored_by: str = "graphene"
) -> int:
    """Materialise the catalogue's BUILT-IN defaults as real, editable/deletable rows — the "Load
    defaults" action. The UI shows only stored rules (nothing is fabricated), so this is how a
    tenant is seeded with the shipped notification set, which it can then curate. Idempotent and
    additive: a ``(trigger, audience)`` that already has a stored rule is skipped, so re-running
    only fills gaps. Returns the number of rules created. (Runtime behaviour is unchanged — the
    consumers already fire built-ins via ``resolve``; this just makes them visible + editable.)
    """
    existing = {
        (r["trigger_key"], r["audience"])
        for r in (
            await session.execute(
                text("SELECT trigger_key, audience FROM notifications.rules WHERE tenant_id = :t"),
                {"t": tenant_id},
            )
        ).mappings()
    }
    created = 0
    for trig in catalogue.CATALOGUE:
        for aud in trig.audiences:
            if not aud.builtin or (trig.key, aud.key) in existing:
                continue
            await create_rule(
                session,
                tenant_id,
                authored_by=authored_by,
                trigger_key=trig.key,
                audience=aud.key,
                label=trig.label,
                title=aud.default_title,
                body=aud.default_body,
                link=aud.default_link,
                priority="normal",
                default_in_app=aud.default_in_app,
                default_email=aud.default_email,
                mandatory=aud.mandatory,
                firm_editable=True,
                enabled=True,
                email_code=aud.email_code,
            )
            created += 1
    return created


async def clone_rules(session: AsyncSession, src_tenant_id: int, dst_tenant_id: int) -> int:
    """Copy a tenant's authored notification rules to another (model → a new firm on creation).

    Each clone gets a fresh ``uid`` and the destination ``tenant_id``; everything else — the
    trigger/audience, copy, channel defaults and the guardrails (``mandatory``, ``firm_editable``,
    ``authored_by``) — is carried verbatim, so a new firm inherits the model's curated rules
    exactly, not just the category config. Skips entirely
    when the destination already has rules, so a retry cannot duplicate them. Returns rows copied.
    """
    # The uid used to be `'nrule_' || gen_random_uuid()` inline in the SQL below, which gave a
    # CLONED rule a different id shape from one made through create_rule() -- one kind, two shapes,
    # which is exactly the split this branch exists to remove. app/ids.py is the only minter, so
    # the source rows are pinned by id first and each is paired with its own uid through `unnest`.
    # Pinning by id (rather than re-running the predicate inside the INSERT) also means a
    # concurrent edit to the model tenant cannot make the join silently copy fewer rows.
    src_ids = [
        int(row[0])
        for row in await session.execute(
            text(
                "SELECT id FROM notifications.rules WHERE tenant_id = :src "
                "  AND NOT EXISTS (SELECT 1 FROM notifications.rules WHERE tenant_id = :dst) "
                "ORDER BY id"
            ),
            {"src": src_tenant_id, "dst": dst_tenant_id},
        )
    ]
    if not src_ids:
        return 0
    result = await session.execute(
        text(
            "INSERT INTO notifications.rules (uid, tenant_id, trigger_key, audience, role_slug, "
            "  label, title, body, link, priority, default_in_app, default_email, mandatory, "
            "  firm_editable, authored_by, enabled, email_code) "
            "SELECT u.uid, :dst, r.trigger_key, "
            "  r.audience, r.role_slug, r.label, r.title, r.body, r.link, r.priority, "
            "  r.default_in_app, r.default_email, r.mandatory, r.firm_editable, r.authored_by, "
            "  r.enabled, r.email_code "
            "FROM notifications.rules r "
            "JOIN unnest(CAST(:uids AS text[]), CAST(:ids AS bigint[])) AS u(uid, id) "
            "  ON u.id = r.id "
            "RETURNING id"  # the INSERTED row's id -- RETURNING cannot see the `r` alias
        ),
        {"dst": dst_tenant_id, "uids": [new_id() for _ in src_ids], "ids": src_ids},
    )
    copied = len(result.fetchall())
    # Seed each freshly-cloned rule's history with a 'created' version attributed to Graphene
    # (operations provisioned the firm from the model). Guarded by NOT EXISTS so it only ever
    # snapshots un-versioned rows — safe to run even when the clone above copied nothing.
    snapshot_cols = ", ".join(_SNAPSHOT_FIELDS)  # the _SNAPSHOT_FIELDS constant, not input — safe
    snapshot_src = ", ".join(f"r.{c}" for c in _SNAPSHOT_FIELDS)
    # Same treatment as the clone above: the version uid is minted in Python, so the rules being
    # snapshotted are pinned by uid first.
    rule_uids = [
        str(row[0])
        for row in await session.execute(
            text(
                "SELECT r.uid FROM notifications.rules r WHERE r.tenant_id = :dst AND NOT EXISTS "
                "  (SELECT 1 FROM notifications.rule_versions v WHERE v.rule_uid = r.uid) "
                "ORDER BY r.id"
            ),
            {"dst": dst_tenant_id},
        )
    ]
    if rule_uids:
        await session.execute(
            text(
                "INSERT INTO notifications.rule_versions "  # noqa: S608 — cols is a constant
                f"  (uid, tenant_id, rule_uid, version_number, action, {snapshot_cols}, "
                "   authored_by_scope, created_by) "
                "SELECT u.uid, r.tenant_id, r.uid, 1, "
                f"  'created', {snapshot_src}, 'operations', auth.uid() "
                "FROM notifications.rules r "
                "JOIN unnest(CAST(:uids AS text[]), CAST(:rule_uids AS text[])) "
                "  AS u(uid, rule_uid) ON u.rule_uid = r.uid"
            ),
            {"uids": [new_id() for _ in rule_uids], "rule_uids": rule_uids},
        )
    return copied


async def list_rules(session: AsyncSession, tenant_id: int) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                f"SELECT {_FIELDS}, {_LAST_EDITOR} FROM notifications.rules "  # noqa: S608
                "WHERE tenant_id = :t ORDER BY id"
            ),
            {"t": tenant_id},
        )
    ).mappings()
    return [dict(r) for r in rows]


async def get_rule(session: AsyncSession, uid: str) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text(f"SELECT {_FIELDS}, {_LAST_EDITOR} FROM notifications.rules WHERE uid = :u"),  # noqa: S608
                {"u": uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row is not None else None


async def create_rule(
    session: AsyncSession, tenant_id: int, *, authored_by: str, **fields: Any
) -> dict[str, Any]:
    trigger_key = str(fields["trigger_key"])
    audience = str(fields["audience"])
    role_slug = fields.get("role_slug")
    email_code = fields.get("email_code")
    _validate(trigger_key, audience, role_slug, email_code)
    uid = new_id()
    await session.execute(
        text(
            "INSERT INTO notifications.rules (uid, tenant_id, trigger_key, audience, role_slug, "
            "  label, title, body, link, priority, default_in_app, default_email, mandatory, "
            "  firm_editable, authored_by, enabled, email_code) "
            "VALUES (:uid, :t, :trigger_key, :audience, :role_slug, :label, :title, :body, :link, "
            "  :priority, :default_in_app, :default_email, :mandatory, "
            "  :firm_editable, :authored_by, :enabled, :email_code)"
        ),
        {
            "uid": uid,
            "t": tenant_id,
            "trigger_key": trigger_key,
            "audience": audience,
            "role_slug": role_slug,
            "label": str(fields.get("label") or ""),
            "title": str(fields["title"]),
            "body": fields.get("body"),
            "link": fields.get("link"),
            "priority": str(fields.get("priority") or "normal"),
            "email_code": email_code,
            "default_in_app": bool(fields.get("default_in_app", True)),
            "default_email": bool(fields.get("default_email", False)),
            "mandatory": bool(fields.get("mandatory", False)),
            "firm_editable": bool(fields.get("firm_editable", True)),
            "authored_by": authored_by,
            "enabled": bool(fields.get("enabled", True)),
        },
    )
    created = await get_rule(session, uid)
    assert created is not None  # noqa: S101 — just inserted it
    await _snapshot_version(session, created, action="created", scope=_scope_of(authored_by))
    return created


_EDITABLE = (
    "label",
    "title",
    "body",
    "link",
    "priority",
    "default_in_app",
    "default_email",
    "mandatory",
    "firm_editable",
    "enabled",
    "email_code",
)


async def update_rule(
    session: AsyncSession, uid: str, *, scope: str = "operations", **fields: Any
) -> dict[str, Any] | None:
    """Apply an edit and snapshot the result as a new version. ``scope`` is the tier making the
    change ('operations' = Graphene admin, 'firm' = the firm) — recorded on the version so the
    history attributes it correctly. A no-op edit (no editable fields) snapshots nothing."""
    sets = {k: fields[k] for k in _EDITABLE if k in fields}
    if not sets:
        return await get_rule(session, uid)
    if (
        sets.get("email_code")
        and sets["email_code"] not in email_catalog.notification_email_codes()
    ):
        raise RuleError(f"Unknown email template '{sets['email_code']}'")
    assignments = ", ".join(f"{k} = :{k}" for k in sets)
    await session.execute(
        text(
            f"UPDATE notifications.rules SET {assignments}, updated_at = now() WHERE uid = :uid"  # noqa: S608
        ),
        {**sets, "uid": uid},
    )
    updated = await get_rule(session, uid)
    if updated is not None:
        await _snapshot_version(session, updated, action="updated", scope=scope)
    return updated


async def delete_rule(session: AsyncSession, uid: str, *, scope: str = "operations") -> None:
    """Delete a rule, recording a final 'deleted' version first so the history survives the row
    (rule_versions.rule_uid has no FK for exactly this)."""
    rule = await get_rule(session, uid)
    if rule is not None:
        await _snapshot_version(session, rule, action="deleted", scope=scope)
    await session.execute(text("DELETE FROM notifications.rules WHERE uid = :u"), {"u": uid})
