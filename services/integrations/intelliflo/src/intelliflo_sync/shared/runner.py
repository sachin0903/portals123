"""The one run loop: which integrations are runnable, and running one's features and recording the outcome.

Both entry points — the scheduler (a job per cron group) and the one-shot runner (`main`) — go through here,
so a change to filtering, recording or notifying lands in both.
"""

import logging
from collections.abc import Iterator
from typing import Any

from intelliflo_sync.shared import config
from intelliflo_sync.shared.base.exceptions import FeatureError
from intelliflo_sync.shared.base.integration import BaseIntegration
from intelliflo_sync.shared.dates import get_data_date
from intelliflo_sync.shared.db import fetch_integration_features, has_successful_run_for_date, record_job_run
from intelliflo_sync.shared.loader import load_integration
from intelliflo_sync.shared.notify import notify_failure, notify_success

logger = logging.getLogger(__name__)


def runnable_integrations(
    integrations: list[dict[str, Any]],
) -> Iterator[tuple[dict[str, Any], type[BaseIntegration], list[str]]]:
    """(row, integration class, features in run order) for each integration that may run, honouring
    RUN_PROVIDER / RUN_FEATURE. An integration that may not is logged and skipped."""
    for row in integrations:
        name = row["name"]
        provider = row["provider"]

        if config.RUN_PROVIDER and provider != config.RUN_PROVIDER:
            logger.info(
                "Skipping '%s': provider '%s' does not match RUN_PROVIDER='%s'", name, provider, config.RUN_PROVIDER
            )
            continue
        if not row["installed"]:
            logger.warning("Skipping '%s': integration is not installed", name)
            continue
        if row["status"] != "approved":
            logger.warning("Skipping '%s': integration is not approved (status='%s')", name, row["status"])
            continue
        if not row["enabled"]:
            logger.warning("Skipping '%s': integration is installed but not enabled", name)
            continue

        features = fetch_integration_features(row["id"])
        if not features:
            logger.warning("Skipping '%s': no features configured in integration_features", name)
            continue

        if config.RUN_FEATURE:
            features = [f for f in features if f == config.RUN_FEATURE]
            if not features:
                logger.warning(
                    "Skipping '%s': feature '%s' not configured for this integration", name, config.RUN_FEATURE
                )
                continue

        try:
            cls = load_integration(provider)
        except ValueError as e:
            logger.error("Skipping '%s': %s", name, e)
            continue

        yield row, cls, cls.order_features(features)  # e.g. IncomeStatements before IncomeStatementItems


def run_features(
    cls: type[BaseIntegration], name: str, integration_id: str, tenant_id: str, features: list[str]
) -> bool:
    """Run the features that have not yet succeeded for today's data date, in ONE call to `run()` so the
    tenant's lake connection is shared between them, then record and notify each feature's own outcome.

    Returns True if the run timed out (the one-shot runner exits non-zero on that; the scheduler carries on).
    """
    data_date = get_data_date()
    pending = [f for f in features if not has_successful_run_for_date(integration_id, f, data_date)]
    if not pending:
        logger.info("All features for '%s' already ran successfully for %s — skipping", name, data_date)
        return False

    def succeeded(feature: str, data: dict | None) -> None:
        record_job_run(integration_id, feature, True, data=data, data_date=data_date)
        notify_success(name, feature, data_date, data or {})

    def failed(feature: str, error: str, data: dict | None = None, notice: str | None = None) -> None:
        record_job_run(integration_id, feature, False, error, data=data, data_date=data_date)
        notify_failure(name, feature, data_date, notice or error)

    logger.info("Running '%s' (tenant: %s) — features: %s", name, tenant_id, pending)
    try:
        results = cls().run(tenant_id=tenant_id, features=pending)
        for feature, data in results.items():
            succeeded(feature, data)
    except FeatureError as e:
        failed_features = e.data.get("failed_features", set())
        feature_data = e.data.get("results", {})
        for feature in pending:
            data = feature_data.get(feature)
            if feature in failed_features:
                logger.error("Feature '%s' failed for %s — %s", feature, name, e, exc_info=True)
                failed(feature, str(e), data)
            else:
                succeeded(feature, data)
    except ValueError as e:
        logger.warning("Skipping %s for '%s' — feature config missing: %s", pending, name, e)
        for feature in pending:
            failed(feature, f"Feature config missing: {e}")
    except TimeoutError as e:
        logger.error("Features %s timed out for %s — %s", pending, name, e)
        for feature in pending:
            failed(feature, str(e), notice=f"Timed out: {e}")
        return True
    except Exception as e:
        logger.error("Features %s failed for %s — %s", pending, name, e, exc_info=True)
        for feature in pending:
            failed(feature, str(e))
    return False
