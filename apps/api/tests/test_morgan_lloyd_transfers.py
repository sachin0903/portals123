"""Recording a transfer at Morgan Lloyd, reading it back, and withdrawing it.

`platform.transfers` was built to track `cam_createtransfer`'s required list (0051 says so) and
nothing had ever sent one, on any branch. So the whole adapter is new and this is its first
coverage — with the emphasis on the four places the vendor's contract is easy to get wrong in a
way that no exception reports:

* the three-L `cam_uncrystalllisedtransfervalue`;
* `cam_status` (the record's own, fixed at Transferring) vs `transferstatus` (the six-value
  progress set the mirror stores) — two option sets that both read as "the status";
* `cam_nonprtv`, which is the WHOLE transfer value despite the name;
* and the three required fields, where a DEFAULT would be the real damage: sending Cash for an
  in-specie transfer instructs a ceding scheme to sell a client's portfolio, and the vendor
  cannot object because both values are valid. ``basis`` and ``extent`` are columns (0147);
  ``pension_type`` is a column as of 0173 (the raise drawer asks it of every pension).

The contract assertions read `vendor_manifest.json` rather than a second copy of the field list,
the way `test_morgan_lloyd_contact_contract.py` does — three of these fields exist only because
the vendor asks for them, so the vendor's own document is the right referee.
"""

from typing import Any

import httpx
import pytest
from morgan_lloyd import enums
from morgan_lloyd.manifest import Field, operation

from app import transfers as domain
from app.integrations.morgan_lloyd import transfers as adapter
from app.integrations.morgan_lloyd.benefits import MorganLloydNotConfigured

_PRESENT = "configured"

#: A transfer with everything the vendor needs, in the shape `app.transfers._SELECT` returns
#: plus the two link columns a caller joins on.
TRANSFER: dict[str, Any] = {
    "uid": "trf000000001",
    "account_id": 900,
    "contact_id": 41,
    "ceding_provider": "Aviva",
    "policy_number": "AV-88213771",
    "transfer_type": "pension",
    "basis": "in_specie",
    "extent": "full",
    "crystallisation": "uncrystallised",
    "pension_type": 18,
    "crystallised_value": 0.0,
    "uncrystallised_value": 184_320.55,
    "total_value": 184_320.55,
    "currency": "GBP",
    "status": "requested",
    "note": "Client wants the drawdown set up as soon as the funds land.",
    "ml_transfer_id": None,
    "ml_scheme_ref": "MLSIPP/01417",
    "ml_contact_id": "9f2c1c76-1111-2222-3333-444455556666",
    "data": {"basis": "in_specie", "extent": "full"},
}


class _FakeApi:
    def __init__(self, **answers: Any) -> None:
        self.answers = answers
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def __aenter__(self) -> "_FakeApi":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def _answer(self, action: str, *args: Any) -> Any:
        self.calls.append((action, args))
        answer = self.answers.get(action, {})
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def create_transfer(self, body: dict[str, Any]) -> Any:
        return self._answer("create_transfer", body)

    async def get_transfer(self, client_reference: str, policy_no: str) -> Any:
        return self._answer("get_transfer", client_reference, policy_no)

    async def update_transfer(self, transfer_id: str, update: dict[str, Any]) -> Any:
        return self._answer("update_transfer", transfer_id, update)

    def called(self, action: str) -> list[tuple[Any, ...]]:
        return [args for name, args in self.calls if name == action]


def _install(monkeypatch: pytest.MonkeyPatch, stub: _FakeApi) -> None:
    class _Settings:
        tenant_id = client_id = client_secret = _PRESENT

    monkeypatch.setattr(adapter, "configured", lambda: True)
    monkeypatch.setattr(adapter, "_settings", lambda: _Settings())
    import morgan_lloyd.client as vendor

    monkeypatch.setattr(vendor, "MorganLloydClient", lambda settings=None: stub)


@pytest.fixture(scope="module")
def documented() -> dict[str, Field]:
    found = operation("cam_createtransfer")
    assert found is not None, "cam_createtransfer is missing from vendor_manifest.json"
    return {f.name: f for f in found.fields}


