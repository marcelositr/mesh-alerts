"""Carregamento e validação da configuração pública do mesh-alerts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path("config.yaml")
LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}
DEFAULTS: dict[str, Any] = {
    "regiao": {"nome": "Região de Ituverava", "estado": "São Paulo", "uf": "SP"},
    "municipios": [
        {"nome": "Aramina", "codigo_ibge": "3503000"},
        {"nome": "Buritizal", "codigo_ibge": "3508207"},
        {"nome": "Guará", "codigo_ibge": "3517703"},
        {"nome": "Igarapava", "codigo_ibge": "3520103"},
        {"nome": "Ituverava", "codigo_ibge": "3524105"},
        {"nome": "Miguelópolis", "codigo_ibge": "3529708"},
    ],
    "meshtastic": {"host": "meshtastic-node.local", "porta": 4403, "canal": 0, "destino": "^all"},
    "servico": {
        "intervalo_segundos": 60,
        "banco": "data/mesh-alerts.sqlite3",
        "diretorio_txt": "output",
        "nivel_log": "INFO",
        "timeout_http_segundos": 25,
        "tentativas_feed": 3,
        "tentativas_malha_ibge": 2,
        "limite_mensagem_caracteres": 220,
    },
    "fontes": {
        "feed_cap": "https://idapfile.mdr.gov.br/idap/api/rss/cap",
        "malhas_ibge": "https://servicodados.ibge.gov.br/api/v3/malhas/municipios",
    },
    "inmet": {
        "previsao_url": "https://apiprevmet3.inmet.gov.br/previsao",
        "fuso_horario": "America/Sao_Paulo",
        "timeout_segundos": 30,
        "janela_recuperacao_minutos": 30,
        "horarios": {"06:00": "manha", "12:00": "tarde", "18:00": "noite"},
        "intervalo_entre_mensagens_segundos": 3,
        "intervalo_entre_municipios_segundos": 30,
    },
}


class ConfigError(ValueError):
    """Erro compreensível de configuração fornecida pelo operador."""


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"'{label}' deve ser um texto não vazio.")
    return value.strip()


def _positive_int(value: Any, label: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigError(f"'{label}' deve ser um número inteiro >= {minimum}.")
    return value


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Load YAML, merge safe defaults, and validate operator-facing values."""
    config_path = Path(path)
    try:
        with config_path.open("r", encoding="utf-8") as stream:
            raw = yaml.safe_load(stream)
    except FileNotFoundError as exc:
        raise ConfigError(f"Arquivo de configuração não encontrado: {config_path}") from exc
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Não foi possível ler '{config_path}': {exc}") from exc

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("A raiz de config.yaml deve ser um objeto YAML.")
    unknown_sections = set(raw) - set(DEFAULTS)
    if unknown_sections:
        raise ConfigError("Seções desconhecidas em config.yaml: " + ", ".join(sorted(unknown_sections)))

    config = _merge(DEFAULTS, raw)
    region = config["regiao"]
    for key in ("nome", "estado", "uf"):
        region[key] = _required_text(region.get(key), f"regiao.{key}")
    if not re.fullmatch(r"[A-Za-z]{2}", region["uf"]):
        raise ConfigError("'regiao.uf' deve conter a sigla de duas letras, por exemplo SP.")
    region["uf"] = region["uf"].upper()

    municipalities = config["municipios"]
    if not isinstance(municipalities, list) or not municipalities:
        raise ConfigError("'municipios' deve conter ao menos um município.")
    names, codes = set(), set()
    for index, municipality in enumerate(municipalities, start=1):
        label = f"municipios[{index}]"
        if not isinstance(municipality, dict):
            raise ConfigError(f"'{label}' deve conter nome e codigo_ibge.")
        name = _required_text(municipality.get("nome"), f"{label}.nome")
        code = municipality.get("codigo_ibge")
        if isinstance(code, int):
            code = str(code)
        if not isinstance(code, str) or not re.fullmatch(r"\d{7}", code):
            raise ConfigError(f"'{label}.codigo_ibge' deve ser um código IBGE de sete dígitos em texto.")
        normalized = name.casefold()
        if normalized in names:
            raise ConfigError(f"Município duplicado: {name}.")
        if code in codes:
            raise ConfigError(f"Código IBGE duplicado: {code}.")
        names.add(normalized)
        codes.add(code)
        municipality["nome"], municipality["codigo_ibge"] = name, code

    meshtastic = config["meshtastic"]
    meshtastic["host"] = _required_text(meshtastic.get("host"), "meshtastic.host")
    meshtastic["destino"] = _required_text(meshtastic.get("destino"), "meshtastic.destino")
    meshtastic["porta"] = _positive_int(meshtastic.get("porta"), "meshtastic.porta")
    if meshtastic["porta"] > 65535:
        raise ConfigError("'meshtastic.porta' deve estar entre 1 e 65535.")
    meshtastic["canal"] = _positive_int(meshtastic.get("canal"), "meshtastic.canal", 0)

    service = config["servico"]
    service["intervalo_segundos"] = _positive_int(service.get("intervalo_segundos"), "servico.intervalo_segundos", 10)
    for key in ("timeout_http_segundos", "tentativas_feed", "tentativas_malha_ibge"):
        service[key] = _positive_int(service.get(key), f"servico.{key}")
    service["limite_mensagem_caracteres"] = _positive_int(
        service.get("limite_mensagem_caracteres"), "servico.limite_mensagem_caracteres", 50
    )
    if service["limite_mensagem_caracteres"] > 220:
        raise ConfigError("'servico.limite_mensagem_caracteres' deve ser no máximo 220.")
    if service.get("nivel_log") not in LOG_LEVELS:
        raise ConfigError("'servico.nivel_log' deve ser DEBUG, INFO, WARNING ou ERROR.")
    for key in ("banco", "diretorio_txt"):
        service[key] = _required_text(service.get(key), f"servico.{key}")

    sources = config["fontes"]
    for key in ("feed_cap", "malhas_ibge"):
        value = _required_text(sources.get(key), f"fontes.{key}")
        if not value.startswith("https://"):
            raise ConfigError(f"'fontes.{key}' deve usar HTTPS.")
        sources[key] = value

    return config


def municipality_map(config: dict[str, Any]) -> dict[str, str]:
    """Return {IBGE code: municipality name} for geometry matching."""
    return {item["codigo_ibge"]: item["nome"] for item in config["municipios"]}
