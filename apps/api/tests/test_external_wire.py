"""Key cache, provider sync, and the live SQL path. No database."""

from __future__ import annotations

import base64
import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

from app import storage
from app.complycube.client import (
    ScreeningClient,
    address_payload,
    client_payload,
    environment,
)
from app.complycube.sync import CLAIM_SQL, build_plan, enqueue
from external.db import RequestBoundStore, accept_screening, publish_tenant
from external.keys import CachedKey, KeyCache, KeyVerifier, LastUsedThrottle, hash_bearer
from external.live import (
    INSERT_CHECK,
    INSERT_CONTACT,
    SELECT_CHECK,
    SELECT_CHECKS,
    SELECT_CLIENT,
    SELECT_CLIENTS,
    SELECT_DOCUMENTS,
    UPDATE_CONTACT,
    UPSERT_CUSTODIAN,
    UPSERT_PROVIDER_DOCUMENT,
    UPSERT_PROVIDER_PHOTO,
    HeldFile,
    LiveStore,
    check_from_row,
    client_from_row,
    identity_set,
)
from external.models import CheckCreate, ClientCreate, CustodianPut

API = Path(__file__).resolve().parents[1]


async def _public(host: str, port: int) -> list[str]:
    """A resolver that answers every host with one public address."""
    return ["93.184.216.34"]


class Row:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


class Result:
    def __init__(self, value: Any) -> None:
        self.value = value

    def scalar(self) -> Any:
        return self.value

    def first(self) -> Any:
        return self.value

    def all(self) -> list[Any]:
        if self.value is None:
            return []
        if isinstance(self.value, list):
            return self.value
        return [self.value]


class Scripted:
    def __init__(self, returns: list[Any] | None = None) -> None:
        self.returns = list(returns or [])
        self.executed: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, params: Any = None) -> Result:
        self.executed.append((str(statement), dict(params or {})))
        value = self.returns.pop(0) if self.returns else None
        return Result(value)

    async def scalar(self, statement: Any, params: Any = None) -> Any:
        result = await self.execute(statement, params)
        return result.scalar()


async def test_build_plan_fetches_the_check_and_the_resources_it_points_at() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        seen.append(path)
        assert request.headers["authorization"] == "test-key"
        payloads = {
            "/checks/chk_1": {
                "id": "chk_1",
                "clientId": "cli_1",
                "status": "complete",
                "outcome": "clear",
                "addressId": "addr_1",
                "documentId": "doc_1",
                "livePhotoId": "photo_1",
                "type": "document_check",
            },
            "/clients/cli_1": {"id": "cli_1", "externalId": "contactuid"},
            "/addresses/addr_1": {"id": "addr_1", "country": "GB"},
            "/documents/doc_1": {"id": "doc_1", "issuingCountry": "US"},
            "/livePhotos/photo_1": {"id": "photo_1"},
        }
        return httpx.Response(200, json=payloads[path])

    client = ScreeningClient("test-key", transport=httpx.MockTransport(handler))
    plan = await build_plan(
        client, {"id": "evt_1", "type": "check.completed", "payload": {"id": "chk_1"}}
    )
    assert plan.check is not None and plan.check["id"] == "chk_1"
    assert plan.client is not None and plan.client["externalId"] == "contactuid"
    assert [item["id"] for item in plan.addresses] == ["addr_1"]
    assert [item["id"] for item in plan.documents] == ["doc_1"]
    assert [item["id"] for item in plan.live_photos] == ["photo_1"]
    assert seen[0] == "/checks/chk_1"


async def test_enqueue_returns_false_when_pgmq_is_absent() -> None:
    session = Scripted()
    assert await enqueue(session, 4) is False  # type: ignore[arg-type]
    sql = " ".join(statement for statement, _ in session.executed)
    assert "pg_extension" in sql
    assert "pgmq.send" not in sql
    assert "FOR UPDATE SKIP LOCKED" in str(CLAIM_SQL)


def test_cache_ttl_miss_ttl_and_last_used_throttle() -> None:
    now = [0.0]
    cache = KeyCache(ttl=60, miss_ttl=5, clock=lambda: now[0])
    throttle = LastUsedThrottle(interval=60, clock=lambda: now[0])
    lookups: list[float] = []
    touches: list[float] = []

    async def lookup(key_hash: str) -> CachedKey:
        lookups.append(now[0])
        if key_hash == hash_bearer("missing"):
            return CachedKey(found=False)
        if key_hash == hash_bearer("revoked"):
            return CachedKey(found=True, uid="revoked", tenant_id=1, revoked=True)
        return CachedKey(found=True, uid="keyuid", tenant_id=7, scopes=("clients:read",))

    async def touch(record: CachedKey) -> None:
        touches.append(now[0])
        assert record.uid == "keyuid"

    verifier = KeyVerifier(lookup, touch, cache=cache, throttle=throttle)
    import asyncio

    asyncio.run(_exercise(verifier, lookups, touches, now))


async def _exercise(
    verifier: KeyVerifier, lookups: list[float], touches: list[float], now: list[float]
) -> None:
    assert hashlib.sha256(b"gpk_live_one").hexdigest() == hash_bearer("gpk_live_one")
    assert await verifier.authenticate("gpk_live_one") is not None
    assert await verifier.authenticate("gpk_live_one") is not None
    assert len(lookups) == 1
    assert len(touches) == 1
    now[0] = 30
    assert await verifier.authenticate("gpk_live_one") is not None
    assert len(lookups) == 1
    assert len(touches) == 1
    now[0] = 61
    assert await verifier.authenticate("gpk_live_one") is not None
    assert len(lookups) == 2
    assert len(touches) == 2
    verifier.invalidate(hash_bearer("gpk_live_one"))
    assert await verifier.authenticate("gpk_live_one") is not None
    assert len(lookups) == 3

    before = len(lookups)
    assert await verifier.authenticate("missing") is None
    assert await verifier.authenticate("missing") is None
    assert len(lookups) == before + 1
    now[0] = 67
    assert await verifier.authenticate("missing") is None
    assert len(lookups) == before + 2

    assert await verifier.authenticate("revoked") is None
    revoked_lookups = len(lookups)
    assert await verifier.authenticate("revoked") is None
    assert len(lookups) == revoked_lookups


