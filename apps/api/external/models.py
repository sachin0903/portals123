"""Public request and response models.

Field names are ours. Unknown fields are rejected so a caller cannot pass
provider-specific names. Nothing here carries a provider id.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    field_validator,
    model_serializer,
    model_validator,
)

from app.complycube.webhook_url import check_syntax
from app.contacts import NAME_CHARS_ERROR, _is_person_name, validate_contact_phone
from app.storage import sniff_document_type

ISO = re.compile(r"^[A-Z]{2}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_FILE_BYTES = 50 * 1024 * 1024
# Base64 expands by 4/3. The transport limit sits above the decoded cap so a file
# just over 50 MB reaches the handler and is rejected as file_too_large.
REQUEST_MAX_BODY = 4 * ((MAX_FILE_BYTES + 1024 * 1024 + 2) // 3) + 8192

IncorporationType = Literal[
    "sole_proprietorship",
    "private_limited_company",
    "public_limited_company",
    "partnership",
    "limited_partnership",
    "limited_liability_partnership",
    "holding_company",
    "non_government_organization",
    "non_profit_organization",
    "governmental_entity",
    "statutory_company",
    "subsidiary_company",
    "unlimited_partnership",
    "unlimited_company",
    "trust",
    "charitable_incorporated_organization",
    "chartered_company",
    "cooperative",
    "professional_association",
    "other",
]

Gender = Literal["male", "female"]
IdentityType = Literal[
    "national_insurance_number",
    "ssn",
    "social_insurance_number",
    "tax_identification_number",
    "national_identity_number",
]
ClientType = Literal["person", "company"]
CheckKind = Literal["identity", "aml"]
CheckStatus = Literal["pending", "clear", "refer", "rejected"]
DocumentType = Literal["selfie", "passport", "drivers_license", "identity"]
DocumentSide = Literal["front", "back"]
Custodian = Literal["seccl", "sei"]
ID_DOCUMENT_TYPES = frozenset({"passport", "drivers_license", "identity"})
#: The document types that have a back. A passport and a selfie do not.
TWO_SIDED_TYPES = frozenset({"drivers_license", "identity"})


def _iso(value: str, field: str) -> str:
    if not ISO.fullmatch(value):
        raise ValueError(f"{field} must be an ISO alpha-2 code")
    return value


class AddressIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    property_number: str | None = Field(default=None, description="Street or property number.")
    building_name: str | None = Field(default=None, description="Building name.")
    line: str | None = Field(default=None, description="Street line.")
    city: str | None = Field(default=None, description="City or town.")
    state: str | None = Field(default=None, description="County, state, or region.")
    postal_code: str | None = Field(default=None, description="Postal code.")
    country: str = Field(description="ISO 3166-1 alpha-2 country code. Required.")
    from_date: date | None = Field(
        default=None, description="Date the client started using this address."
    )

    @field_validator("country")
    @classmethod
    def country_code(cls, value: str) -> str:
        return _iso(value, "country")


class CompanyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(
        default=None, description="Registered name. Required when type is company."
    )
    website: str | None = Field(default=None, description="Public website.")
    registration_number: str | None = Field(
        default=None, description="Company registration number."
    )
    incorporation_country: str | None = Field(
        default=None,
        description=(
            "ISO alpha-2 country of incorporation. A company needs this or an address country."
        ),
    )
    incorporation_type: IncorporationType | None = Field(
        default=None, description="Legal form of the company."
    )

    @field_validator("incorporation_country")
    @classmethod
    def country_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _iso(value, "incorporation_country")


class Identity(BaseModel):
    """One government identity number."""

    model_config = ConfigDict(extra="forbid")

    type: IdentityType = Field(
        description=(
            "Which number this is. national_insurance_number is a UK National Insurance number. "
            "ssn is a US Social Security number. social_insurance_number is a social insurance "
            "number. tax_identification_number is a tax identification number. "
            "national_identity_number is a national identity number."
        )
    )
    identifier: str = Field(min_length=1, description="The identity number for the chosen type.")


class ClientWrite(BaseModel):
    """Shared shape for create and patch. Create adds the required-field rules."""

    model_config = ConfigDict(extra="forbid")

    type: ClientType | None = Field(
        default=None, description="person or company. Create defaults this to person."
    )
    email: str | None = Field(default=None, description="Email address. Required on create.")
    first_name: str | None = Field(default=None, description="Given name. Required for a person.")
    middle_name: str | None = Field(default=None, description="Middle name.")
    last_name: str | None = Field(default=None, description="Family name. Required for a person.")
    dob: date | None = Field(
        default=None, description="Date of birth (YYYY-MM-DD). Required for a person."
    )
    gender: Gender | None = Field(default=None, description="male or female.")
    nationality: list[str] | None = Field(
        default=None,
        description=(
            "ISO alpha-2 nationalities. One code or a list. Required for a person, "
            "at least one code. A company does not need this."
        ),
    )
    birth_country: str | None = Field(default=None, description="ISO alpha-2 country of birth.")
    identities: list[Identity] | None = Field(
        default=None,
        description=(
            "Optional identity numbers. Each type at most once. Omit the field when the firm "
            "does not hold any."
        ),
    )
    mobile: str | None = Field(default=None, description="Mobile telephone number.")
    telephone: str | None = Field(default=None, description="Landline telephone number.")
    company: CompanyIn | None = Field(
        default=None, description="Company details. Required when type is company."
    )
    address: AddressIn | None = Field(
        default=None,
        description=(
            "The client's address. A person needs one. country is required. "
            "A company needs a country here or on the company."
        ),
    )
    tax_residency: list[str] | None = Field(
        default=None, description="ISO alpha-2 countries of tax residency."
    )
    reference: str | None = Field(
        default=None, description="The firm's own identifier for this client."
    )
    metadata: dict[str, str] | None = Field(
        default=None,
        description=(
            "Up to 50 string pairs. Keys are at most 40 characters and must not start with "
            "graphene. Values are at most 500 characters."
        ),
    )

    @field_validator("nationality", mode="before")
    @classmethod
    def nationality_list(cls, value: object) -> object:
        if isinstance(value, str):
            return [value]
        return value

    @field_validator("nationality", "tax_residency")
    @classmethod
    def country_list(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return [_iso(item, "country") for item in value]

    @field_validator("birth_country")
    @classmethod
    def birth(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _iso(value, "birth_country")

    @field_validator("email")
    @classmethod
    def email_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not EMAIL.fullmatch(value):
            raise ValueError("email must be an email address")
        return value

    @field_validator("metadata")
    @classmethod
    def metadata_bounds(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        if len(value) > 50:
            raise ValueError("metadata accepts at most 50 keys")
        for key, item in value.items():
            if key.lower().startswith("graphene"):
                raise ValueError("metadata keys under graphene are reserved")
            if len(key) > 40:
                raise ValueError("metadata keys must be 40 characters or fewer")
            if len(item) > 500:
                raise ValueError("metadata values must be 500 characters or fewer")
        return value

    @field_validator("first_name", "last_name")
    @classmethod
    def person_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _is_person_name(value):
            raise ValueError(NAME_CHARS_ERROR)
        return value

    @field_validator("dob")
    @classmethod
    def dob_not_future(cls, value: date | None) -> date | None:
        if value is not None and value > date.today():
            raise ValueError("Date of birth cannot be in the future.")
        return value

    @field_validator("mobile", "telephone")
    @classmethod
    def phone_number(cls, value: str | None) -> str | None:
        return validate_contact_phone(value)

    @field_validator("identities")
    @classmethod
    def unique_identity_types(cls, value: list[Identity] | None) -> list[Identity] | None:
        if value is None:
            return None
        kinds = [item.type for item in value]
        if len(kinds) != len(set(kinds)):
            raise ValueError("identities must not repeat a type")
        return value


class ClientCreate(ClientWrite):
    model_config = ConfigDict(extra="forbid")

    type: ClientType = Field(default="person", description="person or company. Defaults to person.")
    email: str = Field(description="Email address. Required.")

    @model_validator(mode="after")
    def required_core(self) -> Self:
        if self.email is None or not EMAIL.fullmatch(self.email):
            raise ValueError("email must be an email address")
        if self.type == "person":
            missing = [
                name
                for name, present in (
                    ("first_name", self.first_name),
                    ("last_name", self.last_name),
                    ("dob", self.dob),
                    ("nationality", self.nationality),
                )
                if not present
            ]
            if missing:
                raise ValueError(f"missing {', '.join(missing)}")
            if self.address is None:
                raise ValueError("a person requires an address")
        else:
            company = self.company
            if company is None or not company.name:
                raise ValueError("a company requires company.name")
            if not (self.address or (company and company.incorporation_country)):
                raise ValueError("a company requires a country")
        return self


class ClientPatch(ClientWrite):
    """Every field optional. Unknown fields are still rejected."""


class CheckCreate(BaseModel):
    model_config = cast(
        ConfigDict,
        {
            "extra": "forbid",
            "example": {"kind": "identity"},
        },
    )

    kind: CheckKind = Field(
        description=(
            "identity runs on documents already uploaded for this client. "
            "aml runs from the client record."
        )
    )


class CustodianPut(BaseModel):
    model_config = cast(
        ConfigDict,
        {
            "extra": "forbid",
            "example": {"provider": "seccl", "external_id": "CLT-1001"},
        },
    )

    provider: Custodian = Field(description="Custodian that holds the client: seccl or sei.")
    external_id: str = Field(min_length=1, description="Id the custodian assigned to this client.")


class WebhookPut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(
        min_length=1,
        description=(
            "HTTPS endpoint that receives check updates. The host must resolve to a public "
            "address. A user name or password in the URL is not accepted."
        ),
    )

    @field_validator("url")
    @classmethod
    def https_only(cls, value: str) -> str:
        return str(check_syntax(value.strip()))


class AddressOut(AddressIn):
    pass


class CompanyOut(CompanyIn):
    pass


class ClientOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uid: str = Field(description="Public id. Twelve characters, with no type prefix.")
    type: ClientType = Field(description="person or company.")
    email: str = Field(description="Email address.")
    first_name: str | None = Field(default=None, description="Given name.")
    middle_name: str | None = Field(default=None, description="Middle name.")
    last_name: str | None = Field(default=None, description="Family name.")
    dob: date | None = Field(default=None, description="Date of birth (YYYY-MM-DD).")
    gender: Gender | None = Field(default=None, description="male or female.")
    nationality: list[str] | None = Field(
        default=None,
        description="ISO alpha-2 nationalities. Required for a person. Absent for a company.",
    )
    birth_country: str | None = Field(default=None, description="ISO alpha-2 country of birth.")
    identities: list[Identity] | None = Field(
        default=None,
        description="Identity numbers held for this client. Each type at most once.",
    )
    mobile: str | None = Field(default=None, description="Mobile telephone number.")
    telephone: str | None = Field(default=None, description="Landline telephone number.")
    company: CompanyOut | None = Field(default=None, description="Company details.")
    address: AddressOut | None = Field(default=None, description="The client's address.")
    tax_residency: list[str] | None = Field(
        default=None, description="ISO alpha-2 countries of tax residency."
    )
    reference: str | None = Field(
        default=None, description="The firm's own identifier for this client."
    )
    metadata: dict[str, str] | None = Field(
        default=None,
        description="String pairs stored for the firm. Keys under graphene are reserved.",
    )
    custodians: list[CustodianPut] = Field(
        default_factory=list, description="Custodian ids recorded for this client."
    )


class ClientPage(BaseModel):
    items: list[ClientOut] = Field(description="Clients in this page.")
    limit: int = Field(description="Maximum number of clients requested.")
    offset: int = Field(description="Number of clients skipped.")


class CheckOut(BaseModel):
    """One check. Identity and AML add different fields; absent fields are omitted."""

    model_config = ConfigDict(extra="forbid")

    uid: str = Field(description="Public id of the check. Twelve characters, with no type prefix.")
    kind: CheckKind = Field(description="identity or aml.")
    status: CheckStatus = Field(
        description="pending, clear, refer, or rejected. Hit lists are never returned."
    )
    created_at: datetime = Field(description="When the check was created.")
    updated_at: datetime = Field(description="When the check was last updated.")
    document_uids: list[str] | None = Field(
        default=None,
        description=(
            "Public ids of the documents used. Set on an identity check: the selfie and "
            "the passport, driving licence, or identity document, both sides when a back "
            "was uploaded."
        ),
    )
    issuing_country: str | None = Field(
        default=None,
        description=(
            "ISO alpha-2 country that issued the identity document, when it is known. "
            "Set on an identity check."
        ),
    )
    name: str | None = Field(
        default=None, description="Name that was screened. Set on an AML check."
    )
    dob: date | None = Field(
        default=None, description="Date of birth that was screened. Set on an AML check."
    )
    country: str | None = Field(
        default=None, description="Address country that was screened. Set on an AML check."
    )

    @model_serializer(mode="wrap")
    def _public(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        if not isinstance(data, dict):
            return {"value": data}
        if self.kind == "identity":
            keys = ["uid", "kind", "status", "created_at", "updated_at", "document_uids"]
            if data.get("issuing_country"):
                keys.append("issuing_country")
        else:
            keys = ["uid", "kind", "status", "created_at", "updated_at", "name", "dob", "country"]
        return {key: data.get(key) for key in keys}


class CheckKindStatus(BaseModel):
    """Latest status for one kind of check."""

    model_config = ConfigDict(extra="forbid")

    status: CheckStatus | None = Field(
        description=(
            "Status of the latest check of this kind: pending, clear, refer, or rejected. "
            "Null when the client has no check of this kind."
        )
    )


class ChecksOut(BaseModel):
    """Latest identity and AML status, then every check, newest first."""

    model_config = ConfigDict(extra="forbid")

    identity: CheckKindStatus = Field(description="Latest identity check for this client.")
    aml: CheckKindStatus = Field(description="Latest AML check for this client.")
    checks: list[CheckOut] = Field(description="Every check for this client, newest first.")


def utc_now() -> datetime:
    return datetime.now(UTC)


def screened_name(client: ClientOut) -> str | None:
    """Name an AML check screens: the company name, or the person's full name."""
    if client.type == "company" and client.company is not None and client.company.name:
        return client.company.name
    parts = [part for part in (client.first_name, client.middle_name, client.last_name) if part]
    return " ".join(parts) or None


