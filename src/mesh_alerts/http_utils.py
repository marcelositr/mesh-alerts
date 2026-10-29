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


class _ValidatedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Revalidate redirect targets and prevent HTTPS-to-HTTP downgrades."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        validate_http_url(newurl)
        source_scheme = urlsplit(request.full_url).scheme.lower()
        target_scheme = urlsplit(newurl).scheme.lower()
        if source_scheme == "https" and target_scheme != "https":
            raise ValueError("Redirecionamento de HTTPS para HTTP não é permitido.")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def open_http_url(request: Request, *, timeout: float):
    """Open a request after validating its URL and every redirect target."""
    validate_http_url(request.full_url)
    opener = urllib.request.build_opener(_ValidatedRedirectHandler)
    return opener.open(request, timeout=timeout)  # nosec B310
