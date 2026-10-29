#!/usr/bin/env python3
"""Valida alertas CAP contra os municípios definidos em config.yaml."""

import argparse
import gzip
import io
import json
import sys
import time
import urllib.error
import urllib.request

from .http_utils import open_http_url, validate_http_url
from defusedxml import ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from shapely.geometry import Polygon, shape
from shapely.validation import explain_validity

from .config import ConfigError, load_config, municipality_map
from .cap import NS, active_alert_entries, info_is_current, intersecting_municipalities, polygon_from_cap

MAX_HTTP_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_LOCAL_FILE_BYTES = 16 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 64 * 1024 * 1024


def _read_file_limited(path, max_bytes=None) -> bytes:
    """Read a local input file with the current configured byte limit."""
    if max_bytes is None:
        max_bytes = MAX_LOCAL_FILE_BYTES
    with Path(path).open("rb") as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"Arquivo {path} excede o limite de {max_bytes} bytes")
    return data


def _decompress_gzip_limited(data: bytes) -> bytes:
    """Decompress gzip input without allowing unbounded expansion."""
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
        result = stream.read(MAX_DECOMPRESSED_BYTES + 1)
    if len(result) > MAX_DECOMPRESSED_BYTES:
        raise ValueError(
            f"Conteúdo gzip excede o limite de {MAX_DECOMPRESSED_BYTES} bytes"
        )
    return result


def _default_municipalities():
    """Load defaults only when a caller actually needs them."""
    return municipality_map(load_config())


def baixar_com_retentativas(request, descricao, tentativas=3, timeout=25):
    ultimo_erro = None
    for tentativa in range(1, tentativas + 1):
        try:
            with open_http_url(request, timeout=timeout) as response:
                data = response.read(MAX_HTTP_RESPONSE_BYTES + 1)
                if len(data) > MAX_HTTP_RESPONSE_BYTES:
                    raise ValueError(
                        f"Resposta de {descricao} excede o limite de "
                        f"{MAX_HTTP_RESPONSE_BYTES} bytes"
                    )
                return data
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            ultimo_erro = exc
            if tentativa == tentativas:
                break
            espera = 2 ** tentativa
            print(
                f"Aviso: falha ao consultar {descricao} "
                f"(tentativa {tentativa}/{tentativas}): {exc}; nova tentativa em {espera}s",
                file=sys.stderr,
            )
            time.sleep(espera)
    raise RuntimeError(
        f"não foi possível consultar {descricao} após {tentativas} tentativas: {ultimo_erro}"
    ) from ultimo_erro


def limites_municipais_do_geojson(geojson, municipios=None):
    municipios = municipios or _default_municipalities()
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
        codigo = str(props.get("codarea", props.get("CD_MUN", props.get("id", ""))))
        if codigo not in municipios:
            continue
        geometry_data = feature.get("geometry")
        if not geometry_data:
            raise RuntimeError(f"Geometria ausente para {municipios[codigo]}")
        try:
            geometry = shape(geometry_data)
        except Exception as exc:
            raise RuntimeError(f"Geometria inválida para {municipios[codigo]}") from exc
        if geometry.is_empty or not geometry.is_valid:
            raise RuntimeError(f"Geometria vazia ou inválida para {municipios[codigo]}")
        limites[codigo] = geometry
    if len(limites) != len(municipios):
        faltam = [nome for code, nome in municipios.items() if code not in limites]
        raise RuntimeError(
            f"Malha municipal incompleta ({len(limites)}/{len(municipios)}). "
            f"Municípios não identificados: {', '.join(faltam)}"
        )
    return limites


def carregar_feed(caminho=None, feed_url=None, timeout=25, tentativas=3):
    if caminho:
        data = _read_file_limited(caminho)
    else:
        selected_feed_url = feed_url or load_config()["fontes"]["feed_cap"]
        validate_http_url(selected_feed_url)
        request = urllib.request.Request(selected_feed_url, headers={"User-Agent": "mesh-alerts/0.1"})
        data = baixar_com_retentativas(request, "feed CAP", tentativas=tentativas, timeout=timeout)
    if data[:2] == bytes((0x1f, 0x8b)):
        data = _decompress_gzip_limited(data)
    return ET.fromstring(data)


