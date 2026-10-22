import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from mesh_alerts_service import _safe_filename, process_feed
from mesh_alerts_state import AlertStateStore


NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:cap="urn:oasis:names:tc:emergency:cap:1.2">
  <entry>
    <title>Alerta regional de teste</title>
    <cap:alert>
      <cap:identifier>service-alert-001</cap:identifier>
      <cap:sender>civil.defesa@example.gov.br</cap:sender>
      <cap:sent>2026-10-22T11:00:00Z</cap:sent>
      <cap:status>Actual</cap:status>
      <cap:msgType>Alert</cap:msgType>
      <cap:info>
        <cap:headline>Chuva forte</cap:headline>
        <cap:description>Possibilidade de alagamentos.</cap:description>
        <cap:instruction>Evite áreas alagadas.</cap:instruction>
        <cap:effective>2026-10-22T10:00:00Z</cap:effective>
        <cap:expires>2026-10-22T15:00:00Z</cap:expires>
      </cap:info>
    </cap:alert>
  </entry>
</feed>
"""


class MeshAlertsServiceTests(unittest.TestCase):
    def test_filename_does_not_expose_untrusted_cap_identifier(self):
        name = _safe_filename("../../unexpected/path", "id/with/slashes")
        self.assertTrue(name.endswith(".txt"))
        self.assertNotIn("/", name)
        self.assertNotIn("..", name)

    def test_reprocessing_feed_does_not_enqueue_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = ET.fromstring(FEED)
            store = AlertStateStore(Path(directory) / "state.sqlite3")
            output_dir = Path(directory) / "output"
            with patch(
                "mesh_alerts_service.intersecting_municipalities",
                return_value=({"Ituverava"}, 0, 0),
            ):
                first = process_feed(root, store, {}, NOW, output_dir, "^all")
                second = process_feed(root, store, {}, NOW, output_dir, "^all")

            self.assertEqual(first["queued"], 1)
            self.assertEqual(second["duplicate"], 1)
            self.assertEqual(len(store.list_outbox()), 1)
            txt_files = list(output_dir.glob("*.txt"))
            self.assertEqual(len(txt_files), 1)
            self.assertIn("Chuva forte", txt_files[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
