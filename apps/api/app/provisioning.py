"""Tenant + member provisioning -- WorkOS write-through, mirrored into auth.*.

WorkOS is the system of record for organisations / users / memberships; this app owns
authorisation. Provisioning therefore *writes through* to the WorkOS Management API and
mirrors the result into the local `auth.*` tables (so `auth.resolve_claims` can resolve a
login to its tenant + scope without a WorkOS round-trip on the hot path).

These run inside an `admin_session` (service_role / BYPASSRLS), so the mirror upserts are
not RLS-blocked and every write is captured by the `audit.record()` DML triggers.

The full webhook-driven sync worker (the long-term reconciler) is deferred; for now the
admin action dual-writes WorkOS + the mirror in one handler.
"""

import asyncio
import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast, overload

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from workos import AsyncWorkOSClient
from workos._errors import ConflictError, NotFoundError, WorkOSError
from workos.organization_membership import RoleSingle
from workos.types.user_management import User, UserCreateResponse

from app import contacts as contacts_mod
from app import environments, listing, providers, session_mirror, tenant_storage
from app.comms import ledger
from app.config import settings
from app.email import render as email_render
from app.email import templates_service as email_templates_service
from app.email import transport as email_mod
from app.ids import new_id, tenant_uid
from app.notifications import config as notif_config
from app.notifications import rules_service as notif_rules
from app.workos_sessions import require_owned_session

logger = logging.getLogger(__name__)


def _invitation_state(invitation: object) -> str:
    state = getattr(invitation, "state", "")
    return str(state.value if hasattr(state, "value") else state).lower()


async def _issue_invitation(
    workos: AsyncWorkOSClient, email: str, *, organization_id: str, role_slug: str
) -> Any:
    """Mint the long-lived, revocable credential used by the custom onboarding email.

    The organisation is load-bearing: WorkOS permits both new and existing Users on an
    organisation invitation. An application-only invitation can instead reject the User we have
    just created, which silently pushed every Platform invite onto the password-reset fallback.
    """
    return await workos.user_management.send_invitation(
        email=email,
        organization_id=organization_id,
        role_slug=role_slug,
        expires_in_days=settings.invitation_expires_days,
    )


def _existing_user_conflict(exc: BaseException) -> bool:
    """Whether WorkOS refused because this email already has a User.

    Organisation invitations support existing Users, but preserve the fallback for an older
    environment or vendor refusal that still reports this conflict. The invitation-already-sent
    refusal is a different shape and is not this (``_revoke_pending_invitations_for`` clears it).
    """
    code = str(getattr(exc, "code", None) or "").lower()
    message = str(getattr(exc, "message", None) or exc).lower()
    if code in {
        "user_already_exists",
        "email_already_in_use",
        "organization_membership_already_exists",
    }:
        return True
    return (
        "user already exists" in message
        or "email already in use" in message
        or "already a member" in message
    )


def _pending_membership_refusal(exc: BaseException) -> bool:
    """Whether WorkOS refused because the membership is still PENDING — invited, never accepted.

    A pending membership is neither activatable nor deactivatable at the vendor: accepting the
    invitation is the only thing that activates one, and WorkOS's guidance for removing one is to
    delete it. Both refusals arrive as a 400 naming the state
    (``cannot_{re,de}activate_pending_organization_membership``).

    It is tolerated rather than propagated everywhere it can arise, because **WorkOS membership
    state is not what grants portal access — the ``auth.organisation_memberships`` mirror is**
    (``auth.resolve_claims`` refuses a portal without an active row there). So there is nothing to
    activate or deactivate at the vendor for someone who has not accepted yet, and a vendor
    refusal for that must not fail the act the mirror performs perfectly well. Untolerated, it
    aborted the WHOLE invite — before the email, before the mirror row — and the firm was shown
    WorkOS's own wording ("the invite must be accepted instead") against a client they had just
    typed in and never invited before.
    """
    code = str(getattr(exc, "code", None) or "").lower()
    if code in {
        "cannot_reactivate_pending_organization_membership",
        "cannot_deactivate_pending_organization_membership",
    }:
        return True
    # Message fallback, matching both wordings the vendor has shipped ("A pending organization
    # membership cannot be…" and "Pending organization memberships cannot be…").
    message = str(getattr(exc, "message", None) or exc).lower()
    return "pending organization membership" in message and (
        "cannot be reactivated" in message or "cannot be deactivated" in message
    )


async def _mint_set_password(
    workos: AsyncWorkOSClient,
    email: str,
    base: str | None,
    *,
    organization_id: str,
    role_slug: str,
) -> tuple[Any | None, str]:
    """A set-password URL for an unverified person, via Invitation or password-reset.

    Prefer a WorkOS organisation Invitation (revocable, long-lived, valid for new or existing
    Users). If an older environment explicitly refuses it as an existing-user conflict, the same
    ``/set-password`` page accepts the password-reset fallback.
    """
    invitation: Any | None = None
    try:
        invitation = await _issue_invitation(
            workos, email, organization_id=organization_id, role_slug=role_slug
        )
    except WorkOSError as exc:
        if not _existing_user_conflict(exc):
            raise
    if invitation is not None:
        url = (
            f"{base}/set-password?invitation_token={invitation.token}"
            if base
            else invitation.accept_invitation_url
        )
        return invitation, url
    reset = await workos.user_management.reset_password(email=email)
    url = (
        f"{base}/set-password?token={reset.password_reset_token}"
        if base
        else reset.password_reset_url
    )
    return None, url


async def _revoke_pending_invitation(workos: AsyncWorkOSClient, invitation_id: str | None) -> bool:
    """Revoke a stored invitation when it is still pending. Returns whether WorkOS was mutated."""
    if not invitation_id:
        return False
    try:
        invitation = await workos.user_management.get_invitation(invitation_id)
    except NotFoundError:
        # The stored id names nothing at the vendor — an invitation minted against a different
        # WorkOS project (a restored database, a recreated environment) or since pruned. There
        # is nothing to revoke, which is the state this call exists to reach; refusing here
        # would block every future invite to that person on a mirror column we own.
        return False
    state = _invitation_state(invitation)
    if state == "pending":
        await workos.user_management.revoke_invitation(invitation_id)
        return True
    if state == "accepted":
        raise LookupError("this invitation has already been accepted")
    return False


async def _resend_pending_invitation(
    workos: AsyncWorkOSClient, invitation_id: str | None
) -> Any | None:
    """Resend a stored pending Invitation without destroying its invitation semantics."""
    if not invitation_id:
        return None
    try:
        invitation = await workos.user_management.get_invitation(invitation_id)
    except NotFoundError:
        return None
    state = _invitation_state(invitation)
    if state == "pending":
        return await workos.user_management.resend_invitation(invitation_id)
    if state == "accepted":
        raise LookupError("this invitation has already been accepted")
    return None


async def _revoke_pending_invitations_for(workos: AsyncWorkOSClient, email: str) -> int:
    """Revoke every invitation still PENDING for an address. Returns how many were revoked.

    The stored ``auth.users.invitation_id`` is not enough on its own: it is set only for someone
    we already mirror, so an address invited before a mirror row existed — or through another
    door — leaves a pending invitation this app cannot name. WorkOS refuses to mint a second
    one for the same address, so that invisible leftover made every retry fail, which is the
    shape a half-finished first attempt leaves behind.

    Only PENDING is touched. An accepted invitation is history and revoking it would say the
    person never onboarded; the stored-id path above is what still refuses that case loudly.
    """
    revoked = 0
    for invitation in (await workos.user_management.list_invitations(email=email)).data:
        if _invitation_state(invitation) != "pending":
            continue
        await workos.user_management.revoke_invitation(str(invitation.id))
        revoked += 1
    return revoked


async def _audit(
    session: AsyncSession,
    action: str,
    *,
    tenant_id: int | None,
    entity_type: str | None = None,
    detail: dict[str, object] | None = None,
) -> None:
    """Append an explicit ``audit.log`` row for an action the DML triggers cannot see.

    The trigger spine (``audit.record()``) only fires on writes to attached tables, so actions
    whose whole effect is at WorkOS (session revocation, MFA factor reset, revealing a live
    credential) or that destroy their own subject (tenant deletion) leave no trace unless we
    write one. ``tenant_id`` is deliberately nullable: a deletion record must be stamped NULL to
    survive ``audit.log.tenant_id ON DELETE CASCADE``. The actor comes from the published claims
    (``auth.uid()``), same as the trigger."""
    await session.execute(
        text(
            "INSERT INTO audit.log (tenant_id, user_id, action, entity_type, detail) "
            "VALUES (:t, auth.uid(), :action, :entity_type, CAST(:detail AS jsonb))"
        ),
        {
            "t": tenant_id,
            "action": action,
            "entity_type": entity_type,
            "detail": json.dumps(detail or {}),
        },
    )


async def _client_role_slug(
    session: AsyncSession, tenant_id: int, preferred: str = "client"
) -> str | None:
    """The firm's client-portal role slug to invite a client with: the ``preferred`` one if it
    exists, else the firm's first client-portal role. None if the firm has no client role."""
    return (
        await session.execute(
            text(
                "SELECT slug FROM auth.roles WHERE tenant_id = :t AND portal = 'client' "
                "ORDER BY (slug = :pref) DESC, id LIMIT 1"
            ),
            {"t": tenant_id, "pref": preferred},
        )
    ).scalar_one_or_none()


async def _portal_label(session: AsyncSession, tenant_id: int, portal: str) -> str:
    """Human label for a portal, for email copy (e.g. 'Acme Advisers Client Portal')."""
    if portal == "operations":
        return "Graphene Internal Operations"
    name = (
        await session.execute(text("SELECT name FROM auth.tenants WHERE id = :t"), {"t": tenant_id})
    ).scalar_one_or_none() or "your portal"
    return f"{name} {'Client Portal' if portal == 'client' else 'Platform'}"


async def clone_model_roles(session: AsyncSession, tenant_id: int) -> int:
    """Copy the model tenant's roles (+ their permission grants) into a new tenant as its own
    roles. Mirror-only: the WorkOS org roles are created later, when the tenant's environment
    is registered (environments.push_roles_to_workos). Returns the number of roles cloned."""
    model_id = (
        await session.execute(text("SELECT id FROM auth.tenants WHERE is_model LIMIT 1"))
    ).scalar_one_or_none()
    if model_id is None or model_id == tenant_id:
        return 0
    await session.execute(
        text(
            "INSERT INTO auth.roles (tenant_id, slug, name, type, portal, description) "
            "SELECT :t, slug, name, 'organisation', portal, description "
            "FROM auth.roles WHERE tenant_id = :m "
            "ON CONFLICT (tenant_id, slug) DO NOTHING"
        ),
        {"t": tenant_id, "m": model_id},
    )
    # Copy each cloned role's permission grants (match by slug within the two tenants).
    await session.execute(
        text(
            "INSERT INTO auth.role_permissions (role_id, permission_id) "
            "SELECT tr.id, mp.permission_id "
            "FROM auth.role_permissions mp "
            "JOIN auth.roles mr ON mr.id = mp.role_id AND mr.tenant_id = :m "
            "JOIN auth.roles tr ON tr.tenant_id = :t AND tr.slug = mr.slug "
            "ON CONFLICT DO NOTHING"
        ),
        {"t": tenant_id, "m": model_id},
    )
    # Give document-managing roles the onboarding permissions out of the box: any cloned role that
    # can write documents can also author flows + run cases (onboarding is document-heavy), so a
    # freshly-provisioned firm can onboard without extra role config.
    await session.execute(
        text(
            "INSERT INTO auth.role_permissions (role_id, permission_id) "
            "SELECT r.id, p.id FROM auth.roles r "
            "JOIN auth.permissions p ON p.slug IN "
            "  ('flows:read', 'flows:write', 'cases:read', 'cases:write') "
            "WHERE r.tenant_id = :t AND EXISTS ("
            "  SELECT 1 FROM auth.role_permissions rp JOIN auth.permissions dp ON dp.id = "
            "    rp.permission_id WHERE rp.role_id = r.id AND dp.slug = 'documents:write') "
            "ON CONFLICT DO NOTHING"
        ),
        {"t": tenant_id},
    )
    return int(
        (
            await session.execute(
                text("SELECT count(*) FROM auth.roles WHERE tenant_id = :t"), {"t": tenant_id}
            )
        ).scalar_one()
    )


async def clone_model_email_templates(session: AsyncSession, tenant_id: int) -> int:
    """Copy the model tenant's PUBLISHED email templates into a new tenant as editable copies, so a
    new firm starts with the curated preset rather than only the invisible built-in fallbacks
    (mirrors ``clone_model_roles``). A code the firm later never customises still falls back to the
    shipped default via ``render_or_default`` — so this clone is a convenience, not load-bearing.

    Attribution: the created rows are stamped with the acting operator (``auth.uid()``, resolvable
    under ``admin_session``) — there is no system user, and the model tenant has no logins of its
    own. Returns the number of templates cloned (0 when there is no model, the target IS the model,
    the model holds no published templates, or there is no acting operator to attribute to)."""
    model_id = (
        await session.execute(text("SELECT id FROM auth.tenants WHERE is_model LIMIT 1"))
    ).scalar_one_or_none()
    if model_id is None or model_id == tenant_id:
        return 0
    actor = (await session.execute(text("SELECT auth.uid()"))).scalar_one_or_none()
    if actor is None:
        return 0  # no acting operator to attribute created_by to — skip (fallback stays in force)
    return await email_templates_service.clone_published(
        session, src_tenant_id=int(model_id), dst_tenant_id=tenant_id, actor_id=str(actor)
    )


async def clone_model_notification_config(session: AsyncSession, tenant_id: int) -> int:
    """Copy the model tenant's notification config AND its authored rules into a new tenant
    (mirrors clone_model_roles). Convenience: an absent config/rule falls back to the registry
    defaults, so this is a starting point, not load-bearing. Both the per-category config
    (`tenant_config`) and the curated rules (`rules`, 0068) are cloned — a firm that inherits the
    model's defaults must also inherit the model's notifications, not just their channel settings.
    Returns total rows cloned (0 when there is no model or the target IS the model)."""
    model_id = (
        await session.execute(text("SELECT id FROM auth.tenants WHERE is_model LIMIT 1"))
    ).scalar_one_or_none()
    if model_id is None or model_id == tenant_id:
        return 0
    config_rows = await notif_config.clone_config(session, int(model_id), tenant_id)
    rule_rows = await notif_rules.clone_rules(session, int(model_id), tenant_id)
    return config_rows + rule_rows


