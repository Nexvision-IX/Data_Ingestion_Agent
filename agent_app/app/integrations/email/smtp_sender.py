from __future__ import annotations

import smtplib
import uuid
from email.message import EmailMessage
from typing import Any

from app.config import settings


class SMTPSender:
    def send(
        self,
        *,
        recipient: str,
        subject: str,
        body: str,
    ) -> dict[str, Any]:
        if settings.smtp_dry_run or not settings.smtp_host:
            return {
                "status": "DRAFTED",
                "message_id": None,
                "message": (
                    "SMTP dry-run is enabled or SMTP_HOST is empty."
                ),
            }

        if not recipient:
            raise ValueError(
                "Recipient email address is required for SMTP sending"
            )
        if not settings.smtp_from_email:
            raise ValueError("SMTP_FROM_EMAIL is not configured")

        message = EmailMessage()
        message["From"] = settings.smtp_from_email
        message["To"] = recipient
        message["Subject"] = subject
        message_id = f"<{uuid.uuid4()}@ap-agent.local>"
        message["Message-ID"] = message_id
        message.set_content(body)

        smtp_class = (
            smtplib.SMTP_SSL
            if settings.smtp_use_ssl
            else smtplib.SMTP
        )
        with smtp_class(
            settings.smtp_host,
            settings.smtp_port,
            timeout=30,
        ) as client:
            if settings.smtp_use_tls and not settings.smtp_use_ssl:
                client.starttls()
            if settings.smtp_username:
                client.login(
                    settings.smtp_username,
                    settings.smtp_password,
                )
            client.send_message(message)

        return {
            "status": "SENT",
            "message_id": message_id,
            "message": "Email sent successfully.",
        }
