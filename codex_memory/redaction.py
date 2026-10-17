"""Local secret redaction performed before model requests or logs."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SENSITIVE_FIELD = re.compile(
    r'(?i)(authorization|proxy[-_]?authorization|cookie|set[-_]?cookie|'
    r'api[-_]?key|access[-_]?token|refresh[-_]?token|id[-_]?token|'
    r'client[-_]?secret|password|passwd|credential|secret)'
)
AUTH_QUERY_KEY = re.compile(
    r'(?i)^(?:.*[-_])?token$|^(?:key|api[-_]?key|auth|authorization|'
    r'signature|sig|credential|code|client[-_]?secret)$'
)
BEARER = re.compile(r'(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+')
API_KEY = re.compile(r'\b(?:sk|sess)-[A-Za-z0-9_-]{12,}\b')
JWT = re.compile(r'\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b')
JSON_SECRET = re.compile(
    r'(?i)(["\'](?:authorization|cookie|set-cookie|api[-_]?key|access[-_]?token|'
    r'refresh[-_]?token|client[-_]?secret|password|secret)["\']\s*:\s*)'
    r'(["\'])(.*?)(\2)'
)
URL_CANDIDATE = re.compile(r'https?://[^\s"\'<>]+')


def redact_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        if not parts.scheme or not parts.netloc:
            return url
        query = [
            (key, "[REDACTED]" if AUTH_QUERY_KEY.search(key) else value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
        ]
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        return urlunsplit((parts.scheme, host, parts.path, urlencode(query), ""))
    except Exception:
        return url


def redact_text(text: str) -> str:
    """Redact common credentials without attempting to interpret instructions."""
    text = BEARER.sub(lambda m: f"{m.group(1)} [REDACTED]", text)
    text = API_KEY.sub("[REDACTED_API_KEY]", text)
    text = JWT.sub("[REDACTED_JWT]", text)
    text = JSON_SECRET.sub(lambda m: f'{m.group(1)}{m.group(2)}[REDACTED]{m.group(4)}', text)
    return URL_CANDIDATE.sub(lambda m: redact_url(m.group(0)), text)


def redact_mapping(value):
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if SENSITIVE_FIELD.search(str(key)) else redact_mapping(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_mapping(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value