async def provision_tenant(
    session: AsyncSession,
    *,
    name: str,
    alias: str,
    workos_project_id: str | None = None,
    from_model: bool = True,
) -> dict[str, object]:
    """Create a firm (the `auth.tenants` row).

    Project-per-tenant (docs/identity-and-access.md §0): a tenant IS a WorkOS *project*,
    created manually in the WorkOS dashboard — so we do **not** create a WorkOS org here
    (that was the retired org-per-tenant behaviour). Register the project's environment(s)
    + keys, and the portal org members are invited into, via the WorkOS-environments card.

    Unless `from_model` is false, the new tenant clones the model tenant's roles as a
    starting point (mirror-only; the WorkOS org roles land when its environment is set up).

    Raises ``ValueError`` when the alias is taken — checked before the INSERT so the operator
    gets a readable 409 instead of the unique index's IntegrityError surfacing as a 500.
    """
    taken = (
        await session.execute(
            text("SELECT 1 FROM auth.tenants WHERE alias = :alias"), {"alias": alias}
        )
    ).scalar_one_or_none()
    if taken:
        raise ValueError(f"the alias '{alias}' is already used by another firm")
    row = (
        (
            await session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name, workos_project_id) "
                    "VALUES (:uid, :alias, :name, :proj) "
                    "RETURNING id, uid, alias, name, workos_project_id"
                ),
                {"uid": tenant_uid(), "alias": alias, "name": name, "proj": workos_project_id},
            )
        )
        .mappings()
        .one()
    )
    if from_model:
        await clone_model_roles(session, int(row["id"]))
        # Give the new firm the full, editable onboarding out of the box — the tiered v2 flow with
        # its fact-find forms + terms (a faithful rebuild of the legacy journey), not a stub.
        # The advice fact find (`advice_seed`) is NOT among these: it was written for one firm and
        # is applied by hand (`scripts/seed_advice_journey.py`), never as part of provisioning.
        from app.flows.onboarding_seed import seed_onboarding

        await seed_onboarding(session, tenant_id=int(row["id"]))
        # Clone the model tenant's curated email templates. Best-effort inside a SAVEPOINT: the send
        # path falls back to the shipped defaults for any missing code, so a preset problem must
        # never fail tenant creation — the savepoint rolls back only the clone, leaving the tenant.
        try:
            async with session.begin_nested():
                await clone_model_email_templates(session, int(row["id"]))
        except Exception:
            logger.warning(
                "model email-template clone failed for tenant %s", row["id"], exc_info=True
            )
        # Same best-effort discipline for the notification config (a savepoint of its own so an
        # issue here is independent of the email clone). Absent config falls back to registry
        # defaults, so this is a convenience.
        try:
            async with session.begin_nested():
                await clone_model_notification_config(session, int(row["id"]))
        except Exception:
            logger.warning(
                "model notification-config clone failed for tenant %s", row["id"], exc_info=True
            )
    # Mint the tenant's bucket (docs/storage.md §7). Best-effort: storage being down or
    # unconfigured must never fail tenant creation — a failed/partial provision leaves the
    # registry row 'provisioning' and POST /admin/tenants/{id}/storage/provision retries.
    if tenant_storage.provisioning_configured():
        try:
            await tenant_storage.provision_tenant_storage(session, int(row["id"]))
        except Exception:
            logger.warning(
                "tenant storage provisioning failed for tenant %s", row["id"], exc_info=True
            )
    return dict(row)


async def _claim_untyped(
    session: AsyncSession, *, user_id: str, tenant_id: int, user_type: str
) -> None:
    """Classify an untyped identity as this grant's side, unless it is grandfathered.

    An identity that already holds a membership on the OTHER side of the client/staff boundary is
    represented by ``type = ['client', 'staff']`` on purpose (0121); this update therefore only
    claims the empty array. Splitting a grandfathered dual identity needs a second address and an
    invitation, so it waits for a human."""
    await session.execute(
        text(
            "UPDATE auth.users u SET type = ARRAY[CAST(:type AS text)] "
            "WHERE u.id = :u AND u.tenant_id = :t AND u.type = '{}'::text[] "
            "  AND NOT EXISTS ("
            "    SELECT 1 FROM auth.organisation_memberships m "
            "    WHERE m.user_id = u.id "
            "      AND auth.portal_side(m.portal) <> "
            "          CAST(:type AS text)"
            "  )"
        ),
        {"type": user_type, "u": user_id, "t": tenant_id},
    )


