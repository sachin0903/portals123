"""The vendor's per-connection reference values, on the row that names the firm.

They live on `platform.tenant_platforms.config` since 0133, beside the slice filter. They were on
`platform.providers` -- one row per upstream connection -- and before that in deployment env,
which is wrong for a multi-tenant platform in a specific and silent way: one
`MORGAN_LLOYD_SCHEME_TYPE` cannot hold two firms' values, so the second firm onboarded receives
the first firm's, into a system with no delete.

WHY ONE TABLE. `platform.providers` held Morgan Lloyd rows and nothing else, deliberately, and
ML's connection is one Microsoft tenant per firm -- so the join to the enablement was always 1:1
for the only provider that used it. `resolve_source_config` walked it and REFUSED when a firm held
two, because the wrong choice sends one firm's adviser-firm GUID with another firm's client; that
refusal was guarding a shape nothing could act on, and it cannot arise now.

The rule these pin hardest survives the move: `firm_id` is NOT the Microsoft tenant id. Three
GUIDs are easy to confuse and were -- the Azure DIRECTORY (the OAuth tenant, stamped as
`ml_tenant_id`, and the slice key), the adviser FIRM (a Dynamics `accounts` row) and the adviser
CONTACT (a person).
"""

from __future__ import annotations

from typing import Any

import pytest

from app import platform_actions as pa
from app import providers as providers_mod

FIRM = "6ac39921-d8b7-f011-bbd3-7ced8d6b85ff"
DIRECTORY = "02492031-c828-4a6e-9e27-e07493fb397e"


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None


