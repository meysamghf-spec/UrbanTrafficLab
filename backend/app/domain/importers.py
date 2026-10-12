"""GeoJSON import and export.

Imported attributes that are missing are filled with stated assumptions.
They are not presented as surveyed values.
"""

from __future__ import annotations

from typing import Any

from app.domain.geo import length_of_coordinates, valid_lon_lat
from app.domain.network import empty_network, valid_feature_id

MAX_FEATURES = 5000
SPEED_BY_TYPE = {
    "motorway": 100.0,
    "trunk": 80.0,
    "primary": 50.0,
    "secondary": 40.0,
    "tertiary": 40.0,
    "residential": 30.0,
    "unclassified": 30.0,
    "service": 20.0,
}


class ImportError_(Exception):
    def __init__(self, message: str, code: str = "invalid_geojson") -> None:
        super().__init__(message)
        self.code = code


def import_geojson(document: dict[str, Any], *, max_features: int = MAX_FEATURES) -> dict[str, Any]:
    if not isinstance(document, dict) or document.get("type") != "FeatureCollection":
        raise ImportError_("GeoJSON must be a FeatureCollection.")
    features = document.get("features")
    if not isinstance(features, list) or not features:
        raise ImportError_("GeoJSON contains no features.")
    if len(features) > max_features:
        raise ImportError_(f"GeoJSON exceeds the feature limit of {max_features}.", "too_many_features")
    network = empty_network()
    warnings: list[dict[str, str]] = []
    node_ids: dict[tuple[float, float], str] = {}
    for feature in features:
        if not isinstance(feature, dict):
            raise ImportError_("A feature is not an object.")
        geometry = feature.get("geometry") or {}
        if geometry.get("type") == "Point":
            _add_point_node(network, node_ids, geometry, feature.get("properties") or {})
    road_index = 0
    for feature in features:
        geometry = feature.get("geometry") or {}
        gtype = geometry.get("type")
        if gtype == "LineString":
            lines = [geometry.get("coordinates") or []]
        elif gtype == "MultiLineString":
            lines = geometry.get("coordinates") or []
        else:
            continue
        properties = feature.get("properties") or {}
        for coordinates in lines:
            road_index += 1
            _add_line(network, node_ids, coordinates, properties, road_index, warnings)
    if not network["edges"]:
        raise ImportError_("No LineString roads were found in the GeoJSON.")
    network["meta"]["notes"] = ["Imported from GeoJSON. Missing speeds or lanes are assumptions."]
    return {"network": network, "warnings": warnings}


