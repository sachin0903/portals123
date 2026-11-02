"""Service-to-service internal endpoints (not for browsers).

The shared portal fetches its per-host WorkOS config here (project-per-tenant, §0): the API
is the sole holder of SECRETS_MASTER_KEY, so it decrypts and returns the environment config
the portal needs to construct its WorkOS client. Guarded by a shared internal token
(``INTERNAL_API_TOKEN``); when that is unset the endpoints are disabled (404). Deploy with
these paths reachable only on the internal network.

It is also where the portals' own unauthenticated forms borrow the API's rate limiter: the
portals do the WorkOS call themselves (login), so they ask ``POST /internal/rate-limit``
whether this attempt is allowed. Keeping the counters in one process is the point — a limiter
per portal container is three independent, useless limiters.

And it is where a portal pushes state the API cannot observe: ``POST /internal/onboarding-complete``
tells the mirror that someone has just set their password, which otherwise waited on the WorkOS
events poller.
"""

import hmac
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from workos import AsyncWorkOSClient

from app import audit_actions as actions
from app import branding, environments, provisioning, ratelimit, shell_images, storage
from app.comms import ledger
from app.config import settings
from app.db import SessionLocal
from app.email import render as email_render
from app.email import transport as email_mod
from app.logging import report_error
from app.sign_in_context import sign_in_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"], include_in_schema=False)


def require_internal_token(x_internal_token: str = Header(default="")) -> None:
    """Constant-time check of the shared internal token. A missing config or a bad token is
    a 404 (not 401/403) so the endpoint's existence isn't advertised."""
    expected = settings.internal_api_token
    if not expected or not hmac.compare_digest(x_internal_token, expected):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


async def internal_session() -> AsyncIterator[AsyncSession]:
    """A service_role session for internal calls (no request principal). Gated by the router
    token dependency, not by a user JWT."""
    async with SessionLocal() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        yield session


@router.get("/env-config", name="env-config", dependencies=[Depends(require_internal_token)])
async def env_config(
    host: str,
    session: AsyncSession = Depends(internal_session),
) -> dict[str, object]:
    """The decrypted WorkOS config for a portal host (client_id / issuer / api key / cookie
    password / auth domain). 404 for an unknown host."""
    config = await environments.env_config_by_host(session, host)
    if config is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown host")
    return config


@router.get(
    "/portal-for-host", name="portal-for-host", dependencies=[Depends(require_internal_token)]
)
async def portal_for_host(
    host: str,
    response: Response,
    session: AsyncSession = Depends(internal_session),
) -> dict[str, str]:
    """Which portal a public host is: ``client`` / ``platform`` / ``operations``. 404 for a host no
    tenant has registered.

    The ingress router's lookup. Tenants bring their own domains, so the origin proxy cannot
    tell ``app.firm.com`` from ``advisers.firm.com`` by pattern; it asks here (Caddy
    ``forward_auth``) and routes on the answer. The answer rides in the ``X-Portal`` response
    header as well as the body because that is what a forward-auth hop can copy into the
    upstream request. No secrets: ``env_config_by_host`` decrypts the environment's WorkOS keys,
    which a per-request routing read must never do.
    """
    portal = await environments.portal_for_host(session, host)
    if portal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown host")
    response.headers["X-Portal"] = portal
    # Host→portal changes rarely (a tenant onboards a domain); let the router hold it briefly.
    response.headers["Cache-Control"] = "private, max-age=60"
    return {"portal": portal}


