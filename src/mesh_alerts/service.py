#!/usr/bin/env python3
"""Serviço contínuo CAP para Meshtastic, configurado por config.yaml."""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from .cap import (
    NS, active_alert_entries, alert_key, info_is_current, intersecting_municipalities,
    parse_cap_datetime, references_to_keys,
)
from .config import ConfigError, load_config, municipality_map
from .state import AlertStateStore
from .region import carregar_feed, carregar_limites_com_cache


LOG = logging.getLogger("mesh-alerts")


def _text(element: ET.Element, path: str, default: str = "") -> str:
    return element.findtext(path, default=default, namespaces=NS).strip()


def _current_info(alert: ET.Element, now: datetime) -> list[ET.Element]:
    return [info for info in alert.findall("c:info", NS) if info_is_current(info, now)[0]]


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


def _actionable_instruction(instruction: str, headline: str, description: str, limit: int) -> str | None:
    """Return concise, actionable instruction sentences absent from the alert text."""
    if not instruction:
        return None
    source = " ".join((headline, description)).casefold()
    sentences = re.split(r"(?<=[.!?])\s+|\n+", instruction.strip())
    actions = re.compile(
        r"\b(evite|evitar|não|nao|beba|busque|procure|mantenha|afaste|ligue|acione|"
        r"permaneça|permanece|siga|fique|retire|vá|va|saia|proteja|avoid|seek|call|"
        r"stay|follow|keep|do not)\b",
        re.IGNORECASE,
    )
    selected = []
    seen = set()
    for sentence in sentences:
        sentence = re.sub(r"https?://\S+", "", sentence).strip(" \t\r\n-•")
        sentence = re.sub(r"\s+", " ", sentence).strip()
        normalized = sentence.casefold().strip(" .;:")
        if len(sentence) < 20 or not actions.search(sentence) or not normalized:
            continue
        if normalized in source or normalized in seen:
            continue
        if any(term in normalized for term in (
            "saiba mais", "classificação do alerta", "classificacao do alerta",
            "significado das cores", "defesa civil estadual",
        )):
            continue
        seen.add(normalized)
        selected.append(sentence)
    if not selected:
        return None
    return ("Orientação: " + " ".join(selected))[:limit]


def _make_messages(
    alert: ET.Element, cities: list[str], infos: list[ET.Element],
    limit: int = 220, region_uf: str = "",
) -> list[str]:
    """Build separate identity, headline, classification, and optional guidance messages."""
    info = infos[0]
    headline = _text(info, "c:headline") or _text(info, "c:event") or "Alerta da Defesa Civil"
    severity, urgency, certainty = (
        _text(info, f"c:{field}") for field in ("severity", "urgency", "certainty")
    )
    description = _text(info, "c:description")
    instruction = _text(info, "c:instruction")

    # Remove only the redundant leading provider label; preserve the remaining headline.
    prefix = "Defesa Civil:"
    if headline.casefold().startswith(prefix.casefold()):
        headline = headline[len(prefix):].lstrip()

    labels = [f"{city.upper()}/{region_uf.upper()}" if region_uf else city.upper() for city in cities]
    messages = [
        f"MESH ALERTS | {', '.join(labels)}"[:limit],
        headline[:limit],
    ]
    qualifiers = [value for value in (severity, urgency, certainty) if value and value.casefold() != "unknown"]
    if qualifiers:
        messages.append(" / ".join(qualifiers)[:limit])

    guidance = _actionable_instruction(instruction, headline, description, limit)
    if guidance:
        messages.append(guidance)
    return messages


