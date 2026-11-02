"""Where money leaves from: the account's bank details, with the client's as the fallback.

`crm.contacts.bank_account` has held validated bank details since 0002 — captured by onboarding,
normalised by `contacts.normalise_bank_account`. Saving a bank account does not create a profile
at SEI. The SEI sync copies `PaidTo` onto `platform.accounts.bank_accounts` (`direction` `pay_to`,
`provider_ref` the profile id). Payer profiles are not in that file.

`platform.accounts.bank_accounts` (0120) is the account-level half, as a jsonb ARRAY. Resolution is
**account first, client second**, and that order is not a preference — it is the only rule that
fits both custodians, because they sit on opposite sides of it:

* **SEI** holds them per ACCOUNT: 682 `PaidTo` profiles over 283 accounts (124 with one, 151 with
  two, one house account with 213), and 439 distinct (sort code, account) pairs, so 63 bank
  accounts are shared across two or more accounts.
* **Seccl** holds them per CLIENT: `clients.bankDetails` populated on 10/10 in the committed
  extract and `accounts.bankDetails` on 0/33. Its withdrawal body names no destination at all —
  the destination is implicitly the client's verified account.

So an account-level entry wins where a custodian states one, and the client's is what answers for
everyone else.

**`[0]` is the default and the order is the firm's.** SEI is the only provider needing more than
one, so a caller takes the first and a picker appears only when there is more than one entry. The
order is preserved as stored rather than sorted here: a re-ordering is an operator's decision about
which account money normally goes to, and sorting would quietly overrule it. A SEI sync refreshes
the pay-to entries it copied and leaves the rest in the order stored, ahead of the latest file.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import bank_check
from app.contacts import normalise_bank_account

#: Money OUT versus money IN. SEI has a separate service for each —
#: `createpaytorecipientforaccount2` and `createpayerrecipientforaccount` — and the two are not
#: interchangeable: a pay-to profile is where a withdrawal is sent, a payer profile is who a
#: contribution is expected from.
DIRECTIONS = frozenset({"pay_to", "payer"})

#: The rails a custodian will accept. SEI's `PAY_TO_METHOD` has nine members and three are in live
#: use across the corpus (CHAPS 373, BACS 307, SWIFT 2); Seccl's account `paymentMethods` is
#: `Bank Transfer | Direct Debit` with `BACS Credit` as its pay-out rail. Canonical here, mapped to
#: each vendor's own code at the boundary — the `marital_status` rule.
METHODS = frozenset(
    {"bacs", "chaps", "faster_payment", "direct_debit", "cheque", "swift", "transfer"}
)

#: SEI's own spelling — `createPayToRecipientForAccount2.paymentMethod`, one of `["ACH", "Wire",
#: "Check", "BACS", "CHAPS", "SWIFT", "SEPA", "FASTERPAYMENT"]` (verified against the vendor's
#: OpenAPI spec, not guessed). Only the members a firm's bank details editor can actually choose
#: are mapped from here; the rest of `METHODS` has no live writer yet.
_SEI_PAYMENT_METHOD = {
    "bacs": "BACS",
    "faster_payment": "FASTERPAYMENT",
    "chaps": "CHAPS",
    "swift": "SWIFT",
    "transfer": "Wire",
    "direct_debit": "ACH",
    "cheque": "Check",
}


def sei_payment_method(entry: dict[str, Any]) -> str:
    """SEI's `paymentMethod` for a resolved pay-to entry.

    An explicit `method` wins; otherwise the RAIL decides, which is what "BACS is the default,
    SWIFT for international bank details held" means in practice — an entry with an IBAN has
    chosen the international rail, which only ever reaches SEI over SWIFT, and one without has
    chosen BACS, which is free. Never a guess beyond that: a method outside this mapping (there is
    no live writer for one yet) falls back to the same rail-based answer rather than being sent
    as-is."""
    method = str(entry.get("method") or "").strip().lower()
    if method in _SEI_PAYMENT_METHOD:
        return _SEI_PAYMENT_METHOD[method]
    return "SWIFT" if entry.get("iban") else "BACS"


#: Keys carried through untouched. `provider_ref` is the vendor's own profile id, which is what
#: `payerInfo.payerId` and `payToDetails` take — keeping it in the object rather than on
#: `platform.links` is what makes a second table unnecessary. `verification_status` is Seccl's
#: (Pending | Active | Bypassed; it says a withdrawal to a Pending account "will go to checking"):
#: a passthrough, with nothing branching on it, so we are not discarding a vendor field we may
#: later be asked about. IBAN is NOT here: it is a core rail of `normalise_bank_account` now, and
#: copying it through afterwards would replace the compact form with whatever the adviser typed.
_PASSTHROUGH = (
    "reference",
    "mandate_accepted_on",
    "provider",
    "provider_ref",
    "verification_status",
)


class BankAccountError(ValueError):
    """The details are not usable. Names the field, never the digits."""


def normalise_entry(value: object) -> dict[str, Any]:
    """One element of `platform.accounts.bank_accounts`.

    The sort code, account number, IBAN and BIC go through `contacts.normalise_bank_account` —
    the SAME normaliser the client-level column uses, so `12-34-56` and a compact IBAN mean the
    same thing wherever they are stored. Writing a second one is how two spellings of one sort
    code end up in one estate.
    """
    if not isinstance(value, dict):
        raise BankAccountError("Each bank account must be an object.")
    try:
        core = normalise_bank_account(value)
    except ValueError as exc:  # re-raised as ours, message unchanged (it names no digits)
        raise BankAccountError(str(exc)) from exc
    if core is None:
        raise BankAccountError("Enter the bank details, or omit the entry entirely.")

    entry: dict[str, Any] = dict(core)

    method = str(value.get("method") or "").strip().lower()
    if method:
        if method not in METHODS:
            raise BankAccountError(f"Unknown payment method; known: {sorted(METHODS)}.")
        entry["method"] = method

    direction = str(value.get("direction") or "").strip().lower()
    if direction:
        if direction not in DIRECTIONS:
            raise BankAccountError(f"Direction must be one of {sorted(DIRECTIONS)}.")
        entry["direction"] = direction

    for key in _PASSTHROUGH:
        raw = value.get(key)
        if raw is not None and str(raw).strip():
            entry[key] = str(raw).strip()
    return entry


def normalise_entries(value: object) -> list[dict[str, Any]] | None:
    """The whole array, or None to clear it.

    A LIST, and a bare object is refused rather than wrapped. A single object would read as one
    bank account and then fail every `[0]` in this module — the shape of a bug nobody notices until
    a payment. The database `CHECK` says the same thing; this is the half that gives a caller a
    sentence instead of a constraint name.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        raise BankAccountError(
            "Bank accounts must be a list — the first is the default, and a single account is a "
            "list of one."
        )
    if not isinstance(value, list):
        raise BankAccountError("Bank accounts must be a list.")
    entries = [normalise_entry(item) for item in value]
    return entries or None


