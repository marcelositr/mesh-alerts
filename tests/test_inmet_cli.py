import unittest
from unittest.mock import Mock, patch

from mesh_alerts.inmet_cli import main


class InmetCliChannelTests(unittest.TestCase):
    def run_with_transport(self, extra_args=()):
        interface = Mock()
        sender = Mock()
        with (
            patch("mesh_alerts.inmet_cli._transport_tcp", return_value=(interface, sender)) as transport,
            patch("mesh_alerts.inmet_cli.process_bulletin", return_value={"prepared": 0}) as process,
        ):
            result = main([
                "--period", "manha", "--send", "--tcp-host", "example.test", *extra_args
            ])
        return result, transport, process, interface

    def test_uses_configured_inmet_channel_without_radio_transmission(self):
        result, transport, process, interface = self.run_with_transport()

        self.assertEqual(result, 0)
        self.assertEqual(transport.call_args.args, ("example.test", 4403, 1, "^all"))
        self.assertFalse(process.call_args.kwargs["simulate"])
        interface.close.assert_called_once_with()

    def test_explicit_channel_index_overrides_configuration(self):
        result, transport, _process, _interface = self.run_with_transport(
            ("--channel-index", "4")
        )

        self.assertEqual(result, 0)
        self.assertEqual(transport.call_args.args, ("example.test", 4403, 4, "^all"))

    def test_invalid_channel_index_is_rejected_before_transport_creation(self):
        with patch("mesh_alerts.inmet_cli._transport_tcp") as transport:
            with self.assertRaises(SystemExit):
                main([
                    "--period", "manha", "--send", "--tcp-host", "example.test",
                    "--channel-index", "8",
                ])

        transport.assert_not_called()

    def test_simulation_does_not_create_transport(self):
        with (
            patch("mesh_alerts.inmet_cli._transport_tcp") as transport,
            patch("mesh_alerts.inmet_cli.process_bulletin", return_value={"prepared": 0}) as process,
        ):
            result = main(["--period", "manha"])

        self.assertEqual(result, 0)
        transport.assert_not_called()
        self.assertTrue(process.call_args.kwargs["simulate"])
        self.assertIsNone(process.call_args.kwargs["transport_sender"])

    def test_status_command_is_read_only_and_does_not_load_config_or_open_radio(self):
        records = [{
            "local_date": "2026-10-22",
            "period": "manha",
            "municipality_code": "3503000",
            "status": "failed",
            "updated_at": "2026-10-22T06:00:00-03:00",
            "detail": "transport unavailable",
        }]
        with (
            patch("mesh_alerts.inmet_cli.list_bulletin_status", return_value=records) as status,
            patch("mesh_alerts.inmet_cli.load_configuration") as load_config,
            patch("mesh_alerts.inmet_cli._transport_tcp") as transport,
            patch("builtins.print") as output,
        ):
            result = main(["--status", "--database", "state.sqlite3"])

        self.assertEqual(result, 0)
        status.assert_called_once_with("state.sqlite3")
        load_config.assert_not_called()
        transport.assert_not_called()
        self.assertIn('"failed"', output.call_args.args[0])
        self.assertIn("transport unavailable", output.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
