import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from mesh_alerts_config import carregar_configuracao
from mesh_alerts_inmet import (
    PrevisaoIndisponivelError,
    data_local_atual,
    formatar_mensagem,
    selecionar_periodo_do_dia,
)


class InmetForecastTests(unittest.TestCase):
    def setUp(self):
        self.hoje = date(2026, 10, 22)
        self.municipio = {
            "codigo_ibge": "3524105",
            "nome": "Ituverava",
            "uf": "SP",
        }
        self.previsao = {
            "resumo": "Muitas nuvens com possibilidade de chuva isolada",
            "temp_max": 32,
            "temp_min": 20,
            "umidade_max": 85,
            "umidade_min": 50,
            "int_vento": "Fracos",
            "cod_icone": "36",
            "cod_temp_max_tende_icone": "86",
        }
        self.dados = {
            "3524105": {
                "22/10/2026": {
                    "manha": self.previsao,
                    "tarde": self.previsao,
                    "noite": self.previsao,
                },
                "23/10/2026": {"manha": {"resumo": "PREVISÃO FUTURA"}},
            }
        }

    def test_selects_requested_period_from_today_only(self):
        selected = selecionar_periodo_do_dia(
            self.dados, "3524105", "manha", self.hoje
        )
        self.assertIs(selected, self.previsao)

    def test_missing_today_raises_instead_of_using_future_forecast(self):
        with self.assertRaisesRegex(PrevisaoIndisponivelError, "hoje"):
            selecionar_periodo_do_dia(
                self.dados, "3524105", "manha", date(2026, 10, 24)
            )

    def test_missing_period_does_not_use_another_period_or_future(self):
        with self.assertRaises(PrevisaoIndisponivelError):
            selecionar_periodo_do_dia(
                self.dados, "3524105", "tarde", date(2026, 10, 23)
            )

    def test_compact_message_is_two_lines_and_uses_max_then_min(self):
        message = formatar_mensagem(self.municipio, "manha", self.previsao)
        self.assertEqual(
            message,
            "ITUVERAVA/SP • MANHÃ\n"
            "Muitas nuvens com possibilidade de chuva isolada | 32/20°C | U85/50% | Vento fraco",
        )
        self.assertEqual(len(message.splitlines()), 2)
        self.assertNotIn("cod_icone", message)
        self.assertNotIn("tendência", message.lower())

    def test_optional_fields_are_omitted_not_rendered_as_none(self):
        message = formatar_mensagem(
            self.municipio,
            "noite",
            {"resumo": "Céu claro", "int_vento": ""},
        )
        self.assertEqual(message, "ITUVERAVA/SP • NOITE\nCéu claro")

    def test_local_date_uses_configured_brazil_timezone(self):
        config = carregar_configuracao()
        utc_time = datetime(2026, 10, 23, 1, 30, tzinfo=timezone.utc)
        self.assertEqual(data_local_atual(config, utc_time), date(2026, 10, 22))

    def test_config_has_six_unique_municipalities_and_expected_schedule(self):
        config = carregar_configuracao()
        self.assertEqual(len(config["municipios"]), 6)
        self.assertEqual(
            config["inmet"]["horarios"],
            {"06:00": "manha", "12:00": "tarde", "18:00": "noite"},
        )
        self.assertEqual(config["inmet"]["intervalo_entre_mensagens_segundos"], 3)
        self.assertEqual(config["inmet"]["intervalo_entre_municipios_segundos"], 30)

    def test_config_rejects_invalid_schedule(self):
        config = carregar_configuracao()
        config["inmet"]["horarios"]["13:00"] = "proxima-semana"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.yaml"
            path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Período INMET inválido"):
                carregar_configuracao(path)


if __name__ == "__main__":
    unittest.main()
