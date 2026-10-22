"""Agendamento persistente dos boletins INMET, sem transmissão por padrão.

O estado é gravado antes de chamar o transporte para evitar retransmissões
automáticas após uma interrupção entre envio e confirmação. Isso privilegia
não duplicar mensagens; falhas de transporte exigem análise explícita.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from mesh_alerts_inmet import (
    PrevisaoIndisponivelError,
    data_local_atual,
    gerar_mensagem_municipal,
)

LOG = logging.getLogger(__name__)
Transport = Callable[[str], None]
MessageBuilder = Callable[[dict[str, Any], str, datetime], str]


def _connect(database: str | Path) -> sqlite3.Connection:
    path = Path(database)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.execute(
        """CREATE TABLE IF NOT EXISTS inmet_bulletins (
            local_date TEXT NOT NULL,
            period TEXT NOT NULL,
            municipality_code TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('reserved', 'sent', 'failed', 'unavailable')),
            updated_at TEXT NOT NULL,
            detail TEXT,
            PRIMARY KEY (local_date, period, municipality_code)
        )"""
    )
    connection.commit()
    return connection


def horario_agendado(
    config: dict[str, Any], now: datetime | None = None
) -> tuple[str, str] | None:
    """Return the latest due schedule slot within the configured recovery window."""
    inmet = config["inmet"]
    timezone = ZoneInfo(inmet.get("fuso_horario", "America/Sao_Paulo"))
    instant = now or datetime.now(timezone)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone)
    local = instant.astimezone(timezone)
    recovery_minutes = int(inmet.get("janela_recuperacao_minutos", 30))
    current_minute = local.hour * 60 + local.minute
    due = []
    for scheduled_time, period in inmet.get("horarios", {}).items():
        hour, minute = (int(part) for part in scheduled_time.split(":"))
        scheduled_minute = hour * 60 + minute
        elapsed = current_minute - scheduled_minute
        if 0 <= elapsed <= recovery_minutes:
            due.append((scheduled_minute, scheduled_time, period))
    if not due:
        return None
    _, scheduled_time, period = max(due)
    return scheduled_time, period


def periodo_agendado(config: dict[str, Any], now: datetime | None = None) -> str | None:
    """Return the due period, including a short recovery window after restart."""
    slot = horario_agendado(config, now)
    return slot[1] if slot else None


def executar_boletim(
    config: dict[str, Any],
    database: str | Path,
    periodo: str,
    *,
    now: datetime | None = None,
    enviar: Transport | None = None,
    simular: bool = True,
    construir_mensagem: MessageBuilder | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, int]:
    """Process one period for all cities with persistent per-city deduplication.

    In simulation mode, messages are built and reported but no transport is called
    and no delivery reservation is written. For real delivery, pass ``enviar`` and
    explicitly set ``simular=False``.
    """
    inmet = config["inmet"]
    timezone = ZoneInfo(inmet.get("fuso_horario", "America/Sao_Paulo"))
    instant = now or datetime.now(timezone)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone)
    instant = instant.astimezone(timezone)
    local_date = data_local_atual(config, instant).isoformat()
    if periodo not in {"manha", "tarde", "noite"}:
        raise ValueError(f"Período INMET inválido: {periodo!r}.")
    if not simular and enviar is None:
        raise ValueError("Transporte de envio obrigatório quando simular=False.")

    builder = construir_mensagem or (
        lambda municipality, period, when: gerar_mensagem_municipal(
            municipality, period, config=config, agora=when
        )
    )
    counts = {"prepared": 0, "sent": 0, "duplicate": 0, "unavailable": 0, "failed": 0}
    connection = _connect(database)
    try:
        municipalities = config["municipios"]
        municipality_processed = False
        last_delivery_time = None
        for municipality in municipalities:
            code = str(municipality["codigo_ibge"])
            exists = connection.execute(
                "SELECT status, updated_at FROM inmet_bulletins "
                "WHERE local_date=? AND period=? AND municipality_code=?",
                (local_date, periodo, code),
            ).fetchone()
            if exists:
                # Recover the previous delivery timestamp so a restarted process
                # waits only the remaining interval, not a fresh full interval.
                if exists[0] in {"reserved", "sent", "failed"}:
                    try:
                        candidate = datetime.fromisoformat(exists[1])
                        if last_delivery_time is None or candidate > last_delivery_time:
                            last_delivery_time = candidate
                    except (TypeError, ValueError):
                        pass
                counts["duplicate"] += 1
                municipality_processed = True
                continue

            if not simular:
                delay = int(inmet.get("intervalo_entre_municipios_segundos", 30))
                if last_delivery_time is None:
                    last_delivery = connection.execute(
                        "SELECT updated_at FROM inmet_bulletins "
                        "WHERE local_date=? AND period=? AND status IN ('reserved', 'sent', 'failed') "
                        "ORDER BY updated_at DESC LIMIT 1",
                        (local_date, periodo),
                    ).fetchone()
                    if last_delivery:
                        try:
                            last_delivery_time = datetime.fromisoformat(last_delivery[0])
                        except (TypeError, ValueError):
                            last_delivery_time = None
                if last_delivery_time is not None:
                    elapsed = (instant - last_delivery_time).total_seconds()
                    remaining = max(0.0, delay - max(0.0, elapsed))
                else:
                    remaining = float(delay) if municipality_processed else 0.0
                if remaining > 0:
                    sleep(remaining)

            municipality_processed = True
            try:
                message = builder(municipality, periodo, instant)
            except PrevisaoIndisponivelError as exc:
                LOG.warning("Previsão indisponível para %s: %s", municipality["nome"], exc)
                counts["unavailable"] += 1
                continue
            except Exception as exc:
                LOG.exception("Falha ao preparar previsão de %s", municipality["nome"])
                counts["failed"] += 1
                continue

            counts["prepared"] += 1
            if simular:
                LOG.info("SIMULAÇÃO: %s", message.replace("\n", " | "))
                continue

            # Reserve before external I/O: after a crash, this city is never
            # retransmitted automatically, avoiding a duplicate radio bulletin.
            connection.execute(
                "INSERT INTO inmet_bulletins VALUES (?, ?, ?, 'reserved', ?, NULL)",
                (local_date, periodo, code, instant.isoformat()),
            )
            connection.commit()
            try:
                enviar(message)
            except Exception as exc:
                LOG.exception("Envio falhou/ficou incerto para %s; não haverá retry automático", municipality["nome"])
                connection.execute(
                    "UPDATE inmet_bulletins SET status='failed', updated_at=?, detail=? "
                    "WHERE local_date=? AND period=? AND municipality_code=?",
                    (datetime.now(timezone).isoformat(), str(exc)[:500], local_date, periodo, code),
                )
                connection.commit()
                counts["failed"] += 1
            else:
                connection.execute(
                    "UPDATE inmet_bulletins SET status='sent', updated_at=? "
                    "WHERE local_date=? AND period=? AND municipality_code=?",
                    (datetime.now(timezone).isoformat(), local_date, periodo, code),
                )
                connection.commit()
                counts["sent"] += 1
    finally:
        connection.close()
    return counts
