"""The object store is ONE store, addressed by canonical names.

Every provider service writes parquet into the same core bucket, through the same endpoint, with
the same credential — so each of those facts is set once under an `S3_*` name and the per-provider
`SECCL_S3_*` form exists only as an override. Before that, the endpoint was a separate value per
provider, so one value lived in three files. That matters because of how the mistake fails: a wrong
endpoint does not raise — the parquet lands where the reader never looks, and it presents as "the
bucket is empty" rather than as a configuration error.

`interlace.yaml` interpolates the canonical names directly; these pin the RUNTIME settings (the
gateway's document writes), which cannot read that file.
"""

from __future__ import annotations

import pathlib

import pytest

from seccl_sync.settings import ObjectStoreSettings

CANONICAL = {
    "S3_ENDPOINT_HOST": "rustfs:9000",
    "S3_ACCESS_KEY_ID": "shared-key",
    "S3_SECRET_ACCESS_KEY": "shared-secret",
    "S3_CORE_BUCKET": "platforms",
    "S3_REGION": "eu-west-2",
}

#: Every `SECCL_S3_*` name the settings understand, so a test cannot leak a developer's real one in.
PREFIXED = (
    "SECCL_S3_ENDPOINT",
    "SECCL_S3_ACCESS_KEY_ID",
    "SECCL_S3_SECRET_ACCESS_KEY",
    "SECCL_S3_BUCKET",
    "SECCL_S3_REGION",
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """A bare environment, and no `.env` underfoot: `SettingsConfigDict(env_file=".env")` reads
    the file relative to the CWD, so a developer's own service `.env` would otherwise decide the
    result of every assertion here."""
    for name in (*CANONICAL, *PREFIXED):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(pathlib.Path(__file__).parent)
    return monkeypatch


def test_reads_the_canonical_names(clean_env: pytest.MonkeyPatch) -> None:
    for key, value in CANONICAL.items():
        clean_env.setenv(key, value)
    s = ObjectStoreSettings()
    assert s.endpoint == "rustfs:9000"
    assert s.access_key_id == "shared-key"
    assert s.secret_access_key == "shared-secret"
    assert s.bucket == "platforms"
    assert s.region == "eu-west-2"


def test_the_prefixed_name_still_overrides(clean_env: pytest.MonkeyPatch) -> None:
    # The escape hatch: one provider genuinely pointed at a different store.
    for key, value in CANONICAL.items():
        clean_env.setenv(key, value)
    clean_env.setenv("SECCL_S3_ENDPOINT", "seccl-only:9000")
    clean_env.setenv("SECCL_S3_BUCKET", "seccl-only")
    s = ObjectStoreSettings()
    assert s.endpoint == "seccl-only:9000"
    assert s.bucket == "seccl-only"
    # …and the credential it did NOT override still comes from the shared one.
    assert s.access_key_id == "shared-key"


def test_endpoint_url_adds_the_scheme_the_canonical_name_drops(
    clean_env: pytest.MonkeyPatch,
) -> None:
    # `S3_ENDPOINT_HOST` is host[:port] because the DuckDB secret form demands it; boto3 needs a
    # URL, so the settings put the scheme back rather than asking for the endpoint twice.
    clean_env.setenv("S3_ENDPOINT_HOST", "rustfs:9000")
    assert ObjectStoreSettings().endpoint_url == "http://rustfs:9000"
    assert ObjectStoreSettings(use_ssl=True).endpoint_url == "https://rustfs:9000"


def test_defaults_to_the_core_bucket_with_nothing_set(clean_env: pytest.MonkeyPatch) -> None:
    # Provider-raw documents belong in the core bucket (docs/storage.md), so the default is the
    # bucket name the rest of the stack defaults to — not an empty string that fails at first write.
    assert ObjectStoreSettings().bucket == "platforms"