class TestMissingFields:
    def test_a_complete_transfer_has_nothing_missing(self) -> None:
        assert adapter.missing_fields(TRANSFER) == []

    def test_an_unlinked_account_names_the_scheme_reference(self) -> None:
        absent = adapter.missing_fields({**TRANSFER, "ml_scheme_ref": None})
        assert any("scheme reference" in item for item in absent)

    @pytest.mark.parametrize("column", ["ceding_provider", "policy_number"])
    def test_the_ceding_plan_must_be_identified(self, column: str) -> None:
        assert adapter.missing_fields({**TRANSFER, column: None})

    @pytest.mark.parametrize(
        "column", ["total_value", "crystallised_value", "uncrystallised_value"]
    )
    def test_all_three_values_are_required_and_null_is_not_zero(self, column: str) -> None:
        """ "We do not know the crystallised split" and "none of it is crystallised" are
        different facts. Sending 0 for the first asserts the second, on a pension."""
        absent = adapter.missing_fields({**TRANSFER, column: None})
        assert absent
        assert any("enter 0" in item for item in absent) or "total" in absent[0]

    def test_zero_is_accepted_as_an_answer(self) -> None:
        """It is `None` that is missing, not falsiness — a fully-uncrystallised pension has a
        crystallised value of exactly 0 and that is the common case."""
        assert adapter.missing_fields({**TRANSFER, "crystallised_value": 0}) == []

    def test_a_missing_basis_is_named_and_says_why_it_matters(self) -> None:
        absent = adapter.missing_fields({**TRANSFER, "basis": None, "data": {"extent": "full"}})
        assert any("cash or in-specie" in item for item in absent)
        assert any("sell" in item for item in absent)

    def test_a_missing_extent_is_named(self) -> None:
        absent = adapter.missing_fields({**TRANSFER, "extent": None, "data": {"basis": "cash"}})
        assert any("full or partial" in item for item in absent)


class TestTheBasisIsNeverGuessed:
    """`cam_typeoftransfer` is required, both values are valid, and they mean opposite things."""

    def test_the_column_wins_over_data(self) -> None:
        """0147's home for the fact. jsonb is only a fallback for rows raised before it."""
        assert adapter.basis_of({**TRANSFER, "basis": "cash", "data": {"basis": "in_specie"}}) == (
            "cash"
        )

    def test_data_is_the_fallback_when_the_column_is_empty(self) -> None:
        assert adapter.basis_of({**TRANSFER, "basis": None, "data": {"basis": "cash"}}) == "cash"

    def test_transfer_type_answers_it_where_the_column_says_so(self) -> None:
        """0051's CHECK mixes wrappers and bases, so `cash`/`in_specie` in that column DO say."""
        assert adapter.basis_of({"transfer_type": "in_specie"}) == "in_specie"
        assert adapter.basis_of({"transfer_type": "cash"}) == "cash"

    @pytest.mark.parametrize("wrapper", ["pension", "isa", "gia"])
    def test_a_wrapper_in_that_column_says_nothing_about_the_basis(self, wrapper: str) -> None:
        assert adapter.basis_of({"transfer_type": wrapper}) == ""

    def test_data_wins_over_transfer_type(self) -> None:
        """A row that states its basis in jsonb means it, even when transfer_type says otherwise."""
        assert adapter.basis_of({"transfer_type": "cash", "data": {"basis": "in_specie"}}) == (
            "in_specie"
        )

    def test_an_unknown_basis_is_refused_not_coerced(self) -> None:
        with pytest.raises(ValueError, match="cash or in-specie"):
            adapter.build_transfer(
                {**TRANSFER, "basis": None, "data": {"basis": "partial", "extent": "full"}}
            )


