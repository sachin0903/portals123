"""Where a payment's destination comes from: the account, then the client, then nothing.

`crm.contacts.bank_account` has held validated bank details since 0002 and reached no vendor —
a grep across `app/integrations/` finds no bank field in any payload we send. 0120 adds the
account-level half, and this pins the resolution order, because the two custodians sit on opposite
sides of it: SEI holds pay-to profiles per ACCOUNT (682 over 283 accounts, 151 of them holding
two), Seccl per CLIENT (10/10 populated on clients, 0/33 on accounts).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import bank_accounts


@asynccontextmanager
async def _firm(app_url: str) -> AsyncIterator[AsyncSession]:
    """A service_role session — mirrors `db.py::firm_session`, the write path that would fill a
    vendor form."""
    engine = create_async_engine(app_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        yield session
    await engine.dispose()


@pytest.fixture
def book(super_engine: Engine) -> dict[str, int]:
    """One firm, one client, one account — the shape a payment is instructed against."""
    tag = uuid.uuid4().hex[:8]
    out: dict[str, int] = {}
    with super_engine.connect() as c:
        out["tenant_id"] = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"bankten_{tag}", "a": f"bankfirm-{tag}", "n": f"Bank {tag}"},
            ).scalar_one()
        )
        out["contact_id"] = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, surname) "
                    "VALUES (:t, :u, 'individual', 'Kennedy') RETURNING id"
                ),
                {"t": out["tenant_id"], "u": f"bankcon_{tag}"},
            ).scalar_one()
        )
        out["account_id"] = int(
            c.execute(
                text(
                    "INSERT INTO platform.accounts (tenant_id, contact_id, uid, status, value) "
                    "VALUES (:t, :con, :u, 'active', 1000.00) RETURNING id"
                ),
                {"t": out["tenant_id"], "con": out["contact_id"], "u": f"bankacc_{tag}"},
            ).scalar_one()
        )
        c.commit()
    return out


VALID = {"name": "Mr E Kennedy", "sort_code": "204567", "account_number": "12345678"}


class TestNormalisation:
    def test_it_uses_the_same_normaliser_as_the_client_column(self) -> None:
        """One sort-code format in the estate, not two. `12-34-56` and an 8-digit account number
        mean the same thing wherever they are stored."""
        entry = bank_accounts.normalise_entry(VALID)
        assert entry["sort_code"] == "20-45-67"
        assert entry["account_number"] == "12345678"

    def test_an_international_entry_uses_the_same_iban_normaliser(self) -> None:
        """SEI has live SWIFT pay-to profiles. An IBAN-only entry must survive the same door the
        client column uses, not fail for want of a sort code."""
        entry = bank_accounts.normalise_entry(
            {
                "name": "Mr E Kennedy",
                "iban": "GB82 WEST 1234 5698 7654 32",
                "bic": "BARCGB22",
                "method": "swift",
            }
        )
        assert entry["iban"] == "GB82WEST12345698765432"
        assert entry["bic"] == "BARCGB22"
        assert entry["method"] == "swift"
        assert "sort_code" not in entry

    def test_a_seven_digit_account_is_padded(self) -> None:
        """The Bacs convention — a bank quoting seven digits means a leading zero."""
        entry = bank_accounts.normalise_entry({**VALID, "account_number": "1234567"})
        assert entry["account_number"] == "01234567"

    def test_a_bare_object_is_refused_not_wrapped(self) -> None:
        """A single object would read as one bank account and then fail every `[0]` in the module —
        the shape of a bug nobody notices until a payment."""
        with pytest.raises(bank_accounts.BankAccountError, match="must be a list"):
            bank_accounts.normalise_entries(VALID)

    def test_an_unknown_method_is_refused_by_name(self) -> None:
        with pytest.raises(bank_accounts.BankAccountError, match="Unknown payment method"):
            bank_accounts.normalise_entry({**VALID, "method": "carrier_pigeon"})

    def test_an_unknown_direction_is_refused(self) -> None:
        with pytest.raises(bank_accounts.BankAccountError, match="Direction must be"):
            bank_accounts.normalise_entry({**VALID, "direction": "sideways"})

    def test_the_vendor_profile_id_survives(self) -> None:
        """`provider_ref` is SEI's own `PROFILE_ID`, which is what `payerInfo.payerId` takes.
        Keeping it in the object is what makes a second table unnecessary."""
        entry = bank_accounts.normalise_entry(
            {**VALID, "provider": "sei", "provider_ref": "59220399"}
        )
        assert (entry["provider"], entry["provider_ref"]) == ("sei", "59220399")

    def test_a_vendor_verification_status_is_carried_not_dropped(self) -> None:
        """Seccl publishes it and says a withdrawal to a Pending account "will go to checking".
        Nothing branches on it; it is not discarded either."""
        entry = bank_accounts.normalise_entry({**VALID, "verification_status": "Pending"})
        assert entry["verification_status"] == "Pending"

    def test_an_empty_list_clears_rather_than_storing_nothing(self) -> None:
        assert bank_accounts.normalise_entries([]) is None

    def test_a_blank_entry_is_refused_rather_than_silently_dropped(self) -> None:
        with pytest.raises(bank_accounts.BankAccountError):
            bank_accounts.normalise_entries([{}])


class TestSeiPaymentMethod:
    """What `create_cash_recipient`'s `paymentMethod` prefills from — the vendor's own spelling,
    verified against its OpenAPI enum (`["ACH", "Wire", "Check", "BACS", "CHAPS", "SWIFT", "SEPA",
    "FASTERPAYMENT"]`), not guessed."""

    def test_an_explicit_method_is_mapped_to_the_vendors_spelling(self) -> None:
        assert bank_accounts.sei_payment_method({"method": "bacs"}) == "BACS"
        assert bank_accounts.sei_payment_method({"method": "faster_payment"}) == "FASTERPAYMENT"
        assert bank_accounts.sei_payment_method({"method": "swift"}) == "SWIFT"

    def test_an_unstated_method_falls_back_to_the_rail(self) -> None:
        """ "BACS is the default, SWIFT for international bank details held" — a UK entry with no
        method reads as BACS, and an IBAN entry (which has nothing to choose) reads as SWIFT."""
        assert bank_accounts.sei_payment_method(VALID) == "BACS"
        assert (
            bank_accounts.sei_payment_method({"iban": "GB82WEST12345698765432", "bic": "BARCGB22"})
            == "SWIFT"
        )


class TestSelection:
    def test_the_first_entry_is_the_default(self) -> None:
        """And the order is the firm's: re-ordering is an operator's decision about which account
        money normally goes to, so nothing sorts here."""
        entries = [{"account_number": "1"}, {"account_number": "2"}]
        assert bank_accounts.select(entries)["account_number"] == "1"

    def test_a_direction_narrows_to_the_right_profile(self) -> None:
        """SEI has a separate service for each and they are not interchangeable: a pay-to profile
        is where a withdrawal goes, a payer profile is who a contribution comes from."""
        entries = [
            {"account_number": "in", "direction": "payer"},
            {"account_number": "out", "direction": "pay_to"},
        ]
        assert bank_accounts.select(entries, "pay_to")["account_number"] == "out"
        assert bank_accounts.select(entries, "payer")["account_number"] == "in"

    def test_an_undirected_entry_answers_either_way(self) -> None:
        """Which is what an unstated direction means — Seccl publishes one bank account that
        serves both ways, and the client-level fallback carries none."""
        entries = [{"account_number": "both"}]
        assert bank_accounts.select(entries, "pay_to")["account_number"] == "both"
        assert bank_accounts.select(entries, "payer")["account_number"] == "both"

    def test_no_match_is_none_not_the_wrong_profile(self) -> None:
        entries = [{"account_number": "in", "direction": "payer"}]
        assert bank_accounts.select(entries, "pay_to") is None


class TestTheResolutionOrder:
    """account, then client, then nothing — with the SOURCE reported, because a form about to move
    money must be able to say which it used. "The account's second BACS profile" and "the client's
    details, because this account has none" are different things to put in front of somebody."""

    @pytest.mark.anyio
    async def test_the_accounts_own_details_win(
        self, db: dict[str, str], super_engine: Engine, book: dict[str, int]
    ) -> None:
        """SEI's shape: the destination is a profile on the account."""
        with super_engine.connect() as c:
            c.execute(
                text("UPDATE platform.accounts SET bank_accounts = CAST(:b AS jsonb) WHERE id=:a"),
                {
                    "b": '[{"name":"A","sort_code":"11-11-11","account_number":"11111111"}]',
                    "a": book["account_id"],
                },
            )
            c.execute(
                text("UPDATE crm.contacts SET bank_account = CAST(:b AS jsonb) WHERE id=:c"),
                {
                    "b": '{"name":"C","sort_code":"22-22-22","account_number":"22222222"}',
                    "c": book["contact_id"],
                },
            )
            c.commit()
        async with _firm(db["app"]) as session:
            entry, source = await bank_accounts.resolve_for_account(
                session, account_id=book["account_id"]
            )
        assert source == "account"
        assert entry is not None and entry["sort_code"] == "11-11-11"

    @pytest.mark.anyio
    async def test_it_falls_back_to_the_client(
        self, db: dict[str, str], super_engine: Engine, book: dict[str, int]
    ) -> None:
        """Seccl's shape: nothing on the account, and the client's verified details ARE the
        destination — its withdrawal body names none at all."""
        with super_engine.connect() as c:
            c.execute(
                text("UPDATE crm.contacts SET bank_account = CAST(:b AS jsonb) WHERE id=:c"),
                {
                    "b": '{"name":"C","sort_code":"22-22-22","account_number":"22222222"}',
                    "c": book["contact_id"],
                },
            )
            c.commit()
        async with _firm(db["app"]) as session:
            entry, source = await bank_accounts.resolve_for_account(
                session, account_id=book["account_id"]
            )
        assert source == "contact"
        assert entry is not None and entry["sort_code"] == "22-22-22"

    @pytest.mark.anyio
    async def test_no_details_anywhere_is_reported_as_such(
        self, db: dict[str, str], book: dict[str, int]
    ) -> None:
        """Not an empty dict: a caller must be able to refuse rather than send a blank
        destination."""
        async with _firm(db["app"]) as session:
            entry, source = await bank_accounts.resolve_for_account(
                session, account_id=book["account_id"]
            )
        assert (entry, source) == (None, "none")

    @pytest.mark.anyio
    async def test_an_unknown_account_answers_the_same_as_an_empty_one(
        self, db: dict[str, str]
    ) -> None:
        """Deliberately indistinguishable: a separate refusal would tell an unauthorised caller
        that the account exists."""
        async with _firm(db["app"]) as session:
            assert await bank_accounts.resolve_for_account(session, account_id=-1) == (None, "none")

    @pytest.mark.anyio
    async def test_the_direction_narrows_the_accounts_own_profiles(
        self, db: dict[str, str], super_engine: Engine, book: dict[str, int]
    ) -> None:
        """SEI keeps pay-to and payer profiles on the same account, and paying a contribution's
        payer profile would send money to whoever it came from."""
        with super_engine.connect() as c:
            c.execute(
                text("UPDATE platform.accounts SET bank_accounts = CAST(:b AS jsonb) WHERE id=:a"),
                {
                    "b": (
                        '[{"name":"In","sort_code":"33-33-33","account_number":"33333333",'
                        '  "direction":"payer"},'
                        ' {"name":"Out","sort_code":"44-44-44","account_number":"44444444",'
                        '  "direction":"pay_to"}]'
                    ),
                    "a": book["account_id"],
                },
            )
            c.commit()
        async with _firm(db["app"]) as session:
            out, _ = await bank_accounts.resolve_for_account(
                session, account_id=book["account_id"], direction="pay_to"
            )
            inbound, _ = await bank_accounts.resolve_for_account(
                session, account_id=book["account_id"], direction="payer"
            )
        assert out is not None and out["sort_code"] == "44-44-44"
        assert inbound is not None and inbound["sort_code"] == "33-33-33"


