from __future__ import annotations

import logging
import os
import functions_framework
from typing import Any
from cloudevents.http import from_http
from flask import Request
from .clients.factory import create_email_client
from .config import load_settings
from .filtering import matches_filters
from .models import EmailMessage, EventContext
from .templating import TemplateRenderer


logger = logging.getLogger("na_emailer")
_LOGGING_CONFIGURED = False


def _configure_logging(level_name: str) -> None:
    global _LOGGING_CONFIGURED

    level = getattr(logging, (level_name or "INFO").upper(), logging.INFO)

    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
    else:
        root.setLevel(level)

    if not _LOGGING_CONFIGURED:
        logger.info("na-emailer started (ready to receive events)")
        _LOGGING_CONFIGURED = True

_configure_logging(os.getenv("NA_LOG_LEVEL", "INFO"))


def _ctx_from_cloudevent(ce) -> EventContext:
    #ce SDK objects are mapping-like, but dict(ce) is not reliable across versions.
    spec_keys = {
        "id",
        "source",
        "type",
        "specversion",
        "subject",
        "time",
        "dataschema",
        "emailto",
        "emailcc",
        "emailbcc",
        "datacontenttype",
        "data",
    }

    raw_attrs = getattr(ce, "_attributes", None)
    if isinstance(raw_attrs, dict):
        attrs = raw_attrs
    else:
        attrs = {}

    extensions = {k: v for k, v in attrs.items() if k not in spec_keys}
    return EventContext(
        id=ce["id"],
        source=ce["source"],
        type=ce["type"],
        subject=ce.get("subject"),
        time=ce.get("time"),
        dataschema=ce.get("dataschema"),
        emailto=ce.get("emailto"),
        emailcc=ce.get("emailcc"),
        emailbcc=ce.get("emailbcc"),
        datacontenttype=ce.get("datacontenttype"),
        data=ce.data,
        extensions=extensions,
    )

def _parse_recipients(value) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


def _recipients_from_event(ctx: EventContext):
    #support both ce-email_to and email_to in data for maximum flexibility.
    return _parse_recipients(ctx.emailto), _parse_recipients(ctx.emailcc), _parse_recipients(ctx.emailbcc)

def _extract_raw_mime(ctx: EventContext) -> str | None:
    data: Any = ctx.data
    if data is None:
        return None
    if isinstance(data, (bytes, bytearray)):
        try:
            return bytes(data).decode("utf-8", errors="replace")
        except Exception:
            return None
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        raw = data.get("raw_mime") or data.get("mime") or data.get("message")
        if isinstance(raw, str) and raw.strip():
            return raw
    return None

@functions_framework.http
def handle(request: Request):
    settings = load_settings()
    _configure_logging(settings.log_level)

    logger.info("Request received")

    try:
        ce = from_http(request.headers, request.get_data())
    except Exception as e:
        logger.exception(f"Failed to parse incoming CloudEvent: {e}")
        return ("Invalid CloudEvent", 400)

    try:
        ctx = _ctx_from_cloudevent(ce)
    except Exception as e:
        logger.exception(f"Failed to translate CloudEvent into internal context: {e}")
        return ("Invalid CloudEvent", 400)

    logger.info(
        "CloudEvent received",
        extra={"ce_id": ctx.id, "ce_type": ctx.type, "ce_source": ctx.source, "ce_subject": ctx.subject},
    )

    if not matches_filters(ctx, settings.filters_json, settings.filter_mode):
        logger.info(
            "Event filtered out",
            extra={"ce_id": ctx.id, "ce_type": ctx.type, "ce_source": ctx.source},
        )
        return ("", 204)

    event_recipients, event_cc, event_bcc =_recipients_from_event(ctx)

    recipients = event_recipients or settings.email_to
    email_cc = event_cc or settings.email_cc
    email_bcc = event_bcc or settings.email_bcc

    #MIME multipart mode: bypass templating and send raw MIME as-is.
    raw_mime = None
    if (ctx.datacontenttype or "").strip().lower() in {"mimemultipart", "mime/multipart", "multipart/mixed"}:
        raw_mime = _extract_raw_mime(ctx)
        if not raw_mime:
            return ("Missing raw MIME payload in CloudEvent data", 400)

        msg = EmailMessage(
            subject="",
            text=None,
            html=None,
            sender=settings.email_from,
            to=recipients,
            cc=email_cc,
            bcc=email_bcc,
            headers={
                "X-CloudEvent-ID": ctx.id,
                "X-CloudEvent-Type": ctx.type,
                "X-CloudEvent-Source": ctx.source,
            },
            raw_mime=raw_mime,
        )

        if not (list(msg.to) or list(msg.cc) or list(msg.bcc)):
            logger.warning("No recipients configured; skipping send", extra={"ce_id": ctx.id})
            return ("", 202)

        if settings.dry_run:
            logger.info("DRY RUN raw MIME email (not sent)", extra={"ce_id": ctx.id})
            return ("", 202)

        try:
            client = create_email_client(settings)
            client.send(msg)
        except Exception as e:
            logger.exception(f"Failed to send raw MIME email: {e}")
            return ("Email send failed", 502)

        return ("", 202)

    # if ce contains inline templates
    if ctx.data and isinstance(ctx.data, dict) and "templates_inline_json" in ctx.data:
        settings.templates_inline_json = ctx.data["templates_inline_json"]

    try:
        renderer = TemplateRenderer(settings)
        subject, text, html = renderer.render(ctx)
    except Exception as e:
        logger.exception(f"Failed to render email templates: {e}")
        return ("Template rendering failed", 500)

    msg = EmailMessage(
        subject=subject,
        text=text,
        html=html,
        sender=settings.email_from,
        to=recipients,
        cc=email_cc,
        bcc=email_bcc,
        headers={
            "X-CloudEvent-ID": ctx.id,
            "X-CloudEvent-Type": ctx.type,
            "X-CloudEvent-Source": ctx.source,
        },
        raw_mime=raw_mime,
    )
    logger.info(f"Prepared email message: subject='{msg.subject}', to={msg.to}, cc={msg.cc}, bcc={msg.bcc}")

    if not msg.to:
        logger.warning(
            "No recipients configured (NA_EMAIL_TO is empty); skipping send",
            extra={"ce_id": ctx.id, "ce_type": ctx.type},
        )
        return ("", 202)

    if settings.dry_run:
        logger.info("DRY RUN email (not sent)", extra={"to": list(msg.to), "subject": msg.subject})
        return ("", 202)

    try:
        client = create_email_client(settings)
        client.send(msg)
    except Exception as e:
        logger.exception(f"Failed to send email: {e}")
        return ("Email send failed", 502)

    logger.info("Email queued/sent", extra={"to": list(msg.to), "subject": msg.subject, "ce_id": ctx.id})
    return ("", 202)
