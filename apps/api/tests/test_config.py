"""Tests for app.config.Settings parsing — especially ALLOWED_ORIGINS, which is
set as an env var in deploys. A non-JSON value used to crash the app at startup
(SettingsError from the env source); these pin the tolerant parsing."""

from pathlib import Path

import pytest

from app.config import Settings


def test_allowed_origins_json_array(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "ALLOWED_ORIGINS",
        '["https://client.example.com","https://platform.example.com"]',
    )
    settings = Settings(_env_file=None)
    assert settings.allowed_origins == [
        "https://client.example.com",
        "https://platform.example.com",
    ]


def test_allowed_origins_comma_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    """A plain comma-separated string — the natural thing to type into a deploy
    UI — must parse, not raise."""
    monkeypatch.setenv(
        "ALLOWED_ORIGINS",
        "https://client.example.com, https://platform.example.com",
    )
    settings = Settings(_env_file=None)
    assert settings.allowed_origins == [
        "https://client.example.com",
        "https://platform.example.com",
    ]


def test_allowed_origins_single_bare_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://client.example.com")
    settings = Settings(_env_file=None)
    assert settings.allowed_origins == ["https://client.example.com"]


def test_allowed_origins_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_ORIGINS", "")
    settings = Settings(_env_file=None)
    assert settings.allowed_origins == []


def test_allowed_origins_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    settings = Settings(_env_file=None)
    assert settings.allowed_origins == [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
    ]


def test_workos_reconcile_defaults_on_and_can_be_explicitly_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WORKOS_SYNC_POLL_SECONDS", raising=False)
    assert Settings(_env_file=None).workos_sync_poll_seconds == 60

    monkeypatch.setenv("WORKOS_SYNC_POLL_SECONDS", "0")
    assert Settings(_env_file=None).workos_sync_poll_seconds == 0


def test_compose_runs_workos_reconcile_in_the_worker_not_the_api() -> None:
    compose = (Path(__file__).resolve().parents[3] / "docker-compose.yaml").read_text()
    api_service, worker_and_after = compose.split("  render:", maxsplit=1)
    api_service = api_service.split("  api:", maxsplit=1)[1]

    assert "WORKOS_SYNC_POLL_SECONDS" not in api_service
    assert "WORKOS_SYNC_POLL_SECONDS: ${WORKOS_SYNC_POLL_SECONDS:-60}" in worker_and_after


def test_email_allowlist_accepts_a_comma_separated_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """The non-production send allowlist (app/email/transport.py's stage guard) is typed into a
    deploy UI like
    the origins above, so it takes the same tolerant parsing — full addresses and/or domains."""
    monkeypatch.setenv("EMAIL_ALLOWLIST", "craig@grapheneplatforms.com, @grapheneplatforms.com ")
    settings = Settings(_env_file=None)
    assert settings.email_allowlist == ["craig@grapheneplatforms.com", "@grapheneplatforms.com"]


def test_email_allowlist_accepts_a_json_array(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_ALLOWLIST", '["@grapheneplatforms.com"]')
    settings = Settings(_env_file=None)
    assert settings.email_allowlist == ["@grapheneplatforms.com"]


@pytest.mark.parametrize("value", ["", "   ", " , ,"])
def test_email_allowlist_junk_parses_to_the_fail_safe_empty_list(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """A blank or half-deleted value must land on the EMPTY list, because empty is the branch that
    suppresses every send on dev and staging (tests/test_email_send_guard.py). Parsing junk into a
    one-entry list that matches nothing would work by accident; parsing it into a wildcard would
    mail a firm's clients from staging."""
    monkeypatch.setenv("EMAIL_ALLOWLIST", value)
    assert Settings(_env_file=None).email_allowlist == []


def test_email_allowlist_defaults_to_the_graphene_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unset variable mails ``@grapheneplatforms.com`` on dev and staging — not "mail nobody",
    and not "mail everybody"."""
    monkeypatch.delenv("EMAIL_ALLOWLIST", raising=False)
    assert Settings(_env_file=None).email_allowlist == ["@grapheneplatforms.com"]


def test_email_nonprod_guard_defaults_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EMAIL_NONPROD_GUARD", raising=False)
    assert Settings(_env_file=None).email_nonprod_guard is True


def test_compose_defaults_the_non_production_email_guard() -> None:
    """Both services that send mail. An empty Compose interpolation overrides the Python default, so
    an unset variable in Coolify must still land on the guard and ``@grapheneplatforms.com``."""
    compose = (Path(__file__).resolve().parents[3] / "docker-compose.yaml").read_text()
    api_service, worker_and_after = compose.split("  render:", maxsplit=1)
    api_service = api_service.split("  api:", maxsplit=1)[1]
    guard = "EMAIL_NONPROD_GUARD: ${EMAIL_NONPROD_GUARD:-true}"
    allow = 'EMAIL_ALLOWLIST: "${EMAIL_ALLOWLIST:-@grapheneplatforms.com}"'
    assert guard in api_service
    assert allow in api_service
    assert guard in worker_and_after
    assert allow in worker_and_after


def test_sei_withdrawal_fees_default_to_todays_published_rates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SEI_FASTER_PAYMENT_FEE", raising=False)
    monkeypatch.delenv("SEI_SWIFT_FEE", raising=False)
    settings = Settings(_env_file=None)
    assert settings.sei_faster_payment_fee == 0.50
    assert settings.sei_swift_fee == 15.00


def test_sei_withdrawal_fees_are_configurable_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point — ops can change SEI's published rate with no code deploy."""
    monkeypatch.setenv("SEI_FASTER_PAYMENT_FEE", "0.75")
    monkeypatch.setenv("SEI_SWIFT_FEE", "20")
    settings = Settings(_env_file=None)
    assert settings.sei_faster_payment_fee == 0.75
    assert settings.sei_swift_fee == 20.0


async def test_secrets_not_configured_maps_to_503() -> None:
    """A missing/invalid SECRETS_MASTER_KEY is an instance misconfiguration: endpoints that
    hit crypto fail with an actionable 503, not a bare 500 (app-level exception handler)."""
    from starlette.requests import Request

    from app.crypto import SecretsNotConfigured
    from app.main import secrets_not_configured_handler

    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    response = await secrets_not_configured_handler(
        request, SecretsNotConfigured("SECRETS_MASTER_KEY is not set")
    )
    assert response.status_code == 503
    assert b"SECRETS_MASTER_KEY" in response.body


def test_dev_reload_does_not_wait_forever_for_open_streams() -> None:
    """The platform tab holds GET /notifications/events open. uvicorn's default graceful
    timeout is None, so WatchFiles starts a reload and the worker never exits — `:dev`
    logs "Waiting for connections to close" until CTRL+C."""
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app" / "__main__.py").read_text()
    tree = ast.parse(source)
    runs = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
    ]
    assert runs
    for run in runs:
        kwargs = {kw.arg: kw.value for kw in run.keywords if kw.arg}
        timeout = kwargs["timeout_graceful_shutdown"]
        assert isinstance(timeout, ast.Constant) and timeout.value == 2
    # Watching the package only: a test edit must not restart the dev server.
    reloading = next(
        run
        for run in runs
        if any(
            kw.arg == "reload" and isinstance(kw.value, ast.Constant) and kw.value.value is True
            for kw in run.keywords
        )
    )
    watched = {kw.arg: kw.value for kw in reloading.keywords if kw.arg}
    assert "__file__" in ast.unparse(watched["reload_dirs"])
