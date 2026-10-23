"""Load and validate the mesh-alerts YAML configuration."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.yaml")
VALID_PERIODS = {"manha", "tarde", "noite"}


def load_configuration(path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Load YAML and validate the fields required for forecast generation."""
    path = Path(path)
    try:
        with path.open("r", encoding="utf-8") as file_handle:
            config = yaml.safe_load(file_handle)
    except OSError as exc:
        raise ValueError(f"Não foi possível ler a configuração {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML inválido em {path}: {exc}") from exc

    if not isinstance(config, dict):
        raise ValueError("A configuração deve ser um objeto YAML.")

    municipalities = config.get("municipios")
    if not isinstance(municipalities, list) or not municipalities:
        raise ValueError("A configuração precisa conter uma lista não vazia de municípios.")

    codes = set()
    for index, municipality in enumerate(municipalities, start=1):
        if not isinstance(municipality, dict):
            raise ValueError(f"Município #{index} deve ser um objeto.")
        code = str(municipality.get("codigo_ibge", "")).strip()
        name = str(municipality.get("nome", "")).strip()
        state = str(municipality.get("uf", "")).strip().upper()
        if not code.isdigit() or len(code) != 7:
            raise ValueError(f"Código IBGE inválido no município #{index}: {code!r}.")
        if not name or len(state) != 2:
            raise ValueError(f"Nome ou UF inválidos no município #{index}.")
        if code in codes:
            raise ValueError(f"Código IBGE duplicado: {code}.")
        codes.add(code)

    inmet = config.get("inmet")
    if not isinstance(inmet, dict):
        raise ValueError("A configuração precisa conter a seção 'inmet'.")
    schedule = inmet.get("horarios")
    if not isinstance(schedule, dict) or not schedule:
        raise ValueError("A seção inmet.horarios precisa ser um mapa de horários.")
    for scheduled_time, period in schedule.items():
        if not isinstance(scheduled_time, str) or not re.fullmatch(
            r"(?:[01]\d|2[0-3]):[0-5]\d", scheduled_time
        ):
            raise ValueError(f"Horário inválido: {scheduled_time!r}. Use HH:MM.")
        if period not in VALID_PERIODS:
            raise ValueError(f"Período INMET inválido para {scheduled_time}: {period!r}.")
    for key in (
        "intervalo_entre_mensagens_segundos",
        "intervalo_entre_municipios_segundos",
        "janela_recuperacao_minutos",
    ):
        value = inmet.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"inmet.{key} deve ser um inteiro maior ou igual a zero.")

    return config
