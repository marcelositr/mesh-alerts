import unittest
from unittest.mock import patch
import urllib.error
import xml.etree.ElementTree as ET
from urllib.request import Request

from mesh_alerts.region import avisos_poligonos_invalidos
from mesh_alerts.boundaries import download_with_retries

CAP_NS = "urn:oasis:names:tc:emergency:cap:1.2"


class GeometryDiagnosticTests(unittest.TestCase):
    def test_open_polygon_reports_automatic_ring_closure(self):
        alert = ET.fromstring(
            f'<c:alert xmlns:c="{CAP_NS}"><c:info><c:area>'
            '<c:polygon>-20,-47 -20.1,-47 -20.1,-47.1</c:polygon>'
            '</c:area></c:info></c:alert>'
        )

        warnings = avisos_poligonos_invalidos(alert)

        self.assertEqual(len(warnings), 1)
        self.assertIn("anel não fechado", warnings[0])
        self.assertIn("fechamento automático aplicado", warnings[0])
        self.assertNotIn("geometria ignorada", warnings[0])

    def test_malformed_polygon_is_reported_instead_of_silently_skipped(self):
        alert = ET.fromstring(
            f'<c:alert xmlns:c="{CAP_NS}"><c:info><c:area>'
            '<c:polygon>not-a-coordinate</c:polygon>'
            '</c:area></c:info></c:alert>'
        )

        warnings = avisos_poligonos_invalidos(alert)

        self.assertEqual(len(warnings), 1)
        self.assertIn("não interpretável", warnings[0])
        self.assertIn("geometria ignorada", warnings[0])


class NetworkRetryTests(unittest.TestCase):
    def test_retries_transient_url_error_then_succeeds(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b"ok"

        with patch(
            "mesh_alerts.boundaries.open_http_url",
            side_effect=[urllib.error.URLError("timed out"), Response()],
        ) as urlopen, patch("mesh_alerts.boundaries.time.sleep"):
            data = download_with_retries(Request("https://example.test"), "fonte de teste")

        self.assertEqual(data, b"ok")
        self.assertEqual(urlopen.call_count, 2)

    def test_raises_after_all_retries_fail(self):
        with patch(
            "mesh_alerts.boundaries.open_http_url",
            side_effect=urllib.error.URLError("timed out"),
        ) as urlopen, patch("mesh_alerts.boundaries.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "após 3 tentativas"):
                download_with_retries(Request("https://example.test"), "fonte de teste")

        self.assertEqual(urlopen.call_count, 3)


if __name__ == "__main__":
    unittest.main()