async def test_identity_uploads_documents_and_a_selfie_instead_of_a_session() -> None:
    seen: list[tuple[str, str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        body = json.loads(request.content) if request.content else {}
        seen.append((request.method, path, body))
        if path == "/documents":
            assert body["type"] == "driving_license"
            assert body["issuingCountry"] == "GB"
            return httpx.Response(200, json={"id": "doc_1"})
        if path == "/documents/doc_1/upload/front":
            assert body["fileName"] == "licence.png"
            assert body["data"]
            return httpx.Response(200, json={"id": "img_1"})
        if path == "/livePhotos":
            assert body["clientId"] == "cli_1"
            assert body["data"]
            return httpx.Response(200, json={"id": "photo_1"})
        if path == "/checks":
            assert body == {
                "clientId": "cli_1",
                "type": "identity_check",
                "documentId": "doc_1",
                "livePhotoId": "photo_1",
            }
            return httpx.Response(200, json={"id": "chk_1", "status": "pending"})
        return httpx.Response(404)

    client = ScreeningClient("test-key", transport=httpx.MockTransport(handler))
    created = await client.submit_identity(
        "cli_1",
        document_type="drivers_license",
        issuing_country="GB",
        document=b"document-bytes",
        document_file_name="licence.png",
        selfie=b"selfie-bytes",
    )
    assert created["id"] == "chk_1"
    paths = [path for _, path, _ in seen]
    assert paths == [
        "/documents",
        "/documents/doc_1/upload/front",
        "/livePhotos",
        "/checks",
    ]
    assert "/flow/sessions" not in paths


async def test_aml_is_only_the_screening_check() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "chk_aml", "status": "pending"})

    client = ScreeningClient("test-key", transport=httpx.MockTransport(handler))
    created = await client.create_screening("cli_1", monitoring=False)
    assert created["id"] == "chk_aml"
    assert seen == [
        {"clientId": "cli_1", "type": "extensive_screening_check", "enableMonitoring": False}
    ]


async def test_identity_without_documents_is_refused_before_insert() -> None:
    session = Scripted([Row(client_id=3, contact_id=9, uid="clientuid", email="a@b.co"), []])
    store = LiveStore(session)  # type: ignore[arg-type]
    from external.store import Rejected

    try:
        await store.create_check("clientuid", CheckCreate(kind="identity"), tenant_id=7)
    except Rejected as exc:
        assert exc.code == "documents_required"
        assert exc.status == 422
    else:
        raise AssertionError("identity was accepted")
    sql = "\n".join(statement for statement, _ in session.executed)
    assert "compliance.documents" in sql
    assert "INSERT INTO compliance.checks" not in sql


def test_provider_payload_sends_the_first_nationality_and_a_complete_address() -> None:
    payload = client_payload(
        {"type": "person", "email": "a@b.co", "first_name": "Ada", "nationality": ["GB", "IE"]},
        external_id="contactuid",
    )
    assert payload["personDetails"]["nationality"] == "GB"
    assert payload["externalId"] == "contactuid"
    assert address_payload("cli", {"line": "1", "city": "London"}) is None
    sent = address_payload("cli", {"line": "1", "city": "London", "country": "GB"})
    assert sent is not None and sent["country"] == "GB"


async def test_a_check_does_not_read_or_create_an_account() -> None:
    session = Scripted(
        [
            Row(
                client_id=3,
                contact_id=9,
                uid="clientuid",
                email="a@b.co",
                first_name="Ada",
                last_name="Lovelace",
                dob="1815-12-10",
                address={"country": "GB"},
            )
        ]
    )
    store = LiveStore(session)  # type: ignore[arg-type]
    check = await store.create_check("clientuid", CheckCreate(kind="aml"), tenant_id=7)
    assert check.status == "pending"
    assert check.kind == "aml"
    assert check.name == "Ada Lovelace"
    assert check.country == "GB"
    assert check.dob is not None
    assert check.dob.isoformat() == "1815-12-10"
    sql = "\n".join(statement for statement, _ in session.executed)
    assert "platform.accounts" not in sql
    assert "INSERT INTO platform.accounts" not in sql
    inserted = next(
        (statement, params)
        for statement, params in session.executed
        if "compliance.checks" in statement
    )
    assert "account_id" not in inserted[0]
    assert inserted[1]["tenant_id"] == 7
    assert "result" not in str(SELECT_CHECK)
    assert "body" not in str(SELECT_CHECK)
    assert "result" not in str(INSERT_CHECK)
    assert "account_uid" not in str(SELECT_CHECK)


def test_check_from_row_projects_identity_and_aml_without_provider_fields() -> None:
    identity = check_from_row(
        Row(
            uid="checkuididen",
            kind="identity",
            status="pending",
            created_at="2026-09-24T12:00:00+00:00",
            updated_at="2026-09-24T12:00:00+00:00",
            documents=[
                {"uid": "selfieuid123", "type": "selfie", "issuing_country": None},
                {"uid": "passportuid1", "type": "passport", "issuing_country": "GB"},
            ],
        )
    )
    assert identity.document_uids == ["selfieuid123", "passportuid1"]
    assert identity.issuing_country == "GB"
    assert "name" not in identity.model_dump()
    aml = check_from_row(
        Row(
            uid="checkuidaml1",
            kind="aml",
            status="clear",
            created_at="2026-09-24T12:00:00+00:00",
            updated_at="2026-09-24T12:05:00+00:00",
            name="Ada Lovelace",
            dob="1815-12-10",
            country="GB",
        )
    )
    dumped = aml.model_dump(mode="json")
    assert dumped["name"] == "Ada Lovelace"
    assert dumped["dob"] == "1815-12-10"
    assert dumped["country"] == "GB"
    assert "document_uids" not in dumped
    assert "result" not in dumped
    assert "body" not in dumped


async def test_custodian_put_writes_platform_links_for_the_contact() -> None:
    session = Scripted(
        [
            Row(
                uid="clientuid",
                client_id=3,
                contact_id=9,
                contact_uid="contactuid",
                contact_type="individual",
                email="ada@analytical.example",
                first_name="Ada",
                middle_name=None,
                last_name="Lovelace",
                dob=None,
                nationality=["GB"],
                tax_residency=None,
                data={},
                address=None,
                custodians=[],
            )
        ]
    )
    store = LiveStore(session)  # type: ignore[arg-type]
    updated = await store.put_custodian(
        "clientuid",
        CustodianPut(provider="seccl", external_id="seccl-1"),
        tenant_id=7,
    )
    assert updated is not None
    assert updated.custodians[0].provider == "seccl"
    statement, params = session.executed[-1]
    assert "platform.links" in statement
    assert "platform.provider_code" in statement
    assert params["provider"] == "seccl"
    assert params["entity_id"] == 9
    assert params["tenant_id"] == 7
    assert params["external_id"] == "seccl-1"
    assert "INSERT INTO platform.accounts" not in statement
    assert str(UPSERT_CUSTODIAN) == statement or "platform.links" in statement


async def test_publish_tenant_does_not_set_a_portal() -> None:
    session = Scripted()
    await publish_tenant(session, 7)  # type: ignore[arg-type]
    claims = json.loads(session.executed[0][1]["claims"])
    assert claims["tenant_id"] == 7
    assert "portal" not in claims
    source = (API / "external" / "db.py").read_text(encoding="utf-8")
    assert "SET LOCAL ROLE service_role" in source
    assert "portal=platform" not in source.replace(" ", "")
    assert '"portal"' not in source


def test_external_package_does_not_import_the_portal_database_module() -> None:
    for path in (API / "external").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "app.db" not in text
        assert "app.main" not in text


def test_compliance_migration_names_the_six_tables_and_skips_contact_scope() -> None:
    sql = (API / "migrations" / "sql" / "0186_compliance.sql").read_text(encoding="utf-8")
    for name in ("clients", "addresses", "documents", "live_photos", "checks", "events"):
        assert name in sql
    assert sql.count("ENABLE ROW LEVEL SECURITY") >= 1
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "contact_scope" not in sql


