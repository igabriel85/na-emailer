from __future__ import annotations
from typing import Any
import yagmail
from ..config import Settings
from ..models import EmailMessage
from .base import EmailClient
import smtplib
from email import policy
from email.parser import BytesParser, Parser


class YagmailClient(EmailClient):
    def __init__(self, settings: Settings):
        if not settings.yagmail_user or not settings.yagmail_password:
            raise ValueError("NA_YAGMAIL_USER and NA_YAGMAIL_PASSWORD are required for yagmail client")
        self._settings = settings
        kwargs: dict[str, Any] = {"user": settings.yagmail_user, "password": settings.yagmail_password}
        if settings.yagmail_host:
            kwargs["host"] = settings.yagmail_host
        if settings.yagmail_port is not None:
            kwargs["port"] = settings.yagmail_port
        if settings.yagmail_smtp_starttls is not None:
            kwargs["smtp_starttls"] = settings.yagmail_smtp_starttls
        if settings.yagmail_smtp_ssl is not None:
            kwargs["smtp_ssl"] = settings.yagmail_smtp_ssl

        self._smtp = yagmail.SMTP(**kwargs)

    def _normalize_raw_mime(self, raw_mime: str) -> bytes:
        raw = raw_mime.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8", errors="replace")

        try:
            msg = BytesParser(policy=policy.SMTP).parsebytes(raw)
        except Exception:
            msg = Parser(policy=policy.SMTP).parsestr(raw_mime)

        if not msg.get("From"):
            msg["From"] = self._settings.yagmail_user
        if not msg.get("MIME-Version"):
            msg["MIME-Version"] = "1.0"

        return msg.as_bytes(policy=policy.SMTP)

    def _send_raw_via_smtplib(self, raw_mime: str, recipients: list[str]) -> None:
        host = self._settings.yagmail_host or "smtp.gmail.com"
        port = self._settings.yagmail_port or (465 if self._settings.yagmail_smtp_ssl else 587)

        if self._settings.yagmail_smtp_ssl:
            server: smtplib.SMTP = smtplib.SMTP_SSL(host, port)
        else:
            server = smtplib.SMTP(host, port)

        raw_bytes = self._normalize_raw_mime(raw_mime)

        try:
            server.ehlo()
            if (not self._settings.yagmail_smtp_ssl) and (self._settings.yagmail_smtp_starttls is not False):
                server.starttls()
                server.ehlo()

            server.login(self._settings.yagmail_user, self._settings.yagmail_password)
            server.sendmail(self._settings.yagmail_user, recipients, raw_bytes)
        finally:
            try:
                server.quit()
            except Exception:
                server.close()

    def send(self, message: EmailMessage) -> None:

        if message.raw_mime:
            self._send_raw_via_smtplib(message.raw_mime, list(message.to) + (list(message.cc) if message.cc else []) + (list(message.bcc) if message.bcc else []))
            return

        contents = []
        if message.html:
            contents.append(message.html)
        elif message.text:
            contents.append(message.text)

        self._smtp.send(
            to=list(message.to),
            subject=message.subject,
            contents=contents,
            cc=list(message.cc) if message.cc else None,
            bcc=list(message.bcc) if message.bcc else None,
            headers=dict(message.headers) if message.headers else None,
        )