def _as_entries(raw: object) -> list[dict[str, Any]]:
    """jsonb comes back as a list from psycopg and as text from a driver that has not adapted it."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def stored_entries(raw: object) -> list[dict[str, Any]] | None:
    """The account's own bank accounts as GET should project them.

    NULL and ``[]`` are the same reading — this account has none of its own, so the card falls
    back to the client's. A non-empty list is returned as stored (order preserved: ``[0]`` is
    the default).
    """
    found = _as_entries(raw)
    return found or None


def select(entries: list[dict[str, Any]], direction: str | None = None) -> dict[str, Any] | None:
    """The entry a payment should use: the first matching one, in the firm's own order.

    `direction` narrows to a pay-to (money out) or payer (money in) profile. An entry with NO
    direction matches either, because that is what an unstated one means — the client-level
    fallback carries none, and Seccl publishes one bank account that serves both ways.
    """
    for entry in entries:
        if direction is None or entry.get("direction") in (None, "", direction):
            return entry
    return None


async def resolve_for_account(
    session: AsyncSession, *, account_id: int, direction: str | None = None
) -> tuple[dict[str, Any] | None, str]:
    """`(entry, source)` for one account — `source` is `'account'`, `'contact'` or `'none'`.

    The source travels back with the entry deliberately. A caller filling a vendor form needs to
    say WHERE the destination came from: "the account's second BACS profile" and "the client's
    bank details, because this account has none" are different things to put in front of somebody
    about to move money, and collapsing them would make the form look equally authoritative either
    way.

    Reads through the caller's session, so RLS decides which account is visible: an account the
    caller cannot see returns `(None, 'none')` rather than raising, which is the same answer as an
    account with no details anywhere. That is deliberate — a distinguishable refusal here would
    tell an unauthorised caller that the account exists.
    """
    row = (
        (
            await session.execute(
                text(
                    "SELECT a.bank_accounts AS account_bank, c.bank_account AS contact_bank "
                    "FROM platform.accounts a "
                    "LEFT JOIN crm.contacts c "
                    "  ON c.id = a.contact_id AND c.tenant_id = a.tenant_id "
                    "WHERE a.id = :a"
                ),
                {"a": account_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None, "none"

    entry = select(_as_entries(row["account_bank"]), direction)
    if entry is not None:
        return entry, "account"

    # The client's, which is where onboarding and Seccl both put it. A single object, not a list,
    # and it carries no direction — so it answers for either.
    contact_bank = row["contact_bank"]
    if isinstance(contact_bank, str):
        try:
            contact_bank = json.loads(contact_bank)
        except ValueError:
            contact_bank = None
    if isinstance(contact_bank, dict) and contact_bank:
        return dict(contact_bank), "contact"
    return None, "none"


async def confirm_entries(
    session: AsyncSession,
    *,
    tenant_id: int,
    account_id: int,
    entries: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Each new UK pair asked of the sort-code directory. A pair already stored is left alone.

    An unreachable directory leaves that entry as it is. A rejection raises
    ``BankDetailsRejected``, which is a ``ValueError`` the route already turns into a 400.
    """
    if not entries:
        return entries
    stored_raw = (
        await session.execute(
            text("SELECT bank_accounts FROM platform.accounts WHERE id = :a AND tenant_id = :t"),
            {"a": account_id, "t": tenant_id},
        )
    ).scalar_one_or_none()
    stored_by_pair = {
        pair: item
        for item in _as_entries(stored_raw)
        if (pair := bank_check.uk_pair(item)) is not None
    }
    confirmed: list[dict[str, Any]] = []
    user_id = await bank_check.acting_user(session)
    for entry in entries:
        pair = bank_check.uk_pair(entry)
        if pair is None or pair in stored_by_pair:
            stored = stored_by_pair.get(pair) if pair is not None else None
            if (
                isinstance(stored, dict)
                and stored.get("bank")
                and not str(entry.get("bank") or "").strip()
            ):
                entry = {**entry, "bank": stored["bank"]}
            confirmed.append(entry)
            continue
        confirmed.append(await bank_check.verified_bank(entry, user_id=user_id))
    return confirmed


