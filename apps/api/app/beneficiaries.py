"""Beneficiary nominations — who a member's pension pays out to, and in what share.

`crm.beneficiaries` (0127) hangs off the ACCOUNT, not the contact, because that is the grain the
nomination has: `cam_createbeneficiaries` is addressed by `schemeReference`, a member with two
pensions can nominate differently on each, and expression-of-wish forms are signed per scheme.

Reads go through RLS; writes land here on a service_role `firm_session`, so every statement is
EXPLICITLY scoped to the caller's own `tenant_id` — the same discipline as `app.contacts`.
"""

import json
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: Wrappers a nomination applies to. A beneficiary nomination is a pension instrument: an ISA or a
#: GIA passes under the will and through probate, so offering one there would invite a firm to
#: record a wish that has no effect.
#:
#: THE FALLBACK, not the rule. `platform.products.is_pension` is the authority for this panel
#: (0136) and is what SHOWS it: `account-detail.svelte` gates `<Beneficiaries>` on
#: `account.is_pension ?? isPensionProduct(...)`. This set stays only for a row whose shelf item
#: states nothing — the display half moved and the write half did not, so a firm that ticked
#: `is_pension` on a wrapper outside this set (which is the stated reason the column is editable)
#: got the panel, filled a nomination, and got a 422 that reads as a bug in the panel.
PENSION_PRODUCTS = frozenset({"SIPP", "PENSION", "SSAS", "TPPENSION"})

_SELECT = """
SELECT b.id, b.uid, b.account_id, b.contact_id, b.title, b.name, b.relationship,
       b.proportion::float8 AS proportion, b.nominated_on, b.address, b.note,
       b.created_at, b.updated_at
FROM crm.beneficiaries b
"""


class BeneficiaryConflict(RuntimeError):
    """The nomination set's current state forbids the change — a 409, not a 400.

    A distinct type rather than a ValueError the router splits on message text, for the reason
    `contributions.ContributionConflict` records: that pattern is one reworded message away from
    silently downgrading a conflict to a bad request.
    """


