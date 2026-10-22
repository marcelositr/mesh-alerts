import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from mesh_alerts_inmet import PrevisaoIndisponivelError
from mesh_alerts_inmet_scheduler import executar_boletim, horario_agendado, periodo_agendado


class InmetSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "state" / "test.sqlite3"
        self.config = {
            "inmet": {
                "fuso_horario": "America/Sao_Paulo",
                "horarios": {"06:00": "manha", "12:00": "tarde", "18:00": "noite"},
                "intervalo_entre_mensagens_segundos": 3,
                "intervalo_entre_municipios_segundos": 30,
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
        return f'{municipality["nome"]} {period} {now:%Y-%m-%d}'

    def test_schedule_only_matches_configured_local_minute(self):
        self.assertEqual(periodo_agendado(self.config, self.now), "manha")
        self.assertEqual(horario_agendado(self.config, self.now.replace(minute=1)), ("06:00", "manha"))
        self.assertIsNone(periodo_agendado(self.config, self.now.replace(minute=31)))

    def test_simulation_never_calls_transport_or_persists_delivery(self):
        calls = []
        result = executar_boletim(
            self.config, self.database, "manha", now=self.now,
            enviar=calls.append, simular=True, construir_mensagem=self.builder,
            sleep=lambda _: self.fail("simulation must not sleep"),
        )
        self.assertEqual(result["prepared"], 2)
        self.assertEqual(calls, [])
        self.assertTrue(self.database.exists())
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute("SELECT COUNT(*) FROM inmet_bulletins").fetchone()[0]
        self.assertEqual(rows, 0)

    def test_successful_bulletin_is_persisted_and_duplicate_run_sends_nothing(self):
        calls, delays = [], []
        first = executar_boletim(
            self.config, self.database, "manha", now=self.now,
            enviar=calls.append, simular=False, construir_mensagem=self.builder,
            sleep=delays.append,
        )
        second = executar_boletim(
            self.config, self.database, "manha", now=self.now,
            enviar=calls.append, simular=False, construir_mensagem=self.builder,
            sleep=delays.append,
        )
        self.assertEqual(first["sent"], 2)
        self.assertEqual(second["duplicate"], 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(delays, [30])

    def test_restart_waits_remaining_interval_after_previous_city_delivery(self):\n        self.database.parent.mkdir(parents=True, exist_ok=True)\n        previous_delivery = (self.now - timedelta(seconds=5)).isoformat()\n        with sqlite3.connect(self.database) as connection:\n            connection.execute(\n                "CREATE TABLE inmet_bulletins ("\n                "local_date TEXT, period TEXT, municipality_code TEXT, status TEXT, "\n                "updated_at TEXT, detail TEXT, PRIMARY KEY(local_date, period, municipality_code))"\n            )\n            connection.execute(\n                "INSERT INTO inmet_bulletins VALUES (?, ?, ?, ?, ?, NULL)",\n                ("2026-10-22", "manha", "3503000", "sent", previous_delivery),\n            )\n        delays = []\n        result = executar_boletim(\n            self.config, self.database, "manha", now=self.now,\n            enviar=lambda _: None, simular=False, construir_mensagem=self.builder,\n            sleep=delays.append,\n        )\n        self.assertEqual(result["duplicate"], 1)\n        self.assertEqual(result["sent"], 1)\n        self.assertEqual(delays, [25])\n    def test_unavailable_forecast_does_not_skip_city_pacing(self):
        delays = []
        def builder(municipality, period, now):
            if municipality["codigo_ibge"] == "3503000":
                raise PrevisaoIndisponivelError("forecast not published")
            return self.builder(municipality, period, now)
        result = executar_boletim(
            self.config, self.database, "manha", now=self.now,
            enviar=lambda _: None, simular=False, construir_mensagem=builder,
            sleep=delays.append,
        )
        self.assertEqual(result["unavailable"], 1)
        self.assertEqual(result["sent"], 1)
        self.assertEqual(delays, [30])
    def test_failed_transport_is_not_retried_automatically(self):
        calls = []
        def broken(message):
            calls.append(message)
            raise OSError("transport unavailable")
        first = executar_boletim(
            self.config, self.database, "manha", now=self.now,
            enviar=broken, simular=False, construir_mensagem=self.builder,
            sleep=lambda _: None,
        )
        second = executar_boletim(
            self.config, self.database, "manha", now=self.now,
            enviar=calls.append, simular=False, construir_mensagem=self.builder,
            sleep=lambda _: None,
        )
        self.assertEqual(first["failed"], 2)
        self.assertEqual(second["duplicate"], 2)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
