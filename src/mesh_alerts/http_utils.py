"""HTTP URL validation for outbound requests."""

from __future__ import annotations

import http.client
import ipaddress
import socket
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


def _resolve_public_addresses(host: str, port: int):
    """Resolve once and reject the whole answer if any destination is non-public."""
    try:
        literal = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise ValueError("O host resolve para um endereço IP não público.")

    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses:
        raise OSError(f"Não foi possível resolver o host {host!r}.")

    for family, _socktype, _proto, _canonname, sockaddr in addresses:
        try:
            address = ipaddress.ip_address(sockaddr[0].split("%", 1)[0])
        except ValueError as exc:
            raise ValueError("A resolução DNS retornou um endereço inválido.") from exc
        if not address.is_global:
            raise ValueError(
                f"O host {host!r} resolve para um endereço IP não público."
            )
        if family not in (socket.AF_INET, socket.AF_INET6):
            raise ValueError("A resolução DNS retornou uma família de endereço não suportada.")
    return addresses


def _connect_to_resolved_address(host: str, port: int, timeout, source_address=None):
    """Connect to an already-validated sockaddr, never resolving the hostname again."""
    addresses = _resolve_public_addresses(host, port)
    last_error = None
    for family, socktype, proto, _canonname, sockaddr in addresses:
        connection = socket.socket(family, socktype, proto)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                connection.settimeout(timeout)
            if source_address is not None:
                connection.bind(source_address)
            connection.connect(sockaddr)
            return connection
        except OSError as exc:
            last_error = exc
            connection.close()
    if last_error is not None:
        raise last_error
    raise OSError(f"Não foi possível conectar ao host {host!r}.")


class _ValidatedHTTPConnection(http.client.HTTPConnection):
    """HTTP connection pinned to a public address resolved for this connection."""

    def connect(self):
        if self._tunnel_host:
            raise ValueError("Túneis HTTP não são permitidos nesta conexão validada.")
        self.sock = _connect_to_resolved_address(
            self.host, self.port, self.timeout, self.source_address
        )


class _ValidatedHTTPSConnection(http.client.HTTPSConnection):
    """TLS connection pinned to a public address while verifying the original hostname."""

    def connect(self):
        if self._tunnel_host:
            raise ValueError("Túneis HTTPS não são permitidos nesta conexão validada.")
        raw_socket = _connect_to_resolved_address(
            self.host, self.port, self.timeout, self.source_address
        )
        try:
            self.sock = self._context.wrap_socket(
                raw_socket, server_hostname=self.host
            )
        except Exception:
            raw_socket.close()
            raise


class _ValidatedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, request):
        return self.do_open(_ValidatedHTTPConnection, request)


class _ValidatedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        return self.do_open(
            _ValidatedHTTPSConnection,
            request,
            context=self._context,
            check_hostname=self._check_hostname,
        )


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
    """Open an HTTP(S) request with DNS validation bound to the actual socket."""
    validate_http_url(request.full_url)
    # Do not honor environment proxies: they would move the actual connection
    # away from the validated destination and bypass address pinning.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _ValidatedRedirectHandler,
        _ValidatedHTTPHandler,
        _ValidatedHTTPSHandler,
    )
    return opener.open(request, timeout=timeout)  # nosec B310
