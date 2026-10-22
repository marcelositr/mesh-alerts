import unittest
import xml.etree.ElementTree as ET

from testar_alertas_regiao_ituverava import avisos_poligonos_invalidos

CAP_NS = "urn:oasis:names:tc:emergency:cap:1.2"


class GeometryDiagnosticTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
