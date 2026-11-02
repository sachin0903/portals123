"""Base class for all integrations."""

import logging
from abc import ABC, abstractmethod

from intelliflo_sync.shared.dates import get_data_date


class BaseIntegration(ABC):
    name: str = "base"
    cron: str = ""
    max_workers: int = 1  # number of firms to process in parallel

    def __init__(self):
        self.logger = logging.getLogger(f"integration.{self.name}")
        self.data_date = get_data_date()

    @classmethod
    def cron_for(cls, feature: str) -> str:
        """The cron schedule `feature` runs on. Providers override this to give features their own."""
        return cls.cron

    @classmethod
    def order_features(cls, features: list[str]) -> list[str]:
        """The order to run `features` in. Providers override this when one feature needs another to have run."""
        return list(features)

    @abstractmethod
    def run(self, tenant_id: str, features: list[str] | None = None) -> dict:
        """
        Run the requested features for the firm identified by tenant_id.

        Loads the platform config for tenant_id from the DB, runs the features sequentially in the order
        given, and returns {feature: summary_counts} stored in job_runs.data. With features=None it runs the
        features linked to the tenant in platform_features (the single source of truth); there is no default list.
        """
        raise NotImplementedError
