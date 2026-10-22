"""Validação temporal e espacial de alertas CAP 1.2."""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from shapely.geometry import Polygon

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
    for field in ("effective", "onset", "expires"):
        raw = info.findtext(f"c:{field}", default="", namespaces=NS).strip()
        if not raw:
            continue
        parsed = parse_cap_datetime(raw)
        if parsed is None:
            return False, f"timestamp {field} inválido"
        if field in ("effective", "onset") and parsed > now:
            return False, f"{field} futuro"
        if field == "expires" and parsed <= now:
            return False, "expires expirado"
    return True, None


def polygon_from_cap(value: str) -> Polygon | None:
    """Convert CAP polygon 'lat,lon lat,lon ...' to a valid WGS84 polygon."""
    coords = []
    for pair in (value or "").split():
        try:
            lat, lon = (float(part) for part in pair.split(","))
        except (ValueError, TypeError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        coords.append((lon, lat))
    if len(coords) < 3:
        return None
    try:
        polygon = Polygon(coords)
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        if polygon.is_empty or polygon.area == 0:
            return None
        return polygon
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


def references_to_identifiers(value: str | None) -> set[str]:
    """Extract referenced CAP identifiers from sender,identifier,sent tuples."""
    identifiers: set[str] = set()
    for reference in (value or "").split():
        parts = reference.split(",")
        if len(parts) >= 2 and parts[1]:
            identifiers.add(parts[1])
    return identifiers


def active_alert_entries(root: ET.Element, now: datetime) -> tuple[list[tuple[ET.Element, ET.Element]], int]:
    """Resolve current Actual Alert/Update messages and suppress referenced old IDs."""
    records: list[tuple[ET.Element, ET.Element, str, set[str]]] = []
    suppressed: set[str] = set()
    expired_info_count = 0

    for entry in root.findall("a:entry", NS):
        alert = entry.find(".//c:alert", NS)
        if alert is None:
            continue
        if alert.findtext("c:status", default="", namespaces=NS) != "Actual":
            continue
        msg_type = alert.findtext("c:msgType", default="", namespaces=NS)
        if msg_type not in ("Alert", "Update", "Cancel"):
            continue

        identifier = alert.findtext("c:identifier", default="", namespaces=NS)
        if not identifier:
            continue
        references = references_to_identifiers(
            alert.findtext("c:references", default="", namespaces=NS)
        )
        suppressed.update(references)
        records.append((entry, alert, msg_type, references))

    active: list[tuple[ET.Element, ET.Element]] = []
    for entry, alert, msg_type, _references in records:
        identifier = alert.findtext("c:identifier", default="", namespaces=NS)
        if msg_type == "Cancel" or identifier in suppressed:
            continue
        sent_raw = alert.findtext("c:sent", default="", namespaces=NS)
        sent = parse_cap_datetime(sent_raw)
        if sent is None or sent > now.astimezone(timezone.utc):
            continue

        infos = alert.findall("c:info", NS)
        has_current_info = False
        for info in infos:
            current, reason = info_is_current(info, now)
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
    """Return municipalities intersected by current CAP polygons/circles."""
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
            geometries: list[Polygon] = []
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
