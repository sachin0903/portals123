import asyncio
import logging
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app import tenant_registry, view_as
from app.config import settings
from app.permissions import SYSTEM, Permission, for_portal, satisfies

logger = logging.getLogger(__name__)


@lru_cache
def _jwks_client(client_id: str) -> jwt.PyJWKClient:
    # WorkOS publishes each environment's access-token signing keys here, keyed by client id.
    # Cached per client id so the shared API keeps one JWKS client per tenant environment.
    return jwt.PyJWKClient(f"https://api.workos.com/sso/jwks/{client_id}")


def _unverified_claims(token: str) -> dict[str, object]:
    """Read the token payload WITHOUT verifying the signature -- used only to pick which
    environment's keys to verify against (by ``org_id``, else ``iss``). The signature is always
    checked afterwards against that environment (never trusted on this read alone)."""
    try:
        return jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return {}


def _app_client_id(claims: dict[str, object]) -> str | None:
    """The WorkOS application (portal surface) the token was minted for. WorkOS sets the `client_id`
    claim to the current application; `aud` is a documented fallback (a string, or the first entry
    of a list). Used only to classify the `portal` claim (never to authorise), so an absent value is
    fine — resolve_claims falls back to org-based portal classification."""
    cid = claims.get("client_id")
    if isinstance(cid, str) and cid:
        return cid
    aud = claims.get("aud")
    if isinstance(aud, str) and aud:
        return aud
    if isinstance(aud, list) and aud and isinstance(aud[0], str):
        return aud[0]
    return None


class Principal(BaseModel):
    """The authenticated caller, derived from a verified WorkOS access token."""

    user_id: str
    organisation_id: str | None = None
    role: str | None = None
    permissions: list[str] = []
    session_id: str | None = None
    # The WorkOS APPLICATION the token was minted for (the `client_id` claim, `aud` fallback) — the
    # portal surface under project-per-tenant + application-per-portal (docs/auth-applications-
    # migration.md). Passed to auth.resolve_claims to classify the `portal` claim; None on tokens
    # without it (falls back to org-based portal classification). Not a privilege signal.
    client_id: str | None = None
    # The portal THIS session resolves to (`client` / `platform` / `operations`), from
    # ``auth.resolve_claims``. Same answer ``GET /me/portal`` used to be a second round-trip for.
    # None when the caller has no active membership for the application this token was minted
    # through. Portals refuse a confirmed mismatch; they stay fail-open when this never arrived.
    portal: str | None = None
    # The client this principal is acting as, for a staff view-as session (app/view_as.py).
    # `user_id` stays the member of staff throughout — this says whose portal they are looking
    # at, and it is what collapses the session's scope to that one client in the claim minter.
    view_as_contact_id: int | None = None
    # The `auth.view_as_sessions` row behind that, so the action trail can name the session.
    view_as_session_uid: str | None = None


#: The portal a request says it is. Each portal sets this on its calls to the API. It is a
#: tie-break, not a grant: ``auth.resolve_claims`` uses it only when the token's application
#: is stored on that portal and the login holds an active membership there.
PORTAL_HEADER = "x-graphene-portal"
_NAMED_PORTALS = frozenset({"client", "platform", "operations"})


def _named_portal(request: Request) -> str:
    """The portal header, or '' when it is absent or not one of the three portals."""
    raw = request.headers.get(PORTAL_HEADER, "").strip().lower()
    return raw if raw in _NAMED_PORTALS else ""