class TestBuildTransfer:
    def test_the_uncrystallised_field_keeps_the_vendors_three_ls(
        self, documented: dict[str, Field]
    ) -> None:
        """Verbatim, do not correct it. The column is spelled properly and this is the mangling."""
        payload = adapter.build_transfer(TRANSFER)
        assert "cam_uncrystalllisedtransfervalue" in payload
        assert "cam_uncrystallisedtransfervalue" not in payload
        # And the vendor really does spell it that way — not our typo preserved by a test.
        assert "cam_uncrystalllisedtransfervalue" in documented

    def test_the_total_goes_in_the_confusingly_named_field(self) -> None:
        """`cam_nonprtv` reads as the non-protected slice and is the WHOLE transfer value."""
        payload = adapter.build_transfer(TRANSFER)
        assert payload["cam_nonprtv"] == 184_320.55

    def test_the_record_status_is_transferring_not_a_progress_value(self) -> None:
        """`cam_status` is `AssetStatus`; `transferstatus` is the six-value progress set. Sending
        a progress value here would set the record's own status to something else entirely."""
        payload = adapter.build_transfer(TRANSFER)
        assert payload["cam_status"] == 4
        assert payload["cam_status"] != domain.ML_AWAITING_FORMS

    def test_the_basis_and_extent_map_onto_the_vendors_option_sets(self) -> None:
        payload = adapter.build_transfer(TRANSFER)
        assert payload["cam_typeoftransfer"] == enums.TypeOfTransfer.IN_SPECIE
        assert payload["cam_transfertype"] == enums.TransferType.FULL

    def test_cash_and_partial_map_too(self) -> None:
        payload = adapter.build_transfer({**TRANSFER, "basis": "cash", "extent": "partial"})
        assert payload["cam_typeoftransfer"] == enums.TypeOfTransfer.CASH
        assert payload["cam_transfertype"] == enums.TransferType.PARTIAL

    def test_money_is_a_json_number(self) -> None:
        from decimal import Decimal

        payload = adapter.build_transfer(
            {**TRANSFER, "total_value": Decimal("184320.55"), "crystallised_value": Decimal("0")}
        )
        for field in (
            "cam_nonprtv",
            "cam_crystallisedtransfervalue",
            "cam_uncrystalllisedtransfervalue",
        ):
            assert isinstance(payload[field], float)

    def test_the_note_becomes_a_vendor_note(self) -> None:
        """`transferNotes` creates a Note against the Transfer — what a chasing conversation at
        the scheme administrator actually runs on."""
        payload = adapter.build_transfer(TRANSFER)
        assert payload["transferNotes"] == TRANSFER["note"]

    def test_no_note_sends_no_note_field(self) -> None:
        payload = adapter.build_transfer({**TRANSFER, "note": None})
        assert "transferNotes" not in payload

    def test_the_pension_type_is_omitted_on_a_non_pension(self) -> None:
        payload = adapter.build_transfer(
            {**TRANSFER, "transfer_type": "isa", "pension_type": None, "data": {}}
        )
        assert "cam_pensiontype" not in payload

    def test_a_pension_without_a_type_is_named(self) -> None:
        absent = adapter.missing_fields({**TRANSFER, "pension_type": None, "data": {}})
        assert any("pension type" in item for item in absent)

    def test_a_stated_pension_type_is_sent_as_an_int(self) -> None:
        payload = adapter.build_transfer({**TRANSFER, "pension_type": 2})
        assert payload["cam_pensiontype"] == enums.PensionType.ANNUITY

    def test_data_is_the_fallback_when_the_column_is_empty(self) -> None:
        payload = adapter.build_transfer(
            {**TRANSFER, "pension_type": None, "data": {**TRANSFER["data"], "pension_type": "2"}}
        )
        assert payload["cam_pensiontype"] == enums.PensionType.ANNUITY

    def test_an_unreadable_pension_type_is_refused(self) -> None:
        with pytest.raises(ValueError, match="PensionType"):
            adapter.build_transfer(
                {
                    **TRANSFER,
                    "pension_type": None,
                    "data": {**TRANSFER["data"], "pension_type": "annuity"},
                }
            )

    def test_uncrystallised_maps_to_full(self) -> None:
        payload = adapter.build_transfer(TRANSFER)
        assert payload["cam_crystallised"] == enums.CrystallisedType.FULL

    def test_partial_crystallisation_maps_to_partial(self) -> None:
        payload = adapter.build_transfer(
            {
                **TRANSFER,
                "crystallisation": "partial",
                "crystallised_value": 50_000,
                "uncrystallised_value": 134_320.55,
            }
        )
        assert payload["cam_crystallised"] == enums.CrystallisedType.PARTIAL

    def test_safeguarded_rights_are_ours_not_the_vendors(self) -> None:
        payload = adapter.build_transfer({**TRANSFER, "safeguarded_benefits": True})
        assert not [key for key in payload if "safeguard" in key.lower()]

    def test_pension_types_are_the_vendors_option_set(self) -> None:
        """The Create Transfer field table concatenates Other and Personal Pension Plan into one
        option. The enum and the description list are the 1–20 set; we do not copy the garbled
        row."""
        assert set(domain.PENSION_TYPES) == {int(member) for member in enums.PensionType}
        assert domain.PENSION_TYPES[13] == "Other"
        assert domain.PENSION_TYPES[14] == "Personal Pension Plan"

    def test_the_ceding_providers_address_is_sent_only_where_held(self) -> None:
        bare = adapter.build_transfer(TRANSFER)
        assert not [key for key in bare if key.startswith("cam_provideraddress")]
        rich = adapter.build_transfer(
            {
                **TRANSFER,
                "data": {
                    **TRANSFER["data"],
                    "provider_address_line1": "Aviva, Wellington Row",
                    "provider_city": "York",
                    "provider_postcode": "YO90 1WR",
                },
            }
        )
        # VERBATIM. An address is not a lookup key, and case-folding one does not fail
        # anything — it just puts a mangled address on a real transfer instruction.
        assert rich["cam_provideraddressline1"] == "Aviva, Wellington Row"
        assert rich["cam_providerpostcode"] == "YO90 1WR"

    @pytest.mark.parametrize("spelling", ["in_specie", "In Specie", "IN-SPECIE", " in specie "])
    def test_a_lookup_value_is_normalised_but_an_address_is_not(self, spelling: str) -> None:
        """The two reads are separate for exactly this reason. `basis` may be typed by a person
        or copied from the vendor's own label ("In Specie"), so all of those are one answer —
        refusing one would read as "you did not say" to somebody who did. "Wellington Row" has
        to survive unchanged."""
        payload = adapter.build_transfer(
            {
                **TRANSFER,
                "data": {
                    "basis": spelling,
                    "extent": "FULL",
                    "provider_city": "York",
                    "provider_address_line1": "Wellington Row",
                },
            }
        )
        assert payload["cam_typeoftransfer"] == enums.TypeOfTransfer.IN_SPECIE
        assert payload["cam_transfertype"] == enums.TransferType.FULL
        assert payload["cam_providercity"] == "York"
        assert payload["cam_provideraddressline1"] == "Wellington Row"


