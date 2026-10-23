#!/usr/bin/env python3
"""Match IDAP CAP alerts against the six municipalities in the Ituverava region."""

import argparse
import gzip
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from shapely.geometry import Polygon
from shapely.validation import explain_validity

from mesh_alerts_boundaries import (
    MUNICIPALITIES,
    download_with_retries,
    load_municipal_boundaries,
)

from mesh_alerts_cap import (
    XML_NAMESPACES,
    get_active_alert_entries,
    is_info_current,
    find_intersecting_municipalities,
    parse_cap_polygon,
)

CAP_FEED_URL = "https://idapfile.mdr.gov.br/idap/api/rss/cap"


def load_cap_feed(path: str | Path | None = None) -> ET.Element:
    """Load CAP XML from a local file or the official feed."""
    if path:
        data = Path(path).read_bytes()
    else:
        request = urllib.request.Request(
            CAP_FEED_URL, headers={"User-Agent": "mesh-alerts/0.1"}
        )
        data = download_with_retries(request, "feed CAP")
    if data[:2] == bytes((0x1f, 0x8b)):
        data = gzip.decompress(data)
    return ET.fromstring(data)


def diagnose_invalid_polygons(
    alert: ET.Element, now: datetime | None = None
) -> list[str]:
    """Diagnose malformed or invalid CAP polygons before geometry repair."""
    warnings = []
    number = 0
    for info in alert.findall("c:info", XML_NAMESPACES):
        if now is not None and not is_info_current(info, now)[0]:
            continue
        for node in info.findall(".//c:polygon", XML_NAMESPACES):
            number += 1
            coordinates = []
            try:
                pairs = (node.text or "").split()
                if len(pairs) < 4:
                    raise ValueError("menos de quatro pares de coordenadas")
                for pair in pairs:
                    parts = pair.split(",")
                    if len(parts) != 2:
                        raise ValueError("par de coordenadas inválido")
                    lat, lon = (float(part) for part in parts)
                    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                        raise ValueError("coordenada fora dos limites WGS84")
                    coordinates.append((lon, lat))
                if coordinates[0] != coordinates[-1]:
                    raise ValueError("anel de coordenadas não fechado")
                original = Polygon(coordinates)
            except (ValueError, TypeError) as exc:
                warnings.append(
                    f"polígono CAP #{number} não interpretável ({exc}); "
                    "geometria ignorada no cruzamento"
                )
                continue

            if not original.is_valid:
                repaired = parse_cap_polygon(node.text or "")
                if repaired is None:
                    warnings.append(
                        f"polígono CAP #{number} inválido "
                        f"({explain_validity(original)}); reparo falhou e geometria foi ignorada"
                    )
                else:
                    warnings.append(
                        f"polígono CAP #{number} inválido "
                        f"({explain_validity(original)}); geometria reparada com make_valid"
                    )
            elif original.is_empty or original.area == 0:
                warnings.append(
                    f"polígono CAP #{number} degenerado; geometria ignorada no cruzamento"
                )
    return warnings

def diagnose_geographic_metadata(alert: ET.Element) -> list[str]:
    """Report IBGE metadata that conflicts with monitored states without filtering alerts."""
    monitored_states = {code[:2] for code in MUNICIPALITIES}
    warnings = []

    for area in alert.findall(".//c:area", XML_NAMESPACES):
        area_description = area.findtext(
            "c:areaDesc", default="", namespaces=XML_NAMESPACES
        ).strip()
        codes = []
        for geocode in area.findall("c:geocode", XML_NAMESPACES):
            name = geocode.findtext("c:valueName", default="", namespaces=XML_NAMESPACES).strip()
            value = geocode.findtext("c:value", default="", namespaces=XML_NAMESPACES).strip()
            if name.upper() == "IBGE" and value.isdigit() and len(value) >= 2:
                codes.append(value)

        encoded_states = {code[:2] for code in codes}
        if codes and encoded_states.isdisjoint(monitored_states):
            warnings.append(
                f"metadados administrativos: areaDesc={area_description or 'não informado'}; "
                f"códigos IBGE={', '.join(codes)} apontam para UFs fora do conjunto "
                f"monitorado ({', '.join(sorted(monitored_states))}); isso não invalida "
                "a interseção geométrica com municípios vizinhos"
            )

    return warnings

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Cruza alertas CAP atuais com seis municípios da região de Ituverava."
    )
    parser.add_argument(
        "--feed",
        help="caminho para um XML CAP local; sem esta opção, consulta o feed oficial",
    )
    parser.add_argument(
        "--limites",
        dest="boundaries",
        help="caminho para um GeoJSON municipal local; sem esta opção, consulta o IBGE",
    )
    args = parser.parse_args(argv)

    try:
        root = load_cap_feed(args.feed)
    except (ET.ParseError, OSError, ValueError, RuntimeError) as exc:
        source = args.feed if args.feed else CAP_FEED_URL
        raise SystemExit(f"Não foi possível carregar o feed CAP ({source}): {exc}") from exc

    try:
        boundaries = load_municipal_boundaries(args.boundaries)
    except Exception as exc:
        raise SystemExit(f"Falha ao carregar limites municipais do IBGE: {exc}") from exc

    now = datetime.now(timezone.utc)
    entries = root.findall("a:entry", XML_NAMESPACES)
    alerts, expired_count = get_active_alert_entries(root, now)

    matches = {}
    warnings_by_alert = {}
    circle_count = 0
    alerts_with_problematic_geometry = 0
    problematic_geometry_count = 0
    for entry, alert in alerts:
        identifier = alert.findtext("c:identifier", default="sem-id", namespaces=XML_NAMESPACES)
        title = entry.findtext("a:title", default="Sem título", namespaces=XML_NAMESPACES)
        geometry_warnings = diagnose_invalid_polygons(alert, now)
        if geometry_warnings:
            alerts_with_problematic_geometry += 1
            problematic_geometry_count += len(geometry_warnings)

        cities, _, alert_circle_count = find_intersecting_municipalities(
            alert, boundaries, MUNICIPALITIES, now
        )
        circle_count += alert_circle_count
        if cities:
            matches[identifier] = (title, sorted(cities))
            warnings = list(geometry_warnings)
            warnings.extend(diagnose_geographic_metadata(alert))
            if warnings:
                warnings_by_alert[identifier] = warnings

    print(f"Entradas no feed: {len(entries)}")
    print(f"Alertas vigentes examinados: {len(alerts)}")
    print(f"Alertas vigentes com interseção territorial: {len(matches)}")
    print(
        "Diagnóstico CAP: "
        f"{problematic_geometry_count} geometrias poligonais com problemas "
        f"em {alerts_with_problematic_geometry} alertas vigentes; "
        "detalhes listados apenas para alertas com interseção"
    )
    for identifier, (title, cities) in sorted(matches.items()):
        print(f"- {title} [{identifier}]: {', '.join(cities)}")
        for warning in warnings_by_alert.get(identifier, []):
            print(f"  NOTA DE METADADOS/GEOMETRIA: {warning}")
    print(f"Blocos de informação expirados ignorados: {expired_count}")
    print(f"Áreas circulares CAP analisadas: {circle_count}")
    boundary_source = (
        f"GeoJSON local ({args.boundaries})"
        if args.boundaries
        else "API de malhas do IBGE"
    )
    print(
        "Nota: círculos são aproximados por polígonos geodésicos; "
        f"limites municipais vêm de: {boundary_source}."
    )


if __name__ == "__main__":
    main()
