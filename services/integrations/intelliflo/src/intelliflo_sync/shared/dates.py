"""Data date resolution for the integration engine."""

from datetime import datetime, timedelta

from intelliflo_sync.shared import config


def get_data_date() -> str:
    """
    Return the data date string (YYYYMMDD) for today's run.

    - Monday      → last Friday (skip weekend)
    - Tue to Fri  → yesterday
    - Override    → set DATA_DATE env var (useful for manual reruns)
    """
    if config.DATA_DATE:
        return config.DATA_DATE.replace("-", "")

    today = datetime.now()
    delta = timedelta(days=3) if today.weekday() == 0 else timedelta(days=1)
    return (today - delta).strftime("%Y%m%d")