class TestEveryFieldIsDocumented:
    """The same one-directional check the contact mapper carries: Dynamics ignores a field it
    does not recognise, so a wrong name is a silent no-op that reports success."""

    def test_no_field_is_undocumented(self, documented: dict[str, Field]) -> None:
        payload = adapter.build_transfer(
            {
                **TRANSFER,
                "data": {
                    **TRANSFER["data"],
                    "pension_type": "2",
                    "provider_address_line1": "a",
                    "provider_address_line2": "b",
                    "provider_address_line3": "c",
                    "provider_city": "d",
                    "provider_county": "e",
                    "provider_postcode": "f",
                },
            }
        )
        unknown = sorted(set(payload) - set(documented))
        assert not unknown, f"cam_createtransfer does not document: {unknown}"

    def test_every_unconditionally_required_field_is_sent(
        self, documented: dict[str, Field]
    ) -> None:
        payload = adapter.build_transfer(TRANSFER)
        required = {
            name for name, field in documented.items() if field.required and not field.conditional
        }
        assert required <= set(payload), f"not sent: {sorted(required - set(payload))}"

    def test_option_set_values_are_documented_members(self, documented: dict[str, Field]) -> None:
        payload = adapter.build_transfer(
            {**TRANSFER, "data": {**TRANSFER["data"], "pension_type": "2"}}
        )
        for name, value in payload.items():
            field = documented[name]
            if field.options:
                allowed = {option.value for option in field.options}
                assert int(value) in allowed, f"{name}={value} is not a documented member"

    def test_int_fields_are_ints_and_decimals_are_numbers(
        self, documented: dict[str, Field]
    ) -> None:
        payload = adapter.build_transfer(TRANSFER)
        for name, value in payload.items():
            declared = documented[name].type.strip().lower()
            if declared == "int":
                assert isinstance(value, int) and not isinstance(value, bool), name
            elif declared == "decimal":
                assert isinstance(value, float | int), name


