"""Tests for WorkOS access-token verification (app.auth.get_current_principal).

We sign tokens locally with an RSA keypair and stub the JWKS lookup, so these
run offline and assert the security-critical behaviour: signature, algorithm,
expiry, required claims, and optional issuer pinning.
"""

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials

from app import auth
from app.config import settings

CLIENT_ID = "client_test123"
ISSUER = f"https://api.workos.com/user_management/{CLIENT_ID}"


@pytest.fixture
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(autouse=True)
def _configure(monkeypatch: pytest.MonkeyPatch, signing_key: rsa.RSAPrivateKey) -> None:
    """Point the API at a known client id and stub the JWKS lookup to our key."""
    monkeypatch.setattr(settings, "workos_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "workos_issuer", "")

    public_key = signing_key.public_key()

    class _Key:
        key = public_key

    class _JWKS:
        def get_signing_key_from_jwt(self, token: str) -> _Key:
            return _Key()

    # _jwks_client now takes the environment's client id; the stub ignores it and returns
    # our test key regardless, so verification exercises the same public key.
    monkeypatch.setattr(auth, "_jwks_client", lambda client_id: _JWKS())
    # Permissions are now DB-sourced (auth.member_permissions), not read from the token's
    # `permissions` claim — this file tests JWT verification, not that DB lookup, so default to
    # `[]` (no real DB connection) and let individual tests override with `_stub_permissions`
    # when what they're actually testing IS the resulting permission set.
    _stub_permissions(monkeypatch, [])


def _stub_permissions(monkeypatch: pytest.MonkeyPatch, permissions: list[str]) -> None:
    async def _stub(
        request: Request,
        user_id: str,
        organisation_id: str | None,
        client_id: str | None = None,
    ) -> tuple[list[str], str | None]:
        return permissions, None

    monkeypatch.setattr(auth, "_permissions_for", _stub)


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _request(client: str = "203.0.113.7", host: str = "localhost:8000", **headers: str) -> Request:
    """A minimal ASGI request. The caller's address and headers matter only to the dev-auth
    bypass; the default is a routable (non-loopback) address, i.e. a real deployment, asking for
    a loopback `Host` (what a developer's own browser/SSR fetch sends)."""
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "query_string": b"",
            "scheme": "http",
            "headers": [
                (k.replace("_", "-").encode(), v.encode())
                for k, v in {"host": host, **headers}.items()
            ],
            "client": (client, 54321),
        }
    )