async def grant_portal_access(
    session: AsyncSession,
    *,
    tenant_id: int,
    role_slug: str,
    portal: str | None = None,
    email: str | None = None,
    user_id: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    reveal_link: bool = False,
) -> dict[str, object]:
    """Grant a person access to exactly one kind of portal.

    Self-hosted onboarding (no AuthKit-hosted pages). A NEW person (invite by ``email``) gets a
    WorkOS user created (no password) and receives a WorkOS organisation Invitation whose token
    opens *our* set-password page (via Resend); the membership is reconciled immediately after the
    invitation is minted.
    Linking the invitation to the firm org is what lets WorkOS issue it for either that new User
    or an unclassified imported User; the password-reset path remains only as a compatibility
    fallback for an explicit existing-user refusal. ``auth.users.type`` is the hard boundary in
    0119 and shared as an array in 0121: a client profile may hold only Client access; staff may
    hold only Platform/Operations access. An untyped WorkOS import is claimed by its first
    deliberate grant. Re-granting is idempotent and reactivates the existing membership.

    Every mint (initial invite *or* a portal grant to an un-onboarded person) revokes the previous
    invitation, ends the person's live WorkOS sessions and writes an
    ``auth.users.invitation_minted`` audit row.

    Returns the membership summary plus ``invitation_sent`` (a set-password link was minted) and
    ``email_delivered`` (Resend actually accepted it — so the caller's toast can stop claiming
    "Invitation sent" when email is unconfigured). The set-password URL is a **live credential**:
    it is only included as ``invitation_url`` when ``reveal_link`` is set, which the admin surface
    does through its own explicit, audited "copy invitation link" action rather than on every
    invite response (which would put the credential in proxy logs and browser history)."""
    role = (
        (
            await session.execute(
                text(
                    "SELECT id, portal, name, description "
                    "FROM auth.roles WHERE tenant_id = :t AND slug = :s"
                ),
                {"t": tenant_id, "s": role_slug},
            )
        )
        .mappings()
        .one_or_none()
    )
    if role is None:
        raise LookupError(f"role '{role_slug}' does not exist for this tenant")
    role_portal = role["portal"] or "platform"
    if portal is None:
        portal = role_portal
    elif portal != role_portal:
        raise LookupError(f"role '{role_slug}' is not a {portal} role")
    role_id = role["id"]
    user_type = "client" if portal == "client" else "staff"
    # Rolling-deploy compatibility only; 0121 keeps this scalar synchronised with `type`.
    access_kind = "profile" if user_type == "client" else "staff"

    # One-firm-org: every member authenticates via the tenant's single firm org; the granted
    # PORTAL lives exclusively on the mirror membership row below.
    worg, workos = await environments.firm_org_and_client(session, tenant_id=tenant_id)

    # Resolve the target: an existing mirror user (add a portal) or an invite by email.
    existing_user = None
    if user_id is not None:
        existing_user = (
            (
                await session.execute(
                    text(
                        "SELECT id, email, first_name, last_name, workos_user_id, enabled, type, "
                        "       invitation_id "
                        "FROM auth.users WHERE id = :u AND tenant_id = :t"
                    ),
                    {"u": user_id, "t": tenant_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if existing_user is None:
            raise LookupError("user not found in this tenant")
        existing_types = list(existing_user["type"] or [])
        if existing_types and user_type not in existing_types:
            raise LookupError(
                f"this is a {existing_types[0]} identity and cannot be granted {portal} access"
            )
        if not existing_types:
            await _claim_untyped(
                session, user_id=str(user_id), tenant_id=tenant_id, user_type=user_type
            )
        email = existing_user["email"]
        first_name = existing_user["first_name"]
        last_name = existing_user["last_name"]
    elif email:
        # An invite is idempotent by tenant + email, but it may not silently CROSS the staff/profile
        # boundary. Resolve the mirror before touching WorkOS so a client email entered on Team
        # fails without changing the vendor membership first.
        existing_user = (
            (
                await session.execute(
                    text(
                        "SELECT id, email, first_name, last_name, workos_user_id, enabled, type, "
                        "       invitation_id "
                        "FROM auth.users "
                        "WHERE tenant_id = :t AND lower(email) = lower(:email) "
                        "  AND deleted_at IS NULL"
                    ),
                    {"t": tenant_id, "email": fold_email(email)},
                )
            )
            .mappings()
            .one_or_none()
        )
        if existing_user is not None:
            existing_types = list(existing_user["type"] or [])
            if existing_types and user_type not in existing_types:
                raise LookupError(
                    f"this email belongs to a {existing_types[0]} identity and cannot be invited "
                    f"to the {portal} portal"
                )
            if not existing_types:
                await _claim_untyped(
                    session,
                    user_id=str(existing_user["id"]),
                    tenant_id=tenant_id,
                    user_type=user_type,
                )
            email = existing_user["email"]
            first_name = first_name or existing_user["first_name"]
            last_name = last_name or existing_user["last_name"]
    if not email:
        raise LookupError("an email or existing user id is required")
    email = fold_email(email)

    # Ensure the WorkOS user (reuse by id/email, else create — no password yet).
    wu_id = existing_user["workos_user_id"] if existing_user else None
    # `User | UserCreateResponse`, because WorkOS SDK 10 split them: `create_user` returns
    # `UserCreateResponse` — the same fields as `User` plus `radar_auth_attempt_id` — and it is
    # NOT a subclass, so the two only unify as a union. Both carry what is read below (`id`, and
    # `email_verified` via getattr), so nothing here needs to know which it got.
    wuser: User | UserCreateResponse
    if wu_id:
        wuser = await workos.user_management.get_user(wu_id)
    else:
        found = (await workos.user_management.list_users(email=email)).data
        if found:
            wuser = found[0]
        else:
            try:
                wuser = await workos.user_management.create_user(
                    email=email,
                    first_name=first_name,
                    last_name=last_name,
                    email_verified=False,
                )
            except WorkOSError as exc:
                # create_user that lost the race with an existing User (or a ConflictError
                # subclass): recover via list rather than treating it as a failed invite.
                if not isinstance(exc, ConflictError) and not _existing_user_conflict(exc):
                    raise
                found = (await workos.user_management.list_users(email=email)).data
                if not found:
                    raise
                wuser = found[0]

    # THE ROLE MUST EXIST IN WORKOS FIRST. WorkOS validates `role_slug` when a member is added to
    # an organisation and answers 422 `invalid_role` for an unknown one -- so a mirror role with no
    # `org-*` counterpart makes this whole endpoint fail. That is not a rare state: a tenant's
    # STARTING roles are cloned from the model tenant with one `INSERT ... SELECT`, which touches
    # no vendor, so every cloned tenant had five roles with `workos_role_id IS NULL` and NO portal
    # grant to an existing member could ever succeed. Ensured (and backfilled) here rather than at
    # clone time so it self-heals the tenants that already exist and keeps tenant creation off the
    # WorkOS critical path. `rbac.ensure_workos_org_role` is the one implementation; the role's
    # NAME is worth passing so WorkOS shows something legible rather than the bare slug.
    await environments.ensure_workos_org_role(
        session,
        tenant_id=tenant_id,
        slug=role_slug,
        name=role["name"],
        description=role["description"],
    )
    org_slug = environments.workos_org_slug(role_slug)
    verified = bool(getattr(wuser, "email_verified", False))

    # Mint before the direct membership reconcile: the Invitation API can refuse a person who is
    # already an org member. The membership is still created below because deactivation, role
    # changes and the local pending mirror all need a concrete WorkOS membership before acceptance.
    invitation: Any | None = None
    invitation_url: str | None = None
    email_delivered = False
    sessions_revoked = 0
    if not verified:
        if existing_user:
            await _revoke_pending_invitation(workos, existing_user["invitation_id"])
        await _revoke_pending_invitations_for(workos, email)
        base = await environments.portal_base_url(session, tenant_id, portal)
        invitation, invitation_url = await _mint_set_password(
            workos, email, base, organization_id=worg, role_slug=org_slug
        )

    # Add to the org, then set the role and reactivate.
    # `create_organization_membership` is an UPSERT on (user, organisation): with a membership
    # already present it returns THAT membership rather than raising, so the `except ConflictError`
    # this used to rely on never fired and the role/reactivate half never ran. Verified against the
    # live API -- the call answered with the existing membership's own id. So the reconcile is
    # unconditional now, driven by what the create actually returned, and `ConflictError` is still
    # caught because a genuine conflict is still possible and must not become a 500.
    #
    # THE UPSERT REACTIVATES, so it inherits the pending refusal: against an existing PENDING
    # membership the create answers 400 "Pending organization memberships cannot be reactivated"
    # instead of echoing it back. That is the ordinary state of a re-invite — the invitation minted
    # above is exactly what makes the membership pending — so leaving it uncaught failed the whole
    # invite after the old invitation had already been revoked and a new one issued, which is the
    # WorkOS event log's signature for this bug: repeated `invitation.revoked` +
    # `invitation.created` pairs with no `organization_membership.*` event between them. Recovered
    # the same way a conflict is: the membership already exists and the list says which.
    membership_id: str | None = None
    try:
        created = await workos.organization_membership.create_organization_membership(
            user_id=wuser.id,
            organization_id=worg,
            role=RoleSingle(role_slug=org_slug),
        )
        membership_id = created.id
        mstatus = created.status.value if hasattr(created.status, "value") else str(created.status)
    except (ConflictError, WorkOSError) as exc:
        if not isinstance(exc, ConflictError) and not _pending_membership_refusal(exc):
            raise
        mstatus = ""
        page = await workos.organization_membership.list_organization_memberships(user_id=wuser.id)
        async for m in page.auto_paging_iter():
            if m.organization_id == worg:
                membership_id = m.id
                mstatus = m.status.value if hasattr(m.status, "value") else str(m.status)
                break
    if membership_id is not None:
        # The role is set on the membership for when they accept; the vendor may equally refuse to
        # touch a pending one, and the invitation carries the same `role_slug` anyway, so a refusal
        # here costs nothing and must not cost the invite either.
        try:
            await workos.organization_membership.update_organization_membership(
                membership_id, role=RoleSingle(role_slug=org_slug)
            )
        except WorkOSError as exc:
            if not _pending_membership_refusal(exc):
                raise
        # An inactive membership is put back — but NOT when we have just minted an invitation,
        # because accepting it is what activates the membership and WorkOS refuses a reactivate on
        # a pending one outright. `mstatus` is what the create ANSWERED with, and the invitation
        # issued twenty lines above (or by any other door) can have moved it since, so the refusal
        # is tolerated as well as avoided: see `_pending_membership_refusal` for why this must
        # never fail an invite.
        if mstatus.lower() == "inactive" and invitation is None:
            try:
                await workos.organization_membership.reactivate_organization_membership(
                    membership_id
                )
            except WorkOSError as exc:
                if not _pending_membership_refusal(exc):
                    raise

    # Invite email only for someone not yet onboarded (no password set → email unverified).
    if not verified:
        portal_label = await _portal_label(session, tenant_id, portal)
        portal_brand: Literal["client", "platform"] = (
            "platform" if portal == "platform" else "client"
        )
        brand = await email_mod.brand_for(session, tenant_id, portal=portal_brand)
        # Resolve the envelope identity against the DB — never read it out of ``sender_for``'s
        # cache (see that function for why a miss is both easy and silent). The brand resolved
        # above has already paid for the round trip.
        sender = await email_mod.resolve_sender(session, tenant_id, portal=portal_brand)
        subject, html = await email_render.render_or_default(
            session,
            tenant_id=tenant_id,
            code="invite",
            context={
                "brand": email_mod.brand_tokens(brand),
                # All three spellings, because an author greets with one and signs off with another.
                "first_name": first_name,
                "full_name": " ".join(p for p in (first_name, last_name) if p),
                "surname": last_name,
                "portal_label": portal_label,
                "set_password_url": invitation_url,
            },
        )
        # send_email never raises; its boolean is the ONLY signal that the invite actually went
        # out, so pass it back rather than discarding it.
        email_delivered = await email_mod.send_email(
            to=email,
            subject=subject,
            html=html,
            sender=sender.from_addr,
            reply_to=sender.reply_to,
            # Recipient stays 'external': an invite is addressed BEFORE the local `auth.users` row
            # is a settled fact, and naming a user id we have not written yet would be a guess.
            ctx=ledger.SendContext(tenant_id=tenant_id, template_code="invite"),
        )
        # Same two obligations as a resend (resend_member_invite): a superseded link must not leave
        # a live session behind it, and a minted credential must be evidenced. The initial invite
        # and a portal GRANT mint exactly the same token, so they cannot be the exceptions.
        sessions_revoked = await _revoke_user_sessions(session, workos, str(wuser.id))

    # Mirror the user (create on invite) + the per-portal membership.
    if existing_user:
        user_uuid = existing_user["id"]
    else:
        sent = None if verified else "now"
        user_uuid = (
            await session.execute(
                text(
                    "INSERT INTO auth.users "
                    "  (uid, tenant_id, email, first_name, last_name, workos_user_id, enabled, "
                    "   invitation_id, invitation_sent_at, invitation_expires_at, type, "
                    "   access_kind) "
                    "VALUES (:uid, :t, :email, :fn, :ln, :wu, :en, :invitation_id, "
                    "  CASE WHEN CAST(:sent AS text) IS NULL THEN NULL ELSE now() END, "
                    "  :invitation_expires_at, ARRAY[CAST(:type AS text)], :kind) "
                    # Email is unique WITHIN the tenant, so the invite is idempotent per
                    # (tenant, email) -- the same address in another firm is a different login.
                    "ON CONFLICT (tenant_id, email) DO UPDATE SET "
                    "  first_name = EXCLUDED.first_name, last_name = EXCLUDED.last_name, "
                    "  workos_user_id = EXCLUDED.workos_user_id, "
                    "  type = CASE "
                    "    WHEN auth.users.type = '{}'::text[] THEN EXCLUDED.type "
                    "    ELSE auth.users.type END, "
                    "  access_kind = CASE "
                    "    WHEN auth.users.access_kind = 'unclassified' THEN EXCLUDED.access_kind "
                    "    ELSE auth.users.access_kind END, "
                    "  invitation_id = COALESCE(EXCLUDED.invitation_id, auth.users.invitation_id), "
                    "  invitation_sent_at = CASE WHEN CAST(:sent AS text) IS NULL "
                    "    THEN auth.users.invitation_sent_at ELSE now() END, "
                    "  invitation_expires_at = COALESCE("
                    "    EXCLUDED.invitation_expires_at, auth.users.invitation_expires_at) "
                    "RETURNING id"
                ),
                {
                    "uid": new_id(),
                    "t": tenant_id,
                    "email": fold_email(email),
                    "fn": first_name,
                    "ln": last_name,
                    "wu": wuser.id,
                    "en": verified,
                    "sent": sent,
                    "invitation_id": invitation.id if invitation is not None else None,
                    "invitation_expires_at": (
                        invitation.expires_at if invitation is not None else None
                    ),
                    "type": user_type,
                    "kind": access_kind,
                },
            )
        ).scalar_one()

    mirror_status = "active" if verified else "pending"
    membership_pk = (
        await session.execute(
            text(
                "INSERT INTO auth.organisation_memberships "
                "  (user_id, tenant_id, role_id, portal, status) "
                "VALUES (:u, :t, :r, :p, :st) "
                "ON CONFLICT (user_id, tenant_id, portal) DO UPDATE SET "
                "  role_id = EXCLUDED.role_id, status = EXCLUDED.status "
                "RETURNING id"
            ),
            {"u": user_uuid, "t": tenant_id, "r": role_id, "p": portal, "st": mirror_status},
        )
    ).scalar_one()
    # grant_portal_access is the single-role primitive. Re-granting replaces any historical role
    # set; the multi-role router immediately calls set_member_roles with the complete requested set.
    await session.execute(
        text("DELETE FROM auth.membership_roles WHERE membership_id = :m"),
        {"m": membership_pk},
    )
    await session.execute(
        text(
            "INSERT INTO auth.membership_roles (membership_id, role_id) VALUES (:m, :r) "
            "ON CONFLICT DO NOTHING"
        ),
        {"m": membership_pk, "r": role_id},
    )

    if invitation_url is not None and existing_user:
        await session.execute(
            text(
                "UPDATE auth.users SET "
                "  invitation_id = COALESCE(:i, invitation_id), "
                "  invitation_sent_at = now(), "
                "  invitation_expires_at = COALESCE(:expires, invitation_expires_at), "
                "  invitation_accepted_at = NULL WHERE id = :u"
            ),
            {
                "i": invitation.id if invitation is not None else None,
                "expires": invitation.expires_at if invitation is not None else None,
                "u": user_uuid,
            },
        )

    if invitation_url is not None:
        # The user row exists by now, so the audit row can name it (the trigger spine can't see a
        # WorkOS-only mint).
        await _audit(
            session,
            "auth.users.invitation_minted",
            tenant_id=tenant_id,
            entity_type="auth.users",
            detail={
                "user_id": str(user_uuid),
                "portal": portal,
                "email_delivered": email_delivered,
                "revealed": reveal_link,
                "sessions_revoked": sessions_revoked,
                "trigger": "grant",
            },
        )

    return {
        "user_id": str(user_uuid),
        "tenant_id": tenant_id,
        "email": fold_email(email),
        "portal": portal,
        "role_slug": role_slug,
        "role_slugs": [role_slug],
        "status": mirror_status,
        "invitation_sent": invitation_url is not None,
        "email_delivered": email_delivered,
        "sessions_revoked": sessions_revoked,
        # Withheld unless explicitly asked for — see the docstring.
        "invitation_url": invitation_url if reveal_link else None,
    }


async def provision_client_login(
    session: AsyncSession,
    *,
    tenant_id: int,
    role_slug: str,
    email: str,
    first_name: str | None = None,
    last_name: str | None = None,
    contact_id: int | None = None,
    contact_type: str = "individual",
    reveal_link: bool = False,
) -> dict[str, object]:
    """Ensure a client PARTY + a client-portal LOGIN + the link between them, in one call.

    The three pieces already exist separately (``crm.contacts`` via ``contacts.create_contact``,
    the WorkOS login via ``grant_portal_access``, the ``crm.contact_users`` bridge via
    ``contacts.link_user``) but nothing chained them — and a login with **no** ``crm.contact_users``
    row resolves to an EMPTY ``auth.contact_scope()``, so the person would sign in yet see and
    fulfil nothing. This is the generic "give a contact portal access" primitive, reusable by
    onboarding and anything else that needs a client to be able to log in and act for a party.

    - ``contact_id`` given ⇒ reuse that party (must belong to ``tenant_id``); else create a new
      individual contact from the name/email.
    - Create-or-reuse the WorkOS **client-portal** login via ``grant_portal_access`` (``portal``
      forced to ``'client'``); ``role_slug`` must be one of the tenant's own client-portal roles.
    - Link login ↔ party in ``crm.contact_users`` (capacity ``'self'``) so ``resolve_claims`` yields
      the contact in ``contact_scope``.

    Runs elevated (``service_role``): callers use ``admin_session`` / ``firm_session``. Raises
    ``LookupError`` if the role/environment isn't set up or the contact isn't in the tenant.
    Returns the contact id, the login user id, and whether an onboarding email was sent/delivered.
    The set-password URL itself is only returned when ``reveal_link`` is set (it is a live
    credential — see ``grant_portal_access``).
    """
    # Resolve the client-portal role rather than assuming the slug is literally "client": prefer the
    # requested slug, else fall back to any of the firm's client-portal roles. Keeps invite working
    # for firms whose client role is named differently (e.g. "advised-client").
    resolved_role = await _client_role_slug(session, tenant_id, role_slug)
    if resolved_role is None:
        raise LookupError("this firm has no client-portal role configured")
    role_slug = resolved_role

    if contact_id is None:
        contact = await contacts_mod.create_contact(
            session,
            tenant_id=tenant_id,
            type=contact_type,
            first_name=first_name,
            surname=last_name,
            email=email,
        )
        contact_id = int(str(contact["id"]))

    grant = await grant_portal_access(
        session,
        tenant_id=tenant_id,
        role_slug=role_slug,
        portal="client",
        email=email,
        first_name=first_name,
        last_name=last_name,
        reveal_link=reveal_link,
    )
    user_id = str(grant["user_id"])

    # link_user validates the contact belongs to the tenant (and the user, now created) and is
    # idempotent — so a re-run of onboarding provisioning converges rather than erroring.
    await contacts_mod.link_user(
        session, tenant_id=tenant_id, contact_id=contact_id, user_id=user_id, capacity="self"
    )

    return {
        "tenant_id": tenant_id,
        "contact_id": contact_id,
        "user_id": user_id,
        "portal": "client",
        "role_slug": role_slug,
        "role_slugs": [role_slug],
        "status": grant.get("status"),
        "invitation_sent": grant.get("invitation_sent"),
        "email_delivered": grant.get("email_delivered"),
        "invitation_url": grant.get("invitation_url"),
    }


async def revoke_portal_access(
    session: AsyncSession,
    *,
    tenant_id: int,
    user_id: str,
    portal: str,
) -> dict[str, object]:
    """Revoke a person's access to one portal (deactivate the WorkOS org membership + mirror it).
    The user + any other portal access survive; re-grant via grant_portal_access reactivates it.

    Deactivating the membership does not by itself invalidate an access token or sealed cookie, so
    we end the person's live WorkOS sessions as well — otherwise revoked access keeps working until
    the token expires. They can sign back in to any portal they still hold.

    One-firm-org: the mirror row IS the access control (``resolve_claims`` refuses a portal without
    an active row), so the mirror flips first. The single WorkOS firm-org membership is deactivated
    only when NO portal remains — it is the person's authentication, shared by every portal."""
    await session.execute(
        text(
            "UPDATE auth.organisation_memberships SET status = 'inactive' "
            "WHERE user_id = :u AND tenant_id = :t AND portal = :p"
        ),
        {"u": user_id, "t": tenant_id, "p": portal},
    )
    remaining = (
        await session.execute(
            text(
                "SELECT count(*) FROM auth.organisation_memberships "
                "WHERE user_id = :u AND tenant_id = :t AND status = 'active'"
            ),
            {"u": user_id, "t": tenant_id},
        )
    ).scalar_one()
    membership_id, _org, workos = await _workos_firm_membership(
        session, tenant_id=tenant_id, user_id=user_id
    )
    if not remaining:
        try:
            await workos.organization_membership.deactivate_organization_membership(membership_id)
        except WorkOSError as exc:
            # Nothing to deactivate for an unaccepted invitation — the mirror row flipped above is
            # what withholds the portal (`_pending_membership_refusal`).
            if not _pending_membership_refusal(exc):
                raise
    revoked = await _revoke_user_sessions(
        session, workos, await _workos_user_id(session, tenant_id, user_id)
    )
    return {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "portal": portal,
        "status": "inactive",
        "sessions_revoked": revoked,
    }


async def _caller_tenant_id(session: AsyncSession) -> int | None:
    """The tenant of the *caller* as published in ``request.jwt.claims`` (``auth.tenant_id()``).

    Used as the fail-closed default tenant scope for actions whose caller is a firm acting on its
    own people (``db.firm_session`` publishes the firm's own resolved tenant). ``admin_session``
    resolves to the OPERATIONS tenant, so cross-tenant admin callers must always pass the target
    tenant explicitly — which is the point: a missing scope must never mean "any tenant"."""
    scalar = (await session.execute(text("SELECT auth.tenant_id()"))).scalar_one_or_none()
    return int(scalar) if scalar is not None else None


async def resend_member_invite(
    session: AsyncSession,
    *,
    user_id: str,
    tenant_id: int | None = None,
    portal: str | None = None,
    reveal_link: bool = False,
) -> dict[str, object]:
    """Resend onboarding: revoke the pending invitation, mint a fresh one, and email it through
    Resend. A user may hold several portals; the link is built for the given `portal`, else the
    pending one (a person is onboarded once).

    **``tenant_id`` is a security boundary, not a convenience.** This mints a live credential and
    (with ``reveal_link``) hands it back, and it runs under ``service_role``/BYPASSRLS — so RLS does
    not backstop it. Without a tenant predicate an operator authorised for one firm could name any
    firm in the path, have the user looked up by bare id, and receive a working set-password link
    for someone else's login (including an Internal Operations account, defeating the
    ``operations:write`` gate). Callers pass the tenant they are authorised for; when omitted it
    falls back to
    the CALLER's own tenant from the published claims (the firm self-service path) and refuses
    outright if there is none.

    ``invitation_url`` is a live credential and is withheld unless ``reveal_link`` is set."""
    scope_tenant_id = tenant_id if tenant_id is not None else await _caller_tenant_id(session)
    if scope_tenant_id is None:
        raise LookupError("no tenant scope for this invitation")
    row = (
        (
            await session.execute(
                text(
                    "SELECT u.email, u.first_name, u.last_name, u.tenant_id, "
                    "       u.workos_user_id, u.invitation_id, m.portal, r.slug AS role_slug "
                    "FROM auth.users u "
                    "JOIN auth.organisation_memberships m "
                    "  ON m.user_id = u.id AND m.tenant_id = u.tenant_id "
                    "JOIN auth.roles r ON r.id = m.role_id AND r.tenant_id = m.tenant_id "
                    # The tenant predicate is the authorisation boundary — see the docstring.
                    "WHERE u.id = :u AND u.tenant_id = :t AND u.deleted_at IS NULL "
                    "  AND m.status = 'pending' "
                    "  AND (CAST(:portal AS text) IS NULL OR m.portal = :portal) "
                    "ORDER BY (m.status = 'pending') DESC LIMIT 1"
                ),
                {"u": user_id, "t": scope_tenant_id, "portal": portal},
            )
        )
        .mappings()
        .one_or_none()
    )
    if not row:
        raise LookupError("no membership for this user")
    tenant_id = int(row["tenant_id"])
    portal = row["portal"] or "platform"
    worg, workos = await environments.firm_org_and_client(session, tenant_id=tenant_id)
    base = await environments.portal_base_url(session, tenant_id, portal)
    invitation = await _resend_pending_invitation(workos, row["invitation_id"])
    if invitation is not None:
        set_password_url = (
            f"{base}/set-password?invitation_token={invitation.token}"
            if base
            else invitation.accept_invitation_url
        )
    else:
        await _revoke_pending_invitations_for(workos, row["email"])
        invitation, set_password_url = await _mint_set_password(
            workos,
            row["email"],
            base,
            organization_id=worg,
            role_slug=environments.workos_org_slug(str(row["role_slug"])),
        )

    portal_label = await _portal_label(session, tenant_id, portal)
    portal_brand: Literal["client", "platform"] = "platform" if portal == "platform" else "client"
    brand = await email_mod.brand_for(session, tenant_id, portal=portal_brand)
    # As on the initial invite: the firm's own From + Reply-To, resolved rather than read out of
    # ``sender_for``'s cache.
    sender = await email_mod.resolve_sender(session, tenant_id, portal=portal_brand)
    subject, html = await email_render.render_or_default(
        session,
        tenant_id=tenant_id,
        code="invite",
        context={
            "brand": email_mod.brand_tokens(brand),
            "first_name": row["first_name"],
            "full_name": " ".join(p for p in (row["first_name"], row["last_name"]) if p),
            "surname": row["last_name"],
            "portal_label": portal_label,
            "set_password_url": set_password_url,
        },
    )
    email_delivered = await email_mod.send_email(
        to=row["email"],
        subject=subject,
        html=html,
        sender=sender.from_addr,
        reply_to=sender.reply_to,
        # A RESEND, unlike the first invite, has a settled user to attribute to.
        ctx=ledger.SendContext(
            tenant_id=tenant_id, template_code="invite", recipient_user_id=str(user_id)
        ),
    )

    # Any session established off the previous link dies with it (see the docstring).
    revoked = await _revoke_user_sessions(session, workos, str(row["workos_user_id"] or ""))

    await session.execute(
        text(
            "UPDATE auth.users SET "
            "  invitation_id = COALESCE(:i, invitation_id), "
            "  invitation_sent_at = now(), "
            "  invitation_expires_at = COALESCE(:expires, invitation_expires_at), "
            "  invitation_accepted_at = NULL WHERE id = :u"
        ),
        {
            "i": invitation.id if invitation is not None else None,
            "expires": invitation.expires_at if invitation is not None else None,
            "u": user_id,
        },
    )
    await _audit(
        session,
        "auth.users.invitation_minted",
        tenant_id=tenant_id,
        entity_type="auth.users",
        detail={
            "user_id": user_id,
            "portal": portal,
            "email_delivered": email_delivered,
            "revealed": reveal_link,
            "sessions_revoked": revoked,
        },
    )
    return {
        "user_id": user_id,
        "portal": portal,
        "invitation_sent": True,
        "email_delivered": email_delivered,
        "invitation_url": set_password_url if reveal_link else None,
    }


async def revoke_member_invitation(
    session: AsyncSession,
    *,
    user_id: str,
    tenant_id: int | None = None,
) -> dict[str, object]:
    """Revoke one pending onboarding credential without revoking the person's portal grant."""
    scope_tenant_id = tenant_id if tenant_id is not None else await _caller_tenant_id(session)
    if scope_tenant_id is None:
        raise LookupError("no tenant scope for this invitation")
    row = (
        (
            await session.execute(
                text(
                    "SELECT invitation_id FROM auth.users u "
                    "WHERE u.id = :u AND u.tenant_id = :t AND u.deleted_at IS NULL "
                    "  AND EXISTS ("
                    "    SELECT 1 FROM auth.organisation_memberships m "
                    "    WHERE m.user_id = u.id AND m.tenant_id = u.tenant_id "
                    "      AND m.status = 'pending'"
                    "  )"
                ),
                {"u": user_id, "t": scope_tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise LookupError("no pending invitation for this user")
    invitation_id = row["invitation_id"]
    if not invitation_id:
        raise LookupError("no pending invitation for this user")
    workos = await environments.client_for_env(
        session, await environments.primary_environment_id(session, scope_tenant_id)
    )
    revoked = await _revoke_pending_invitation(workos, str(invitation_id))
    await session.execute(
        text(
            "UPDATE auth.users SET invitation_id = NULL, invitation_expires_at = NULL "
            "WHERE id = :u AND tenant_id = :t"
        ),
        {"u": user_id, "t": scope_tenant_id},
    )
    await _audit(
        session,
        "auth.users.invitation_revoked",
        tenant_id=scope_tenant_id,
        entity_type="auth.users",
        detail={"user_id": user_id, "invitation_id": str(invitation_id), "revoked": revoked},
    )
    return {"user_id": user_id, "invitation_revoked": revoked}


async def send_member_password_reset(
    session: AsyncSession,
    *,
    tenant_id: int,
    user_id: str,
    portal: str | None = None,
) -> dict[str, object]:
    """Mint and email a password-reset link for an existing tenant member.

    This is distinct from ``resend_member_invite``: active accounts receive the password-reset
    template and invitation timestamps are not rewritten. The caller must already have authorised
    ``tenant_id``; the lookup repeats that predicate because this runs with BYPASSRLS.
    """
    row = (
        (
            await session.execute(
                text(
                    "SELECT u.email, u.workos_user_id, m.portal "
                    "FROM auth.users u "
                    "JOIN auth.organisation_memberships m "
                    "  ON m.user_id = u.id AND m.tenant_id = u.tenant_id "
                    "WHERE u.id = :u AND u.tenant_id = :t AND u.deleted_at IS NULL "
                    "  AND (CAST(:portal AS text) IS NULL OR m.portal = :portal) "
                    "ORDER BY (m.status = 'active') DESC, m.portal LIMIT 1"
                ),
                {"u": user_id, "t": tenant_id, "portal": portal},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise LookupError("no membership for this user")
    selected_portal = str(row["portal"])
    workos = await environments.client_for_env(
        session, await environments.primary_environment_id(session, tenant_id)
    )
    reset = await workos.user_management.reset_password(email=row["email"])
    base = await environments.portal_base_url(session, tenant_id, selected_portal)
    set_password_url = (
        f"{base}/set-password?token={reset.password_reset_token}"
        if base
        else reset.password_reset_url
    )
    brand_portal: Literal["client", "platform"] = (
        "client" if selected_portal == "client" else "platform"
    )
    brand = await email_mod.brand_for(session, tenant_id, portal=brand_portal)
    sender = await email_mod.resolve_sender(session, tenant_id, portal=brand_portal)
    subject, html = await email_render.render_or_default(
        session,
        tenant_id=tenant_id,
        code="password_reset",
        context={
            "brand": email_mod.brand_tokens(brand),
            # Keep the same non-enumerating template shape as self-service reset. The manager
            # already knows the person, but two reset paths should not produce different mail.
            "first_name": None,
            "full_name": None,
            "surname": None,
            "set_password_url": set_password_url,
        },
    )
    delivered = await email_mod.send_email(
        to=row["email"],
        subject=subject,
        html=html,
        sender=sender.from_addr,
        reply_to=sender.reply_to,
        ctx=ledger.SendContext(
            tenant_id=tenant_id,
            template_code="password_reset",
            recipient_user_id=str(user_id),
        ),
    )
    revoked = await _revoke_user_sessions(session, workos, str(row["workos_user_id"] or ""))
    await _audit(
        session,
        "auth.users.password_reset_minted",
        tenant_id=tenant_id,
        entity_type="auth.users",
        detail={
            "user_id": user_id,
            "portal": selected_portal,
            "email_delivered": delivered,
            "sessions_revoked": revoked,
        },
    )
    return {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "portal": selected_portal,
        "email_delivered": delivered,
        "sessions_revoked": revoked,
    }


async def _revoke_user_sessions(
    session: AsyncSession, workos: AsyncWorkOSClient, workos_user_id: str
) -> int:
    """End every live WorkOS session for one user. Best-effort: a WorkOS outage must not fail the
    action that asked for it (the account is already disabled locally by then), so failures are
    logged and counted as zero. Returns how many sessions were revoked.

    A session WorkOS accepted is dropped from the local mirror in the same transaction. One
    WorkOS refused is left in the table, so the list does not hide a browser that is still
    signed in.
    """
    if not workos_user_id:
        return 0
    revoked = 0
    try:
        page = await workos.user_management.list_sessions(workos_user_id)
        async for s in page.auto_paging_iter():
            status = s.status.value if hasattr(s.status, "value") else str(s.status)
            if status != "active":
                continue
            await workos.user_management.revoke_session(session_id=s.id)
            await session_mirror.forget(session, s.id)
            revoked += 1
    except WorkOSError:
        logger.warning("could not revoke WorkOS sessions for %s", workos_user_id, exc_info=True)
    return revoked


async def revoke_tenant_sessions(session: AsyncSession, tenant_id: int) -> int:
    """Kill every live WorkOS session belonging to a firm's people — the "and existing sessions
    stop working" half of suspending a firm. Without this an already signed-in user would keep
    working until their token expired. Best-effort per user; returns the number of sessions revoked
    (0 when the firm has no WorkOS environment yet).

    Enforcement of the suspension itself is the API's: every request from the firm is refused 403 by
    ``db._refuse_if_suspended``. **The portal sign-in form is NOT yet a gate** — the state is
    published to it (``env_config_by_host`` → ``tenant_enabled``) but no portal reads it, so a
    suspended firm's user can still complete a WorkOS login and only then finds every call 403s. See
    the handoff in docs/api.md (packages/auth ``RawEnvConfig``/``normalise`` drop the field)."""
    try:
        env_id = await environments.primary_environment_id(session, tenant_id)
        workos = await environments.client_for_env(session, env_id)
    except LookupError:
        return 0  # no environment/keys => nobody can be signed in through it anyway
    workos_user_ids = list(
        (
            await session.execute(
                text(
                    "SELECT workos_user_id FROM auth.users "
                    "WHERE tenant_id = :t AND workos_user_id IS NOT NULL AND deleted_at IS NULL"
                ),
                {"t": tenant_id},
            )
        )
        .scalars()
        .all()
    )
    revoked = 0
    for workos_user_id in workos_user_ids:
        revoked += await _revoke_user_sessions(session, workos, str(workos_user_id))
    return revoked


async def update_tenant(
    session: AsyncSession,
    *,
    tenant_id: int,
    name: str | None = None,
    alias: str | None = None,
    website: str | None = None,
    enabled: bool | None = None,
) -> dict[str, object]:
    """Edit a firm's local attributes (name/alias/website/enabled). Project-per-tenant:
    these are local — nothing is written through to WorkOS (a tenant is a project, and the
    firm name is not a WorkOS org name). Only non-None fields change (COALESCE).

    Switching ``enabled`` off is a real kill switch, not a flag, and each half is enforced by
    something that reads it:

    * every live WorkOS session for the firm's people is revoked here (``revoke_tenant_sessions``);
    * every subsequent API request is refused 403 by the request layer —
      ``db._refuse_if_suspended``, called from both ``tenant_session`` and ``firm_session``.

    Not yet closed: the sign-in form itself. ``environments.env_config_by_host`` publishes
    ``tenant_enabled`` for it, but no portal consumes that field, so a suspended firm's user still
    completes the WorkOS login and meets the 403s afterwards instead of an honest "suspended"
    message. Handed off with the portal half (docs/api.md).

    Raises ``LookupError`` for an unknown tenant and ``ValueError`` when the alias is taken."""
    was_enabled = (
        await session.execute(
            text("SELECT enabled FROM auth.tenants WHERE id = :t"), {"t": tenant_id}
        )
    ).scalar_one_or_none()
    if was_enabled is None:
        raise LookupError("tenant not found")
    if alias is not None:
        taken = (
            await session.execute(
                text("SELECT 1 FROM auth.tenants WHERE alias = :alias AND id <> :t"),
                {"alias": alias, "t": tenant_id},
            )
        ).scalar_one_or_none()
        if taken:
            raise ValueError(f"the alias '{alias}' is already used by another firm")
    row = (
        (
            await session.execute(
                text(
                    "UPDATE auth.tenants SET "
                    "  name = COALESCE(:name, name), alias = COALESCE(:alias, alias), "
                    "  website = COALESCE(:website, website), "
                    "  enabled = COALESCE(:enabled, enabled) "
                    "WHERE id = :t "
                    "RETURNING id, uid, alias, name, website, enabled, parent_id, "
                    "  workos_project_id"
                ),
                {
                    "name": name,
                    "alias": alias,
                    "website": website,
                    "enabled": enabled,
                    "t": tenant_id,
                },
            )
        )
        .mappings()
        .one()
    )
    result = dict(row)
    if enabled is not None:
        # Disabling a firm stops its bucket resolving (uploads/branding fall back);
        # re-enabling restores it. The bucket itself is untouched (docs/storage.md §7).
        if enabled:
            await tenant_storage.resume_tenant_storage(session, tenant_id)
        else:
            await tenant_storage.suspend_tenant_storage(session, tenant_id)
    # Suspending the firm: cut the live sessions too, so "blocked from signing in" is true for
    # people who are already signed in and not just for the next login.
    if enabled is False and was_enabled:
        revoked = await revoke_tenant_sessions(session, tenant_id)
        await _audit(
            session,
            "auth.tenants.suspended",
            tenant_id=tenant_id,
            entity_type="auth.tenants",
            detail={"tenant_id": tenant_id, "sessions_revoked": revoked},
        )
        result["sessions_revoked"] = revoked
    return result


async def _user_portals(session: AsyncSession, tenant_id: int, user_id: str) -> list[str]:
    """The portals a user holds a membership in for this tenant."""
    return list(
        (
            await session.execute(
                text(
                    "SELECT portal FROM auth.organisation_memberships "
                    "WHERE user_id = :u AND tenant_id = :t"
                ),
                {"u": user_id, "t": tenant_id},
            )
        )
        .scalars()
        .all()
    )


async def _portal_statuses(session: AsyncSession, tenant_id: int, user_id: str) -> dict[str, str]:
    """``{portal: status}`` for every membership row this person holds in the firm — including the
    ``inactive`` ones, which is the whole point when it is used as a pre-suspension snapshot."""
    rows = (
        (
            await session.execute(
                text(
                    "SELECT portal, status FROM auth.organisation_memberships "
                    "WHERE user_id = :u AND tenant_id = :t"
                ),
                {"u": user_id, "t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    return {str(r["portal"]): str(r["status"]) for r in rows}


async def _suspension_snapshot(
    session: AsyncSession, tenant_id: int, user_id: str
) -> dict[str, str] | None:
    """The per-portal statuses this person held the last time they were suspended, or None.

    Reactivation used to set **every** membership active, so a portal deliberately revoked *before*
    the suspension came silently back — an access grant nobody asked for, produced by an unrelated
    action. There is nowhere in the schema to keep that state (no ``suspended_portals`` column, no
    status history), but there is somewhere durable and already required to exist: the audit trail.
    ``deactivate_member`` writes the snapshot into its ``auth.users.suspended`` row, and this reads
    the newest one back.

    Deliberately the *newest* row: suspend → reactivate → re-grant → suspend again must restore the
    second snapshot, not the first. None when the person was suspended before this existed (or the
    trail has been rotated), in which case the caller restores everything and says so rather than
    guessing — the old behaviour, but now labelled."""
    detail = (
        await session.execute(
            text(
                "SELECT detail FROM audit.log "
                "WHERE tenant_id = :t AND action = 'auth.users.suspended' "
                "  AND detail ->> 'user_id' = :u "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"t": tenant_id, "u": str(user_id)},
        )
    ).scalar_one_or_none()
    before = (detail or {}).get("portals_before") if isinstance(detail, dict) else None
    if not isinstance(before, dict) or not before:
        return None
    return {str(k): str(v) for k, v in before.items()}


async def _workos_user_id(session: AsyncSession, tenant_id: int, user_id: str) -> str:
    """The mirror user's WorkOS id ('' when they have none — e.g. never synced, or the id names a
    login in a DIFFERENT firm). Every caller already knows the tenant it is authorised for, so the
    lookup is scoped to it: these run as service_role/BYPASSRLS and a bare `WHERE id = :u` would
    happily resolve another firm's login."""
    return str(
        (
            await session.execute(
                text("SELECT workos_user_id FROM auth.users WHERE id = :u AND tenant_id = :t"),
                {"u": user_id, "t": tenant_id},
            )
        ).scalar_one_or_none()
        or ""
    )


async def _workos_firm_membership(
    session: AsyncSession, *, tenant_id: int, user_id: str
) -> tuple[str, str, AsyncWorkOSClient]:
    """Resolve (membership_id, organization_id, client) for a member's ONE firm-org membership.

    One-firm-org: WorkOS holds a single membership per person (authentication); WHICH portals they
    may use is exclusively ``auth.organisation_memberships.portal``. Scoped to `tenant_id` (see
    `_workos_user_id`)."""
    wuser = (
        await session.execute(
            text("SELECT workos_user_id FROM auth.users WHERE id = :u AND tenant_id = :t"),
            {"u": user_id, "t": tenant_id},
        )
    ).scalar_one_or_none()
    if not wuser:
        raise LookupError("user has no WorkOS id — sync the tenant first")

    worg, workos = await environments.firm_org_and_client(session, tenant_id=tenant_id)
    page = await workos.organization_membership.list_organization_memberships(user_id=wuser)
    async for m in page.auto_paging_iter():
        if m.organization_id == worg:
            return m.id, m.organization_id, workos
    raise LookupError("no WorkOS firm membership for this user")


async def validate_member_role_set(
    session: AsyncSession,
    *,
    tenant_id: int,
    role_slugs: list[str],
    portal: str | None = None,
) -> str:
    """Validate a complete role set before any WorkOS side effect and return its portal."""
    unique_slugs = list(dict.fromkeys(role_slugs))
    if not unique_slugs:
        raise ValueError("at least one role is required")
    rows = (
        (
            await session.execute(
                text(
                    "SELECT slug, COALESCE(portal, 'platform') AS portal "
                    "FROM auth.roles WHERE tenant_id = :t AND slug = ANY(:slugs)"
                ),
                {"t": tenant_id, "slugs": unique_slugs},
            )
        )
        .mappings()
        .all()
    )
    by_slug = {str(row["slug"]): str(row["portal"]) for row in rows}
    missing = [slug for slug in unique_slugs if slug not in by_slug]
    if missing:
        raise LookupError(f"no role '{missing[0]}' in this tenant")
    portals = {by_slug[slug] for slug in unique_slugs}
    if len(portals) != 1:
        raise ValueError("all selected roles must belong to the same portal")
    resolved_portal = portals.pop()
    if portal is not None and resolved_portal != portal:
        raise ValueError(f"selected roles belong to the {resolved_portal} portal, not {portal}")
    return resolved_portal


async def set_member_roles(
    session: AsyncSession,
    *,
    tenant_id: int,
    user_id: str,
    portal: str,
    role_slugs: list[str],
) -> dict[str, object]:
    """Replace a member's complete role set within one portal — mirror-authoritative.

    Every role must exist in this tenant and belong to the target portal. The permissions of all
    selected roles are unioned by ``auth.resolve_claims`` → ``auth.member_permissions``. An empty
    role set is refused: portal access with no role is an unusable identity, not a useful state.

    The first selected role remains ``organisation_memberships.role_id`` and the cosmetic WorkOS
    role. Order is therefore stable and intentional, while authorisation treats the set equally."""
    unique_slugs = list(dict.fromkeys(role_slugs))
    await validate_member_role_set(
        session, tenant_id=tenant_id, role_slugs=unique_slugs, portal=portal
    )

    roles = (
        (
            await session.execute(
                text(
                    "SELECT id, slug, COALESCE(portal, 'platform') AS portal "
                    "FROM auth.roles WHERE tenant_id = :t AND slug = ANY(:slugs)"
                ),
                {"t": tenant_id, "slugs": unique_slugs},
            )
        )
        .mappings()
        .all()
    )
    by_slug = {str(r["slug"]): r for r in roles}
    membership_id = (
        await session.execute(
            text(
                "UPDATE auth.organisation_memberships SET role_id = :primary "
                "WHERE user_id = :u AND tenant_id = :t AND portal = :p RETURNING id"
            ),
            {
                "primary": by_slug[unique_slugs[0]]["id"],
                "u": user_id,
                "t": tenant_id,
                "p": portal,
            },
        )
    ).scalar_one_or_none()
    if membership_id is None:
        raise LookupError("no membership for this user/portal")

    await session.execute(
        text("DELETE FROM auth.membership_roles WHERE membership_id = :m"),
        {"m": membership_id},
    )
    await session.execute(
        text(
            "INSERT INTO auth.membership_roles (membership_id, role_id) "
            "SELECT :m, id FROM auth.roles "
            "WHERE tenant_id = :t AND slug = ANY(:slugs)"
        ),
        {"m": membership_id, "t": tenant_id, "slugs": unique_slugs},
    )

    try:
        membership_id, _org, workos = await _workos_firm_membership(
            session, tenant_id=tenant_id, user_id=user_id
        )
        await workos.organization_membership.update_organization_membership(
            membership_id,
            role=RoleSingle(role_slug=environments.workos_org_slug(unique_slugs[0])),
        )
    except (LookupError, WorkOSError):
        pass  # cosmetic under one-firm-org; the mirror is authoritative
    return {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "portal": portal,
        "role_slug": unique_slugs[0],
        "role_slugs": unique_slugs,
    }


async def set_member_role(
    session: AsyncSession,
    *,
    tenant_id: int,
    user_id: str,
    portal: str,
    role_slug: str,
) -> dict[str, object]:
    """Compatibility wrapper for callers that still set exactly one role."""
    return await set_member_roles(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        portal=portal,
        role_slugs=[role_slug],
    )


async def _require_member(session: AsyncSession, tenant_id: int, user_id: str) -> None:
    """Raise LookupError unless the login exists **in this tenant** (the routers map that to 404).

    Every whole-person action here is a loop over the person's memberships plus tenant-scoped
    UPDATEs, so naming a login that belongs to another firm updated nothing and returned a success
    payload — a no-op reported as done ("status": "inactive" for a user who is still active in their
    own firm). The sibling endpoints (resend / reveal / reset-mfa / role / revoke) all resolve a row
    first and 404, so this is the one shape that lied. Fail loudly instead; the tenant predicate is
    also the authorisation boundary, exactly as in ``resend_member_invite``."""
    found = (
        await session.execute(
            text(
                "SELECT 1 FROM auth.users WHERE id = :u AND tenant_id = :t AND deleted_at IS NULL"
            ),
            {"u": user_id, "t": tenant_id},
        )
    ).scalar_one_or_none()
    if not found:
        raise LookupError("user not found in this tenant")


async def deactivate_member(
    session: AsyncSession,
    *,
    tenant_id: int,
    user_id: str,
) -> dict[str, object]:
    """Suspend a person entirely in a firm: deactivate every portal membership (WorkOS), end their
    live WorkOS sessions, and disable the user so they can't sign in on any portal. Per-portal
    revoke is revoke_portal_access; this is the whole-user switch.

    **Records the pre-suspension per-portal state** in its ``auth.users.suspended`` audit row. That
    is what lets ``reactivate_member`` put the person back as they were, instead of handing back
    every portal including one deliberately revoked beforehand. (There is no column for it; the
    audit trail is the durable, already-mandatory place. See ``_suspension_snapshot``.)

    Raises LookupError when the login isn't one of this tenant's (see ``_require_member``)."""
    await _require_member(session, tenant_id, user_id)
    before = await _portal_statuses(session, tenant_id, user_id)
    # One-firm-org: ONE WorkOS membership per person — deactivate it once.
    last_client: AsyncWorkOSClient | None = None
    try:
        membership_id, _org, workos = await _workos_firm_membership(
            session, tenant_id=tenant_id, user_id=user_id
        )
        last_client = workos
        try:
            await workos.organization_membership.deactivate_organization_membership(membership_id)
        except WorkOSError as exc:
            # Invited and never accepted: there is no vendor membership to deactivate, and the
            # mirror rows below are what actually withhold the portal. Suspending them locally is
            # the whole act (`_pending_membership_refusal`).
            if not _pending_membership_refusal(exc):
                raise
    except LookupError:
        pass  # never synced to WorkOS — the mirror rows below still suspend them locally
    # Sessions outlive membership deactivation, so end them explicitly — otherwise "suspended"
    # only takes effect at the next sign-in.
    revoked = (
        await _revoke_user_sessions(
            session, last_client, await _workos_user_id(session, tenant_id, user_id)
        )
        if last_client is not None
        else 0
    )
    await session.execute(
        text(
            "UPDATE auth.organisation_memberships SET status = 'inactive' "
            "WHERE user_id = :u AND tenant_id = :t"
        ),
        {"u": user_id, "t": tenant_id},
    )
    await session.execute(
        text("UPDATE auth.users SET enabled = false WHERE id = :u AND tenant_id = :t"),
        {"u": user_id, "t": tenant_id},
    )
    await _audit(
        session,
        "auth.users.suspended",
        tenant_id=tenant_id,
        entity_type="auth.users",
        detail={
            "user_id": str(user_id),
            # The state to restore. Read back by _suspension_snapshot on reactivation.
            "portals_before": before,
            "sessions_revoked": revoked,
        },
    )
    return {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "status": "inactive",
        "sessions_revoked": revoked,
        "portals_before": before,
    }


async def reactivate_member(
    session: AsyncSession,
    *,
    tenant_id: int,
    user_id: str,
) -> dict[str, object]:
    """Un-suspend a person: put back the access they held before the suspension, and re-enable them.

    The restored status follows the same rule as everywhere else — ``active`` only once WorkOS says
    the email is verified (i.e. they have set a password), else ``pending`` — rather than forcing
    ``active`` on someone who never onboarded.

    **Per-portal, from the snapshot.** This used to set *every* membership active, so a portal
    deliberately revoked before the suspension was handed back by an action that never mentioned it.
    ``deactivate_member`` records the pre-suspension statuses in its audit row, so a portal that was
    already revoked stays revoked here — locally *and* at WorkOS (we don't reactivate its
    membership). Where there is no snapshot (suspended before this existed, or the trail rotated) we
    restore everything, as before, and report ``snapshot_found: false`` so the caller can say which
    of the two happened instead of implying the precise one.

    Returns the portals restored and the portals left revoked. Raises LookupError when the login
    isn't one of this tenant's (see ``_require_member``)."""
    await _require_member(session, tenant_id, user_id)
    snapshot = await _suspension_snapshot(session, tenant_id, user_id)
    # A portal that was revoked BEFORE the suspension is not part of what is being restored.
    stay_revoked = {p for p, s in (snapshot or {}).items() if s == "inactive"}
    verified = False
    portals = await _user_portals(session, tenant_id, user_id)
    restored = [p for p in portals if p not in stay_revoked]
    # One-firm-org: ONE WorkOS membership per person — reactivate it once (any portal restored
    # means the person may authenticate again; WHICH portals is the mirror rows below).
    if restored:
        try:
            membership_id, _org, workos = await _workos_firm_membership(
                session, tenant_id=tenant_id, user_id=user_id
            )
            try:
                await workos.organization_membership.reactivate_organization_membership(
                    membership_id
                )
            except WorkOSError as exc:
                # Suspended while their invitation was still outstanding. WorkOS will refuse this
                # until the invite is accepted, so REFUSING here would leave such a person
                # permanently un-restorable; the mirror restore below is what hands the access
                # back, and their invitation link still works (`_pending_membership_refusal`).
                if not _pending_membership_refusal(exc):
                    raise
            wuser = await _workos_user_id(session, tenant_id, user_id)
            if wuser:
                verified = bool(
                    getattr(await workos.user_management.get_user(wuser), "email_verified", False)
                )
        except LookupError:
            pass  # no WorkOS membership (never synced): still restore the mirror rows, as before
    status = "active" if verified else "pending"
    await session.execute(
        text(
            "UPDATE auth.organisation_memberships SET status = :st "
            "WHERE user_id = :u AND tenant_id = :t "
            # `<> ALL(:skip)` rather than a NOT IN over an empty list, which is never true in SQL.
            "  AND portal <> ALL(CAST(:skip AS text[]))"
        ),
        {"u": user_id, "t": tenant_id, "st": status, "skip": sorted(stay_revoked)},
    )
    await session.execute(
        text("UPDATE auth.users SET enabled = :en WHERE id = :u AND tenant_id = :t"),
        {"u": user_id, "t": tenant_id, "en": verified},
    )
    left_revoked = sorted(stay_revoked & set(await _portal_statuses(session, tenant_id, user_id)))
    await _audit(
        session,
        "auth.users.reactivated",
        tenant_id=tenant_id,
        entity_type="auth.users",
        detail={
            "user_id": str(user_id),
            "portals_restored": sorted(restored),
            "portals_left_revoked": left_revoked,
            "snapshot_found": snapshot is not None,
            "status": status,
        },
    )
    return {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "status": status,
        "portals_restored": sorted(restored),
        "portals_left_revoked": left_revoked,
        "snapshot_found": snapshot is not None,
    }


@dataclass(frozen=True)
class MemberFilters:
    """Status + the two date ranges, grouped into one object rather than five more bare keywords
    on ``members_with_portals``. The `limit`-driven ``@overload`` pair below already multiplies
    every optional keyword across two return shapes; five more tipped mypy over its own overload
    search-space limit ("too many unions") at the one call site threading every filter through
    (``routers/firm.py::list_members``). Grouping them costs that call site nothing — it still
    names each field — and keeps the overloads checkable.
    """

    status: Sequence[str] | None = None
    created_since: datetime | None = None
    created_until: datetime | None = None
    active_since: datetime | None = None
    active_until: datetime | None = None


# `limit` decides the shape, so it decides the TYPE. Overloads rather than a bare union: every
# existing caller passes no limit and wants a list, and a union would make each of them narrow a
# value that cannot actually vary — mypy caught exactly that at the admin members route.
@overload
async def members_with_portals(
    session: AsyncSession,
    tenant_id: int,
    *,
    access_kind: str | None = ...,
    q: str | None = ...,
    role_slug: str | Sequence[str] | None = ...,
    filters: MemberFilters | None = ...,
    limit: None = ...,
    offset: int = ...,
) -> list[dict[str, object]]: ...


@overload
async def members_with_portals(
    session: AsyncSession,
    tenant_id: int,
    *,
    access_kind: str | None = ...,
    q: str | None = ...,
    role_slug: str | Sequence[str] | None = ...,
    filters: MemberFilters | None = ...,
    limit: int,
    offset: int = ...,
) -> dict[str, object]: ...


async def members_with_portals(
    session: AsyncSession,
    tenant_id: int,
    *,
    access_kind: str | None = None,
    q: str | None = None,
    role_slug: str | Sequence[str] | None = None,
    filters: MemberFilters | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, object]] | dict[str, object]:
    """A firm's identities with their one portal, complete role set and linked client records.

    ``access_kind`` keeps the established API query vocabulary while filtering the shared
    ``auth.users.type`` array: profile -> client, staff -> staff, unclassified -> empty. A
    grandfathered ``['client', 'staff']`` identity therefore appears on both pages, which is the
    truth about it and lets an operator split it; all new identities are single-sided.

    ``q`` searches name and email; ``role_slug`` narrows to the people holding any of one or more
    roles (a bare string is one role, same as ever); ``status``/the two date ranges narrow the
    same way. All are applied in SQL because ``limit`` is: filtering a window in the browser
    searches the page you happen to be on and reports the result as the firm's answer.

    **Two response shapes, chosen by ``limit``** — the convention ``/crm/accounts`` sets and
    ``/documents/requests`` follows. Absent ⇒ the bare list every existing caller reads (the
    invite pickers, the client record's adviser selector), so this widens rather than breaks;
    given ⇒ ``{items, total}``.
    """
    if access_kind not in (None, "staff", "profile", "unclassified"):
        raise ValueError("unknown access kind")
    role_slugs = listing.clean_many(role_slug)
    f = filters or MemberFilters()
    statuses = listing.clean_many(f.status)
    rows = (
        (
            await session.execute(
                text(
                    # DRIVEN FROM `users`, not from `organisation_memberships`. A person with no
                    # portal row is a real and reachable state -- `sync_tenant` upserts the
                    # firm's WorkOS people into `auth.users` and deliberately NEVER invents a
                    # portal row (which portals someone may use is this app's decision, not
                    # WorkOS's; deriving it from an org membership is the org-based
                    # classification the identity model forbids). Inner-joining the memberships
                    # made every one of those people INVISIBLE: a real sync imported 26 and the
                    # members page showed no change, so an operator could not grant a portal to
                    # anyone the sync had brought in -- the one action the import exists to
                    # enable. They now appear with an empty `portals`, which is exactly what the
                    # page's "grant this portal" branch already renders for.
                    "SELECT u.id AS user_id, u.uid AS user_uid, u.email, u.name, "
                    "       u.enabled, u.type, "
                    "       u.avatar, u.created_at, u.last_sign_in, u.is_adviser, "
                    "       (u.invitation_id IS NOT NULL "
                    "         AND u.invitation_accepted_at IS NULL "
                    "         AND u.invitation_expires_at > now()) AS invitation_pending, "
                    "       u.invitation_expires_at, "
                    # WHICH CUSTODIANS KNOW THIS PERSON -- the tag the directory wears.
                    # `provider_refs` is a provider -> id map, so its KEYS are the answer and no
                    # second column is needed to hold them.
                    "       COALESCE("
                    "         (SELECT array_agg(k ORDER BY k)"
                    "            FROM jsonb_object_keys(u.provider_refs) AS k),"
                    "         '{}'::text[]"
                    "       ) AS providers, "
                    # WHERE THE ROW CAME FROM, DERIVED rather than stored. A person provisioned
                    # through WorkOS has a `workos_user_id`; a row `auth.merge_provider_user`
                    # minted for a custodian's employee has none and never will, because WorkOS is
                    # the only issuer of an identity. A column duplicating this would be a second
                    # spelling free to disagree with the first.
                    "       (u.workos_user_id IS NULL AND u.provider_refs IS NOT NULL) "
                    "         AS from_provider, "
                    # What the Edit drawer prefills from. Returned by the LIST rather than by a
                    # per-person GET because the drawer opens on a row the page already holds.
                    "       u.first_name, u.last_name, u.direct_phone, u.job_title, "
                    "       COALESCE(u.qualifications, '{}'::text[])::text[] AS qualifications, "
                    "       u.fca_reference, u.bio, u.photo_url, "
                    # The client records this login can act for (`crm.contact_users`, the bridge
                    # `provision_client` writes). A profile page has to show WHO a login is, and an
                    # email is not that — one login legitimately acts for several parties
                    # (authorised person, power of attorney), which is exactly why the link is a
                    # table and not a column. Aggregated in the same pass rather than fetched per
                    # row: a page of 100 profiles would otherwise be 100 extra queries.
                    "       COALESCE(("
                    "         SELECT json_agg(json_build_object("
                    "           'contact_id', ct.id, 'uid', ct.uid, 'capacity', cu.capacity, "
                    "           'name', COALESCE("
                    "             NULLIF(TRIM(CONCAT_WS(' ', ct.first_name, ct.surname)), ''), "
                    "             ct.name)"
                    "         ) ORDER BY ct.id) "
                    "         FROM crm.contact_users cu "
                    "         JOIN crm.contacts ct ON ct.id = cu.contact_id "
                    "         WHERE cu.user_id = u.id"
                    "       ), '[]'::json) AS contacts, "
                    "       m.portal, m.status, m.created_at AS membership_created_at, "
                    "       COALESCE("
                    "         array_agg(r.slug ORDER BY (r.id = m.role_id) DESC, r.name) "
                    "           FILTER (WHERE r.id IS NOT NULL), "
                    "         '{}'::text[]"
                    "       ) AS role_slugs "
                    "FROM auth.users u "
                    "LEFT JOIN auth.organisation_memberships m "
                    "  ON m.user_id = u.id AND m.tenant_id = :t "
                    "LEFT JOIN auth.membership_roles mr ON mr.membership_id = m.id "
                    "LEFT JOIN auth.roles r ON r.id = mr.role_id "
                    "WHERE u.tenant_id = :t "
                    "  AND ("
                    "    CAST(:kind AS text) IS NULL "
                    "    OR (CAST(:kind AS text) = 'staff' "
                    "        AND u.type @> ARRAY['staff']::text[]) "
                    "    OR (CAST(:kind AS text) = 'profile' "
                    "        AND u.type @> ARRAY['client']::text[]) "
                    "    OR (CAST(:kind AS text) = 'unclassified' AND u.type = '{}'::text[])"
                    "  ) "
                    "  AND (CAST(:q AS text) IS NULL "
                    "       OR u.email ILIKE :q OR COALESCE(u.name, '') ILIKE :q) "
                    # The role filter is a membership question, so it is an EXISTS rather than a
                    # predicate on the aggregated array: filtering the aggregate would need it
                    # built first, and HAVING over a role set drops the other roles from the row.
                    "  AND (CAST(:role AS text[]) IS NULL OR EXISTS ("
                    "        SELECT 1 FROM auth.membership_roles mr2 "
                    "        JOIN auth.roles r2 ON r2.id = mr2.role_id "
                    "        WHERE mr2.membership_id = m.id "
                    "          AND r2.slug = ANY(CAST(:role AS text[]))"
                    "      )) "
                    "  AND (CAST(:status AS text[]) IS NULL OR ("
                    "        CASE WHEN m.status = 'pending' THEN 'invited' "
                    "             WHEN u.enabled THEN 'active' "
                    "             ELSE 'inactive' END"
                    "      ) = ANY(CAST(:status AS text[]))) "
                    "  AND (CAST(:created_since AS timestamptz) IS NULL "
                    "       OR COALESCE(m.created_at, u.created_at) "
                    "          >= CAST(:created_since AS timestamptz)) "
                    "  AND (CAST(:created_until AS timestamptz) IS NULL "
                    "       OR COALESCE(m.created_at, u.created_at) "
                    "          < CAST(:created_until AS timestamptz)) "
                    "  AND (CAST(:active_since AS timestamptz) IS NULL "
                    "       OR u.last_sign_in >= CAST(:active_since AS timestamptz)) "
                    "  AND (CAST(:active_until AS timestamptz) IS NULL "
                    "       OR u.last_sign_in < CAST(:active_until AS timestamptz)) "
                    # THE PRIMARY KEYS ALONE. `auth.users.id` is `uuid PRIMARY KEY` (0001) and
                    # `m.id` is `organisation_memberships`' own, so Postgres knows every other
                    # projected column is functionally dependent on the pair and grants them for
                    # free. Fifteen more were listed, two of them expensive to compare per row
                    # pair — `u.provider_refs` (jsonb) and `u.bio` (a paragraph of prose) — on a
                    # scan with no LIMIT and no pagination: `auth.merge_provider_user` publishes a
                    # custodian's whole employee directory into `auth.users` (546 rows for firm
                    # 3102 in the corpus, 470 of them departed and synced deliberately), so the
                    # hash key carried a jsonb blob and a bio for every one of them.
                    #
                    # `m.portal`/`m.status`/`m.created_at` are dependent on `m.id` too, but a
                    # LEFT JOIN miss makes `m.id` NULL — and a NULL group key is not a functional
                    # dependency Postgres will infer — so they stay named. That is the case the
                    # query exists for: a person the sync imported with no portal row at all.
                    "GROUP BY u.id, m.id, m.portal, m.status, m.created_at "
                    "ORDER BY (u.workos_user_id IS NULL AND u.provider_refs IS NOT NULL), "
                    "         u.email, m.portal"
                ),
                {
                    "t": tenant_id,
                    "kind": access_kind,
                    "q": f"%{q.strip()}%" if q and q.strip() else None,
                    "role": role_slugs,
                    "status": statuses,
                    "created_since": f.created_since,
                    "created_until": f.created_until,
                    "active_since": f.active_since,
                    "active_until": f.active_until,
                },
            )
        )
        .mappings()
        .all()
    )
    order: list[str] = []
    base: dict[str, dict[str, object]] = {}
    portals: dict[str, list[dict[str, object]]] = {}
    # Built ONCE. `providers.meta()` returns `dict(found)` — a fresh copy of the registry entry —
    # and it was called per provider tag per row inside the comprehension below, which is 546 rows
    # for the firm above.
    provider_names = {code: providers.meta(code)["name"] for code in providers.PROVIDERS}
    for r in rows:
        uid = str(r["user_id"])
        if uid not in base:
            base[uid] = {
                "user_id": uid,
                "user_uid": r["user_uid"],
                "email": r["email"],
                "name": r["name"],
                "enabled": r["enabled"],
                "invitation_pending": bool(r["invitation_pending"]),
                "invitation_expires_at": r["invitation_expires_at"],
                # `types` is the canonical key; `type` is the same array under the name the
                # column has, so a caller that reads either cannot miss it (the Team edit
                # drawer read `.type.length` and crashed when only `types` was present).
                "types": list(r["type"] or []),
                "type": list(r["type"] or []),
                # The custodians that know this person, and whether the row IS one of theirs.
                # NAMED SERVER-SIDE, from `providers.meta()`: six hard-coded copies had drifted and
                # the one that mattered fell back to Morgan Lloyd's name for any unlisted provider,
                # so every SEI surface carried the wrong custodian's name above SEI's own data.
                "providers": [
                    {"provider": slug, "name": provider_names.get(slug, slug)}
                    for slug in (r["providers"] or [])
                ],
                "from_provider": bool(r["from_provider"]),
                # Advising is a capability WITHIN staff, so a boolean rather than a `type` member.
                "is_adviser": bool(r["is_adviser"]),
                # The firm's own words about its staff, for the Edit drawer.
                "first_name": r["first_name"],
                "last_name": r["last_name"],
                "direct_phone": r["direct_phone"],
                "job_title": r["job_title"],
                "qualifications": list(r["qualifications"] or []),
                "fca_reference": r["fca_reference"],
                "bio": r["bio"],
                "photo_url": r["photo_url"],
                "avatar": r["avatar"],
                "created_at": r["created_at"],
                "last_sign_in": r["last_sign_in"],
                "contacts": list(r["contacts"] or []),
                # Response compatibility during the same transition as the database column.
                "access_kind": (
                    "unclassified"
                    if len(r["type"] or []) != 1
                    else ("profile" if r["type"][0] == "client" else "staff")
                ),
            }
            portals[uid] = []
            order.append(uid)
        # A null `portal` is the LEFT JOIN saying "this person holds none", not a portal named
        # null -- appending it would put an undefined entry in front of every UI that reads
        # `portals.find(p => p.portal === …)`.
        if r["portal"] is not None:
            role_slugs = list(r["role_slugs"] or [])
            portals[uid].append(
                {
                    "portal": r["portal"],
                    "role_slug": role_slugs[0] if role_slugs else None,
                    "role_slugs": role_slugs,
                    "status": r["status"],
                    "created_at": r["membership_created_at"],
                }
            )
    people = [{**base[uid], "portals": portals[uid]} for uid in order]
    if limit is None:
        return people
    # Paged in PYTHON, over the assembled people, not in SQL. The query returns one row per
    # (person, portal) and the assembly folds them together, so a SQL `LIMIT` would cut the row
    # set mid-person: someone holding two portals could arrive with one of them missing, or be
    # counted twice in the total. The filtering that matters — search, role, kind — is still done
    # in SQL, so what is being windowed here is a list already narrowed to the answer.
    start = max(0, offset)
    return {
        "items": people[start : start + max(1, min(limit, 200))],
        "total": len(people),
    }


async def sync_tenant(
    session: AsyncSession,
    tenant_id: int,
    *,
    push_roles: bool = True,
    progress: Callable[[], None] | None = None,
) -> dict[str, int]:
    """WorkOS reconcile for one tenant — PERSON-level, one-firm-org.

    WorkOS authenticates (one firm-org membership per person); WHICH portals a person may use is
    OUR ``auth.organisation_memberships`` and is never derived from WorkOS. So the reconcile
    upserts the USERS (names, avatar, email_verified → enabled) and maps the WorkOS membership's
    state onto the person's existing mirror rows' STATUS: inactive ⇒ all rows inactive;
    active+verified ⇒ pending rows promoted to active. It never invents a portal row. Optionally
    re-pushes the tenant's roles first. Raises LookupError if the environment/keys aren't set up.

    The background poller passes push_roles=False — role pushes are only needed after role
    edits, not on every tick."""
    if push_roles:
        await environments.push_roles_to_workos(session, tenant_id)

    env_id = await environments.primary_environment_id(session, tenant_id)
    workos = await environments.client_for_env(session, env_id)
    worg = (
        await session.execute(
            text("SELECT firm_org_id FROM auth.tenant_environments WHERE id = :e"),
            {"e": env_id},
        )
    ).scalar_one_or_none()
    if not worg:
        return {"members": 0}

    # Counted separately because "synced 33 members" and "changed nothing you can see" were both
    # true at once, and the operator was only told the first. `imported` is the number the mirror
    # did not already have; `without_portal` is how many of the firm's people hold no portal
    # access at all -- which is the state this reconcile LEAVES them in on purpose, and the reason
    # a successful sync can look like a no-op.
    members = 0
    imported = 0
    page = await workos.organization_membership.list_organization_memberships(organization_id=worg)
    if progress is not None:
        progress()
    async for m in page.auto_paging_iter():
        if progress is not None:
            progress()
        wuser = m.user
        status = m.status.value if hasattr(m.status, "value") else str(m.status)
        # A self-hosted member is a WorkOS org member immediately but can't sign in until
        # they set a password (which verifies their email). So treat email_verified as the
        # onboarding signal: unverified => still 'pending'/disabled; deactivated => inactive.
        verified = bool(getattr(wuser, "email_verified", False))
        params = {
            "t": tenant_id,
            "email": fold_email(wuser.email),
            "fn": wuser.first_name,
            "ln": wuser.last_name,
            # name is a generated column (first + last); avatar mirrors the profile picture.
            "avatar": getattr(wuser, "profile_picture_url", None),
            "wu": wuser.id,
            "en": verified and status != "inactive",
        }
        # Match on EITHER natural key — a WorkOS user id (globally unique) or (tenant, email)
        # — since either may already exist. Then update in place, else insert.
        existing = (
            await session.execute(
                text(
                    "SELECT id FROM auth.users "
                    "WHERE workos_user_id = :wu OR (tenant_id = :t AND email = :email) LIMIT 1"
                ),
                params,
            )
        ).scalar_one_or_none()
        if existing is None:
            imported += 1
            uid = (
                await session.execute(
                    text(
                        "INSERT INTO auth.users (uid, tenant_id, email, first_name, "
                        "  last_name, avatar, workos_user_id, enabled) "
                        "VALUES (:uid, :t, :email, :fn, :ln, :avatar, :wu, :en) "
                        "RETURNING id"
                    ),
                    {**params, "uid": new_id()},
                )
            ).scalar_one()
        else:
            uid = existing
            await session.execute(
                text(
                    "UPDATE auth.users SET tenant_id = :t, email = :email, "
                    "  first_name = :fn, last_name = :ln, avatar = :avatar, "
                    "  workos_user_id = :wu, enabled = :en WHERE id = :id"
                ),
                {**params, "id": uid},
            )
        # Person-level status onto the person's OWN portal rows (never inventing one).
        if status == "inactive":
            await session.execute(
                text(
                    "UPDATE auth.organisation_memberships SET status = 'inactive' "
                    "WHERE user_id = :u AND tenant_id = :t"
                ),
                {"u": uid, "t": tenant_id},
            )
        elif verified:
            await session.execute(
                text(
                    "UPDATE auth.organisation_memberships SET status = 'active' "
                    "WHERE user_id = :u AND tenant_id = :t AND status = 'pending'"
                ),
                {"u": uid, "t": tenant_id},
            )
        members += 1

    without_portal = int(
        (
            await session.execute(
                text(
                    "SELECT count(*) FROM auth.users u "
                    "WHERE u.tenant_id = :t AND NOT EXISTS ("
                    "  SELECT 1 FROM auth.organisation_memberships m "
                    "  WHERE m.user_id = u.id AND m.tenant_id = :t"
                    ")"
                ),
                {"t": tenant_id},
            )
        ).scalar_one()
    )
    return {"members": members, "imported": imported, "without_portal": without_portal}


async def mark_onboarding_complete(
    session: AsyncSession,
    *,
    email: str | None = None,
    workos_user_id: str | None = None,
    tenant_id: int | None = None,
) -> dict[str, object]:
    """Record that a person finished onboarding (they set a password, which verifies their email).

    This is the PUSH half of the pending→active transition. Setting a password happens entirely at
    the portal (``/set-password`` → ``confirmPasswordReset``) and wrote nothing here, so the only
    reconciler was the WorkOS events poller — which is off unless ``WORKOS_SYNC_POLL_SECONDS`` is
    set, leaving fully-onboarded people rendered as never-invited indefinitely. Called on the
    set-password path, the mirror is correct immediately and the poller becomes a safety net rather
    than the mechanism.

    Idempotent and forgiving by design (it runs on an unauthenticated portal path): identifies the
    login by ``workos_user_id`` or by ``email`` (+ ``tenant_id`` when the same address exists in
    several firms), flips ``enabled`` and stamps ``invitation_accepted_at``, and promotes only
    ``pending`` memberships — a deliberately revoked (``inactive``) portal is left revoked.
    Returns the users/memberships touched (both 0 when the email is unknown — never an error, so
    the caller can't be used to probe which addresses exist).

    **Scoped to people mid-onboarding.** A login with no ``pending`` membership is untouched, so
    this can never re-enable a deliberately suspended account. The caller (``routers.internal``)
    also confirms with WorkOS that the identity really is ``email_verified`` first."""
    if not email and not workos_user_id:
        return {"users": 0, "memberships": 0}
    params = {"email": fold_email(email), "wu": workos_user_id, "t": tenant_id}
    user_ids = list(
        (
            await session.execute(
                text(
                    "SELECT id FROM auth.users WHERE deleted_at IS NULL "
                    "  AND (CAST(:wu AS text) IS NOT NULL AND workos_user_id = :wu "
                    "       OR CAST(:wu AS text) IS NULL AND email = :email) "
                    "  AND (CAST(:t AS int) IS NULL OR tenant_id = :t)"
                ),
                params,
            )
        )
        .scalars()
        .all()
    )
    if not user_ids:
        return {"users": 0, "memberships": 0}
    # Only ever re-enable someone who is mid-onboarding. A login whose memberships are all
    # 'inactive' was DELIBERATELY suspended by an operator; flipping `enabled` back on for them
    # would turn "this person finished onboarding" into an un-suspend primitive reachable from an
    # unauthenticated portal path. Narrow the set to logins with a pending membership.
    user_ids = list(
        (
            await session.execute(
                text(
                    "SELECT DISTINCT user_id FROM auth.organisation_memberships "
                    "WHERE user_id = ANY(:ids) AND status = 'pending'"
                ),
                {"ids": user_ids},
            )
        )
        .scalars()
        .all()
    )
    if not user_ids:
        return {"users": 0, "memberships": 0}
    await session.execute(
        text(
            "UPDATE auth.users SET enabled = true, "
            "  invitation_accepted_at = COALESCE(invitation_accepted_at, now()) "
            "WHERE id = ANY(:ids)"
        ),
        {"ids": user_ids},
    )
    # RETURNING rather than rowcount: the async Result type doesn't expose one.
    promoted = (
        (
            await session.execute(
                text(
                    "UPDATE auth.organisation_memberships SET status = 'active' "
                    "WHERE user_id = ANY(:ids) AND status = 'pending' RETURNING portal"
                ),
                {"ids": user_ids},
            )
        )
        .scalars()
        .all()
    )
    return {"users": len(user_ids), "memberships": len(promoted)}


async def reset_member_mfa(
    session: AsyncSession,
    *,
    tenant_id: int,
    user_id: str,
) -> dict[str, object]:
    """Delete every enrolled MFA factor for a member, so they can re-enrol at their next sign-in.

    Without this a person who loses their authenticator is locked out permanently — TOTP is the
    only factor we offer and there are no recovery codes. Supported by the WorkOS SDK:
    ``multi_factor_auth.list_user_auth_factors`` + ``delete_factor``. Deleting a factor changes
    nothing in our own tables, so the DML audit triggers can't see it — an explicit audit row is
    written instead. Raises LookupError when the person has no WorkOS login or no environment."""
    wuser = await _workos_user_id(session, tenant_id, user_id)
    if not wuser:
        raise LookupError("user has no WorkOS id — sync the tenant first")
    env_id = await environments.primary_environment_id(session, tenant_id)
    workos = await environments.client_for_env(session, env_id)
    factor_page = await workos.multi_factor_auth.list_user_auth_factors(wuser)
    factor_ids = [factor.id async for factor in factor_page.auto_paging_iter()]
    for factor_id in factor_ids:
        await workos.multi_factor_auth.delete_factor(factor_id)
    # Their current sessions were established with the old factor; end them so the next sign-in
    # goes through enrolment.
    revoked = await _revoke_user_sessions(session, workos, wuser)
    await _audit(
        session,
        "auth.users.mfa_reset",
        tenant_id=tenant_id,
        entity_type="auth.users",
        detail={
            "user_id": user_id,
            "factors_deleted": len(factor_ids),
            "sessions_revoked": revoked,
        },
    )
    return {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "factors_deleted": len(factor_ids),
        "sessions_revoked": revoked,
    }


async def member_security_posture(
    session: AsyncSession,
    *,
    tenant_id: int,
    user_id: str,
    current_session_id: str | None = None,
) -> dict[str, object]:
    """Authentication posture for one tenant member.

    Sessions are the local mirror. Email verification and factors are still read from
    WorkOS, and a failure there leaves ``available`` false without dropping the session
    list. A person with no WorkOS login still raises LookupError: there is nothing to show.
    """
    workos_user_id = await _workos_user_id(session, tenant_id, user_id)
    if not workos_user_id:
        raise LookupError("user has no WorkOS id — sync the tenant first")
    views = await session_mirror.sessions_for_user(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        current_session_id=current_session_id,
    )
    posture: dict[str, object] = {
        "available": False,
        "email_verified": False,
        "mfa_enrolled": False,
        "factor_count": 0,
        "active_sessions": len(views),
        "sessions": views,
    }
    try:
        workos = await environments.client_for_env(
            session, await environments.primary_environment_id(session, tenant_id)
        )
        workos_user, factor_page = await asyncio.gather(
            workos.user_management.get_user(workos_user_id),
            workos.multi_factor_auth.list_user_auth_factors(workos_user_id),
        )
        factors = [factor async for factor in factor_page.auto_paging_iter()]
    except (LookupError, WorkOSError):
        return posture
    posture["available"] = True
    posture["email_verified"] = bool(getattr(workos_user, "email_verified", False))
    posture["mfa_enrolled"] = bool(factors)
    posture["factor_count"] = len(factors)
    return posture


async def revoke_member_session(
    session: AsyncSession, *, tenant_id: int, user_id: str, session_id: str
) -> dict[str, object]:
    """End one live session, and only if it belongs to this member.

    ``revoke_session`` takes a bare id. The local row is enough to prove ownership once the
    mirror has the session. Until that backfill has run, the id is checked against WorkOS's
    own list. Either way the call still goes to WorkOS, which is what signs the browser out,
    and the local row is removed only after that call succeeds.
    """
    workos_user_id = await _workos_user_id(session, tenant_id, user_id)
    if not workos_user_id:
        raise LookupError("user has no WorkOS id — sync the tenant first")
    workos = await environments.client_for_env(
        session, await environments.primary_environment_id(session, tenant_id)
    )
    if not await session_mirror.owns(
        session, tenant_id=tenant_id, user_id=user_id, session_id=session_id
    ):
        session_page = await workos.user_management.list_sessions(workos_user_id)
        sessions = [item async for item in session_page.auto_paging_iter()]
        require_owned_session(sessions, session_id)
    await workos.user_management.revoke_session(session_id=session_id)
    await session_mirror.forget(session, session_id)
    return {"user_id": user_id, "session_id": session_id}


# The firm's own tenant-scoped tables, counted in an offboarding export so an operator can see
# what deletion would destroy. Deliberately a fixed list (not information_schema) so a new table
# is a conscious addition here rather than silently changing an FCA-facing archive.
_EXPORT_COUNT_TABLES: tuple[str, ...] = (
    "crm.contacts",
    "documents.documents",
    "documents.files",
    "documents.requests",
    "documents.form_responses",
    "flows.flows",
    "flows.cases",
    "tasks.tasks",
)


async def export_tenant(session: AsyncSession, tenant_id: int) -> dict[str, object]:
    """The offboarding archive for one firm: its identity, authorisation and audit record, plus a
    row count for each of its data tables.

    This is the retention artefact an operator takes before destroying a firm (FCA record-keeping)
    — the identity/RBAC/audit half in full, because that is the evidence that cannot be
    reconstructed, and counts for the domain tables so the operator can see the scale of what a
    deletion removes. Environment rows are included WITHOUT their secrets (the ciphertext is
    useless without the master key and has no place in an export). Raises LookupError if the
    tenant is unknown."""
    tenant = (
        (await session.execute(text("SELECT * FROM auth.tenants WHERE id = :t"), {"t": tenant_id}))
        .mappings()
        .one_or_none()
    )
    if tenant is None:
        raise LookupError("tenant not found")

    async def _rows(sql: str) -> list[dict[str, object]]:
        return [
            dict(r) for r in (await session.execute(text(sql), {"t": tenant_id})).mappings().all()
        ]

    counts: dict[str, int] = {}
    for table in _EXPORT_COUNT_TABLES:
        counts[table] = int(
            (
                await session.execute(
                    text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"),  # noqa: S608 - fixed allowlist above, never user input
                    {"t": tenant_id},
                )
            ).scalar_one()
        )

    return {
        "exported_at": (await session.execute(text("SELECT now()"))).scalar_one(),
        # Minus 0211's generated search text: derived from the fields beside it, not a record.
        "tenant": {k: v for k, v in tenant.items() if k != "_search"},
        "environments": await _rows(
            "SELECT id, stage, workos_client_id, issuer, client_portal_host, platform_host, "
            "  operations_host, firm_org_id, "
            "  client_portal_client_id, platform_client_id, operations_client_id "
            "FROM auth.tenant_environments WHERE tenant_id = :t ORDER BY id"
        ),
        "roles": await _rows(
            "SELECT r.id, r.slug, r.name, r.portal, r.description, "
            "  COALESCE(array_agg(p.slug ORDER BY p.slug) "
            "    FILTER (WHERE p.slug IS NOT NULL), '{}') AS permissions "
            "FROM auth.roles r "
            "LEFT JOIN auth.role_permissions rp ON rp.role_id = r.id "
            "LEFT JOIN auth.permissions p ON p.id = rp.permission_id "
            "WHERE r.tenant_id = :t GROUP BY r.id ORDER BY r.id"
        ),
        "users": await _rows(
            "SELECT id, uid, email, first_name, last_name, enabled, workos_user_id, "
            "  invitation_sent_at, invitation_accepted_at, last_sign_in, created_at, deleted_at "
            "FROM auth.users WHERE tenant_id = :t ORDER BY email"
        ),
        "memberships": await _rows(
            "SELECT m.user_id, m.portal, m.status, r.slug AS role_slug "
            "FROM auth.organisation_memberships m "
            "LEFT JOIN auth.roles r ON r.id = m.role_id "
            "WHERE m.tenant_id = :t ORDER BY m.user_id, m.portal"
        ),
        "audit_log": await _rows(
            "SELECT id, created_at, action, entity_type, entity_id, user_id, detail "
            "FROM audit.log WHERE tenant_id = :t ORDER BY id"
        ),
        "data_counts": counts,
    }


async def _audited_tenant_tables(session: AsyncSession) -> list[str]:
    """Every table that carries the ``audit_row`` trigger **and** a ``tenant_id`` column.

    Discovered from the catalogue rather than hard-coded: the list grows with every migration that
    calls ``audit.attach``, and a stale hard-coded copy would silently reintroduce the deletion
    failure below the moment a new table was attached."""
    return [
        str(t)
        for t in (
            await session.execute(
                text(
                    "SELECT n.nspname || '.' || c.relname "
                    "FROM pg_trigger tg "
                    "JOIN pg_class c ON c.oid = tg.tgrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE tg.tgname = 'audit_row' AND NOT tg.tgisinternal "
                    "  AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid "
                    "              AND a.attname = 'tenant_id' AND a.attnum > 0 "
                    "              AND NOT a.attisdropped) "
                    "ORDER BY 1"
                )
            )
        )
        .scalars()
        .all()
    ]


async def _refuse_undeletable_tenant(
    session: AsyncSession, tenant_id: int, *, is_model: bool
) -> None:
    """Refuse (ValueError → 422) deleting a tenant that isn't a firm, or that is the caller's own.

    ``delete_tenant`` offboards *firms*. Three targets must never reach the DELETE:

    * **the operations tenant** — Graphene's own staff live there, so deleting it would destroy the
      admin surface that authorises the deletion;
    * **the model tenant** — it is the role preset every new firm is cloned from, not a customer;
    * **the caller's own tenant** — the case that actually *breaks*, and why this is a guard rather
      than a nicety. ``auth.tenants`` has no ``tenant_id`` column, so ``audit.record()`` falls back
      to ``auth.tenant_id()`` (the ACTOR's tenant) when it stamps the row. Delete the actor's own
      tenant and that trigger insert references the row being removed, so ``log_tenant_id_fkey``
      fires and the transaction 500s — the same cascade-vs-trigger collision
      ``_purge_audited_tenant_rows`` exists for, one level up and not fixable by ordering. An
      operations principal deleting the operations tenant hits it every single time.

    The ops tenant is resolved from the environment registry (``operations_tenant_id``) AND from the
    caller's published claims, so re-pointing the ops environment's client id doesn't open a back
    door: the actor is still resolved to the tenant they are deleting."""
    if is_model:
        raise ValueError(
            "the model tenant is the role preset for new firms, not a firm — see /model"
        )
    try:
        ops_tenant_id = await environments.operations_tenant_id(session)
    except LookupError:
        ops_tenant_id = None
    if ops_tenant_id is not None and tenant_id == ops_tenant_id:
        raise ValueError(
            "this is Graphene's own Internal Operations tenant, not a firm — it cannot be deleted"
        )
    if await _caller_tenant_id(session) == tenant_id:
        raise ValueError("you cannot delete the tenant your own account belongs to")


async def _purge_audited_tenant_rows(session: AsyncSession, tenant_id: int) -> list[str]:
    """Delete a firm's rows from every audit-attached, tenant-scoped table — **before** its
    ``auth.tenants`` row goes.

    Why this exists, because it is not obvious and it made deletion impossible:
    ``audit.record()`` is an AFTER ROW trigger, so relying on ``ON DELETE CASCADE`` from
    ``auth.tenants`` means the cascade deletes (say) ``auth.users``, the trigger fires, and it
    INSERTs an ``audit.log`` row stamped with the affected row's ``tenant_id`` — a tenant that no
    longer exists by then. ``log_tenant_id_fkey`` is NOT DEFERRABLE, so the insert fails and the
    whole ``DELETE FROM auth.tenants`` 500s. Every invocation, unconditionally.

    Deleting those rows first keeps each trigger insert legal (the tenant is still there), and the
    audit rows it writes are cascaded away with the tenant a moment later — which is expected:
    ``export_tenant`` is the retention copy, and the deletion record itself is stamped
    ``tenant_id = NULL`` so it survives.

    Order is discovered, not assumed: a table whose delete is blocked by an inbound FK is retried on
    the next pass, until a pass makes no progress. Each attempt runs in a SAVEPOINT so a failure
    doesn't poison the outer transaction. Returns the tables actually purged."""
    remaining = await _audited_tenant_tables(session)
    purged: list[str] = []
    while remaining:
        failed: list[str] = []
        for table in remaining:
            savepoint = await session.begin_nested()
            try:
                await session.execute(
                    text(f"DELETE FROM {table} WHERE tenant_id = :t"),  # noqa: S608 - catalogue-derived identifier, never user input
                    {"t": tenant_id},
                )
            except DBAPIError:
                await savepoint.rollback()
                failed.append(table)
            else:
                await savepoint.commit()
                purged.append(table)
        if len(failed) == len(remaining):
            # No progress: leave the rest to the cascade and let the caller surface any failure
            # rather than looping forever.
            logger.warning(
                "tenant %s: could not pre-delete %s before dropping the tenant", tenant_id, failed
            )
            break
        remaining = failed
    return purged


async def delete_tenant(
    session: AsyncSession,
    *,
    tenant_id: int,
    confirm_alias: str,
) -> dict[str, object]:
    """Destroy a firm: the final step of offboarding, after suspend + export.

    Deliberately hard to fire by accident and hard to fire in the wrong order:

    - the caller must type the firm's ``alias`` (``confirm_alias``), and
    - the firm must already be suspended (``enabled = false``) — so its logins are dead and its
      sessions revoked before anything is destroyed, and the operator has had the export step.

    WorkOS keeps its own copy of the identities, so every org membership in the firm's portal orgs
    is deactivated first (best-effort): after this nobody can sign in even though the WorkOS
    project itself is deleted by hand in the dashboard. The ``auth.tenants`` row then goes, and its
    users / environments / domain rows cascade with it.

    **The compliance trail:** ``audit.log.tenant_id`` is ``ON DELETE CASCADE``, so the firm's audit
    rows die with the row. Take ``export_tenant`` first — that is the retention copy — and note
    that the deletion record written here is stamped ``tenant_id = NULL`` precisely so it survives
    the cascade. (Changing that FK to ``ON DELETE SET NULL`` is the proper fix and needs a
    migration; see docs/api.md.)

    **Why the rows are deleted explicitly first** (``_purge_audited_tenant_rows``): the row-level
    ``audit.record()`` trigger fires on the cascade and inserts an ``audit.log`` row referencing the
    tenant that has just been removed, so a plain ``DELETE FROM auth.tenants`` violates
    ``log_tenant_id_fkey`` every single time.

    **Three tenants are not firms and cannot be deleted here** (``_refuse_undeletable_tenant``):
    Graphene's own operations tenant, the model tenant, and whichever tenant the *caller* belongs
    to.

    Raises LookupError (unknown firm) or ValueError (wrong alias / still enabled / not a firm)."""
    tenant = (
        (
            await session.execute(
                text(
                    "SELECT id, uid, alias, name, enabled, is_model FROM auth.tenants WHERE id = :t"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if tenant is None:
        raise LookupError("tenant not found")
    if confirm_alias != tenant["alias"]:
        raise ValueError("the confirmation does not match this firm's alias")
    if tenant["enabled"]:
        raise ValueError("suspend the firm before deleting it")
    await _refuse_undeletable_tenant(session, tenant_id, is_model=bool(tenant["is_model"]))

    counts = {
        "users": int(
            (
                await session.execute(
                    text("SELECT count(*) FROM auth.users WHERE tenant_id = :t"), {"t": tenant_id}
                )
            ).scalar_one()
        ),
        "audit_events": int(
            (
                await session.execute(
                    text("SELECT count(*) FROM audit.log WHERE tenant_id = :t"), {"t": tenant_id}
                )
            ).scalar_one()
        ),
    }

    # Cut WorkOS access before the local rows go (afterwards we'd have no ids to work from).
    deactivated = 0
    for user_id in (
        (
            await session.execute(
                text("SELECT id FROM auth.users WHERE tenant_id = :t"), {"t": tenant_id}
            )
        )
        .scalars()
        .all()
    ):
        try:
            # One-firm-org: one WorkOS membership per person.
            membership_id, _org, workos = await _workos_firm_membership(
                session, tenant_id=tenant_id, user_id=str(user_id)
            )
            await workos.organization_membership.deactivate_organization_membership(membership_id)
            deactivated += 1
            wu = await _workos_user_id(session, tenant_id, str(user_id))
            await _revoke_user_sessions(session, workos, wu)
        except (LookupError, WorkOSError):
            continue  # already gone / no environment — the local delete still proceeds

    # tenant_id NULL so this record survives the cascade (see the docstring).
    await _audit(
        session,
        "auth.tenants.deleted",
        tenant_id=None,
        entity_type="auth.tenants",
        detail={
            "tenant": {k: str(tenant[k]) for k in ("id", "uid", "alias", "name")},
            "workos_memberships_deactivated": deactivated,
            **counts,
        },
    )
    # Must happen before the tenant row goes — see the docstring.
    purged = await _purge_audited_tenant_rows(session, tenant_id)
    await session.execute(text("DELETE FROM auth.tenants WHERE id = :t"), {"t": tenant_id})
    return {
        "tenant_id": tenant_id,
        "alias": tenant["alias"],
        "deleted": True,
        "workos_memberships_deactivated": deactivated,
        "tables_purged": len(purged),
        **counts,
    }


USER_TYPES: tuple[str, ...] = ("client", "staff")


STAFF_PROFILE_FIELDS: tuple[str, ...] = (
    "job_title",
    "qualifications",
    "fca_reference",
    "bio",
    "photo_url",
    "direct_phone",
)


def fold_email(email: str | None) -> str:
    """The ONE normalisation of an address before it touches `auth.users`.

    `auth.users.email` is stored folded (0125) and `users_email_is_folded` enforces it, so this is
    not a convenience: a write path that skips it fails at the constraint. That is deliberate —
    the alternative, discovered in 0124, is that a path which does not normalise silently creates
    a SECOND login for one person, because `ON CONFLICT (tenant_id, email)` does not match a
    differently-cased address.

    Whitespace goes too. A pasted address routinely carries a trailing space, and `' a@b.test'` is
    not `'a@b.test'` to any unique key.
    """
    return (email or "").strip().lower()


async def set_provider_ref(
    session: AsyncSession,
    *,
    tenant_id: int,
    user_id: str,
    provider: str,
    external_id: str,
) -> dict[str, object]:
    """Record which id a custodian knows this person by, or clear it.

    `auth.users.provider_refs` (0118) is a `provider -> id` map. SEI's is `UserDetail.ENTITY_ID`,
    which enrolment takes as `employeeServicingRelationshipList[].employeeEntityId` — required by
    `POST /accountservice/accounts` and, until 0118, with no source in the estate at all.

    ONE implementation, called by both the operations route (`/admin/tenants/…`) and the firm's own
    (`/users/…`). The two differ in who may call them and how the tenant is established, which is
    the routers' business; what a mapping IS must not be, or the statement drifts and the clearing
    branch below is exactly the sort of detail that drifts first.

    **This is the MANUAL half.** `auth.merge_provider_user` (0123) attaches automatically wherever
    the custodian's address matches a login's, which is the common case; this is for the ordinary
    one it declines to guess at — a firm's SEI address and its Graphene login address are
    frequently different. Never inferred from a NAME: only 37 of the 233 distinct
    `ACCOUNT_DSR_EMP_ID` values in SEI's ClientAccountLink appear in `UserDetail` at all, so a
    display-name match would be wrong 84% of the time and the failure would be an account opened
    naming the wrong adviser.
    """
    known = provider.strip().lower()
    if known not in providers.PROVIDERS:
        raise ValueError(f"unknown provider '{provider}'")
    wanted = external_id.strip()
    updated = (
        await session.execute(
            text(
                # `- :p` removes the key when the id is blank, so clearing a mapping leaves no
                # `{"sei": ""}` behind — an empty string in a vendor payload is not the same as
                # sending nothing, and a drawer's prefill would treat it as configured.
                "UPDATE auth.users SET provider_refs = CASE "
                "    WHEN :ext = '' "
                "      THEN NULLIF(COALESCE(provider_refs, '{}'::jsonb) - :p, '{}'::jsonb) "
                "    ELSE COALESCE(provider_refs, '{}'::jsonb) || jsonb_build_object(:p, :ext) "
                "  END "
                " WHERE id = :u AND tenant_id = :t "
                " RETURNING provider_refs"
            ),
            {"u": str(user_id), "t": tenant_id, "p": known, "ext": wanted},
        )
    ).scalar_one_or_none()
    # A CLEAR of a mapping nobody holds is a no-op rather than a 404: the statement returns NULL
    # both for "no such member" and for "that member had no refs", and only the first is an error.
    if updated is None and wanted:
        raise LookupError("that person is not a member of this firm")
    return {"provider": known, "provider_refs": updated or {}}


async def update_member_profile(
    session: AsyncSession,
    *,
    tenant_id: int,
    user_id: str,
    fields: dict[str, object],
) -> dict[str, object]:
    """What a FIRM says about one of its people — the team member page's save.

    Writes only the keys it is GIVEN, which is the rule the catalogue's model form had to learn
    the hard way: a form that posts its whole shape clears every control it does not render, and
    that is how saving a model's name came to wipe the VAT flag the platform had set.

    Three groups, and they are here together because they are one editing session on one person:

    * the NAME (`first_name`/`last_name`) — WorkOS also writes these on a reconcile, so a firm's
      correction to a misspelt surname holds only until the next sync. That is a real limitation
      and it belongs in the drawer's copy, not in a refusal: the alternative is a firm unable to
      fix a name at all.
    * the NATURE — `type` (client and/or staff) and `is_adviser`. Validated against
      :data:`USER_TYPES`, and `is_adviser` implies staff, which the database also enforces
      (`users_adviser_is_staff`): asking for an adviser who is not staff is a request problem, so
      it raises rather than being quietly widened.
    * the STAFF PROFILE (0118) — :data:`STAFF_PROFILE_FIELDS`. `qualifications` is a list.

    NOT here: `enabled`. A login's status is not a column a firm sets — deactivating has to reach
    WorkOS too, or the person keeps their session and can still sign in. `deactivate_member` and
    `reactivate_member` own that, and the drawer's switch calls them.
    """
    updates: dict[str, object] = {}

    if "type" in fields:
        raw = fields["type"]
        types = [str(t) for t in raw] if isinstance(raw, list) else []
        unknown = sorted(set(types) - set(USER_TYPES))
        if unknown:
            raise ValueError(f"unknown type(s) {unknown}; expected any of {list(USER_TYPES)}")
        # Sorted and de-duplicated so two callers stating the same nature write the same array,
        # and a no-op save stays a no-op (`auth.users` carries an audit.record trigger).
        updates["type"] = sorted(set(types))

    if "is_adviser" in fields:
        updates["is_adviser"] = bool(fields["is_adviser"])

    # The implication, checked HERE so the message names it. The CHECK constraint is the floor and
    # would refuse the write anyway, but it would refuse it by naming a constraint.
    #
    # BOTH DIRECTIONS. It only asked whether the SAVE wanted to make somebody an adviser, so
    # narrowing `type` on an existing one slipped past: `PUT /firm/members/{id}` with
    # `{"type": ["client"]}` sends no `is_adviser` key at all (the route passes
    # `exclude_unset=True` and the field is optional), so `wants_adviser` was False, neither
    # branch ran, and the UPDATE wrote `type = '{client}'` while leaving `is_adviser = true` —
    # straight into `users_adviser_is_staff` as a 500 naming a constraint. Removing staff from an
    # adviser is the ordinary way a firm changes somebody's job.
    #
    # So the question is what the row will HOLD once this save lands, which needs the stored value
    # wherever the body does not state one.
    needs_stored = ("is_adviser" not in updates and "type" in updates) or (
        updates.get("is_adviser") and "type" not in updates
    )
    stored: dict[str, Any] = {}
    if needs_stored:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT type, is_adviser FROM auth.users WHERE id = :u AND tenant_id = :t"
                    ),
                    {"u": user_id, "t": tenant_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise LookupError("Member not found in this firm")
        stored = dict(row)

    resulting_adviser = bool(
        updates["is_adviser"] if "is_adviser" in updates else stored.get("is_adviser", False)
    )
    resulting_type = list(
        cast("list[str]", updates["type"]) if "type" in updates else (stored.get("type") or [])
    )
    if resulting_adviser and "staff" not in resulting_type:
        raise ValueError(
            "an adviser is a member of staff, so `type` must include 'staff' — "
            "clear `is_adviser` in the same save to move this person off the staff side"
        )

    org_fields: tuple[tuple[str, Literal["seccl"]], ...] = (("seccl_node", "seccl"),)
    if "sei_dsr" in fields:
        # The adviser's own SEI user, not the office team. Enrolment reads this id
        # and only names them on a relationship they already hold.
        from app.adviser_orgs import employee_options

        value = fields["sei_dsr"]
        text_value = str(value).strip() if value is not None else ""
        if text_value:
            allowed = {
                item["value"] for item in await employee_options(session, tenant_id=tenant_id)
            }
            if text_value not in allowed:
                raise ValueError("That SEI employee is not one this firm's book grants.")
        await set_provider_ref(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            provider="sei",
            external_id=text_value,
        )
    if any(field in fields for field, _provider in org_fields):
        # Merge into the stored object. A save that names only the Seccl node must not wipe
        # the other key, and a blank clears that one key. The id has to be an enabled node.
        from app.adviser_orgs import assignable_org_keys

        stored_orgs = (
            (
                await session.execute(
                    text("SELECT provider_orgs FROM auth.users WHERE id = :u AND tenant_id = :t"),
                    {"u": user_id, "t": tenant_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if stored_orgs is None:
            raise LookupError("Member not found in this firm")
        current = stored_orgs["provider_orgs"] or {}
        if isinstance(current, str):
            current = json.loads(current)
        if not isinstance(current, dict):
            current = {}
        current = dict(current)
        labels = {"seccl": "Seccl node"}
        for field, provider in org_fields:
            if field not in fields:
                continue
            value = fields[field]
            text_value = str(value).strip() if value is not None else ""
            if not text_value:
                current.pop(provider, None)
                continue
            enabled = await assignable_org_keys(session, tenant_id=tenant_id, provider=provider)
            if text_value not in enabled:
                raise ValueError(f"That {labels[provider]} is not one this firm has enabled.")
            current[provider] = text_value
        updates["provider_orgs"] = json.dumps(current) if current else None

    for name in ("first_name", "last_name", *STAFF_PROFILE_FIELDS):
        if name in fields:
            value = fields[name]
            if name == "qualifications":
                updates[name] = (
                    [str(q).strip() for q in value if str(q).strip()]
                    if isinstance(value, list)
                    else []
                )
            else:
                # A blank box CLEARS the field rather than storing "" — every reader treats an
                # empty string as present, so an adviser card would draw an empty row under the
                # name (the `Adviser` type's own rule: omitted, never "").
                text_value = str(value).strip() if value is not None else ""
                updates[name] = text_value or None

    if not updates and "sei_dsr" not in fields:
        raise ValueError("nothing to update")

    returning = (
        "RETURNING id, email, name, enabled, type, is_adviser, job_title,"
        " qualifications, fca_reference, bio, photo_url, direct_phone,"
        " provider_orgs, provider_refs"
    )
    if updates:
        assignments = ", ".join(
            "provider_orgs = CAST(:provider_orgs AS jsonb)"
            if name == "provider_orgs"
            else f"{name} = :{name}"
            for name in updates
        )
        statement = (
            f"UPDATE auth.users SET {assignments} WHERE id = :u AND tenant_id = :t {returning}"  # noqa: S608 - keys are our own literals
        )
        params = {**updates, "u": user_id, "t": tenant_id}
    else:
        statement = (
            "SELECT id, email, name, enabled, type, is_adviser, job_title, qualifications, "
            "fca_reference, bio, photo_url, direct_phone, provider_orgs, provider_refs "
            "FROM auth.users WHERE id = :u AND tenant_id = :t"
        )
        params = {"u": user_id, "t": tenant_id}
    row = (await session.execute(text(statement), params)).mappings().one_or_none()
    if row is None:
        raise LookupError("Member not found in this firm")
    if updates.get("is_adviser") is False:
        # Only an `is_adviser` login may be a client's adviser (0245). Somebody who stops being
        # one keeps every client they were on — they stay on each team as support — and those
        # clients are left with no adviser until the firm names one, which the clients list shows.
        await session.execute(
            text(
                "UPDATE crm.client_advisers SET role = :support "
                "WHERE tenant_id = :t AND adviser_id = :u AND role = :adviser"
            ),
            {
                "t": tenant_id,
                "u": user_id,
                "adviser": contacts_mod.ADVISER_ROLE,
                "support": contacts_mod.SUPPORT_ROLE,
            },
        )
    out = dict(row)
    out["user_id"] = str(out.pop("id"))
    out["type"] = list(out["type"] or [])
    out["qualifications"] = list(out["qualifications"] or [])
    orgs = out.pop("provider_orgs") or {}
    if isinstance(orgs, str):
        orgs = json.loads(orgs)
    if not isinstance(orgs, dict):
        orgs = {}
    refs = out.pop("provider_refs") or {}
    if isinstance(refs, str):
        refs = json.loads(refs)
    if not isinstance(refs, dict):
        refs = {}
    out["seccl_node"] = orgs.get("seccl")
    out["sei_dsr"] = refs.get("sei")
    return out
