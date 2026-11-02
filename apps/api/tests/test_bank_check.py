"""Sort-code directory check (``app.bank_check``).

The key sits in the upstream URL, so these tests pin that a failure reports a fixed
sentence and never the key, the URL or the account digits. The stored bank name is
``bank``, which is the column portals already uses.
"""

from __future__ import annotations

import logging

import httpx
import pytest
from fastapi import HTTPException

from app import bank_check, ratelimit
from app.config import settings

_KEY = "pc_test_key_secret"
_VALID = {
    "valid": True,
    "stateid": 0,
    "sortcode": "100000",
    "accountnumber": "31510604",
    "directdebits": False,
    "fasterpayments": True,
    "chaps": True,
    "bacs": True,
    "bankbic": "BKENGB33",
    "bankname": "BANK OF ENGLAND",
    "branchname": "HEAD OFFICE",
}


def test_parse_accepts_a_valid_pair() -> None:
    check = bank_check.parse_response(_VALID, sort_code="100000", account_number="31510604")
    assert check is not None
    assert check.valid is True
    assert check.state == "valid"
    assert check.sort_code == "10-00-00"
    assert check.account_number == "31510604"
    assert check.bank_name == "BANK OF ENGLAND"
    assert check.branch_name == "HEAD OFFICE"
    assert check.bank_bic == "BKENGB33"
    assert check.supports.faster_payments is True
    assert check.supports.direct_debits is False
    assert check.message == ""


def test_parse_rejects_an_unknown_sort_code() -> None:
    check = bank_check.parse_response(
        {"valid": False, "stateid": -1}, sort_code="999999", account_number="12345678"
    )
    assert check is not None
    assert check.valid is False
    assert check.state == "sort_code_unknown"
    assert check.message == bank_check.SORT_CODE_UNKNOWN
    assert "999999" not in check.message


def test_parse_rejects_an_account_number() -> None:
    check = bank_check.parse_response(
        {"valid": False, "stateid": -2}, sort_code="100000", account_number="31510605"
    )
    assert check is not None
    assert check.state == "account_invalid"
    assert check.message == bank_check.ACCOUNT_INVALID
    assert check.sort_code == "10-00-00"
    assert check.account_number == "31510605"
    assert check.bank_name == ""
    assert check.supports.bacs is False


def test_parse_pads_a_seven_digit_account_number() -> None:
    check = bank_check.parse_response(
        {"valid": True, "stateid": 0, "sortcode": "100000", "accountnumber": "3151060"},
        sort_code="100000",
        account_number="03151060",
    )
    assert check is not None
    assert check.account_number == "03151060"


def test_parse_ignores_a_non_object() -> None:
    assert (
        bank_check.parse_response(["nope"], sort_code="100000", account_number="31510604") is None
    )


@pytest.mark.asyncio
async def test_unconfigured_does_not_call_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "postcoder_api_key", "")

    def _boom(*_a: object, **_k: object) -> None:
        raise AssertionError("upstream was called")

    monkeypatch.setattr(httpx, "AsyncClient", _boom)
    assert await bank_check.check_uk_account("10-00-00", "31510604") is None


@pytest.mark.asyncio
async def test_timeout_returns_none_and_does_not_log_the_key(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(settings, "postcoder_api_key", _KEY)

    class _Client:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *a: object) -> bool:
            return False

        async def post(self, url: str, **_k: object) -> None:
            raise httpx.ConnectTimeout(f"timed out calling {url}")

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    with caplog.at_level(logging.INFO):
        assert await bank_check.check_uk_account("100000", "31510604") is None
    assert "bank check unavailable" in caplog.text
    assert _KEY not in caplog.text
    assert "31510604" not in caplog.text