class TestTheWriter:
    """THE HALF THAT DID NOT EXIST.

    `normalise_entries` and its two closed vocabularies had no caller outside this file, and every
    write to `platform.accounts.bank_accounts` in the repo was a test fixture — no PATCH allowlist
    entry, no provider projection, no UI control, no pipeline staging. So ninety lines of
    validators, a custom exception type and the `jsonb_typeof = 'array'` CHECK were guarding a shape
    no code could produce, and `resolve_for_account` ALWAYS fell through to the client: the account
    branch above, and the whole account-first rule it pins, was unreachable in production.

    `PUT /crm/accounts/{uid}/bank-accounts` is the writer (`bank_accounts.set_for_account`), gated
    `instructions:write` like the client-level door.
    """

    @pytest.mark.anyio
    async def test_it_stores_the_normalised_entries_in_the_order_given(
        self, db: dict[str, str], book: dict[str, int]
    ) -> None:
        """The ORDER as given, not sorted: `[0]` is the default a payment takes, so which account
        money normally goes to is an operator's decision."""
        async with _firm(db["app"]) as session:
            saved = await bank_accounts.set_for_account(
                session,
                tenant_id=book["tenant_id"],
                account_id=book["account_id"],
                entries=[
                    {"name": "Second", "sort_code": "22-22-22", "account_number": "22222222"},
                    {"name": "First", "sort_code": "11 11 11", "account_number": "11111111"},
                ],
            )
        entries = saved["bank_accounts"]
        assert isinstance(entries, list)
        assert [e["name"] for e in entries] == ["Second", "First"]
        # Through the SAME normaliser the client-level column uses, so `11 11 11` and `11-11-11`
        # are one sort code wherever they are stored — canonicalised to `11-11-11`, and the
        # account number left-padded to eight digits (the Bacs convention).
        assert entries[1]["sort_code"] == "11-11-11"
        assert entries[1]["account_number"] == "11111111"

    @pytest.mark.anyio
    async def test_what_it_writes_is_what_resolution_then_reads(
        self, db: dict[str, str], book: dict[str, int]
    ) -> None:
        """The point of the whole finding: the writer and the reader are the two ends of the
        account-first rule, and until now only one end existed."""
        async with _firm(db["app"]) as session:
            await bank_accounts.set_for_account(
                session,
                tenant_id=book["tenant_id"],
                account_id=book["account_id"],
                entries=[
                    {
                        "name": "Payout",
                        "sort_code": "11-11-11",
                        "account_number": "11111111",
                        "direction": "pay_to",
                        "method": "bacs",
                    }
                ],
            )
            entry, source = await bank_accounts.resolve_for_account(
                session, account_id=book["account_id"], direction="pay_to"
            )
        assert source == "account"
        assert entry is not None
        assert entry["name"] == "Payout"

    @pytest.mark.anyio
    async def test_null_clears_and_resolution_falls_back_again(
        self, db: dict[str, str], super_engine: Engine, book: dict[str, int]
    ) -> None:
        with super_engine.connect() as c:
            c.execute(
                text("UPDATE crm.contacts SET bank_account = CAST(:b AS jsonb) WHERE id=:c"),
                {
                    "b": '{"account_name":"C","sort_code":"22-22-22","account_number":"22222222"}',
                    "c": book["contact_id"],
                },
            )
            c.commit()
        async with _firm(db["app"]) as session:
            await bank_accounts.set_for_account(
                session,
                tenant_id=book["tenant_id"],
                account_id=book["account_id"],
                entries=[{"name": "A", "sort_code": "11-11-11", "account_number": "11111111"}],
            )
            await bank_accounts.set_for_account(
                session,
                tenant_id=book["tenant_id"],
                account_id=book["account_id"],
                entries=None,
            )
            _entry, source = await bank_accounts.resolve_for_account(
                session, account_id=book["account_id"]
            )
        assert source == "contact"

    @pytest.mark.anyio
    async def test_a_bare_object_is_refused_before_the_check_constraint_names_itself(
        self, db: dict[str, str], book: dict[str, int]
    ) -> None:
        """The validation this endpoint exists to reach. The DB CHECK says the same thing; this is
        the half that gives a caller a sentence."""
        async with _firm(db["app"]) as session:
            with pytest.raises(bank_accounts.BankAccountError, match="list"):
                await bank_accounts.set_for_account(
                    session,
                    tenant_id=book["tenant_id"],
                    account_id=book["account_id"],
                    entries={"name": "A", "sort_code": "11-11-11", "account_number": "11111111"},
                )

    @pytest.mark.anyio
    async def test_an_unknown_method_is_refused(
        self, db: dict[str, str], book: dict[str, int]
    ) -> None:
        async with _firm(db["app"]) as session:
            with pytest.raises(bank_accounts.BankAccountError, match="payment method"):
                await bank_accounts.set_for_account(
                    session,
                    tenant_id=book["tenant_id"],
                    account_id=book["account_id"],
                    entries=[
                        {
                            "name": "A",
                            "sort_code": "11-11-11",
                            "account_number": "11111111",
                            "method": "carrier_pigeon",
                        }
                    ],
                )

    @pytest.mark.anyio
    async def test_another_firms_account_is_not_writable(
        self, db: dict[str, str], book: dict[str, int]
    ) -> None:
        """The UPDATE is scoped to the caller's own `tenant_id` — never a client-supplied one —
        which is `app.contacts`' discipline for every write on a `service_role` session."""
        async with _firm(db["app"]) as session:
            with pytest.raises(LookupError):
                await bank_accounts.set_for_account(
                    session,
                    tenant_id=book["tenant_id"] + 10_000,
                    account_id=book["account_id"],
                    entries=[{"name": "A", "sort_code": "11-11-11", "account_number": "11111111"}],
                )


class TestTheGetProjection:
    """NULL and `[]` are the same reading on GET: this account has none of its own."""

    def test_empty_is_none(self) -> None:
        assert bank_accounts.stored_entries(None) is None
        assert bank_accounts.stored_entries([]) is None
        assert bank_accounts.stored_entries("[]") is None

    def test_a_list_of_objects_is_kept_in_order(self) -> None:
        assert bank_accounts.stored_entries([{"name": "Second"}, {"name": "First"}]) == [
            {"name": "Second"},
            {"name": "First"},
        ]
