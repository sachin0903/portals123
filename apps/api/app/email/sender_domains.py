"""A firm's own sending domain, registered by Graphene operations on the shared Resend account.

The HTTP calls live in ``app.email.transport``. That module is the only one allowed to hold the
Resend credential, and this one decides what a domain name means, stores the row, and turns a
provider snapshot into "may we send from this yet?".

One domain per firm. Mail uses it only when Resend has verified sending AND operations has chosen
an address on it. Anything short of that keeps the platform ``EMAIL_FROM``.
"""

from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import ratelimit
from app.comms import ledger
from app.config import settings
from app.email import render as email_render
from app.email import transport
from app.ids import new_id

logger = logging.getLogger(__name__)

#: The DNS records that prove a domain may send. Receiving and tracking records do not.
SENDING_RECORDS = frozenset({"SPF", "DKIM"})

_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

_COLUMNS = """
    uid, domain, resend_domain_id, region, provider_status, sending_verified,
    from_address, verified_at, records
"""


class SenderDomainRejected(Exception):
    """The operator's request cannot be applied. ``status_code`` is what the route answers."""

    def __init__(self, message: str, *, status_code: int = 422) -> None:
        self.status_code = status_code
        super().__init__(message)


class DnsRecordOut(BaseModel):
    """One DNS row, in the shape the settings page copies out.

    ``required`` is false for DMARC. Resend does not check it, and mail sends without it.
    """

    purpose: str
    name: str
    type: str
    value: str
    ttl: str | None = None
    status: str
    priority: int | None = None
    required: bool = True


class SenderDomainOut(BaseModel):
    """The firm's sending domain. ``active`` is the only state ``resolve_sender`` will use."""

    uid: str
    domain: str
    region: str
    status: str
    sending_verified: bool
    active: bool
    from_address: str | None
    verified_at: datetime | None
    records: list[DnsRecordOut]


class RegisterSenderDomain(BaseModel):
    domain: str = Field(min_length=1, max_length=253)


class SetSenderAddress(BaseModel):
    from_address: str = Field(min_length=1, max_length=254)


class SenderTestOut(BaseModel):
    """A test of the firm's current sender, mailed to the operator who asked.

    ``to`` is that operator's own address, echoed so the page can say where to look. There is no
    other address in this response: the route takes no recipient.
    """

    status: Literal["delivered", "suppressed", "failed"]
    to: str
    subject: str
    detail: str | None = None


def sending_is_verified(status: str, records: list[dict[str, object]]) -> bool:
    """True when Resend will accept mail from this domain.

    ``verified`` is the aggregate "sending works". A partial status still counts when every SPF
    and DKIM record has verified on its own — receiving, which we do not enable, must not hold
    sending back. A status of ``verified`` wins even when the payload carried no records.
    """
    if status == "verified":
        return True
    relevant = [record for record in records if record.get("record") in SENDING_RECORDS]
    present = {str(record.get("record")) for record in relevant}
    if not SENDING_RECORDS <= present:
        return False
    return all(record.get("status") == "verified" for record in relevant)


def normalise_domain(raw: str) -> str:
    """A hostname Resend can register, or a rejection the operator can act on.

    The platform's own sender domain is refused: a firm must not be able to take over the
    address every other firm falls back to.
    """
    value = raw.strip().lower().rstrip(".")
    if (
        not value
        or "@" in value
        or "://" in value
        or "/" in value
        or any(char.isspace() for char in value)
        or "*" in value
    ):
        raise SenderDomainRejected("Enter the domain on its own, such as mail.example.co.uk.")
    if not value.isascii() or len(value) > 253:
        raise SenderDomainRejected("Enter a domain such as mail.example.co.uk.")
    labels = value.split(".")
    if len(labels) < 2 or any(_LABEL.fullmatch(label) is None for label in labels):
        raise SenderDomainRejected("Enter a domain such as mail.example.co.uk.")
    platform = transport._domain(transport._address(settings.email_from))
    if platform and (value == platform or value.endswith("." + platform)):
        raise SenderDomainRejected("That domain belongs to the platform sender.")
    return value


