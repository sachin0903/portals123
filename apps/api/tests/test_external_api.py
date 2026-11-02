"""Firm-facing API contract, exercised against the in-memory stub."""

from __future__ import annotations

import base64
import re
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlparse

from httpx import Response
from litestar.testing import TestClient

from app.ids import ALPHABET, ID_LENGTH
from external.app import create_app
from external.html import BUNDLE, GUIDE_ORDER, GUIDES, SCALAR_SCRIPT, SCALAR_VERSION

AUTH = {"Authorization": "Bearer test_dummy"}
PERSON = {
    "first_name": "Ada",
    "last_name": "Lovelace",
    "dob": "1815-12-10",
    "email": "ada@analytical.example",
    "nationality": ["GB"],
    "address": {"country": "GB", "line": "1 Analytical Engine", "city": "London"},
}


_IMAGE = base64.b64encode(b"not-returned-image").decode("ascii")


def _upload(
    client: TestClient,
    uid: str,
    doc_type: str,
    *,
    country: str | None = None,
    file: str | None = _IMAGE,
) -> Response:
    body: dict[str, object] = {"type": doc_type}
    if country is not None:
        body["issuing_country"] = country
    if file is not None:
        body["file"] = file
    return client.post(f"/clients/{uid}/documents", json=body, headers=AUTH)


def _client(**kwargs: object) -> TestClient:
    return TestClient(create_app(**kwargs))  # type: ignore[arg-type]


def _assert_public_id(value: object) -> None:
    assert isinstance(value, str)
    assert len(value) == ID_LENGTH
    assert set(value) <= set(ALPHABET)


def test_missing_version_is_latest_and_echoed() -> None:
    with _client() as client:
        response = client.post("/clients", json=PERSON, headers=AUTH)
    assert response.status_code == 201
    assert response.headers["x-version"] == "v1"


def test_unknown_version_is_400_and_not_echoed() -> None:
    with _client() as client:
        response = client.get("/clients", headers={**AUTH, "X-Version": "v9"})
    assert response.status_code == 400
    assert response.json()["code"] == "unknown_version"
    assert response.headers.get("x-version") != "v9"


def test_required_person_fields_are_enforced() -> None:
    body = {key: value for key, value in PERSON.items() if key != "first_name"}
    with _client() as client:
        response = client.post("/clients", json=body, headers=AUTH)
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_body"
    assert "first_name" in response.json()["message"]


def test_optional_client_fields_round_trip() -> None:
    body = {
        **PERSON,
        "middle_name": "Augusta",
        "gender": "female",
        "nationality": "GB",
        "birth_country": "GB",
        "identities": [{"type": "national_insurance_number", "identifier": "QQ123456C"}],
        "mobile": "+447700900123",
        "telephone": "02079460123",
        "tax_residency": ["GB"],
        "reference": "firm-1",
        "metadata": {"desk": "north"},
        "address": {
            "property_number": "12",
            "building_name": "Engine House",
            "line": "1 Analytical Engine",
            "city": "London",
            "state": "London",
            "postal_code": "SW1A 1AA",
            "country": "GB",
            "from_date": "1815-12-10",
        },
    }
    with _client() as client:
        created = client.post("/clients", json=body, headers=AUTH)
        assert created.status_code == 201
        uid = created.json()["uid"]
        fetched = client.get(f"/clients/{uid}", headers=AUTH)
    assert fetched.status_code == 200
    got = fetched.json()
    assert got["middle_name"] == "Augusta"
    assert got["gender"] == "female"
    assert got["nationality"] == ["GB"]
    assert got["birth_country"] == "GB"
    assert got["identities"] == [{"type": "national_insurance_number", "identifier": "QQ123456C"}]
    assert "joined_date" not in got
    assert "ssn" not in got
    assert got["metadata"] == {"desk": "north"}
    assert got["reference"] == "firm-1"
    assert got["address"]["property_number"] == "12"
    assert got["address"]["country"] == "GB"
    assert got["address"]["from_date"] == "1815-12-10"
    assert "type" not in got["address"]
    assert "to_date" not in got["address"]


def test_address_country_is_required_and_a_person_still_needs_an_address() -> None:
    missing_country = {
        **PERSON,
        "address": {"line": "1 Analytical Engine", "city": "London"},
    }
    missing_address = {key: value for key, value in PERSON.items() if key != "address"}
    with _client() as client:
        without_country = client.post("/clients", json=missing_country, headers=AUTH)
        without_address = client.post("/clients", json=missing_address, headers=AUTH)
    assert without_country.status_code == 400
    assert without_country.json()["code"] == "invalid_body"
    assert "country" in without_country.json()["message"]
    assert without_address.status_code == 400
    assert "address" in without_address.json()["message"]


