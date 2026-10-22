#!/usr/bin/env python3
"""Cruza alertas CAP do IDAP com os seis municípios da região de Ituverava."""

import gzip
import json
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from shapely.geometry import shape

from mesh_alerts_cap import (
    NS,
    active_alert_entries,
    intersecting_municipalities,
)

FEED = Path("/tmp/defesa-civil-cap.xml")
MUNICIPIOS = {
    "3503000": "Aramina",
    "3508207": "Buritizal",
    "3517703": "Guará",
    "3520103": "Igarapava",
    "3524105": "Ituverava",
    "3529708": "Miguelópolis",
}
MALHAS_URL = (
    "https://servicodados.ibge.gov.br/api/v3/malhas/estados/35"
    "?intrarregiao=municipio&formato=application/vnd.geo+json&qualidade=minima"
)


def limites_municipais_do_geojson(geojson):
    """Extrai e valida os seis limites municipais de um GeoJSON do IBGE."""
    limites = {}
    for feature in geojson.get("features", []):
        props = feature.get("properties") or {}
        codigo = str(
            props.get("codarea", props.get("CD_MUN", props.get("id", "")))
        )
        if codigo not in MUNICIPIOS:
            continue
        geometry_data = feature.get("geometry")
        if not geometry_data:
            raise RuntimeError(f"Geometria ausente para {MUNICIPIOS[codigo]}")
        try:
            geometry = shape(geometry_data)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Geometria inválida para {MUNICIPIOS[codigo]}"
            ) from exc
        if geometry.is_empty or not geometry.is_valid:
            raise RuntimeError(f"Geometria vazia ou inválida para {MUNICIPIOS[codigo]}")
        limites[codigo] = geometry

    if len(limites) != len(MUNICIPIOS):
        faltam = [nome for code, nome in MUNICIPIOS.items() if code not in limites]
        raise RuntimeError(
            f"Malha municipal incompleta ({len(limites)}/6). "
            f"Municípios não identificados: {', '.join(faltam)}"
        )
    return limites


def carregar_limites():
    request = urllib.request.Request(
        MALHAS_URL, headers={"User-Agent": "mesh-alerts/0.1"}
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        data = response.read()
    if data[:2] == bytes((0x1f, 0x8b)):
        data = gzip.decompress(data)
    geojson = json.loads(data.decode("utf-8"))
    return limites_municipais_do_geojson(geojson)


def main():
    if not FEED.is_file():
        raise SystemExit(
            f"Feed não encontrado em {FEED}. Baixe o XML CAP para esse caminho "
            "ou ajuste a constante FEED no script."
        )

    try:
        root = ET.parse(FEED).getroot()
    except (ET.ParseError, OSError) as exc:
        raise SystemExit(f"Não foi possível ler o XML CAP: {exc}") from exc

    try:
        limites = carregar_limites()
    except Exception as exc:
        raise SystemExit(f"Falha ao carregar limites municipais do IBGE: {exc}") from exc

    agora = datetime.now(timezone.utc)
    entries = root.findall("a:entry", NS)
    alertas, expirados = active_alert_entries(root, agora)

    achados = {}
    circulos = 0
    for entry, alert in alertas:
        cidades, _expirados_do_alerta, circulos_do_alerta = intersecting_municipalities(
            alert, limites, MUNICIPIOS, agora
        )
        circulos += circulos_do_alerta
        if cidades:
            ident = alert.findtext("c:identifier", default="sem-id", namespaces=NS)
            titulo = entry.findtext("a:title", default="Sem título", namespaces=NS)
            achados[ident] = (titulo, sorted(cidades))

    print(f"Entradas no feed: {len(entries)}")
    print(f"Alertas vigentes com interseção territorial: {len(achados)}")
    for ident, (titulo, cidades) in sorted(achados.items()):
        print(f"- {titulo} [{ident}]: {', '.join(cidades)}")
    print(f"Blocos de informação expirados ignorados: {expirados}")
    print(f"Áreas circulares CAP analisadas: {circulos}")
    print("Nota: círculos são aproximados por polígonos geodésicos; limites municipais vêm do IBGE.")


if __name__ == "__main__":
    main()