@router.get(
    "/assets/{name}", name="internal-public-asset", dependencies=[Depends(require_internal_token)]
)
async def public_asset(
    name: str,
    host: str = "",
    operations: bool = False,
    tenant_uid: str = "",
    session: AsyncSession = Depends(internal_session),
) -> Response:
    """Relay one content-hashed public asset from internal object storage.

    The portal authenticates this server-to-server call. The browser sees only its own
    ``/_assets/{opaque-name}`` URL; object-store endpoints, buckets and keys never cross the API.
    """
    if tenant_uid:
        tenant_id = (
            await session.execute(
                text("SELECT id FROM auth.tenants WHERE uid = :uid"), {"uid": tenant_uid}
            )
        ).scalar_one_or_none()
        if tenant_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        tenant_id = int(tenant_id)
    elif operations:
        try:
            tenant_id = await environments.operations_tenant_id(session)
        except LookupError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found") from exc
    else:
        config = await environments.env_config_by_host(session, host)
        if config is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        tenant_id = int(config["tenant_id"])
    try:
        data, content_type = await storage.get_public_asset(session, tenant_id, name)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found") from exc
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            # SVG is accepted for logos. It must remain safe even when opened directly under the
            # portal origin, not only when embedded in <img>.
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/admin-branding", name="admin-branding", dependencies=[Depends(require_internal_token)]
)
async def admin_branding(
    session: AsyncSession = Depends(internal_session),
) -> dict[str, object]:
    """The Graphene (operations) tenant's own logo set + name, for the ADMIN portal's shell.

    The admin portal has no host row in ``auth.tenant_environments`` (only client/platform hosts
    exist), so ``/internal/env-config`` can never resolve it — this is the minimal read instead.
    Deliberately ONLY the logo assets and the name: the admin surface keeps the product look
    (colours/fonts/material stay Graphene's), so none of the theming tokens are served here.
    The logos come from the Graphene tenant's CLIENT-portal branding — the set edited at
    ``/admin/tenants/{id}/branding`` — the same place every firm's logo set lives.

    404 when the core WorkOS environment isn't registered yet (local dev before seeding): the
    portal treats that exactly like an unknown host and falls back to the static Graphene brand.
    """
    try:
        tenant_id = await environments.operations_tenant_id(session)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    row = (
        (
            await session.execute(
                text("SELECT name, theme FROM auth.tenants WHERE id = :t"), {"t": tenant_id}
            )
        )
        .mappings()
        .one()
    )
    b = branding.parse_lenient(row["theme"], "client")

    def shell_src(value: str | None) -> str | None:
        # A large inline raster is withheld: a data URI in the admin shell is decoded on the main
        # thread and becomes Largest Contentful Paint (`shell_images.shell_safe_src`).
        return shell_images.shell_safe_src(storage.public_asset_src(value)) if value else None

    return {
        "app_name": row["name"],
        "logo_url": shell_src(b.logo_url),
        "logo_dark_url": shell_src(b.logo_dark_url),
        "icon_url": shell_src(b.icon_url),
        "logo_variant": b.logo_variant,
    }


class ForgotPassword(BaseModel):
    email: str
    # The portal's public host — its WorkOS project is resolved from it, and the set-password
    # link points back to it. e.g. "app.firm.com" or the ops portal host.
    host: str
    # The END USER's IP, as seen by the portal. The API only ever sees the portal container, so
    # without this the per-IP limit is meaningless (the per-email/per-host ones still bite).
    client_ip: str | None = None


def _is_expected_workos_miss(exc: BaseException) -> bool:
    """True for the ordinary "no such user" answer, false for an outage.

    WorkOS answers an unknown email with a 4xx; that is the privacy-preserving no-op path and
    stays at INFO. A 5xx, a 429, a connection error or anything unexpected is an OUTAGE — every
    password reset on the instance is failing — and must reach the error tracker.
    """
    code = getattr(exc, "status_code", None)
    return isinstance(code, int) and 400 <= code < 500 and code != 429