def normalise_from_address(raw: str, domain: str) -> str:
    """One mailbox on ``domain``. Resend does not register the address itself."""
    single = transport._single_address(raw)
    if single is None:
        raise SenderDomainRejected("Enter one email address on the sending domain.")
    address = single.lower()
    if transport._domain(address) != domain:
        raise SenderDomainRejected(f"The address must be on {domain}.")
    return address


def _publish_name(name: str, domain: str) -> str:
    """The hostname a firm's DNS provider expects.

    Resend returns a label relative to the sending domain (``send``, ``resend._domainkey``).
    The settings page and the CSV show the full name, so the firm can paste it without guessing
    which zone it belongs in.
    """
    host = name.strip().rstrip(".").lower()
    if host in {"", "@"}:
        return domain
    if host == domain or host.endswith(f".{domain}"):
        return host
    return f"{host}.{domain}"


#: Resend's suggested starting policy. ``p=none`` reports without rejecting mail, which is the
#: safe record to hand a firm before anyone has watched a DMARC report.
DMARC_VALUE = "v=DMARC1; p=none;"


def present_records(domain: str, stored: list[dict[str, object]]) -> list[DnsRecordOut]:
    """The rows the settings page shows: Resend's records, plus an optional DMARC record.

    DMARC is not one of Resend's verification records. It is appended when the provider did not
    return one, and it never counts towards ``sending_verified``.
    """
    records = [
        DnsRecordOut(
            purpose=str(item.get("record") or ""),
            name=_publish_name(str(item.get("name") or ""), domain),
            type=str(item.get("type") or ""),
            value=str(item.get("value") or ""),
            ttl=item.get("ttl") if isinstance(item.get("ttl"), str) else None,
            status=str(item.get("status") or ""),
            priority=item.get("priority") if isinstance(item.get("priority"), int) else None,
            required=str(item.get("record") or "") != "DMARC",
        )
        for item in stored
    ]
    if any(record.purpose == "DMARC" for record in records):
        return records
    records.append(
        DnsRecordOut(
            purpose="DMARC",
            name=_publish_name("_dmarc", domain),
            type="TXT",
            value=DMARC_VALUE,
            ttl="Auto",
            status="optional",
            required=False,
        )
    )
    return records


def _record_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _view(row: RowMapping) -> SenderDomainOut:
    domain = str(row["domain"])
    from_address = row["from_address"]
    verified = bool(row["sending_verified"])
    address = str(from_address) if isinstance(from_address, str) and from_address else None
    verified_at = row["verified_at"]
    return SenderDomainOut(
        uid=str(row["uid"]),
        domain=domain,
        region=str(row["region"]),
        status=str(row["provider_status"]),
        sending_verified=verified,
        active=verified and address is not None,
        from_address=address,
        verified_at=verified_at if isinstance(verified_at, datetime) else None,
        records=present_records(domain, _record_list(row["records"])),
    )