async def resolve_claims_cached(
    request: Request,
    session: AsyncSession,
    user_id: str,
    organisation_id: str | None,
    client_id: str | None = None,
    view_as_contact_id: int | None = None,
) -> dict[str, object]:
    """``auth.resolve_claims``, cached on ``request.state`` for one request's lifetime.

    ``get_current_principal`` and whichever session dependency follows it
    (``tenant_session``/``firm_session``/``admin_session``/``book_session`` and friends, in
    ``app/db.py``) each open their OWN DB connection -- but resolve the SAME verified identity.
    Without this, every authenticated request ran this non-trivial ``SECURITY DEFINER`` function
    (lineage aggregation, portal classification, contact/account scope, role_ids) TWICE, on two
    separate connections, for identical inputs -- exactly the "runs it twice" a reviewer would
    (and did) flag.

    Safe to reuse across connections: ``auth.resolve_claims`` is ``STABLE`` (no side effects), and
    a millisecond-old cached result within one request is no less consistent than the two separate
    reads it replaces -- which could, in principle, have observed two different snapshots had a
    write landed in the gap between them. Keyed on ``(user_id, organisation_id)`` because every
    real caller within one request passes the same pair (the same verified principal throughout).

    A view-as session (``view_as_contact_id`` set) goes to ``auth.resolve_view_as_claims``
    instead, which resolves this login's STAFF scope through the same minter and then narrows it
    to the one client (``app/view_as.py``).
    """
    # client_id (the token's application) is part of the key: it drives the portal classification,
    # so two calls for the same user/org but different apps are genuinely different resolutions.
    # So is the client being viewed as — a different one is a different session entirely. So is
    # the portal the caller named: the same token is platform on :5175 and operations on :5174.
    named = _named_portal(request)
    key = (user_id, organisation_id, client_id, view_as_contact_id, named)
    cached = getattr(request.state, "resolved_claims", None)
    if cached is not None and cached[0] == key:
        return cached[1]  # type: ignore[no-any-return]
    # Transaction-local, so it cannot leak onto the next request that borrows this connection.
    # An empty string clears a name set earlier in the same transaction. The function reads it
    # and ignores anything that is not a portal this token's application is actually stored on.
    await session.execute(
        sql_text("SELECT set_config('request.portal', :portal, true)"),
        {"portal": named},
    )
    query = (
        "SELECT auth.resolve_view_as_claims(:u, :o, :v)"
        if view_as_contact_id is not None
        else "SELECT auth.resolve_claims(:u, :o, :c)"
    )
    result = (
        await session.execute(
            sql_text(query),
            {"u": user_id, "o": organisation_id, "c": client_id, "v": view_as_contact_id},
        )
    ).scalar_one()
    request.state.resolved_claims = (key, result)
    return result  # type: ignore[no-any-return]


async def _permissions_for(
    request: Request,
    user_id: str,
    organisation_id: str | None,
    client_id: str | None = None,
) -> tuple[list[str], str | None]:
    """The caller's current permissions, read from the DB rather than trusted from the token --
    the tables joined here are the actual source of truth (a role's permissions are written
    directly by app/rbac.py::set_org_role_permissions; WorkOS's copy is a mirror, and WorkOS is no
    longer even given a copy to embed in the token -- see rbac.py's module docstring).

    Runs its own short-lived service_role session: no RLS context exists yet at this point
    (request.jwt.claims isn't published until db.py builds it FROM this principal), so this can't
    run as `authenticated` -- same reasoning as auth.resolve_claims itself.

    Calls ``resolve_claims_cached`` (rather than a single SQL function bundling resolve+join,
    which was the original shape) specifically so its ``role_ids`` gets cached on ``request.state``
    and reused by the session dependency that runs right after this one -- the expensive half of
    the work is `resolve_claims`, not the small `role_permissions` join that follows it here.

    The SQL function returns a role's whole grant set. ``permissions.for_portal`` then drops
    operations-only slugs (including ``system``) unless the resolved portal is operations, so a
    Graphene role attached to both memberships cannot make every firm ``can()`` true."""
    from app.db import SessionLocal  # local import: db.py imports Principal/build_jwt_claims

    async with SessionLocal() as session, session.begin():
        await session.execute(sql_text("SET LOCAL ROLE service_role"))
        resolved = await resolve_claims_cached(
            request, session, user_id, organisation_id, client_id
        )
        role_ids = resolved.get("role_ids") if resolved else None
        portal = resolved.get("portal") if isinstance(resolved, dict) else None
        portal_name = portal if isinstance(portal, str) else None
        if not isinstance(role_ids, list) or not role_ids:
            return [], portal_name
        result = await session.execute(
            sql_text("SELECT auth.member_permissions(:role_ids)"),
            {"role_ids": list(role_ids)},
        )
        slugs = list(result.scalar_one() or [])
        return for_portal(slugs, portal_name), portal_name


_bearer = HTTPBearer(auto_error=True)


# Stages the dev auth bypass may run in. A positive allowlist, not `!= 'production'`: a copied
# .env on a staging box must not be one variable away from a full auth bypass.
_DEV_BYPASS_STAGES = frozenset({"dev", "local", "test"})
# Peer addresses a dev token may arrive from. A container-network address is a real deployment.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
# Any of these means a proxy is in front of us, so a loopback peer says nothing about the caller.
_FORWARDING_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-real-ip", "forwarded")


