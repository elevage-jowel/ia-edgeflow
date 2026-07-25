"""Proactive alerting for events that need a human's attention.

Two optional channels, configured under `notifications:` in config.yaml --
leave a channel's keys blank (or the whole section out) to disable it.
Both use only the standard library (urllib for Telegram's HTTP API,
smtplib for email) so no extra dependency is needed just for this.

Failures to send are logged, never raised -- a broken notification
channel must not be able to crash the engine.
"""
from __future__ import annotations

import logging
import smtplib
import time
from dataclasses import dataclass
from email.mime.text import MIMEText
from urllib import parse as urllib_parse
from urllib import request as urllib_request

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NotificationConfig:
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    email_from: str | None = None
    email_to: str | None = None

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host and self.email_to)


class Notifier:
    def __init__(self, cfg: NotificationConfig):
        self.cfg = cfg
        self._last_sent: dict[str, float] = {}

    def notify(self, subject: str, message: str, *, kind: str = "generic",
               min_interval_seconds: float = 0) -> None:
        """Send `subject`/`message` on every enabled channel.

        `kind` + `min_interval_seconds` throttle repeats of the same kind
        of event (e.g. a bug that fires every poll cycle) so one broken
        signal can't turn into hundreds of messages.
        """
        if min_interval_seconds > 0:
            now = time.time()
            last = self._last_sent.get(kind, 0.0)
            if now - last < min_interval_seconds:
                return
            self._last_sent[kind] = now

        if self.cfg.telegram_enabled:
            self._send_telegram(subject, message)
        if self.cfg.email_enabled:
            self._send_email(subject, message)

    def _send_telegram(self, subject: str, message: str) -> None:
        try:
            text = f"{subject}\n\n{message}"
            url = f"https://api.telegram.org/bot{self.cfg.telegram_bot_token}/sendMessage"
            data = urllib_parse.urlencode({
                "chat_id": self.cfg.telegram_chat_id,
                "text": text,
            }).encode()
            with urllib_request.urlopen(url, data=data, timeout=10):
                pass
        except Exception:
            logger.exception("failed to send Telegram notification")

    def _send_email(self, subject: str, message: str) -> None:
        try:
            msg = MIMEText(message)
            msg["Subject"] = subject
            msg["From"] = self.cfg.email_from or (self.cfg.smtp_username or "sentinel@localhost")
            msg["To"] = self.cfg.email_to
            with smtplib.SMTP(self.cfg.smtp_host, self.cfg.smtp_port, timeout=10) as smtp:
                smtp.starttls()
                if self.cfg.smtp_username:
                    smtp.login(self.cfg.smtp_username, self.cfg.smtp_password or "")
                smtp.send_message(msg)
        except Exception:
            logger.exception("failed to send email notification")
