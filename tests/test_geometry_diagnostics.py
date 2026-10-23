import unittest
from unittest.mock import patch
import urllib.error
import xml.etree.ElementTree as ET

from testar_alertas_regiao_ituverava import diagnose_invalid_polygons
from mesh_alerts_boundaries import download_with_retries

CAP_NS = "urn:oasis:names:tc:emergency:cap:1.2"


class GeometryDiagnosticTests(unittest.TestCase):
    def test_malformed_polygon_is_reported_instead_of_silently_skipped(self):
        alert = ET.fromstring(
            f'<c:alert xmlns:c="{CAP_NS}"><c:info><c:area>'
            '<c:polygon>not-a-coordinate</c:polygon>'
            '</c:area></c:info></c:alert>'
        )

        warnings = diagnose_invalid_polygons(alert)

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
            "mesh_alerts_boundaries.urllib.request.urlopen",
            side_effect=[urllib.error.URLError("timed out"), Response()],
        ) as urlopen, patch("mesh_alerts_boundaries.time.sleep"):
            data = download_with_retries(object(), "fonte de teste")

        self.assertEqual(data, b"ok")
        self.assertEqual(urlopen.call_count, 2)

    def test_raises_after_all_retries_fail(self):
        with patch(
            "mesh_alerts_boundaries.urllib.request.urlopen",
            side_effect=urllib.error.URLError("timed out"),
        ) as urlopen, patch("mesh_alerts_boundaries.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "após 3 tentativas"):
                download_with_retries(object(), "fonte de teste")

        self.assertEqual(urlopen.call_count, 3)


if __name__ == "__main__":
    unittest.main()