@router.post(
    "/forgot-password", name="forgot-password", dependencies=[Depends(require_internal_token)]
)
async def forgot_password(
    body: ForgotPassword,
    session: AsyncSession = Depends(internal_session),
) -> dict[str, bool]:
    """Send a password-set/reset link (Resend) for the given email on the given portal host.
    Reused for both 'forgot password' and login-time email verification (setting a password
    verifies the email). Always returns ok — never reveals whether the email exists.

    **The email wears the brand of the portal that asked.** ``host`` names one — a tenant
    environment registers its client-portal and platform hosts separately — so a staffer resetting
    from the platform portal gets the platform branding and sender identity, not the client-portal
    one. That is a property of the EMAIL, which only the account owner receives; the response is a
    fixed ``{ok: true}`` on every branch and carries none of it.

    Rate-limited per email, per portal host and (when the portal passes it) per end-user IP: the
    form is unauthenticated, so without a cap it is an email bomb aimed at any known address and
    a way to burn our Resend/WorkOS quota. A throttled request returns the same ``{ok: true}``
    (no oracle) and sends nothing.

    **A suspended firm gets nothing.** This is the one credential-minting path that runs on
    ``internal_session`` (service_role, no principal, no ``_refuse_if_suspended``), so without the
    check below "every member is blocked from both portals" leaked: the forgot-password form on a
    suspended firm's own host would still mint a WorkOS reset token and email a working
    set-password link. Same silent ``{ok: true}`` as every other refusal here, so it stays free of
    an oracle.
    """
    throttle = await ratelimit.limiter.check(
        ratelimit.FORGOT_PASSWORD,
        ratelimit.forgot_password_limits(
            email=body.email, host=body.host, client_ip=body.client_ip
        ),
    )
    if not throttle.allowed:
        # Same response as the success path — the caller must not learn it was throttled either.
        return {"ok": True}

    cfg = await environments.env_config_by_host(session, body.host)
    # A suspended firm's people are locked out; minting them a set-password link is not a reset,
    # it is a way back in. (A host we don't recognise is the ops/admin host or local dev — that
    # falls through to the core project below, and has no firm to suspend.)
    if cfg is not None and not cfg.get("tenant_enabled", True):
        logger.info(
            "forgot-password: the firm is suspended, nothing sent",
            extra={"event": "forgot_password_tenant_suspended", "host": body.host},
        )
        return {"ok": True}
    if cfg and cfg.get("workos_api_key"):
        workos = AsyncWorkOSClient(
            api_key=str(cfg["workos_api_key"]), client_id=str(cfg["workos_client_id"] or "")
        )
    else:
        # Admin/ops host (not a tenant host) or local dev — the platform's core project.
        workos = environments.core_workos_client()
    tenant_id = int(cfg["tenant_id"]) if cfg and cfg.get("tenant_id") is not None else None
    # WHICH audience asked. `body.host` is the portal the form was posted from, and a tenant
    # environment registers its client-portal and platform hosts separately, so the host names the
    # portal — `env_config_by_host` has always derived it (it is how the config's own `branding` is
    # picked) and now returns it. Every other send site resolves a portal like this
    # (`tasks/notifier.py` from the assignee kind, `provisioning.py` from the portal being granted);
    # this one took `brand_for`'s `client` default for every caller, so a member of a firm's STAFF
    # resetting from the platform portal received the client-portal brand and sender identity.
    #
    # Safe against the no-oracle rule: it is decided from the CALLER'S OWN host, before and
    # independently of anything WorkOS says, and it changes only the EMAIL — which only the account
    # owner ever receives. The response below stays a fixed `{ok: true}` on every branch, so nothing
    # observable to an unauthenticated caller varies with whether the address exists, or with which
    # side of the firm it belongs to. An unknown host is not a tenant host at all and takes the
    # Graphene branch, unchanged.
    portal: branding.Portal = "platform" if (cfg or {}).get("portal") == "platform" else "client"
    try:
        reset = await workos.user_management.reset_password(email=body.email)
    except Exception as exc:  # noqa: BLE001 — classified below; the caller always gets {ok:true}
        if _is_expected_workos_miss(exc):
            logger.info(
                "forgot-password: no matching user",
                extra={"event": "forgot_password_no_user", "host": body.host},
            )
        else:
            report_error(
                "forgot-password: WorkOS reset_password failed — password resets are DOWN for "
                "this host",
                exc=exc,
                log=logger,
                event="forgot_password_provider_failed",
                host=body.host,
            )
        return {"ok": True}
    url = f"https://{body.host}/set-password?token={reset.password_reset_token}"
    # The firm's own envelope identity on a white-labelled host (the invite paths already do
    # this) — a reset mail from another brand reads as phishing. `portal` matters here too: the
    # support address it resolves is per-portal, so a staff reset would otherwise carry the
    # client-facing Reply-To.
    sender = await email_mod.resolve_sender(session, tenant_id, portal=portal)
    if tenant_id is not None:
        # Resolve the firm brand and dress BOTH the shipped default and (via context) any published
        # override in it — otherwise a firm with no override gets a Graphene-branded reset.
        brand = await email_mod.brand_for(session, tenant_id, portal=portal)
        subject, html = await email_render.render_or_default(
            session,
            tenant_id=tenant_id,
            code="password_reset",
            context={
                # NO NAME, all three of them: naming the recipient tells a stranger who typed the
                # address that it has an account here.
                "first_name": None,
                "full_name": None,
                "surname": None,
                "set_password_url": url,
                "brand": email_mod.brand_tokens(brand),
            },
        )
    else:
        # No tenant host (ops/admin or local dev) — no per-firm template to resolve. Still put the
        # shipped default in the same envelope `render_or_default` would (document wrapper, base
        # CSS, preheader span): the reset mail a Graphene operator receives must not be a different
        # shape from the one a firm's user receives just because it took the other branch.
        subject, html = email_render.render_default(
            "password_reset",
            # `asset_origin=None`: this branch is the one with no tenant, so there is no firm
            # host to resolve assets against — and the shipped default carries no uploaded image
            # anyway. The tenant branch above gets its origin from `render_or_default`.
            asset_origin=None,
            context={
                "brand": email_mod.brand_tokens(email_mod.GRAPHENE_BRAND),
                # All three names, as the tenant branch passes them: the code's vocabulary is one
                # list, so a branch that omits two of it renders whatever references them blank.
                # None rather than absent for the same reason as above — a reset names nobody.
                "first_name": None,
                "full_name": None,
                "surname": None,
                "set_password_url": url,
            },
        )
    sent = await email_mod.send_email(
        to=body.email,
        subject=subject,
        html=html,
        sender=sender.from_addr,
        reply_to=sender.reply_to,
        # 'external' deliberately: this route must not confirm whether an account exists, so it
        # resolves no user and the ledger records exactly what the route knew. `tenant_id` is None
        # on the ops/local branch, which makes the row Graphene's own.
        ctx=ledger.SendContext(tenant_id=tenant_id, template_code="password_reset"),
    )
    if not sent:
        report_error(
            "forgot-password: the reset email was not sent (Resend unconfigured or failing)",
            log=logger,
            event="forgot_password_email_failed",
            host=body.host,
        )
    return {"ok": True}


