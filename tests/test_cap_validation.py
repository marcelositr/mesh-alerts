import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from mesh_alerts_cap import (
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


if __name__ == "__main__":
    unittest.main()
