"""Retrieve and format daily INMET forecasts without transmitting messages."""

from __future__ import annotations

import json
import urllib.request

from .http_utils import open_http_url, validate_http_url
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from .inmet_config import load_configuration

PERIOD_LABELS = {"manha": "MANHÃ", "tarde": "TARDE", "noite": "NOITE"}
USER_AGENT = "mesh-alerts/0.1"
WIND_INTENSITY_LABELS = {
    "fracos": "fraco",
    "moderados": "moderado",
    "fortes": "forte",
    "calmos": "calmo",
}


class ForecastUnavailableError(RuntimeError):
    """The API did not provide a forecast for the requested date and period."""


def get_local_date(config: dict[str, Any], now: datetime | None = None) -> date:
    """Return the current date in the configured time zone."""
    timezone = ZoneInfo(config["inmet"].get("fuso_horario", "America/Sao_Paulo"))
    instant = now if now is not None else datetime.now(timezone)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone)
    return instant.astimezone(timezone).date()


def fetch_forecast(ibge_code: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Retrieve the official INMET forecast for a municipality."""
    config = config or load_configuration()
    inmet = config["inmet"]
    base_url = str(inmet.get("previsao_url", "")).rstrip("/")
    if not base_url:
        raise ValueError("inmet.previsao_url não foi configurada.")
    validate_http_url(base_url)
    url = f"{base_url}/{ibge_code}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
        },
    )
    timeout = int(inmet.get("timeout_segundos", 30))
    with open_http_url(request, timeout=timeout) as resposta:
        data = json.loads(resposta.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Resposta inesperada do INMET para o código IBGE {ibge_code}.")
    return data


def select_forecast_period(
    data: dict[str, Any],
    ibge_code: str,
    period: str,
    forecast_date: date,
) -> dict[str, Any]:
    """Select the requested period for the specified date without falling back to future dates."""
    if period not in PERIOD_LABELS:
        raise ValueError(f"Período inválido: {period!r}.")
    municipalities = data.get(str(ibge_code))
    if not isinstance(municipalities, dict):
        raise ForecastUnavailableError(
            f"INMET não retornou o município IBGE {ibge_code}."
        )
    daily_forecast = municipalities.get(forecast_date.strftime("%d/%m/%Y"))
    if not isinstance(daily_forecast, dict):
        raise ForecastUnavailableError(
            f"INMET não retornou previsão para hoje ({forecast_date:%d/%m/%Y}), código {ibge_code}."
        )
    period_forecast = daily_forecast.get(period)
    if not isinstance(period_forecast, dict):
        raise ForecastUnavailableError(
            f"INMET não retornou o período {period} de hoje ({forecast_date:%d/%m/%Y}), "
            f"código {ibge_code}."
        )
    return period_forecast


def format_bulletin_messages(
    municipality: dict[str, str],
    period: str,
    forecast: dict[str, Any],
) -> tuple[str, ...]:
    """Format an INMET bulletin as a summary message and a details message."""
    if period not in PERIOD_LABELS:
        raise ValueError(f"Período inválido: {period!r}.")

    name = str(municipality.get("nome", "")).strip()
    state = str(municipality.get("uf", "")).strip().upper()
    if not name or len(state) != 2:
        raise ValueError("Município precisa ter nome e UF válidos.")

    summary = str(forecast.get("resumo") or "").strip()
    if not summary:
        raise ForecastUnavailableError("INMET retornou um resumo vazio para o período.")

    header_message = (
        f"MESH ALERTS | {name.upper()}/{state} | {PERIOD_LABELS[period]}\n"
        f"{summary}"
    )

    details = []
    max_temp = forecast.get("temp_max")
    min_temp = forecast.get("temp_min")
    if max_temp is not None and min_temp is not None:
        details.append(f"Temperatura: {max_temp}°C / {min_temp}°C")

    max_humidity = forecast.get("umidade_max")
    min_humidity = forecast.get("umidade_min")
    if max_humidity is not None and min_humidity is not None:
        details.append(f"Umidade: {max_humidity}% / {min_humidity}%")

    wind = str(forecast.get("int_vento") or "").strip()
    direction = str(forecast.get("dir_vento") or "").strip()
    if wind:
        wind = WIND_INTENSITY_LABELS.get(wind.casefold(), wind.casefold())
        if direction:
            wind = f"{wind} ({direction})"
        details.append(f"Vento: {wind}")
    elif direction:
        details.append(f"Direção do vento: {direction}")

    if details:
        return header_message, "\n".join(details)
    return (header_message,)


def format_bulletin_message(
    municipality: dict[str, str],
    period: str,
    forecast: dict[str, Any],
) -> str:
    """Return only the first message for callers that need the summary."""
    return format_bulletin_messages(municipality, period, forecast)[0]


def build_municipal_bulletin(
    municipality: dict[str, str],
    period: str,
    forecast_date: date | None = None,
    config: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> tuple[str, ...]:
    """Retrieve and format today's forecast as separate messages."""
    config = config or load_configuration()
    selected_date = forecast_date or get_local_date(config, now)
    code = str(municipality["codigo_ibge"])
    data = fetch_forecast(code, config)
    forecast = select_forecast_period(data, code, period, selected_date)
    return format_bulletin_messages(municipality, period, forecast)