class OnboardingComplete(BaseModel):
    """A portal reporting that someone has just set their password on ``/set-password``."""

    # Either identifier will do; ``workos_user_id`` is exact, ``email`` needs ``host`` to
    # disambiguate (email is unique per TENANT, so the same address exists in several firms).
    email: str | None = None
    workos_user_id: str | None = None
    # The portal host the reset happened on: resolves the firm and the WorkOS project this claim is
    # verified against. Omitted for the ops/admin host (which is not a tenant host).
    host: str | None = None


def _workos_for_host_cfg(cfg: dict[str, object] | None) -> AsyncWorkOSClient:
    """The WorkOS client for a portal host's project, falling back to the platform's core project
    (the ops/admin host and local dev are not tenant hosts). Mirrors ``forgot_password``."""
    if cfg and cfg.get("workos_api_key"):
        return AsyncWorkOSClient(
            api_key=str(cfg["workos_api_key"]), client_id=str(cfg["workos_client_id"] or "")
        )
    return environments.core_workos_client()


@router.post(
    "/onboarding-complete",
    name="onboarding-complete",
    dependencies=[Depends(require_internal_token)],
)
async def onboarding_complete(
    body: OnboardingComplete,
    session: AsyncSession = Depends(internal_session),
) -> dict[str, object]:
    """Push the pending→active transition into the mirror the moment someone sets their password.

    Setting a password happens entirely at the portal (self-hosted ``/set-password`` →
    ``confirmPasswordReset``) and wrote nothing here, so the only reconciler was the WorkOS events
    poller. A fully-onboarded person therefore rendered as never-invited until the next poll — or
    indefinitely, wherever polling is off. This is the push half; the poller is the safety net.

    **The claim is verified, not trusted.** The identifier reaches us from a portal action, so
    before touching anything we ask WorkOS whether that identity really is ``email_verified``
    (which is what setting a password does). Combined with ``mark_onboarding_complete`` only ever
    promoting ``pending`` memberships, a wrong or tampered identifier cannot re-enable a suspended
    account.

    Always 200 with counts: it is reachable from an unauthenticated user journey, so an unknown or
    unverified identity is a silent no-op rather than an existence oracle, and a WorkOS outage must
    not break the page on which the user has *already* set their password."""
    if not body.email and not body.workos_user_id:
        return {"ok": True, "users": 0, "memberships": 0}

    cfg = await environments.env_config_by_host(session, body.host) if body.host else None
    workos = _workos_for_host_cfg(cfg)
    tenant_id = int(cfg["tenant_id"]) if cfg and cfg.get("tenant_id") is not None else None

    # Confirm with WorkOS that the password really was set (setting one verifies the email).
    wuser: Any | None
    try:
        if body.workos_user_id:
            wuser = await workos.user_management.get_user(body.workos_user_id)
        else:
            found = (await workos.user_management.list_users(email=body.email)).data
            wuser = found[0] if found else None
    except Exception as exc:  # noqa: BLE001 — never fail the user's page; the poller reconciles
        report_error(
            "onboarding-complete: could not verify the identity with WorkOS",
            exc=exc,
            log=logger,
            event="onboarding_complete_verify_failed",
            host=body.host or "",
        )
        return {"ok": True, "users": 0, "memberships": 0}

    if wuser is None or not bool(getattr(wuser, "email_verified", False)):
        logger.info(
            "onboarding-complete: no verified identity for this claim",
            extra={"event": "onboarding_complete_unverified", "host": body.host or ""},
        )
        return {"ok": True, "users": 0, "memberships": 0}

    result = await provisioning.mark_onboarding_complete(
        session,
        workos_user_id=str(wuser.id),
        email=getattr(wuser, "email", None) or body.email,
        tenant_id=tenant_id,
    )
    # Record the activation as an auth event — the moment someone set their password and became
    # active. Only when a real pending→active promotion happened (a re-call promotes nothing, so it
    # must not re-emit); best-effort, so a stalled event write never breaks the set-password page.
    memberships = result.get("memberships", 0)
    users = result.get("users", 0)
    promoted = (isinstance(memberships, int) and memberships > 0) or (
        isinstance(users, int) and users > 0
    )
    if promoted:
        await actions.record_auth_action(
            event_type=actions.AUTH_ACTIVATED_EVENT,
            tenant_id=tenant_id,
            actor_sub=str(wuser.id),
        )
    return {"ok": True, **result}


