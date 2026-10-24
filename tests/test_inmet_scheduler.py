import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from mesh_alerts.inmet import ForecastUnavailableError
from mesh_alerts.inmet_scheduler import (
    process_bulletin,
    get_due_schedule_slot,
    get_due_period,
)


class InmetSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "state" / "test.sqlite3"
        self.config = {
            "inmet": {
                "fuso_horario": "America/Sao_Paulo",
                "horarios": {
                    "06:00": "manha",
                    "12:00": "tarde",
                    "18:00": "noite",
                },
                "intervalo_entre_mensagens_segundos": 3,
                "intervalo_entre_municipios_segundos": 30,
                "janela_recuperacao_minutos": 30,
            },
            "municipios": [
                {"nome": "Cidade A", "codigo_ibge": "3503000", "uf": "SP"},
                {"nome": "Cidade B", "codigo_ibge": "3508207", "uf": "SP"},
            ],
        }
        self.now = datetime(2026, 10, 22, 6, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))

    def tearDown(self):
        self.temp.cleanup()

    def builder(self, municipality, period, now):
        return (
            f'MESH ALERTS | {municipality["nome"].upper()}/SP | {period.upper()}\nResumo',
            "Temperatura: 20°C / 10°C\nUmidade: 80% / 40%\nVento: fraco (N)",
        )

    def test_schedule_recovers_only_inside_configured_window(self):
        self.assertEqual(get_due_period(self.config, self.now), "manha")
        self.assertEqual(
            get_due_schedule_slot(self.config, self.now.replace(minute=1)),
            ("06:00", "manha"),
        )
        self.assertIsNone(get_due_period(self.config, self.now.replace(minute=31)))

    def test_simulation_never_calls_transport_or_persists_delivery(self):
        calls = []
        result = process_bulletin(
            self.config,
            self.database,
            "manha",
            now=self.now,
            transport_sender=calls.append,
            simulate=True,
            message_builder=self.builder,
            sleep=lambda _: self.fail("simulation must not sleep"),
        )
        self.assertEqual(result["prepared"], 2)
        self.assertEqual(calls, [])
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute("SELECT COUNT(*) FROM inmet_bulletins").fetchone()[0]
        self.assertEqual(rows, 0)

    def test_successful_bulletin_is_persisted_and_duplicate_run_sends_nothing(self):
        calls, delays = [], []
        first = process_bulletin(
            self.config,
            self.database,
            "manha",
            now=self.now,
            transport_sender=calls.append,
            simulate=False,
            message_builder=self.builder,
            sleep=delays.append,
        )
        second = process_bulletin(
            self.config,
            self.database,
            "manha",
            now=self.now,
            transport_sender=calls.append,
            simulate=False,
            message_builder=self.builder,
            sleep=delays.append,
        )
        self.assertEqual(first["sent"], 2)
        self.assertEqual(second["duplicate"], 2)
        self.assertEqual(len(calls), 4)
        self.assertTrue(calls[0].startswith("MESH ALERTS |"))
        self.assertTrue(calls[1].startswith("Temperatura:"))
        self.assertEqual(delays, [3, 30, 3])

    def test_restart_waits_remaining_interval_after_previous_city_delivery(self):
        self.database.parent.mkdir(parents=True, exist_ok=True)
        previous_delivery = (self.now - timedelta(seconds=5)).isoformat()
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "CREATE TABLE inmet_bulletins ("
                "local_date TEXT, period TEXT, municipality_code TEXT, status TEXT, "
                "updated_at TEXT, detail TEXT, "
                "PRIMARY KEY(local_date, period, municipality_code))"
            )
            connection.execute(
                "INSERT INTO inmet_bulletins VALUES (?, ?, ?, ?, ?, NULL)",
                ("2026-10-22", "manha", "3503000", "sent", previous_delivery),
            )
        delays = []
        result = process_bulletin(
            self.config,
            self.database,
            "manha",
            now=self.now,
            transport_sender=lambda _: None,
            simulate=False,
            message_builder=self.builder,
            sleep=delays.append,
        )
        self.assertEqual(result["duplicate"], 1)
        self.assertEqual(result["sent"], 1)
        self.assertEqual(delays, [25, 3])

    def test_restart_paces_each_new_city_after_the_previous_delivery(self):
        self.config["municipios"].append(
            {"nome": "Cidade C", "codigo_ibge": "3517703", "uf": "SP"}
        )
        self.database.parent.mkdir(parents=True, exist_ok=True)
        previous_delivery = (self.now - timedelta(seconds=5)).isoformat()
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "CREATE TABLE inmet_bulletins ("
                "local_date TEXT, period TEXT, municipality_code TEXT, status TEXT, "
                "updated_at TEXT, detail TEXT, "
                "PRIMARY KEY(local_date, period, municipality_code))"
            )
            connection.execute(
                "INSERT INTO inmet_bulletins VALUES (?, ?, ?, ?, ?, NULL)",
                ("2026-10-22", "manha", "3503000", "sent", previous_delivery),
            )
        delays = []
        result = process_bulletin(
            self.config,
            self.database,
            "manha",
            now=self.now,
            transport_sender=lambda _: None,
            simulate=False,
            message_builder=self.builder,
            sleep=delays.append,
        )
        self.assertEqual(result["duplicate"], 1)
        self.assertEqual(result["sent"], 2)
        self.assertEqual(delays, [25, 3, 30, 3])

    def test_unavailable_forecast_does_not_skip_city_pacing(self):
        delays = []

        def builder(municipality, period, now):
            if municipality["codigo_ibge"] == "3503000":
                raise ForecastUnavailableError("forecast not published")
            return self.builder(municipality, period, now)

        result = process_bulletin(
            self.config,
            self.database,
            "manha",
            now=self.now,
            transport_sender=lambda _: None,
            simulate=False,
            message_builder=builder,
            sleep=delays.append,
        )
        self.assertEqual(result["unavailable"], 1)
        self.assertEqual(result["sent"], 1)
        self.assertEqual(delays, [30, 3])

    def test_failed_transport_is_not_retried_automatically(self):
        calls = []

        def broken(message):
            calls.append(message)
            raise OSError("transport unavailable")

        first = process_bulletin(
            self.config,
            self.database,
            "manha",
            now=self.now,
            transport_sender=broken,
            simulate=False,
            message_builder=self.builder,
            sleep=lambda _: None,
        )
        second = process_bulletin(
            self.config,
            self.database,
            "manha",
            now=self.now,
            transport_sender=calls.append,
            simulate=False,
            message_builder=self.builder,
            sleep=lambda _: None,
        )
        self.assertEqual(first["failed"], 2)
        self.assertEqual(second["duplicate"], 2)
        self.assertEqual(len(calls), 2)

    def test_second_message_failure_is_not_retried_automatically(self):
        self.config["municipios"] = self.config["municipios"][:1]
        calls = []

        def fail_on_second(message):
            calls.append(message)
            if len(calls) == 2:
                raise OSError("transport interrupted on second message")

        first = process_bulletin(
            self.config,
            self.database,
            "manha",
            now=self.now,
            transport_sender=fail_on_second,
            simulate=False,
            message_builder=self.builder,
            sleep=lambda _: None,
        )
        second = process_bulletin(
            self.config,
            self.database,
            "manha",
            now=self.now,
            transport_sender=calls.append,
            simulate=False,
            message_builder=self.builder,
            sleep=lambda _: None,
        )
        self.assertEqual(first["failed"], 1)
        self.assertEqual(second["duplicate"], 1)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
