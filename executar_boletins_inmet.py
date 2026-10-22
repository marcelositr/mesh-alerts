"""CLI de boletins INMET: simulação por padrão; rádio somente com --send explícito."""

from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from mesh_alerts_config import carregar_configuracao
from mesh_alerts_inmet_scheduler import executar_boletim, periodo_agendado


def _transport_tcp(host: str, port: int, channel: int, destination: str):
    """Create a lazy TCP sender; this path is reachable only with --send."""
    try:
        from meshtastic.tcp_interface import TCPInterface
    except ImportError as exc:
        raise SystemExit(
            "Para transmitir via TCP, instale o pacote meshtastic no ambiente virtual."
        ) from exc

    interface = TCPInterface(hostname=host, portNumber=port)

    def send(message: str) -> None:
        interface.sendText(
            message, destinationId=destination, wantAck=False, channelIndex=channel
        )

    return interface, send


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Agenda boletins de previsão do INMET para os municípios configurados."
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--database", default="data/inmet-bulletins.sqlite3")
    parser.add_argument("--send", action="store_true", help="habilita envio real; sem esta opção, apenas simula")
    parser.add_argument("--tcp-host", help="host do dispositivo Meshtastic TCP (obrigatório com --send)")
    parser.add_argument("--tcp-port", type=int, default=4403)
    parser.add_argument("--channel-index", type=int, default=0)
    parser.add_argument("--destination", default="^all")
    parser.add_argument("--loop", action="store_true", help="verifica horários continuamente")
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = carregar_configuracao(args.config)
    interface = None
    sender = None
    if args.send:
        if not args.tcp_host:
            parser.error("--tcp-host é obrigatório com --send")
        interface, sender = _transport_tcp(
            args.tcp_host, args.tcp_port, args.channel_index, args.destination
        )

    timezone = ZoneInfo(config["inmet"].get("fuso_horario", "America/Sao_Paulo"))
    last_slot = None
    try:
        while True:
            now = datetime.now(timezone)
            period = periodo_agendado(config, now)
            slot = (now.date().isoformat(), now.strftime("%H:%M"), period)
            if period and slot != last_slot:
                logging.getLogger(__name__).info(
                    "%s boletim INMET para %s municípios",
                    "Transmitindo" if args.send else "Simulando",
                    len(config["municipios"]),
                )
                counts = executar_boletim(
                    config,
                    args.database,
                    period,
                    now=now,
                    enviar=sender,
                    simular=not args.send,
                )
                logging.getLogger(__name__).info("Resultado do ciclo: %s", counts)
                last_slot = slot
            if not args.loop:
                return 0
            time.sleep(10)
    finally:
        if interface is not None:
            try:
                interface.close()
            except Exception:
                logging.getLogger(__name__).debug("Falha ao fechar TCPInterface", exc_info=True)


if __name__ == "__main__":
    raise SystemExit(main())
