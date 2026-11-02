"""Issuer-keyed WorkOS environment registry for the shared API (project-per-tenant).

Each tenant is a WorkOS *project* with its own environment(s); the shared API therefore
receives tokens issued by many environments and must verify each against the environment
that issued it. This module holds an in-memory map of ``issuer -> environment config``,
loaded from ``auth.tenant_environments`` at startup and refreshed when a new environment is
registered. Token verification (``app.auth``) reads it on the hot path with **no** per-request
DB query. A cache miss on a populated registry means the token came from an environment we
do not serve; an *empty* registry means the API runs in the single-environment fallback used
for local dev / a control-plane environment.

See docs/identity-and-access.md §0 and docs/api.md.
"""

import asyncio
import logging
import time
from dataclasses import dataclass

from sqlalchemy import text

logger = logging.getLogger(__name__)

#: How long after a reload a MISS is answered from memory rather than re-reading the table. Bounds
#: the DB cost of garbage tokens (an unknown org on every request must not become a query per
#: request) while keeping a freshly registered firm's wait to one such interval at most.
MISS_RELOAD_INTERVAL_SECONDS = 15.0


@dataclass(frozen=True)
class EnvConfig:
    """The non-secret config needed to verify a token from one tenant environment."""

    tenant_id: int
    stage: str
    workos_client_id: str  # selects the JWKS (https://api.workos.com/sso/jwks/<client_id>)
    issuer: str  # the token `iss` this environment mints, and the value we enforce


_by_issuer: dict[str, EnvConfig] = {}
# WorkOS org id (the token's `org_id`) -> environment. The most robust key: it's the portal org
# the user authenticated into, stored per tenant, and independent of the token's issuer format
# (custom AuthKit domains change the issuer but never the org id).
_by_org: dict[str, EnvConfig] = {}
# Monotonic time of the last (attempted) reload, and the lock that makes a reload single-flight.
# `_last_reload` starts in the past so the very first miss after boot is allowed to reload.
_last_reload: float = float("-inf")
_reload_lock: asyncio.Lock | None = None


def default_issuer(workos_client_id: str) -> str:
    """The `iss` a WorkOS User Management environment mints by default — deterministic from the
    client id. Used when an environment doesn't store an explicit issuer, so tokens still verify
    without an operator hand-typing it. A **custom AuthKit domain** changes the host, so such
    environments must set `auth.tenant_environments.issuer` explicitly (it takes precedence)."""
    return f"https://api.workos.com/user_management/{workos_client_id}"


def resolve_issuer(issuer: str | None) -> EnvConfig | None:
    """The environment that mints this token issuer, or None."""
    if not issuer:
        return None
    return _by_issuer.get(issuer)


def resolve_org(org_id: str | None) -> EnvConfig | None:
    """The environment that owns this WorkOS org (the token's ``org_id``), or None. Preferred over
    issuer matching — it's independent of the token's issuer format (custom AuthKit domains)."""
    if not org_id:
        return None
    return _by_org.get(org_id)


def is_populated() -> bool:
    """True once at least one tenant environment is registered. When False the API falls
    back to the single configured environment (``WORKOS_CLIENT_ID``) for local dev."""
    return bool(_by_issuer or _by_org)


def _lookup(org_id: str | None, issuer: str | None) -> EnvConfig | None:
    return resolve_org(org_id) or resolve_issuer(issuer)


