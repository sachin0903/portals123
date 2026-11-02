"""Integration engine configuration — loaded from environment variables.

A `pydantic-settings` model is the source of truth (validated types, `.env`/`.env.local` loading,
same pattern as `pkgs/intelliflo/settings.py`), instantiated once below. The module-level
constants (`PG_HOST`, `SCHEMA_NAME`, …) are kept as a compatibility surface: every call site in
this service already does `from intelliflo_sync.shared import config; config.PG_HOST`, and
rewriting all of them to `config.settings.pg_host` is a separate, larger change from "validate the
env vars" — not bundled in here.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class IntegrationsSettings(BaseSettings):
    """Env vars are matched case-insensitively to these field names, with no common prefix —
    this service's vars were never under one (`PG_*`, `S3_*`, `SLACK_*`, `RESEND_*`, …), so field
    names spell out the exact var they read."""

    model_config = SettingsConfigDict(env_file=(".env", ".env.local"), extra="ignore")

    # -- Database (the `portals` OLTP database — integrations.*, platform.accounts/links) --
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_dbname: str = "portals"
    pg_user: str = "migrator"
    pg_password: str = "migrator"
    pg_schema: str = "integrations"
    # Role assumed after connecting (SET ROLE). The integrations tables use FORCE ROW LEVEL
    # SECURITY with no policies, so writes only succeed as a BYPASSRLS role such as service_role.
    # Empty = don't switch.
    pg_set_role: str = "service_role"

    # -- The shared SEI DuckLake (docs/datalake.md) — the whole-firm `sei` core catalogue every
    # Intelliflo feature reads, read-only as `lake_reader`. Same canonical names apps/api and the
    # other provider syncs already use — ONE value per fact, not a per-service copy. --
    # `dbname=datalakes host=... user=lake_reader password=...` — see apps/api/.env.example.
    datalake_catalog_dsn: str = ""
    s3_core_bucket: str = ""
    s3_endpoint_host: str = ""  # host[:port], no scheme
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_use_ssl: bool = False

    # -- Logging --
    log_level: str = "INFO"

    # -- Data date override (for manual reruns) --
    data_date: str | None = None

    # -- Manual run filters --
    run_provider: str | None = None  # e.g. "intelliflo"
    run_feature: str | None = None  # e.g. "Valuations"

    # -- Notifications --
    slack_webhook_url: str | None = None
    resend_email_api_key: str | None = None
    smtp_sender_email: str = "no-reply@platform.grapheneplatforms.com"
    integration_notify_email_to: str | None = None


settings = IntegrationsSettings()

# -- Compatibility surface: every existing call site reads these as module constants. --
PG_HOST = settings.pg_host
PG_PORT = settings.pg_port
PG_DBNAME = settings.pg_dbname
PG_USER = settings.pg_user
PG_PASSWORD = settings.pg_password
SCHEMA_NAME = settings.pg_schema
PG_SET_ROLE = settings.pg_set_role

DATALAKE_CATALOG_DSN = settings.datalake_catalog_dsn
S3_CORE_BUCKET = settings.s3_core_bucket
S3_ENDPOINT_HOST = settings.s3_endpoint_host
S3_ACCESS_KEY_ID = settings.s3_access_key_id
S3_SECRET_ACCESS_KEY = settings.s3_secret_access_key
S3_USE_SSL = settings.s3_use_ssl

LOG_LEVEL = settings.log_level

DATA_DATE = settings.data_date

RUN_PROVIDER = settings.run_provider
RUN_FEATURE = settings.run_feature

SLACK_WEBHOOK_URL = settings.slack_webhook_url
RESEND_EMAIL_API_KEY = settings.resend_email_api_key
SMTP_SENDER_EMAIL = settings.smtp_sender_email
INTEGRATION_NOTIFY_EMAIL_TO = settings.integration_notify_email_to