def identity_details(
    documents: list[tuple[str, str, str | None]],
) -> tuple[list[str], str | None]:
    """Document uids used, and the issuing country of the identity document when known.

    Both sides of an identity document are listed. The country is the newest one held.
    """
    used = [
        (uid, kind, country)
        for uid, kind, country in documents
        if kind == "selfie" or kind in ID_DOCUMENT_TYPES
    ]
    issuing: str | None = None
    for _uid, kind, country in reversed(used):
        if kind in ID_DOCUMENT_TYPES and country:
            issuing = country
            break
    return [uid for uid, _kind, _country in used], issuing


def checks_overview(checks: list[CheckOut]) -> ChecksOut:
    """`checks` is newest first. Status is that latest check, or null when none exists."""

    def latest(kind: CheckKind) -> CheckStatus | None:
        for check in checks:
            if check.kind == kind:
                return check.status
        return None

    return ChecksOut(
        identity=CheckKindStatus(status=latest("identity")),
        aml=CheckKindStatus(status=latest("aml")),
        checks=checks,
    )


def decode_document_file(value: object) -> bytes:
    """Decode a raw base64 file. The error text never includes the value."""
    if not isinstance(value, str) or not value or value.startswith("data:"):
        raise ValueError("file must be a base64 string")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("file must be a base64 string") from exc
    if not decoded:
        raise ValueError("file must be a base64 string")
    return decoded


