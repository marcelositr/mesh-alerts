"""Validação temporal e espacial de alertas CAP 1.2."""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.ops import unary_union
from shapely.validation import make_valid

ATOM_NS = "http://www.w3.org/2005/Atom"
CAP_NS = "urn:oasis:names:tc:emergency:cap:1.2"
NS = {"a": ATOM_NS, "c": CAP_NS}
EARTH_RADIUS_KM = 6371.0088


def parse_cap_datetime(value: str | None) -> datetime | None:
    """Parse a CAP timestamp. Return None when absent or invalid."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def info_is_current(info: ET.Element, now: datetime) -> tuple[bool, str | None]:
    """Check effective/onset/expires; malformed supplied timestamps fail closed."""
    now = now.astimezone(timezone.utc)
    # "onset" describes when the event is expected to begin, not when
    # the warning itself becomes effective. A warning may be current before onset.
    for field in ("effective", "expires"):
        raw = info.findtext(f"c:{field}", default="", namespaces=NS).strip()
        if not raw:
            continue
        parsed = parse_cap_datetime(raw)
        if parsed is None:
            return False, f"timestamp {field} inválido"
        if field == "effective" and parsed > now:
            return False, "effective futuro"
        if field == "expires" and parsed <= now:
            return False, "expires expirado"
    # Validate a supplied onset even though a future onset does not deactivate it.
    onset = info.findtext("c:onset", default="", namespaces=NS).strip()
    if onset and parse_cap_datetime(onset) is None:
        return False, "timestamp onset inválido"
    return True, None


def _polygonal_parts(geometry) -> list[Polygon]:
    """Keep polygon components only, excluding collapsed lines from repairs."""
    if isinstance(geometry, Polygon):
        return [geometry] if not geometry.is_empty and geometry.area > 0 else []
    if isinstance(geometry, (MultiPolygon, GeometryCollection)):
        parts = []
        for component in geometry.geoms:
            parts.extend(_polygonal_parts(component))
        return parts
    return []


def polygon_from_cap(value: str) -> Polygon | MultiPolygon | None:
    """Convert a closed CAP polygon to repaired polygonal WGS84 geometry."""
    pairs = (value or "").split()
    if len(pairs) < 4:
        return None

    coords = []
    for pair in pairs:
        try:
            parts = pair.split(",")
            if len(parts) != 2:
                return None
            lat, lon = (float(part) for part in parts)
        except (ValueError, TypeError):
            return None
        if not (math.isfinite(lat) and math.isfinite(lon)):
            return None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        coords.append((lon, lat))

    # CAP polygon boundaries must explicitly repeat the first point at the end.
    if coords[0] != coords[-1]:
        return None

    try:
        geometry = Polygon(coords)
        if not geometry.is_valid:
            geometry = make_valid(geometry)
        parts = _polygonal_parts(geometry)
        if not parts:
            return None
        repaired = unary_union(parts)
        if repaired.is_empty or repaired.area == 0:
            return None
        if isinstance(repaired, (Polygon, MultiPolygon)):
            return repaired
        return None
    except Exception:
        return None


def circle_from_cap(value: str, points: int = 72) -> Polygon | None:
    """Approximate a CAP circle with geodesic points, preserving radius in km."""
    try:
        center, radius_text = (value or "").rsplit(None, 1)
        lat1, lon1 = (float(part) for part in center.split(","))
        radius_km = float(radius_text.strip())
    except (ValueError, TypeError):
        return None
    if not (math.isfinite(lat1) and math.isfinite(lon1) and math.isfinite(radius_km)):
        return None
    if not (-90 <= lat1 <= 90 and -180 <= lon1 <= 180 and radius_km > 0):
        return None
    if points < 12:
        points = 12

    angular = radius_km / EARTH_RADIUS_KM
    phi1 = math.radians(lat1)
    lam1 = math.radians(lon1)
    coords = []
    for index in range(points):
        bearing = 2 * math.pi * index / points
        phi2 = math.asin(
            math.sin(phi1) * math.cos(angular)
            + math.cos(phi1) * math.sin(angular) * math.cos(bearing)
        )
        lam2 = lam1 + math.atan2(
            math.sin(bearing) * math.sin(angular) * math.cos(phi1),
            math.cos(angular) - math.sin(phi1) * math.sin(phi2),
        )
        lon2 = (math.degrees(lam2) + 540) % 360 - 180
        coords.append((lon2, math.degrees(phi2)))
    polygon = Polygon(coords)
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    return None if polygon.is_empty else polygon


def references_to_keys(value: str | None) -> set[tuple[str, str]]:
    """Extract (sender, identifier) keys from CAP sender,identifier,sent tuples."""
    keys: set[tuple[str, str]] = set()
    for reference in (value or "").split():
        parts = reference.split(",")
        if len(parts) == 3 and parts[0] and parts[1] and parse_cap_datetime(parts[2]):
            keys.add((parts[0], parts[1]))
    return keys


def alert_key(alert: ET.Element) -> tuple[str, str] | None:
    """Return the CAP identity pair (sender, identifier)."""
    sender = alert.findtext("c:sender", default="", namespaces=NS).strip()
    identifier = alert.findtext("c:identifier", default="", namespaces=NS).strip()
    return (sender, identifier) if sender and identifier else None


def active_alert_entries(root: ET.Element, now: datetime) -> tuple[list[tuple[ET.Element, ET.Element]], int]:
    """Resolve current Actual Alert/Update messages and valid references to old IDs."""
    records: list[tuple[ET.Element, ET.Element, str, tuple[str, str]]] = []
    suppressed: set[tuple[str, str]] = set()
    expired_info_count = 0
    now_utc = now.astimezone(timezone.utc)

    for entry in root.findall("a:entry", NS):
        alert = entry.find(".//c:alert", NS)
        if alert is None:
            continue
        if alert.findtext("c:status", default="", namespaces=NS) != "Actual":
            continue
        msg_type = alert.findtext("c:msgType", default="", namespaces=NS)
        if msg_type not in ("Alert", "Update", "Cancel"):
            continue

        key = alert_key(alert)
        sent = parse_cap_datetime(alert.findtext("c:sent", default="", namespaces=NS))
        if key is None or sent is None or sent > now_utc:
            continue

        # Only a message already sent can supersede an earlier alert.
        references = references_to_keys(
            alert.findtext("c:references", default="", namespaces=NS)
        )
        if msg_type in ("Update", "Cancel"):
            suppressed.update(references)
        records.append((entry, alert, msg_type, key))

    active: list[tuple[ET.Element, ET.Element]] = []
    for entry, alert, msg_type, key in records:
        if msg_type == "Cancel" or key in suppressed:
            continue

        infos = alert.findall("c:info", NS)
        has_current_info = False
        for info in infos:
            current, reason = info_is_current(info, now_utc)
            if not current and reason == "expires expirado":
                expired_info_count += 1
            if current:
                has_current_info = True
        if has_current_info:
            active.append((entry, alert))
    return active, expired_info_count


def intersecting_municipalities(
    alert: ET.Element,
    boundaries: dict[str, object],
    municipality_names: dict[str, str],
    now: datetime,
) -> tuple[set[str], int, int]:
    """Return municipalities matched by current CAP geocodes, polygons, or circles."""
    cities: set[str] = set()
    expired_count = 0
    circle_count = 0
    for info in alert.findall("c:info", NS):
        current, reason = info_is_current(info, now)
        if not current:
            if reason == "expires expirado":
                expired_count += 1
            continue
        for area in info.findall("c:area", NS):
            # CAP area geometry is the union of polygons, circles, and geocodes.
            # Exact IBGE matches remain useful when the sender supplies no geometry.
            for geocode in area.findall("c:geocode", NS):
                value_name = geocode.findtext(
                    "c:valueName", default="", namespaces=NS
                ).strip()
                value = geocode.findtext("c:value", default="", namespaces=NS).strip()
                if value_name.upper() == "IBGE" and value in municipality_names:
                    cities.add(municipality_names[value])

            geometries = []
            for node in area.findall("c:polygon", NS):
                polygon = polygon_from_cap(node.text or "")
                if polygon is not None:
                    geometries.append(polygon)
            for node in area.findall("c:circle", NS):
                circle_count += 1
                polygon = circle_from_cap(node.text or "")
                if polygon is not None:
                    geometries.append(polygon)
            for geometry in geometries:
                for code, boundary in boundaries.items():
                    if geometry.intersects(boundary):
                        cities.add(municipality_names[code])
    return cities, expired_count, circle_count
