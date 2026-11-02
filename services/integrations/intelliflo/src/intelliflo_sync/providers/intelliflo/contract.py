"""What the Intelliflo integration needs from the database (read-only check).

`CORE` is required for the engine to do anything; each feature also lists what only it needs, so a gap
blocks that feature and nothing else. Both the integrations.* tables and platform.accounts /
platform.links come from apps/api's own migrations, which always run ahead of this service.
"""

import logging

from intelliflo_sync.providers.intelliflo import config
from intelliflo_sync.shared import config as base_config
from intelliflo_sync.shared import contract
from intelliflo_sync.shared import db as shared_db

logger = logging.getLogger(__name__)

S = base_config.SCHEMA_NAME
P = config.PLATFORM_SCHEMA

CORE: contract.Requirements = {
    f"{S}.platforms": [
        "id",
        "tenant_id",
        "platform_name",
        "firm_id",
        "tenant_name",
        "installed",
        "enabled",
        "status",
        "application_data",
        "updated_at",
    ],
    # the SEI firm id and DSR codes of a platform are read from here, by the platform's firm_id (the internal tenant id)
    f"{P}.tenant_platforms": ["tenant_id", "provider", "config"],
    f"{S}.platform_features": ["platform_id", "feature_code"],
    f"{S}.features": ["code"],
    f"{S}.job_runs": ["platform_id", "feature", "status", "error_message", "data", "data_date"],
}

_LINKS: contract.Requirements = {
    f"{P}.links": [
        "tenant_id",
        "entity_type",
        "entity_id",
        "provider",
        "link_type",
        "external_id",
        "data",
    ],
    f"{P}.accounts": ["id", "tenant_id"],
}

FEATURES: dict[str, contract.Requirements] = {}

# What `integration-seed` writes.
SEED: contract.Requirements = contract.merge(
    CORE,
    {f"{S}.platforms": ["provider_id", "provider_name", "app_id", "group_id"]},
    _LINKS,
)


def blocked_features(features: list[str]) -> dict[str, list[str]]:
    """{feature: [missing objects]} for the requested features whose requirements are not met."""
    conn = shared_db._get_connection()
    try:
        with conn.cursor() as cur:
            blocked = {}
            for feature in features:
                gaps = contract.describe(contract.missing_columns(cur, FEATURES.get(feature, {})))
                if gaps:
                    blocked[feature] = gaps
            return blocked
    finally:
        conn.close()


def check_core_or_exit() -> None:
    """Exit with a clear message if the core tables are absent — nothing can run, and migrations can't track."""
    conn = shared_db._get_connection()
    try:
        with conn.cursor() as cur:
            core_gaps = contract.describe(contract.missing_columns(cur, CORE))
    finally:
        conn.close()
    if core_gaps:
        logger.error(
            "Database schema is not ready — missing %s. apps/api's own migrations create these; "
            "check they have run against this database before the engine starts.",
            ", ".join(core_gaps),
        )
        raise SystemExit(1)


def warn_blocked_features() -> None:
    """Log which features cannot run yet because the schema lacks something they need (run after migrations)."""
    for feature, gaps in blocked_features(list(FEATURES)).items():
        logger.warning("Feature %s is blocked until the schema has: %s", feature, ", ".join(gaps))
