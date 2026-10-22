#!/usr/bin/env python3
"""Cruza alertas CAP do IDAP com Aramina, Buritizal, Guará, Igarapava, Ituverava e Miguelópolis."""
import gzip
import json
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

try:
    from shapely.geometry import Polygon, shape
except ImportError:
    raise SystemExit("Instale a dependência com: python3 -m pip install --user shapely")

FEED = "/tmp/defesa-civil-cap.xml"
MUNICIPIOS = {
    "3503000": "Aramina",
    "3508207": "Buritizal",
    "3517703": "Guará",
    "3520103": "Igarapava",
    "3524105": "Ituverava",
    "3529708": "Miguelópolis",
}
NS = {"a": "http://www.w3.org/2005/Atom",
      "c": "urn:oasis:names:tc:emergency:cap:1.2"}
AGORA = datetime.now(timezone.utc)

# A API de malhas aceita a UF com intrarregiao=municipio;
# não depende de uma lista de códigos na URL.
url = ("https://servicodados.ibge.gov.br/api/v3/malhas/estados/35"
       "?intrarregiao=municipio&formato=application/vnd.geo+json&qualidade=minima")
req = urllib.request.Request(url, headers={"User-Agent": "CAP-region-check/1.0"})
try:
    with urllib.request.urlopen(req, timeout=45) as r:
        dados = r.read()
        if dados[:2] == b"\x1f\x8b":
            dados = gzip.decompress(dados)
        geo = json.loads(dados.decode("utf-8"))
except Exception as e:
    raise SystemExit(f"Falha ao consultar malha municipal do IBGE: {e}")

limites = {}
for feature in geo.get("features", []):
    props = feature.get("properties") or {}
    codigo = str(props.get("codarea", props.get("CD_MUN", props.get("id", ""))))
    if codigo in MUNICIPIOS:
        limites[codigo] = shape(feature["geometry"])

if len(limites) != len(MUNICIPIOS):
    faltam = [nome for cod, nome in MUNICIPIOS.items() if cod not in limites]
    raise SystemExit(
        f"IBGE retornou malha, mas não identifiquei todos os códigos "
        f"({len(limites)}/6). Faltando: {', '.join(faltam)}. "
        "Nenhum resultado territorial foi calculado."
    )

try:
    root = ET.parse(FEED).getroot()
except Exception as e:
    raise SystemExit(f"Não consegui ler {FEED}: {e}")

entries = root.findall("a:entry", NS)
achados = {}
expirados = 0
circulos = 0

for entry in entries:
    alert = entry.find(".//c:alert", NS)
    if alert is None:
        continue
    if alert.findtext("c:status", default="", namespaces=NS) != "Actual":
        continue
    if alert.findtext("c:msgType", default="", namespaces=NS) not in ("Alert", "Update"):
        continue

    ident = alert.findtext("c:identifier", default="sem-id", namespaces=NS)
    titulo = entry.findtext("a:title", default="Sem título", namespaces=NS)
    cidades = set()
    info_vigente = False

    for info in alert.findall("c:info", NS):
        validade = info.findtext("c:expires", default="", namespaces=NS)
        if validade:
            try:
                if datetime.fromisoformat(validade.replace("Z", "+00:00")) <= AGORA:
                    expirados += 1
                    continue
            except ValueError:
                pass
        info_vigente = True

        for area in info.findall("c:area", NS):
            if area.findall("c:circle", NS):
                circulos += 1
            for node in area.findall("c:polygon", NS):
                coords = []
                for pair in (node.text or "").split():
                    try:
                        lat, lon = map(float, pair.split(","))
                        coords.append((lon, lat))
                    except ValueError:
                        continue
                if len(coords) < 3:
                    continue
                try:
                    poly = Polygon(coords)
                    if not poly.is_valid:
                        poly = poly.buffer(0)
                    if poly.is_empty:
                        continue
                    for codigo, limite in limites.items():
                        if poly.intersects(limite):
                            cidades.add(MUNICIPIOS[codigo])
                except Exception:
                    continue

    if info_vigente and cidades:
        achados[ident] = (titulo, sorted(cidades))

print(f"Entradas no feed: {len(entries)}")
print(f"Alertas com interseção territorial: {len(achados)}")
for ident, (titulo, cidades) in sorted(achados.items()):
    print(f"- {titulo} [{ident}]: {', '.join(cidades)}")
print(f"Blocos de informação expirados ignorados: {expirados}")
print(f"Áreas com círculo CAP que exigem verificação: {circulos}")
print("Nota: polígonos foram cruzados com limites municipais; círculos CAP não foram cruzados.")