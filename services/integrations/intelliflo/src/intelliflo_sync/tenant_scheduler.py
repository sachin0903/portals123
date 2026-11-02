"""Tenant sync — runs on its own schedule, separate from the integration engine's feature jobs.

Does not run migrations: the integrations schema (including platforms.app_id / group_id) must already exist, created
by the engine or the webhook at their startup.
"""

import datetime
import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from intelliflo_sync.providers.intelliflo import config as iflo_config
from intelliflo_sync.providers.intelliflo.tenant_sync import sync_tenants
from intelliflo_sync.shared import config

logger = logging.getLogger(__name__)


def _setup() -> None:
    logging.basicConfig(
        level=config.LOG_LEVEL,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logging.Formatter.converter = lambda *args: datetime.datetime.now(ZoneInfo("Europe/London")).timetuple()


def run_once() -> None:
    """Sync the tenants once and exit."""
    _setup()
    sync_tenants()


def run() -> None:
    """Sync the tenants on INTELLIFLO_TENANT_SYNC_CRON."""
    _setup()

    def job():
        try:
            sync_tenants()
        except Exception as e:
            logger.error("Tenant sync failed: %s", e, exc_info=True)

    scheduler = BlockingScheduler()
    scheduler.add_job(
        job,
        CronTrigger.from_crontab(iflo_config.TENANT_SYNC_CRON, timezone="Europe/London"),
        id="tenant_sync",
        name="tenant_sync",
    )
    logger.info("Scheduled: tenant_sync (cron: %s Europe/London)", iflo_config.TENANT_SYNC_CRON)
    scheduler.start()
