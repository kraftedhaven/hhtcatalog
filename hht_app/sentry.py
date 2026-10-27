"""Sentry error monitoring and privacy scrubbing for HHT Commerce Agent."""

import os
import re
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

# Sensitive key patterns that must have their values replaced with [SCRUBBED]
SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)(token|secret|password|passwd|key|credential|auth|authorization|jwt|cookie|session|"
    r"api[-_]?key|refresh[-_]?token|access[-_]?token|bearer|private[-_]?key|client[-_]?secret)"
)

# Connection string pattern (e.g. postgresql://user:password@host:port/db)
DB_URL_PATTERN = re.compile(r"(postgres(?:ql)?://[^:]+:)([^@]+)(@)", re.IGNORECASE)

# Presigned URL / Signed photo URL query parameter pattern
SIGNED_QUERY_PARAMS = {
    "x-amz-signature",
    "x-amz-credential",
    "x-amz-security-token",
    "signature",
    "sig",
    "token",
    "x-amz-date",
}

# Data URI pattern for images
DATA_URI_PATTERN = re.compile(r"data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=]+", re.IGNORECASE)

# JWT pattern
JWT_PATTERN = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9._-]{10,}\.[A-Za-z0-9._-]{10,}")


def sanitize_url(url: str) -> str:
    """Scrub database credentials and signed storage URL signatures from URLs."""
    if not isinstance(url, str) or not url:
        return url

    # Scrub DB password if present
    scrubbed = DB_URL_PATTERN.sub(r"\1[SCRUBBED]\3", url)

    # Scrub signed storage parameters (e.g. IBM COS / S3 signed photo URLs)
    if any(param in scrubbed.lower() for param in ("signature", "x-amz-signature", "x-amz-credential", "token")):
        try:
            parts = urlsplit(scrubbed)
            if parts.query:
                query_dict = parse_qs(parts.query, keep_blank_values=True)
                sanitized_query: dict[str, list[str]] = {}
                for k, vals in query_dict.items():
                    if k.lower() in SIGNED_QUERY_PARAMS:
                        sanitized_query[k] = ["[SCRUBBED]"]
                    else:
                        sanitized_query[k] = vals
                new_query = urlencode(sanitized_query, doseq=True, safe="[]")
                scrubbed = urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))
        except Exception:
            pass

    return scrubbed


def sanitize_string(val: str) -> str:
    """Scrub sensitive credentials, tokens, JWTs, DB URLs, and image bytes from strings."""
    if not isinstance(val, str) or not val:
        return val

    # Scrub base64 image data URIs
    if "data:image/" in val:
        val = DATA_URI_PATTERN.sub("[IMAGE_DATA_URL_SCRUBBED]", val)

    # Scrub DB URLs
    val = DB_URL_PATTERN.sub(r"\1[SCRUBBED]\3", val)

    # Scrub JWTs
    val = JWT_PATTERN.sub("[JWT_SCRUBBED]", val)

    # Scrub presigned query params in URLs
    if "http" in val and any(s in val.lower() for s in ("signature", "x-amz")):
        val = sanitize_url(val)

    # Scrub massive base64 payloads (e.g. raw provider image inputs)
    if len(val) > 1000 and re.match(r"^[A-Za-z0-9+/=\s]{1000,}$", val.strip()):
        return f"[IMAGE_BASE64_BYTES_SCRUBBED, len={len(val)}]"

    return val


def sanitize_value(key: str | None, value: Any) -> Any:
    """Recursively sanitize dictionary values, headers, and payloads."""
    # Check if the key itself designates sensitive credential/token
    if key and SENSITIVE_KEY_PATTERN.search(str(key)):
        return "[SCRUBBED]"

    # If the key designates image data or binary photos
    if key and any(k in str(key).lower() for k in ("image", "photo", "pic", "data", "bytes")) and isinstance(value, (bytes, bytearray)):
        return f"[IMAGE_BYTES_SCRUBBED, bytes={len(value)}]"

    if isinstance(value, (bytes, bytearray)):
        return f"[BINARY_BYTES_SCRUBBED, bytes={len(value)}]"

    if isinstance(value, str):
        return sanitize_string(value)

    if isinstance(value, dict):
        return {k: sanitize_value(k, v) for k, v in value.items()}

    if isinstance(value, list):
        return [sanitize_value(key, v) for v in value]

    if isinstance(value, tuple):
        return tuple(sanitize_value(key, v) for v in value)

    return value