def _token(key: rsa.RSAPrivateKey, **overrides: Any) -> str:
    claims: dict[str, Any] = {
        "sub": "user_01",
        "org_id": "org_01",
        "role": "admin",
        "permissions": ["widgets:read"],
        "sid": "session_01",
        "exp": datetime.now(UTC) + timedelta(minutes=5),
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256")


async def test_valid_token_yields_principal(signing_key: rsa.RSAPrivateKey) -> None:
    principal = await auth.get_current_principal(
        _request(), credentials=_creds(_token(signing_key))
    )
    assert principal.user_id == "user_01"
    assert principal.organisation_id == "org_01"
    assert principal.role == "admin"
    # Permissions are DB-sourced now (stubbed to `[]` by the autouse fixture), NOT the token's
    # own `permissions` claim — proving the claim is ignored is the point here.
    assert principal.permissions == []
    assert principal.session_id == "session_01"


async def test_expired_token_rejected(signing_key: rsa.RSAPrivateKey) -> None:
    # Expired well past the 60s leeway — unambiguously rejected.
    token = _token(signing_key, exp=datetime.now(UTC) - timedelta(minutes=5))
    with pytest.raises(HTTPException) as exc:
        await auth.get_current_principal(_request(), credentials=_creds(token))
    assert exc.value.status_code == 401


async def test_recently_expired_token_within_leeway_accepted(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    # A token just past exp (within the 60s leeway) is still accepted — this is the fix for the
    # intermittent "invalid or expired" that worked on the next attempt (SDK/PyJWT boundary + skew).
    token = _token(signing_key, exp=datetime.now(UTC) - timedelta(seconds=10))
    principal = await auth.get_current_principal(_request(), credentials=_creds(token))
    assert principal.user_id == "user_01"


async def test_token_missing_sub_is_401_not_500(signing_key: rsa.RSAPrivateKey) -> None:
    # `require=["sub"]` turns this into a clean 401 instead of a downstream KeyError/500.
    claims = {"org_id": "org_01", "exp": datetime.now(UTC) + timedelta(minutes=5)}
    token = jwt.encode(claims, signing_key, algorithm="RS256")
    with pytest.raises(HTTPException) as exc:
        await auth.get_current_principal(_request(), credentials=_creds(token))
    assert exc.value.status_code == 401


async def test_token_missing_exp_rejected(signing_key: rsa.RSAPrivateKey) -> None:
    token = jwt.encode({"sub": "user_01"}, signing_key, algorithm="RS256")
    with pytest.raises(HTTPException) as exc:
        await auth.get_current_principal(_request(), credentials=_creds(token))
    assert exc.value.status_code == 401


async def test_wrong_signing_key_rejected(signing_key: rsa.RSAPrivateKey) -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(HTTPException) as exc:
        await auth.get_current_principal(_request(), credentials=_creds(_token(other)))
    assert exc.value.status_code == 401


async def test_non_rs256_algorithm_rejected(signing_key: rsa.RSAPrivateKey) -> None:
    # The verifier allows only RS256, so an HS256 token is rejected — this closes
    # the algorithm-confusion class of attacks (e.g. HS256 with the public key).
    claims = {"sub": "user_01", "exp": datetime.now(UTC) + timedelta(minutes=5)}
    token = jwt.encode(claims, "unit-test-hmac-secret-at-least-32-bytes-long", algorithm="HS256")
    with pytest.raises(HTTPException) as exc:
        await auth.get_current_principal(_request(), credentials=_creds(token))
    assert exc.value.status_code == 401


async def test_issuer_enforced_when_configured(
    signing_key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "workos_issuer", ISSUER)
    good = await auth.get_current_principal(
        _request(), credentials=_creds(_token(signing_key, iss=ISSUER))
    )
    assert good.user_id == "user_01"

    bad = _token(signing_key, iss="https://evil.example.com")
    with pytest.raises(HTTPException) as exc:
        await auth.get_current_principal(_request(), credentials=_creds(bad))
    assert exc.value.status_code == 401


async def test_unconfigured_client_id_is_500(
    signing_key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "workos_client_id", "")
    with pytest.raises(HTTPException) as exc:
        await auth.get_current_principal(_request(), credentials=_creds(_token(signing_key)))
    assert exc.value.status_code == 500


# ---- shared API / project-per-tenant: issuer-keyed environment selection ----

TENANT_ISSUER = "https://api.workos.com/user_management/client_tenantA"


def _register_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import tenant_registry

    env = tenant_registry.EnvConfig(
        tenant_id=1, stage="production", workos_client_id="client_tenantA", issuer=TENANT_ISSUER
    )
    monkeypatch.setattr(tenant_registry, "_by_issuer", {TENANT_ISSUER: env})


def test_registry_builds_issuer_and_org_maps() -> None:
    """A row with no explicit issuer registers under the derived WorkOS issuer, an explicit issuer
    (custom AuthKit domain) is kept, and the tenant's ONE firm org id (0108: the per-portal org
    columns are gone) maps to its environment so tokens resolve by org id regardless of issuer
    format."""
    from app import tenant_registry

    rows = [
        {
            "tenant_id": 7,
            "stage": "production",
            "workos_client_id": "client_xyz",
            "issuer": None,
            "firm_org_id": "org_firm7",
        },
        {
            "tenant_id": 8,
            "stage": "production",
            "workos_client_id": "client_abc",
            "issuer": "https://custom.example/iss",
            "firm_org_id": None,  # firm org not minted yet — issuer still registers
        },
    ]
    by_issuer, by_org = tenant_registry._build(rows)
    derived = tenant_registry.default_issuer("client_xyz")
    assert derived == "https://api.workos.com/user_management/client_xyz"
    assert by_issuer[derived].workos_client_id == "client_xyz"
    assert by_issuer["https://custom.example/iss"].workos_client_id == "client_abc"
    # The firm org id resolves to the right client — the robust path used for verification.
    assert by_org["org_firm7"].workos_client_id == "client_xyz"
    assert "org_firm8" not in by_org  # an org-less row registers by issuer only


async def test_token_verified_by_org_id_when_issuer_unknown(
    signing_key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The org id selects the environment even when the token's issuer is a custom domain we don't
    recognise — the robust path that fixes per-tenant projects with custom AuthKit domains."""
    from app import tenant_registry

    env = tenant_registry.EnvConfig(
        tenant_id=1,
        stage="production",
        workos_client_id="client_tenantA",
        issuer="https://custom.example/iss",
    )
    monkeypatch.setattr(tenant_registry, "_by_org", {"org_01": env})
    monkeypatch.setattr(tenant_registry, "_by_issuer", {})
    monkeypatch.setattr(settings, "workos_client_id", "")  # no fallback — must resolve by org
    principal = await auth.get_current_principal(
        _request(),
        credentials=_creds(_token(signing_key, iss="https://unrecognised.example/whatever")),
    )
    assert principal.user_id == "user_01"


async def test_token_verified_against_registered_environment(
    signing_key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A token whose issuer is a registered environment verifies against that environment
    -- even with no single WORKOS_CLIENT_ID configured (the registry drives selection)."""
    _register_env(monkeypatch)
    monkeypatch.setattr(settings, "workos_client_id", "")  # prove the registry, not the fallback
    principal = await auth.get_current_principal(
        _request(),
        credentials=_creds(_token(signing_key, iss=TENANT_ISSUER)),
    )
    assert principal.user_id == "user_01"


async def test_unknown_issuer_falls_back_to_configured_env(
    signing_key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A token whose issuer isn't a registered environment falls back to the configured
    (Ops / control-plane) env — the signature check is the gate, so registering tenant
    environments never locks out the fallback env. The miss first asks the database whether the
    map is stale (this replica may have booted before the firm was registered); only a miss that
    survives the reload falls back."""
    from app import tenant_registry

    _register_env(monkeypatch)  # registry populated with a DIFFERENT env
    reloads: list[int] = []

    async def fake_load() -> int:
        reloads.append(1)
        return 1

    monkeypatch.setattr(tenant_registry, "load", fake_load)
    monkeypatch.setattr(tenant_registry, "_last_reload", float("-inf"))
    principal = await auth.get_current_principal(
        _request(),
        credentials=_creds(_token(signing_key, iss="https://api.workos.com/user_management/other")),
    )
    assert principal.user_id == "user_01"
    assert reloads == [1]


# ---- the dev auth bypass: flag + development stage + loopback, unproxied caller ----
#
# `apps/api/.env.example` ships APP_STAGE=dev, so a copied env file on a shared box used to be
# one variable away from accepting an unverified principal with arbitrary permissions. These
# pin all three guards.

DEV_TOKEN = "dev:" + base64.urlsafe_b64encode(
    json.dumps({"sub": "user_dev", "permissions": ["system"]}).encode()
).decode().rstrip("=")


@pytest.fixture
def _dev_bypass_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "demo_auth_bypass", True)
    monkeypatch.setattr(settings, "app_stage", "dev")


@pytest.mark.usefixtures("_dev_bypass_on")
async def test_dev_token_accepted_from_loopback_on_a_dev_stage() -> None:
    principal = await auth.get_current_principal(
        _request("127.0.0.1"), credentials=_creds(DEV_TOKEN)
    )
    assert principal.user_id == "user_dev"
    assert principal.permissions == ["system"]


@pytest.mark.usefixtures("_dev_bypass_on")
async def test_dev_token_rejected_from_a_remote_caller() -> None:
    """Loopback only: the flag being on inside a container must not let anything on the network
    mint its own principal."""
    with pytest.raises(HTTPException) as exc:
        await auth.get_current_principal(_request("10.0.3.14"), credentials=_creds(DEV_TOKEN))
    assert exc.value.status_code == 401


@pytest.mark.usefixtures("_dev_bypass_on")
async def test_dev_token_rejected_when_proxied() -> None:
    """A loopback peer behind a reverse proxy says nothing about who the caller is."""
    with pytest.raises(HTTPException) as exc:
        await auth.get_current_principal(
            _request("127.0.0.1", x_forwarded_for="198.51.100.9"), credentials=_creds(DEV_TOKEN)
        )
    assert exc.value.status_code == 401


@pytest.mark.usefixtures("_dev_bypass_on")
async def test_dev_token_rejected_when_the_host_is_not_loopback() -> None:
    """A plain TCP/stream proxy forwards to 127.0.0.1 and sets no `X-Forwarded-*`, so the peer
    address alone said "developer's own machine" when it was really the public internet. What it
    does NOT rewrite is `Host`, so a non-loopback Host is refused."""
    with pytest.raises(HTTPException) as exc:
        await auth.get_current_principal(
            _request("127.0.0.1", host="api.example.com"), credentials=_creds(DEV_TOKEN)
        )
    assert exc.value.status_code == 401


@pytest.mark.usefixtures("_dev_bypass_on")
async def test_dev_token_accepted_for_ipv6_and_bare_loopback_hosts() -> None:
    """The Host check must not break the ordinary local shapes: `127.0.0.1:8000`, `[::1]:8000`
    and a portless `localhost`."""
    for host in ("127.0.0.1:8000", "[::1]:8000", "localhost"):
        principal = await auth.get_current_principal(
            _request("127.0.0.1", host=host), credentials=_creds(DEV_TOKEN)
        )
        assert principal.user_id == "user_dev"


async def test_dev_token_rejected_on_staging(monkeypatch: pytest.MonkeyPatch) -> None:
    """`!= 'production'` was not enough: a staging deployment with a copied dev .env refused
    nothing. The stage is now a positive allowlist."""
    monkeypatch.setattr(settings, "demo_auth_bypass", True)
    monkeypatch.setattr(settings, "app_stage", "staging")
    with pytest.raises(HTTPException) as exc:
        await auth.get_current_principal(_request("127.0.0.1"), credentials=_creds(DEV_TOKEN))
    assert exc.value.status_code == 401


async def test_dev_token_rejected_when_flag_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "demo_auth_bypass", False)
    monkeypatch.setattr(settings, "app_stage", "dev")
    with pytest.raises(HTTPException) as exc:
        await auth.get_current_principal(_request("127.0.0.1"), credentials=_creds(DEV_TOKEN))
    assert exc.value.status_code == 401


# ---- is_system: one source of truth, and it agrees with SQL ----


async def test_workos_role_named_admin_is_not_a_superuser(signing_key: rsa.RSAPrivateKey) -> None:
    """`role` is a free-text name inside a tenant's own WorkOS project. A firm calling one of
    its roles "admin" must not become a cross-tenant superuser."""
    for role in ("admin", "system", "owner"):
        principal = await auth.get_current_principal(
            _request(), credentials=_creds(_token(signing_key, role=role, permissions=[]))
        )
        assert principal.role == role  # still reported...
        assert not auth.is_system(principal)  # ...but it grants nothing
        with pytest.raises(HTTPException):
            auth.get_system_principal(principal=principal)


async def test_system_permission_is_what_grants_system(
    signing_key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The token's own `permissions` claim is ignored now — stub the DB read instead of passing
    # `permissions=["system"]` on the token, since that's what a real request would see.
    _stub_permissions(monkeypatch, ["system"])
    principal = await auth.get_current_principal(
        _request(), credentials=_creds(_token(signing_key, role="adviser"))
    )
    assert auth.is_system(principal)


async def test_there_is_no_break_glass_any_more(
    signing_key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SYSTEM_USER_IDS break-glass is DELETED: the only way to be system is an operations
    role carrying the `system` permission (DB-sourced). Pin the absence — the setting, the
    applier and the principal flag are all gone."""
    assert not hasattr(settings, "system_user_ids")
    assert not hasattr(auth, "_apply_break_glass")
    assert "break_glass" not in auth.Principal.model_fields


async def test_non_allowlisted_user_gets_nothing_extra(
    signing_key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_permissions(monkeypatch, ["contacts:read"])
    principal = await auth.get_current_principal(
        _request(), credentials=_creds(_token(signing_key))
    )
    assert principal.permissions == ["contacts:read"]
    assert not auth.is_system(principal)


# ---- the `role` claim published to Postgres ------------------------------------------------
#
# The role claim is DISPLAY-ONLY now: 0034 removed the `role IN ('system','admin')` superuser
# branch, and 0107 dropped `auth.has_role()` and the `auth.role() = 'client'` deny belt (the
# `portal` claim is the signal). With no SQL reader left, the old `role_claim` namespacing
# (`admin` → `tenant:admin`) is gone with the branches it defended: `build_jwt_claims` publishes
# the role verbatim (blank ⇒ 'anon'), and privilege travels ONLY in `permissions`.
#
# `tests/test_permissions.py::test_role_named_admin_is_not_system_under_rls` executes
# auth.is_system() in a real Postgres against exactly these claims.


async def test_build_jwt_claims_publishes_the_role_verbatim(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    """End to end from the token: a tenant-named "admin" role reaches `request.jwt.claims`
    unmodified — it is display-only — and carries no permissions, so neither Python nor SQL
    treats it as system."""
    principal = await auth.get_current_principal(
        _request(), credentials=_creds(_token(signing_key, role="admin", permissions=[]))
    )
    claims = auth.build_jwt_claims(principal)
    assert claims["role"] == "admin"  # verbatim: no SQL branch reads it any more
    assert claims["permissions"] == []
    assert not auth.is_system(principal)


def test_build_jwt_claims_defaults_a_blank_role_to_anon() -> None:
    assert auth.build_jwt_claims(auth.Principal(user_id="u"))["role"] == "anon"
    assert auth.build_jwt_claims(auth.Principal(user_id="u", role="   "))["role"] == "anon"
    assert auth.build_jwt_claims(auth.Principal(user_id="u", role="adviser"))["role"] == "adviser"


async def test_build_jwt_claims_keeps_the_system_permission_when_it_is_real(
    signing_key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Privilege travels in `permissions`: a genuine system principal reaches SQL as one, and its
    role name rides along as plain display text."""
    _stub_permissions(monkeypatch, ["system"])
    principal = await auth.get_current_principal(
        _request(),
        credentials=_creds(_token(signing_key, role="admin")),
    )
    claims = auth.build_jwt_claims(principal)
    assert claims["role"] == "admin"
    assert "system" in claims["permissions"]  # type: ignore[operator]
    assert auth.is_system(principal)


# ---- resolve_claims_cached: one resolve_claims call per request, not one per session dep ----
#
# get_current_principal (via _permissions_for) and whichever session dependency runs right after
# it (tenant_session/firm_session/admin_session/book_session, app/db.py) both need
# auth.resolve_claims's result for the SAME verified identity. Without this cache, a single
# request ran that non-trivial SECURITY DEFINER function twice, on two separate connections --
# a PR reviewer caught this. These pin the caching contract directly, without a real DB: what
# matters is call COUNT, which is easiest to see against a fake session.


class _FakeResolveClaimsResult:
    def __init__(self, value: dict[str, object]) -> None:
        self._value = value

    def scalar_one(self) -> dict[str, object]:
        return self._value


class _FakeSession:
    """Counts `execute` calls; always answers with the same canned resolve_claims payload."""

    def __init__(self, value: dict[str, object]) -> None:
        self.value = value
        self.calls = 0

    async def execute(self, *_args: object, **_kwargs: object) -> _FakeResolveClaimsResult:
        self.calls += 1
        return _FakeResolveClaimsResult(self.value)


_RESOLVED = {"tenant_id": 1, "role_ids": [7]}


async def test_resolve_claims_cached_reuses_the_result_for_the_same_request_and_key() -> None:
    session = _FakeSession(_RESOLVED)
    request = _request()
    first = await auth.resolve_claims_cached(request, session, "wos_1", "org_1")  # type: ignore[arg-type]
    second = await auth.resolve_claims_cached(request, session, "wos_1", "org_1")  # type: ignore[arg-type]
    assert first == second == _RESOLVED
    # One resolution publishes the portal name and then resolves. The second call was a cache
    # hit, so it did neither.
    assert session.calls == 2


async def test_resolve_claims_cached_misses_for_a_different_identity() -> None:
    session = _FakeSession(_RESOLVED)
    request = _request()
    await auth.resolve_claims_cached(request, session, "wos_1", "org_1")  # type: ignore[arg-type]
    await auth.resolve_claims_cached(request, session, "wos_2", "org_1")  # type: ignore[arg-type]
    assert session.calls == 4  # each identity publishes the portal name, then resolves


async def test_resolve_claims_cached_does_not_leak_across_requests() -> None:
    """The cache lives on `request.state`, so it must not survive past the one request —
    otherwise a later, unrelated request could see a stale answer for a coincidentally-reused
    key, or (worse) one principal's resolved claims leaking onto another's request object if
    they were ever accidentally shared."""
    session = _FakeSession(_RESOLVED)
    await auth.resolve_claims_cached(_request(), session, "wos_1", "org_1")  # type: ignore[arg-type]
    await auth.resolve_claims_cached(_request(), session, "wos_1", "org_1")  # type: ignore[arg-type]
    assert session.calls == 4  # a fresh Request each time -- no cache to hit