@pytest.mark.asyncio
async def test_upstream_posts_the_pair_and_parses_the_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "postcoder_api_key", _KEY)
    seen: dict[str, object] = {}

    class _Resp:
        is_success = True

        def json(self) -> dict[str, object]:
            return _VALID

    class _Client:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *a: object) -> bool:
            return False

        async def post(
            self,
            url: str,
            *,
            params: dict[str, str],
            json: dict[str, str],
            headers: dict[str, str],
        ) -> _Resp:
            seen["url"] = url
            seen["params"] = params
            seen["json"] = json
            seen["headers"] = headers
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    check = await bank_check.check_uk_account("10-00-00", "31510604")
    assert check is not None and check.valid is True
    assert seen["url"] == f"https://ws.postcoder.com/pcw/{_KEY}/bank"
    assert seen["json"] == {"sortcode": "100000", "accountnumber": "31510604"}
    assert seen["headers"] == {"accept": "application/json", "user-agent": "graphene-api"}


@pytest.mark.asyncio
async def test_verified_bank_stores_the_directory_name(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _ok(_sort: str, _number: str, **_extra: object) -> bank_check.BankCheck:
        parsed = bank_check.parse_response(_VALID, sort_code="100000", account_number="31510604")
        assert parsed is not None
        return parsed

    monkeypatch.setattr(bank_check, "check_uk_account", _ok)
    saved = await bank_check.verified_bank(
        {"name": "Jo Cole", "sort_code": "10-00-00", "account_number": "31510604"}
    )
    assert saved["sort_code"] == "10-00-00"
    assert saved["account_number"] == "31510604"
    assert saved["bank"] == "BANK OF ENGLAND"
    assert saved["name"] == "Jo Cole"
    assert "bank_name" not in saved


@pytest.mark.asyncio
async def test_verified_bank_does_not_overwrite_a_named_bank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _ok(_sort: str, _number: str, **_extra: object) -> bank_check.BankCheck:
        parsed = bank_check.parse_response(_VALID, sort_code="100000", account_number="31510604")
        assert parsed is not None
        return parsed

    monkeypatch.setattr(bank_check, "check_uk_account", _ok)
    saved = await bank_check.verified_bank(
        {
            "name": "Jo Cole",
            "bank": "The firm's own label",
            "sort_code": "10-00-00",
            "account_number": "31510604",
        }
    )
    assert saved["bank"] == "The firm's own label"


@pytest.mark.asyncio
async def test_verified_bank_rejects_without_naming_the_digits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _no(_sort: str, _number: str, **_extra: object) -> bank_check.BankCheck:
        parsed = bank_check.parse_response(
            {"valid": False, "stateid": -2}, sort_code="100000", account_number="00000000"
        )
        assert parsed is not None
        return parsed

    monkeypatch.setattr(bank_check, "check_uk_account", _no)
    with pytest.raises(bank_check.BankDetailsRejected, match="isn't valid") as raised:
        await bank_check.verified_bank({"sort_code": "10-00-00", "account_number": "00000000"})
    assert "00000000" not in str(raised.value)


@pytest.mark.asyncio
async def test_verified_bank_leaves_an_unreachable_pair_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _down(_sort: str, _number: str, **_extra: object) -> None:
        return None

    monkeypatch.setattr(bank_check, "check_uk_account", _down)
    original = {"name": "Jo Cole", "sort_code": "10-00-00", "account_number": "31510604"}
    assert await bank_check.verified_bank(original) == original


@pytest.mark.asyncio
async def test_a_metered_user_is_refused_before_the_directory_is_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cap is the one the address lookup already uses. A contact save and a fact-find
    write-back spend it too, and a refusal must not place the paid call."""
    monkeypatch.setattr(settings, "postcoder_api_key", _KEY)
    called = False

    async def _post(*_a: object, **_k: object) -> None:
        nonlocal called
        called = True

    class _Client:
        def __init__(self, *_a: object, **_k: object) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_a: object) -> bool:
            return False

        post = _post

    async def _refuse(*_a: object, **_k: object) -> None:
        raise HTTPException(status_code=429, detail="Too many bank checks. Wait a moment.")

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr(ratelimit, "enforce", _refuse)
    with pytest.raises(HTTPException) as raised:
        await bank_check.check_uk_account("10-00-00", "31510604", user_id="user_metered")
    assert raised.value.status_code == 429
    assert called is False
