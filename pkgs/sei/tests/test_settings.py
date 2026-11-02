"""Credential resolution: per-environment first, shared fallback second."""

from __future__ import annotations

import pytest

from sei.settings import ENVIRONMENTS, SeiSettings, cred


def test_per_environment_beats_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEI_APPKEY", "shared")
    monkeypatch.setenv("SEI_I31_APPKEY", "i31-specific")
    assert cred("i31", "appkey") == "i31-specific"
    assert cred("p04", "appkey") == "shared"


def test_shared_fallback_is_used_when_env_specific_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SEI_I31_PFX_PASSWORD", raising=False)
    monkeypatch.setenv("SEI_PFX_PASSWORD", "cert-pass")
    assert cred("i31", "pfx_password") == "cert-pass"


def test_empty_value_falls_through(monkeypatch: pytest.MonkeyPatch) -> None:
    # An env var set to "" is how a .env.example arrives; it must not shadow the fallback.
    monkeypatch.setenv("SEI_I31_APPKEY", "")
    monkeypatch.setenv("SEI_APPKEY", "shared")
    assert cred("i31", "appkey") == "shared"


def test_for_environment_defaults_to_the_swp_host(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENVIRONMENTS:
        monkeypatch.delenv(f"SEI_{key.upper()}_BASE_URL", raising=False)
    monkeypatch.delenv("SEI_BASE_URL", raising=False)
    assert SeiSettings.for_environment("i31").base_url == ENVIRONMENTS["i31"]
    assert SeiSettings.for_environment("p04").base_url == ENVIRONMENTS["p04"]


def test_missing_names_the_env_vars_to_fill_in(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "SEI_BASE_URL",
        "SEI_PFX_BASE64",
        "SEI_PFX_PASSWORD",
        "SEI_OAUTH_USERNAME",
        "SEI_OAUTH_PASSWORD",
        "SEI_APPKEY",
        "SEI_I31_OAUTH_USERNAME",
        "SEI_I31_OAUTH_PASSWORD",
        "SEI_I31_APPKEY",
        "SEI_I31_PFX_BASE64",
        "SEI_I31_PFX_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = SeiSettings.for_environment("i31")
    assert not settings.configured
    # base_url still resolves from the built-in topology, so it is not reported missing.
    assert settings.missing() == [
        "SEI_PFX_BASE64",
        "SEI_PFX_PASSWORD",
        "SEI_I31_OAUTH_USERNAME",
        "SEI_I31_OAUTH_PASSWORD",
        "SEI_I31_APPKEY",
    ]


def test_a_base64_certificate_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """The only certificate shape, and the one this once refused.

    A file-path form existed alongside the base64 until Aug 2026, and `configured` asked for the
    PATH while `ssl_context` and `pfx_material()` preferred the base64 — so a container setting
    only `SEI_PFX_BASE64` (the one form a one-env-list deployment can carry) was refused at
    `BaseClient.__aenter__` with `SEI is not configured: SEI_PFX_PATH unset`, naming the variable
    it could not usefully set. Removing the path form is what makes that unrepeatable: there is
    one value, so there is one check and one name in the error.
    """
    settings = SeiSettings(
        base_url="https://sei.example",
        pfx_base64="AA==",
        pfx_password="pw",
        oauth_username="u",
        oauth_password="p",
        appkey="k",
    )
    assert settings.configured
    assert settings.missing() == []


def test_no_certificate_names_the_one_variable_to_set() -> None:
    settings = SeiSettings(
        base_url="https://sei.example",
        pfx_password="pw",
        oauth_username="u",
        oauth_password="p",
        appkey="k",
    )
    assert not settings.configured
    assert settings.missing() == ["SEI_PFX_BASE64"]


def test_a_file_path_is_no_longer_a_setting() -> None:
    """`extra="ignore"` means a stale `SEI_PFX_PATH` in an environment is silently dropped rather
    than raising — so the guard is that the field is gone, not that it errors."""
    assert "pfx_path" not in SeiSettings.model_fields
