# na-emailer

Knative function that receives **CloudEvents**, optionally filters them (typically via configuration injected by a **Knative SinkBinding**), renders **Jinja2** templates (subject + plain/html), and sends an email notification.

## How it works
- Accepts an incoming HTTP request carrying a CloudEvent (binary or structured).
- Loads configuration from environment variables (all `NA_*`).
- Applies attribute filters (e.g. `type`, `source`, `subject`, extensions).
- Selects templates based on CloudEvent `type`.
- Renders templates from either:
  - inline templates provided via env vars (recommended for Knative manifest-based config), or
  - template files on disk.
- Sends email using a pluggable client backend (default: `yagmail`).

## Logging
- `NA_LOG_LEVEL`: `DEBUG|INFO|WARNING|ERROR` (default: `INFO`).
- The service logs a startup line **at process start**:
  - `na-emailer started (ready to receive events)`
- For each request it logs: received event, filtered out / rendered / dry-run / send result.

## Environment variables
### Filtering
- `NA_FILTERS_JSON`: JSON object of required matches.
  - Example: `{"type":"com.acme.ready","source":"/sensor"}`
  - Values can be scalars or arrays (array means “actual in expected”).
- `NA_FILTER_MODE`: `all` (default) or `any`.

### Template selection
- `NA_TEMPLATE_MAP_JSON`: JSON object mapping CloudEvent type to template base name.
  - Example: `{"com.acme.job.done":"job_done"}`
- `NA_TEMPLATE_DEFAULT`: fallback template base name (default `default`).
- `NA_TEMPLATE_STRICT_UNDEFINED`: `true|false` (default `false`).

### Template loading (inline vs filesystem)
The renderer chooses templates in this order:
1. **Inline templates** from `NA_TEMPLATES_INLINE_JSON` (if set and non-empty)
2. Filesystem templates from `NA_TEMPLATES_DIR`

#### Inline templates (recommended for Knative manifests)
- `NA_TEMPLATES_INLINE_JSON`: JSON object mapping template filenames to template content.

Example value:
- `{"default.subject.j2":"[{{ ce.type }}] Notification","default.txt.j2":"Hello {{ data.name }}"}`

Notes:
- Keys must be the exact template filenames the renderer looks up, e.g. `default.subject.j2`, `default.txt.j2`, `default.html.j2`.
- When embedding JSON in YAML (Knative manifests), make sure to quote/escape properly (usually a YAML block scalar is easiest).
- Inline templates will also be defineble via CE data (e.g. `data.templates_inline_json`).

#### Filesystem templates
- `NA_TEMPLATES_DIR`: templates folder.
  - **Local default**: the repo’s `./templates` directory (resolved automatically).
  - **Container default**: `/app/templates` (the Dockerfile copies templates there).

### Email
- `NA_EMAIL_CLIENT`: email backend (default `yagmail`).
- `NA_EMAIL_FROM`: optional sender.
- `NA_EMAIL_TO`: comma-separated recipients, optional is overriden by CE data.
- `NA_EMAIL_CC`, `NA_EMAIL_BCC`: optional, overriden by CE settings.
- `NA_EMAIL_SUBJECT_PREFIX`: optional prefix, overriden by CE settings.
- `NA_DRY_RUN`: `true|false` (if true, renders but doesn’t send).

### Yagmail backend
Required when `NA_EMAIL_CLIENT=yagmail` and `NA_DRY_RUN=false`:
- `NA_YAGMAIL_USER`
- `NA_YAGMAIL_PASSWORD`

Optional:
- `NA_YAGMAIL_HOST`, `NA_YAGMAIL_PORT`
- `NA_YAGMAIL_SMTP_STARTTLS`, `NA_YAGMAIL_SMTP_SSL`

Note: In case of standard Gmail account this settings are not required.

## Templates
Templates are chosen by **base name**.

For base name `default` the renderer will look for:
- `default.subject.j2` (required)
- `default.txt.j2` (optional)
- `default.html.j2` (optional)

The template context includes:
- `ce`: CloudEvent attributes (plus extensions)
- `data`: the CloudEvent data

Note: If `inline_templates` are provided, the html template is prioritized and used for email body. If only the text template is provided, it will be used as the email body.
## Local development
### Option A: run via `start.py` (recommended)
This prints an explicit “Waiting for CloudEvents...” line and defaults to dry-run.

```zsh
python -m pip install -e '.[test]'
python start.py
```

### Option B: run via Functions Framework directly

```zsh
python -m pip install -e '.[test]'
export NA_DRY_RUN=true
functions-framework --target handle --port 8080
```

### Send a test CloudEvent

```zsh
curl -i http://localhost:8080/ \
  -H 'Content-Type: application/cloudevents+json' \
  -d '{
  "specversion": "1.0",
  "id": "1",
  "source": "/local",
  "type": "com.acme.test",
  "datacontenttype": "application/json",
  "data": {
    "name": "Hello world",
    "subject": "NA Notification Test 3",
    "email_to": [
      "juhasz.gabriel@gmail.com"
    ],
    "email_cc": [
      "juhasz_gabriel@yahoo.com"
    ],
    "email_bcc": [
    ],
    "templates_inline_json": {
      "default.subject.j2": "[{{ ce.type }}] Notification",
      "default.txt.j2": "Hello {{ data.name }}\n\nCloudEvent: {{ ce.id }}\nType: {{ ce.type }}\nSource: {{ ce.source }}\n",
      "default.html.j2": "<!doctype html>\n<html>\n  <body style=\"font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif;\">\n    <h2>NA Notification</h2>\n    <p><b>Name:</b> {{ data.name }}</p>\n\n    <h3>CloudEvent</h3>\n    <ul>\n      <li><b>id</b>: {{ ce.id }}</li>\n      <li><b>type</b>: {{ ce.type }}</li>\n      <li><b>source</b>: {{ ce.source }}</li>\n      <li><b>subject</b>: {{ ce.subject }}</li>\n      <li><b>time</b>: {{ ce.time }}</li>\n    </ul>\n\n    <h3>Data</h3>\n    <pre style=\"background:#f6f8fa;padding:12px;border-radius:6px;\">{{ data | tojson(indent=2) }}</pre>\n  </body>\n</html>\n"
    }
  }
}
'

```

## Notes
This repo is intentionally small and focused (core runtime + tests).

Add new email backends by implementing `app.clients.base.EmailClient` and wiring it in `app.clients.factory.create_email_client`.
