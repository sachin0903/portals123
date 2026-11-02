"""Which env files the connection settings read, and in which order.

This is a one-line `model_config` and it was wrong for months in a way nothing could catch by
reading code. `env_file=".env"` is right for `services/morgan-lloyd`, which has no `.env.local`,
and wrong for `apps/api`, whose whole convention is that local secrets live in `.env.local` --
which is also what `apps/api/.env.example` tells you to do.

The result: `MORGAN_LLOYD_CLIENT_ID` / `_SECRET` / `_TENANT_ID`, placed exactly where the example
says, were read by NOTHING in the API. One call site (`integrations/morgan_lloyd/benefits.py`)
had noticed and passed `_env_file=(".env", ".env.local")` itself, so drawdown worked; `sync.py`,
`platform_actions` and the client's own default constructor kept building an unconfigured object,
and the failure surfaces only as a 401 from a live vendor.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from morgan_lloyd.settings import MorganLloydSettings


@pytest.fixture(autouse=True)
def _no_ambient_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real `MORGAN_LLOYD_*` vars in the developer's shell would mask what the files say."""
    for name in (
        "CLIENT_ID",
        "CLIENT_SECRET",
        "TENANT_ID",
        "SCHEME_TYPE",
        "SIPP_TYPE",
        "ADVISER_FIRM_ID",
        "INVESTMENT_PROVIDER_ID",
    ):
        monkeypatch.delenv(f"MORGAN_LLOYD_{name}", raising=False)


def test_both_files_are_declared() -> None:
    """Pinned as a pair rather than by behaviour alone, because dropping one is a one-character
    edit whose consequence is silent."""
    assert MorganLloydSettings.model_config["env_file"] == (".env", ".env.local")


def test_a_value_in_env_local_alone_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE BUG. `.env.local` is where `apps/api/.env.example` says to put the credentials."""
    (tmp_path / ".env").write_text("MORGAN_LLOYD_TENANT_ID=from-env\n")
    (tmp_path / ".env.local").write_text("MORGAN_LLOYD_CLIENT_SECRET=from-env-local\n")
    monkeypatch.chdir(tmp_path)
    assert MorganLloydSettings().client_secret == "from-env-local"


def test_env_local_wins_over_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Later file wins — the override direction the repo's convention assumes."""
    (tmp_path / ".env").write_text("MORGAN_LLOYD_CLIENT_ID=shared\n")
    (tmp_path / ".env.local").write_text("MORGAN_LLOYD_CLIENT_ID=local-override\n")
    monkeypatch.chdir(tmp_path)
    assert MorganLloydSettings().client_id == "local-override"


def test_env_alone_still_works(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`services/morgan-lloyd` has no `.env.local`, and a missing file must not be an error."""
    (tmp_path / ".env").write_text(
        "MORGAN_LLOYD_CLIENT_ID=service-id\nMORGAN_LLOYD_SCHEME_TYPE=16\n"
    )
    monkeypatch.chdir(tmp_path)
    settings = MorganLloydSettings()
    assert settings.client_id == "service-id"
    assert settings.scheme_type == 16


def test_the_fixed_reference_values_are_unset_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No file, no values. These are FIRM-specific -- the Create Scheme pairing is validated as a
    pair and both "Firm ID we provided you" GUIDs are minted per integrating firm -- so baking
    the Clifton dev org's in as defaults would have another deployment quietly sending them."""
    monkeypatch.chdir(tmp_path)
    settings = MorganLloydSettings()
    assert settings.scheme_type is None
    assert settings.sipp_type is None
    assert settings.investment_provider_id == ""
    assert settings.adviser_firm_id == ""


class TestTheFirmIdIsOneFact:
    """ "The Firm ID we provided you" names the SAME GUID in two places, and it is not the
    Microsoft tenant id.

    Three identifiers are easy to confuse and were:

      * the Azure **tenant id** (`02492031-…`) — the directory, used for the OAuth token URL and
        stamped on every synced record as `ml_tenant_id`;
      * the **Firm ID** (`6ac39921-…`) — a Dynamics `accounts` row, which the vendor asks for as
        `cam_financialadviser_Account` on Create Contact, `accountId` on Create Adviser AND
        `provider` on Create Investment / Create Valuation;
      * the adviser **contact** (`4dc533b3-…`) — a person.

    Verified against the live org, not inferred from the wording: every synced contact carries
    `_cam_financialadviser_value = 6ac39921-…`, which is exactly the `provider` GUID the API
    Access Document lists. Which means the firm id was never unknown — it sat in that document
    under the other field name while `MORGAN_LLOYD_ADVISER_FIRM_ID` stayed blank, leaving every
    contact we created unattached (so `cam_createscheme` 403s) and `raw.adviser_contacts` empty.
    """

    def test_the_provider_falls_back_to_the_adviser_firm(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One env var configures both, so they cannot drift apart by half-configuration."""
        (tmp_path / ".env").write_text("MORGAN_LLOYD_ADVISER_FIRM_ID=firm-guid\n")
        monkeypatch.chdir(tmp_path)
        settings = MorganLloydSettings()
        assert settings.investment_provider_id == "", "nothing explicit was set"
        assert settings.investment_provider == "firm-guid"

    def test_an_explicit_provider_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Kept separable: the vendor could issue a firm a different `provider` account, and a
        deployment where they differ has to be able to say so."""
        (tmp_path / ".env").write_text(
            "MORGAN_LLOYD_ADVISER_FIRM_ID=firm-guid\n"
            "MORGAN_LLOYD_INVESTMENT_PROVIDER_ID=other-guid\n"
        )
        monkeypatch.chdir(tmp_path)
        assert MorganLloydSettings().investment_provider == "other-guid"

    def test_neither_set_stays_blank(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Blank means "refuse and name it", never "guess"."""
        monkeypatch.chdir(tmp_path)
        assert MorganLloydSettings().investment_provider == ""

    def test_the_firm_id_is_not_the_tenant_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The distinction this class exists for. Falling back to `tenant_id` would send the
        Azure directory GUID as a Dynamics `accounts` reference — accepted as well-formed and
        wrong, in a system with no delete."""
        (tmp_path / ".env").write_text(
            "MORGAN_LLOYD_TENANT_ID=02492031-c828-4a6e-9e27-e07493fb397e\n"
        )
        monkeypatch.chdir(tmp_path)
        settings = MorganLloydSettings()
        assert settings.tenant_id
        assert settings.adviser_firm_id == ""
        assert settings.investment_provider == ""