async def _account(session: AsyncSession, *, tenant_id: int, account_id: int) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                text(
                    "SELECT a.id, a.contact_id, p.product_type, p.is_pension "
                    "FROM platform.accounts a "
                    "  LEFT JOIN platform.products p ON p.id = a.product_id "
                    "WHERE a.id = :a AND a.tenant_id = :t"
                ),
                {"a": account_id, "t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise LookupError("Account not found")
    product = str(row["product_type"] or "").upper()
    # THE SHELF FIRST, then the wrapper set — the same order the panel that offers this form uses,
    # so what can be SAVED is what was SHOWN. A ticked `is_pension` settles it; the set decides
    # only for a shelf item that states nothing.
    #
    # An UNMAPPED product is allowed through, deliberately. `product_id` is NULL for any account
    # the provider pipeline could not resolve a product for (SEI's House Account, an unmodelled
    # wrapper), and refusing there would mean a real SIPP whose product mapping is missing cannot
    # record a nomination — a data-quality problem punishing the member. A wrapper we KNOW is not a
    # pension is refused; one we cannot name is not claimed to be either way.
    if row["is_pension"]:
        return dict(row)
    if product and product not in PENSION_PRODUCTS:
        raise ValueError("Beneficiary nominations apply to pension accounts only.")
    return dict(row)


async def list_for_account(session: AsyncSession, *, account_id: int) -> list[dict[str, Any]]:
    """The account's nominations, largest share first.

    By share rather than by name or by creation order: the set is read to answer "who gets what",
    and that is easiest to check against 100% with the biggest at the top.

    NO `tenant_id` — and the asymmetry with the writes below is deliberate, not an oversight. This
    runs on an RLS-bound session (`book_session`), where `beneficiaries_read` already scopes to the
    caller's lineage or their own contact scope; adding a tenant predicate here would be a second,
    weaker copy of that policy in application code, and the day the two disagree the policy is the
    one that is right. The writes take an explicit `tenant_id` because they run as `service_role`,
    which BYPASSES RLS — there the scoping has nowhere else to live. Same split as `app.contacts`.
    """
    rows = (
        await session.execute(
            text(f"{_SELECT} WHERE b.account_id = :a ORDER BY b.proportion DESC, b.name"),
            {"a": account_id},
        )
    ).mappings()
    return [dict(r) for r in rows]


def _proportion(value: Any) -> Decimal:
    try:
        share = Decimal(str(value))
    except (TypeError, ArithmeticError) as exc:
        raise ValueError("Enter the share as a percentage.") from exc
    if share <= 0 or share > 100:
        raise ValueError("A beneficiary's share must be more than 0% and no more than 100%.")
    return share


async def add(
    session: AsyncSession,
    *,
    tenant_id: int,
    account_id: int,
    created_by: str | None,
    name: str,
    proportion: Any,
    title: str | None = None,
    relationship: str | None = None,
    nominated_on: Any = None,
    address: dict[str, Any] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Nominate a beneficiary against a pension account.

    Refuses a set that would exceed 100%, and NOT one that falls short. The asymmetry is the whole
    rule: a nomination set is built a row at a time, so demanding it total 100 would make the FIRST
    insert fail — adding a 50% beneficiary to an empty account refused for totalling 50. Over 100
    is unambiguously wrong at every point in the edit; under 100 is only wrong when the firm says
    it is finished, which is a judgement the UI states (a running total) rather than one the write
    path can make.
    """
    account = await _account(session, tenant_id=tenant_id, account_id=account_id)
    # SERIALISED PER ACCOUNT, which is what makes the check below a control rather than a
    # suggestion. The over-100 refusal is a read of `sum(proportion)` and then an INSERT, and
    # under READ COMMITTED two advisers — or one double-submitted form — each see the old total
    # and each insert, leaving an expression of wish over 100% in the one record the scheme
    # administrator reads when paying out death benefits.
    #
    # The same lock `instructions.py` takes for the same shape ("without the lock two concurrent
    # withdrawals both read the same headroom and both pass the cumulative gate"), on the ACCOUNT
    # row because that is what the total is per. A constraint cannot express it: 0134's header is
    # right that `= 100` would fail the first 50% insert, and the rule actually enforced is
    # `<= 100`.
    await session.execute(
        text("SELECT id FROM platform.accounts WHERE id = :a AND tenant_id = :t FOR UPDATE"),
        {"a": account_id, "t": tenant_id},
    )
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("A beneficiary needs a name.")
    share = _proportion(proportion)

    allocated = Decimal(
        str(
            (
                await session.execute(
                    text(
                        "SELECT COALESCE(sum(proportion), 0) FROM crm.beneficiaries "
                        "WHERE tenant_id = :t AND account_id = :a"
                    ),
                    {"t": tenant_id, "a": account_id},
                )
            ).scalar_one()
        )
    )
    if allocated + share > 100:
        raise BeneficiaryConflict(
            f"That would allocate {allocated + share}% of the fund. "
            f"{100 - allocated}% is still unallocated."
        )

    uid = (
        await session.execute(
            text(
                "INSERT INTO crm.beneficiaries "
                "  (tenant_id, account_id, contact_id, title, name, relationship, proportion, "
                "   nominated_on, address, note, created_by) "
                "VALUES (:t, :a, :c, :title, :name, :rel, :share, :on, "
                "        CAST(:addr AS jsonb), :note, "
                "        (SELECT id FROM auth.users WHERE workos_user_id = :by)) "
                "RETURNING uid"
            ),
            {
                "t": tenant_id,
                "a": account_id,
                "c": account["contact_id"],
                "title": (title or "").strip() or None,
                "name": clean_name,
                "rel": (relationship or "").strip() or None,
                "share": share,
                "on": nominated_on,
                "addr": _json(address),
                "note": (note or "").strip() or None,
                "by": created_by,
            },
        )
    ).scalar_one()
    record = await get(session, tenant_id=tenant_id, uid=uid)
    assert record is not None  # noqa: S101 - just inserted, same transaction
    return record


async def get(session: AsyncSession, *, tenant_id: int, uid: str) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text(f"{_SELECT} WHERE b.tenant_id = :t AND b.uid = :u"),
                {"t": tenant_id, "u": uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


async def remove(session: AsyncSession, *, tenant_id: int, account_id: int, uid: str) -> None:
    """Delete a nomination.

    SCOPED BY `account_id`, not by the uid alone. The route resolves the account so the URL names
    a book the caller may write, and that only holds if the statement APPLIES it: this session is
    `service_role` (`firm_book_write_session`), so RLS cannot backstop a uid that belongs to
    another client's account — and the delete is hard by design, so there is nothing to restore.
    The nomination is the only instruction the scheme administrator has about who a pension pays
    out to.

    A hard DELETE, not a soft one, and this is the one place in the client record where that is
    right: a superseded expression of wish is not history to preserve but a statement the member
    has WITHDRAWN, and a retained row is one careless read away from paying out to somebody the
    member removed. The audit trigger keeps the deletion itself (0127), so who removed what and
    when is still answerable — which is what the trail is actually for.
    """
    # RETURNING rather than `rowcount`: SQLAlchemy types `execute` as `Result[Any]`, which has no
    # `rowcount` attribute to typecheck against, and a scalar answer is the same question asked in
    # a way the type system can see.
    removed = (
        await session.execute(
            text(
                "DELETE FROM crm.beneficiaries "
                " WHERE tenant_id = :t AND account_id = :a AND uid = :u RETURNING uid"
            ),
            {"t": tenant_id, "a": account_id, "u": uid},
        )
    ).scalar_one_or_none()
    if removed is None:
        raise LookupError("Beneficiary not found")


def _json(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    cleaned = {k: v for k, v in value.items() if v not in (None, "")}
    return json.dumps(cleaned) if cleaned else None
