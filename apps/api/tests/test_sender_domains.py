"""Tenant sending domains: what may be registered, and when it becomes the From.

Operations registers one domain per firm on the shared Resend account. Mail uses it only after
Resend has verified sending and an address on that domain has been chosen. Until then the platform
``EMAIL_FROM`` behaviour is unchanged, including a support address on that domain becoming the From.
"""

from __future__ import annotations

import json
import time
import types
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app import ratelimit
from app.addressing import admin_tenant_id
from app.auth import Principal, get_current_principal
from app.db import admin_session
from app.email import sender_domains
from app.email import transport as email_mod
from app.main import app
from tests import email_support

PLATFORM = "Graphene <no-reply@graphene.test>"
DOMAIN = "mail.ridley.test"
ADDRESS = "notifications@mail.ridley.test"


def test_a_domain_is_a_hostname_and_not_the_platform_sender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(email_mod.settings, "email_from", PLATFORM)
    assert sender_domains.normalise_domain("  Mail.Ridley.Test. ") == DOMAIN
    with pytest.raises(sender_domains.SenderDomainRejected):
        sender_domains.normalise_domain("hello@ridley.test")
    with pytest.raises(sender_domains.SenderDomainRejected):
        sender_domains.normalise_domain("https://mail.ridley.test/path")
    with pytest.raises(sender_domains.SenderDomainRejected):
        sender_domains.normalise_domain("*.ridley.test")
    with pytest.raises(sender_domains.SenderDomainRejected) as caught:
        sender_domains.normalise_domain("mail.graphene.test")
    assert caught.value.status_code == 422


def test_the_from_address_must_be_one_mailbox_on_the_domain() -> None:
    assert (
        sender_domains.normalise_from_address("Notifications@Mail.Ridley.Test", DOMAIN) == ADDRESS
    )
    with pytest.raises(sender_domains.SenderDomainRejected):
        sender_domains.normalise_from_address("hello@other.test", DOMAIN)
    with pytest.raises(sender_domains.SenderDomainRejected):
        sender_domains.normalise_from_address("a@mail.ridley.test, b@mail.ridley.test", DOMAIN)


def test_sending_is_verified_from_the_spf_and_dkim_records() -> None:
    records = [
        {"record": "SPF", "status": "verified"},
        {"record": "DKIM", "status": "verified"},
        {"record": "Receiving MX", "status": "failed"},
    ]
    assert sender_domains.sending_is_verified("partially_verified", records) is True
    assert sender_domains.sending_is_verified("verified", []) is True
    assert sender_domains.sending_is_verified("pending", records[:1]) is False
    failed = [dict(records[0], status="failed"), records[1]]
    assert sender_domains.sending_is_verified("partially_failed", failed) is False
    # DMARC is optional. A missing or unverified one must not hold sending back.
    with_dmarc = [*records, {"record": "DMARC", "status": "optional"}]
    assert sender_domains.sending_is_verified("partially_verified", with_dmarc) is True


def test_the_page_shows_full_hostnames_and_an_optional_dmarc_record() -> None:
    shown = sender_domains.present_records(
        DOMAIN,
        [
            {
                "record": "DKIM",
                "name": "resend._domainkey",
                "type": "TXT",
                "value": "p=abc",
                "status": "verified",
            },
            {
                "record": "SPF",
                "name": f"send.{DOMAIN}",
                "type": "TXT",
                "value": "v=spf1 include:amazonses.com ~all",
                "status": "verified",
            },
        ],
    )
    by_purpose = {record.purpose: record for record in shown}
    assert by_purpose["DKIM"].name == f"resend._domainkey.{DOMAIN}"
    assert by_purpose["SPF"].name == f"send.{DOMAIN}"
    assert by_purpose["DMARC"].required is False
    assert by_purpose["DMARC"].name == f"_dmarc.{DOMAIN}"
    assert by_purpose["DMARC"].value == "v=DMARC1; p=none;"
    again = sender_domains.present_records(
        DOMAIN,
        [
            {
                "record": "DMARC",
                "name": "_dmarc",
                "type": "TXT",
                "value": "v=DMARC1; p=reject;",
            }
        ],
    )
    assert [record.purpose for record in again].count("DMARC") == 1
    assert again[0].value == "v=DMARC1; p=reject;"


