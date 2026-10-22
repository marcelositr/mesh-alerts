#!/usr/bin/env python3
"""Continuous CAP-to-Meshtastic service with durable SQLite state."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from mesh_alerts_cap import (
    NS,
    active_alert_entries,
    alert_key,
    info_is_current,
    intersecting_municipalities,
    parse_cap_datetime,
    references_to_keys,
)
from mesh_alerts_state import AlertStateStore
from testar_alertas_regiao_ituverava import (
    FEED_URL,
    MUNICIPIOS,
    carregar_feed,
    carregar_limites,
)

LOG = logging.getLogger("mesh-alerts")


def _text(element: ET.Element, path: str, default: str = "") -> str:
    return element.findtext(path, default=default, namespaces=NS).strip()


def _current_info(alert: ET.Element, now: datetime) -> list[ET.Element]:
    return [
        info for info in alert.findall("c:info", NS)
        if info_is_current(info, now)[0]
    ]


def _expiry_for_current_info(infos: list[ET.Element]) -> str | None:
    expiries = []
    for info in infos:
        raw = _text(info, "c:expires")
        if not raw:
            return None
        parsed = parse_cap_datetime(raw)
        if parsed is not None:
            expiries.append(parsed)
    return max(expiries).isoformat() if expiries else None


def _safe_filename(sender: str, identifier: str) -> str:
    digest = hashlib.sha256(f"{sender}\n{identifier}".encode("utf-8")).hexdigest()[:24]
    return f"{digest}.txt"


def _make_message(alert: ET.Element, cities: list[str], infos: list[ET.Element]) -> str:
    info = infos[0]
    headline = _text(info, "c:headline") or _text(info, "c:event") or "Alerta da Defesa Civil"
    severity = _text(info, "c:severity")
    urgency = _text(info, "c:urgency")
    certainty = _text(info, "c:certainty")
    description = _text(info, "c:description")
    instruction = _text(info, "c:instruction")
    expires = _text(info, "c:expires")

    parts = [f"DEFESA CIVIL: {headline}", f"Região: {', '.join(cities)}"]
    qualifiers = [value for value in (severity, urgency, certainty) if value and value != "Unknown"]
    if qualifiers:
        parts.append(" / ".join(qualifiers))
    if description:
        parts.append(description)
    if instruction:
        parts.append(f"Orientação: {instruction}")
    if expires:
        parts.append(f"Validade: {expires}")
    # Keep a compact radio message. The full resulting text is also saved as TXT.
    return " | ".join(" ".join(part.split()) for part in parts)[:220]


def _write_text_file(output_dir: Path, sender: str, identifier: str, message: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / _safe_filename(sender, identifier)
    temporary = destination.with_suffix(".txt.tmp")
    temporary.write_text(message + "\n", encoding="utf-8")
    temporary.replace(destination)


def process_feed(root: ET.Element, store: AlertStateStore, boundaries, now: datetime,
                 output_dir: Path, destination: str) -> dict[str, int]:
    active_entries, _ = active_alert_entries(root, now)
    active_keys = {
        key for _, alert in active_entries
        if (key := alert_key(alert)) is not None
    }
    entries = []
    for entry in root.findall("a:entry", NS):
        alert = entry.find(".//c:alert", NS)
        if alert is None:
            continue
        key = alert_key(alert)
        sent = parse_cap_datetime(_text(alert, "c:sent"))
        if key is None or sent is None:
            continue
        entries.append((sent, entry, alert, key))
    entries.sort(key=lambda item: item[0])

    counts = {"queued": 0, "recorded": 0, "duplicate": 0, "conflict": 0, "expired": 0}
    for _, entry, alert, key in entries:
        msg_type = _text(alert, "c:msgType")
        if _text(alert, "c:status") != "Actual" or msg_type not in {"Alert", "Update", "Cancel"}:
            continue

        sender, identifier = key
        refs = references_to_keys(_text(alert, "c:references"))
        infos = _current_info(alert, now)
        is_current = key in active_keys and bool(infos)
        cities: list[str] = []
        if is_current:
            matched, _, _ = intersecting_municipalities(alert, boundaries, MUNICIPIOS, now)
            cities = sorted(matched)
        matches_region = bool(cities)
        expires_at = _expiry_for_current_info(infos) if infos else None
        payload = {
            "title": _text(entry, "a:title") or _text(infos[0], "c:headline") if infos else _text(entry, "a:title"),
            "headline": _text(infos[0], "c:headline") if infos else "",
            "description": _text(infos[0], "c:description") if infos else "",
            "instruction": _text(infos[0], "c:instruction") if infos else "",
            "cities": cities,
            "sent": _text(alert, "c:sent"),
            "expires": expires_at,
        }

        message = None
        if msg_type in {"Alert", "Update"} and matches_region:
            message = _make_message(alert, cities, infos)
        elif msg_type == "Cancel":
            previous_titles = []
            for ref_sender, ref_identifier in refs:
                previous = store.get_alert(ref_sender, ref_identifier)
                if previous and previous["matches_region"]:
                    try:
                        previous_payload = json.loads(previous["payload_json"])
                        previous_titles.append(previous_payload.get("title") or ref_identifier)
                    except (TypeError, ValueError):
                        previous_titles.append(ref_identifier)
            if previous_titles:
                message = "DEFESA CIVIL: CANCELADO " + ", ".join(previous_titles)
                message = message[:220]

        if message:
            _write_text_file(output_dir, sender, identifier, message)

        result = store.process_cap_alert(
            sender=sender,
            identifier=identifier,
            sent=_text(alert, "c:sent"),
            msg_type=msg_type,
            payload=payload,
            references=sorted(refs),
            expires_at=expires_at,
            matches_region=matches_region,
            message_text=message,
            destination=destination,
            now=now,
        )
        counts[result] = counts.get(result, 0) + 1
    return counts


def dispatch_pending(store: AlertStateStore, host: str, port: int, channel_index: int) -> int:
    if not store.list_outbox(status="pending"):
        return 0

    from meshtastic.tcp_interface import TCPInterface

    interface = None
    sent_count = 0
    try:
        interface = TCPInterface(hostname=host, portNumber=port)
        while True:
            item = store.claim_next()
            if item is None:
                break
            try:
                interface.sendText(
                    item["message_text"],
                    destinationId=item["destination"],
                    wantAck=False,
                    channelIndex=channel_index,
                )
            except Exception as exc:
                store.mark_failed(item["id"], str(exc), retryable=True)
                LOG.warning("Falha ao entregar item %s via TCP: %s", item["id"], exc)
                break
            else:
                # This means the TCP client accepted the send request, not that
                # a radio or recipient confirmed reception.
                store.mark_sent(item["id"])
                sent_count += 1
    finally:
        if interface is not None:
            try:
                interface.close()
            except Exception:
                LOG.debug("Erro ao fechar interface TCP", exc_info=True)
    return sent_count


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Serviço contínuo CAP para Meshtastic via TCP.")
    parser.add_argument("--tcp-host", required=True, help="IP/nome do nó Meshtastic com TCP habilitado")
    parser.add_argument("--tcp-port", type=int, default=4403)
    parser.add_argument("--channel-index", type=int, default=0)
    parser.add_argument("--destination", default="^all")
    parser.add_argument("--database", default="data/mesh-alerts.sqlite3")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--interval", type=int, default=60, help="intervalo de consulta em segundos")
    parser.add_argument("--once", action="store_true", help="executa um ciclo e encerra")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    args = parser.parse_args(argv)
    if args.interval < 10:
        parser.error("--interval deve ser de pelo menos 10 segundos")

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    store = AlertStateStore(args.database)
    recovered = store.recover_interrupted_sends()
    if recovered:
        LOG.warning(
            "%s envio(s) interrompido(s) marcado(s) como incerto(s); "
            "não serão repetidos automaticamente", recovered
        )

    boundaries = None
    output_dir = Path(args.output_dir)
    try:
        while True:
            now = datetime.now(timezone.utc)
            try:
                if boundaries is None:
                    boundaries = carregar_limites()
                root = carregar_feed()
                counts = process_feed(root, store, boundaries, now, output_dir, args.destination)
                LOG.info("Feed CAP processado: %s", counts)
            except Exception:
                LOG.exception("Falha ao consultar/processar o feed CAP; estado persistido preservado")
            try:
                sent_count = dispatch_pending(store, args.tcp_host, args.tcp_port, args.channel_index)
                if sent_count:
                    LOG.info("%s mensagem(ns) aceita(s) pelo cliente TCP", sent_count)
            except Exception:
                LOG.exception("Falha na conexão Meshtastic TCP; pendências permanecem no banco")
            if args.once:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        LOG.info("Encerramento solicitado; estado persistido em %s", args.database)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