async def _row(session: AsyncSession, tenant_id: int) -> RowMapping | None:
    return (
        (
            await session.execute(
                text(
                    f"SELECT {_COLUMNS} FROM email.sender_domains WHERE tenant_id = :t"  # noqa: S608
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )


async def read_sender_domain(session: AsyncSession, tenant_id: int) -> SenderDomainOut | None:
    row = await _row(session, tenant_id)
    return _view(row) if row is not None else None


def _require(row: RowMapping | None) -> RowMapping:
    if row is None:
        raise SenderDomainRejected("This firm has no sending domain.", status_code=404)
    return row


async def _store_snapshot(
    session: AsyncSession,
    resend_domain_id: str,
    *,
    status: str,
    records: list[dict[str, object]] | None,
) -> bool:
    """Write Resend's latest opinion. Returns whether a row matched.

    ``records is None`` means this payload did not carry them (a thin webhook), so the stored
    records stay and verification is judged from those plus the new status.
    """
    if records is None:
        current = (
            await session.execute(
                text("SELECT records FROM email.sender_domains WHERE resend_domain_id = :id"),
                {"id": resend_domain_id},
            )
        ).scalar_one_or_none()
        judged = _record_list(current)
        record_sql = "records"
        params: dict[str, object] = {"id": resend_domain_id, "status": status}
    else:
        judged = records
        record_sql = "CAST(:records AS jsonb)"
        params = {
            "id": resend_domain_id,
            "status": status,
            "records": json.dumps(records),
        }
    verified = sending_is_verified(status, judged)
    if status in {"failed", "not_started", "deleted"}:
        verified = False
    params["verified"] = verified
    matched = (
        await session.execute(
            text(
                f"UPDATE email.sender_domains SET provider_status = :status, "  # noqa: S608
                f"records = {record_sql}, sending_verified = :verified, "
                "verified_at = CASE "
                "  WHEN :verified AND verified_at IS NULL THEN now() "
                "  WHEN NOT :verified THEN NULL "
                "  ELSE verified_at END "
                "WHERE resend_domain_id = :id RETURNING tenant_id"
            ),
            params,
        )
    ).first()
    if matched is None:
        return False
    transport.clear_caches()
    return True


async def register_sender_domain(
    session: AsyncSession, tenant_id: int, raw_domain: str
) -> SenderDomainOut:
    domain = normalise_domain(raw_domain)
    if await _row(session, tenant_id) is not None:
        raise SenderDomainRejected(
            "This firm already has a sending domain. Remove it before adding another.",
            status_code=409,
        )
    taken = (
        await session.execute(
            text("SELECT 1 FROM email.sender_domains WHERE domain = :d"), {"d": domain}
        )
    ).first()
    if taken is not None:
        raise SenderDomainRejected("That domain is already registered.", status_code=409)
    try:
        created = await transport.create_sending_domain(domain)
    except transport.ResendError as exc:
        raise SenderDomainRejected(str(exc), status_code=exc.status_code) from exc
    records = created.records
    try:
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO email.sender_domains "
                    "  (uid, tenant_id, domain, resend_domain_id, region, provider_status, "
                    "   sending_verified, records, verified_at) "
                    "VALUES (:uid, :t, :domain, :rid, :region, :status, :verified, "
                    "        CAST(:records AS jsonb), "
                    "        CASE WHEN :verified THEN now() ELSE NULL END)"
                ),
                {
                    "uid": new_id(),
                    "t": tenant_id,
                    "domain": domain,
                    "rid": created.id,
                    "region": created.region,
                    "status": created.status,
                    "verified": sending_is_verified(created.status, records),
                    "records": json.dumps(records),
                },
            )
    except IntegrityError as exc:
        try:
            await transport.delete_sending_domain(created.id)
        except transport.ResendError:
            logger.warning(
                "a Resend domain was left behind after the row could not be stored",
                extra={"event": "sender_domain_orphaned"},
            )
        raise SenderDomainRejected("That domain is already registered.", status_code=409) from exc
    transport.clear_caches()
    stored = await read_sender_domain(session, tenant_id)
    if stored is None:  # pragma: no cover - the insert just succeeded
        raise SenderDomainRejected("The sending domain could not be stored.", status_code=502)
    return stored


async def set_from_address(
    session: AsyncSession, tenant_id: int, raw_address: str
) -> SenderDomainOut:
    row = _require(await _row(session, tenant_id))
    address = normalise_from_address(raw_address, str(row["domain"]))
    await session.execute(
        text("UPDATE email.sender_domains SET from_address = :a WHERE tenant_id = :t"),
        {"a": address, "t": tenant_id},
    )
    transport.clear_caches()
    stored = await read_sender_domain(session, tenant_id)
    assert stored is not None  # noqa: S101 - the row was just updated in this transaction
    return stored


async def refresh_sender_domain(
    session: AsyncSession, tenant_id: int, *, start: bool
) -> SenderDomainOut:
    """Read Resend's current opinion, optionally starting a verification check first.

    Starting a check marks the domain pending at Resend even when it was already verified, so
    the settings page only does that from the verify action. Refresh never does.
    """
    row = _require(await _row(session, tenant_id))
    domain_id = str(row["resend_domain_id"])
    try:
        if start:
            await transport.verify_sending_domain(domain_id)
        snapshot = await transport.retrieve_sending_domain(domain_id)
    except transport.ResendError as exc:
        raise SenderDomainRejected(str(exc), status_code=exc.status_code) from exc
    await _store_snapshot(session, domain_id, status=snapshot.status, records=snapshot.records)
    stored = await read_sender_domain(session, tenant_id)
    assert stored is not None  # noqa: S101 - the row was just written in this transaction
    return stored


async def remove_sender_domain(session: AsyncSession, tenant_id: int) -> None:
    row = _require(await _row(session, tenant_id))
    try:
        await transport.delete_sending_domain(str(row["resend_domain_id"]))
    except transport.ResendError as exc:
        raise SenderDomainRejected(str(exc), status_code=exc.status_code) from exc
    await session.execute(
        text("DELETE FROM email.sender_domains WHERE tenant_id = :t"), {"t": tenant_id}
    )
    transport.clear_caches()


async def apply_domain_event(session: AsyncSession, event_type: str, data: object) -> bool:
    """Apply a signed Resend domain webhook. False when the domain is not one of ours."""
    if not isinstance(data, dict):
        return False
    domain_id = data.get("id")
    if not isinstance(domain_id, str) or not domain_id:
        return False
    if event_type == "domain.deleted":
        return await _store_snapshot(session, domain_id, status="deleted", records=None)
    if event_type not in {"domain.updated", "domain.created"}:
        return False
    status = data.get("status")
    status_value = status if isinstance(status, str) and status else "pending"
    raw_records = data.get("records")
    records: list[dict[str, object]] | None
    if isinstance(raw_records, list):
        records = []
        for item in raw_records:
            parsed = transport._domain_record(item)
            if parsed is not None:
                records.append(parsed)
    else:
        records = None
    return await _store_snapshot(session, domain_id, status=status_value, records=records)


async def _operator_mailbox(session: AsyncSession) -> tuple[str, str]:
    """The acting operator's own user id and email. Nothing in the request can change either.

    The same lookup a template test uses. A caller with no address on record is refused rather
    than mailed somewhere else.
    """
    row = (
        (
            await session.execute(
                text(
                    "SELECT auth.uid() AS user_id, "
                    "       (SELECT email FROM auth.users WHERE id = auth.uid()) AS email"
                )
            )
        )
        .mappings()
        .one()
    )
    user_id = str(row["user_id"] or "")
    email = str(row["email"] or "").strip()
    if not user_id or not email:
        raise SenderDomainRejected(
            "A test email can only be sent to your own address, and this account has no email "
            "address on record. Ask an administrator to set one, then try again."
        )
    return user_id, email


async def send_sender_test(session: AsyncSession, tenant_id: int) -> SenderTestOut:
    """Mail the operator who asked, as this firm would send to a client right now.

    The envelope is ``resolve_sender`` — the platform address until the domain is verified and
    has a mailbox, and the chosen address after that. A test that went out as Graphene when the
    firm had its own domain would prove the wrong thing.
    """
    user_id, to = await _operator_mailbox(session)
    await ratelimit.enforce(
        ratelimit.EMAIL_TEST,
        ratelimit.email_test_limits(user_id=user_id, tenant_id=tenant_id),
        detail="Too many test emails. Try again later.",
    )
    sender = await transport.resolve_sender(session, tenant_id, portal="client")
    brand = await transport.brand_for(session, tenant_id, portal="client")
    subject = f"[TEST] {brand.name} sending address"
    body = (
        f"<p>This is a test of how mail from {html.escape(brand.name)} is sent.</p>"
        "<p>You asked for it from the firm's Sending settings. "
        "It went only to your own address.</p>"
    )
    document = email_render.sendable(
        body,
        preheader=html.escape(f"A test of how mail from {brand.name} is sent."),
        asset_origin=await transport.asset_origin(session, tenant_id),
    )
    await transport.refresh_allowlist()
    reason = transport.suppression_reason(to) or transport.sending_disabled()
    delivered = await transport.send_email(
        to=to,
        subject=subject,
        html=document,
        sender=sender.from_addr,
        reply_to=sender.reply_to,
        ctx=ledger.SendContext(
            tenant_id=tenant_id,
            template_code="sender-domain-test",
            recipient_user_id=user_id,
        ),
    )
    if delivered:
        return SenderTestOut(status="delivered", to=to, subject=subject)
    if reason is not None:
        return SenderTestOut(
            status="suppressed",
            to=to,
            subject=subject,
            detail="Email sending is unavailable. Contact support.",
        )
    return SenderTestOut(
        status="failed",
        to=to,
        subject=subject,
        detail="The test email could not be sent. Try again later.",
    )
