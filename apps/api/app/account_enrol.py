"""Create or resume a locally-activated account on the platforms its product names.

Onboarding ``setup_account`` activates the Graphene draft first. This module then:

- ensures SIPP products are mapped to every enabled scheme administrator (so a firm
  that enabled Morgan Lloyd before the attach existed still gets the link on the
  journey that needs it). A third-party pension is not mapped — Morgan Lloyd does
  not administer it;
- ensures GIA, ISA, JISA and SIPP name SEI as custodian when the firm has SEI
  enabled, and stamps that custodian on an account that still has none and no
  other custodian to choose;
- creates or resumes the client, scheme and policy at the administrator via
  ``morgan_lloyd.sync_client`` (idempotent);
- leaves custodian enrolment as ``pending_ops``. Seccl ``ClientCreate`` exists only as
  an admin-console action (nested address / bank / node are not mapped from Graphene);
  SEI enrolment is an eight-level journey and is deliberately unbuilt.

Failures here do not fail the journey: Morgan Lloyd commonly 422s on missing contact
fields, and a completed local hand-off with an ops-visible report is the recoverable
state. Staff retry from the account's Sync button.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import providers

logger = logging.getLogger("account_enrol")

_ACCOUNT_SQL = """
SELECT a.id, a.contact_id, a.product_id, a.custodian::text AS custodian,
       p.product_type::text AS product_type
FROM platform.accounts a
LEFT JOIN platform.products p ON p.id = a.product_id
WHERE a.id = :a AND a.tenant_id = :t
"""

_ADMIN_SQL = """
SELECT pp.provider::text
FROM platform.product_platforms pp
WHERE pp.product_id = :p AND pp.tenant_id = :t
  AND pp.provider = ANY(:admins)
  AND COALESCE(pp.enabled, true)
ORDER BY (pp.role = 'scheme_administrator') DESC NULLS LAST
LIMIT 1
"""


def _is_administered(row: dict[str, Any]) -> bool:
    return providers.administered(str(row.get("product_type") or ""))


async def sync_activated_account(
    session: AsyncSession, *, tenant_id: int, account_id: int
) -> dict[str, Any]:
    """Push this activated account to its scheme administrator; leave the custodian queued.

    Returns a per-platform report stamped onto ``platform.accounts.data.platform_sync``.
    Never raises: the journey has already activated the book locally, and a vendor
    refusal must not roll that back.
    """
    report: dict[str, Any] = {
        "custodian": {"status": "pending_ops"},
        "administrator": {"status": "skipped"},
    }
    try:
        row = (
            (await session.execute(text(_ACCOUNT_SQL), {"a": account_id, "t": tenant_id}))
            .mappings()
            .one_or_none()
        )
    except (AttributeError, TypeError):
        return report
    if not row:
        return report
    account = dict(row)

    if account.get("custodian"):
        report["custodian"] = {
            "provider": account["custodian"],
            "status": "pending_ops",
            "reason": "enrolment_unbuilt",
        }

    product_id = _int_or_none(account.get("product_id"))
    if product_id is not None:
        try:
            await providers.attach_scheme_administrators(session, tenant_id, product_id=product_id)
            await providers.attach_custodians(session, tenant_id, product_id=product_id)
        except Exception:
            logger.exception(
                "Failed to attach a provider for product %s on account %s",
                product_id,
                account_id,
            )
        if not account.get("custodian"):
            stamped = (
                await session.execute(
                    text(
                        "SELECT custodian::text FROM platform.accounts "
                        "WHERE id = :a AND tenant_id = :t"
                    ),
                    {"a": account_id, "t": tenant_id},
                )
            ).scalar_one_or_none()
            if stamped:
                account["custodian"] = str(stamped)
                report["custodian"] = {
                    "provider": account["custodian"],
                    "status": "pending_ops",
                    "reason": "enrolment_unbuilt",
                }

    if not _is_administered(account):
        report["administrator"] = {"status": "skipped", "reason": "no_administrator"}
        return await _stamp(session, tenant_id, account_id, report)

    admin = await _scheme_administrator(session, tenant_id, product_id)
    if not admin:
        report["administrator"] = {"status": "skipped", "reason": "no_administrator"}
        return await _stamp(session, tenant_id, account_id, report)

    contact_id = account.get("contact_id")
    if not contact_id:
        report["administrator"] = {
            "provider": admin,
            "status": "skipped",
            "reason": "no_contact",
        }
        return await _stamp(session, tenant_id, account_id, report)

    if admin == "morgan-lloyd":
        report["administrator"] = await _sync_morgan_lloyd(
            session, tenant_id=tenant_id, contact_id=int(contact_id), account_id=account_id
        )
    else:
        report["administrator"] = {
            "provider": admin,
            "status": "skipped",
            "reason": "unsupported",
        }
    return await _stamp(session, tenant_id, account_id, report)


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _scheme_administrator(
    session: AsyncSession, tenant_id: int, product_id: int | None
) -> str | None:
    if product_id is None:
        return None
    try:
        return (
            await session.execute(
                text(_ADMIN_SQL),
                {
                    "p": product_id,
                    "t": tenant_id,
                    "admins": list(providers.administrators()),
                },
            )
        ).scalar_one_or_none()
    except (AttributeError, TypeError):
        return None


async def _sync_morgan_lloyd(
    session: AsyncSession, *, tenant_id: int, contact_id: int, account_id: int
) -> dict[str, Any]:
    from app.integrations import morgan_lloyd

    out: dict[str, Any] = {"provider": "morgan-lloyd"}
    if not morgan_lloyd.configured():
        out["status"] = "skipped"
        out["reason"] = "unconfigured"
        return out
    try:
        result = await morgan_lloyd.sync_client(
            session, tenant_id=tenant_id, contact_id=contact_id, account_id=account_id
        )
        out["status"] = "synced"
        out["report"] = result
        return out
    except morgan_lloyd.MorganLloydNotConfigured:
        out["status"] = "skipped"
        out["reason"] = "unconfigured"
        return out
    except morgan_lloyd.MorganLloydUnavailable as exc:
        out["status"] = "failed"
        out["reason"] = str(exc)
        return out
    except (LookupError, ValueError) as exc:
        out["status"] = "failed"
        out["reason"] = str(exc)
        return out
    except Exception as exc:
        logger.exception("Morgan Lloyd sync failed for account %s", account_id)
        out["status"] = "failed"
        out["reason"] = str(exc)
        return out


async def _stamp(
    session: AsyncSession, tenant_id: int, account_id: int, report: dict[str, Any]
) -> dict[str, Any]:
    marker = {"platform_sync": {"at": datetime.now(UTC).isoformat(), **report}}
    try:
        await session.execute(
            text(
                "UPDATE platform.accounts "
                "SET data = COALESCE(data, '{}'::jsonb) || CAST(:marker AS jsonb), "
                "    updated_at = now() "
                "WHERE id = :a AND tenant_id = :t"
            ),
            {
                "a": account_id,
                "t": tenant_id,
                "marker": json.dumps(marker),
            },
        )
    except (AttributeError, TypeError):
        pass
    return report