def scrub_event(event: dict[str, Any], hint: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Privacy scrubber hook for Sentry events.
    
    Scrubs:
    - Tokens, JWTs, OAuth credentials, passwords, cookies, auth headers
    - DATABASE_URL / connection strings
    - Signed photo URLs (presigned IBM COS / S3 signatures)
    - Image bytes and base64 image data
    - Raw AI provider request/response dumps
    """
    if not isinstance(event, dict):
        return event

    # 1. Scrub Request headers and data
    request_data = event.get("request")
    if isinstance(request_data, dict):
        # Sanitize request URL
        if "url" in request_data:
            request_data["url"] = sanitize_url(str(request_data["url"]))

        # Sanitize query string
        if "query_string" in request_data and isinstance(request_data["query_string"], (str, bytes)):
            qs = request_data["query_string"]
            if isinstance(qs, bytes):
                qs = qs.decode("utf-8", errors="replace")
            request_data["query_string"] = sanitize_url(f"?{qs}").lstrip("?")

        # Sanitize headers
        headers = request_data.get("headers")
        if isinstance(headers, dict):
            request_data["headers"] = {
                k: ("[SCRUBBED]" if SENSITIVE_KEY_PATTERN.search(k) else sanitize_value(k, v))
                for k, v in headers.items()
            }

        # Sanitize body / form / json data
        if "data" in request_data:
            request_data["data"] = sanitize_value("data", request_data["data"])

        # Sanitize cookies
        if "cookies" in request_data:
            request_data["cookies"] = "[SCRUBBED]"

    # 2. Scrub Breadcrumbs
    breadcrumbs = event.get("breadcrumbs")
    if isinstance(breadcrumbs, dict) and isinstance(breadcrumbs.get("values"), list):
        for b in breadcrumbs["values"]:
            if isinstance(b, dict):
                if "data" in b and isinstance(b["data"], dict):
                    b["data"] = sanitize_value("breadcrumb_data", b["data"])
                if "message" in b and isinstance(b["message"], str):
                    b["message"] = sanitize_string(b["message"])

    # 3. Scrub Contexts and Extra
    for section in ("contexts", "extra", "tags", "user"):
        if section in event and isinstance(event[section], dict):
            event[section] = sanitize_value(section, event[section])

    # 4. Scrub Exception values and stack trace variables
    exception = event.get("exception")
    if isinstance(exception, dict) and isinstance(exception.get("values"), list):
        for exc in exception["values"]:
            if isinstance(exc, dict):
                if "value" in exc and isinstance(exc["value"], str):
                    exc["value"] = sanitize_string(exc["value"])
                stacktrace = exc.get("stacktrace")
                if isinstance(stacktrace, dict) and isinstance(stacktrace.get("frames"), list):
                    for frame in stacktrace["frames"]:
                        if isinstance(frame, dict) and isinstance(frame.get("vars"), dict):
                            frame["vars"] = sanitize_value("vars", frame["vars"])

    return event


def scrub_breadcrumb(crumb: dict[str, Any], hint: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Privacy scrubber hook for breadcrumbs."""
    if not isinstance(crumb, dict):
        return crumb

    if "data" in crumb and isinstance(crumb["data"], dict):
        crumb["data"] = sanitize_value("breadcrumb_data", crumb["data"])
    if "message" in crumb and isinstance(crumb["message"], str):
        crumb["message"] = sanitize_string(crumb["message"])

    return crumb


def init_sentry(app=None) -> bool:
    """Initialize Sentry SDK with privacy scrubbing for Flask application."""
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration

        environment = os.environ.get("SENTRY_ENVIRONMENT", "production").strip() or "production"
        release = os.environ.get("SENTRY_RELEASE", "").strip() or None
        traces_sample_rate = float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.10"))
        profile_session_sample_rate = float(os.environ.get("SENTRY_PROFILE_SESSION_SAMPLE_RATE", "0.10"))
        profile_lifecycle = os.environ.get("SENTRY_PROFILE_LIFECYCLE", "trace").strip() or "trace"

        sentry_sdk.init(
            dsn=dsn,
            environment=environment,
            release=release,
            integrations=[FlaskIntegration()],
            send_default_pii=False,
            before_send=scrub_event,
            before_breadcrumb=scrub_breadcrumb,
            traces_sample_rate=traces_sample_rate,
            profile_session_sample_rate=profile_session_sample_rate,
            profile_lifecycle=profile_lifecycle,
        )
        return True
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Sentry initialization failed: %s", exc)
        return False
