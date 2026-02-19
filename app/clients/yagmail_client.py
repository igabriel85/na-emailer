from __future__ import annotations
from typing import Any
import yagmail
from ..config import Settings
from ..models import EmailMessage
from .base import EmailClient


class YagmailClient(EmailClient):
    def __init__(self, settings: Settings):
        if not settings.yagmail_user or not settings.yagmail_password:
            raise ValueError("NA_YAGMAIL_USER and NA_YAGMAIL_PASSWORD are required for yagmail client")

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

    def send(self, message: EmailMessage) -> None:
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