class DocumentUpload(BaseModel):
    """JSON body for ``POST /clients/{uid}/documents``."""

    model_config = cast(
        ConfigDict,
        {
            "extra": "forbid",
            "example": {
                "type": "passport",
                "issuing_country": "GB",
                "file": "aGVsbG8=",
            },
        },
    )

    type: DocumentType = Field(description="selfie, passport, drivers_license, or identity.")
    side: DocumentSide = Field(
        default="front",
        description=(
            "front or back. Defaults to front. back is accepted for a driving licence or an "
            "identity document. A passport and a selfie have a front only."
        ),
    )
    issuing_country: str | None = Field(
        default=None,
        description=(
            "ISO alpha-2 country that issued the document. Required for a passport, "
            "driving licence, or identity document. Absent for a selfie."
        ),
    )
    file: str = Field(
        description=(
            "Base64-encoded image. The raw base64 string, with no data: URL prefix. Not returned."
        ),
        repr=False,
        json_schema_extra={"format": "byte"},
    )
    _content: bytes = PrivateAttr()

    @field_validator("issuing_country")
    @classmethod
    def country_code(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        return _iso(value, "issuing_country")

    @field_validator("file")
    @classmethod
    def file_is_base64(cls, value: object) -> str:
        decode_document_file(value)
        if not isinstance(value, str):
            raise ValueError("file must be a base64 string")
        return value

    @model_validator(mode="after")
    def document_rules(self) -> Self:
        if self.type == "selfie" and self.issuing_country:
            raise ValueError("issuing_country is not accepted for a selfie")
        if self.type in ID_DOCUMENT_TYPES and not self.issuing_country:
            raise ValueError("issuing_country is required")
        if self.side == "back" and self.type not in TWO_SIDED_TYPES:
            raise ValueError("side back is accepted only for drivers_license or identity")
        self._content = decode_document_file(self.file)
        return self

    def content(self) -> bytes:
        """Decoded image bytes. Callers must not log them."""
        return self._content


#: The file types an identity image is kept as, by sniffed content type.
IMAGE_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "application/pdf": "pdf",
    "image/heic": "heic",
    "image/heif": "heif",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/tiff": "tiff",
    "image/bmp": "bmp",
    "image/avif": "avif",
}