class _Response:
    def __init__(self, body: dict[str, Any] | None, status: int = 200) -> None:
        self.status_code = status
        self._body = body
        self.content = b"{}" if body is not None else b""

    def json(self) -> dict[str, Any] | None:
        return self._body


class _Resend:
    """Records domain calls. ``httpx.AsyncClient`` is replaced with an instance of this."""

    def __init__(self, responses: list[tuple[dict[str, Any] | None, int]]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> _Resend:
        return self

    async def __aenter__(self) -> _Resend:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def request(self, method: str, url: str, **kwargs: Any) -> _Response:
        self.calls.append((method, url, kwargs.get("json")))
        body, status = self._responses.pop(0)
        return _Response(body, status)


def _install(monkeypatch: pytest.MonkeyPatch, resend: _Resend) -> None:
    monkeypatch.setattr(email_mod.settings, "resend_api_key", "re_test")
    monkeypatch.setattr(email_mod.settings, "email_from", PLATFORM)
    monkeypatch.setattr(
        email_mod,
        "httpx",
        types.SimpleNamespace(AsyncClient=resend, HTTPError=httpx.HTTPError),
    )


def _firm(super_engine: Any, *, support: str = "hello@ridley.test") -> dict[str, str]:
    return email_support.make_firm(
        super_engine,
        prefix="sd",
        name="Ridley",
        theme={"client": {"support": {"email": support}}},
    )


def _insert(
    super_engine: Any,
    tenant_id: str,
    *,
    verified: bool = False,
    with_address: bool = False,
    status: str = "not_started",
) -> str:
    """Insert this firm's domain. The hostname is derived from the tenant so rows cannot collide
    across the shared test database. Returns the domain."""
    domain = f"mail.t{tenant_id}.test"
    with super_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO email.sender_domains "
                "  (uid, tenant_id, domain, resend_domain_id, region, provider_status, "
                "   sending_verified, from_address, records) "
                "VALUES (:uid, :t, :domain, :rid, 'eu-west-1', :status, :verified, :address, "
                "        '[]'::jsonb)"
            ),
            {
                "uid": f"sd{tenant_id}",
                "t": int(tenant_id),
                "domain": domain,
                "rid": f"dom{tenant_id}",
                "status": status,
                "verified": verified,
                "address": f"notifications@{domain}" if with_address else None,
            },
        )
    return domain


async def _resolve(db: dict[str, str], tenant_id: str, **kwargs: Any) -> email_mod.Sender:
    opener, engine = email_support.session_opener(
        db, email_support.ops_claims("operator"), role="service_role"
    )
    try:
        async with opener() as session:
            return await email_mod.resolve_sender(session, int(tenant_id), **kwargs)
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def _cold(monkeypatch: pytest.MonkeyPatch) -> None:
    email_mod.clear_caches()
    monkeypatch.setattr(email_mod.settings, "email_from", PLATFORM)