def _request_hostname(request: Request) -> str:
    """The bare hostname the caller asked for (`Host` minus any port), lowercased."""
    host = request.headers.get("host", "").split(",")[0].strip().lower()
    if host.startswith("["):  # IPv6 literal, e.g. [::1]:8000
        return host[1 : host.index("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if ":" in host else host


def _dev_bypass_allowed(request: Request | None) -> tuple[bool, str]:
    """Whether an unverified `dev:` principal may be accepted, and why not if it may not.

    Four guards, all of which must hold: the explicit flag, a *stage from an allowlist* (not
    `!= 'production'`), a loopback peer, and a request that does not look proxied — no
    `X-Forwarded-*`/`Forwarded` header AND a `Host` naming loopback (a plain TCP/stream proxy
    rewrites neither, so the public hostname it forwards is what gives it away).

    Being honest about the limit: these are heuristics about the *shape* of the request, not
    proof of who the caller is. A reverse proxy configured to forward to 127.0.0.1 while
    rewriting `Host` to localhost and setting no forwarding headers (nginx `proxy_pass` with
    `proxy_set_header` omitted) would still satisfy all four. The real protections are that
    `DEMO_AUTH_BYPASS` defaults off and `APP_STAGE` must be a development stage; these guards
    exist so that a *copied* dev environment file cannot on its own open a hole, and so that a
    deployment run behind `--proxy-headers` fails closed.
    """
    if not settings.demo_auth_bypass:
        return False, "DEMO_AUTH_BYPASS is off"
    if settings.app_stage not in _DEV_BYPASS_STAGES:
        return False, f"APP_STAGE={settings.app_stage!r} is not a development stage"
    if request is None:
        return False, "no request context to check the caller's address"
    host = request.client.host if request.client else None
    if host not in _LOOPBACK_HOSTS:
        return False, f"caller {host!r} is not loopback"
    present = [h for h in _FORWARDING_HEADERS if h in request.headers]
    if present:
        return False, f"proxied request (headers: {', '.join(present)})"
    hostname = _request_hostname(request)
    if hostname not in _LOOPBACK_HOSTS:
        return False, f"request Host {hostname!r} is not loopback (a proxy is forwarding to us)"
    return True, ""


def _issue_principal(request: Request, principal: Principal) -> Principal:
    """Finalise the request principal: stash it in
    ``request.state`` (i.e. ``scope["state"]``, the dict the whole request shares) so the
    action-event middleware (``app/audit_actions.py``) can attribute the request AFTER the
    response — middleware cannot see dependency return values, but it holds the same scope dict."""
    request.state.principal = principal
    return principal


async def get_current_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> Principal:
    """Verify the WorkOS access token (JWT) and extract the principal.

    The frontend holds the sealed session; it sends the access token as a
    bearer on each API call. We verify the signature against WorkOS's JWKS
    and trust its identity claims (``sub``/``org_id``/``sid``) -- but not ``permissions``,
    which is read fresh from the DB on every call (``_permissions_for`` /
    ``auth.member_permissions``) rather than from whatever WorkOS embedded in the token, then
    scoped to the resolved portal (``permissions.for_portal``) so a Graphene role's ``system``
    grant cannot bypass firm gates on the platform portal. The DB tables are the actual source
    of truth: a role's permissions are written there directly by ``rbac.set_org_role_permissions``,
    and WorkOS's own copy (used only so its dashboard can show it) is a mirror, not the origin.
    This also means a permission change takes effect on the very next request, with no
    refresh/re-login delay.

    Shared API / project-per-tenant (docs/identity-and-access.md §0): a token may come
    from any tenant environment, so we read its ``iss`` (unverified) to pick the matching
    environment from the registry, then verify against THAT environment's keys and enforce
    its issuer. An empty registry falls back to the single configured environment (local
    dev / control-plane); a populated registry with an unrecognised issuer is rejected.
    """
    token = credentials.credentials

    # A view-as session (app/view_as.py): a member of staff working inside the client portal as
    # one of their clients. The credential is an opaque token resolved against
    # `auth.view_as_sessions`, not a WorkOS JWT, so it is routed before the JWKS path.
    #
    # The principal that comes out is deliberately modest. `user_id` is the MEMBER OF STAFF, so
    # every audit row names them rather than the client. `portal` is `client`, because the client
    # portal is where they are and its endpoints must behave the way they do for a client.
    # `permissions` is empty, so no staff authority travels into that portal: every
    # `require_permission` gate refuses, and `auth.can_read_all()` is false. What remains is the
    # one client, applied by the claim minter (`auth.resolve_view_as_claims`).
    #
    # And the session READS. `view_as.permits` refuses every method that could change anything,
    # here rather than per endpoint, so the restriction cannot be missed by a route added later.
    if token.startswith(view_as.TOKEN_PREFIX):
        if not view_as.permits(request.method, request.url.path):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You cannot take this action on behalf of the client.",
            )
        from app.db import SessionLocal  # local import: db.py imports from this module

        async with SessionLocal() as session, session.begin():
            await session.execute(sql_text("SET LOCAL ROLE service_role"))
            live = await view_as.resolve(session, token)
        if live is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="This view-as session has ended",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return _issue_principal(
            request,
            Principal(
                user_id=live.adviser_sub,
                # Not a WorkOS session, so there is no organisation on it; the session
                # dependencies in db.py accept that for a view-as principal and resolve the
                # tenant from the adviser's identity instead.
                organisation_id=None,
                portal="client",
                role="client",
                permissions=[],
                view_as_contact_id=live.contact_id,
                view_as_session_uid=live.uid,
            ),
        )

    # DEV-ONLY bypass (mirrors the portals' hooks.server.ts): accept a `dev:<base64url-json>`
    # token and build the principal from it, skipping JWKS verification. Triple-guarded by
    # `_dev_bypass_allowed` (flag + development stage + loopback, unproxied caller) so neither a
    # prod deploy with the flag flipped nor a copied dev `.env` on a staging box opens a hole.
    if token.startswith("dev:"):
        allowed, refusal = _dev_bypass_allowed(request)
        if not allowed:
            # Loud: someone is presenting a bypass token at a deployment that refuses it.
            logger.warning("Refused a `dev:` bearer token -- %s.", refusal)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired access token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        import base64
        import json

        raw = token[4:]
        raw += "=" * (-len(raw) % 4)  # restore base64 padding stripped by base64url
        try:
            payload = json.loads(base64.urlsafe_b64decode(raw))
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid dev token"
            ) from exc
        logger.warning(
            "DEV AUTH BYPASS: accepting an unverified dev principal from %s (stage=%s). "
            "This must never be enabled outside a developer's own machine.",
            request.client.host if request.client else "unknown",
            settings.app_stage,
        )
        return _issue_principal(
            request,
            Principal(
                user_id=str(payload["sub"]),
                organisation_id=payload.get("org_id"),
                role=payload.get("role"),
                permissions=payload.get("permissions", []),
                session_id=payload.get("sid"),
                client_id=_app_client_id(payload),
                portal=payload.get("portal"),
            ),
        )

    unverified = _unverified_claims(token)
    issuer_claim = unverified.get("iss")
    org_claim = unverified.get("org_id")
    issuer_str = issuer_claim if isinstance(issuer_claim, str) else None
    org_str = org_claim if isinstance(org_claim, str) else None
    # Prefer the org id (robust: independent of the token's issuer format), then the issuer. A
    # miss reloads the registry from the database (throttled) before answering: this process may
    # have booted before the firm was registered, and the admin refresh only reaches one replica.
    env = await tenant_registry.resolve(org_str, issuer_str)

    if env is not None:
        client_id: str = env.workos_client_id
        # Pin the issuer only when it matches what we hold (an iss-based match); when resolved by
        # org the real issuer may be a custom AuthKit domain we didn't derive, so rely on the
        # client-scoped JWKS signature check (the real gate) rather than reject a valid token.
        expected_issuer: str | None = issuer_str if issuer_str == env.issuer else None
    else:
        # Not in the registry (or absent): fall back to the configured environment (the
        # Ops/control-plane env, or local dev). Safe because the JWKS signature check below is the
        # real gate — a token not signed by this env's keys fails. So registering tenant
        # environments never locks out the fallback env.
        client_id = settings.workos_client_id
        expected_issuer = settings.workos_issuer or None

    if not client_id:
        logger.error(
            "No WorkOS client id to verify the token (registry empty and WORKOS_CLIENT_ID unset)."
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Auth not configured",
        )
    try:
        # PyJWT's JWKS client is synchronous and performs HTTP on a cache miss. Keep that network
        # wait off uvicorn's event loop just like the async WorkOS management client below it.
        signing_key = await asyncio.to_thread(
            _jwks_client(client_id).get_signing_key_from_jwt, token
        )
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            # `require` makes a token missing these claims a 401 (PyJWTError),
            # not a downstream KeyError/500. `issuer`, when known, is enforced.
            issuer=expected_issuer,
            # A small `exp` leeway (clock skew + the refresh boundary): the WorkOS SDK on the
            # SvelteKit side may still treat a just-expired token as valid and not refresh it, so
            # without leeway the API would 401 a token the caller believes is live — the
            # intermittent "invalid or expired" that works on the next request. 60s is well within
            # the token's short lifetime; a truly stale token (past leeway) is still rejected and
            # the session-layer refresh kicks in.
            leeway=60,
            options={"verify_aud": False, "require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as exc:
        logger.warning("Access token verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    org_id = claims.get("org_id")
    app_cid = _app_client_id(claims)
    permissions, portal = await _permissions_for(
        request, claims["sub"], org_id if isinstance(org_id, str) else None, app_cid
    )
    return _issue_principal(
        request,
        Principal(
            user_id=claims["sub"],
            organisation_id=claims.get("org_id"),
            role=claims.get("role"),
            permissions=permissions,
            session_id=claims.get("sid"),
            client_id=app_cid,
            portal=portal,
        ),
    )


def is_system(principal: Principal) -> bool:
    """Graphene platform staff: the `system` permission on the verified token.

    ONE source of truth, and it is a *permission*, so it agrees with ``auth.is_system()`` in
    SQL (which reads the same ``permissions`` claim via ``build_jwt_claims``):

    * The `role` claim is deliberately NOT consulted. It is a free-text name chosen inside a
      tenant's own WorkOS project, so honouring ``role in ('system', 'admin')`` handed
      cross-tenant superuser to any firm that happened to call a role "admin".
    * There is no allowlist and no break-glass: the ONLY way to be system is an operations role
      carrying the `system` permission (DB-sourced per request, and only on an operations-portal
      session — ``for_portal`` strips it from a firm-portal principal).
    """
    return SYSTEM.slug in principal.permissions


def get_system_principal(principal: Principal = Depends(get_current_principal)) -> Principal:
    """Guard for the cross-tenant admin surface: only Graphene system principals — an
    operations role carrying the `system` permission (DB-sourced). The admin API is additionally
    network-locked at the edge (see docs/project-context.md)."""
    if not is_system(principal):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="System role required",
        )
    return principal


def require_permission(principal: Principal, permission: Permission | str) -> None:
    """Authorise an action: the caller's verified token must carry `permission` (or a Graphene
    system principal, or the matching ``:write`` when a ``:read`` is required — write implies read).
    Accepts a `Permission` constant from `app.permissions` or a raw slug. Raises 403 otherwise.
    Used across the /firm and /admin surfaces."""
    slug = permission.slug if isinstance(permission, Permission) else permission
    if is_system(principal) or satisfies(set(principal.permissions), slug):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Requires the '{slug}' permission",
    )


