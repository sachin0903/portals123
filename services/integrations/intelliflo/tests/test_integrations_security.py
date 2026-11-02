"""The integrations never log credentials, tokens or the lake catalog password."""

import base64
import logging
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

from intelliflo_sync.shared import config, lake
from intelliflo_sync.shared.base.auth import OAuth2Auth

SECRET = "S3cret$Value-xyz"
TOKEN = "tok-abc.def.ghi"
ROOT = Path(__file__).resolve().parents[1]


def _auth():
    return OAuth2Auth("https://idp.example/token", "client-1", SECRET, scope="a b", extra_data={"tenant_id": "1"})


def _response(status, body):
    resp = MagicMock()
    resp.ok, resp.status_code = status < 400, status
    resp.json.return_value = body
    resp.raise_for_status.side_effect = None if status < 400 else requests.HTTPError(f"{status}")
    return resp


def test_token_fetch_logs_no_secret_credentials_or_token(monkeypatch, caplog):
    monkeypatch.setattr(requests, "post", lambda *a, **kw: _response(200, {"access_token": TOKEN, "expires_in": 3600}))
    with caplog.at_level(logging.DEBUG):
        headers = _auth().get_headers()
    assert headers == {"Authorization": f"Bearer {TOKEN}"}
    basic = base64.b64encode(f"client-1:{SECRET}".encode()).decode()
    for sensitive in (SECRET, TOKEN, basic, "client-1"):
        assert sensitive not in caplog.text
    assert "Fetching OAuth2 token" in caplog.text


def test_failed_token_request_logs_only_the_error_code(monkeypatch, caplog):
    monkeypatch.setattr(requests, "post", lambda *a, **kw: _response(400, {"error": "invalid_scope", "detail": SECRET}))
    with caplog.at_level(logging.DEBUG), pytest.raises(requests.HTTPError):
        _auth().get_headers()
    assert "invalid_scope" in caplog.text and "400" in caplog.text
    assert SECRET not in caplog.text


def test_redact_hides_the_catalog_password():
    text = 'Failed to attach "postgres:dbname=postgres host=h port=5432 user=u password=hunter2xyz" more'
    assert "hunter2xyz" not in lake._redact(text) and "password=***" in lake._redact(text)


class _FakeDuckDBCon:
    """Records every `execute()` call; raises on ATTACH when `fail` is set, with the password
    embedded in the message the way DuckLake's own attach errors do."""

    def __init__(self, fail: bool = False):
        self.statements: list[str] = []
        self._fail = fail

    def execute(self, sql, *a, **kw):
        self.statements.append(sql)
        if self._fail and sql.strip().startswith("ATTACH"):
            raise OSError(f"attach failed: {sql}")
        return self


def test_open_lake_refuses_clearly_when_the_dsn_is_not_configured(monkeypatch):
    monkeypatch.setattr(config, "DATALAKE_CATALOG_DSN", "")
    with pytest.raises(RuntimeError, match="DATALAKE_CATALOG_DSN is not configured"):
        lake.open_lake()


def test_open_lake_attaches_read_only_to_the_sei_schema(monkeypatch):
    con = _FakeDuckDBCon()
    monkeypatch.setattr(config, "DATALAKE_CATALOG_DSN", "dbname=datalakes host=h user=lake_reader password=hunter2xyz")
    monkeypatch.setattr(config, "S3_CORE_BUCKET", "platforms")
    monkeypatch.setattr(config, "S3_ENDPOINT_HOST", "localhost:9000")
    monkeypatch.setattr(config, "S3_ACCESS_KEY_ID", "key")
    monkeypatch.setattr(config, "S3_SECRET_ACCESS_KEY", "secret")
    import duckdb

    monkeypatch.setattr(duckdb, "connect", lambda *a, **kw: con)
    assert lake.open_lake() is con
    attach = next(s for s in con.statements if s.strip().startswith("ATTACH"))
    assert "METADATA_SCHEMA 'sei'" in attach and "READ_ONLY" in attach
    assert "hunter2xyz" in attach  # the real DSN is what gets attached...
    assert any("USE core" in s for s in con.statements)
    secret = next(s for s in con.statements if s.strip().startswith("CREATE SECRET"))
    assert "SCOPE 's3://platforms'" in secret and "ENDPOINT 'localhost:9000'" in secret


def test_open_lake_error_redacts_the_password_and_does_not_chain(monkeypatch):
    con = _FakeDuckDBCon(fail=True)
    monkeypatch.setattr(config, "DATALAKE_CATALOG_DSN", "dbname=datalakes host=h user=lake_reader password=hunter2xyz")
    import duckdb

    monkeypatch.setattr(duckdb, "connect", lambda *a, **kw: con)
    with pytest.raises(RuntimeError) as err:
        lake.open_lake()
    assert "hunter2xyz" not in str(err.value) and "password=***" in str(err.value)
    assert err.value.__suppress_context__  # the traceback does not print the original, unredacted error


def test_no_log_call_in_the_integrations_mentions_secrets():
    pattern = re.compile(
        r"(?:logger|logging|self\.logger)\.\w+\(.*(?:secret|password|CREDS|credentials|\bheaders\b|\btoken_data\b(?!\.get\(.expires_in.\)))",
        re.IGNORECASE,
    )
    offenders = []
    for path in (ROOT / "src/intelliflo_sync").rglob("*.py"):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            # an environment variable's NAME in a message ("INTELLIFLO_WEBHOOK_SECRET is not set") is not its value
            scrubbed = re.sub(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_(?:SECRET|PASSWORD|KEY)\b", "", line)
            if pattern.search(scrubbed):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert offenders == []


# No "same server" guess-and-warn tests here any more: that heuristic existed because the old
# mechanism defaulted the lake's Postgres to PG_HOST when LAKE_PG_HOST was unset, which was unsafe
# to guess on a shared server. The new one has no default to guess — DATALAKE_CATALOG_DSN is the
# one explicit, shared value every reader (this service and apps/api) is configured with, or
# `open_lake()` refuses outright (see test_open_lake_refuses_clearly_when_the_dsn_is_not_configured
# above).
