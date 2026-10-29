"""HTTP URL validation for outbound requests."""

from __future__ import annotations

import ipaddress
import urllib.request
from urllib.parse import urlsplit
from urllib.request import Request


def validate_http_url(url: str) -> None:
    """Reject unsafe URL forms and literal non-public IP destinations."""
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("A URL precisa usar HTTP ou HTTPS e informar um host.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URLs com credenciais embutidas não são permitidas.")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("A URL contém uma porta inválida.") from exc
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("Destinos IP não públicos (privados, loopback ou reservados) não são permitidos.")


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