def require_any_permission(principal: Principal, *permissions: Permission | str) -> None:
    """Authorise when the caller holds ANY of `permissions` (system bypasses; write implies read).
    For endpoints reachable from more than one capability — e.g. listing roles serves both the
    role editor (roles:read) and the member editor's role picker (members:read)."""
    held = set(principal.permissions)
    if is_system(principal):
        return
    for permission in permissions:
        slug = permission.slug if isinstance(permission, Permission) else permission
        if satisfies(held, slug):
            return
    wanted = ", ".join(p.slug if isinstance(p, Permission) else p for p in permissions)
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Requires one of: {wanted}",
    )


# ---- the `role` claim ----------------------------------------------------------------------
# `role` is a FREE-TEXT role name chosen inside a tenant's own WorkOS project and is DISPLAY-ONLY.
# No SQL branch grants OR denies on it any more: 0034 removed the `role IN ('system','admin')`
# superuser grant, and 0107 removed both `auth.has_role()` and the `auth.role() = 'client'` deny
# belt (the portal claim is the signal). Privilege travels in `permissions`; the portal in
# `portal`. The old namespacing of privileged names is gone with the branches it defended.


def build_jwt_claims(principal: Principal) -> dict[str, object]:
    """The JWT-derived half of the RLS claims contract (docs/identity-and-access.md
    section 6). The internal half (``user_uuid``/``tenant_id``/``tenant_ids``/
    ``contact_ids``/``account_ids``) is resolved in the DB by ``auth.resolve_claims``;
    ``db.py::tenant_session`` merges the two into ``request.jwt.claims``.

    ``permissions`` is whatever ``get_current_principal`` already resolved from the DB
    (``auth.member_permissions``, not the JWT claim -- see its docstring), so ``auth.is_system()``
    in SQL always agrees with ``is_system`` in Python. This function itself does no DB work; it
    just republishes whatever ``principal.permissions`` already holds by the time it's called.

    ``role`` is published verbatim (blank ⇒ ``anon``): it is display-only — no SQL branch grants
    or denies on it (0034 + 0107). Privilege travels in ``permissions``; the portal in ``portal``.
    """
    claims: dict[str, object] = {
        "sub": principal.user_id,
        "role": (principal.role or "").strip() or "anon",
        "permissions": principal.permissions,
    }
    if principal.organisation_id is not None:
        claims["org_id"] = principal.organisation_id
    if principal.session_id is not None:
        claims["sid"] = principal.session_id
    return claims