def held_document(upload: DocumentUpload) -> HeldDocument:
    """The upload with the content type read from its own bytes and a file name to match.

    The firm sends no name, so the name is the document type, its side, and the extension of the
    sniffed type. A file that is not a recognised image or PDF is kept as
    ``application/octet-stream``.
    """
    content = upload.content()
    try:
        # The image and PDF signatures are all in the first bytes; the rest is not read.
        sniffed = sniff_document_type(content[:4096])
    except ValueError:
        sniffed = ""
    ext = IMAGE_EXTENSIONS.get(sniffed)
    content_type = sniffed if ext else "application/octet-stream"
    stem = upload.type if upload.type == "selfie" else f"{upload.type}-{upload.side}"
    return HeldDocument(
        type=upload.type,
        issuing_country=upload.issuing_country,
        content=content,
        file_name=f"{stem}.{ext or 'bin'}",
        content_type=content_type,
        side=upload.side,
    )


class DocumentOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uid: str = Field(
        description="Public id of the document. Twelve characters, with no type prefix."
    )
    type: DocumentType = Field(description="selfie, passport, drivers_license, or identity.")
    side: DocumentSide = Field(
        default="front", description="front or back. A selfie and a passport are front."
    )
    issuing_country: str | None = Field(
        default=None,
        description=(
            "ISO alpha-2 issuing country. Set for a passport, driving licence, or identity "
            "document. Null for a selfie."
        ),
    )