class _Session:
    """Answers the ONE lookup `resolve_source_config` makes (0133; it used to make two)."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.statements: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, clause: Any, params: dict[str, Any] | None = None) -> _Result:
        self.statements.append((str(clause), dict(params or {})))
        return _Result([self.answers.pop(0)] if self.answers else [])


class TestTheEnablementCarriesTheVendorValues:
    """One validator now (`validate_config`), because there is one config. It used to be two
    functions over two tables, which is how `adviser_contact_id` came to be shape-checked in one
    and not the other."""

    def test_the_documented_shape_passes(self) -> None:
        providers_mod.validate_config(
            "morgan-lloyd",
            {
                "tenant_ids": [DIRECTORY],
                "firm_id": FIRM,
                "scheme_type": 16,
                "sipp_type": 809130016,
            },
        )

    def test_a_partial_config_is_fine(self) -> None:
        """Half-configured is a real state — the firm id arrives from the access document before
        anyone has decided the scheme pairing. The vendor values are OPTIONAL keys, and
        `sync.py` refuses a half-configured PRODUCT rather than topping it up, which is what
        makes optional safe here."""
        providers_mod.validate_config("morgan-lloyd", {"tenant_ids": [DIRECTORY]})
        providers_mod.validate_config("morgan-lloyd", {"tenant_ids": [DIRECTORY], "firm_id": FIRM})

    def test_the_slice_key_is_still_required(self) -> None:
        """Folding the vendor values in must not make the FILTER optional: a firm enabled on no
        Microsoft tenant syncs the whole Dynamics org."""
        with pytest.raises(ValueError, match="tenant_ids"):
            providers_mod.validate_config("morgan-lloyd", {"firm_id": FIRM})

    def test_an_unknown_key_is_refused(self) -> None:
        """A typo would store cleanly, resolve to nothing, and leave scheme creation refusing for
        a value the operator can see on screen."""
        with pytest.raises(ValueError, match="scheme_typ"):
            providers_mod.validate_config(
                "morgan-lloyd", {"tenant_ids": [DIRECTORY], "scheme_typ": 16}
            )

    def test_an_option_set_must_be_a_number(self) -> None:
        with pytest.raises(ValueError, match="whole number"):
            providers_mod.validate_config(
                "morgan-lloyd", {"tenant_ids": [DIRECTORY], "scheme_type": "sixteen"}
            )

    def test_a_boolean_is_not_a_number(self) -> None:
        """`True` is an int in Python and 1 is not a documented scheme type."""
        with pytest.raises(ValueError, match="whole number"):
            providers_mod.validate_config(
                "morgan-lloyd", {"tenant_ids": [DIRECTORY], "scheme_type": True}
            )

    def test_the_adviser_contact_must_look_like_a_guid(self) -> None:
        with pytest.raises(ValueError, match="NOT the Microsoft tenant id"):
            providers_mod.validate_config(
                "morgan-lloyd", {"tenant_ids": [DIRECTORY], "adviser_contact_id": "a-person"}
            )

    def test_the_refusal_names_the_confusion_it_is_guarding(self) -> None:
        """The message has to say WHICH GUID, because the tenant id is well-formed and wrong —
        it would be accepted by the vendor as a reference and record nonsense."""
        with pytest.raises(ValueError, match="Azure directory"):
            providers_mod.validate_config(
                "morgan-lloyd", {"tenant_ids": [DIRECTORY], "adviser_contact_id": "not-a-guid"}
            )

    def test_firm_id_is_deliberately_not_guid_checked(self) -> None:
        """ONE NAME, TWO MEANINGS, and this is the reason the shape check does not follow the key
        across providers: SEI's `firm_id` is its slice firm and is not a GUID. Constraining the
        name rather than the provider would refuse every SEI enablement."""
        providers_mod.validate_config("sei", {"firm_id": "SELCT"})

    def test_a_provider_with_no_vendor_config_refuses_any(self) -> None:
        """Seccl publishes no analogue. Accepting the keys for it would invent a contract."""
        with pytest.raises(ValueError, match="unknown config keys"):
            providers_mod.validate_config("seccl", {"node_ids": ["0"], "adviser_contact_id": FIRM})

    def test_an_unknown_provider_is_refused(self) -> None:
        with pytest.raises(ValueError, match="unknown provider"):
            providers_mod.validate_config("nope", {})


class TestResolveSourceConfig:
    @pytest.mark.asyncio
    async def test_the_firms_own_row_carries_the_values(self) -> None:
        session = _Session(
            {"tenant_ids": [DIRECTORY], "firm_id": FIRM, "scheme_type": 16},
        )
        found = await providers_mod.resolve_source_config(session, "morgan-lloyd", 7)
        assert found == {"firm_id": FIRM, "scheme_type": 16}

    @pytest.mark.asyncio
    async def test_the_slice_filter_is_never_returned_as_a_vendor_value(self) -> None:
        """THE ONE RULE THE MERGE INTRODUCED. Both facts share a column now, so a caller reading
        "the vendor's values for this firm" must not be handed `tenant_ids` — which is the Azure
        DIRECTORY, the exact GUID the shape check exists to stop being sent as a firm reference.
        The filter is what `_CONFIG_KEYS` calls required; the vendor values are what it calls
        optional, and this reads only the second."""
        session = _Session({"tenant_ids": [DIRECTORY], "firm_id": FIRM})
        found = await providers_mod.resolve_source_config(session, "morgan-lloyd", 7)
        assert found == {"firm_id": FIRM}
        assert "tenant_ids" not in found

    @pytest.mark.asyncio
    async def test_it_reads_one_row_and_not_two(self) -> None:
        """The two-table walk is gone, and a single statement is what says so — the second lookup
        was `platform.providers`, which no longer exists."""
        session = _Session({"tenant_ids": [DIRECTORY], "firm_id": FIRM})
        await providers_mod.resolve_source_config(session, "morgan-lloyd", 7)
        assert len(session.statements) == 1, session.statements
        assert "tenant_platforms" in session.statements[0][0]
        assert session.statements[0][1] == {"t": 7, "p": "morgan-lloyd"}

    @pytest.mark.asyncio
    async def test_several_microsoft_tenants_are_no_longer_undecidable(self) -> None:
        """INVERTED BY 0133, deliberately. This used to raise `AmbiguousSource`: the values lived
        on a second row keyed by ONE external id, so a firm listing two had two candidate sets and
        no basis for choosing, and picking wrong sends one firm's adviser-firm GUID with another
        firm's client — which the vendor accepts as well-formed.

        The question does not arise now. The values are on the firm's own row, so a firm with two
        Microsoft tenants in its slice still has exactly one set of reference values: its own."""
        session = _Session({"tenant_ids": [DIRECTORY, "other-guid"], "firm_id": FIRM})
        assert await providers_mod.resolve_source_config(session, "morgan-lloyd", 7) == {
            "firm_id": FIRM
        }
        assert not hasattr(providers_mod, "AmbiguousSource")

    @pytest.mark.asyncio
    async def test_no_enablement_resolves_to_nothing(self) -> None:
        """Which the caller falls back to env for, rather than refusing."""
        session = _Session(None)
        assert await providers_mod.resolve_source_config(session, "morgan-lloyd", 7) == {}

    @pytest.mark.asyncio
    async def test_an_enablement_with_no_vendor_values_resolves_to_nothing(self) -> None:
        session = _Session({"tenant_ids": [DIRECTORY]})
        assert await providers_mod.resolve_source_config(session, "morgan-lloyd", 7) == {}


class TestDefaultsPreferTheSource:
    def test_the_source_wins_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MORGAN_LLOYD_SCHEME_TYPE", "99")
        out = pa.defaults_for("morgan-lloyd", {"scheme_type": 16})
        assert out["schemeType"] == "16"

    def test_env_fills_what_the_source_does_not_carry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PER FIELD, not per object. A half-filled row must not blank the rest, or configuring
        `firm_id` would silently unset the scheme pairing."""
        monkeypatch.setenv("MORGAN_LLOYD_SIPP_TYPE", "809130016")
        out = pa.defaults_for("morgan-lloyd", {"firm_id": FIRM})
        assert out["cam_financialadviser_Account"] == FIRM
        assert out["sippType"] == "809130016"

    def test_the_firm_id_reaches_all_three_field_names(self) -> None:
        """One value, three names: the vendor calls it `cam_financialadviser_Account`, `accountId`
        and Create Investment's `provider`."""
        out = pa.defaults_for("morgan-lloyd", {"firm_id": FIRM})
        assert out["cam_financialadviser_Account"] == FIRM
        assert out["accountId"] == FIRM
        assert out["provider"] == FIRM

    def test_no_source_and_no_env_offers_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in (
            "SCHEME_TYPE",
            "SIPP_TYPE",
            "ADVISER_FIRM_ID",
            "INVESTMENT_PROVIDER_ID",
            "ADVISER_CONTACT_ID",
        ):
            monkeypatch.delenv(f"MORGAN_LLOYD_{var}", raising=False)
        monkeypatch.setattr(
            "morgan_lloyd.settings.MorganLloydSettings.model_config",
            {"env_prefix": "MORGAN_LLOYD_", "extra": "ignore"},
        )
        assert pa.defaults_for("morgan-lloyd", {}) == {}

    def test_every_source_key_is_one_the_validator_knows(self) -> None:
        """A default mapped to a config key the validator refuses could never be set. The
        allowlist is `_CONFIG_KEYS`' OPTIONAL set since 0133 — the required half is the slice
        filter, which is not a vendor reference value."""
        _, known = providers_mod._CONFIG_KEYS["morgan-lloyd"]
        assert set(pa._MORGAN_LLOYD_SOURCE_KEYS.values()) <= known

    def test_every_prefilled_field_has_a_source_key(self) -> None:
        """Otherwise a field would be settable in env and not on the connection — the split that
        made these values hard to find in the first place."""
        assert set(pa._MORGAN_LLOYD_DEFAULTS) == set(pa._MORGAN_LLOYD_SOURCE_KEYS)