def test_partner_writes_mobile_and_phone() -> None:
    insert = str(INSERT_CONTACT)
    update = str(UPDATE_CONTACT)
    selected = str(SELECT_CLIENT)
    assert "mobile" in insert
    assert "phone" in insert
    assert "mobile" in update
    assert "phone" in update
    assert "mobile" in selected
    assert "phone" in selected


async def test_phones_are_written_to_the_columns_and_read_back_from_them() -> None:
    session = Scripted([11, 22, None])
    store = LiveStore(session)  # type: ignore[arg-type]
    await store.create_client(
        ClientCreate(
            email="ada@analytical.example",
            first_name="Ada",
            last_name="Lovelace",
            dob="1815-12-10",  # type: ignore[arg-type]
            nationality=["GB"],
            address={"country": "GB"},  # type: ignore[arg-type]
            mobile="+447700900123",
            telephone="020 7946 0123",
        ),
        tenant_id=7,
    )
    params = session.executed[0][1]
    assert params["mobile"] == "+447700900123"
    assert params["phone"] == "+442079460123"
    read = client_from_row(
        Row(
            uid="clientuid",
            contact_type="individual",
            email="ada@analytical.example",
            first_name="Ada",
            last_name="Lovelace",
            dob=None,
            nationality=["GB"],
            data={"mobile": "from-data", "telephone": "from-data"},
            mobile="+447700900123",
            phone="02079460123",
            address=None,
            custodians=[],
        )
    )
    assert read.mobile == "+447700900123"
    assert read.telephone == "02079460123"


async def test_identities_are_written_and_read_back() -> None:
    session = Scripted([11, 22, None])
    store = LiveStore(session)  # type: ignore[arg-type]
    await store.create_client(
        ClientCreate(
            email="ada@analytical.example",
            first_name="Ada",
            last_name="Lovelace",
            dob="1815-12-10",  # type: ignore[arg-type]
            nationality=["GB"],
            address={"country": "GB"},  # type: ignore[arg-type]
            identities=[
                {"type": "national_insurance_number", "identifier": "QQ123456C"},  # type: ignore[arg-type]
                {"type": "ssn", "identifier": "123-45-6789"},  # type: ignore[arg-type]
            ],
        ),
        tenant_id=7,
    )
    params = session.executed[0][1]
    assert params["national_identifier"] == "QQ123456C"
    assert params["national_identifier_type"] == "national_insurance_number"
    stored = json.loads(params["data"])["identities"]
    assert stored[1] == {"type": "ssn", "identifier": "123-45-6789"}
    read = client_from_row(
        Row(
            uid="clientuid",
            contact_type="individual",
            email="ada@analytical.example",
            first_name="Ada",
            last_name="Lovelace",
            dob=None,
            nationality=["GB"],
            data={"identities": stored},
            mobile=None,
            phone=None,
            address=None,
            custodians=[],
        )
    )
    assert read.identities is not None
    assert [item.type for item in read.identities] == ["national_insurance_number", "ssn"]
    legacy = client_from_row(
        Row(
            uid="clientuid",
            contact_type="individual",
            email="ada@analytical.example",
            first_name="Ada",
            last_name="Lovelace",
            dob=None,
            nationality=["GB"],
            data={"identity": {"type": "ssn", "identifier": "123-45-6789"}},
            mobile=None,
            phone=None,
            address=None,
            custodians=[],
        )
    )
    assert legacy.identities is not None
    assert legacy.identities[0].identifier == "123-45-6789"


def test_client_list_loads_custodian_links() -> None:
    listed = " ".join(str(SELECT_CLIENTS).split())
    single = " ".join(str(SELECT_CLIENT).split())
    assert "platform.links" in listed
    assert "'[]'::json AS custodians" not in listed
    assert listed.count("platform.links") == single.count("platform.links")


def test_webhook_handlers_are_not_a_process_wide_store() -> None:
    source = (API / "external" / "app.py").read_text(encoding="utf-8")
    assert "store.webhook_url" not in source
    assert "store.webhook_secret" not in source


