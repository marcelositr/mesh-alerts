#!/usr/bin/env python3
"""Cruza alertas CAP do IDAP com os seis municípios da região de Ituverava."""

import argparse
import gzip
import json
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from shapely.geometry import Polygon, shape
from shapely.validation import explain_validity

from mesh_alerts_cap import (
    NS,
    active_alert_entries,
    info_is_current,
    intersecting_municipalities,
    polygon_from_cap,
)

FEED_URL = "https://idapfile.mdr.gov.br/idap/api/rss/cap"
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
    if not isinstance(geojson, dict) or geojson.get("type") != "FeatureCollection":
        raise RuntimeError("Resposta do IBGE não é um GeoJSON FeatureCollection")
    features = geojson.get("features")
    if not isinstance(features, list):
        raise RuntimeError("GeoJSON do IBGE não contém uma lista de features")

    limites = {}
    for feature in features:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties") or {}
        if not isinstance(props, dict):
            continue
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
        except Exception as exc:
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


def carregar_feed(caminho=None):
    """Carrega XML CAP de um arquivo local ou do feed oficial."""
    if caminho:
        data = Path(caminho).read_bytes()
    else:
        request = urllib.request.Request(
            FEED_URL, headers={"User-Agent": "mesh-alerts/0.1"}
        )
        with urllib.request.urlopen(request, timeout=45) as response:
            data = response.read()
    if data[:2] == bytes((0x1f, 0x8b)):
        data = gzip.decompress(data)
    return ET.fromstring(data)


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


def avisos_poligonos_invalidos(alert, now=None):
    """Diagnostica polígonos CAP malformados ou inválidos antes do reparo."""
    avisos = []
    numero = 0
    for info in alert.findall("c:info", NS):
        if now is not None and not info_is_current(info, now)[0]:
            continue
        for node in info.findall(".//c:polygon", NS):
            numero += 1
            coords = []
            try:
                pairs = (node.text or "").split()
                if len(pairs) < 4:
                    raise ValueError("menos de quatro pares de coordenadas")
                for pair in pairs:
                    partes = pair.split(",")
                    if len(partes) != 2:
                        raise ValueError("par de coordenadas inválido")
                    lat, lon = (float(parte) for parte in partes)
                    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                        raise ValueError("coordenada fora dos limites WGS84")
                    coords.append((lon, lat))
                if coords[0] != coords[-1]:
                    raise ValueError("anel de coordenadas não fechado")
                original = Polygon(coords)
            except (ValueError, TypeError) as exc:
                avisos.append(
                    f"polígono CAP #{numero} não interpretável ({exc}); "
                    "geometria ignorada no cruzamento"
                )
                continue

            if not original.is_valid:
                repaired = polygon_from_cap(node.text or "")
                if repaired is None:
                    avisos.append(
                        f"polígono CAP #{numero} inválido "
                        f"({explain_validity(original)}); reparo falhou e geometria foi ignorada"
                    )
                else:
                    avisos.append(
                        f"polígono CAP #{numero} inválido "
                        f"({explain_validity(original)}); geometria reparada com make_valid"
                    )
            elif original.is_empty or original.area == 0:
                avisos.append(
                    f"polígono CAP #{numero} degenerado; geometria ignorada no cruzamento"
                )
    return avisos

def avisos_metadados_geograficos(alert):
    """Sinaliza metadados IBGE incompatíveis com os estados monitorados, sem filtrar alertas."""
    ufs_monitoradas = {codigo[:2] for codigo in MUNICIPIOS}
    avisos = []

    for area in alert.findall(".//c:area", NS):
        area_desc = area.findtext("c:areaDesc", default="", namespaces=NS).strip()
        codigos = []
        for geocode in area.findall("c:geocode", NS):
            nome = geocode.findtext("c:valueName", default="", namespaces=NS).strip()
            valor = geocode.findtext("c:value", default="", namespaces=NS).strip()
            if nome.upper() == "IBGE" and valor.isdigit() and len(valor) >= 2:
                codigos.append(valor)

        ufs_codificadas = {codigo[:2] for codigo in codigos}
        if codigos and ufs_codificadas.isdisjoint(ufs_monitoradas):
            avisos.append(
                f"metadados administrativos: areaDesc={area_desc or 'não informado'}; "
                f"códigos IBGE={', '.join(codigos)} apontam para UFs fora do conjunto "
                f"monitorado ({', '.join(sorted(ufs_monitoradas))}); isso não invalida "
                "a interseção geométrica com municípios vizinhos"
            )

    return avisos

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Cruza alertas CAP atuais com seis municípios da região de Ituverava."
    )
    parser.add_argument(
        "--feed",
        help="caminho para um XML CAP local; sem esta opção, consulta o feed oficial",
    )
    args = parser.parse_args(argv)

    try:
        root = carregar_feed(args.feed)
    except (ET.ParseError, OSError, ValueError, RuntimeError) as exc:
        fonte = args.feed if args.feed else FEED_URL
        raise SystemExit(f"Não foi possível carregar o feed CAP ({fonte}): {exc}") from exc

    try:
        limites = carregar_limites()
    except Exception as exc:
        raise SystemExit(f"Falha ao carregar limites municipais do IBGE: {exc}") from exc

    agora = datetime.now(timezone.utc)
    entries = root.findall("a:entry", NS)
    alertas, expirados = active_alert_entries(root, agora)

    achados = {}
    avisos_por_alerta = {}
    circulos = 0
    alertas_com_geometria_problemática = 0
    total_geometrias_problematicas = 0
    for entry, alert in alertas:
        ident = alert.findtext("c:identifier", default="sem-id", namespaces=NS)
        titulo = entry.findtext("a:title", default="Sem título", namespaces=NS)
        avisos_geometria = avisos_poligonos_invalidos(alert, agora)
        if avisos_geometria:
            alertas_com_geometria_problemática += 1
            total_geometrias_problematicas += len(avisos_geometria)

        cidades, _expirados_do_alerta, circulos_do_alerta = intersecting_municipalities(
            alert, limites, MUNICIPIOS, agora
        )
        circulos += circulos_do_alerta
        if cidades:
            achados[ident] = (titulo, sorted(cidades))
            avisos = list(avisos_geometria)
            avisos.extend(avisos_metadados_geograficos(alert))
            if avisos:
                avisos_por_alerta[ident] = avisos

    print(f"Entradas no feed: {len(entries)}")
    print(f"Alertas vigentes examinados: {len(alertas)}")
    print(f"Alertas vigentes com interseção territorial: {len(achados)}")
    print(
        "Diagnóstico CAP: "
        f"{total_geometrias_problematicas} geometrias poligonais com problemas "
        f"em {alertas_com_geometria_problemática} alertas vigentes; "
        "detalhes listados apenas para alertas com interseção"
    )
    for ident, (titulo, cidades) in sorted(achados.items()):
        print(f"- {titulo} [{ident}]: {', '.join(cidades)}")
        for aviso in avisos_por_alerta.get(ident, []):
            print(f"  NOTA DE METADADOS/GEOMETRIA: {aviso}")
    print(f"Blocos de informação expirados ignorados: {expirados}")
    print(f"Áreas circulares CAP analisadas: {circulos}")
    print("Nota: círculos são aproximados por polígonos geodésicos; limites municipais vêm do IBGE.")


if __name__ == "__main__":
    main()