def _write_text_file(
    output_dir: Path, sender: str, identifier: str, message: str, message_index: int = 1,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    base = Path(_safe_filename(sender, identifier))
    destination = output_dir / f"{base.stem}-{message_index:02d}{base.suffix}"
    temporary = destination.with_suffix(".txt.tmp")
    temporary.write_text(message + "\n", encoding="utf-8")
    temporary.replace(destination)


def process_feed(
    root: ET.Element, store: AlertStateStore, boundaries, now: datetime,
    output_dir: Path, destination: str, municipalities: dict[str, str] | None = None,
    message_limit: int = 220, region_uf: str = "",
) -> dict[str, int]:
    if municipalities is None:
        municipalities = municipality_map(load_config())
    active_entries, _ = active_alert_entries(root, now)
    active_keys = {key for _, alert in active_entries if (key := alert_key(alert)) is not None}
    entries = []
    for entry in root.findall("a:entry", NS):
        alert = entry.find(".//c:alert", NS)
        if alert is None:
            continue
        key = alert_key(alert)
        sent = parse_cap_datetime(_text(alert, "c:sent"))
        if key is not None and sent is not None:
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
        all_infos = alert.findall("c:info", NS)
        is_current = key in active_keys and bool(infos)
        cities: list[str] = []
        if is_current:
            matched, _, _ = intersecting_municipalities(alert, boundaries, municipalities, now)
            cities = sorted(matched)
        matches_region = bool(cities)
        expires_at = _expiry_for_current_info(all_infos) if all_infos else None
        payload = {
            "title": (_text(entry, "a:title") or _text(infos[0], "c:headline")) if infos else _text(entry, "a:title"),
            "headline": _text(infos[0], "c:headline") if infos else "",
            "description": _text(infos[0], "c:description") if infos else "",
            "instruction": _text(infos[0], "c:instruction") if infos else "",
            "event": _text(infos[0], "c:event") if infos else "",
            "severity": _text(infos[0], "c:severity") if infos else "",
            "urgency": _text(infos[0], "c:urgency") if infos else "",
            "certainty": _text(infos[0], "c:certainty") if infos else "",
            "senderName": _text(infos[0], "c:senderName") if infos else "",
            "onset": _text(infos[0], "c:onset") if infos else "",
            "msgType": msg_type, "identifier": identifier,
            "cities": cities, "sent": _text(alert, "c:sent"), "expires": expires_at,
        }
        message: str | list[str] | None = None
        if msg_type in {"Alert", "Update"} and matches_region:
            message = _make_messages(alert, cities, infos, message_limit, region_uf)
        # CAP Cancel messages update internal lifecycle state and invalidate pending
        # notifications, but intentionally do not generate a Meshtastic broadcast.
        if isinstance(message, list):
            for index, part in enumerate(message, start=1):
                _write_text_file(output_dir, sender, identifier, part, index)
        elif message:
            _write_text_file(output_dir, sender, identifier, message)
        result = store.process_cap_alert(
            sender=sender, identifier=identifier, sent=_text(alert, "c:sent"),
            msg_type=msg_type, payload=payload, references=sorted(refs),
            expires_at=expires_at, matches_region=matches_region, is_current=is_current,
            message_text=message, destination=destination, now=now,
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
                interface.sendText(item["message_text"], destinationId=item["destination"],
                                   wantAck=False, channelIndex=channel_index)
            except Exception as exc:
                store.mark_uncertain(item["id"], str(exc))
                LOG.warning("Resultado incerto para o item %s via TCP: %s", item["id"], exc)
                break
            else:
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
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", default="config.yaml")
    pre_args, _ = pre_parser.parse_known_args(argv)
    try:
        config = load_config(pre_args.config)
    except ConfigError as exc:
        raise SystemExit(f"Configuração inválida: {exc}") from exc

    meshtastic = config["meshtastic"]
    service = config["servico"]
    sources = config["fontes"]
    parser = argparse.ArgumentParser(description="Serviço contínuo CAP para Meshtastic via TCP.")
    parser.add_argument("--config", default="config.yaml", help="arquivo YAML de configuração")
    parser.add_argument("--tcp-host", default=None, help="sobrescreve meshtastic.host")
    parser.add_argument("--tcp-port", type=int, default=None, help="sobrescreve meshtastic.porta")
    parser.add_argument("--channel-index", type=int, default=None, help="sobrescreve meshtastic.canal")
    parser.add_argument("--destination", default=None, help="sobrescreve meshtastic.destino")
    parser.add_argument("--database", default=None, help="sobrescreve servico.banco")
    parser.add_argument("--output-dir", default=None, help="sobrescreve servico.diretorio_txt")
    parser.add_argument("--interval", type=int, default=None, help="sobrescreve servico.intervalo_segundos")
    parser.add_argument("--once", action="store_true", help="executa um ciclo e encerra")
    parser.add_argument("--resolve-uncertain", type=int, help="ID de envio incerto a resolver e encerrar")
    parser.add_argument("--resolution", choices=("sent", "retry"), help="resultado confirmado ou decisão de tentar novamente")
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default=None)
    args = parser.parse_args(argv)

    host = args.tcp_host or os.environ.get("MESH_ALERTS_TCP_HOST") or meshtastic["host"]
    port = args.tcp_port if args.tcp_port is not None else int(os.environ.get("MESH_ALERTS_TCP_PORT", meshtastic["porta"]))
    channel = args.channel_index if args.channel_index is not None else int(os.environ.get("MESH_ALERTS_CHANNEL_INDEX", meshtastic["canal"]))
    destination = args.destination or os.environ.get("MESH_ALERTS_DESTINATION") or meshtastic["destino"]
    database = args.database or os.environ.get("MESH_ALERTS_DATABASE") or service["banco"]
    output_dir = args.output_dir or os.environ.get("MESH_ALERTS_OUTPUT_DIR") or service["diretorio_txt"]
    interval = args.interval if args.interval is not None else int(os.environ.get("MESH_ALERTS_INTERVAL", service["intervalo_segundos"]))
    log_level = args.log_level or service["nivel_log"]
    if not 1 <= port <= 65535:
        parser.error("porta TCP deve estar entre 1 e 65535")
    if channel < 0:
        parser.error("índice do canal não pode ser negativo")
    if interval < 10:
        parser.error("--interval/servico.intervalo_segundos deve ser de pelo menos 10 segundos")
    if args.resolve_uncertain is None and not host:
        parser.error("meshtastic.host é obrigatório para executar o serviço")
    if args.resolve_uncertain is not None and not args.resolution:
        parser.error("--resolution sent|retry é obrigatório com --resolve-uncertain")
    logging.basicConfig(level=getattr(logging, log_level), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    store = AlertStateStore(database)
    if args.resolve_uncertain is not None:
        store.resolve_uncertain(args.resolve_uncertain, resolution=args.resolution)
        LOG.info("Envio incerto %s resolvido como %s", args.resolve_uncertain, args.resolution)
        return 0
    recovered = store.recover_interrupted_sends()
    if recovered:
        LOG.warning("%s envio(s) interrompido(s) marcado(s) como incerto(s); não serão repetidos automaticamente", recovered)

    municipalities = municipality_map(config)
    boundaries = None
    output_path = Path(output_dir)
    try:
        while True:
            now = datetime.now(timezone.utc)
            try:
                if boundaries is None:
                    boundary_cache = Path(database).parent / "ibge-municipal-boundaries.geojson"
                    boundaries, used_cache, cache_reason = carregar_limites_com_cache(
                        municipalities,
                        sources["malhas_ibge"],
                        boundary_cache,
                        timeout=service["timeout_http_segundos"],
                        tentativas=service["tentativas_malha_ibge"],
                    )
                    if used_cache:
                        LOG.warning(
                            "API do IBGE indisponível (%s); usando cache municipal validado em %s",
                            cache_reason,
                            boundary_cache,
                        )
                root = carregar_feed(
                    None, sources["feed_cap"], service["timeout_http_segundos"],
                    service["tentativas_feed"],
                )
                counts = process_feed(
                    root, store, boundaries, now, output_path, destination,
                    municipalities, service["limite_mensagem_caracteres"], config["regiao"]["uf"],
                )
                LOG.info("Feed CAP processado: %s", counts)
            except Exception:
                LOG.exception("Falha ao consultar/processar o feed CAP; estado persistido preservado")
            try:
                invalidated = store.invalidate_stale_pending(now=datetime.now(timezone.utc))
                if invalidated:
                    LOG.info("%s mensagem(ns) pendente(s) invalidada(s) por expiração/substituição", invalidated)
                sent_count = dispatch_pending(store, host, port, channel)
                if sent_count:
                    LOG.info("%s mensagem(ns) aceita(s) pelo cliente TCP", sent_count)
            except Exception:
                LOG.exception("Falha na conexão Meshtastic TCP; pendências permanecem no banco")
            if args.once:
                return 0
            time.sleep(interval)
    except KeyboardInterrupt:
        LOG.info("Encerramento solicitado; estado persistido em %s", database)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