def test_compliance_migration_stores_one_webhook_per_tenant() -> None:
    sql = (API / "migrations" / "sql" / "0186_compliance.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE compliance.webhooks" in sql
    assert "UNIQUE (tenant_id)" in sql
    from app.tenant_data import CLEAR_TABLES

    assert "compliance.webhooks" in CLEAR_TABLES


async def test_webhook_put_get_and_delete_are_scoped_by_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cryptography.fernet import Fernet

    from app.config import settings

    monkeypatch.setattr(settings, "secrets_master_key", Fernet.generate_key().decode())
    inserted = Scripted([None])
    store = LiveStore(inserted, resolver=_public)  # type: ignore[arg-type]
    saved = await store.put_webhook("https://firm.example/hooks", tenant_id=7)
    assert saved.url == "https://firm.example/hooks"
    assert saved.secret
    statement, params = inserted.executed[0]
    assert "compliance.webhooks" in statement
    assert params["tenant_id"] == 7
    assert params["url"] == "https://firm.example/hooks"
    assert params["secret_ct"] != saved.secret

    loaded = Scripted([Row(url="https://firm.example/hooks")])
    got = await LiveStore(loaded).get_webhook(tenant_id=7)  # type: ignore[arg-type]
    assert got.url == "https://firm.example/hooks"
    assert "secret" not in got.model_dump()
    select_sql, select_params = loaded.executed[0]
    assert "compliance.webhooks" in select_sql
    assert select_params["tenant_id"] == 7
    assert "secret" not in select_sql

    other = Scripted([None])
    missed = await LiveStore(other).get_webhook(tenant_id=8)  # type: ignore[arg-type]
    assert missed.url is None
    assert "secret" not in missed.model_dump()
    assert other.executed[0][1]["tenant_id"] == 8

    removed = Scripted([None])
    cleared = await LiveStore(removed).delete_webhook(tenant_id=7)  # type: ignore[arg-type]
    assert cleared.url is None
    assert "secret" not in cleared.model_dump()
    delete_sql, delete_params = removed.executed[0]
    assert "DELETE" in delete_sql
    assert "compliance.webhooks" in delete_sql
    assert delete_params["tenant_id"] == 7


async def test_one_firm_cannot_replace_another_firms_webhook() -> None:
    from external.store import StubStore

    store = StubStore()
    first = await store.put_webhook("https://a.example/hooks", tenant_id=1)
    second = await store.put_webhook("https://b.example/hooks", tenant_id=2)
    assert first.secret and second.secret and first.secret != second.secret
    kept = await store.get_webhook(tenant_id=1)
    replaced = await store.put_webhook("https://b.example/new", tenant_id=2)
    again = await store.get_webhook(tenant_id=1)
    other = await store.get_webhook(tenant_id=2)
    assert kept.url == "https://a.example/hooks"
    assert "secret" not in kept.model_dump()
    assert again.url == "https://a.example/hooks"
    assert "secret" not in again.model_dump()
    assert replaced.secret
    assert other.url == "https://b.example/new"
    assert "secret" not in other.model_dump()


def _client_row(**extra: Any) -> Row:
    values: dict[str, Any] = {
        "uid": "clientuid",
        "client_id": 3,
        "contact_id": 9,
        "contact_uid": "contactuid12",
        "contact_type": "individual",
        "email": "ada@analytical.example",
        "first_name": "Ada",
        "middle_name": None,
        "last_name": "Lovelace",
        "dob": "1815-12-10",
        "nationality": ["GB"],
        "tax_residency": None,
        "data": {},
        "address": {"line": "1 Road", "city": "London", "country": "GB", "body": {}},
        "custodians": [],
        "provider_client_id": None,
        "mobile": None,
        "phone": None,
    }
    values.update(extra)
    return Row(**values)


async def test_live_aml_check_calls_the_screening_provider() -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        body = json.loads(request.content) if request.content else {}
        seen.append((path, body))
        if path == "/clients":
            return httpx.Response(200, json={"id": "cli_1"})
        if path == "/addresses":
            return httpx.Response(200, json={"id": "addr_1"})
        if path == "/checks":
            return httpx.Response(200, json={"id": "chk_aml", "status": "pending"})
        return httpx.Response(404, json={"message": "missing"})

    screening = ScreeningClient("test-key", transport=httpx.MockTransport(handler))
    session = Scripted(
        [
            _client_row(),
            None,
            Row(created_at="2026-09-24T12:00:00+00:00", updated_at="2026-09-24T12:00:00+00:00"),
        ]
    )
    store = LiveStore(session, screening=screening)  # type: ignore[arg-type]
    check = await store.create_check("clientuid", CheckCreate(kind="aml"), tenant_id=7)
    assert check.status == "pending"
    assert check.kind == "aml"
    paths = [path for path, _ in seen]
    assert "ON CONFLICT (provider_check_id)" in str(INSERT_CHECK)
    assert "/checks" in paths
    screening_body = next(body for path, body in seen if path == "/checks")
    assert screening_body["type"] == "extensive_screening_check"
    assert screening_body["clientId"] == "cli_1"
    inserted = next(
        params for statement, params in session.executed if "compliance.checks" in statement
    )
    assert inserted["provider_check_id"] == "chk_aml"
    assert inserted["tenant_id"] == 7
    assert "complycube" not in json.dumps(check.model_dump(mode="json")).lower()


async def test_live_identity_check_uploads_documents_and_starts_a_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        seen.append(path)
        body = json.loads(request.content) if request.content else {}
        if path == "/clients":
            return httpx.Response(200, json={"id": "cli_1"})
        if path == "/addresses":
            return httpx.Response(200, json={"id": "addr_1"})
        if path == "/documents":
            assert body["type"] == "passport"
            return httpx.Response(200, json={"id": "doc_1"})
        if path == "/documents/doc_1/upload/front":
            return httpx.Response(200, json={"id": "img_1"})
        if path == "/livePhotos":
            return httpx.Response(200, json={"id": "photo_1"})
        if path == "/checks":
            assert body["type"] == "identity_check"
            return httpx.Response(200, json={"id": "chk_id", "status": "pending"})
        return httpx.Response(404, json={"message": "missing"})

    screening = ScreeningClient("test-key", transport=httpx.MockTransport(handler))
    objects = {"files/s.jpg": b"selfie-bytes", "files/p.png": b"passport-bytes"}

    async def get_document(key: str, *, bucket: str | None = None) -> tuple[bytes, str]:
        assert bucket == "t-firm"
        return objects[key], "image/jpeg"

    monkeypatch.setattr(storage, "get_document", get_document)
    session = Scripted(
        [
            _client_row(),
            [
                Row(
                    uid="selfieuid123",
                    classification="selfie",
                    issuing_country=None,
                    side=None,
                    object_key="files/s.jpg",
                    body={"file_name": "selfie.jpg", "bucket": "t-firm"},
                ),
                Row(
                    uid="passuid12345",
                    classification="passport",
                    issuing_country="GB",
                    side="front",
                    object_key="files/p.png",
                    body={"file_name": "passport-front.png", "bucket": "t-firm"},
                ),
            ],
            None,
            None,
            None,
            Row(created_at="2026-09-24T12:00:00+00:00", updated_at="2026-09-24T12:00:00+00:00"),
        ]
    )
    store = LiveStore(session, screening=screening)  # type: ignore[arg-type]
    check = await store.create_check("clientuid", CheckCreate(kind="identity"), tenant_id=7)
    assert check.status == "pending"
    assert check.kind == "identity"
    assert "/documents" in seen
    assert "/documents/doc_1/upload/front" in seen
    assert "/documents/doc_1/upload/back" not in seen
    assert "/livePhotos" in seen
    assert "/checks" in seen
    inserted = next(
        params for statement, params in session.executed if "compliance.checks" in statement
    )
    assert inserted["provider_check_id"] == "chk_id"
    provider_document = next(
        params
        for statement, params in session.executed
        if statement == str(UPSERT_PROVIDER_DOCUMENT)
    )
    assert provider_document["provider_document_id"] == "doc_1"
    assert provider_document["object_key"] == "files/p.png"
    provider_photo = next(
        params for statement, params in session.executed if statement == str(UPSERT_PROVIDER_PHOTO)
    )
    assert provider_photo["provider_photo_id"] == "photo_1"
    assert provider_photo["object_key"] == "files/s.jpg"


async def test_patch_revalidates_address_company_and_identity() -> None:
    from external.models import ClientPatch

    session = Scripted([_client_row(), None, None, None])
    store = LiveStore(session)  # type: ignore[arg-type]
    updated = await store.patch_client(
        "clientuid",
        ClientPatch(
            address={"line": "2 Road", "city": "York", "country": "IE"},  # type: ignore[arg-type]
            company={"name": "Analytical Engines Ltd"},  # type: ignore[arg-type]
            identities=[{"type": "national_insurance_number", "identifier": "QQ123456C"}],  # type: ignore[arg-type]
        ),
        tenant_id=7,
    )
    assert updated is not None
    assert updated.address is not None
    assert updated.address.line == "2 Road"
    assert updated.address.country == "IE"
    assert updated.company is not None
    assert updated.company.name == "Analytical Engines Ltd"
    assert updated.identities is not None
    assert updated.identities[0].identifier == "QQ123456C"
    address = next(
        params
        for statement, params in session.executed
        if "compliance.addresses" in statement and "INSERT" in statement
    )
    assert address["line"] == "2 Road"
    assert address["country"] == "IE"
    assert address["tenant_id"] == 7


def test_client_create_still_round_trips_through_the_model() -> None:
    body = ClientCreate(
        email="ada@analytical.example",
        first_name="Ada",
        last_name="Lovelace",
        dob="1815-12-10",  # type: ignore[arg-type]
        nationality=["GB"],
        address={"country": "GB"},  # type: ignore[arg-type]
    )
    assert body.type == "person"
    assert body.nationality == ["GB"]
    assert body.address is not None
    assert body.address.country == "GB"


async def test_screening_receiver_only_stores_the_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The receiver stores the inbox row in one transaction and applies nothing.

    The worker's consumer fetches from the provider later, so a provider error can never roll
    the stored event back or hold the webhook's 200.
    """
    order: list[str] = []

    @asynccontextmanager
    async def fake_session():  # type: ignore[no-untyped-def]
        order.append("begin")
        try:
            yield object()
            order.append("commit")
        except Exception:
            order.append("rollback")
            raise

    async def fake_accept(session: object, event: dict[str, Any]) -> int:
        del session, event
        order.append("insert")
        return 1

    monkeypatch.setattr("external.db.service_session", fake_session)
    monkeypatch.setattr("external.db.accept_event", fake_accept)

    await accept_screening({"id": "evt_1", "type": "check.completed", "payload": {}})

    assert order == ["begin", "insert", "commit"]


class _CheckSession:
    """Returns a client row, an empty document list, or a check stamp from the SQL text."""

    def __init__(self, client: Row) -> None:
        self.client = client
        self.executed: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, params: Any = None) -> Result:
        sql = str(statement)
        self.executed.append((sql, dict(params or {})))
        if "INSERT INTO compliance.checks" in sql:
            return Result(
                Row(
                    created_at="2026-09-24T12:00:00+00:00",
                    updated_at="2026-09-24T12:00:00+00:00",
                )
            )
        if "FROM compliance.clients cl" in sql:
            return Result(self.client)
        if "compliance.documents" in sql:
            return Result([])
        return Result(None)


def _session_boundary(sessions: list[_CheckSession]):  # type: ignore[no-untyped-def]
    order: list[str] = []

    @asynccontextmanager
    async def fake_session():  # type: ignore[no-untyped-def]
        session = sessions.pop(0)
        order.append("begin")
        try:
            yield session
            order.append("commit")
        except Exception:
            order.append("rollback")
            raise

    return fake_session, order


async def test_provider_client_id_commits_before_address_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        seen.append(f"{request.method} {path}?{request.url.query.decode()}")
        if request.method == "GET" and path == "/clients":
            return httpx.Response(200, json={"items": []})
        if request.method == "POST" and path == "/clients":
            return httpx.Response(200, json={"id": "cli_new"})
        if path == "/addresses":
            return httpx.Response(200, json={"id": "addr_1"})
        if path == "/checks":
            return httpx.Response(200, json={"id": "chk_aml", "status": "pending"})
        return httpx.Response(404, json={"message": "missing"})

    sessions = [
        _CheckSession(_client_row()),
        _CheckSession(_client_row(provider_client_id="cli_new")),
    ]
    fake_session, boundaries = _session_boundary(sessions)

    def _note(request: httpx.Request) -> httpx.Response:
        boundaries.append(f"http {request.method} {request.url.path.removeprefix('/v1')}")
        return handler(request)

    monkeypatch.setattr("external.db.service_session", fake_session)
    store = RequestBoundStore(
        screening=ScreeningClient("test-key", transport=httpx.MockTransport(_note))
    )
    check = await store.create_check("clientuid", CheckCreate(kind="aml"), tenant_id=7)
    assert check.kind == "aml"
    assert any("externalId=contactuid12" in item for item in seen)
    assert boundaries.index("commit") < boundaries.index("http POST /addresses")
    created = [item for item in seen if item.startswith("POST /clients")]
    assert created == ["POST /clients?"]


async def test_retry_reuses_the_provider_client_found_by_contact_uid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        seen.append(f"{request.method} {path}?{request.url.query.decode()}")
        if request.method == "GET" and path == "/clients":
            return httpx.Response(
                200,
                # Made by the first try, so stamped with this deployment's name.
                json={
                    "items": [
                        {
                            "id": "cli_existing",
                            "externalId": "contactuid12",
                            "metadata": {"graphene_env": environment()},
                        }
                    ]
                },
            )
        if path == "/addresses":
            return httpx.Response(200, json={"id": "addr_1"})
        if path == "/checks":
            body = json.loads(request.content)
            assert body["clientId"] == "cli_existing"
            return httpx.Response(200, json={"id": "chk_aml", "status": "pending"})
        return httpx.Response(404, json={"message": "missing"})

    sessions = [
        _CheckSession(_client_row()),
        _CheckSession(_client_row(provider_client_id="cli_existing")),
    ]
    fake_session, boundaries = _session_boundary(sessions)

    def _note(request: httpx.Request) -> httpx.Response:
        boundaries.append(f"http {request.method} {request.url.path.removeprefix('/v1')}")
        return handler(request)

    monkeypatch.setattr("external.db.service_session", fake_session)
    store = RequestBoundStore(
        screening=ScreeningClient("test-key", transport=httpx.MockTransport(_note))
    )
    await store.create_check("clientuid", CheckCreate(kind="aml"), tenant_id=7)
    assert not any(item.startswith("POST /clients") for item in seen)
    assert any("externalId=contactuid12" in item for item in seen)
    assert boundaries.index("commit") < boundaries.index("http POST /addresses")


def _provider_clients(
    found: list[dict[str, Any]],
) -> tuple[ScreeningClient, list[str], list[dict[str, Any]]]:
    """A provider whose client search answers ``found`` (whatever the query) and whose create
    answers ``cli_new``: the client, the requests it saw, and the bodies it was sent to create."""
    seen: list[str] = []
    created: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        seen.append(f"{request.method} {path}?{request.url.query.decode()}")
        if request.method == "GET" and path == "/clients":
            return httpx.Response(200, json={"items": found})
        if request.method == "POST" and path == "/clients":
            created.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "cli_new"})
        return httpx.Response(404, json={"message": "missing"})

    return ScreeningClient("test-key", transport=httpx.MockTransport(handler)), seen, created


@pytest.mark.parametrize("env", ["staging", ""])
async def test_a_partner_provider_client_is_stamped_with_the_deployment_and_the_firm(
    monkeypatch: pytest.MonkeyPatch, env: str
) -> None:
    """The create carries our ids and the firm's, never the firm's own metadata. The search asks
    for this deployment's clients only when the deployment has a name."""
    from app.config import settings

    monkeypatch.setattr(settings, "app_stage", env)
    screening, seen, created = _provider_clients([])
    session = _CheckSession(
        _client_row(
            tenant_uid="ten_firm01",
            tenant_name="Analytical Advisers",
            data={"reference": "FIRM-REF-1", "metadata": {"crm_id": "crm-9"}},
        )
    )
    store = LiveStore(session, screening=screening)  # type: ignore[arg-type]
    assert await store.bind_provider_client("clientuid", CheckCreate(kind="aml"), tenant_id=7)
    expected = {
        "graphene_origin": "partner",
        "graphene_firm_uid": "ten_firm01",
        "graphene_firm": "Analytical Advisers",
        "graphene_contact_uid": "contactuid12",
        "graphene_client_uid": "clientuid",
        "graphene_firm_reference": "FIRM-REF-1",
    }
    if env:
        expected["graphene_env"] = env
    assert len(created) == 1
    assert created[0]["metadata"] == expected
    assert created[0]["externalId"] == "contactuid12"
    assert "crm_id" not in json.dumps(created[0])
    search = next(item for item in seen if item.startswith("GET /clients"))
    assert "externalId=contactuid12" in search
    if env:
        assert "metadata.graphene_env=staging" in search
    else:
        assert "metadata." not in search
    stored = next(
        params for statement, params in session.executed if "provider_client_id" in params
    )
    assert stored["provider_client_id"] == "cli_new"


async def test_a_partner_check_does_not_adopt_another_deployments_provider_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ComplyCube does not keep ``externalId`` unique and the sandbox key is shared, so a client
    with this contact uid stamped by another deployment is not ours: a new one is created."""
    from app.config import settings

    monkeypatch.setattr(settings, "app_stage", "staging")
    screening, seen, created = _provider_clients(
        [{"id": "cli_dev", "externalId": "contactuid12", "metadata": {"graphene_env": "dev"}}]
    )
    session = _CheckSession(_client_row())
    store = LiveStore(session, screening=screening)  # type: ignore[arg-type]
    assert await store.bind_provider_client("clientuid", CheckCreate(kind="aml"), tenant_id=7)
    assert len(created) == 1
    assert created[0]["metadata"]["graphene_env"] == "staging"
    stored = next(
        params for statement, params in session.executed if "provider_client_id" in params
    )
    assert stored["provider_client_id"] == "cli_new"
    assert any("metadata.graphene_env=staging" in item for item in seen)


async def test_webhook_secret_is_stored_as_ciphertext(monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.fernet import Fernet

    from app.config import settings
    from app.crypto import decrypt

    monkeypatch.setattr(settings, "secrets_master_key", Fernet.generate_key().decode())
    session = Scripted([None])
    store = LiveStore(session, resolver=_public)  # type: ignore[arg-type]
    saved = await store.put_webhook("https://firm.example/hooks", tenant_id=7)
    params = session.executed[0][1]
    stored = params["secret_ct"]
    assert stored != saved.secret
    assert decrypt(stored) == saved.secret
    assert "secret" not in params or params.get("secret") != saved.secret


def test_webhook_secret_is_absent_from_the_authenticated_grant_and_the_audit_trail() -> None:
    sql = (API / "migrations" / "sql" / "0186_compliance.sql").read_text(encoding="utf-8")
    webhooks = sql.split("CREATE TABLE compliance.webhooks", 1)[1].split("CREATE OR REPLACE", 1)[0]
    assert "secret_ct" in webhooks
    assert "secret text" not in webhooks
    assert "GRANT SELECT (id, tenant_id, url, created_at, updated_at)" in sql
    granted = sql.split("GRANT SELECT (id, tenant_id, url, created_at, updated_at)", 1)[1]
    assert "secret_ct" not in granted.split("TO authenticated", 1)[0]
    redact = sql.split("v_redact", 1)[1].split(";", 1)[0]
    assert "'secret_ct'" in redact


async def test_retry_uploads_the_address_when_the_provider_client_has_none() -> None:
    """A saved provider client id must not skip the address after a failed upload."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        seen.append(f"{request.method} {path}?{request.url.query.decode()}")
        if request.method == "GET" and path == "/addresses":
            return httpx.Response(200, json={"items": []})
        if request.method == "POST" and path == "/addresses":
            return httpx.Response(200, json={"id": "addr_1"})
        if path == "/checks":
            return httpx.Response(200, json={"id": "chk_aml", "status": "pending"})
        return httpx.Response(404, json={"message": "missing"})

    screening = ScreeningClient("test-key", transport=httpx.MockTransport(handler))
    session = Scripted(
        [
            _client_row(provider_client_id="cli_saved"),
            Row(created_at="2026-09-24T12:00:00+00:00", updated_at="2026-09-24T12:00:00+00:00"),
        ]
    )
    store = LiveStore(session, screening=screening)  # type: ignore[arg-type]
    await store.create_check(
        "clientuid",
        CheckCreate(kind="aml"),
        tenant_id=7,
        upload_address=False,
        already_bound=True,
    )
    assert any(item.startswith("POST /addresses") for item in seen)
    posted = next(item for item in seen if item.startswith("POST /addresses"))
    started = next(item for item in seen if item.startswith("POST /checks"))
    assert seen.index(posted) < seen.index(started)


async def test_aml_does_not_start_when_the_provider_address_upload_fails() -> None:
    from external.store import Rejected

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        seen.append(f"{request.method} {path}")
        if request.method == "GET" and path == "/addresses":
            return httpx.Response(200, json={"items": []})
        if request.method == "POST" and path == "/addresses":
            return httpx.Response(503, json={"message": "down"})
        if path == "/checks":
            return httpx.Response(200, json={"id": "chk_aml", "status": "pending"})
        return httpx.Response(404, json={"message": "missing"})

    screening = ScreeningClient("test-key", transport=httpx.MockTransport(handler))
    session = Scripted([_client_row(provider_client_id="cli_saved")])
    store = LiveStore(session, screening=screening)  # type: ignore[arg-type]
    with pytest.raises(Rejected):
        await store.create_check(
            "clientuid",
            CheckCreate(kind="aml"),
            tenant_id=7,
            upload_address=False,
            already_bound=True,
        )
    assert not any(item.startswith("POST /checks") for item in seen)


async def test_aml_starts_when_the_address_is_country_only() -> None:
    """A country is enough for AML. A missing street must not refuse the check."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        seen.append(f"{request.method} {path}")
        if request.method == "GET" and path == "/addresses":
            return httpx.Response(200, json={"items": []})
        if request.method == "POST" and path == "/addresses":
            return httpx.Response(200, json={"id": "addr_1"})
        if path == "/checks":
            return httpx.Response(200, json={"id": "chk_aml", "status": "pending"})
        return httpx.Response(404, json={"message": "missing"})

    screening = ScreeningClient("test-key", transport=httpx.MockTransport(handler))
    session = Scripted(
        [
            _client_row(
                provider_client_id="cli_saved",
                address={"country": "GB", "body": {}},
            ),
            Row(created_at="2026-09-24T12:00:00+00:00", updated_at="2026-09-24T12:00:00+00:00"),
        ]
    )
    store = LiveStore(session, screening=screening)  # type: ignore[arg-type]
    check = await store.create_check(
        "clientuid",
        CheckCreate(kind="aml"),
        tenant_id=7,
        upload_address=False,
        already_bound=True,
    )
    assert check.status == "pending"
    assert check.country == "GB"
    assert not any(item.startswith("POST /addresses") for item in seen)
    assert any(item.startswith("POST /checks") for item in seen)


async def test_a_cached_key_is_refused_when_the_firm_is_suspended() -> None:
    lookups: list[str] = []
    enabled = {"ok": True}

    async def lookup(key_hash: str) -> CachedKey:
        lookups.append(key_hash)
        return CachedKey(found=True, uid="keyuid", tenant_id=7, scopes=("clients:read",))

    async def touch(record: CachedKey) -> None:
        del record

    async def tenant_enabled(tenant_id: int) -> bool:
        assert tenant_id == 7
        return enabled["ok"]

    verifier = KeyVerifier(lookup, touch, tenant_enabled=tenant_enabled)
    assert (await verifier.authenticate("gpk_live_one")) is not None
    enabled["ok"] = False
    suspended = await verifier.authenticate("gpk_live_one")
    assert suspended is not None and suspended.suspended
    assert len(lookups) == 1


async def test_create_check_adopts_the_row_a_webhook_stored_first() -> None:
    """The provider's webhook can land before the insert commits. The insert then keeps the row
    the consumer stored, with its uid and the status the provider already reported."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        if request.method == "GET" and path == "/addresses":
            return httpx.Response(200, json={"items": [{"id": "addr_1"}]})
        if path == "/checks":
            return httpx.Response(200, json={"id": "chk_aml", "status": "pending"})
        return httpx.Response(404, json={"message": "missing"})

    screening = ScreeningClient("test-key", transport=httpx.MockTransport(handler))
    session = Scripted(
        [
            _client_row(provider_client_id="cli_saved"),
            Row(
                uid="storedfirst1",
                status="clear",
                created_at="2026-09-24T12:00:00+00:00",
                updated_at="2026-09-24T12:01:00+00:00",
            ),
        ]
    )
    store = LiveStore(session, screening=screening)  # type: ignore[arg-type]
    check = await store.create_check(
        "clientuid", CheckCreate(kind="aml"), tenant_id=7, already_bound=True
    )
    assert check.uid == "storedfirst1"
    assert check.status == "clear"
    sql = " ".join(str(INSERT_CHECK).split())
    assert "ON CONFLICT (provider_check_id) WHERE provider_check_id IS NOT NULL" in sql
    # Another firm's row is never claimed.
    assert "compliance.checks.tenant_id = EXCLUDED.tenant_id" in sql


async def test_create_check_refuses_when_the_provider_id_is_another_firms_row() -> None:
    from external.store import Rejected

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        if request.method == "GET" and path == "/addresses":
            return httpx.Response(200, json={"items": [{"id": "addr_1"}]})
        if path == "/checks":
            return httpx.Response(200, json={"id": "chk_aml", "status": "pending"})
        return httpx.Response(404, json={"message": "missing"})

    screening = ScreeningClient("test-key", transport=httpx.MockTransport(handler))
    session = Scripted([_client_row(provider_client_id="cli_saved"), None])
    store = LiveStore(session, screening=screening)  # type: ignore[arg-type]
    with pytest.raises(Rejected) as raised:
        await store.create_check(
            "clientuid", CheckCreate(kind="aml"), tenant_id=7, already_bound=True
        )
    assert raised.value.status == 502


async def test_a_blank_screening_key_fails_the_check_closed_without_a_request() -> None:
    from external.store import Rejected

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={})

    screening = ScreeningClient("", transport=httpx.MockTransport(handler))
    session = Scripted([_client_row(provider_client_id="cli_saved")])
    store = LiveStore(session, screening=screening)  # type: ignore[arg-type]
    with pytest.raises(Rejected) as raised:
        await store.create_check(
            "clientuid", CheckCreate(kind="aml"), tenant_id=7, already_bound=True
        )
    assert raised.value.code == "check_unavailable"
    assert seen == []