class TestCreateTransfer:
    async def test_records_the_transfer_and_returns_the_guid(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = _FakeApi(
            create_transfer={
                "transferId": {"cam_personalpolicyid": "aa11bb22-3333-4444-5555-666677778888"}
            }
        )
        _install(monkeypatch, stub)
        guid = await adapter.create_transfer(TRANSFER)
        assert guid == "aa11bb22-3333-4444-5555-666677778888"

    @pytest.mark.parametrize(
        "envelope",
        [
            {"transferId": "aa11bb22-3333-4444-5555-666677778888"},
            {"transferId": {"cam_personalpolicyid": "aa11bb22-3333-4444-5555-666677778888"}},
            {"response": {"transferId": "aa11bb22-3333-4444-5555-666677778888"}},
            {"cam_personalpolicyid": "aa11bb22-3333-4444-5555-666677778888"},
            {"message": "Transfer aa11bb22-3333-4444-5555-666677778888 created"},
        ],
    )
    async def test_the_guid_is_found_wherever_the_envelope_puts_it(
        self, monkeypatch: pytest.MonkeyPatch, envelope: dict[str, Any]
    ) -> None:
        """The `create_contact` scar: the custom actions answer an envelope, not a flat body, and
        reading only the flat spelling meant creating the record and discarding its id."""
        stub = _FakeApi(create_transfer=envelope)
        _install(monkeypatch, stub)
        assert await adapter.create_transfer(TRANSFER) == ("aa11bb22-3333-4444-5555-666677778888")

    async def test_an_answer_with_no_id_is_unavailable_and_warns_about_retrying(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The record may exist. A retry would create a second instruction to a ceding scheme, so
        the error has to say so rather than reading as "nothing happened"."""
        stub = _FakeApi(create_transfer={"message": "Created"})
        _install(monkeypatch, stub)
        with pytest.raises(adapter.MorganLloydUnavailable, match="retry"):
            await adapter.create_transfer(TRANSFER)

    async def test_missing_details_refuse_before_the_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = _FakeApi()
        _install(monkeypatch, stub)
        with pytest.raises(ValueError):
            await adapter.create_transfer({**TRANSFER, "basis": None, "extent": None, "data": {}})
        assert stub.calls == []

    async def test_no_credentials_is_not_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(adapter, "configured", lambda: False)
        with pytest.raises(MorganLloydNotConfigured):
            await adapter.create_transfer(TRANSFER)

    async def test_a_vendor_failure_is_unavailable_not_a_crash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = _FakeApi(create_transfer=httpx.ConnectError("boom"))
        _install(monkeypatch, stub)
        with pytest.raises(adapter.MorganLloydUnavailable):
            await adapter.create_transfer(TRANSFER)


class TestFetchStatus:
    async def test_reads_the_progress_value_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = _FakeApi(get_transfer={"transferstatus": domain.ML_AWAITING_CEDING_SCHEME})
        _install(monkeypatch, stub)
        assert await adapter.fetch_status(TRANSFER) == domain.ML_AWAITING_CEDING_SCHEME

    async def test_it_is_looked_up_by_the_client_guid_and_the_ceding_policy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`cam_getpensiontransfer` is keyed by the CLIENT's Dynamics GUID plus the ceding policy
        number — the vendor gives us a transfer id and then offers no lookup for it."""
        stub = _FakeApi(get_transfer={"transferstatus": 0})
        _install(monkeypatch, stub)
        await adapter.fetch_status(TRANSFER)
        assert stub.called("get_transfer") == [
            ("9f2c1c76-1111-2222-3333-444455556666", "AV-88213771")
        ]

    async def test_the_value_is_read_out_of_the_envelope_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = _FakeApi(get_transfer={"response": {"transferstatus": 3}})
        _install(monkeypatch, stub)
        assert await adapter.fetch_status(TRANSFER) == 3

    async def test_an_unlisted_transfer_is_none_not_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = _FakeApi(get_transfer={"lastnote": "nothing here"})
        _install(monkeypatch, stub)
        assert await adapter.fetch_status(TRANSFER) is None

    async def test_an_unpushed_client_says_so_rather_than_answering_not_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A transfer that exists cannot be read back without the client link, and "not found"
        would send an adviser looking for the wrong problem."""
        stub = _FakeApi()
        _install(monkeypatch, stub)
        with pytest.raises(ValueError, match="pushed to Morgan Lloyd"):
            await adapter.fetch_status({**TRANSFER, "ml_contact_id": None})
        assert stub.calls == []

    async def test_no_policy_number_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = _FakeApi()
        _install(monkeypatch, stub)
        with pytest.raises(ValueError, match="policy number"):
            await adapter.fetch_status({**TRANSFER, "policy_number": None})

    def test_the_mirror_stores_the_progress_set_this_returns(self) -> None:
        """`record_ml_status` refuses an unknown value, so the two vocabularies must agree — and
        the record's own `cam_status` (4 = Transferring) is a DIFFERENT set that happens to
        overlap numerically, which is exactly why this is asserted."""
        assert set(domain.ML_STATUS_LABELS) == {v.value for v in enums.TransferStatus}


class TestCancelTransfer:
    RECORDED = {**TRANSFER, "ml_transfer_id": "aa11bb22-3333-4444-5555-666677778888"}

    async def test_withdraws_a_transfer_that_has_not_moved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = _FakeApi(
            get_transfer={"transferstatus": domain.ML_AWAITING_CEDING_SCHEME},
            update_transfer="OData-EntityId",
        )
        _install(monkeypatch, stub)
        result = await adapter.cancel_transfer(self.RECORDED)

        assert result["ml_status"] == domain.ML_NO_LONGER_TRANSFERRING
        assert stub.called("update_transfer") == [
            (
                "aa11bb22-3333-4444-5555-666677778888",
                {"cam_transferstatus": domain.ML_NO_LONGER_TRANSFERRING},
            )
        ]

    @pytest.mark.parametrize("reached", [domain.ML_FUNDS_RECEIVED, domain.ML_FUNDS_SENT])
    async def test_it_refuses_once_the_money_has_moved(
        self, monkeypatch: pytest.MonkeyPatch, reached: int
    ) -> None:
        """Past "funds received" the ceding scheme has already paid. Withdrawing then is not a
        cancellation but a return of money arranged with two counterparties, so the refusal names
        the vendor's current status rather than failing generically."""
        stub = _FakeApi(get_transfer={"transferstatus": reached})
        _install(monkeypatch, stub)
        with pytest.raises(adapter.TransferNotCancellable) as exc:
            await adapter.cancel_transfer(self.RECORDED)

        assert domain.ML_STATUS_LABELS[reached] in str(exc.value)
        assert stub.called("update_transfer") == []

    async def test_an_already_withdrawn_transfer_is_idempotent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = _FakeApi(get_transfer={"transferstatus": domain.ML_NO_LONGER_TRANSFERRING})
        _install(monkeypatch, stub)
        result = await adapter.cancel_transfer(self.RECORDED)

        assert result["response"] == {"already_cancelled": True}
        assert stub.called("update_transfer") == []

    async def test_the_gate_reads_the_vendor_not_our_mirror(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The mirror is only as fresh as the last refresh, and the whole point of the gate is
        that somebody else may have moved the record on — `NotAnApplication`'s reasoning."""
        stub = _FakeApi(get_transfer={"transferstatus": domain.ML_FUNDS_SENT})
        _install(monkeypatch, stub)
        stale = {**self.RECORDED, "ml_status": domain.ML_AWAITING_FORMS}
        with pytest.raises(adapter.TransferNotCancellable):
            await adapter.cancel_transfer(stale)

    async def test_an_unrecorded_transfer_cannot_be_cancelled_there(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = _FakeApi()
        _install(monkeypatch, stub)
        with pytest.raises(ValueError, match="not been recorded"):
            await adapter.cancel_transfer(TRANSFER)
        assert stub.calls == []

    async def test_an_unlisted_transfer_is_still_withdrawn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A None progress means the vendor does not list it under that policy number. That is
        not evidence the money has moved, so the withdrawal proceeds rather than refusing."""
        stub = _FakeApi(get_transfer={}, update_transfer=None)
        _install(monkeypatch, stub)
        result = await adapter.cancel_transfer(self.RECORDED)
        assert result["ml_status"] == domain.ML_NO_LONGER_TRANSFERRING