@pytest.mark.asyncio
async def test_registering_stores_the_records_and_does_not_send_yet(
    db: dict[str, str], super_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    firm = _firm(super_engine)
    resend = _Resend(
        [
            (
                {
                    "id": "dom_new",
                    "name": DOMAIN,
                    "status": "not_started",
                    "region": "eu-west-1",
                    "records": [
                        {
                            "record": "DKIM",
                            "name": "resend._domainkey.mail",
                            "type": "TXT",
                            "value": "p=abc",
                            "ttl": "Auto",
                            "status": "not_started",
                        }
                    ],
                },
                200,
            )
        ]
    )
    _install(monkeypatch, resend)
    opener, engine = email_support.session_opener(
        db, email_support.ops_claims("operator"), role="service_role"
    )
    try:
        async with opener() as session:
            created = await sender_domains.register_sender_domain(
                session, int(firm["tenant_id"]), DOMAIN
            )
    finally:
        await engine.dispose()
    method, url, body = resend.calls[0]
    assert method == "POST"
    assert url.endswith("/domains")
    assert body == {
        "name": DOMAIN,
        "region": "eu-west-1",
        "open_tracking": False,
        "click_tracking": False,
        "capabilities": {"sending": "enabled", "receiving": "disabled"},
    }
    assert created.sending_verified is False
    assert created.active is False
    assert created.records[0].value == "p=abc"
    sender = await _resolve(db, firm["tenant_id"])
    assert sender.from_addr == f'"{firm["firm"]}" <no-reply@graphene.test>'
    assert sender.reply_to == "hello@ridley.test"


@pytest.mark.asyncio
async def test_a_second_domain_is_refused_before_resend_is_called(
    db: dict[str, str], super_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    firm = _firm(super_engine)
    _insert(super_engine, firm["tenant_id"])

    async def boom(name: str) -> email_mod.ResendDomain:
        raise AssertionError(name)

    monkeypatch.setattr(email_mod, "create_sending_domain", boom)
    opener, engine = email_support.session_opener(
        db, email_support.ops_claims("operator"), role="service_role"
    )
    try:
        async with opener() as session:
            with pytest.raises(sender_domains.SenderDomainRejected) as caught:
                await sender_domains.register_sender_domain(
                    session, int(firm["tenant_id"]), "mail.other.test"
                )
    finally:
        await engine.dispose()
    assert caught.value.status_code == 409


@pytest.mark.asyncio
async def test_an_unverified_domain_keeps_the_platform_sender(
    db: dict[str, str], super_engine: Any
) -> None:
    firm = _firm(super_engine)
    domain = _insert(super_engine, firm["tenant_id"], verified=False, status="pending")
    sender = await _resolve(db, firm["tenant_id"])
    assert sender.from_addr == f'"{firm["firm"]}" <no-reply@graphene.test>'
    assert sender.reply_to == "hello@ridley.test"
    assert domain.startswith("mail.t")


@pytest.mark.asyncio
async def test_a_verified_domain_sends_as_the_firm_and_replies_to_support(
    db: dict[str, str], super_engine: Any
) -> None:
    """Support on the sending domain stays Reply-To. It does not replace the chosen From."""
    firm = _firm(super_engine, support="hello@mail.ridley.test")
    domain = _insert(super_engine, firm["tenant_id"], verified=True, status="verified")
    # The chosen address is on the domain; support is too, and still must not become the From.
    address = f"notifications@{domain}"
    opener, engine = email_support.session_opener(
        db, email_support.ops_claims("operator"), role="service_role"
    )
    try:
        async with opener() as session:
            await sender_domains.set_from_address(session, int(firm["tenant_id"]), address)
    finally:
        await engine.dispose()
    # Point support at this domain without it being the chosen mailbox. The superuser connection
    # avoids the audit trigger, which requires a real user id.
    with super_engine.connect() as conn:
        conn.execute(
            text("UPDATE auth.tenants SET theme = CAST(:theme AS jsonb) WHERE id = :t"),
            {
                "t": int(firm["tenant_id"]),
                "theme": json.dumps({"client": {"support": {"email": f"hello@{domain}"}}}),
            },
        )
    email_mod.clear_caches()
    sender = await _resolve(db, firm["tenant_id"])
    assert sender.from_addr == f'"{firm["firm"]}" <{address}>'
    assert sender.reply_to == f"hello@{domain}"


@pytest.mark.asyncio
async def test_an_adviser_on_the_tenant_domain_is_the_literal_from(
    db: dict[str, str], super_engine: Any
) -> None:
    firm = _firm(super_engine)
    domain = _insert(
        super_engine, firm["tenant_id"], verified=True, with_address=True, status="verified"
    )
    address = f"notifications@{domain}"
    on_domain = await _resolve(
        db,
        firm["tenant_id"],
        on_behalf_of=email_mod.OnBehalfOf("Sarah Whitfield", f"sarah@{domain}"),
    )
    assert on_domain.from_addr == f'"Sarah Whitfield" <sarah@{domain}>'
    assert on_domain.reply_to is None
    off_domain = await _resolve(
        db,
        firm["tenant_id"],
        on_behalf_of=email_mod.OnBehalfOf("Sarah Whitfield", "sarah@ridley.test"),
    )
    assert off_domain.from_addr == f'"Sarah Whitfield ({firm["firm"]})" <{address}>'
    assert off_domain.reply_to == "sarah@ridley.test"


@pytest.mark.asyncio
async def test_a_domain_webhook_turns_sending_on_and_a_failure_turns_it_off(
    db: dict[str, str], super_engine: Any
) -> None:
    firm = _firm(super_engine)
    domain = _insert(
        super_engine, firm["tenant_id"], with_address=True, verified=False, status="pending"
    )
    address = f"notifications@{domain}"
    resend_id = f"dom{firm['tenant_id']}"
    email_mod._sender_cache[(int(firm["tenant_id"]), "client")] = (
        time.monotonic(),
        email_mod.Sender(PLATFORM),
    )
    opener, engine = email_support.session_opener(
        db, email_support.ops_claims("operator"), role="service_role"
    )
    try:
        async with opener() as session:
            matched = await sender_domains.apply_domain_event(
                session,
                "domain.updated",
                {
                    "id": resend_id,
                    "status": "partially_verified",
                    "records": [
                        {
                            "record": "SPF",
                            "name": "send",
                            "type": "TXT",
                            "value": "v=spf1",
                            "status": "verified",
                        },
                        {
                            "record": "DKIM",
                            "name": "resend._domainkey",
                            "type": "TXT",
                            "value": "p=abc",
                            "status": "verified",
                        },
                    ],
                },
            )
            assert matched is True
        async with opener() as session:
            stored = await sender_domains.read_sender_domain(session, int(firm["tenant_id"]))
    finally:
        await engine.dispose()
    assert (int(firm["tenant_id"]), "client") not in email_mod._sender_cache
    assert stored is not None and stored.active is True
    sender = await _resolve(db, firm["tenant_id"])
    assert address in sender.from_addr

    opener, engine = email_support.session_opener(
        db, email_support.ops_claims("operator"), role="service_role"
    )
    try:
        async with opener() as session:
            await sender_domains.apply_domain_event(
                session, "domain.updated", {"id": resend_id, "status": "failed"}
            )
    finally:
        await engine.dispose()
    email_mod.clear_caches()
    fallen_back = await _resolve(db, firm["tenant_id"])
    assert fallen_back.from_addr == f'"{firm["firm"]}" <no-reply@graphene.test>'


@pytest.mark.asyncio
async def test_the_from_address_has_to_sit_on_the_registered_domain(
    db: dict[str, str], super_engine: Any
) -> None:
    firm = _firm(super_engine)
    domain = _insert(super_engine, firm["tenant_id"])
    opener, engine = email_support.session_opener(
        db, email_support.ops_claims("operator"), role="service_role"
    )
    try:
        async with opener() as session:
            saved = await sender_domains.set_from_address(
                session, int(firm["tenant_id"]), f"Notes@{domain}"
            )
            assert saved.from_address == f"notes@{domain}"
            with pytest.raises(sender_domains.SenderDomainRejected):
                await sender_domains.set_from_address(
                    session, int(firm["tenant_id"]), "notes@other.test"
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_sender_test_goes_to_the_operator_as_the_firm(
    db: dict[str, str], super_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The button has no recipient. It mails the operator, from the address the firm would use."""
    firm = email_support.make_firm(
        super_engine,
        prefix="sd",
        name="Ridley",
        with_operator=True,
        theme={"client": {"support": {"email": "hello@ridley.test"}}},
    )
    domain = _insert(
        super_engine, firm["tenant_id"], verified=True, with_address=True, status="verified"
    )
    address = f"notifications@{domain}"
    with super_engine.connect() as conn:
        operator = conn.execute(
            text("SELECT email FROM auth.users WHERE id = :id"),
            {"id": firm["operator_uuid"]},
        ).scalar_one()
    sent: list[dict[str, Any]] = []

    async def _capture(**kwargs: Any) -> bool:
        sent.append(kwargs)
        return True

    monkeypatch.setattr(email_mod, "send_email", _capture)
    monkeypatch.setattr(email_mod, "suppression_reason", lambda _to: None)
    monkeypatch.setattr(email_mod, "sending_disabled", lambda: None)

    async def _refresh() -> None:
        return None

    monkeypatch.setattr(email_mod, "refresh_allowlist", _refresh)
    ratelimit.limiter.reset()
    opener, engine = email_support.session_opener(
        db, email_support.ops_claims(firm["operator_uuid"]), role="service_role"
    )
    try:
        async with opener() as session:
            result = await sender_domains.send_sender_test(session, int(firm["tenant_id"]))
    finally:
        await engine.dispose()
    assert result.status == "delivered"
    assert result.to == operator
    assert sent[0]["to"] == operator
    assert address in sent[0]["sender"]
    assert sent[0]["reply_to"] == "hello@ridley.test"
    assert result.subject.startswith("[TEST] ")

    async def _no(**_kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(email_mod, "send_email", _no)
    monkeypatch.setattr(email_mod, "suppression_reason", lambda _to: "not on this stage")
    opener, engine = email_support.session_opener(
        db, email_support.ops_claims(firm["operator_uuid"]), role="service_role"
    )
    try:
        async with opener() as session:
            held = await sender_domains.send_sender_test(session, int(firm["tenant_id"]))
    finally:
        await engine.dispose()
    assert held.status == "suppressed"
    assert held.to == operator
    assert operator not in (held.detail or "")


@pytest.mark.asyncio
async def test_a_sender_test_refuses_an_operator_with_no_address(
    db: dict[str, str], super_engine: Any
) -> None:
    firm = _firm(super_engine)
    opener, engine = email_support.session_opener(
        db,
        email_support.ops_claims("00000000-0000-0000-0000-000000000099"),
        role="service_role",
    )
    try:
        async with opener() as session:
            with pytest.raises(sender_domains.SenderDomainRejected) as caught:
                await sender_domains.send_sender_test(session, int(firm["tenant_id"]))
    finally:
        await engine.dispose()
    assert caught.value.status_code == 422


# ---- the operations gate, over HTTP and without a database ------------------------------------


@pytest_asyncio.fixture
async def http() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.fixture
def acting(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    calls: list[str] = []

    async def _no_session() -> AsyncIterator[object]:
        yield object()

    async def _read(session: object, tenant_id: int) -> None:
        return None

    async def _register(
        session: object, tenant_id: int, raw: str
    ) -> sender_domains.SenderDomainOut:
        calls.append(raw)
        return sender_domains.SenderDomainOut(
            uid="sd_1",
            domain=DOMAIN,
            region="eu-west-1",
            status="not_started",
            sending_verified=False,
            active=False,
            from_address=None,
            verified_at=None,
            records=[],
        )

    app.dependency_overrides[admin_session] = _no_session
    app.dependency_overrides[admin_tenant_id] = lambda: 7
    monkeypatch.setattr(sender_domains, "read_sender_domain", _read)
    monkeypatch.setattr(sender_domains, "register_sender_domain", _register)
    yield calls
    app.dependency_overrides.pop(get_current_principal, None)
    app.dependency_overrides.pop(admin_session, None)
    app.dependency_overrides.pop(admin_tenant_id, None)


@pytest.fixture
def principal_for() -> Any:
    def act(perms: list[str]) -> None:
        app.dependency_overrides[get_current_principal] = lambda: Principal(
            user_id="user_ops", role="operations", permissions=list(perms)
        )

    return act


@pytest.mark.asyncio
async def test_reading_the_domain_needs_tenants_read(
    http: AsyncClient, acting: list[str], principal_for: Any
) -> None:
    principal_for(["tenants:read"])
    allowed = await http.get("/admin/tenants/ten_seven/sender-domain")
    assert allowed.status_code == 200
    assert allowed.json() is None

    principal_for([])
    refused = await http.get("/admin/tenants/ten_seven/sender-domain")
    assert refused.status_code == 403


@pytest.mark.asyncio
async def test_registering_the_domain_needs_tenants_write(
    http: AsyncClient, acting: list[str], principal_for: Any
) -> None:
    principal_for(["tenants:read"])
    refused = await http.post("/admin/tenants/ten_seven/sender-domain", json={"domain": DOMAIN})
    assert refused.status_code == 403
    assert acting == []

    principal_for(["tenants:write"])
    allowed = await http.post("/admin/tenants/ten_seven/sender-domain", json={"domain": DOMAIN})
    assert allowed.status_code == 200
    assert allowed.json()["domain"] == DOMAIN
    assert acting == [DOMAIN]
