"""Carregamento e validação da configuração YAML do mesh-alerts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.yaml")
PERIODOS_VALIDOS = {"manha", "tarde", "noite"}


def carregar_configuracao(caminho: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Carrega o YAML e valida os campos necessários à previsão."""
    path = Path(caminho)
    try:
        with path.open("r", encoding="utf-8") as arquivo:
            config = yaml.safe_load(arquivo)
    except OSError as exc:
        raise ValueError(f"Não foi possível ler a configuração {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML inválido em {path}: {exc}") from exc

    if not isinstance(config, dict):
        raise ValueError("A configuração deve ser um objeto YAML.")

    municipios = config.get("municipios")
    if not isinstance(municipios, list) or not municipios:
        raise ValueError("A configuração precisa conter uma lista não vazia de municípios.")

    codigos = set()
    for indice, municipio in enumerate(municipios, start=1):
        if not isinstance(municipio, dict):
            raise ValueError(f"Município #{indice} deve ser um objeto.")
        codigo = str(municipio.get("codigo_ibge", "")).strip()
        nome = str(municipio.get("nome", "")).strip()
        uf = str(municipio.get("uf", "")).strip().upper()
        if not codigo.isdigit() or len(codigo) != 7:
            raise ValueError(f"Código IBGE inválido no município #{indice}: {codigo!r}.")
        if not nome or len(uf) != 2:
            raise ValueError(f"Nome ou UF inválidos no município #{indice}.")
        if codigo in codigos:
            raise ValueError(f"Código IBGE duplicado: {codigo}.")
        codigos.add(codigo)

    inmet = config.get("inmet")
    if not isinstance(inmet, dict):
        raise ValueError("A configuração precisa conter a seção 'inmet'.")
    horarios = inmet.get("horarios")
    if not isinstance(horarios, dict) or not horarios:
        raise ValueError("A seção inmet.horarios precisa ser um mapa de horários.")
    for horario, periodo in horarios.items():
        if not isinstance(horario, str) or len(horario) != 5 or horario[2] != ":":
            raise ValueError(f"Horário inválido: {horario!r}. Use HH:MM.")
        if periodo not in PERIODOS_VALIDOS:
            raise ValueError(f"Período INMET inválido para {horario}: {periodo!r}.")
    for chave in ("intervalo_entre_mensagens_segundos", "intervalo_entre_municipios_segundos"):
        valor = inmet.get(chave)
        if not isinstance(valor, int) or isinstance(valor, bool) or valor < 0:
            raise ValueError(f"inmet.{chave} deve ser um inteiro maior ou igual a zero.")

    return config