@dataclass(frozen=True)
class HeldDocument:
    """Bytes kept with a document. The firm API never returns them."""

    type: DocumentType
    issuing_country: str | None
    content: bytes
    file_name: str
    content_type: str
    side: DocumentSide = "front"


class WebhookOut(BaseModel):
    url: str | None = Field(description="Endpoint that receives check updates, or null when unset.")


class WebhookCreated(WebhookOut):
    secret: str = Field(
        description="Signing secret. Returned only when the webhook is set or replaced."
    )


class HealthOut(BaseModel):
    status: Literal["ok"] = Field(description="ok when this process is serving.")


class Problem(BaseModel):
    """Error body returned by every non-2xx response."""

    code: str = Field(description="Stable error code.")
    message: str = Field(description="What the caller should change.")


def to_client_out(
    body: ClientCreate, *, uid: str, custodians: list[CustodianPut] | None = None
) -> ClientOut:
    """Copy a write model into the response model.

    Address and company values are separate types, so a write instance is not a
    valid response instance. Dumping to plain values lets the response model
    validate them as its own types.
    """
    payload = body.model_dump()
    payload["uid"] = uid
    if custodians is not None:
        payload["custodians"] = [item.model_dump() for item in custodians]
    return ClientOut.model_validate(payload)


def apply_patch(current: ClientOut, body: ClientPatch) -> ClientOut:
    """Re-validate a patch the same way create does.

    ``model_copy`` leaves nested objects as plain dicts. The live writer then
    reads attributes on address, company, and identities.
    """
    payload = current.model_dump()
    payload.update(body.model_dump(exclude_unset=True))
    return ClientOut.model_validate(payload)
