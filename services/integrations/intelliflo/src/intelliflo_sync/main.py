"""Run the integration engine once, directly (no scheduler)."""

import logging
import sys

from intelliflo_sync.providers.intelliflo import contract as iflo_contract
from intelliflo_sync.shared import config
from intelliflo_sync.shared.db import fetch_all_integrations
from intelliflo_sync.shared.runner import run_features, runnable_integrations

logger = logging.getLogger(__name__)


def run() -> None:
    logging.basicConfig(
        level=config.LOG_LEVEL,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Nothing to migrate here any more: the `integrations` schema comes from apps/api's own
    # migrations, and the SEI lake (shared/lake.py) is a read-only attach to services/sei's own
    # catalogue — this service never creates objects in either.
    iflo_contract.check_core_or_exit()
    iflo_contract.warn_blocked_features()

    integrations = fetch_all_integrations()
    if not integrations:
        logger.warning("No integrations found in DB.")
        return

    for row, cls, features in runnable_integrations(integrations):
        if run_features(cls, row["name"], row["id"], row["tenant_id"], features):
            logger.error("Exiting after a timeout.")
            sys.exit(1)


if __name__ == "__main__":
    run()