async def set_for_account(
    session: AsyncSession,
    *,
    tenant_id: int,
    account_id: int,
    entries: object,
    directory: bool = True,
) -> dict[str, Any]:
    """Set (or clear, with `None`) an account's bank accounts. Tenant-scoped.

    THE WRITER THIS MODULE HAD NONE OF. `normalise_entries` and its two closed vocabularies were
    reachable from nothing outside their own test, and every write to
    `platform.accounts.bank_accounts` in the repo was a test fixture — so `resolve_for_account`
    always fell through to the client's own details and the account branch it exists for could
    never be taken. Ninety lines of validators, a custom exception and a `jsonb_typeof = 'array'`
    CHECK were guarding a shape no code could produce.

    Its own function rather than a key on the account PATCH, for the reason
    `contacts.set_bank_account` gives: bank data gets one validated door, and the audit row it
    writes is then unambiguous about what changed. The return carries only the canonical entries
    back, never a wider row than the caller asked about.

    The ORDER as given is stored, not sorted — `[0]` is the default a payment takes, so which
    account money normally goes to is an operator's decision and sorting would quietly overrule it.

    ``directory`` is false only when the caller has already run ``confirm_entries`` on this
    payload. A review's approval leaves it true and asks again.
    """
    normalised = normalise_entries(entries)
    if directory:
        normalised = await confirm_entries(
            session, tenant_id=tenant_id, account_id=account_id, entries=normalised
        )
    row = (
        (
            await session.execute(
                text(
                    "UPDATE platform.accounts SET bank_accounts = CAST(:b AS jsonb) "
                    "WHERE id = :a AND tenant_id = :t "
                    "RETURNING id, uid, bank_accounts"
                ),
                {
                    "b": json.dumps(normalised) if normalised is not None else None,
                    "a": account_id,
                    "t": tenant_id,
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise LookupError("Account not found")
    return dict(row)
