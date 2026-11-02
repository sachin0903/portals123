"""Email notification helper, vendored from `sei_reports`' `sei_utils.email_sender` (plain-email path only —
`send_email_with_file` was never used by this service)."""

import logging

import resend

logger = logging.getLogger(__name__)


def send_email(subject: str, body: str, to: list[str], sender: str, resend_api_key: str) -> bool:
    """Send a plain HTML email via Resend."""
    try:
        resend.api_key = resend_api_key
        recipients = to if isinstance(to, list) else [to]
        resend.Emails.send(
            {
                "from": sender,
                "to": recipients,
                "subject": subject,
                "html": f"<p>{body}</p>",
            }
        )
        logger.info("Email sent successfully")
        return True
    except Exception as e:
        logger.error("Email sending failed: %s", e)
        return False
