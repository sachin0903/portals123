"""Intelliflo provider configuration."""

import os
import re

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class IntellifloSettings(BaseSettings):
    """Almost everything here is `INTELLIFLO_*` (`env_prefix` below); the two that aren't
    (`data_date`, `platform_schema`) are shared with other parts of this service and name their
    own env var explicitly via `validation_alias`.
    """

    model_config = SettingsConfigDict(env_prefix="INTELLIFLO_", env_file=(".env", ".env.local"), extra="ignore")

    token_url: str = "https://identity.gb.intelliflo.net/core/connect/token"
    # Credentials have no defaults: a missing one is reported by missing_credentials(), never sent
    # to Intelliflo as a placeholder.
    client_id: str = ""
    client_secret: str = ""
    tenant_id: str = "12551"
    grant_type: str = "tenant_client_credentials"
    scope: str = "openid hub client_data client_financial_data firm_data fund_data apps app_data"
    valuation_batch_scope: str = "valuation_batch.app"
    # The valuation client defaults to the general one (the same Intelliflo app); set
    # INTELLIFLO_VALUATION_CLIENT_ID / _SECRET only if it differs — filled in in `_load` below.
    valuation_client_id: str | None = None
    valuation_client_secret: str | None = None
    api_base_url: str = "https://api.gb.intelliflo.net"
    api_key: str = ""
    cron: str = "30 9 * * 1-5"  # default schedule for every feature (Europe/London)

    max_workers: int = 1

    # Tenant sync: the installed app whose group settings list every tenant, and its own schedule
    app_id: str = ""
    tenant_sync_cron: str = "0 6 * * *"

    # Webhook registration
    webhook_callback_url: str = "https://dev-api.grapheneplatforms.com/intelliflo/webhook"
    webhook_secret: str = ""
    webhook_topics: str = "appinstalled,appuninstalled"  # comma-separated; split in `_load`

    # The product provider a NEW tenant row is created under (integrations.platforms.provider_id/_name).
    # Used by the tenant sync at INSERT only, never at run time and never on an existing row: the row is
    # the source of truth and an admin can change it per tenant. Defaults are Graphene's own provider.
    default_provider_id: str = "5261"
    default_provider_name: str = "Graphene Platforms Ltd"

    # Not INTELLIFLO_-prefixed: shared with the rest of this service (DATA_DATE) or with
    # apps/api's own naming (PG_PLATFORM_SCHEMA).
    data_date: str | None = Field(default=None, validation_alias="DATA_DATE")
    platform_schema: str = Field(default="platform", validation_alias="PG_PLATFORM_SCHEMA")


def _load() -> IntellifloSettings:
    settings = IntellifloSettings()
    settings.client_id = settings.client_id.strip()
    settings.client_secret = settings.client_secret.strip()
    settings.api_key = settings.api_key.strip()
    settings.app_id = settings.app_id.strip()
    settings.webhook_secret = settings.webhook_secret.strip()
    settings.default_provider_id = settings.default_provider_id.strip()
    settings.default_provider_name = settings.default_provider_name.strip()
    if settings.valuation_client_id is None:
        settings.valuation_client_id = settings.client_id
    else:
        settings.valuation_client_id = settings.valuation_client_id.strip()
    if settings.valuation_client_secret is None:
        settings.valuation_client_secret = settings.client_secret
    else:
        settings.valuation_client_secret = settings.valuation_client_secret.strip()
    return settings


settings = _load()

# -- Compatibility surface: every existing call site reads these as module constants. --
TOKEN_URL = settings.token_url
CLIENT_ID = settings.client_id
CLIENT_SECRET = settings.client_secret
TENANT_ID = settings.tenant_id
GRANT_TYPE = settings.grant_type
SCOPE = settings.scope
VALUATION_BATCH_SCOPE = settings.valuation_batch_scope
# _load() always resolves these to a string (falling back to CLIENT_ID/CLIENT_SECRET when unset) —
# the field itself stays `str | None` since that's the configurable shape, pre-fallback.
assert settings.valuation_client_id is not None
assert settings.valuation_client_secret is not None
VALUATION_CLIENT_ID: str = settings.valuation_client_id
VALUATION_CLIENT_SECRET: str = settings.valuation_client_secret
API_BASE_URL = settings.api_base_url
API_KEY = settings.api_key
CRON = settings.cron

MAX_WORKERS = settings.max_workers

APP_ID = settings.app_id
TENANT_SYNC_CRON = settings.tenant_sync_cron

DEFAULT_PROVIDER_ID = settings.default_provider_id
DEFAULT_PROVIDER_NAME = settings.default_provider_name

WEBHOOK_CALLBACK_URL = settings.webhook_callback_url
WEBHOOK_SECRET = settings.webhook_secret
WEBHOOK_TOPICS = [t.strip() for t in settings.webhook_topics.split(",")]

DATA_DATE = settings.data_date

PLATFORM_SCHEMA = settings.platform_schema


def feature_cron(feature: str) -> str | None:
    """A feature's own schedule, from INTELLIFLO_<FEATURE>_CRON, or None to use CRON.

    Valuations → INTELLIFLO_VALUATIONS_CRON, IncomeStatements → INTELLIFLO_INCOME_STATEMENTS_CRON,
    IncomeStatementItems → INTELLIFLO_INCOME_STATEMENT_ITEMS_CRON,
    Transactions → INTELLIFLO_TRANSACTIONS_CRON.
    Features that end up on the same schedule run together, in order; the others run independently.

    A plain `os.getenv`, not a settings field: the var name is built from `feature` at call time,
    which a static pydantic field can't express.
    """
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", feature).upper()
    return (os.getenv(f"INTELLIFLO_{snake}_CRON") or "").strip() or None


def missing_credentials() -> list[str]:
    """Names of the required Intelliflo settings that are not set."""
    required = {
        "INTELLIFLO_CLIENT_ID": CLIENT_ID,
        "INTELLIFLO_CLIENT_SECRET": CLIENT_SECRET,
        "INTELLIFLO_API_KEY": API_KEY,
    }
    return [name for name, value in required.items() if not value]