def test_aml_without_an_address_country_is_422() -> None:
    company = {
        "type": "company",
        "email": "ada@analytical.example",
        "company": {"name": "Analytical Engines", "incorporation_country": "GB"},
    }
    with _client() as client:
        created = client.post("/clients", json=company, headers=AUTH)
        assert created.status_code == 201
        response = client.post(
            f"/clients/{created.json()['uid']}/checks",
            json={"kind": "aml"},
            headers=AUTH,
        )
    assert response.status_code == 422
    assert response.json()["code"] == "address_country_required"


def test_retired_identity_fields_are_rejected() -> None:
    with _client() as client:
        response = client.post(
            "/clients",
            json={**PERSON, "ssn": "123-45-6789", "joined_date": "2020-01-02", "identity": {}},
            headers=AUTH,
        )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_body"


def test_unknown_fields_are_rejected() -> None:
    with _client() as client:
        response = client.post("/clients", json={**PERSON, "workflow": "hosted"}, headers=AUTH)
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_body"


def test_openapi_groups_the_reference_and_pages_the_guides() -> None:
    with _client() as client:
        response = client.get("/schema/openapi.json")
        reference = client.get("/docs")
    assert response.status_code == 200
    document = response.json()
    text = response.text
    description = document["info"]["description"]
    assert [tag["name"] for tag in document["tags"]] == [
        "Health",
        "Clients",
        "Checks",
        "Webhooks",
        "Custodians",
    ]
    by_name = {tag["name"]: tag for tag in document["tags"]}
    assert "parent" not in by_name["Clients"]
    assert "parent" not in by_name["Checks"]
    assert "POST /clients" in by_name["Clients"]["description"]
    assert "address_country_required" in by_name["Checks"]["description"]
    assert "Client guide" not in description
    assert "# Clients" not in description
    assert "# Checks" not in description
    for tag in document["tags"]:
        assert tag["description"].strip()
    headings = [line[2:].strip() for line in description.splitlines() if line.startswith("# ")]
    expected = []
    for slug in GUIDE_ORDER:
        for line in (GUIDES / f"{slug}.md").read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                expected.append(line[2:].strip())
                break
    assert headings == expected
    assert "Authorization: Bearer <key>" in description
    assert "live_" in description and "test_" in description
    assert "gpk_live_" not in description and "gpk_test_" not in description
    assert "test_dummy" in description
    assert "10 requests per second" in description
    scheme = document["components"]["securitySchemes"]["bearerAuth"]
    assert scheme["type"] == "http"
    assert scheme["scheme"] == "bearer"
    assert document["security"] == [{"bearerAuth": []}]
    seen: set[str] = set()
    for path, item in document["paths"].items():
        assert not path.startswith("/docs")
        for method, operation in item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            assert operation["summary"].strip()
            assert operation["description"].strip()
            assert not operation["description"].startswith("Requires ")
            assert "Requires clients:" not in operation["description"]
            assert "Requires checks:" not in operation["description"]
            assert "Requires links:" not in operation["description"]
            assert "The Graphene API lets a firm" not in operation["description"]
            assert operation["tags"]
            seen.update(operation["tags"])
            for param in operation.get("parameters") or []:
                assert param.get("description"), (path, method, param.get("name"))
    assert seen == {"Health", "Clients", "Checks", "Webhooks", "Custodians"}
    for schema_name, schema in document["components"]["schemas"].items():
        for name, prop in (schema.get("properties") or {}).items():
            assert prop.get("description"), (schema_name, name)
    assert "ada@analytical.example" in text
    assert "success_url" not in text
    assert "cancel_url" not in text
    assert "redirect_url" not in text
    assert "/clients/{uid}/documents" in document["paths"]
    uploaded = document["paths"]["/clients/{uid}/documents"]["post"]
    assert "nationality" in uploaded["description"]
    assert "multipart" not in uploaded["description"]
    assert "application/json" in uploaded["requestBody"]["content"]
    assert "multipart/form-data" not in uploaded["requestBody"]["content"]
    file_schema = document["components"]["schemas"]["DocumentUpload"]["properties"]["file"]
    assert file_schema["type"] == "string"
    assert file_schema["format"] == "byte"
    assert "URL follows" not in uploaded["responses"]["201"]["description"]
    assert "file_too_large" in uploaded["responses"]["422"]["description"]
    assert "CLT-1001" in text
    create = document["paths"]["/clients"]["post"]["responses"]
    assert {"201", "400", "401", "403", "429"} <= set(create)
    check = document["paths"]["/clients/{uid}/checks"]["post"]["responses"]
    assert "422" in check
    assert "address_country_required" in check["422"]["description"]
    client_props = document["components"]["schemas"]["ClientCreate"]["properties"]
    for retired in (
        "ssn",
        "social_insurance_number",
        "national_identity_number",
        "tax_identification_number",
        "joined_date",
    ):
        assert retired not in client_props
    assert "joined_date" not in text
    identity = document["components"]["schemas"]["Identity"]["properties"]
    assert "UK National Insurance number" in identity["type"]["description"]
    assert set(identity["type"]["enum"]) == {
        "national_insurance_number",
        "ssn",
        "social_insurance_number",
        "tax_identification_number",
        "national_identity_number",
    }
    assert "identities" in client_props
    assert "identity" not in client_props
    gender_values: set[str] = set()
    gender = client_props["gender"]
    gender_values.update(value for value in gender.get("enum") or [] if isinstance(value, str))
    for item in gender.get("anyOf") or []:
        gender_values.update(value for value in item.get("enum") or [] if isinstance(value, str))
    assert gender_values == {"male", "female"}
    assert "secret" not in document["components"]["schemas"]["WebhookOut"]["properties"]
    assert "secret" in document["components"]["schemas"]["WebhookCreated"]["properties"]
    assert document["paths"]["/health"]["get"]["security"] == []
    assert "country" in document["components"]["schemas"]["AddressIn"]["required"]
    assert "addresses" not in client_props
    assert "address" in client_props
    address_props = document["components"]["schemas"]["AddressIn"]["properties"]
    assert "from_date" in address_props
    assert "type" not in address_props
    assert "to_date" not in address_props
    assert "Permissions" not in headings
    assert "clients:read" not in description
    assert "more than one replica" not in text
    assert "50 MB" in uploaded["description"]
    examples = document["paths"]["/clients"]["post"]["requestBody"]["content"]["application/json"][
        "examples"
    ]
    assert set(examples) == {"Minimal Individual", "Individual", "Organisation"}
    assert set(examples["Minimal Individual"]["value"]) == {
        "first_name",
        "last_name",
        "dob",
        "email",
        "nationality",
        "address",
    }
    assert examples["Individual"]["value"]["identities"][0]["type"] == "national_insurance_number"
    assert examples["Organisation"]["value"]["type"] == "company"
    assert examples["Organisation"]["value"]["company"]["name"]
    for name, example in examples.items():
        assert example["summary"] == name

    def response_examples(path: str, method: str, status: str) -> dict:
        body = document["paths"][path][method]["responses"][status]["content"]["application/json"]
        found = body["examples"]
        assert "example" not in body
        for label, example in found.items():
            assert example["summary"] == label
            assert label not in {
                "ClientOut",
                "ClientPage",
                "ChecksOut",
                "CheckOut",
                "DocumentOut",
                "2",
            }
        return found

    created = response_examples("/clients", "post", "201")
    fetched = response_examples("/clients/{uid}", "get", "200")
    assert set(created) == {"Minimal Individual", "Individual", "Organisation"}
    assert set(fetched) == set(created)
    assert created["Minimal Individual"]["value"]["uid"]
    assert created["Individual"]["value"]["identities"][0]["identifier"] == "QQ123456C"
    assert created["Organisation"]["value"]["company"]["name"] == "Analytical Engines Ltd"
    page = response_examples("/clients", "get", "200")
    assert set(page) == {"Individual and organisation"}
    kinds = {item["type"] for item in page["Individual and organisation"]["value"]["items"]}
    assert kinds == {"person", "company"}
    overview = response_examples("/clients/{uid}/checks", "get", "200")
    assert set(overview) == {"Pending", "Identity clear, AML refer"}
    assert overview["Pending"]["value"]["identity"]["status"] == "pending"
    later = overview["Identity clear, AML refer"]["value"]
    assert later["identity"]["status"] == "clear"
    assert later["aml"]["status"] == "refer"
    for path, method, status in (
        ("/checks/{uid}", "get", "200"),
        ("/clients/{uid}/checks", "post", "201"),
    ):
        checks = response_examples(path, method, status)
        assert {item["value"]["kind"] for item in checks.values()} == {"identity", "aml"}
        assert set(checks) == {"Identity check", "AML check"}
    documents = response_examples("/clients/{uid}/documents", "post", "201")
    assert documents["Selfie"]["value"] == {
        "uid": documents["Selfie"]["value"]["uid"],
        "type": "selfie",
        "side": "front",
    }
    assert documents["Driving licence back"]["value"]["side"] == "back"
    assert documents["Passport"]["value"]["type"] == "passport"
    assert documents["Passport"]["value"]["issuing_country"] == "GB"
    assert document["components"]["schemas"]["ClientPage"].get("title") is None
    clients_schema = document["paths"]["/clients"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert "oneOf" not in clients_schema
    assert "account_uid" not in text
    assert "/checks/{uid}/events" not in document["paths"]
    checks_schema = document["components"]["schemas"]["ChecksOut"]["properties"]
    assert set(checks_schema) == {"identity", "aml", "checks"}
    check_fields = document["components"]["schemas"]["CheckOut"]["properties"]
    assert {"uid", "kind", "status", "created_at", "updated_at"} <= set(check_fields)
    assert "document_uids" in check_fields
    assert "issuing_country" in check_fields
    assert {"name", "dob", "country"} <= set(check_fields)
    assert "10 requests" in document["paths"]["/clients"]["get"]["responses"]["429"]["description"]
    assert reference.status_code == 200
    assert "Scalar.createApiReference" in reference.text
    assert "preferredSecurityScheme" in reference.text
    assert "mcp: { disabled: true }" in reference.text
    assert "/docs/guides/" not in reference.text


_SCRIPT_SRC = re.compile(
    r"""<script\b[^>]*\bsrc\s*=\s*(?P<q>['"]?)(?P<src>[^\s'">]+)(?P=q)""",
    re.IGNORECASE,
)
_SCALAR_SHA256 = "5b77383a4b93329c436e6686c6093ad3b3f0686f4d0af0d36f006722884e8bb0"


def test_docs_script_is_served_by_this_process() -> None:
    api_root = Path(__file__).resolve().parents[1]
    assert BUNDLE.is_file()
    assert BUNDLE.is_relative_to(api_root)
    assert "dist" not in BUNDLE.parts
    dockerfile = (api_root / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY apps/api apps/api" in dockerfile
    body = BUNDLE.read_bytes()
    assert sha256(body).hexdigest() == _SCALAR_SHA256
    assert body.startswith(f"/* @scalar/api-reference@{SCALAR_VERSION} */".encode())
    with _client() as client:
        page = client.get("/docs")
        assert page.status_code == 200
        assert "://" not in page.text
        srcs = [match.group("src") for match in _SCRIPT_SRC.finditer(page.text)]
        assert srcs == [SCALAR_SCRIPT]
        for src in srcs:
            parsed = urlparse(src)
            assert parsed.scheme == "" and parsed.netloc == "", src
            assert src.startswith("/")
            assert not src.startswith("//")
        lowered = page.text.lower()
        for host in ("jsdelivr", "unpkg", "cdnjs", "googleapis", "cloudflare"):
            assert host not in lowered
        script = client.get(SCALAR_SCRIPT)
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    assert script.content == body
    assert b"createApiReference" in script.content
    assert b"complycube" not in script.content.lower()


def test_openapi_and_guides_do_not_name_the_provider() -> None:
    with _client() as client:
        schema = client.get("/schema/openapi.json")
        reference = client.get("/docs")
    assert schema.status_code == 200
    assert "complycube" not in schema.text.lower()
    assert "complycube" not in reference.text.lower()
    for path in GUIDES.glob("*.md"):
        assert "complycube" not in path.read_text(encoding="utf-8").lower()


def test_a_person_without_nationality_is_rejected_on_create() -> None:
    body = {key: value for key, value in PERSON.items() if key != "nationality"}
    with _client() as client:
        response = client.post("/clients", json=body, headers=AUTH)
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_body"
    assert "nationality" in response.json()["message"]


def test_a_company_does_not_require_nationality() -> None:
    body = {
        "type": "company",
        "email": "ada@analytical.example",
        "company": {"name": "Analytical Engines", "incorporation_country": "GB"},
    }
    with _client() as client:
        response = client.post("/clients", json=body, headers=AUTH)
    assert response.status_code == 201
    assert response.json()["nationality"] is None


def test_stub_sets_the_stub_header() -> None:
    with _client() as client:
        response = client.post("/clients", json=PERSON, headers=AUTH)
    assert response.headers["x-graphene-stub"] == "true"


def test_check_response_has_no_hit_payload() -> None:
    with _client() as client:
        created = client.post("/clients", json=PERSON, headers=AUTH)
        uid = created.json()["uid"]
        response = client.post(f"/clients/{uid}/checks", json={"kind": "aml"}, headers=AUTH)
        assert response.status_code == 201
        body = response.json()
        assert set(body) == {
            "uid",
            "kind",
            "status",
            "created_at",
            "updated_at",
            "name",
            "dob",
            "country",
        }
        assert "result" not in body
        assert "hits" not in response.text.lower()
        assert "complycube" not in response.text.lower()
        assert "url" not in body
        assert "account_uid" not in body
        assert "client_uid" not in body
        assert body["status"] == "pending"
        assert body["name"] == "Ada Lovelace"
        assert body["dob"] == "1815-12-10"
        assert body["country"] == "GB"
        fetched = client.get(f"/checks/{body['uid']}", headers=AUTH)
        assert fetched.status_code == 200
        assert fetched.json() == body
        overview = client.get(f"/clients/{uid}/checks", headers=AUTH)
    assert overview.status_code == 200
    assert overview.json()["identity"] == {"status": None}
    assert overview.json()["aml"] == {"status": "pending"}
    assert [item["uid"] for item in overview.json()["checks"]] == [body["uid"]]


def test_documents_then_identity_and_aml() -> None:
    with _client(request_limit=30) as client:
        created = client.post("/clients", json=PERSON, headers=AUTH)
        uid = created.json()["uid"]
        missing = client.post(f"/clients/{uid}/checks", json={"kind": "identity"}, headers=AUTH)
        assert missing.status_code == 422
        assert missing.json()["code"] == "documents_required"
        selfie = _upload(client, uid, "selfie")
        assert selfie.status_code == 201
        assert selfie.json()["type"] == "selfie"
        assert selfie.json()["issuing_country"] is None
        assert _IMAGE not in selfie.text
        assert "not-returned-image" not in selfie.text
        bare = client.post(f"/clients/{uid}/checks", json={"kind": "identity"}, headers=AUTH)
        assert bare.status_code == 422
        assert bare.json()["code"] == "documents_required"
        no_country = _upload(client, uid, "passport")
        assert no_country.status_code == 400
        assert "issuing_country" in no_country.json()["message"]
        passport = _upload(client, uid, "passport", country="GB")
        assert passport.status_code == 201
        document = passport.json()
        assert set(document) == {"uid", "type", "side", "issuing_country"}
        assert document["side"] == "front"
        assert document["type"] == "passport"
        assert document["issuing_country"] == "GB"
        assert _IMAGE not in passport.text
        refused = _upload(client, uid, "passport", country="GB", file="not base64")
        assert refused.status_code == 400
        assert refused.json()["code"] == "invalid_body"
        assert "file" in refused.json()["message"]
        assert "not base64" not in refused.text
        missing_file = _upload(client, uid, "selfie", file=None)
        assert missing_file.status_code == 400
        assert "file" in missing_file.json()["message"]
        prefixed = _upload(client, uid, "selfie", file="data:image/png;base64," + _IMAGE)
        assert prefixed.status_code == 400
        assert prefixed.json()["code"] == "invalid_body"
        oversized = _upload(
            client,
            uid,
            "selfie",
            file=base64.b64encode(b"x" * (50 * 1024 * 1024 + 1)).decode("ascii"),
        )
        assert oversized.status_code == 422
        assert oversized.json() == {
            "code": "file_too_large",
            "message": "The file is larger than 50 MB.",
        }
        identity = client.post(f"/clients/{uid}/checks", json={"kind": "identity"}, headers=AUTH)
        aml = client.post(f"/clients/{uid}/checks", json={"kind": "aml"}, headers=AUTH)
    assert identity.status_code == 201
    assert identity.json()["status"] == "pending"
    assert identity.json()["kind"] == "identity"
    assert "url" not in identity.json()
    assert document["uid"] in identity.json()["document_uids"]
    assert identity.json()["issuing_country"] == "GB"
    assert "name" not in identity.json()
    assert "account_uid" not in identity.json()
    assert aml.status_code == 201
    assert aml.json()["status"] == "pending"
    assert aml.json()["name"] == "Ada Lovelace"
    assert "document_uids" not in aml.json()
    overview = client.get(f"/clients/{uid}/checks", headers=AUTH)
    assert overview.status_code == 200
    assert overview.json()["identity"]["status"] == "pending"
    assert overview.json()["aml"]["status"] == "pending"
    assert [item["uid"] for item in overview.json()["checks"]] == [
        aml.json()["uid"],
        identity.json()["uid"],
    ]


def test_a_back_is_accepted_for_a_licence_or_identity_document_only() -> None:
    with _client() as client:
        uid = client.post("/clients", json=PERSON, headers=AUTH).json()["uid"]
        for doc_type in ("passport", "selfie"):
            body: dict[str, object] = {"type": doc_type, "side": "back", "file": _IMAGE}
            if doc_type == "passport":
                body["issuing_country"] = "GB"
            refused = client.post(f"/clients/{uid}/documents", json=body, headers=AUTH)
            assert refused.status_code == 400, doc_type
            assert refused.json()["code"] == "invalid_body"
            assert "side back" in refused.json()["message"]
        bad_side = client.post(
            f"/clients/{uid}/documents",
            json={"type": "identity", "issuing_country": "GB", "side": "top", "file": _IMAGE},
            headers=AUTH,
        )
        assert bad_side.status_code == 400
        assert _upload(client, uid, "selfie").status_code == 201
        back = client.post(
            f"/clients/{uid}/documents",
            json={
                "type": "drivers_license",
                "issuing_country": "GB",
                "side": "back",
                "file": _IMAGE,
            },
            headers=AUTH,
        )
        assert back.status_code == 201
        assert back.json()["side"] == "back"
        assert back.json()["type"] == "drivers_license"
        # A back on its own is not an identity document.
        alone = client.post(f"/clients/{uid}/checks", json={"kind": "identity"}, headers=AUTH)
        assert alone.status_code == 422
        assert alone.json()["code"] == "documents_required"
        front = _upload(client, uid, "drivers_license", country="GB")
        assert front.json()["side"] == "front"
        identity = client.post(f"/clients/{uid}/checks", json={"kind": "identity"}, headers=AUTH)
    assert identity.status_code == 201
    used = identity.json()["document_uids"]
    assert back.json()["uid"] in used
    assert front.json()["uid"] in used


def test_the_webhook_must_be_https_with_no_credentials() -> None:
    with _client() as client:
        for url in (
            "http://firm.example/hooks",
            "https://user:pass@firm.example/hooks",
            "https://127.0.0.1/hooks",
            "https://10.0.0.1/hooks",
            "https://localhost/hooks",
        ):
            refused = client.put("/webhook", json={"url": url}, headers=AUTH)
            assert refused.status_code == 400, url
            assert refused.json()["code"] == "invalid_body"
        assert client.get("/webhook", headers=AUTH).json() == {"url": None}


def test_post_then_get_returns_the_same_client() -> None:
    with _client() as client:
        created = client.post("/clients", json=PERSON, headers=AUTH)
        assert created.status_code == 201
        payload = created.json()
        _assert_public_id(payload["uid"])
        fetched = client.get(f"/clients/{payload['uid']}", headers=AUTH)
    assert fetched.status_code == 200
    assert fetched.json() == payload


def test_unknown_tokens_are_rate_limited_before_the_key_lookup() -> None:
    from external.keys import CachedKey, KeyVerifier
    from external.store import StubStore

    lookups: list[str] = []

    async def lookup(key_hash: str) -> CachedKey:
        lookups.append(key_hash)
        return CachedKey(found=False)

    async def touch(record: CachedKey) -> None:
        del record

    with _client(
        stub=False,
        keys=KeyVerifier(lookup, touch),
        store=StubStore(),
        request_limit=2,
        request_period=60,
    ) as client:
        statuses = [
            client.get("/clients", headers={"Authorization": f"Bearer guess-{index}"}).status_code
            for index in range(3)
        ]
    assert statuses == [401, 401, 429]
    assert len(lookups) == 2


def test_a_valid_key_is_not_blocked_by_the_unknown_token_window() -> None:
    """Unknown bearers must not fill a bucket that then refuses a real key."""
    from external.keys import SCOPES, CachedKey, KeyCache, KeyVerifier, hash_bearer
    from external.store import StubStore

    now = [0.0]
    cache = KeyCache(ttl=60, miss_ttl=5, clock=lambda: now[0])
    lookups: list[str] = []
    good = hash_bearer("live_good")

    async def lookup(key_hash: str) -> CachedKey:
        lookups.append(key_hash)
        if key_hash == good:
            return CachedKey(found=True, uid="keyuid", tenant_id=7, scopes=SCOPES)
        return CachedKey(found=False)

    async def touch(record: CachedKey) -> None:
        del record

    headers = {"Authorization": "Bearer live_good"}
    with _client(
        stub=False,
        keys=KeyVerifier(lookup, touch, cache=cache),
        store=StubStore(),
        request_limit=2,
        request_period=120,
    ) as client:
        assert client.get("/clients", headers=headers).status_code == 200
        unknown = [
            client.get("/clients", headers={"Authorization": f"Bearer guess-{index}"}).status_code
            for index in range(3)
        ]
        now[0] = 61
        again = client.get("/clients", headers=headers)
    assert unknown == [401, 401, 429]
    assert again.status_code == 200
    assert lookups.count(good) == 2
    assert len(lookups) == 4


def test_external_process_receives_the_secrets_master_key() -> None:
    root = Path(__file__).resolve().parents[3]
    compose = (root / "docker-compose.yaml").read_text(encoding="utf-8")
    external = compose.split("\n  external:\n", 1)[1].split("\n  render:", 1)[0]
    assert "SECRETS_MASTER_KEY:" in external
    # A firm's documents go to object storage, so the process has the store's credentials.
    assert "*s3" in external
    example = (root / "apps" / "api" / ".env.example").read_text(encoding="utf-8")
    firm = example.split("Firm API", 1)[1].split("ALLOWED_ORIGINS", 1)[0]
    assert "SECRETS_MASTER_KEY" in firm
    deploy = (root / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    note = deploy.split("python -m external", 1)[1].split("tagged", 1)[0]
    assert "SECRETS_MASTER_KEY" in note
    assert "localhost:8010/health" in external
    assert "localhost:8010/docs" not in external
    local = (root / "docker-compose.local.yaml").read_text(encoding="utf-8")
    local_external = local.split("\n  external:\n", 1)[1].split("\n  client:", 1)[0]
    assert "localhost:8010/health" in local_external
    assert "localhost:8010/docs" not in local_external


def test_rate_limit_uses_the_documented_code() -> None:
    with _client(request_limit=1) as client:
        first = client.get("/clients", headers=AUTH)
        second = client.get("/clients", headers=AUTH)
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["code"] == "rate_limited"


def test_screening_webhook_refuses_a_body_over_256_kib_before_the_signature() -> None:
    source = (
        Path(__file__).resolve().parents[1].joinpath("external/app.py").read_text(encoding="utf-8")
    )
    handler = source.split("async def screening_webhook", 1)[1].split("\n    @", 1)[0]
    length_at = handler.find("content-length")
    body_at = handler.find("request.body()")
    verify_at = handler.find("verify(")
    assert length_at > 0
    assert body_at > length_at
    assert verify_at > body_at
    payload = b"{" + (b"x" * (256 * 1024)) + b"}"
    with _client(screening_secret="top-secret") as client:  # noqa: S106
        response = client.post(
            "/internal/webhooks/screening",
            content=payload,
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 413
    assert response.json()["code"] == "body_too_large"
    assert "invalid_signature" not in response.text
    assert "complycube" not in response.text.lower()


def test_internal_webhook_is_absent_from_the_schema() -> None:
    secret = "top-secret"  # noqa: S105
    with _client(screening_secret=secret) as client:
        schema = client.get("/schema/openapi.json")
        assert "internal" not in schema.text
        from app.complycube.signature import digest

        body = b'{"id":"evt_1","type":"check.completed","payload":{"id":"chk"}}'
        header = digest(secret, body)
        response = client.post(
            "/internal/webhooks/screening",
            content=body,
            headers={"ComplyCube-Signature": header, "content-type": "application/json"},
        )
    assert response.status_code == 200
    assert response.json() == {"received": True}
    assert "complycube" not in response.text.lower()


def test_migration_sql_is_the_file_the_docs_describe() -> None:
    sql = (
        Path(__file__)
        .resolve()
        .parents[1]
        .joinpath("migrations/sql/0186_compliance.sql")
        .read_text(encoding="utf-8")
    )
    for table in ("clients", "addresses", "documents", "live_photos", "checks", "events"):
        assert f"compliance.{table}" in sql or f"'{table}'" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "contact_scope" not in sql
    addresses = sql.split("CREATE TABLE compliance.addresses", 1)[1].split("CREATE TABLE", 1)[0]
    assert "UNIQUE (client_id)" in addresses
    assert "to_date" not in addresses
    assert "type text" not in addresses
    trigger = sql.split("CREATE TRIGGER set_updated_at", 1)[0].rsplit("FOREACH tbl", 1)[-1]
    assert "events" not in trigger
    events = sql.split("CREATE TABLE compliance.events", 1)[1].split("CREATE INDEX", 1)[0]
    assert "updated_at" not in events


def test_webhook_secret_is_returned_only_on_put() -> None:
    with _client() as client:
        put = client.put("/webhook", json={"url": "https://firm.example/hooks"}, headers=AUTH)
        assert put.status_code == 200
        assert isinstance(put.json()["secret"], str)
        got = client.get("/webhook", headers=AUTH)
        removed = client.delete("/webhook", headers=AUTH)
    assert got.status_code == 200
    assert got.json() == {"url": "https://firm.example/hooks"}
    assert "secret" not in got.json()
    assert removed.status_code == 200
    assert removed.json() == {"url": None}
    assert "secret" not in removed.json()


def test_health_is_public_and_static() -> None:
    with _client() as client:
        response = client.get("/health")
        refused = client.get("/health", headers={"X-Version": "v9"})
        clients = client.get("/clients")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert refused.status_code == 200
    assert refused.json() == {"status": "ok"}
    assert clients.status_code == 401


def test_gender_other_is_rejected() -> None:
    with _client() as client:
        response = client.post("/clients", json={**PERSON, "gender": "other"}, headers=AUTH)
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_body"


def test_identities_round_trip_and_types_must_be_unique() -> None:
    body = {
        **PERSON,
        "identities": [
            {"type": "national_insurance_number", "identifier": "QQ123456C"},
            {"type": "ssn", "identifier": "123-45-6789"},
        ],
    }
    duplicate = {
        **PERSON,
        "email": "dup@analytical.example",
        "identities": [
            {"type": "ssn", "identifier": "123-45-6789"},
            {"type": "ssn", "identifier": "987-65-4321"},
        ],
    }
    with _client() as client:
        created = client.post("/clients", json=body, headers=AUTH)
        repeated = client.post("/clients", json=duplicate, headers=AUTH)
    assert created.status_code == 201
    assert created.json()["identities"] == body["identities"]
    assert repeated.status_code == 400
    assert repeated.json()["code"] == "invalid_body"


def test_partner_phones_are_rejected_when_the_portal_would_reject_them() -> None:
    """UK numbers need 10 national digits after +44; a bare number needs a leading 0."""
    with _client(request_limit=30) as client:
        mobile = client.post("/clients", json={**PERSON, "mobile": "+441234"}, headers=AUTH)
        telephone = client.post("/clients", json={**PERSON, "telephone": "1111111"}, headers=AUTH)
        today = client.post(
            "/clients",
            json={**PERSON, "email": "phones@analytical.example", "mobile": "+447700900123"},
            headers=AUTH,
        )
        assert today.status_code == 201
        uid = today.json()["uid"]
        patched = client.patch(f"/clients/{uid}", json={"telephone": "+445555"}, headers=AUTH)
    assert mobile.status_code == 400
    assert mobile.json()["code"] == "invalid_body"
    assert telephone.status_code == 400
    assert telephone.json()["code"] == "invalid_body"
    assert patched.status_code == 400
    assert patched.json()["code"] == "invalid_body"
    assert today.json()["mobile"] == "+447700900123"


def test_person_names_reject_digits_and_symbols() -> None:
    with _client(request_limit=30) as client:
        first = client.post("/clients", json={**PERSON, "first_name": "Ada1"}, headers=AUTH)
        last = client.post("/clients", json={**PERSON, "last_name": "Love&lace"}, headers=AUTH)
        created = client.post(
            "/clients",
            json={**PERSON, "email": "names@analytical.example", "last_name": "O'Brien"},
            headers=AUTH,
        )
        assert created.status_code == 201
        uid = created.json()["uid"]
        patched = client.patch(f"/clients/{uid}", json={"first_name": "Anne-Marie"}, headers=AUTH)
        digits = client.patch(f"/clients/{uid}", json={"first_name": "Ada2"}, headers=AUTH)
    assert first.status_code == 400
    assert first.json()["code"] == "invalid_body"
    assert "letters" in first.json()["message"]
    assert last.status_code == 400
    assert last.json()["code"] == "invalid_body"
    assert patched.status_code == 200
    assert patched.json()["first_name"] == "Anne-Marie"
    assert patched.json()["last_name"] == "O'Brien"
    assert digits.status_code == 400


def test_future_date_of_birth_is_rejected_on_create_and_patch() -> None:
    future = (date.today() + timedelta(days=1)).isoformat()
    today = date.today().isoformat()
    with _client(request_limit=30) as client:
        created = client.post("/clients", json={**PERSON, "dob": future}, headers=AUTH)
        allowed = client.post(
            "/clients",
            json={**PERSON, "email": "today@analytical.example", "dob": today},
            headers=AUTH,
        )
        assert allowed.status_code == 201
        uid = allowed.json()["uid"]
        patched = client.patch(f"/clients/{uid}", json={"dob": future}, headers=AUTH)
    assert created.status_code == 400
    assert created.json()["code"] == "invalid_body"
    assert "future" in created.json()["message"]
    assert patched.status_code == 400
    assert patched.json()["code"] == "invalid_body"
    assert allowed.json()["dob"] == today


def test_patch_of_nested_address_company_and_identity_does_not_crash() -> None:
    with _client() as client:
        created = client.post("/clients", json=PERSON, headers=AUTH)
        assert created.status_code == 201
        uid = created.json()["uid"]
        patched = client.patch(
            f"/clients/{uid}",
            json={
                "address": {"line": "2 Road", "city": "York", "country": "IE"},
                "company": {"name": "Analytical Engines Ltd"},
                "identities": [
                    {
                        "type": "national_insurance_number",
                        "identifier": "QQ123456C",
                    }
                ],
            },
            headers=AUTH,
        )
    assert patched.status_code == 200
    body = patched.json()
    assert body["address"]["line"] == "2 Road"
    assert body["address"]["city"] == "York"
    assert body["address"]["country"] == "IE"
    assert body["company"]["name"] == "Analytical Engines Ltd"
    assert body["identities"][0]["identifier"] == "QQ123456C"