def test_a_check_the_provider_could_not_run_reads_as_pending_to_the_firm() -> None:
    failed = check_from_row(
        Row(
            uid="checkuidfail",
            kind="aml",
            status="failed",
            created_at="2026-09-24T12:00:00+00:00",
            updated_at="2026-09-24T12:05:00+00:00",
            name="Ada Lovelace",
            dob=None,
            country="GB",
        )
    )
    assert failed.status == "pending"


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16


def test_an_upload_is_named_and_typed_from_its_own_bytes() -> None:
    from external.models import DocumentUpload, held_document

    def held(content: bytes, **fields: Any) -> Any:
        return held_document(
            DocumentUpload(file=base64.b64encode(content).decode("ascii"), **fields)
        )

    passport = held(PNG, type="passport", issuing_country="GB")
    assert (passport.file_name, passport.content_type, passport.side) == (
        "passport-front.png",
        "image/png",
        "front",
    )
    back = held(JPEG, type="drivers_license", issuing_country="GB", side="back")
    assert (back.file_name, back.content_type, back.side) == (
        "drivers_license-back.jpg",
        "image/jpeg",
        "back",
    )
    selfie = held(JPEG, type="selfie")
    assert selfie.file_name == "selfie.jpg"
    unknown = held(b"\x00\x01\x02\x03binary", type="selfie")
    assert (unknown.file_name, unknown.content_type) == ("selfie.bin", "application/octet-stream")


