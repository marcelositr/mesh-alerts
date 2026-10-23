import unittest
from mesh_alerts.http_utils import validate_http_url


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


if __name__ == "__main__":
    unittest.main()
