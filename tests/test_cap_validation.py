import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from mesh_alerts_cap import (
    active_alert_entries,
    circle_from_cap,
    info_is_current,
    parse_cap_datetime,
    polygon_from_cap,
    references_to_identifiers,
)

NS = {"c": "urn:oasis:names:tc:emergency:cap:1.2"}


class CapValidationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)

    def info(self, fields):
        parts = "".join(f"<c:{key}>{value}</c:{key}>" for key, value in fields.items())
        return ET.fromstring(f'<c:info xmlns:c="{NS["c"]}">{parts}</c:info>')

    def test_parse_cap_timestamp_normalizes_utc(self):
        parsed = parse_cap_datetime("2026-10-22T15:00:00-03:00")
        self.assertEqual(parsed, datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc))

    def test_expired_info_is_not_current(self):
        info = self.info({"expires": "2026-10-22T17:59:59Z"})
        self.assertEqual(info_is_current(info, self.now), (False, "expires expirado"))

    def test_future_onset_is_not_current(self):
        info = self.info({"onset": "2026-10-22T18:01:00Z"})
        self.assertEqual(info_is_current(info, self.now), (False, "onset futuro"))

    def test_malformed_expiry_fails_closed(self):
        info = self.info({"expires": "not-a-time"})
        self.assertEqual(info_is_current(info, self.now), (False, "timestamp expires inválido"))

    def test_polygon_parses_lat_lon_and_rejects_bad_input(self):
        polygon = polygon_from_cap("-20.0,-47.0 -20.1,-47.0 -20.1,-47.1")
        self.assertIsNotNone(polygon)
        self.assertTrue(polygon.is_valid)
        self.assertIsNone(polygon_from_cap("bad input"))

    def test_circle_radius_is_converted_to_polygon(self):
        circle = circle_from_cap("-20.0,-47.0 10")
        self.assertIsNotNone(circle)
        self.assertTrue(circle.is_valid)
        self.assertGreater(circle.area, 0)

    def test_references_extract_identifier(self):
        self.assertEqual(
            references_to_identifiers("sender@example,alert-123,2026-10-22T12:00:00Z"),
            {"alert-123"},
        )

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

    def test_cancel_suppresses_referenced_alert(self):
        xml = """<feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:c="urn:oasis:names:tc:emergency:cap:1.2">
          <entry><title>Original</title><content><c:alert>
            <c:identifier>old-id</c:identifier><c:sent>2026-10-22T17:00:00Z</c:sent>
            <c:status>Actual</c:status><c:msgType>Alert</c:msgType>
            <c:info><c:area><c:polygon>-20,-47 -20.1,-47 -20.1,-47.1</c:polygon></c:area></c:info>
          </c:alert></content></entry>
          <entry><title>Cancelamento</title><content><c:alert>
            <c:identifier>cancel-id</c:identifier><c:sent>2026-10-22T17:30:00Z</c:sent>
            <c:status>Actual</c:status><c:msgType>Cancel</c:msgType>
            <c:references>sender@example,old-id,2026-10-22T17:00:00Z</c:references>
          </c:alert></content></entry>
        </feed>"""
        root = ET.fromstring(xml)
        active, _expired = active_alert_entries(root, self.now)
        self.assertEqual(active, [])


if __name__ == "__main__":
    unittest.main()
