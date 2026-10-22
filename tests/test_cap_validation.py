import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from shapely.geometry import box

from testar_alertas_regiao_ituverava import (
    MUNICIPIOS,
    carregar_feed,
    limites_municipais_do_geojson,
    avisos_metadados_geograficos,
    avisos_poligonos_invalidos,
)

from mesh_alerts_cap import (
    active_alert_entries,
    circle_from_cap,
    info_is_current,
    intersecting_municipalities,
    parse_cap_datetime,
    polygon_from_cap,
    references_to_keys,
)

NS = {"c": "urn:oasis:names:tc:emergency:cap:1.2"}


class CapValidationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)

    def info(self, fields):
        parts = "".join(f"<c:{key}>{value}</c:{key}>" for key, value in fields.items())
        return ET.fromstring(f'<c:info xmlns:c="{NS["c"]}">{parts}</c:info>')

    def test_local_cap_feed_file_is_parsed(self):
        xml = '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.xml"
            path.write_text(xml, encoding="utf-8")
            root = carregar_feed(path)
        self.assertEqual(root.tag, "{http://www.w3.org/2005/Atom}feed")

    def test_warns_when_cap_polygon_is_invalid_before_repair(self):
        alert = ET.fromstring(
            '<c:alert xmlns:c="urn:oasis:names:tc:emergency:cap:1.2"><c:info><c:area>'
            '<c:polygon>-20,-47 -20.1,-47.1 -20,-47.1 -20.1,-47 -20,-47</c:polygon>'
            '</c:area></c:info></c:alert>'
        )
        warnings = avisos_poligonos_invalidos(alert)
        self.assertEqual(len(warnings), 1)
        self.assertIn("polígono CAP #1 inválido", warnings[0])
        self.assertIn("reparada", warnings[0])

    def test_warns_when_intersecting_alert_geocodes_are_outside_monitored_states(self):
        alert = ET.fromstring(
            '<c:alert xmlns:c="urn:oasis:names:tc:emergency:cap:1.2"><c:info><c:area>'
            '<c:areaDesc>MINAS GERAIS/MG</c:areaDesc>'
            '<c:geocode><c:valueName>IBGE</c:valueName><c:value>3145000</c:value></c:geocode>'
            '<c:geocode><c:valueName>IBGE</c:valueName><c:value>3157708</c:value></c:geocode>'
            '<c:geocode><c:valueName>IBGE</c:valueName><c:value>3170107</c:value></c:geocode>'
            '</c:area></c:info></c:alert>'
        )
        warnings = avisos_metadados_geograficos(alert)
        self.assertEqual(len(warnings), 1)
        self.assertIn("MINAS GERAIS/MG", warnings[0])
        self.assertIn("3145000", warnings[0])
        self.assertIn("35", warnings[0])
        self.assertIn("não invalida", warnings[0])

    def test_no_metadata_warning_when_ibge_geocode_matches_monitored_state(self):
        alert = ET.fromstring(
            '<c:alert xmlns:c="urn:oasis:names:tc:emergency:cap:1.2"><c:info><c:area>'
            '<c:areaDesc>SÃO PAULO/SP</c:areaDesc>'
            '<c:geocode><c:valueName>IBGE</c:valueName><c:value>3503000</c:value></c:geocode>'
            '</c:area></c:info></c:alert>'
        )
        self.assertEqual(avisos_metadados_geograficos(alert), [])

    def test_ibge_geojson_loads_all_six_valid_municipalities(self):
        geometry = {
            "type": "Polygon",
            "coordinates": [[
                [-47.1, -20.1], [-47.0, -20.1],
                [-47.0, -20.0], [-47.1, -20.0],
                [-47.1, -20.1],
            ]],
        }
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"codarea": code},
                    "geometry": geometry,
                }
                for code in MUNICIPIOS
            ],
        }
        boundaries = limites_municipais_do_geojson(geojson)
        self.assertEqual(set(boundaries), set(MUNICIPIOS))
        self.assertTrue(all(boundary.is_valid for boundary in boundaries.values()))

    def test_ibge_geojson_rejects_non_feature_collection(self):
        with self.assertRaisesRegex(RuntimeError, "FeatureCollection"):
            limites_municipais_do_geojson({"type": "Feature", "features": []})

    def test_ibge_geojson_rejects_malformed_target_geometry(self):
        geojson = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"codarea": "3503000"},
                "geometry": {"type": "Polygon", "coordinates": "malformed"},
            }],
        }
        with self.assertRaisesRegex(RuntimeError, "Geometria inválida"):
            limites_municipais_do_geojson(geojson)

    def test_ibge_geojson_rejects_incomplete_municipality_set(self):
        geojson = {"type": "FeatureCollection", "features": []}
        with self.assertRaisesRegex(RuntimeError, "Malha municipal incompleta"):
            limites_municipais_do_geojson(geojson)

    def test_ibge_geojson_rejects_invalid_target_geometry(self):
        geojson = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"codarea": "3503000"},
                "geometry": None,
            }],
        }
        with self.assertRaisesRegex(RuntimeError, "Geometria ausente"):
            limites_municipais_do_geojson(geojson)

    def test_parse_cap_timestamp_normalizes_utc(self):
        parsed = parse_cap_datetime("2026-10-22T15:00:00-03:00")
        self.assertEqual(parsed, datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc))

    def test_expired_info_is_not_current(self):
        info = self.info({"expires": "2026-10-22T17:59:59Z"})
        self.assertEqual(info_is_current(info, self.now), (False, "expires expirado"))

    def test_future_onset_does_not_deactivate_current_warning(self):
        info = self.info({
            "effective": "2026-10-22T17:00:00Z",
            "onset": "2026-10-22T18:01:00Z",
            "expires": "2026-10-22T19:00:00Z",
        })
        self.assertEqual(info_is_current(info, self.now), (True, None))

    def test_future_effective_is_not_current(self):
        info = self.info({"effective": "2026-10-22T18:01:00Z"})
        self.assertEqual(info_is_current(info, self.now), (False, "effective futuro"))

    def test_malformed_onset_fails_closed(self):
        info = self.info({"onset": "not-a-time"})
        self.assertEqual(info_is_current(info, self.now), (False, "timestamp onset inválido"))

    def test_malformed_expiry_fails_closed(self):
        info = self.info({"expires": "not-a-time"})
        self.assertEqual(info_is_current(info, self.now), (False, "timestamp expires inválido"))

    def test_polygon_parses_lat_lon_and_rejects_bad_input(self):
        polygon = polygon_from_cap(
            "-20.0,-47.0 -20.1,-47.0 -20.1,-47.1 -20.0,-47.0"
        )
        self.assertIsNotNone(polygon)
        self.assertTrue(polygon.is_valid)
        self.assertIsNone(polygon_from_cap("bad input"))
        self.assertIsNone(
            polygon_from_cap("-20.0,-47.0 -20.1,-47.0 -20.1,-47.1")
        )
        self.assertIsNone(
            polygon_from_cap(
                "-20.0,-47.0 -20.1,-47.0 999,-47.1 -20.0,-47.0"
            )
        )

    def test_circle_radius_is_converted_to_polygon(self):
        circle = circle_from_cap("-20.0,-47.0 10")
        self.assertIsNotNone(circle)
        self.assertTrue(circle.is_valid)
        self.assertGreater(circle.area, 0)

    def test_polygon_intersection_includes_boundary_touch(self):
        alert = ET.fromstring(
            f'<c:alert xmlns:c="{NS["c"]}"><c:info>'
            '<c:area><c:polygon>'
            '-20.0,-47.0 -20.1,-47.0 -20.1,-47.1 -20.0,-47.0'
            '</c:polygon></c:area></c:info></c:alert>'
        )
        boundaries = {
            "inside": box(-47.08, -20.08, -47.02, -20.02),
            "edge": box(-47.0, -20.08, -46.95, -20.02),
            "outside": box(-46.8, -20.08, -46.7, -20.02),
        }
        names = {"inside": "Dentro", "edge": "Borda", "outside": "Fora"}
        cities, _expired, _circles = intersecting_municipalities(
            alert, boundaries, names, self.now
        )
        self.assertEqual(cities, {"Dentro", "Borda"})

    def test_minas_geocode_does_not_suppress_geometry_crossing_sp_municipality(self):
        alert = ET.fromstring(
            '<c:alert xmlns:c="urn:oasis:names:tc:emergency:cap:1.2"><c:info><c:area>'
            '<c:areaDesc>MINAS GERAIS/MG</c:areaDesc>'
            '<c:geocode><c:valueName>IBGE</c:valueName><c:value>3145000</c:value></c:geocode>'
            '<c:polygon>-20.0,-47.0 -20.1,-47.0 -20.1,-47.1 -20.0,-47.0</c:polygon>'
            '</c:area></c:info></c:alert>'
        )
        boundaries = {"3524105": box(-47.08, -20.08, -47.02, -20.02)}
        names = {"3524105": "Ituverava"}
        cities, _expired, _circles = intersecting_municipalities(
            alert, boundaries, names, self.now
        )
        self.assertEqual(cities, {"Ituverava"})

    def test_circle_intersection_finds_nearby_municipality(self):
        alert = ET.fromstring(
            f'<c:alert xmlns:c="{NS["c"]}"><c:info>'
            '<c:area><c:circle>-20.0,-47.0 10</c:circle></c:area>'
            '</c:info></c:alert>'
        )
        boundaries = {
            "near": box(-47.02, -20.02, -46.98, -19.98),
            "far": box(-47.5, -20.5, -47.4, -20.4),
        }
        names = {"near": "Próximo", "far": "Distante"}
        cities, _expired, circle_count = intersecting_municipalities(
            alert, boundaries, names, self.now
        )
        self.assertEqual(cities, {"Próximo"})
        self.assertEqual(circle_count, 1)

    def test_references_extract_sender_and_identifier(self):
        self.assertEqual(
            references_to_keys("sender@example,alert-123,2026-10-22T12:00:00Z"),
            {("sender@example", "alert-123")},
        )
        self.assertEqual(references_to_keys("bad-reference"), set())

    def test_alert_without_sent_timestamp_is_rejected(self):
        xml = """<feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:c="urn:oasis:names:tc:emergency:cap:1.2">
          <entry><title>Sem envio</title><content><c:alert>
            <c:identifier>missing-sent</c:identifier>
            <c:status>Actual</c:status><c:msgType>Alert</c:msgType>
            <c:info><c:area><c:polygon>-20,-47 -20.1,-47 -20.1,-47.1</c:polygon></c:area></c:info>
          </c:alert></content></entry>
        </feed>"""
        root = ET.fromstring(xml)
        active, _expired = active_alert_entries(root, self.now)
        self.assertEqual(active, [])

    def test_future_cancel_does_not_suppress_current_alert(self):
        xml = """<feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:c="urn:oasis:names:tc:emergency:cap:1.2">
          <entry><title>Original</title><content><c:alert>
            <c:sender>sender@example</c:sender><c:identifier>old-id</c:identifier>
            <c:sent>2026-10-22T17:00:00Z</c:sent><c:status>Actual</c:status>
            <c:msgType>Alert</c:msgType>
            <c:info><c:expires>2026-10-22T19:00:00Z</c:expires></c:info>
          </c:alert></content></entry>
          <entry><title>Future cancel</title><content><c:alert>
            <c:sender>sender@example</c:sender><c:identifier>cancel-id</c:identifier>
            <c:sent>2026-10-22T19:00:00Z</c:sent><c:status>Actual</c:status>
            <c:msgType>Cancel</c:msgType>
            <c:references>sender@example,old-id,2026-10-22T17:00:00Z</c:references>
          </c:alert></content></entry>
        </feed>"""
        root = ET.fromstring(xml)
        active, _expired = active_alert_entries(root, self.now)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0][1].findtext("c:identifier", namespaces=NS), "old-id")

    def test_reference_from_another_sender_does_not_suppress_alert(self):
        xml = """<feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:c="urn:oasis:names:tc:emergency:cap:1.2">
          <entry><title>Original</title><content><c:alert>
            <c:sender>sender-a@example</c:sender><c:identifier>same-id</c:identifier>
            <c:sent>2026-10-22T17:00:00Z</c:sent><c:status>Actual</c:status>
            <c:msgType>Alert</c:msgType>
            <c:info><c:expires>2026-10-22T19:00:00Z</c:expires></c:info>
          </c:alert></content></entry>
          <entry><title>Unrelated cancel</title><content><c:alert>
            <c:sender>sender-b@example</c:sender><c:identifier>cancel-id</c:identifier>
            <c:sent>2026-10-22T17:30:00Z</c:sent><c:status>Actual</c:status>
            <c:msgType>Cancel</c:msgType>
            <c:references>sender-b@example,same-id,2026-10-22T17:00:00Z</c:references>
          </c:alert></content></entry>
        </feed>"""
        root = ET.fromstring(xml)
        active, _expired = active_alert_entries(root, self.now)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0][1].findtext("c:identifier", namespaces=NS), "same-id")

    def test_update_suppresses_original_and_returns_update(self):
        xml = """<feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:c="urn:oasis:names:tc:emergency:cap:1.2">
          <entry><title>Original</title><content><c:alert>
            <c:sender>sender@example</c:sender><c:identifier>old-id</c:identifier>
            <c:sent>2026-10-22T17:00:00Z</c:sent><c:status>Actual</c:status>
            <c:msgType>Alert</c:msgType>
            <c:info><c:expires>2026-10-22T19:00:00Z</c:expires></c:info>
          </c:alert></content></entry>
          <entry><title>Atualização</title><content><c:alert>
            <c:sender>sender@example</c:sender><c:identifier>update-id</c:identifier>
            <c:sent>2026-10-22T17:30:00Z</c:sent><c:status>Actual</c:status>
            <c:msgType>Update</c:msgType>
            <c:references>sender@example,old-id,2026-10-22T17:00:00Z</c:references>
            <c:info><c:expires>2026-10-22T20:00:00Z</c:expires></c:info>
          </c:alert></content></entry>
        </feed>"""
        root = ET.fromstring(xml)
        active, _expired = active_alert_entries(root, self.now)
        self.assertEqual(len(active), 1)
        self.assertEqual(
            active[0][1].findtext("c:identifier", namespaces=NS),
            "update-id",
        )

    def test_cancel_suppresses_referenced_alert(self):
        xml = """<feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:c="urn:oasis:names:tc:emergency:cap:1.2">
          <entry><title>Original</title><content><c:alert>
            <c:sender>sender@example</c:sender><c:identifier>old-id</c:identifier><c:sent>2026-10-22T17:00:00Z</c:sent>
            <c:status>Actual</c:status><c:msgType>Alert</c:msgType>
            <c:info><c:area><c:polygon>-20,-47 -20.1,-47 -20.1,-47.1</c:polygon></c:area></c:info>
          </c:alert></content></entry>
          <entry><title>Cancelamento</title><content><c:alert>
            <c:sender>sender@example</c:sender><c:identifier>cancel-id</c:identifier><c:sent>2026-10-22T17:30:00Z</c:sent>
            <c:status>Actual</c:status><c:msgType>Cancel</c:msgType>
            <c:references>sender@example,old-id,2026-10-22T17:00:00Z</c:references>
          </c:alert></content></entry>
        </feed>"""
        root = ET.fromstring(xml)
        active, _expired = active_alert_entries(root, self.now)
        self.assertEqual(active, [])


    def test_ibge_geocode_matches_municipality_without_polygon_or_circle(self):
        alert = ET.fromstring(
            f'<c:alert xmlns:c="{NS["c"]}"><c:info><c:area>'
            '<c:areaDesc>Ituverava/SP</c:areaDesc>'
            '<c:geocode><c:valueName>IBGE</c:valueName>'
            '<c:value>3524105</c:value></c:geocode>'
            '</c:area></c:info></c:alert>'
        )
        boundaries = {"3524105": box(-47.8, -20.5, -47.7, -20.4)}
        names = {"3524105": "Ituverava"}
        cities, _expired, _circles = intersecting_municipalities(
            alert, boundaries, names, self.now
        )
        self.assertEqual(cities, {"Ituverava"})


if __name__ == "__main__":
    unittest.main()
