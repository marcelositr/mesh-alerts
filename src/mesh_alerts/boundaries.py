"""Retrieve and validate municipal boundaries used for CAP alert matching."""

from __future__ import annotations

import gzip
import json
import sys
import time
import urllib.error
import urllib.request

from .http_utils import open_http_url
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

MUNICIPALITIES = {
    "3503000": "Aramina",
    "3508207": "Buritizal",
    "3517703": "Guará",
    "3520103": "Igarapava",
    "3524105": "Ituverava",
    "3529708": "Miguelópolis",
}
IBGE_BOUNDARIES_BASE_URL = "https://servicodados.ibge.gov.br/api/v3/malhas/municipios"


def download_with_retries(
    request: urllib.request.Request,
    description: str,
    attempts: int = 3,
    timeout: float = 25,
) -> bytes:
    """Download an external resource with retries for transient failures."""
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            with open_http_url(request, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt == attempts:
                break
            delay = 2 ** attempt
            print(
                f"Aviso: falha ao consultar {description} "
                f"(tentativa {attempt}/{attempts}): {exc}; nova tentativa em {delay}s",
                file=sys.stderr,
            )
            time.sleep(delay)
    raise RuntimeError(
        f"não foi possível consultar {description} após {attempts} tentativas: {last_error}"
    ) from last_error


def load_municipal_boundaries_from_geojson(
    geojson: dict[str, Any],
) -> dict[str, BaseGeometry]:
    """Extract and validate the six municipal boundaries from IBGE GeoJSON."""
    if not isinstance(geojson, dict) or geojson.get("type") != "FeatureCollection":
        raise RuntimeError("Resposta do IBGE não é um GeoJSON FeatureCollection")
    features = geojson.get("features")
    if not isinstance(features, list):
        raise RuntimeError("GeoJSON do IBGE não contém uma lista de features")

    boundaries = {}
    for feature in features:
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties") or {}
        if not isinstance(properties, dict):
            continue
        code = str(
            properties.get("codarea", properties.get("CD_MUN", properties.get("id", "")))
        )
        if code not in MUNICIPALITIES:
            continue
        geometry_data = feature.get("geometry")
        if not geometry_data:
            raise RuntimeError(f"Geometria ausente para {MUNICIPALITIES[code]}")
        try:
            geometry = shape(geometry_data)
        except Exception as exc:
            raise RuntimeError(
                f"Geometria inválida para {MUNICIPALITIES[code]}"
            ) from exc
        if geometry.is_empty or not geometry.is_valid:
            raise RuntimeError(f"Geometria vazia ou inválida para {MUNICIPALITIES[code]}")
        boundaries[code] = geometry

    if len(boundaries) != len(MUNICIPALITIES):
        missing = [name for code, name in MUNICIPALITIES.items() if code not in boundaries]
        raise RuntimeError(
            f"Malha municipal incompleta ({len(boundaries)}/6). "
            f"Municípios não identificados: {', '.join(missing)}"
        )
    return boundaries


def load_municipal_boundaries(
    path: str | Path | None = None,
) -> dict[str, BaseGeometry]:
    """Load boundaries from local GeoJSON or retrieve current IBGE municipal boundaries."""
    if path:
        try:
            geojson = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"não foi possível ler o GeoJSON local {path}: {exc}") from exc
        return load_municipal_boundaries_from_geojson(geojson)

    def fetch_municipal_boundary(code: str, name: str) -> list[dict[str, Any]]:
        url = (
            f"{IBGE_BOUNDARIES_BASE_URL}/{code}"
            "?formato=application/vnd.geo+json&qualidade=minima"
        )
        request = urllib.request.Request(
            url, headers={"User-Agent": "mesh-alerts/0.1"}
        )
        data = download_with_retries(
            request, f"malha municipal de {name}", attempts=3, timeout=25
        )
        if data[:2] == bytes((0x1f, 0x8b)):
            data = gzip.decompress(data)
        geojson = json.loads(data.decode("utf-8"))
        if not isinstance(geojson, dict):
            raise RuntimeError(f"Resposta GeoJSON inválida para {name}")
        if geojson.get("type") == "FeatureCollection":
            municipal_features = geojson.get("features")
            if not isinstance(municipal_features, list):
                raise RuntimeError(f"GeoJSON sem lista de features para {name}")
            return municipal_features
        if geojson.get("type") == "Feature":
            return [geojson]
        raise RuntimeError(f"Resposta do IBGE não é GeoJSON para {name}")

    features = []
    with ThreadPoolExecutor(max_workers=len(MUNICIPALITIES)) as executor:
        tarefas = {
            executor.submit(fetch_municipal_boundary, code, name): name
            for code, name in MUNICIPALITIES.items()
        }
        for tarefa in as_completed(tarefas):
            name = tarefas[tarefa]
            try:
                features.extend(tarefa.result())
            except Exception as exc:
                raise RuntimeError(
                    f"não foi possível carregar a malha municipal de {name}: {exc}"
                ) from exc

    return load_municipal_boundaries_from_geojson(
        {"type": "FeatureCollection", "features": features}
    )