async def test_a_document_is_kept_in_object_storage_and_not_in_the_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import tenant_storage
    from external.models import HeldDocument

    put: list[tuple[str, bytes, str, str | None]] = []

    async def put_document(
        key: str, data: bytes, content_type: str, *, bucket: str | None = None
    ) -> None:
        put.append((key, data, content_type, bucket))

    async def document_bucket(session: Any, tenant_id: int | None = None) -> str:
        assert tenant_id == 7
        return "t-firm"

    monkeypatch.setattr(storage, "put_document", put_document)
    monkeypatch.setattr(tenant_storage, "document_bucket", document_bucket)
    session = Scripted([Row(id=3, contact_id=9, contact_uid="contactuid12"), None])
    store = LiveStore(session)  # type: ignore[arg-type]
    out = await store.create_document(
        "clientuid",
        HeldDocument(
            type="drivers_license",
            issuing_country="GB",
            content=PNG,
            file_name="drivers_license-back.png",
            content_type="image/png",
            side="back",
        ),
        tenant_id=7,
    )
    assert out.side == "back"
    assert out.type == "drivers_license"
    key = f"files/clients/contactuid12/{out.uid}/original.png"
    assert put == [(key, PNG, "image/png", "t-firm")]
    statement, params = session.executed[-1]
    assert "INSERT INTO compliance.documents" in statement
    assert params["object_key"] == key
    assert params["side"] == "back"
    assert params["tenant_id"] == 7
    body = json.loads(params["body"])
    assert body == {
        "file_name": "drivers_license-back.png",
        "content_type": "image/png",
        "size_bytes": len(PNG),
        "bucket": "t-firm",
    }
    assert base64.b64encode(PNG).decode("ascii") not in params["body"]


