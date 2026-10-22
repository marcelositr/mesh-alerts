"""Consulta e formata previsões diárias do INMET, sem transmitir mensagens."""

from __future__ import annotations

import json
import urllib.request
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from mesh_alerts_config import carregar_configuracao

PERIODOS = {"manha": "MANHÃ", "tarde": "TARDE", "noite": "NOITE"}
USER_AGENT = "mesh-alerts/0.1"
VENTO_NORMALIZADO = {
    "fracos": "fraco",
    "moderados": "moderado",
    "fortes": "forte",
    "calmos": "calmo",
}


class PrevisaoIndisponivelError(RuntimeError):
    """A API não forneceu a previsão do dia/período solicitado."""


def data_local_atual(config: dict[str, Any], agora: datetime | None = None) -> date:
    """Obtém a data atual no fuso configurado, inclusive quando chamada por UTC."""
    fuso = ZoneInfo(config["inmet"].get("fuso_horario", "America/Sao_Paulo"))
    instante = agora if agora is not None else datetime.now(fuso)
    if instante.tzinfo is None:
        instante = instante.replace(tzinfo=fuso)
    return instante.astimezone(fuso).date()


def buscar_previsao(codigo_ibge: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Consulta o endpoint oficial do INMET para um município."""
    config = config or carregar_configuracao()
    inmet = config["inmet"]
    base_url = str(inmet.get("previsao_url", "")).rstrip("/")
    if not base_url:
        raise ValueError("inmet.previsao_url não foi configurada.")
    url = f"{base_url}/{codigo_ibge}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
        },
    )
    timeout = int(inmet.get("timeout_segundos", 30))
    with urllib.request.urlopen(request, timeout=timeout) as resposta:
        dados = json.loads(resposta.read().decode("utf-8"))
    if not isinstance(dados, dict):
        raise ValueError(f"Resposta inesperada do INMET para o código IBGE {codigo_ibge}.")
    return dados


def selecionar_periodo_do_dia(
    dados: dict[str, Any],
    codigo_ibge: str,
    periodo: str,
    hoje: date,
) -> dict[str, Any]:
    """Seleciona apenas o período solicitado na data de hoje; nunca usa dias futuros."""
    if periodo not in PERIODOS:
        raise ValueError(f"Período inválido: {periodo!r}.")
    municipios = dados.get(str(codigo_ibge))
    if not isinstance(municipios, dict):
        raise PrevisaoIndisponivelError(
            f"INMET não retornou o município IBGE {codigo_ibge}."
        )
    previsao_dia = municipios.get(hoje.strftime("%d/%m/%Y"))
    if not isinstance(previsao_dia, dict):
        raise PrevisaoIndisponivelError(
            f"INMET não retornou previsão para hoje ({hoje:%d/%m/%Y}), código {codigo_ibge}."
        )
    dados_periodo = previsao_dia.get(periodo)
    if not isinstance(dados_periodo, dict):
        raise PrevisaoIndisponivelError(
            f"INMET não retornou o período {periodo} de hoje ({hoje:%d/%m/%Y}), "
            f"código {codigo_ibge}."
        )
    return dados_periodo


def formatar_mensagem(
    municipio: dict[str, str],
    periodo: str,
    previsao: dict[str, Any],
) -> str:
    """Formata o boletim compacto em duas linhas, preservando o resumo do INMET."""
    if periodo not in PERIODOS:
        raise ValueError(f"Período inválido: {periodo!r}.")

    nome = str(municipio.get("nome", "")).strip()
    uf = str(municipio.get("uf", "")).strip().upper()
    if not nome or len(uf) != 2:
        raise ValueError("Município precisa ter nome e UF válidos.")

    resumo = str(previsao.get("resumo") or "").strip()
    if not resumo:
        raise PrevisaoIndisponivelError("INMET retornou um resumo vazio para o período.")

    partes = [resumo]
    temp_max = previsao.get("temp_max")
    temp_min = previsao.get("temp_min")
    if temp_max is not None and temp_min is not None:
        partes.append(f"{temp_max}/{temp_min}°C")

    umidade_max = previsao.get("umidade_max")
    umidade_min = previsao.get("umidade_min")
    if umidade_max is not None and umidade_min is not None:
        partes.append(f"U{umidade_max}/{umidade_min}%")

    vento = str(previsao.get("int_vento") or "").strip()
    if vento:
        vento = VENTO_NORMALIZADO.get(vento.casefold(), vento.casefold())
        partes.append(f"Vento {vento}")

    return f"{nome.upper()}/{uf} • {PERIODOS[periodo]}\n" + " | ".join(partes)


def gerar_mensagem_municipal(
    municipio: dict[str, str],
    periodo: str,
    hoje: date | None = None,
    config: dict[str, Any] | None = None,
    agora: datetime | None = None,
) -> str:
    """Consulta e formata a previsão de hoje para um município e período."""
    config = config or carregar_configuracao()
    data_previsao = hoje or data_local_atual(config, agora)
    codigo = str(municipio["codigo_ibge"])
    dados = buscar_previsao(codigo, config)
    previsao = selecionar_periodo_do_dia(dados, codigo, periodo, data_previsao)
    return formatar_mensagem(municipio, periodo, previsao)