class TestTheAdviserPair:
    """The firm and the adviser CONTACT are required together, and both are entity references.

    Learned from the org in two steps, neither visible in the field table: the firm alone is
    refused with `400 0x80040224 Financial Adviser contact must be populated for the adviser
    firm`, and a bare GUID for the contact is refused with `Invalid Format for
    cam_FinancialAdviserContact`. It only bites once the firm IS configured — which is why
    omitting both worked for months and created every client unattached.
    """

    ADVISER_CONTACT = "4dc533b3-708c-f111-8077-6045bd0e08b2"

    def test_the_pair_resolves_from_one_source_row(self) -> None:
        out = pa.defaults_for(
            "morgan-lloyd", {"firm_id": FIRM, "adviser_contact_id": self.ADVISER_CONTACT}
        )
        assert out["cam_financialadviser_Account"] == FIRM
        assert out["cam_FinancialAdviserContact"] == self.ADVISER_CONTACT

    def test_the_adviser_contact_is_a_known_source_key(self) -> None:
        _, optional = providers_mod._CONFIG_KEYS["morgan-lloyd"]
        assert "adviser_contact_id" in optional

    def test_it_must_be_a_guid(self) -> None:
        with pytest.raises(ValueError, match="adviser_contact_id"):
            providers_mod.validate_config(
                "morgan-lloyd",
                {"tenant_ids": [DIRECTORY], "adviser_contact_id": "Rowan Graphene-Test"},
            )

    def test_it_is_not_the_firm(self) -> None:
        """A person and an account. Sending the firm's GUID as the adviser contact is well-formed
        and wrong — the vendor would file the client under a non-existent adviser."""
        keys = pa._MORGAN_LLOYD_SOURCE_KEYS
        assert keys["cam_FinancialAdviserContact"] == "adviser_contact_id"
        assert keys["cam_financialadviser_Account"] == "firm_id"
