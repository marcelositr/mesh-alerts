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


if __name__ == "__main__":
    unittest.main()
