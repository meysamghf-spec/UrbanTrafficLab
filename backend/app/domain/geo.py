"""Geometry helpers. Distances are meters. Coordinates are longitude, latitude in degrees."""

from __future__ import annotations

import math
from typing import Any

EARTH_RADIUS_M = 6_371_000.0
METERS_PER_DEG_LAT = 110_540.0
METERS_PER_DEG_LON_EQUATOR = 111_320.0


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def finite_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def valid_lon_lat(lon: float, lat: float) -> bool:
    return finite_number(lon) and finite_number(lat) and -180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0


def cardinal_direction(lon1: float, lat1: float, lon2: float, lat2: float) -> str | None:
    """Dominant compass direction of travel from point 1 to point 2.

    East/west wins ties so a diagonal that is equally east and north is eastbound.
    """
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    if abs(dlon) < 1e-12 and abs(dlat) < 1e-12:
        return None
    if abs(dlon) >= abs(dlat):
        return "eastbound" if dlon > 0 else "westbound"
    return "northbound" if dlat > 0 else "southbound"


def to_local_meters(lon: float, lat: float, origin_lon: float, origin_lat: float) -> tuple[float, float]:
    x = (lon - origin_lon) * METERS_PER_DEG_LON_EQUATOR * math.cos(math.radians(origin_lat))
    y = (lat - origin_lat) * METERS_PER_DEG_LAT
    return x, y


def point_segment_distance_m(
    lon: float,
    lat: float,
    lon1: float,
    lat1: float,
    lon2: float,
    lat2: float,
) -> float:
    origin_lon = lon
    origin_lat = lat
    px, py = 0.0, 0.0
    ax, ay = to_local_meters(lon1, lat1, origin_lon, origin_lat)
    bx, by = to_local_meters(lon2, lat2, origin_lon, origin_lat)
    dx = bx - ax
    dy = by - ay
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-9:
        return math.hypot(ax - px, ay - py)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    cx = ax + t * dx
    cy = ay + t * dy
    return math.hypot(cx - px, cy - py)


def line_distance_m(lon: float, lat: float, coordinates: list[list[float]]) -> float:
    if len(coordinates) == 1:
        return haversine_m(lon, lat, coordinates[0][0], coordinates[0][1])
    best = float("inf")
    for start, end in zip(coordinates, coordinates[1:]):
        best = min(best, point_segment_distance_m(lon, lat, start[0], start[1], end[0], end[1]))
    return best


def edge_midpoint(edge: dict[str, Any]) -> list[float]:
    coords = edge.get("geometry") or []
    if not coords:
        return [0.0, 0.0]
    first = coords[0]
    last = coords[-1]
    return [(first[0] + last[0]) / 2.0, (first[1] + last[1]) / 2.0]


def bbox_of_nodes(nodes: list[dict[str, Any]]) -> list[float] | None:
    if not nodes:
        return None
    lons = [float(node["lon"]) for node in nodes]
    lats = [float(node["lat"]) for node in nodes]
    return [min(lons), min(lats), max(lons), max(lats)]


def polygon_centroid(geometry: dict[str, Any] | None) -> list[float] | None:
    if not geometry:
        return None
    gtype = geometry.get("type")
    if gtype == "Point":
        coords = geometry.get("coordinates") or []
        if len(coords) >= 2:
            return [float(coords[0]), float(coords[1])]
        return None
    if gtype != "Polygon":
        return None
    rings = geometry.get("coordinates") or []
    if not rings or not rings[0]:
        return None
    ring = rings[0]
    points = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    if not points:
        return None
    lon = sum(float(point[0]) for point in points) / len(points)
    lat = sum(float(point[1]) for point in points) / len(points)
    return [lon, lat]


def length_of_coordinates(coordinates: list[list[float]]) -> float:
    total = 0.0
    for start, end in zip(coordinates, coordinates[1:]):
        total += haversine_m(start[0], start[1], end[0], end[1])
    return total