class AuthEvent(BaseModel):
    """A portal reporting an authentication action the API's request floor cannot see — login /
    logout / MFA happen in the portal's own server via WorkOS, never as an API mutation."""

    # Closed vocabulary — a caller cannot invent an event type.
    kind: str = Field(
        description="'login' | 'logout' | 'login_failed' | 'mfa_enrolled' | 'mfa_removed'"
    )
    # The acting login's WorkOS id (present for every kind except login_failed, where the
    # credentials were wrong so there is no verified subject).
    workos_user_id: str | None = None
    # The attempted email — recorded for a failed login only (the security-relevant identifier, not
    # a credential); ignored for the other kinds.
    email: str | None = None
    # The authentication METHOD (WorkOS-aligned: password / sso / oauth / mfa / magic) — recorded on
    # login + login_failed so the trail reads "Signed in with a password", mirroring WorkOS's
    # authentication.<method>_{succeeded,failed}. Unknown/absent values are dropped, not trusted.
    method: str | None = None
    # The browser this sign-in came from. Recorded on a successful login only — WorkOS is told the
    # same IP and user agent on the call that minted the session, and the device label is derived
    # from the user agent (WorkOS has no device-type field).
    client_ip: str | None = None
    user_agent: str | None = None
    device: str | None = None
    # The portal host, which resolves the firm (tenant) this event belongs to. Omitted for the
    # ops/admin host and local dev, which are not tenant hosts.
    host: str | None = None


#: kind → (event type, HTTP-style status). Closed map: an unknown kind is refused, never recorded.
_AUTH_EVENT_KINDS: dict[str, tuple[str, int]] = {
    "login": (actions.AUTH_LOGIN_EVENT, 200),
    "logout": (actions.AUTH_LOGOUT_EVENT, 200),
    "login_failed": (actions.AUTH_LOGIN_FAILED_EVENT, 401),
    "mfa_enrolled": (actions.AUTH_MFA_ENROLLED_EVENT, 200),
    "mfa_removed": (actions.AUTH_MFA_REMOVED_EVENT, 200),
}

#: The authentication methods we record (WorkOS's authentication.<method>_* vocabulary). A method
#: outside this closed set is dropped rather than trusted — the label reads generically then.
_AUTH_METHODS: frozenset[str] = frozenset({"password", "sso", "oauth", "mfa", "magic"})