def carregar_limites(municipios, base_url, timeout=12, tentativas=2, cache_path=None):
    base_url = base_url.rstrip("/")
    validate_http_url(base_url)
    features = []

    if base_url.endswith("/municipios"):
        state_codes = sorted({codigo[:2] for codigo in municipios})
        state_base_url = base_url[: -len("/municipios")] + "/estados"
        for state_code in state_codes:
            url = (
                f"{state_base_url}/{state_code}"
                "?intrarregiao=municipio"
                "&formato=application/vnd.geo+json"
                "&qualidade=minima"
            )
            request = urllib.request.Request(
                url, headers={"User-Agent": "mesh-alerts/0.1"}
            )
            data = baixar_com_retentativas(
                request,
                f"malhas municipais da UF {state_code}",
                tentativas=tentativas,
                timeout=timeout,
            )
            if data[:2] == bytes((0x1f, 0x8b)):
                data = _decompress_gzip_limited(data)
            geojson = json.loads(data.decode("utf-8"))
            if not isinstance(geojson, dict) or geojson.get("type") != "FeatureCollection":
                raise RuntimeError(
                    f"Resposta do IBGE não é GeoJSON FeatureCollection para a UF {state_code}"
                )
            state_features = geojson.get("features")
            if not isinstance(state_features, list):
                raise RuntimeError(
                    f"GeoJSON sem lista de features para a UF {state_code}"
                )
            features.extend(state_features)
    else:
        for codigo, nome in municipios.items():
            url = f"{base_url}/{codigo}?formato=application/vnd.geo+json&qualidade=minima"
            request = urllib.request.Request(
                url, headers={"User-Agent": "mesh-alerts/0.1"}
            )
            data = baixar_com_retentativas(
                request,
                f"malha municipal de {nome}",
                tentativas=tentativas,
                timeout=timeout,
            )
            if data[:2] == bytes((0x1f, 0x8b)):
                data = _decompress_gzip_limited(data)
            geojson = json.loads(data.decode("utf-8"))
            if not isinstance(geojson, dict):
                raise RuntimeError(f"Resposta GeoJSON inválida para {nome}")
            if geojson.get("type") == "FeatureCollection":
                municipal_features = geojson.get("features")
                if not isinstance(municipal_features, list):
                    raise RuntimeError(f"GeoJSON sem lista de features para {nome}")
                features.extend(municipal_features)
            elif geojson.get("type") == "Feature":
                features.append(geojson)
            else:
                raise RuntimeError(f"Resposta do IBGE não é GeoJSON para {nome}")

    geojson = {"type": "FeatureCollection", "features": features}
    boundaries = limites_municipais_do_geojson(geojson, municipios)

    if cache_path is not None:
        selected_features = []
        for feature in features:
            if not isinstance(feature, dict):
                continue
            properties = feature.get("properties") or {}
            if not isinstance(properties, dict):
                continue
            code = str(
                properties.get("codarea", properties.get("CD_MUN", properties.get("id", "")))
            )
            if code in municipios:
                selected_features.append(feature)
        cache_geojson = {"type": "FeatureCollection", "features": selected_features}
        cache_file = Path(cache_path)
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            temporary_file = cache_file.with_suffix(cache_file.suffix + ".tmp")
            temporary_file.write_text(
                json.dumps(cache_geojson, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary_file.replace(cache_file)
        except OSError as exc:
            print(
                f"Aviso: não foi possível atualizar o cache da malha municipal "
                f"{cache_file}: {exc}",
                file=sys.stderr,
            )

    return boundaries


def carregar_limites_de_arquivo(caminho, municipios):
    try:
        geojson = json.loads(_read_file_limited(caminho).decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError(f"não foi possível ler a malha municipal local {caminho}: {exc}") from exc
    return limites_municipais_do_geojson(geojson, municipios)


def carregar_limites_com_cache(
    municipios, base_url, cache_path, timeout=12, tentativas=2
):
    """Load current IBGE boundaries, falling back to a previously validated local cache."""
    try:
        boundaries = carregar_limites(
            municipios,
            base_url,
            timeout=timeout,
            tentativas=tentativas,
            cache_path=cache_path,
        )
        return boundaries, False, None
    except Exception as live_error:
        cache_file = Path(cache_path)
        if not cache_file.is_file():
            raise
        cache_age_seconds = time.time() - cache_file.stat().st_mtime
        max_cache_age_seconds = 365 * 24 * 60 * 60
        if cache_age_seconds > max_cache_age_seconds:
            raise RuntimeError(
                f"IBGE indisponível ({live_error}) e cache municipal excede "
                f"a idade máxima de 365 dias: {cache_file}"
            ) from live_error
        try:
            boundaries = carregar_limites_de_arquivo(cache_file, municipios)
        except Exception as cache_error:
            raise RuntimeError(
                f"IBGE indisponível ({live_error}) e cache municipal inválido "
                f"({cache_file}: {cache_error})"
            ) from live_error
        return boundaries, True, str(live_error)


def avisos_poligonos_invalidos(alert, now=None):
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
                avisos.append(f"polígono CAP #{numero} não interpretável ({exc}); geometria ignorada")
                continue
            if not original.is_valid:
                repaired = polygon_from_cap(node.text or "")
                if repaired is None:
                    avisos.append(
                        f"polígono CAP #{numero} inválido ({explain_validity(original)}); "
                        "reparo falhou e geometria foi ignorada"
                    )
                else:
                    avisos.append(
                        f"polígono CAP #{numero} inválido ({explain_validity(original)}); "
                        "geometria reparada com make_valid"
                    )
            elif original.is_empty or original.area == 0:
                avisos.append(f"polígono CAP #{numero} degenerado; geometria ignorada")
    return avisos


def avisos_metadados_geograficos(alert, municipios=None):
    municipios = municipios or municipality_map(load_config())
    ufs_monitoradas = {codigo[:2] for codigo in municipios}
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
    parser = argparse.ArgumentParser(description="Cruza alertas CAP com os municípios configurados.")
    parser.add_argument("--config", default="config.yaml", help="arquivo YAML de configuração")
    parser.add_argument("--feed", help="caminho para um XML CAP local; sem esta opção, consulta a fonte configurada")
    parser.add_argument(
        "--boundaries",
        help="caminho para um GeoJSON municipal local; sem esta opção, consulta o IBGE",
    )
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        raise SystemExit(f"Configuração inválida: {exc}") from exc
    municipios = municipality_map(config)
    fontes, servico = config["fontes"], config["servico"]
    try:
        root = carregar_feed(
            args.feed, fontes["feed_cap"], servico["timeout_http_segundos"], servico["tentativas_feed"]
        )
    except (ET.ParseError, OSError, ValueError, RuntimeError) as exc:
        fonte = args.feed if args.feed else fontes["feed_cap"]
        raise SystemExit(f"Não foi possível carregar o feed CAP ({fonte}): {exc}") from exc
    try:
        if args.boundaries:
            limites = carregar_limites_de_arquivo(args.boundaries, municipios)
        else:
            limites = carregar_limites(
                municipios, fontes["malhas_ibge"], timeout=servico["timeout_http_segundos"],
                tentativas=servico["tentativas_malha_ibge"],
            )
    except Exception as exc:
        source = f"arquivo local {args.boundaries}" if args.boundaries else "IBGE"
        raise SystemExit(f"Falha ao carregar limites municipais ({source}): {exc}") from exc

    agora = datetime.now(timezone.utc)
    entries = root.findall("a:entry", NS)
    alertas, expirados = active_alert_entries(root, agora)
    achados, avisos_por_alerta = {}, {}
    circulos = alertas_com_geometria_problemática = total_geometrias_problematicas = 0
    for entry, alert in alertas:
        ident = alert.findtext("c:identifier", default="sem-id", namespaces=NS)
        titulo = entry.findtext("a:title", default="Sem título", namespaces=NS)
        avisos_geometria = avisos_poligonos_invalidos(alert, agora)
        if avisos_geometria:
            alertas_com_geometria_problemática += 1
            total_geometrias_problematicas += len(avisos_geometria)
        cidades, _, circulos_do_alerta = intersecting_municipalities(alert, limites, municipios, agora)
        circulos += circulos_do_alerta
        if cidades:
            achados[ident] = (titulo, sorted(cidades))
            avisos = avisos_geometria + avisos_metadados_geograficos(alert, municipios)
            if avisos:
                avisos_por_alerta[ident] = avisos

    print(f"Entradas no feed: {len(entries)}")
    print(f"Alertas vigentes examinados: {len(alertas)}")
    print(f"Alertas vigentes com interseção territorial: {len(achados)}")
    print(
        "Diagnóstico CAP: "
        f"{total_geometrias_problematicas} geometrias poligonais com problemas "
        f"em {alertas_com_geometria_problemática} alertas vigentes; detalhes listados apenas para alertas com interseção"
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
