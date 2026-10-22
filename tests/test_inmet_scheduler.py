import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from mesh_alerts_inmet_scheduler import executar_boletim, periodo_agendado


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
        self.assertIsNone(periodo_agendado(self.config, self.now.replace(minute=1)))

    def test_simulation_never_calls_transport_or_persists_delivery(self):
        calls = []
        result = executar_boletim(
            self.config, self.database, "manha", now=self.now,
            enviar=calls.append, simular=True, construir_mensagem=self.builder,
            sleep=lambda _: self.fail("simulation must not sleep"),
        )
        self.assertEqual(result["prepared"], 2)
        self.assertEqual(calls, [])
        self.assertFalse(self.database.exists())

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