@router.post("/auth-event", name="auth-event", dependencies=[Depends(require_internal_token)])
async def auth_event(
    body: AuthEvent,
    session: AsyncSession = Depends(internal_session),
) -> dict[str, object]:
    """Record an authentication event a portal performed (see ``AuthEvent``). Token-gated
    service-to-service, so it is on ``EXEMPT_MUTATING_ROUTES`` (no human principal — the acting
    login, when there is one, is named by its WorkOS id). Always 200: it rides an auth flow that has
    already happened, and it must never break the portal's login/logout page. An unknown ``kind`` is
    a silent no-op rather than an error.

    The firm is resolved from the host (like ``forgot_password``/``onboarding_complete``);
    ``record_auth_action`` resolves the actor uuid from the WorkOS id so the trail reads by name. A
    FAILED login has no verified subject — it is attributed to ``ANONYMOUS_LOGIN_SUB`` with the
    attempted email in the metadata."""
    mapped = _AUTH_EVENT_KINDS.get(body.kind)
    if mapped is None:
        return {"ok": True}  # unknown kind — never invent an event type
    event_type, status_code = mapped

    cfg = await environments.env_config_by_host(session, body.host) if body.host else None
    tenant_id = int(cfg["tenant_id"]) if cfg and cfg.get("tenant_id") is not None else None

    # The method is a closed enum recorded only on the authentication events (login / login_failed),
    # never on logout (which is session-revocation, not an authentication).
    method = body.method if body.method in _AUTH_METHODS else None
    subject: dict[str, str] | None = (
        {"method": method} if (method and body.kind in ("login", "login_failed")) else None
    )
    # Browser context rides the successful sign-in only. `device` is on the subject so the
    # activity sentence can say "from Chrome on macOS"; the IP and user agent stay beside it
    # in the metadata, capped, and are not woven into the sentence.
    context = (
        sign_in_context(client_ip=body.client_ip, user_agent=body.user_agent, device=body.device)
        if body.kind == "login"
        else {}
    )
    if context.get("device"):
        subject = {**(subject or {}), "device": context["device"]}
    extra: dict[str, Any] | None = {
        key: context[key] for key in ("ip", "user_agent") if key in context
    } or None

    if body.kind == "login_failed":
        await actions.record_auth_action(
            event_type=event_type,
            tenant_id=tenant_id,
            actor_sub=actions.ANONYMOUS_LOGIN_SUB,
            status=status_code,
            subject=subject,
            extra={"attempted_email": body.email} if body.email else None,
        )
        return {"ok": True}

    if not body.workos_user_id:
        return {"ok": True}  # login/logout/MFA need a subject to attribute — nothing to record
    await actions.record_auth_action(
        event_type=event_type,
        tenant_id=tenant_id,
        actor_sub=body.workos_user_id,
        status=status_code,
        subject=subject,
        extra=extra,
    )
    return {"ok": True}


class RateLimitCheck(BaseModel):
    """A portal asking whether one attempt on an unauthenticated surface is allowed."""

    # Which cap to apply. Enumerated, not free-form: a caller must not be able to invent a
    # bucket and get an unmetered one.
    surface: str = Field(description="'login' | 'forgot-password' | 'mfa'")
    # The identity being attempted: an email address for login/forgot-password; for mfa an opaque
    # per-challenge handle (a hash of the pending token — never the token, which is a credential).
    identifier: str
    # The portal host, so one firm's login attempts can't exhaust another firm's budget.
    host: str
    # The END USER's IP as the portal sees it (SvelteKit `getClientAddress()`).
    client_ip: str | None = None


class RateLimitOut(BaseModel):
    allowed: bool
    retry_after_seconds: int


@router.post("/rate-limit", name="rate-limit", dependencies=[Depends(require_internal_token)])
async def rate_limit(body: RateLimitCheck) -> RateLimitOut:
    """Consume one token on an unauthenticated portal surface.

    Returns 200 either way (never 429): the caller is a trusted server-side portal action that
    needs to render its own copy — "too many attempts, try again in N seconds" — not an HTTP
    error. Calling this CONSUMES a token, so call it once per real attempt.
    """
    if body.surface == ratelimit.LOGIN:
        keyed = ratelimit.login_limits(
            email=body.identifier, host=body.host, client_ip=body.client_ip
        )
    elif body.surface == ratelimit.FORGOT_PASSWORD:
        keyed = ratelimit.forgot_password_limits(
            email=body.identifier, host=body.host, client_ip=body.client_ip
        )
    elif body.surface == ratelimit.MFA:
        keyed = ratelimit.mfa_limits(challenge=body.identifier, client_ip=body.client_ip)
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown rate-limit surface {body.surface!r}",
        )
    decision = await ratelimit.limiter.check(body.surface, keyed)
    return RateLimitOut(allowed=decision.allowed, retry_after_seconds=decision.retry_after)
