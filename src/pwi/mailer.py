"""Delivery over SMTP.

Not the Gmail MCP: that connector exposes only `create_draft`/`update_draft` and cannot
send, and it is unreachable from headless scheduled compute anyway. See the ADR.

Credentials come from the environment (a GitHub Actions secret in production, `.env`
locally) and are never logged.
"""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465  # implicit TLS


class MailNotConfigured(RuntimeError):
    """Raised at startup rather than at send time, so a misconfigured job fails loudly."""


class Mailer:
    def __init__(self, address: str, app_password: str, recipient: str) -> None:
        self._address = address
        self._password = app_password
        self._recipient = recipient

    @classmethod
    def from_env(cls) -> Mailer:
        address = os.environ.get("GMAIL_ADDRESS", "").strip()
        password = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
        if not address or not password:
            raise MailNotConfigured("GMAIL_ADDRESS and GMAIL_APP_PASSWORD must both be set")
        # Google displays app passwords in groups of four; pasted spaces are a common trip.
        recipient = os.environ.get("NOTIFY_TO", "").strip() or address
        return cls(address, password, recipient)

    def send(self, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self._address
        message["To"] = self._recipient
        message["Subject"] = subject
        message.set_content(body)

        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.login(self._address, self._password)
            smtp.send_message(message)
