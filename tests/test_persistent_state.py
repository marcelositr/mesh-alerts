import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from mesh_alerts_state import AlertStateStore


NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
SENT = "2026-10-22T11:00:00Z"
EXPIRES = "2026-10-22T15:00:00Z"


class AlertStateStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "state" / "alerts.sqlite3"
        self.store = AlertStateStore(self.db_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def add_alert(self, **overrides):
        args = {
            "sender": "civil.defesa@example.gov.br",
            "identifier": "alert-001",
            "sent": SENT,
            "msg_type": "Alert",
            "payload": {"headline": "Alerta de teste"},
            "matches_region": True,
            "message_text": "ALERTA: teste",
            "destination": "^all",
            "now": NOW,
        }
        args.update(overrides)
        return self.store.process_cap_alert(**args)

    def test_alert_is_queued_once_even_after_store_reopens(self):
        self.assertEqual(self.add_alert(), "queued")
        self.assertEqual(self.add_alert(), "duplicate")
        reopened = AlertStateStore(self.db_path)
        self.assertEqual(len(reopened.list_outbox(status="pending")), 1)
        self.assertEqual(reopened.get_alert("civil.defesa@example.gov.br", "alert-001")["active"], 1)

    def test_same_identity_with_different_content_is_conflict_not_new_message(self):
        self.assertEqual(self.add_alert(), "queued")
        self.assertEqual(
            self.add_alert(payload={"headline": "conteúdo alterado"}),
            "conflict",
        )
        self.assertEqual(len(self.store.list_outbox()), 1)

    def test_update_deactivates_referenced_alert_even_outside_region(self):
        self.assertEqual(self.add_alert(), "queued")
        result = self.add_alert(
            identifier="alert-002",
            msg_type="Update",
            references=[("civil.defesa@example.gov.br", "alert-001")],
            matches_region=False,
            message_text="não deve ser enviado",
        )
        self.assertEqual(result, "recorded")
        old = self.store.get_alert("civil.defesa@example.gov.br", "alert-001")
        self.assertEqual(old["active"], 0)
        self.assertEqual(old["lifecycle_state"], "superseded")
        self.assertEqual(len(self.store.list_outbox()), 1)

    def test_cancel_of_local_alert_queues_cancellation_and_deactivates_old(self):
        self.assertEqual(self.add_alert(), "queued")
        result = self.add_alert(
            identifier="cancel-001",
            msg_type="Cancel",
            references=[("civil.defesa@example.gov.br", "alert-001")],
            matches_region=False,
            message_text="CANCELADO: alerta anterior",
        )
        self.assertEqual(result, "queued")
        old = self.store.get_alert("civil.defesa@example.gov.br", "alert-001")
        self.assertEqual(old["active"], 0)
        self.assertEqual(old["lifecycle_state"], "cancelled")
        self.assertEqual([item["action"] for item in self.store.list_outbox()], ["alert", "cancel"])

    def test_expired_alert_is_recorded_but_not_queued(self):
        result = self.add_alert(expires_at="2026-10-22T11:59:00Z")
        self.assertEqual(result, "expired")
        self.assertEqual(self.store.list_outbox(), [])
        self.assertEqual(self.store.get_alert("civil.defesa@example.gov.br", "alert-001")["active"], 0)

    def test_pending_alert_is_not_sent_after_expiry(self):
        self.assertEqual(self.add_alert(), "queued")
        self.assertEqual(
            self.store.invalidate_stale_pending(now=datetime(2026, 10, 22, 16, 0, tzinfo=timezone.utc)),
            1,
        )
        self.assertEqual(self.store.list_outbox(status="pending"), [])
        self.assertEqual(self.store.list_outbox(status="failed")[0]["action"], "alert")

    def test_update_invalidates_old_pending_alert(self):
        self.assertEqual(self.add_alert(), "queued")
        self.add_alert(
            identifier="alert-002",
            msg_type="Update",
            references=[("civil.defesa@example.gov.br", "alert-001")],
            matches_region=False,
            message_text=None,
        )
        self.assertEqual(self.store.list_outbox(status="pending"), [])
        self.assertEqual(self.store.list_outbox(status="failed")[0]["identifier"], "alert-001")

    def test_pending_send_survives_restart_and_is_claimed_once(self):
        self.add_alert()
        first = self.store.claim_next()
        self.assertEqual(first["status"], "sending")
        self.assertEqual(first["attempts"], 1)
        self.assertIsNone(self.store.claim_next())
        self.store.mark_sent(first["id"])
        reopened = AlertStateStore(self.db_path)
        self.assertEqual(reopened.list_outbox(status="sent")[0]["attempts"], 1)

    def test_interrupted_send_becomes_uncertain_and_is_not_retried_automatically(self):
        self.add_alert()
        claimed = self.store.claim_next()
        self.assertEqual(self.store.recover_interrupted_sends(), 1)
        self.assertEqual(self.store.list_outbox(status="uncertain")[0]["id"], claimed["id"])
        self.assertIsNone(self.store.claim_next())

    def test_uncertain_send_requires_explicit_retry_decision(self):
        self.add_alert()
        claimed = self.store.claim_next()
        self.store.recover_interrupted_sends()
        self.store.resolve_uncertain(claimed["id"], resolution="retry")
        retried = self.store.claim_next()
        self.assertEqual(retried["id"], claimed["id"])
        self.assertEqual(retried["attempts"], 2)

    def test_transport_exception_after_dispatch_becomes_uncertain(self):
        self.add_alert()
        claimed = self.store.claim_next()
        self.store.mark_uncertain(claimed["id"], "timeout after dispatch")
        item = self.store.list_outbox(status="uncertain")[0]
        self.assertEqual(item["id"], claimed["id"])
        self.assertEqual(item["last_error"], "timeout after dispatch")
        self.assertIsNone(self.store.claim_next())

    def test_in_memory_database_is_rejected(self):
        with self.assertRaises(ValueError):
            AlertStateStore(":memory:")

    def test_retryable_transport_failure_returns_to_pending(self):
        self.add_alert()
        claimed = self.store.claim_next()
        self.store.mark_failed(claimed["id"], "connection refused", retryable=True)
        self.assertEqual(self.store.list_outbox(status="pending")[0]["id"], claimed["id"])

    def test_invalid_or_naive_timestamps_are_rejected(self):
        with self.assertRaises(ValueError):
            self.add_alert(sent="not-a-date")
        with self.assertRaises(ValueError):
            self.add_alert(sent="2026-10-22T11:00:00")

    def test_different_senders_can_use_same_identifier(self):
        self.assertEqual(self.add_alert(), "queued")
        self.assertEqual(
            self.add_alert(sender="another.sender@example.gov.br"),
            "queued",
        )
        self.assertEqual(len(self.store.list_outbox()), 2)


if __name__ == "__main__":
    unittest.main()
