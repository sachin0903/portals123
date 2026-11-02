"""What a drawer prefills, and where each value comes from.

`defaults_for` returned `{}` for anything but Morgan Lloyd, so **every Seccl and SEI drawer opened
blank** — including SEI's `create_cash_recipient`, the one form in the estate that can reach a sort
code, on which an operator typed the destination bank account by hand from nothing. Two of the
fields below had no source in the estate at all before 0119/0120: `portfolioId` (required by four
SEI write paths, and a `platform.links` row permits exactly one where 59 of 1,104 accounts carry
two) and the BACS trio (`crm.contacts.bank_account` had reached no vendor since 0002).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import platform_actions as pa
from tests.conftest import SECCL_PLATFORM_SQL


class TestLeafMatching:
    """SEI documents its fields as dotted paths into its JSON:API envelope, and eleven services
    name `accountNumber` with no two agreeing on the path. One resolved fact has to fill them all.
    """

    def test_the_leaf_is_the_last_segment(self) -> None:
        assert pa.leaf("createCashDisbursement.MemoDisbursement.accountNumber") == "accountNumber"
        assert pa.leaf("firmId") == "firmId", "a flat name is its own leaf"

    def test_one_account_number_fills_every_path_that_names_it(self) -> None:
        described = {
            a["key"]: a["defaults"]
            for a in pa.describe("sei", account={"accountNumber": "MSP000762"})
        }
        # Three different envelopes, one fact.
        assert described["update_account_details"] == {"data.attributes.accountNumber": "MSP000762"}
        assert described["update_dsr_tags"] == {
            "data.attributes.searchCriteria.accountNumber": "MSP000762"
        }
        assert described["assign_implementation_model"] == {"accountNumber": "MSP000762"}

    def test_a_leaf_appearing_twice_in_one_action_fills_both(self) -> None:
        """`createCashDisbursement` documents an accountNumber under two envelopes. They name the
        same account, so both are prefilled — this is not ambiguity."""
        disbursement = next(
            a
            for a in pa.describe("sei", account={"accountNumber": "MSP1"})
            if a["key"] == "create_cash_disbursement"
        )
        filled = [n for n in disbursement["defaults"] if n.endswith("accountNumber")]
        assert filled, "the disbursement drawer must know which account it pays from"
        assert all(disbursement["defaults"][n] == "MSP1" for n in filled)

    def test_only_fields_this_action_documents_are_prefilled(self) -> None:
        """So a drawer never shows a value that is not part of its contract."""
        for action in pa.describe("sei", account={"portfolioId": "58251749"}):
            documented = {f["name"] for f in action["fields"]}
            assert set(action["defaults"]) <= documented, action["key"]


class TestTenantDefaults:
    def test_seis_firm_id_comes_from_the_enablement(self) -> None:
        """Required by 17 of SEI's 26 authorised services, and it was never prefilled."""
        out = pa.tenant_defaults("sei", {"firm_id": "3106"})
        assert out["firmId"] == "3106"

    def test_the_contribution_description_code_is_prefilled(self) -> None:
        out = pa.tenant_defaults(
            "sei", {"firm_id": "3106", "contribution_description_code": "21121"}
        )
        assert out["descriptionTypeCode"] == "21121"

    def test_the_treatment_code_is_deliberately_not(self) -> None:
        """`contribution_treatment_codes` is a MAP keyed by category, because the treatment code is
        what encodes personal-vs-employer at SEI. There is no single value to prefill, and picking
        one would put an employer contribution's code on a personal one."""
        out = pa.tenant_defaults(
            "sei",
            {
                "firm_id": "3106",
                "contribution_treatment_codes": {"personal": "4958", "employer": "4959"},
            },
        )
        assert "treatmentTypeCode" not in out

    def test_a_map_or_list_value_never_becomes_a_form_string(self) -> None:
        """Guards the general case: `str({'personal': '4958'})` in a vendor payload is a Python
        repr sent to a custodian."""
        out = pa.tenant_defaults("sei", {"firm_id": "3106", "dsr_ids": ["59261", "59262"]})
        assert list(out) == ["firmId"]

    def test_seccl_declares_no_firm_field(self) -> None:
        """Its action paths interpolate `{firm}` themselves, so there is no form field. Declared
        empty rather than omitted, so the absence is a statement."""
        assert pa.tenant_defaults("seccl", {"node_ids": ["1"]}) == {}


class TestPrecedence:
    def test_the_account_beats_the_firm(self) -> None:
        """Most specific last. A firm-level default must never override the account the operator
        actually opened the drawer on."""
        described = {
            a["key"]: a["defaults"]
            for a in pa.describe(
                "sei",
                slice_config={"firm_id": "3106"},
                account={"accountNumber": "MSP-REAL"},
            )
        }
        got = described["update_account_details"]
        assert got["data.attributes.firmId"] == "3106"
        assert got["data.attributes.accountNumber"] == "MSP-REAL"

    def test_morgan_lloyd_still_resolves_from_its_connection(self) -> None:
        """The behaviour that already existed must not have moved."""
        described = pa.describe("morgan-lloyd", {"firm_id": "6ac39921-d8b7-f011-bbd3-7ced8d6b85ff"})
        assert any(a["defaults"] for a in described), "ML's source-config defaults still apply"


