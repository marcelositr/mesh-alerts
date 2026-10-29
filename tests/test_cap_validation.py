import json
import os
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from shapely.geometry import box

from mesh_alerts.region import (
    carregar_feed as load_cap_feed,
    carregar_limites as load_municipal_boundaries,
    carregar_limites_com_cache as load_municipal_boundaries_with_cache,
    carregar_limites_de_arquivo as load_municipal_boundaries_from_file,
    avisos_metadados_geograficos as diagnose_geographic_metadata,
    avisos_poligonos_invalidos as diagnose_invalid_polygons,
    baixar_com_retentativas,
    _decompress_gzip_limited,
)
from mesh_alerts.boundaries import MUNICIPALITIES, load_municipal_boundaries_from_geojson

from mesh_alerts.cap import (
    get_active_alert_entries,
    parse_cap_circle,
    is_info_current,
    find_intersecting_municipalities,
    parse_cap_timestamp,
    parse_cap_polygon,
    parse_reference_keys,
)

XML_NAMESPACES = {"c": "urn:oasis:names:tc:emergency:cap:1.2"}


class CapValidationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)

    def test_http_download_rejects_response_over_configured_limit(self):
        with patch("mesh_alerts.region.MAX_HTTP_RESPONSE_BYTES", 10):
            with patch("mesh_alerts.region.open_http_url") as open_url:
                response = open_url.return_value.__enter__.return_value
                response.read.return_value = b"x" * 11
                with self.assertRaisesRegex(ValueError, "excede o limite"):
                    baixar_com_retentativas(
                        object(), "feed de teste", tentativas=1, timeout=1
                    )
                response.read.assert_called_once_with(11)

    def test_gzip_decompression_rejects_expansion_over_configured_limit(self):
        import gzip

        compressed = gzip.compress(b"x" * 100)
        with patch("mesh_alerts.region.MAX_DECOMPRESSED_BYTES", 10):
            with self.assertRaisesRegex(ValueError, "excede o limite"):
                _decompress_gzip_limited(compressed)

    def info(self, fields):
        parts = "".join(f"<c:{key}>{value}</c:{key}>" for key, value in fields.items())
        return ET.fromstring(f'<c:info xmlns:c="{XML_NAMESPACES["c"]}">{parts}</c:info>')

    def test_local_cap_feed_file_is_parsed(self):
        xml = '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.xml"
            path.write_text(xml, encoding="utf-8")
            root = load_cap_feed(path)
        self.assertEqual(root.tag, "{http://www.w3.org/2005/Atom}feed")

    def test_warns_when_cap_polygon_is_invalid_before_repair(self):
        alert = ET.fromstring(
            '<c:alert xmlns:c="urn:oasis:names:tc:emergency:cap:1.2"><c:info><c:area>'
            '<c:polygon>-20,-47 -20.1,-47.1 -20,-47.1 -20.1,-47 -20,-47</c:polygon>'
            '</c:area></c:info></c:alert>'
        )
        warnings = diagnose_invalid_polygons(alert)
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
        warnings = diagnose_geographic_metadata(alert)
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
        self.assertEqual(diagnose_geographic_metadata(alert), [])


    def test_live_ibge_loader_requests_each_state_once(self):
        geometry = {
            "type": "Polygon",
            "coordinates": [[
                [-47.1, -20.1], [-47.0, -20.1],
                [-47.0, -20.0], [-47.1, -20.0],
                [-47.1, -20.1],
            ]],
        }
        municipalities = {code: f"Municipality {code}" for code in MUNICIPALITIES}
        payload = json.dumps({
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"codarea": code},
                    "geometry": geometry,
                }
                for code in municipalities
            ],
        }).encode("utf-8")

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "ibge-boundaries.geojson"
            with patch(
                "mesh_alerts.region.baixar_com_retentativas",
                return_value=payload,
            ) as download:
                boundaries = load_municipal_boundaries(
                    municipalities,
                    "https://servicodados.ibge.gov.br/api/v3/malhas/municipios",
                    timeout=5,
                    tentativas=1,
                    cache_path=cache_path,
                )
            cached_boundaries = load_municipal_boundaries_from_file(
                cache_path, municipalities
            )
            self.assertEqual(set(cached_boundaries), set(municipalities))

        self.assertEqual(set(boundaries), set(municipalities))
        self.assertEqual(download.call_count, 1)
        request = download.call_args.args[0]
        self.assertIn("/malhas/estados/35?", request.full_url)
        self.assertIn("intrarregiao=municipio", request.full_url)
        self.assertIn("formato=application/vnd.geo+json", request.full_url)

    def test_validated_boundary_cache_is_used_when_ibge_is_unavailable(self):
        fixture = Path(__file__).parent / "fixtures" / "ibge-sp-municipios-api.geojson"
        municipalities = {code: f"Municipality {code}" for code in MUNICIPALITIES}
        with patch(
            "mesh_alerts.region.carregar_limites",
            side_effect=RuntimeError("IBGE timeout"),
        ):
            boundaries, used_cache, cache_reason = load_municipal_boundaries_with_cache(
                municipalities,
                "https://servicodados.ibge.gov.br/api/v3/malhas/municipios",
                fixture,
                timeout=1,
                tentativas=1,
            )

        self.assertTrue(used_cache)
        self.assertIn("IBGE timeout", cache_reason)
        self.assertEqual(set(boundaries), set(municipalities))
        self.assertTrue(all(boundary.is_valid for boundary in boundaries.values()))

    def test_expired_boundary_cache_is_rejected_when_ibge_is_unavailable(self):
        fixture = Path(__file__).parent / "fixtures" / "ibge-sp-municipios-api.geojson"
        municipalities = {code: f"Municipality {code}" for code in MUNICIPALITIES}
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "ibge-boundaries.geojson"
            cache_path.write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
            expired_timestamp = time.time() - 400 * 24 * 60 * 60
            os.utime(cache_path, (expired_timestamp, expired_timestamp))
            with patch(
                "mesh_alerts.region.carregar_limites",
                side_effect=RuntimeError("IBGE timeout"),
            ):
                with self.assertRaisesRegex(RuntimeError, "idade máxima de 365 dias"):
                    load_municipal_boundaries_with_cache(
                        municipalities,
                        "https://servicodados.ibge.gov.br/api/v3/malhas/municipios",
                        cache_path,
                        timeout=1,
                        tentativas=1,
                    )

    def test_local_ibge_boundary_fixture_loads_without_network_access(self):
        fixture = Path(__file__).parent / "fixtures" / "ibge-sp-municipios-api.geojson"
        boundaries = load_municipal_boundaries_from_file(
            fixture, {code: f"Municipality {code}" for code in MUNICIPALITIES}
        )
        self.assertEqual(set(boundaries), set(MUNICIPALITIES))
        self.assertTrue(all(boundary.is_valid for boundary in boundaries.values()))

    def test_ibge_reference_fixture_contains_exact_six_municipalities(self):
        fixture = Path(__file__).parent / "fixtures" / "ibge-sp-municipios-api.geojson"
        geojson = json.loads(fixture.read_text(encoding="utf-8"))
        boundaries = load_municipal_boundaries_from_geojson(geojson)
        self.assertEqual(set(boundaries), set(MUNICIPALITIES))
        self.assertEqual(
            sum(1 for feature in geojson["features"]
                if str((feature.get("properties") or {}).get("codarea")) == "3503000"),
            1,
        )
        self.assertTrue(all(boundary.is_valid for boundary in boundaries.values()))

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
                for code in MUNICIPALITIES
            ],
        }
        boundaries = load_municipal_boundaries_from_geojson(geojson)
        self.assertEqual(set(boundaries), set(MUNICIPALITIES))
        self.assertTrue(all(boundary.is_valid for boundary in boundaries.values()))

    def test_ibge_geojson_rejects_non_feature_collection(self):
        with self.assertRaisesRegex(RuntimeError, "FeatureCollection"):
            load_municipal_boundaries_from_geojson({"type": "Feature", "features": []})

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
            load_municipal_boundaries_from_geojson(geojson)

    def test_ibge_geojson_rejects_incomplete_municipality_set(self):
        geojson = {"type": "FeatureCollection", "features": []}
        with self.assertRaisesRegex(RuntimeError, "Malha municipal incompleta"):
            load_municipal_boundaries_from_geojson(geojson)

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
            load_municipal_boundaries_from_geojson(geojson)

    def test_parse_cap_timestamp_normalizes_utc(self):
        parsed = parse_cap_timestamp("2026-10-22T15:00:00-03:00")
        self.assertEqual(parsed, datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc))

    def test_expired_info_is_not_current(self):
        info = self.info({"expires": "2026-10-22T17:59:59Z"})
        self.assertEqual(is_info_current(info, self.now), (False, "expires expirado"))

    def test_future_onset_does_not_deactivate_current_warning(self):
        info = self.info({
            "effective": "2026-10-22T17:00:00Z",
            "onset": "2026-10-22T18:01:00Z",
            "expires": "2026-10-22T19:00:00Z",
        })
        self.assertEqual(is_info_current(info, self.now), (True, None))

    def test_future_effective_is_not_current(self):
        info = self.info({"effective": "2026-10-22T18:01:00Z"})
        self.assertEqual(is_info_current(info, self.now), (False, "effective futuro"))

    def test_malformed_onset_fails_closed(self):
        info = self.info({"onset": "not-a-time"})
        self.assertEqual(is_info_current(info, self.now), (False, "timestamp onset inválido"))

    def test_malformed_expiry_fails_closed(self):
        info = self.info({"expires": "not-a-time"})
        self.assertEqual(is_info_current(info, self.now), (False, "timestamp expires inválido"))

    def test_polygon_parses_lat_lon_and_rejects_bad_input(self):
        polygon = parse_cap_polygon(
            "-20.0,-47.0 -20.1,-47.0 -20.1,-47.1 -20.0,-47.0"
        )
        self.assertIsNotNone(polygon)
        self.assertTrue(polygon.is_valid)
        self.assertIsNone(parse_cap_polygon("bad input"))
        self.assertIsNone(
            parse_cap_polygon("-20.0,-47.0 -20.1,-47.0 -20.1,-47.1")
        )
        self.assertIsNone(
            parse_cap_polygon(
                "-20.0,-47.0 -20.1,-47.0 999,-47.1 -20.0,-47.0"
            )
        )

    def test_circle_radius_is_converted_to_polygon(self):
        circle = parse_cap_circle("-20.0,-47.0 10")
        self.assertIsNotNone(circle)
        self.assertTrue(circle.is_valid)
        self.assertGreater(circle.area, 0)

    def test_polygon_intersection_includes_boundary_touch(self):
        alert = ET.fromstring(
            f'<c:alert xmlns:c="{XML_NAMESPACES["c"]}"><c:info>'
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
        cities, _expired, _circles = find_intersecting_municipalities(
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
        cities, _expired, _circles = find_intersecting_municipalities(
            alert, boundaries, names, self.now
        )
        self.assertEqual(cities, {"Ituverava"})

    def test_circle_intersection_finds_nearby_municipality(self):
        alert = ET.fromstring(
            f'<c:alert xmlns:c="{XML_NAMESPACES["c"]}"><c:info>'
            '<c:area><c:circle>-20.0,-47.0 10</c:circle></c:area>'
            '</c:info></c:alert>'
        )
        boundaries = {
            "near": box(-47.02, -20.02, -46.98, -19.98),
            "far": box(-47.5, -20.5, -47.4, -20.4),
        }
        names = {"near": "Próximo", "far": "Distante"}
        cities, _expired, circle_count = find_intersecting_municipalities(
            alert, boundaries, names, self.now
        )
        self.assertEqual(cities, {"Próximo"})
        self.assertEqual(circle_count, 1)

    def test_references_extract_sender_and_identifier(self):
        self.assertEqual(
            parse_reference_keys("sender@example,alert-123,2026-10-22T12:00:00Z"),
            {("sender@example", "alert-123")},
        )
        self.assertEqual(parse_reference_keys("bad-reference"), set())

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
        active, _expired = get_active_alert_entries(root, self.now)
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
        active, _expired = get_active_alert_entries(root, self.now)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0][1].findtext("c:identifier", namespaces=XML_NAMESPACES), "old-id")

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
        active, _expired = get_active_alert_entries(root, self.now)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0][1].findtext("c:identifier", namespaces=XML_NAMESPACES), "same-id")

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
        active, _expired = get_active_alert_entries(root, self.now)
        self.assertEqual(len(active), 1)
        self.assertEqual(
            active[0][1].findtext("c:identifier", namespaces=XML_NAMESPACES),
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
        active, _expired = get_active_alert_entries(root, self.now)
        self.assertEqual(active, [])


    def test_ibge_geocode_matches_municipality_without_polygon_or_circle(self):
        alert = ET.fromstring(
            f'<c:alert xmlns:c="{XML_NAMESPACES["c"]}"><c:info><c:area>'
            '<c:areaDesc>Ituverava/SP</c:areaDesc>'
            '<c:geocode><c:valueName>IBGE</c:valueName>'
            '<c:value>3524105</c:value></c:geocode>'
            '</c:area></c:info></c:alert>'
        )
        boundaries = {"3524105": box(-47.8, -20.5, -47.7, -20.4)}
        names = {"3524105": "Ituverava"}
        cities, _expired, _circles = find_intersecting_municipalities(
            alert, boundaries, names, self.now
        )
        self.assertEqual(cities, {"Ituverava"})


if __name__ == "__main__":
    unittest.main()
