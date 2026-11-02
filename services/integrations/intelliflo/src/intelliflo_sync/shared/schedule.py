"""Group a tenant's features by the cron schedule each one runs on."""

from collections.abc import Callable


def group_by_cron(features: list[str], cron_for: Callable[[str], str]) -> dict[str, list[str]]:
    """{cron: [features]}, keeping the given order inside each group.

    Features that share a schedule run together in one job, in order (so a feature that needs another
    to run first still gets it); features with their own schedule get a job of their own and run
    independently. `features` should already be in run order (see BaseIntegration.order_features).
    """
    groups: dict[str, list[str]] = {}
    for feature in features:
        groups.setdefault((cron_for(feature) or "").strip(), []).append(feature)
    return groups