@pytest.mark.parametrize(
    "failure",
    [storage.StorageUnavailable("not configured"), RuntimeError("NoSuchBucket not configured")],
)
async def test_a_document_is_refused_when_object_storage_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    from app import tenant_storage
    from external.models import HeldDocument
    from external.store import Rejected

    async def unavailable(*args: Any, **kwargs: Any) -> None:
        raise failure

    async def no_bucket(session: Any, tenant_id: int | None = None) -> str:
        return ""

    monkeypatch.setattr(storage, "put_document", unavailable)
    monkeypatch.setattr(tenant_storage, "document_bucket", no_bucket)
    session = Scripted([Row(id=3, contact_id=9, contact_uid="contactuid12"), None])
    with pytest.raises(Rejected) as refused:
        await LiveStore(session).create_document(  # type: ignore[arg-type]
            "clientuid",
            HeldDocument(
                type="selfie",
                issuing_country=None,
                content=JPEG,
                file_name="selfie.jpg",
                content_type="image/jpeg",
            ),
            tenant_id=7,
        )
    assert (refused.value.code, refused.value.status) == ("storage_unavailable", 503)
    assert "not configured" not in refused.value.message


def _held(uid: str, kind: str, side: str = "front", country: str | None = "GB") -> HeldFile:
    return HeldFile(
        uid=uid,
        kind=kind,
        side=side,
        issuing_country=None if kind == "selfie" else country,
        object_key=f"files/{uid}",
        body={},
    )


