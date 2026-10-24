"""Persistent INMET bulletin scheduling with transmission disabled by default.

Delivery state is stored before transport invocation to prevent automatic
retransmission after interruption. Uncertain transport failures require review.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .inmet import (
    ForecastUnavailableError,
    get_local_date,
    build_municipal_bulletin,
)

LOG = logging.getLogger(__name__)
Transport = Callable[[str], None]
MessageBuilder = Callable[[dict[str, Any], str, datetime], str | tuple[str, ...]]


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


def get_due_schedule_slot(
    config: dict[str, Any], now: datetime | None = None
) -> tuple[str, str] | None:
    """Return the latest scheduled slot within the configured recovery window."""
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


def get_due_period(config: dict[str, Any], now: datetime | None = None) -> str | None:
    """Return the due period, including the recovery window after restart."""
    slot = get_due_schedule_slot(config, now)
    return slot[1] if slot else None


def process_bulletin(
    config: dict[str, Any],
    database: str | Path,
    period: str,
    *,
    now: datetime | None = None,
    transport_sender: Transport | None = None,
    simulate: bool = True,
    message_builder: MessageBuilder | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, int]:
    """Process one period for all municipalities with persistent deduplication.

    Simulation builds and logs messages without invoking transport or reserving
    deliveries. Real delivery requires a transport sender and ``simulate=False``.
    """
    inmet = config["inmet"]
    timezone = ZoneInfo(inmet.get("fuso_horario", "America/Sao_Paulo"))
    instant = now or datetime.now(timezone)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone)
    instant = instant.astimezone(timezone)
    local_date = get_local_date(config, instant).isoformat()
    if period not in {"manha", "tarde", "noite"}:
        raise ValueError(f"Período INMET inválido: {period!r}.")
    if not simulate and transport_sender is None:
        raise ValueError("Transporte de envio obrigatório quando simular=False.")

    builder = message_builder or (
        lambda municipality, period, when: build_municipal_bulletin(
            municipality, period, config=config, now=when
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
                (local_date, period, code),
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

            if not simulate:
                delay = int(inmet.get("intervalo_entre_municipios_segundos", 30))
                if last_delivery_time is None:
                    last_delivery = connection.execute(
                        "SELECT updated_at FROM inmet_bulletins "
                        "WHERE local_date=? AND period=? AND status IN ('reserved', 'sent', 'failed') "
                        "ORDER BY updated_at DESC LIMIT 1",
                        (local_date, period),
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
                    instant += timedelta(seconds=remaining)

            municipality_processed = True
            try:
                built_messages = builder(municipality, period, instant)
                if isinstance(built_messages, str):
                    messages = (built_messages,)
                else:
                    messages = tuple(
                        message for message in built_messages
                        if isinstance(message, str) and message.strip()
                    )
                if not messages:
                    raise ForecastUnavailableError(
                        "INMET não retornou mensagens para o boletim."
                    )
            except ForecastUnavailableError as exc:
                LOG.warning("Previsão indisponível para %s: %s", municipality["nome"], exc)
                counts["unavailable"] += 1
                continue
            except Exception:
                LOG.exception("Falha ao preparar previsão de %s", municipality["nome"])
                counts["failed"] += 1
                continue

            counts["prepared"] += 1
            if simulate:
                for message in messages:
                    LOG.info("SIMULAÇÃO: %s", message.replace("\n", " | "))
                continue

            # Reserve before external I/O: after a crash, this municipality is never
            # retransmitted automatically, avoiding a duplicate radio bulletin.
            connection.execute(
                "INSERT INTO inmet_bulletins VALUES (?, ?, ?, 'reserved', ?, NULL)",
                (local_date, period, code, instant.isoformat()),
            )
            connection.commit()
            try:
                message_interval = int(
                    inmet.get("intervalo_entre_mensagens_segundos", 0)
                )
                for index, message in enumerate(messages):
                    if index > 0 and message_interval > 0:
                        sleep(float(message_interval))
                        instant += timedelta(seconds=message_interval)
                    transport_sender(message)
            except Exception as exc:
                LOG.exception(
                    "Envio falhou/ficou incerto para %s; não haverá retry automático",
                    municipality["nome"],
                )
                connection.execute(
                    "UPDATE inmet_bulletins SET status='failed', updated_at=?, detail=? "
                    "WHERE local_date=? AND period=? AND municipality_code=?",
                    (datetime.now(timezone).isoformat(), str(exc)[:500], local_date, period, code),
                )
                connection.commit()
                counts["failed"] += 1
                last_delivery_time = instant
            else:
                connection.execute(
                    "UPDATE inmet_bulletins SET status='sent', updated_at=? "
                    "WHERE local_date=? AND period=? AND municipality_code=?",
                    (datetime.now(timezone).isoformat(), local_date, period, code),
                )
                connection.commit()
                counts["sent"] += 1
                last_delivery_time = instant
    finally:
        connection.close()
    return counts
