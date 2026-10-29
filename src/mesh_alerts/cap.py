"""Temporal and spatial validation of CAP 1.2 alerts."""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.validation import make_valid

ATOM_NS = "http://www.w3.org/2005/Atom"
CAP_NS = "urn:oasis:names:tc:emergency:cap:1.2"
XML_NAMESPACES = {"a": ATOM_NS, "c": CAP_NS}
EARTH_RADIUS_KM = 6371.0088


def parse_cap_timestamp(value: str | None) -> datetime | None:
    """Parse a CAP timestamp, returning None when it is absent or invalid."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def is_info_current(info: ET.Element, now: datetime) -> tuple[bool, str | None]:
    """Validate effective, onset, and expires timestamps.

    Malformed supplied timestamps invalidate the information block.
    """
    now = now.astimezone(timezone.utc)
    # "onset" describes when the event is expected to begin, not when
    # the warning itself becomes effective. A warning may be current before onset.
    for field in ("effective", "expires"):
        raw = info.findtext(f"c:{field}", default="", namespaces=XML_NAMESPACES).strip()
        if not raw:
            continue
        parsed = parse_cap_timestamp(raw)
        if parsed is None:
            return False, f"timestamp {field} inválido"
        if field == "effective" and parsed > now:
            return False, "effective futuro"
        if field == "expires" and parsed <= now:
            return False, "expires expirado"
    # Validate a supplied onset even though a future onset does not deactivate it.
    onset = info.findtext("c:onset", default="", namespaces=XML_NAMESPACES).strip()
    if onset and parse_cap_timestamp(onset) is None:
        return False, "timestamp onset inválido"
    return True, None


def _extract_polygonal_parts(geometry: BaseGeometry) -> list[Polygon]:
    """Retain polygon components and discard collapsed non-polygonal geometry."""
    if isinstance(geometry, Polygon):
        return [geometry] if not geometry.is_empty and geometry.area > 0 else []
    if isinstance(geometry, (MultiPolygon, GeometryCollection)):
        parts = []
        for component in geometry.geoms:
            parts.extend(_extract_polygonal_parts(component))
        return parts
    return []


def parse_cap_polygon(value: str) -> Polygon | MultiPolygon | None:
    """Parse a closed CAP polygon and repair invalid polygonal geometry."""
    pairs = (value or "").split()
    if len(pairs) < 4:
        return None

    coordinates = []
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
        coordinates.append((lon, lat))

    # CAP polygon boundaries must explicitly repeat the first point at the end.
    if coordinates[0] != coordinates[-1]:
        return None

    try:
        geometry = Polygon(coordinates)
        if not geometry.is_valid:
            geometry = make_valid(geometry)
        parts = _extract_polygonal_parts(geometry)
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


def parse_cap_circle(value: str, points: int = 72) -> Polygon | None:
    """Approximate a CAP circle with geodesic points while preserving its radius in kilometers."""
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
    coordinates = []
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
        coordinates.append((lon2, math.degrees(phi2)))
    polygon = Polygon(coordinates)
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    return None if polygon.is_empty else polygon


def parse_reference_records(value: str | None) -> set[tuple[str, str, datetime]]:
    """Extract CAP references while preserving the referenced message timestamp."""
    records: set[tuple[str, str, datetime]] = set()
    for reference in (value or "").split():
        parts = reference.split(",")
        if len(parts) != 3 or not parts[0] or not parts[1]:
            continue
        sent = parse_cap_timestamp(parts[2])
        if sent is not None:
            records.add((parts[0], parts[1], sent))
    return records


def parse_reference_keys(value: str | None) -> set[tuple[str, str]]:
    """Extract sender and identifier pairs from valid CAP references."""
    return {(sender, identifier) for sender, identifier, _sent in parse_reference_records(value)}


def get_alert_key(alert: ET.Element) -> tuple[str, str] | None:
    """Return the CAP sender and identifier pair."""
    sender = alert.findtext("c:sender", default="", namespaces=XML_NAMESPACES).strip()
    identifier = alert.findtext("c:identifier", default="", namespaces=XML_NAMESPACES).strip()
    return (sender, identifier) if sender and identifier else None


def get_active_alert_entries(
    root: ET.Element, now: datetime
) -> tuple[list[tuple[ET.Element, ET.Element]], int]:
    """Resolve current Actual Alert and Update messages and their valid references."""
    records: list[tuple[ET.Element, ET.Element, str, tuple[str, str]]] = []
    sent_by_key: dict[tuple[str, str], datetime] = {}
    references_by_key: list[tuple[str, tuple[str, str], datetime]] = []
    suppressed: set[tuple[str, str]] = set()
    expired_info_count = 0
    now_utc = now.astimezone(timezone.utc)

    for entry in root.findall("a:entry", XML_NAMESPACES):
        alert = entry.find(".//c:alert", XML_NAMESPACES)
        if alert is None:
            continue
        if alert.findtext("c:status", default="", namespaces=XML_NAMESPACES) != "Actual":
            continue
        msg_type = alert.findtext("c:msgType", default="", namespaces=XML_NAMESPACES)
        if msg_type not in ("Alert", "Update", "Cancel"):
            continue

        key = get_alert_key(alert)
        sent = parse_cap_timestamp(alert.findtext("c:sent", default="", namespaces=XML_NAMESPACES))
        if key is None or sent is None or sent > now_utc:
            continue

        sent_by_key[key] = sent
        # Preserve the referenced sent timestamp. A sender/identifier pair alone
        # is insufficient: a malformed or mismatched reference must not suppress
        # a different message that happens to reuse the same identity.
        references = parse_reference_records(
            alert.findtext("c:references", default="", namespaces=XML_NAMESPACES)
        )
        if msg_type in ("Update", "Cancel"):
            references_by_key.extend((msg_type, (ref_sender, ref_identifier), ref_sent)
                                     for ref_sender, ref_identifier, ref_sent in references)
        records.append((entry, alert, msg_type, key))

    for _msg_type, ref_key, ref_sent in references_by_key:
        if sent_by_key.get(ref_key) == ref_sent:
            suppressed.add(ref_key)

    active: list[tuple[ET.Element, ET.Element]] =
    for entry, alert, msg_type, key in records:
        if msg_type == "Cancel" or key in suppressed:
            continue

        info_blocks = alert.findall("c:info", XML_NAMESPACES)
        has_current_info = False
        for info in info_blocks:
            current, reason = is_info_current(info, now_utc)
            if not current and reason == "expires expirado":
                expired_info_count += 1
            if current:
                has_current_info = True
        if has_current_info:
            active.append((entry, alert))
    return active, expired_info_count


def find_intersecting_municipalities(
    alert: ET.Element,
    boundaries: dict[str, BaseGeometry],
    municipality_names: dict[str, str],
    now: datetime,
) -> tuple[set[str], int, int]:
    """Return municipalities matched by current CAP geocodes, polygons, or circles."""
    cities: set[str] = set()
    expired_count = 0
    circle_count = 0
    for info in alert.findall("c:info", XML_NAMESPACES):
        current, reason = is_info_current(info, now)
        if not current:
            if reason == "expires expirado":
                expired_count += 1
            continue
        for area in info.findall("c:area", XML_NAMESPACES):
            # CAP area geometry is the union of polygons, circles, and geocodes.
            # Exact IBGE matches remain useful when the sender supplies no geometry.
            for geocode in area.findall("c:geocode", XML_NAMESPACES):
                value_name = geocode.findtext(
                    "c:valueName", default="", namespaces=XML_NAMESPACES
                ).strip()
                value = geocode.findtext("c:value", default="", namespaces=XML_NAMESPACES).strip()
                if value_name.upper() == "IBGE" and value in municipality_names:
                    cities.add(municipality_names[value])

            geometries = []
            for node in area.findall("c:polygon", XML_NAMESPACES):
                polygon = parse_cap_polygon(node.text or "")
                if polygon is not None:
                    geometries.append(polygon)
            for node in area.findall("c:circle", XML_NAMESPACES):
                circle_count += 1
                polygon = parse_cap_circle(node.text or "")
                if polygon is not None:
                    geometries.append(polygon)
            for geometry in geometries:
                for code, boundary in boundaries.items():
                    if geometry.intersects(boundary):
                        cities.add(municipality_names[code])
    return cities, expired_count, circle_count


NS = XML_NAMESPACES
parse_cap_datetime = parse_cap_timestamp
info_is_current = is_info_current
polygon_from_cap = parse_cap_polygon
circle_from_cap = parse_cap_circle
references_to_keys = parse_reference_keys
references_to_records = parse_reference_records
alert_key = get_alert_key
active_alert_entries = get_active_alert_entries
intersecting_municipalities = find_intersecting_municipalities