def export_geojson(network: dict[str, Any]) -> dict[str, Any]:
    features = []
    for node in network["nodes"]:
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [node["lon"], node["lat"]]},
                "properties": {"id": node["id"], "name": node["name"], "kind": node["kind"], "feature_type": "intersection"},
            }
        )
    for edge in network["edges"]:
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": edge["geometry"]},
                "properties": {
                    "id": edge["id"],
                    "road_id": edge["road_id"],
                    "name": edge["name"],
                    "from_node": edge["from_node"],
                    "to_node": edge["to_node"],
                    "lanes": edge["lanes"],
                    "speed_kmh": edge["speed_kmh"],
                    "length_m": edge["length_m"],
                    "road_type": edge["road_type"],
                    "feature_type": "directed_edge",
                    "oneway": True,
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def _add_point_node(
    network: dict[str, Any],
    node_ids: dict[tuple[float, float], str],
    geometry: dict[str, Any],
    properties: dict[str, Any],
) -> None:
    coordinates = geometry.get("coordinates") or []
    if len(coordinates) < 2 or not valid_lon_lat(coordinates[0], coordinates[1]):
        raise ImportError_("A point feature has invalid coordinates.")
    key = _key(coordinates[0], coordinates[1])
    node_id = str(properties.get("id") or properties.get("node_id") or f"N{len(node_ids) + 1}")
    if not valid_feature_id(node_id):
        node_id = f"N{len(node_ids) + 1}"
    if key not in node_ids:
        node_ids[key] = node_id
        network["nodes"].append(
            {
                "id": node_id,
                "name": str(properties.get("name") or node_id),
                "lon": key[0],
                "lat": key[1],
                "kind": "intersection",
            }
        )
        network["intersections"].append({"node_id": node_id, "control": "uncontrolled"})


def _add_line(
    network: dict[str, Any],
    node_ids: dict[tuple[float, float], str],
    coordinates: list[Any],
    properties: dict[str, Any],
    index: int,
    warnings: list[dict[str, str]],
) -> None:
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        raise ImportError_("A road feature needs at least two coordinates.")
    cleaned = []
    for point in coordinates:
        if not isinstance(point, list) or len(point) < 2 or not valid_lon_lat(point[0], point[1]):
            raise ImportError_("A road coordinate is invalid.")
        cleaned.append([float(point[0]), float(point[1])])
    start = _ensure_node(network, node_ids, cleaned[0], properties.get("from_node"))
    end = _ensure_node(network, node_ids, cleaned[-1], properties.get("to_node"))
    if start == end:
        raise ImportError_("A road feature starts and ends at the same point.")
    road_id = str(properties.get("road_id") or properties.get("id") or f"W{index}")
    if not valid_feature_id(road_id):
        road_id = f"W{index}"
    road_type = str(properties.get("road_type") or properties.get("highway") or "unclassified")
    lanes = properties.get("lanes")
    if lanes is None:
        lanes = 1
        warnings.append({"code": "lanes_assumed", "message": f"{road_id} has no lane count. One lane was assumed."})
    try:
        lanes = int(lanes)
    except (TypeError, ValueError) as exc:
        raise ImportError_(f"{road_id} has a lane count that is not an integer.") from exc
    if lanes < 1:
        raise ImportError_(f"{road_id} must have at least one lane.")
    speed = properties.get("speed_kmh")
    if speed is None:
        speed = SPEED_BY_TYPE.get(road_type, 30.0)
        warnings.append(
            {
                "code": "speed_assumed",
                "message": f"{road_id} has no speed. {speed:.0f} km/h was assumed from road type '{road_type}'.",
            }
        )
    try:
        speed = float(speed)
    except (TypeError, ValueError) as exc:
        raise ImportError_(f"{road_id} has a speed that is not a number.") from exc
    name = str(properties.get("name") or road_id)
    length = round(length_of_coordinates(cleaned), 3)
    oneway = _as_bool(properties.get("oneway"))
    forward = _edge(road_id, name, start, end, lanes, speed, road_type, cleaned, length)
    network["edges"].append(forward)
    if not oneway:
        backward = _edge(road_id, name, end, start, lanes, speed, road_type, list(reversed(cleaned)), length)
        network["edges"].append(backward)


def _ensure_node(
    network: dict[str, Any],
    node_ids: dict[tuple[float, float], str],
    coordinate: list[float],
    preferred: Any,
) -> str:
    key = _key(coordinate[0], coordinate[1])
    if key in node_ids:
        return node_ids[key]
    node_id = str(preferred) if isinstance(preferred, str) and valid_feature_id(preferred) else f"N{len(node_ids) + 1}"
    while any(node["id"] == node_id for node in network["nodes"]):
        node_id = f"N{len(node_ids) + 1}_{len(network['nodes']) + 1}"
    node_ids[key] = node_id
    network["nodes"].append({"id": node_id, "name": node_id, "lon": key[0], "lat": key[1], "kind": "intersection"})
    network["intersections"].append({"node_id": node_id, "control": "uncontrolled"})
    return node_id


def _edge(
    road_id: str,
    name: str,
    start: str,
    end: str,
    lanes: int,
    speed: float,
    road_type: str,
    geometry: list[list[float]],
    length: float,
) -> dict[str, Any]:
    return {
        "id": f"{road_id}_{start}_{end}",
        "road_id": road_id,
        "name": name,
        "from_node": start,
        "to_node": end,
        "lanes": lanes,
        "speed_kmh": round(speed, 6),
        "length_m": length,
        "road_type": road_type,
        "geometry": geometry,
        "bridge_id": None,
        "tunnel_id": None,
        "roundabout_id": None,
    }


def _key(lon: float, lat: float) -> tuple[float, float]:
    return (round(float(lon), 6), round(float(lat), 6))


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes"}
    return bool(value)