class TestAccountDefaults:
    """The half that needed schema. Read through the caller's session, so RLS decides what is
    visible — and an account nobody may see answers the same as one with nothing configured."""

    @pytest.fixture
    def book(self, super_engine: Engine) -> dict[str, int | str]:
        tag = uuid.uuid4().hex[:8]
        out: dict[str, int | str] = {}
        with super_engine.connect() as c:
            c.execute(text(SECCL_PLATFORM_SQL))
            tenant = int(
                c.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) "
                        "VALUES (:u, :a, :n) RETURNING id"
                    ),
                    {"u": f"padten_{tag}", "a": f"padfirm-{tag}", "n": f"Pad {tag}"},
                ).scalar_one()
            )
            contact = int(
                c.execute(
                    text(
                        "INSERT INTO crm.contacts (tenant_id, uid, type, surname) "
                        "VALUES (:t, :u, 'individual', 'Kennedy') RETURNING id"
                    ),
                    {"t": tenant, "u": f"padcon_{tag}"},
                ).scalar_one()
            )
            account = int(
                c.execute(
                    text(
                        "INSERT INTO platform.accounts "
                        "  (tenant_id, contact_id, uid, status, currency, bank_accounts) "
                        "VALUES (:t, :c, :u, 'active', 'GBP', CAST(:b AS jsonb)) RETURNING id"
                    ),
                    {
                        "t": tenant,
                        "c": contact,
                        "u": f"padacc_{tag}",
                        "b": (
                            '[{"name":"Mr E Kennedy","sort_code":"20-45-67",'
                            ' "account_number":"12345678","direction":"pay_to","method":"bacs"}]'
                        ),
                    },
                ).scalar_one()
            )
            c.execute(
                text(
                    "INSERT INTO platform.links "
                    "  (tenant_id, entity_type, entity_id, provider, external_id) "
                    "VALUES (:t, 'account', :e, 'sei', :x)"
                ),
                {"t": tenant, "e": account, "x": f"MSP{tag}"},
            )
            # A Capital and an Income portfolio, which is the shape 59 of 1,104 SEI accounts have.
            for ext, principal in ((f"CAP{tag}", True), (f"INC{tag}", False)):
                c.execute(
                    text(
                        "INSERT INTO platform.portfolios "
                        "  (tenant_id, account_id, provider, external_id, is_principal) "
                        "VALUES (:t, :a, 'sei', :x, :p)"
                    ),
                    {"t": tenant, "a": account, "x": ext, "p": principal},
                )
            c.commit()
        out.update(
            {"tenant_id": tenant, "account_id": account, "number": f"MSP{tag}", "cap": f"CAP{tag}"}
        )
        return out

    @pytest.mark.anyio
    async def test_it_resolves_the_account_number_portfolio_and_bank(
        self, db: dict[str, str], book: dict[str, int | str]
    ) -> None:
        engine = create_async_engine(db["app"])
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE service_role"))
            out = await pa.account_defaults(session, "sei", int(book["account_id"]))
        await engine.dispose()

        assert out["accountNumber"] == book["number"]
        assert out["portfolioId"] == book["cap"], "the PRINCIPAL portfolio, not either of the two"
        assert out["currencyCode"] == "GBP"
        # The destination, from the account's own pay-to entry. Digits for the vendor; the book
        # stores the readable form.
        assert out["bacsDestinationSortCode"] == "204567"
        assert out["bacsPayToAccountNumber"] == "12345678"
        assert out["bacsAccountName"] == "Mr E Kennedy"
        # The fixture's own stored `"method":"bacs"` — see `TestSeiPaymentMethod` in
        # `test_bank_account_resolution.py` for the mapping itself.
        assert out["paymentMethod"] == "BACS"

    @pytest.mark.anyio
    async def test_an_unknown_account_yields_nothing(self, db: dict[str, str]) -> None:
        engine = create_async_engine(db["app"])
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE service_role"))
            assert await pa.account_defaults(session, "sei", -1) == {}
        await engine.dispose()

    @pytest.mark.anyio
    async def test_it_never_returns_a_field_outside_the_declared_set(
        self, db: dict[str, str], book: dict[str, int | str]
    ) -> None:
        """The resolver decides what an account answers; a stray key would reach a vendor payload
        through `describe`'s leaf match without ever being reviewed."""
        engine = create_async_engine(db["app"])
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE service_role"))
            out = await pa.account_defaults(session, "sei", int(book["account_id"]))
        await engine.dispose()
        assert set(out) <= set(pa._ACCOUNT_FIELDS)