def test_an_identity_check_takes_the_newest_selfie_and_front_and_that_documents_back() -> None:
    from external.store import Rejected

    files = [
        _held("selfie_old", "selfie"),
        _held("licence_front", "drivers_license"),
        _held("licence_back_ie", "drivers_license", "back", "IE"),
        _held("licence_back", "drivers_license", "back"),
        _held("passport_back_mismatch", "identity", "back"),
        _held("selfie_new", "selfie"),
    ]
    chosen = identity_set(files)
    assert chosen.selfie.uid == "selfie_new"
    assert chosen.front.uid == "licence_front"
    assert chosen.back is not None and chosen.back.uid == "licence_back"

    newer_passport = identity_set([*files, _held("passport", "passport")])
    assert newer_passport.front.uid == "passport"
    assert newer_passport.back is None

    for missing in (
        [_held("s", "selfie"), _held("b", "drivers_license", "back")],
        [_held("f", "drivers_license")],
    ):
        with pytest.raises(Rejected) as refused:
            identity_set(missing)
        assert refused.value.code == "documents_required"


async def test_an_identity_check_sends_the_back_with_the_front(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uploads: dict[str, dict[str, Any]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        body = json.loads(request.content) if request.content else {}
        if path == "/clients":
            return httpx.Response(200, json={"id": "cli_1"})
        if path == "/addresses":
            return httpx.Response(200, json={"items": [{"id": "addr_1"}]})
        if path == "/documents":
            assert body["type"] == "driving_license"
            return httpx.Response(200, json={"id": "doc_1"})
        if path.startswith("/documents/doc_1/upload/"):
            uploads[path.rsplit("/", 1)[-1]] = body
            return httpx.Response(200, json={"id": "img"})
        if path == "/livePhotos":
            return httpx.Response(200, json={"id": "photo_1"})
        if path == "/checks":
            return httpx.Response(200, json={"id": "chk_id", "status": "pending"})
        return httpx.Response(404, json={"message": "missing"})

    objects = {"files/s": JPEG, "files/f": PNG, "files/b": b"\xff\xd8\xffback"}

    async def get_document(key: str, *, bucket: str | None = None) -> tuple[bytes, str]:
        return objects[key], "image/jpeg"

    monkeypatch.setattr(storage, "get_document", get_document)

    def doc(uid: str, kind: str, side: str, key: str, name: str) -> Row:
        return Row(
            uid=uid,
            classification=kind,
            issuing_country=None if kind == "selfie" else "GB",
            side=side,
            object_key=key,
            body={"file_name": name, "bucket": "t-firm"},
        )

    session = Scripted(
        [
            _client_row(provider_client_id="cli_1"),
            [
                doc("selfieuid123", "selfie", "front", "files/s", "selfie.jpg"),
                doc("frontuid1234", "drivers_license", "front", "files/f", "licence-front.png"),
                doc("backuid12345", "drivers_license", "back", "files/b", "licence-back.jpg"),
            ],
            None,
            None,
            Row(created_at="2026-09-24T12:00:00+00:00", updated_at="2026-09-24T12:00:00+00:00"),
        ]
    )
    screening = ScreeningClient("test-key", transport=httpx.MockTransport(handler))
    store = LiveStore(session, screening=screening)  # type: ignore[arg-type]
    check = await store.create_check(
        "clientuid", CheckCreate(kind="identity"), tenant_id=7, already_bound=True
    )
    assert set(uploads) == {"front", "back"}
    assert uploads["front"] == {
        "fileName": "licence-front.png",
        "data": base64.b64encode(PNG).decode("ascii"),
    }
    assert uploads["back"] == {
        "fileName": "licence-back.jpg",
        "data": base64.b64encode(objects["files/b"]).decode("ascii"),
    }
    assert check.document_uids == ["selfieuid123", "frontuid1234", "backuid12345"]
    assert check.issuing_country == "GB"


async def test_a_row_written_before_object_storage_still_submits() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        if path == "/addresses":
            return httpx.Response(200, json={"items": [{"id": "addr_1"}]})
        if path == "/documents":
            return httpx.Response(200, json={"id": "doc_1"})
        if path == "/livePhotos":
            return httpx.Response(200, json={"id": "photo_1"})
        if path == "/checks":
            return httpx.Response(200, json={"id": "chk_legacy", "status": "pending"})
        return httpx.Response(200, json={"id": "img"})

    legacy = base64.b64encode(b"legacy-passport").decode("ascii")
    session = Scripted(
        [
            _client_row(provider_client_id="cli_1"),
            [
                Row(
                    uid="selfieuid123",
                    classification="selfie",
                    issuing_country=None,
                    side=None,
                    object_key=None,
                    body={"file_name": "selfie.jpg", "data": legacy},
                ),
                Row(
                    uid="passuid12345",
                    classification="passport",
                    issuing_country="GB",
                    side=None,
                    object_key=None,
                    body={"file_name": "front.jpg", "data": legacy},
                ),
            ],
            None,
            None,
            Row(created_at="2026-09-24T12:00:00+00:00", updated_at="2026-09-24T12:00:00+00:00"),
        ]
    )
    screening = ScreeningClient("test-key", transport=httpx.MockTransport(handler))
    store = LiveStore(session, screening=screening)  # type: ignore[arg-type]
    check = await store.create_check(
        "clientuid", CheckCreate(kind="identity"), tenant_id=7, already_bound=True
    )
    assert check.document_uids == ["selfieuid123", "passuid12345"]
    inserted = next(
        params for statement, params in session.executed if "compliance.checks" in statement
    )
    assert inserted["provider_check_id"] == "chk_legacy"


def test_the_firms_documents_never_include_the_providers_copies() -> None:
    """The provider's document rows (recorded by the event consumer, or by an identity submission
    to hold the firm's object key) carry a provider document id. The firm's own reads skip them,
    so they never appear in ``document_uids`` and are never submitted as the firm's file."""
    assert "provider_document_id IS NULL" in str(SELECT_DOCUMENTS)
    for statement in (SELECT_CHECK, SELECT_CHECKS):
        documents = str(statement).split("AS country", 1)[1].split("AS documents", 1)[0]
        assert "d.provider_document_id IS NULL" in documents
