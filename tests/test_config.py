import tempfile
import unittest
from pathlib import Path

import yaml

from mesh_alerts_config import ConfigError, load_config, municipality_map


class ConfigurationTests(unittest.TestCase):
    def test_repository_config_loads_with_six_current_municipalities(self):
        config = load_config("config.yaml")
        municipalities = municipality_map(config)
        self.assertEqual(len(municipalities), 6)
        self.assertEqual(municipalities["3524105"], "Ituverava")
        self.assertEqual(config["meshtastic"]["porta"], 4403)

    def test_missing_file_has_actionable_error(self):
        with self.assertRaisesRegex(ConfigError, "não encontrado"):
            load_config("definitely-not-a-real-config.yaml")

    def test_invalid_yaml_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text("- item\n- item2\n", encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "raiz"):
                load_config(path)

    def test_duplicate_municipality_code_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(yaml.safe_dump({
                "municipios": [
                    {"nome": "Cidade A", "codigo_ibge": "3503000"},
                    {"nome": "Cidade B", "codigo_ibge": "3503000"},
                ]
            }, allow_unicode=True), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "duplicado"):
                load_config(path)

    def test_invalid_interval_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(yaml.safe_dump({
                "servico": {"intervalo_segundos": 1}
            }), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "pelo menos 10"):
                load_config(path)

    def test_unknown_section_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text("misterio: true\n", encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "desconhecidas"):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
