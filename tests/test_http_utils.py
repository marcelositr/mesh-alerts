import unittest
from urllib.request import Request

from mesh_alerts.http_utils import (
    _ValidatedRedirectHandler,
    validate_http_url,
)


class ValidateHttpUrlTests(unittest.TestCase):
    def test_accepts_http_and_https(self):
        validate_http_url("http://example.test/resource")
        validate_http_url("https://example.test/resource")

    def test_rejects_file_url(self):
        with self.assertRaises(ValueError):
            validate_http_url("file:///etc/passwd")

    def test_rejects_ftp_url(self):
        with self.assertRaises(ValueError):
            validate_http_url("ftp://example.test/resource")

    def test_rejects_missing_host(self):
        with self.assertRaises(ValueError):
            validate_http_url("https:///resource")

    def test_rejects_redirect_to_non_http_scheme(self):
        handler = _ValidatedRedirectHandler()
        request = Request("https://example.test/start")
        with self.assertRaises(ValueError):
            handler.redirect_request(
                request, None, 302, "Found", {}, "file:///etc/passwd"
            )

    def test_rejects_https_downgrade_redirect(self):
        handler = _ValidatedRedirectHandler()
        request = Request("https://example.test/start")
        with self.assertRaisesRegex(ValueError, "HTTPS para HTTP"):
            handler.redirect_request(
                request, None, 302, "Found", {}, "http://example.test/end"
            )

    def test_allows_https_redirect(self):
        handler = _ValidatedRedirectHandler()
        request = Request("https://example.test/start")
        redirected = handler.redirect_request(
            request, None, 302, "Found", {}, "https://cdn.example.test/end"
        )
        self.assertEqual(redirected.full_url, "https://cdn.example.test/end")


    def test_rejects_private_loopback_and_reserved_literal_ips(self):
        for url in (
            "http://127.0.0.1/resource",
            "http://10.0.0.8/resource",
            "http://192.168.1.10/resource",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/resource",
            "http://[fc00::1]/resource",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_http_url(url)

    def test_rejects_embedded_credentials_and_invalid_port(self):
        with self.assertRaisesRegex(ValueError, "credenciais"):
            validate_http_url("https://user:pass@example.test/resource")
        with self.assertRaisesRegex(ValueError, "porta inválida"):
            validate_http_url("https://example.test:bad/resource")

    def test_rejects_redirect_to_private_ip(self):
        handler = _ValidatedRedirectHandler()
        request = Request("https://example.test/start")
        with self.assertRaises(ValueError):
            handler.redirect_request(
                request, None, 302, "Found", {}, "http://127.0.0.1/internal"
            )


if __name__ == "__main__":
    unittest.main()
