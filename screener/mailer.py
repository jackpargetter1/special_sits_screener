"""SMTP email sending (works with Gmail app passwords out of the box)."""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from . import config

logger = logging.getLogger(__name__)


class MailerConfigError(RuntimeError):
    pass


def send_email(subject: str, html_body: str, to_addrs: list[str] | None = None) -> None:
    recipients = to_addrs or config.EMAIL_TO
    if not recipients:
        raise MailerConfigError("No recipients configured — set EMAIL_TO (comma-separated).")
    if not config.SMTP_USER or not config.SMTP_PASSWORD:
        raise MailerConfigError("Set SMTP_USER and SMTP_PASSWORD (for Gmail, use an app password).")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config.EMAIL_FROM
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html"))

    logger.info("Sending email to %s via %s:%d", recipients, config.SMTP_HOST, config.SMTP_PORT)
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as server:
        server.starttls()
        server.login(config.SMTP_USER, config.SMTP_PASSWORD)
        server.sendmail(config.EMAIL_FROM, recipients, msg.as_string())
