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



if __name__ == "__main__":
    unittest.main()
