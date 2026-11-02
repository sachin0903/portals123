"""Send Slack and/or email notifications for integration job outcomes."""

import logging

from intelliflo_sync.shared import config
from intelliflo_sync.shared.email import send_email
from intelliflo_sync.shared.slack import send_failure_notification, send_success_notification

logger = logging.getLogger(__name__)


def _summary_fields(data: dict) -> dict[str, str]:
    """Pull count dicts out of a feature result, skipping large list fields."""
    fields: dict[str, str] = {}
    for key, val in (data or {}).items():
        if isinstance(val, dict):
            for k, v in val.items():
                fields[f"{key}.{k}"] = str(v)
        elif isinstance(val, (int, float, str, bool)):
            fields[key] = str(val)
    return fields


def _email_recipients() -> list[str] | None:
    raw = config.INTEGRATION_NOTIFY_EMAIL_TO
    if not raw:
        return None
    return [r.strip() for r in raw.split(",") if r.strip()]


def notify_success(name: str, feature: str, data_date: str, data: dict) -> None:
    title = f"{name} — {feature} succeeded"
    message = f"Feature '{feature}' completed successfully for {data_date}."
    fields = _summary_fields(data)

    if config.SLACK_WEBHOOK_URL:
        try:
            send_success_notification(
                config.SLACK_WEBHOOK_URL,
                title=title,
                message=message,
                details=fields,
                footer="Integration Engine",
            )
        except Exception as exc:
            logger.warning("Slack success notification failed: %s", exc)

    recipients = _email_recipients()
    if config.RESEND_EMAIL_API_KEY and recipients:
        detail_lines = "".join(f"<li><b>{k}</b>: {v}</li>" for k, v in fields.items())
        body = f"{message}<ul>{detail_lines}</ul>" if detail_lines else message
        try:
            send_email(
                subject=title,
                body=body,
                to=recipients,
                sender=config.SMTP_SENDER_EMAIL,
                resend_api_key=config.RESEND_EMAIL_API_KEY,
            )
        except Exception as exc:
            logger.warning("Email success notification failed: %s", exc)


def notify_failure(name: str, feature: str, data_date: str, error: str) -> None:
    title = f"{name} — {feature} FAILED"
    message = f"Feature '{feature}' failed for {data_date}."

    if config.SLACK_WEBHOOK_URL:
        try:
            send_failure_notification(
                config.SLACK_WEBHOOK_URL,
                title=title,
                message=message,
                error=error,
                footer="Integration Engine",
            )
        except Exception as exc:
            logger.warning("Slack failure notification failed: %s", exc)

    recipients = _email_recipients()
    if config.RESEND_EMAIL_API_KEY and recipients:
        body = f"{message}<br><br><pre>{error[:1000]}</pre>"
        try:
            send_email(
                subject=title,
                body=body,
                to=recipients,
                sender=config.SMTP_SENDER_EMAIL,
                resend_api_key=config.RESEND_EMAIL_API_KEY,
            )
        except Exception as exc:
            logger.warning("Email failure notification failed: %s", exc)
