"""Slack notification helper, vendored from `sei_reports`' `sei_utils.slack`."""

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)


def send_slack_notification(
    webhook_url: str,
    message: str,
    title: str | None = None,
    color: str = "good",
    fields: list[dict[str, Any]] | None = None,
    footer: str = "Intelliflo Sync",
) -> bool:
    """Send a notification to Slack using a webhook URL."""
    if not webhook_url:
        logger.warning("Slack webhook URL not configured, skipping notification")
        return False

    try:
        payload: dict[str, Any] = {
            "attachments": [
                {
                    "color": color,
                    "title": title if title else "Notification",
                    "text": message,
                    "fields": fields if fields else [],
                    "footer": footer,
                    "ts": int(time.time()),
                }
            ]
        }

        response = requests.post(webhook_url, json=payload, timeout=10)

        if response.status_code == 200:
            logger.info("Slack notification sent successfully")
            return True
        logger.error("Failed to send Slack notification: %s - %s", response.status_code, response.text)
        return False

    except Exception as e:
        logger.error("Error sending Slack notification: %s", e)
        return False


def send_success_notification(
    webhook_url: str,
    title: str,
    message: str,
    details: dict[str, str] | None = None,
    footer: str = "Intelliflo Sync",
) -> bool:
    fields = [{"title": k, "value": v, "short": True} for k, v in (details or {}).items()]
    return send_slack_notification(
        webhook_url=webhook_url,
        message=message,
        title=title,
        color="good",
        fields=fields,
        footer=footer,
    )


def send_failure_notification(
    webhook_url: str,
    title: str,
    message: str,
    error: str | None = None,
    details: dict[str, str] | None = None,
    footer: str = "Intelliflo Sync",
) -> bool:
    fields = [{"title": k, "value": v, "short": True} for k, v in (details or {}).items()]
    if error:
        fields.append({"title": "Error", "value": f"```{error[:500]}```", "short": False})
    return send_slack_notification(
        webhook_url=webhook_url,
        message=message,
        title=title,
        color="danger",
        fields=fields,
        footer=footer,
    )
