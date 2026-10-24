import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from mesh_alerts.service import _safe_filename, process_feed
from mesh_alerts.state import AlertStateStore


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
        <cap:headline>Defesa Civil: Chuva forte</cap:headline>
        <cap:event>CHUVA FORTE</cap:event>
        <cap:severity>Severe</cap:severity>
        <cap:urgency>Expected</cap:urgency>
        <cap:certainty>Likely</cap:certainty>
        <cap:senderName>Defesa Civil Estadual</cap:senderName>
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

    def test_cancel_updates_state_without_broadcasting_cancel_message(self):
        cancel_feed = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:cap="urn:oasis:names:tc:emergency:cap:1.2">
  <entry>
    <title>Cancelamento do alerta regional de teste</title>
    <cap:alert>
      <cap:identifier>service-cancel-001</cap:identifier>
      <cap:sender>civil.defesa@example.gov.br</cap:sender>
      <cap:sent>2026-10-22T12:00:00Z</cap:sent>
      <cap:status>Actual</cap:status>
      <cap:msgType>Cancel</cap:msgType>
      <cap:references>civil.defesa@example.gov.br,service-alert-001,2026-10-22T11:00:00Z</cap:references>
    </cap:alert>
  </entry>
</feed>
"""
        with tempfile.TemporaryDirectory() as directory:
            alert_root = ET.fromstring(FEED)
            cancel_root = ET.fromstring(cancel_feed)
            store = AlertStateStore(Path(directory) / "state.sqlite3")
            output_dir = Path(directory) / "output"
            with patch(
                "mesh_alerts.service.intersecting_municipalities",
                return_value=({"Ituverava"}, 0, 0),
            ):
                alert_counts = process_feed(
                    alert_root, store, {}, NOW, output_dir, "^all", region_uf="SP"
                )
                cancel_counts = process_feed(
                    cancel_root, store, {}, NOW, output_dir, "^all", region_uf="SP"
                )

            self.assertEqual(alert_counts["queued"], 1)
            self.assertEqual(cancel_counts["cancelled"], 1)
            previous = store.get_alert("civil.defesa@example.gov.br", "service-alert-001")
            cancellation = store.get_alert("civil.defesa@example.gov.br", "service-cancel-001")
            self.assertEqual(previous["lifecycle_state"], "cancelled")
            self.assertEqual(cancellation["lifecycle_state"], "cancelled")
            outbox = store.list_outbox()
            self.assertEqual(len(outbox), 4)
            self.assertTrue(all(item["action"] == "alert" for item in outbox))
            self.assertTrue(all(item["status"] == "failed" for item in outbox))
            self.assertEqual(len(list(output_dir.glob("*.txt"))), 4)
            self.assertFalse(any("CANCELADO" in path.read_text(encoding="utf-8") for path in output_dir.glob("*.txt")))

    def test_reprocessing_feed_does_not_enqueue_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = ET.fromstring(FEED)
            store = AlertStateStore(Path(directory) / "state.sqlite3")
            output_dir = Path(directory) / "output"
            with patch(
                "mesh_alerts.service.intersecting_municipalities",
                return_value=({"Ituverava"}, 0, 0),
            ):
                first = process_feed(root, store, {}, NOW, output_dir, "^all", region_uf="SP")
                second = process_feed(root, store, {}, NOW, output_dir, "^all", region_uf="SP")

            self.assertEqual(first["queued"], 1)
            self.assertEqual(second["duplicate"], 1)
            queued = store.list_outbox()
            self.assertEqual(len(queued), 4)
            self.assertEqual(
                [item["message_text"] for item in queued],
                [
                    "MESH ALERTS | ITUVERAVA/SP",
                    "Chuva forte",
                    "Severe / Expected / Likely",
                    "Orientação: Evite áreas alagadas.",
                ],
            )
            txt_files = sorted(output_dir.glob("*.txt"))
            self.assertEqual(len(txt_files), 4)
            self.assertEqual(
                [path.read_text(encoding="utf-8").strip() for path in txt_files],
                [item["message_text"] for item in queued],
            )
            self.assertNotIn("Defesa Civil:", "\n".join(item["message_text"] for item in queued))
            payload = store.get_alert("civil.defesa@example.gov.br", "service-alert-001")
            import json
            saved_payload = json.loads(payload["payload_json"])
            self.assertEqual(saved_payload["severity"], "Severe")
            self.assertEqual(saved_payload["urgency"], "Expected")
            self.assertEqual(saved_payload["certainty"], "Likely")
            self.assertEqual(saved_payload["senderName"], "Defesa Civil Estadual")


if __name__ == "__main__":
    unittest.main()
