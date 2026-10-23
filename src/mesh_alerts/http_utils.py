"""HTTP URL validation for outbound requests."""

from __future__ import annotations

import urllib.request
from urllib.parse import urlsplit
from urllib.request import Request


def validate_http_url(url: str) -> None:
    """Reject non-HTTP(S) URLs before making an outbound request."""
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("A URL precisa usar HTTP ou HTTPS e informar um host.")


def open_http_url(request: Request, *, timeout: float):
    """Open a request after validating its URL scheme and host."""
    validate_http_url(request.full_url)
    return urllib.request.urlopen(request, timeout=timeout)  # nosec B310
