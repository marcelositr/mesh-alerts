import hashlib
from datetime import datetime
import json
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from testar_alertas_regiao_ituverava import (
    MUNICIPIOS,
    limites_municipais_do_geojson,
)
from mesh_alerts_cap import NS, intersecting_municipalities

FIXTURES = Path(__file__).parent / "fixtures"
GEOJSON = FIXTURES / "ibge-sp-municipios-api.geojson"
METADATA = FIXTURES / "ibge-sp-municipios-api.metadata.json"
ALERT_XML = FIXTURES / "cap-194638-2026.xml"

EXPECTED_CITIES = {"Aramina", "Igarapava", "Miguelópolis"}
EXPECTED_CODES = {"3503000", "3520103", "3529708"}


class RealAlertGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.metadata = json.loads(
            METADATA.read_text(encoding="utf-8")
        )
        raw_geojson = GEOJSON.read_bytes()
        digest = hashlib.sha256(raw_geojson).hexdigest()
        if digest != cls.metadata["fixture_sha256"]:
            raise RuntimeError("SHA-256 do snapshot IBGE não confere")

        geojson = json.loads(raw_geojson)
        cls.boundaries = limites_municipais_do_geojson(geojson)
        cls.municipality_names = {
            code: MUNICIPIOS[code] for code in MUNICIPIOS
        }
        feed = ET.parse(ALERT_XML).getroot()
        alerts = feed.findall(".//c:alert", NS)
        if len(alerts) != 1:
            raise RuntimeError(
                f"Esperado exatamente um alerta CAP; encontrados: {len(alerts)}"
            )
        cls.root = alerts[0]
        cls.now = datetime.fromisoformat("2026-10-22T14:00:00-03:00")

    def test_snapshot_contains_all_six_municipalities(self):
        self.assertEqual(set(self.boundaries), set(MUNICIPIOS))

    def test_real_cap_alert_matches_expected_municipalities(self):
        cities, expired_count, circle_count = intersecting_municipalities(
            self.root,
            self.boundaries,
            self.municipality_names,
            self.now,
        )

        self.assertEqual(cities, EXPECTED_CITIES)
        self.assertEqual(expired_count, 0)
        self.assertEqual(circle_count, 0)


if __name__ == "__main__":
    unittest.main()
