from __future__ import annotations

import json
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


def _recipients_from_event(ctx: EventContext, filter="email_to") -> list[str]:
    #prefer CloudEvent extension first (binary mode: ce-email_to header -> extension)
    if filter in ctx.extensions:
        return _parse_recipients(ctx.extensions.get(filter))

    data: Any = ctx.data

    #normalize structured-mode data into a dict if it came in as JSON string/bytes
    if isinstance(data, (bytes, bytearray)):
        try:
            data = data.decode("utf-8")
        except Exception:
            data = None

    if isinstance(data, str):
        s = data.strip()
        if s.startswith("{") and s.endswith("}"):
            try:
                data = json.loads(s)
            except Exception:
                data = None

    if isinstance(data, dict) and filter in data:
        return _parse_recipients(data.get(filter))

    return []

@functions_framework.http
def handle(request: Request):
    settings = load_settings()
    _configure_logging(settings.log_level)

    logger.info("Request received")

    try:
        ce = from_http(request.headers, request.get_data())
    except Exception:
        logger.exception("Failed to parse incoming CloudEvent")
        return ("Invalid CloudEvent", 400)

    try:
        ctx = _ctx_from_cloudevent(ce)
    except Exception:
        logger.exception("Failed to translate CloudEvent into internal context")
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

    #if ce contains inline templates
    if ctx.data and isinstance(ctx.data, dict) and "templates_inline_json" in ctx.data:
        settings.templates_inline_json =  ctx.data["templates_inline_json"]

    try:
        renderer = TemplateRenderer(settings)
        subject, text, html = renderer.render(ctx)
    except Exception:
        logger.exception("Failed to render email templates")
        return ("Template rendering failed", 500)

    #CE overrides NA_EMAIL_TO if present, to allow dynamic recipients per event.
    event_recipients = _recipients_from_event(ctx)
    event_cc = _recipients_from_event(ctx, filter="email_cc")
    event_bcc = _recipients_from_event(ctx, filter="email_bcc")

    recipients = event_recipients or settings.email_to
    email_cc = event_cc or settings.email_cc
    email_bcc = event_bcc or settings.email_bcc

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
    )
    print(f"Prepared email message: subject='{msg.subject}', to={msg.to}, cc={msg.cc}, bcc={msg.bcc}")

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
    except Exception:
        logger.exception("Failed to send email")
        return ("Email send failed", 502)

    logger.info("Email queued/sent", extra={"to": list(msg.to), "subject": msg.subject, "ce_id": ctx.id})
    return ("", 202)
