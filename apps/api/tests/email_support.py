"""Shared scaffolding for the email-template suites (a helper module, like ``tests/logcapture.py``).

Most of these suites need the same firm — a tenant, one platform-portal staffer, the membership —
and the same two session shapes: ``authenticated`` + claims for the firm router
(``db.py::tenant_session``) and ``service_role`` + claims for the admin door
(``db.py::admin_session``). Kept in one place so a change to the real session contract lands once.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.auth import Principal

WRITE = ["email_templates:write"]


def make_firm(
    super_engine: Engine,
    *,
    prefix: str = "e",
    name: str = "EFirm",
    first_name: str = "Cara",
    last_name: str = "Staff",
    email_domain: str = "e.test",
    theme: dict[str, Any] | None = None,
    with_operator: bool = False,
) -> dict[str, str]:
    """A tenant with one platform-portal staff member.

    ``with_operator`` adds a Graphene operator holding NO membership in that tenant — the reason
    authorship cannot be derived from the author's directory position later.
    """
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name, theme) "
                    "VALUES (:uid, :alias, :name, CAST(:theme AS jsonb)) RETURNING id"
                ),
                {
                    "uid": f"{prefix}ten_{s}",
                    "alias": f"{prefix}firm-{s}",
                    "name": f"{name} {s}",
                    "theme": json.dumps(theme) if theme else None,
                },
            ).scalar_one()
        )
        # The login IS the tenant now (0107): resolve_claims reads only auth.users.tenant_id.
        user = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, first_name, "
                    "  last_name, enabled) "
                    "VALUES (:uid, :t, :email, :wos, :first, :last, true) RETURNING id"
                ),
                {
                    "uid": f"{prefix}usr_{s}",
                    "t": tenant,
                    "email": f"staff_{s}@{email_domain}",
                    "wos": f"{prefix}wos_{s}",
                    "first": first_name,
                    "last": last_name,
                },
            ).scalar_one()
        )
        # Membership rows ARE portal access (0107): an ACTIVE platform row is what lets the
        # staffer's session resolve a portal at all.
        c.execute(
            text(
                "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal, status) "
                "VALUES (:u, :t, 'platform', 'active')"
            ),
            {"u": user, "t": tenant},
        )
        firm = {
            "tenant_id": str(tenant),
            "user_uuid": user,
            "org": f"{prefix}org_{s}",
            "wos": f"{prefix}wos_{s}",
            "email": f"staff_{s}@{email_domain}",
            # A preview/test send samples {{ first_name }} as the VIEWER, and the brand name comes
            # from auth.tenants.name — so the expected copy depends on both.
            "first_name": first_name,
            "firm": f"{name} {s}",
        }
        if with_operator:
            firm["operator_uuid"] = str(
                c.execute(
                    text(
                        "INSERT INTO auth.users (uid, email, workos_user_id, first_name, "
                        "  last_name, enabled) "
                        "VALUES (:uid, :email, :wos, 'Olly', 'Ops', true) RETURNING id"
                    ),
                    {
                        "uid": f"{prefix}ops_{s}",
                        "email": f"ops_{s}@graphene.test",
                        "wos": f"{prefix}wosops_{s}",
                    },
                ).scalar_one()
            )
    return firm


def principal(firm: dict[str, str], perms: list[str] | None = None) -> Principal:
    return Principal(
        user_id=firm["wos"],
        organisation_id=firm["org"],
        role="adviser",
        permissions=WRITE if perms is None else perms,
    )


def ops_principal(perms: list[str] | None = None) -> Principal:
    return Principal(
        user_id="wos_ops",
        organisation_id="ops_org",
        role="ops",
        permissions=perms if perms is not None else ["tenants:write"],
    )


def firm_claims(firm: dict[str, str], perms: list[str] | None = None) -> dict[str, Any]:
    return {
        "sub": firm["wos"],
        "role": "adviser",
        # Staff reach (auth.tenant_lineage) is a WHITELIST on the portal claim now (0107).
        "portal": "platform",
        "permissions": WRITE if perms is None else perms,
        "org_id": firm["org"],
        "user_uuid": firm["user_uuid"],
        "tenant_id": int(firm["tenant_id"]),
        "tenant_ids": [int(firm["tenant_id"])],
        "contact_ids": [],
        "account_ids": [],
    }


def ops_claims(user_uuid: str, perms: list[str] | None = None) -> dict[str, Any]:
    return {
        "sub": "wos_ops",
        "user_uuid": user_uuid,
        "role": "ops",
        "portal": "operations",
        "permissions": perms if perms is not None else ["tenants:write"],
        "tenant_id": None,
        "tenant_ids": [],
        "contact_ids": [],
        "account_ids": [],
    }


class FakeResponse:
    """Resend's answer. ``json()`` matters as well as ``raise_for_status()``: the id in that body is
    what ``comms.messages.provider_message_id`` stores, and it is the ONLY handle the delivery
    webhook has for matching a later callback back to the send."""

    def __init__(self, body: dict[str, Any] | None = None) -> None:
        self._body = {"id": "re_fake_message_id"} if body is None else body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._body


class FakeClient:
    """Stands in for ``httpx.AsyncClient`` inside ``app.email``, recording what reached "Resend".

    ``fail=True`` records the payload and THEN raises — the only way to drive ``send_email``'s
    except branch, where the bearer token is in scope. Substituted for ``httpx`` in ``app.email``'s
    namespace, never as an attribute on the real httpx module: nothing else in the process should
    lose its HTTP client because a test ran.
    """

    def __init__(self, sent: list[dict[str, Any]], *, fail: bool = False) -> None:
        self._sent = sent
        self._fail = fail

    def __call__(self, *args: Any, **kwargs: Any) -> FakeClient:
        return self

    async def __aenter__(self) -> FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self._sent.append(kwargs["json"])
        if self._fail:
            raise RuntimeError("resend is down")
        return FakeResponse()


class FakeResult:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def mappings(self) -> FakeResult:
        return self

    def one_or_none(self) -> dict[str, Any] | None:
        return self._row


class FakeRenderSession:
    """The one published-template row ``render_published_email`` selects, without Docker.

    The render path's only DB interaction is that SELECT, so this drives the REAL Jinja merge and
    the REAL ``to_email_html`` inlining. ``None`` means "this tenant has published nothing".
    """

    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row
        self.executed = 0

    async def execute(self, *_a: Any, **_k: Any) -> FakeResult:
        self.executed += 1
        return FakeResult(self._row)


def published_row(
    html: str, *, subject: str = "Subject", preheader: str = "", css: str = ""
) -> dict[str, Any]:
    return {"subject": subject, "preheader": preheader, "schema": {"html": html, "css": css}}


def session_opener(
    db: dict[str, str], claims: dict[str, Any], *, role: str = "authenticated"
) -> tuple[Callable[[], AbstractAsyncContextManager[AsyncSession]], AsyncEngine]:
    """``(opener, engine)`` — the real request-layer shape: ``SET LOCAL ROLE`` + published claims.

    The caller disposes the engine (a fixture yields the opener, then awaits ``engine.dispose()``).
    """
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def _open() -> AsyncIterator[AsyncSession]:
        async with maker() as session, session.begin():
            await session.execute(text(f"SET LOCAL ROLE {role}"))
            await session.execute(
                text("SELECT set_config('request.jwt.claims', :c, true)"),
                {"c": json.dumps(claims)},
            )
            yield session

    return _open, engine