async def resolve(org_id: str | None, issuer: str | None) -> EnvConfig | None:
    """The environment a token belongs to, by org id then issuer — reloading the registry from
    the database on a miss.

    The map is per process. The API runs as one replica per site, and the admin call that
    registers a firm (and ``POST /admin/environments/refresh``) reaches only the replica that
    served it, so every other replica kept verifying that firm's tokens against the fallback
    environment's keys — ``Unable to find a signing key`` — until it was next restarted. The
    browser is load-balanced across sites, so a correct password and a correct TOTP code
    succeeded on one site and were refused on the others, which read as random sign-in failures.

    A miss is the one signal a replica gets that its copy may be stale, so it reloads then: at
    most once per ``MISS_RELOAD_INTERVAL_SECONDS`` (a stream of garbage tokens is not a query
    per request) and single-flight (a page load fanning out into dozens of calls is one reload,
    not dozens). A reload that fails is logged and answered from the current map, so a database
    blip degrades to today's behaviour rather than a 500. A hit never touches the database."""
    hit = _lookup(org_id, issuer)
    if hit is not None or (org_id is None and issuer is None):
        return hit
    global _reload_lock
    if _reload_lock is None:
        _reload_lock = asyncio.Lock()
    if _reload_lock.locked():
        # A reload is in flight for an earlier miss. Wait for it rather than answering from the
        # map it is about to replace — the first token of a burst must not be the only one served.
        async with _reload_lock:
            pass
        return _lookup(org_id, issuer)
    if time.monotonic() - _last_reload < MISS_RELOAD_INTERVAL_SECONDS:
        return None
    async with _reload_lock:
        try:
            count = await load()
            logger.info("Tenant environment registry reloaded on a miss: %d environment(s).", count)
        except Exception:  # noqa: BLE001 - verification must not 500 because the reload failed
            logger.exception("Tenant environment registry reload failed; using current map.")
    return _lookup(org_id, issuer)


async def load() -> int:
    """(Re)load the registry from ``auth.tenant_environments``; returns the count loaded. A row
    needs a ``workos_client_id`` to be usable; its ``issuer`` is derived from the client id when not
    stored (``default_issuer``), so environments verify without an operator setting it — only
    custom-AuthKit-domain environments need an explicit ``issuer``."""
    from app.db import system_transaction  # lazy: db imports auth, which imports this module

    # Stamped before the read, so a failing database is retried once per interval from the
    # miss path (``resolve``), not on every request that carries an unknown token.
    global _by_issuer, _by_org, _last_reload
    _last_reload = time.monotonic()
    async with system_transaction() as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT tenant_id, stage, workos_client_id, issuer, firm_org_id "
                        "FROM auth.tenant_environments "
                        "WHERE workos_client_id IS NOT NULL"
                    )
                )
            )
            .mappings()
            .all()
        )

    _by_issuer, _by_org = _build(rows)
    return len(_by_issuer)


# One-firm-org: the token's org_id is always the tenant's single firm org.
_ORG_COLUMNS = ("firm_org_id",)


def _build(rows: object) -> tuple[dict[str, EnvConfig], dict[str, EnvConfig]]:
    """Build (issuer -> EnvConfig, org_id -> EnvConfig) from tenant_environment rows, deriving a
    missing issuer from the client id. Pure (no DB) so it's unit-testable."""
    by_issuer: dict[str, EnvConfig] = {}
    by_org: dict[str, EnvConfig] = {}
    for r in rows:  # type: ignore[attr-defined]
        issuer = r["issuer"] or default_issuer(r["workos_client_id"])
        cfg = EnvConfig(
            tenant_id=r["tenant_id"],
            stage=r["stage"],
            workos_client_id=r["workos_client_id"],
            issuer=issuer,
        )
        by_issuer[issuer] = cfg
        for col in _ORG_COLUMNS:
            org_id = r.get(col) if hasattr(r, "get") else r[col]
            if org_id:
                by_org[org_id] = cfg
    return by_issuer, by_org


async def load_on_startup() -> None:
    """Best-effort load at boot -- never crashes startup. An unreachable DB or empty table
    just leaves the API in single-environment fallback."""
    try:
        count = await load()
        logger.info("Tenant environment registry loaded: %d environment(s).", count)
    except Exception:  # noqa: BLE001 - boot must not fail because the registry didn't load
        logger.exception("Tenant environment registry load failed; using fallback verification.")


# The admin action that registers a newly-created environment calls this after inserting the
# auth.tenant_environments row, so the shared API picks it up without a restart.
refresh = load
