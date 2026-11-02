"""Integration engine scheduler — loads enabled integrations from DB and runs them on schedule."""

import datetime
import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from intelliflo_sync.providers.intelliflo import contract as iflo_contract
from intelliflo_sync.shared import config
from intelliflo_sync.shared.db import fetch_all_integrations
from intelliflo_sync.shared.runner import run_features, runnable_integrations
from intelliflo_sync.shared.schedule import group_by_cron

logger = logging.getLogger(__name__)


def run():
    logging.basicConfig(
        level=config.LOG_LEVEL,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logging.Formatter.converter = lambda *args: datetime.datetime.now(ZoneInfo("Europe/London")).timetuple()

    # Nothing to migrate here any more: the `integrations` schema comes from apps/api's own
    # migrations, and the SEI lake (shared/lake.py) is a read-only attach to services/sei's own
    # catalogue — this service never creates objects in either.
    iflo_contract.check_core_or_exit()
    iflo_contract.warn_blocked_features()

    integrations = fetch_all_integrations()

    if not integrations:
        logger.warning("No integrations found in DB. Exiting.")
        return

    if config.RUN_PROVIDER:
        logger.info("RUN_PROVIDER filter active: only scheduling provider '%s'", config.RUN_PROVIDER)
    if config.RUN_FEATURE:
        logger.info("RUN_FEATURE filter active: only scheduling feature '%s'", config.RUN_FEATURE)

    scheduler = BlockingScheduler()

    for row, cls, features in runnable_integrations(integrations):
        name = row["name"]
        provider = row["provider"]

        def make_job(cls, name, integration_id, tenant_id, features):
            def job():
                run_features(cls, name, integration_id, tenant_id, features)

            return job

        # One job per distinct schedule: features that share a cron run together in order; a feature with its own
        # cron (INTELLIFLO_<FEATURE>_CRON) is scheduled, and can be run, independently of the others.
        for cron, group in group_by_cron(features, cls.cron_for).items():
            if not cron:
                logger.error(
                    "Skipping '%s' features %s: no cron defined (set %s_CRON)",
                    name,
                    group,
                    provider.upper(),
                )
                continue
            try:
                trigger = CronTrigger.from_crontab(cron, timezone="Europe/London")
            except ValueError as e:
                logger.error("Skipping '%s' features %s: invalid cron %r (%s)", name, group, cron, e)
                continue
            scheduler.add_job(
                make_job(cls, name, row["id"], row["tenant_id"], group),
                trigger,
                id=f"{name} [{cron}]",
                name=f"{name} {group}",
            )
            logger.info(
                "Scheduled: %s (provider: %s, features: %s, cron: %s Europe/London)",
                name,
                provider,
                group,
                cron,
            )

    if not scheduler.get_jobs():
        logger.warning("No active integrations to schedule. Exiting.")
        return

    logger.info("Starting scheduler with %d job(s)", len(scheduler.get_jobs()))
    scheduler.start()
