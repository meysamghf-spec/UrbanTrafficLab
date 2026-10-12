"""In-memory road-network document.

The document is a JSON-serializable directed graph. Bidirectional roads are two
directed edges that share a road_id. Baseline documents are stored outside this
module and are never updated in place by the scenario engine.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any

import networkx as nx

from app.domain.geo import (
    bbox_of_nodes,
    cardinal_direction,
    edge_midpoint,
    haversine_m,
    line_distance_m,
    valid_lon_lat,
)

ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]{0,63}$")
COORD_MATCH_M = 30.0


def empty_network() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "nodes": [],
        "edges": [],
        "intersections": [],
        "signals": [],
        "bridges": [],
        "tunnels": [],
        "roundabouts": [],
        "land_uses": [],
        "transit_routes": [],
        "turn_restrictions": [],
        "demand_profiles": [],
        "meta": {"comparison_peaks": [], "notes": []},
    }


def copy_network(network: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(network)


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(data: Any) -> str:
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def valid_feature_id(value: str) -> bool:
    return bool(ID_RE.fullmatch(value))


def nodes(network: dict[str, Any]) -> list[dict[str, Any]]:
    return network["nodes"]


def edges(network: dict[str, Any]) -> list[dict[str, Any]]:
    return network["edges"]


def get_node(network: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    for node in network["nodes"]:
        if node["id"] == node_id:
            return node
    return None


def get_edge(network: dict[str, Any], edge_id: str) -> dict[str, Any] | None:
    for edge in network["edges"]:
        if edge["id"] == edge_id:
            return edge
    return None


def edges_for_road(network: dict[str, Any], road_id: str) -> list[dict[str, Any]]:
    return [edge for edge in network["edges"] if edge["road_id"] == road_id]


def road_catalog(network: dict[str, Any]) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for edge in network["edges"]:
        road = catalog.setdefault(
            edge["road_id"],
            {"id": edge["road_id"], "name": edge["name"], "edges": []},
        )
        road["edges"].append(edge)
    return catalog


def edge_cardinal(network: dict[str, Any], edge: dict[str, Any]) -> str | None:
    start = get_node(network, edge["from_node"])
    end = get_node(network, edge["to_node"])
    if start is None or end is None:
        return None
    return cardinal_direction(start["lon"], start["lat"], end["lon"], end["lat"])


def incident_edges(network: dict[str, Any], node_id: str) -> list[dict[str, Any]]:
    return [
        edge
        for edge in network["edges"]
        if edge["from_node"] == node_id or edge["to_node"] == node_id
    ]


def signal_for_node(network: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    for signal in network["signals"]:
        if signal["node_id"] == node_id:
            return signal
    return None


def intersection_for_node(network: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    for item in network["intersections"]:
        if item["node_id"] == node_id:
            return item
    return None


def _road_summary(road: dict[str, Any]) -> dict[str, Any]:
    edge = road["edges"][0]
    return {
        "id": road["id"],
        "name": road["name"],
        "kind": "road",
        "from_node": edge["from_node"],
        "to_node": edge["to_node"],
    }


def _node_summary(node: dict[str, Any]) -> dict[str, Any]:
    return {"id": node["id"], "name": node["name"], "kind": "intersection"}


def resolve_road(network: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    """Resolve a road without substituting a different feature.

    A provided feature_id is exact. If it does not match, the label is not used
    as a fallback.
    """
    feature_id = str(target.get("feature_id") or "").strip()
    label = str(target.get("label") or "").strip()
    coordinates = target.get("coordinates")
    catalog = road_catalog(network)

    if feature_id:
        if feature_id in catalog:
            return {"status": "unique", "road": catalog[feature_id], "note": None}
        edge = get_edge(network, feature_id)
        if edge and edge["road_id"] in catalog:
            return {"status": "unique", "road": catalog[edge["road_id"]], "note": None}
        return {
            "status": "not_found",
            "message": (
                f"Selected feature '{feature_id}' was not found. "
                "A different road was not substituted."
            ),
            "candidates": [],
        }

    matches = _roads_matching_label(catalog, label) if label else []
    note = None
    if len(matches) > 1 and _valid_coordinate(coordinates):
        narrowed = _roads_near_coordinate(matches, coordinates)
        if len(narrowed) == 1:
            matches = narrowed
            note = "The road name was ambiguous. The supplied coordinate selected one candidate."
    if not matches and _valid_coordinate(coordinates):
        near = _roads_near_coordinate(list(catalog.values()), coordinates)
        if len(near) == 1:
            return {
                "status": "unique",
                "road": near[0],
                "note": "The road was resolved from the supplied coordinate.",
            }
        if len(near) > 1:
            matches = near
    if len(matches) == 1:
        return {"status": "unique", "road": matches[0], "note": note}
    if len(matches) > 1:
        return {
            "status": "ambiguous",
            "message": f"'{label or 'coordinate'}' matches multiple roads. Select one feature.",
            "candidates": [_road_summary(road) for road in matches],
        }
    return {
        "status": "not_found",
        "message": f"No road matched '{label or feature_id or 'the request'}'.",
        "candidates": [],
    }


def resolve_node(network: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    feature_id = str(target.get("feature_id") or "").strip()
    label = str(target.get("label") or "").strip()
    label = re.sub(
        r"^(?:the\s+)?intersection\s+(?:of\s+)?",
        "",
        label,
        flags=re.IGNORECASE,
    ).strip()
    coordinates = target.get("coordinates")

    if feature_id:
        node = get_node(network, feature_id)
        if node is None:
            return {
                "status": "not_found",
                "message": (
                    f"Selected intersection '{feature_id}' was not found. "
                    "A different intersection was not substituted."
                ),
                "candidates": [],
            }
        return {"status": "unique", "node": node, "note": None}

    if label:
        by_id = get_node(network, label)
        if by_id is not None:
            return {"status": "unique", "node": by_id, "note": None}
        named = [node for node in network["nodes"] if node["name"].lower() == label.lower()]
        if len(named) == 1:
            return {"status": "unique", "node": named[0], "note": None}
        if len(named) > 1:
            return {
                "status": "ambiguous",
                "message": f"'{label}' matches multiple intersections. Select one feature.",
                "candidates": [_node_summary(node) for node in named],
            }
        if " and " in label.lower():
            left, right = re.split(r"\s+and\s+", label, maxsplit=1, flags=re.IGNORECASE)
            return _intersection_of_roads(network, left.strip(), right.strip())

    if _valid_coordinate(coordinates):
        nearest = _nearest_nodes(network, coordinates, limit=3)
        within = [item for item in nearest if item["distance_m"] <= COORD_MATCH_M]
        if len(within) == 1:
            return {
                "status": "unique",
                "node": within[0]["node"],
                "note": "The intersection was resolved from the supplied coordinate.",
            }
        if len(within) > 1:
            return {
                "status": "ambiguous",
                "message": "Multiple intersections are near the supplied coordinate.",
                "candidates": [_node_summary(item["node"]) for item in within],
            }
    return {
        "status": "not_found",
        "message": f"No intersection matched '{label or feature_id or 'the request'}'.",
        "candidates": [],
    }


def make_edge(
    *,
    road_id: str,
    name: str,
    from_node: dict[str, Any],
    to_node: dict[str, Any],
    lanes: int,
    speed_kmh: float,
    road_type: str,
) -> dict[str, Any]:
    geometry = [
        [float(from_node["lon"]), float(from_node["lat"])],
        [float(to_node["lon"]), float(to_node["lat"])],
    ]
    length_m = haversine_m(geometry[0][0], geometry[0][1], geometry[1][0], geometry[1][1])
    return {
        "id": f"{road_id}_{from_node['id']}_{to_node['id']}",
        "road_id": road_id,
        "name": name,
        "from_node": from_node["id"],
        "to_node": to_node["id"],
        "lanes": int(lanes),
        "speed_kmh": round(float(speed_kmh), 6),
        "length_m": round(length_m, 3),
        "road_type": road_type,
        "geometry": geometry,
        "bridge_id": None,
        "tunnel_id": None,
        "roundabout_id": None,
    }


def add_directed_edge(network: dict[str, Any], edge: dict[str, Any]) -> None:
    network["edges"].append(edge)


def reverse_edge(network: dict[str, Any], edge: dict[str, Any]) -> dict[str, Any]:
    start = get_node(network, edge["to_node"])
    end = get_node(network, edge["from_node"])
    if start is None or end is None:
        raise ValueError("Cannot reverse an edge with missing endpoints.")
    reversed_edge = make_edge(
        road_id=edge["road_id"],
        name=edge["name"],
        from_node=start,
        to_node=end,
        lanes=edge["lanes"],
        speed_kmh=edge["speed_kmh"],
        road_type=edge["road_type"],
    )
    reversed_edge["bridge_id"] = edge.get("bridge_id")
    reversed_edge["tunnel_id"] = edge.get("tunnel_id")
    reversed_edge["roundabout_id"] = edge.get("roundabout_id")
    return reversed_edge


def validate_network(network: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    node_ids = [node["id"] for node in network["nodes"]]
    if len(node_ids) != len(set(node_ids)):
        issues.append(_issue("error", "duplicate_node_id", "Node ids must be unique."))
    edge_ids = [edge["id"] for edge in network["edges"]]
    if len(edge_ids) != len(set(edge_ids)):
        issues.append(_issue("error", "duplicate_edge_id", "Directed edge ids must be unique."))
    seen_pairs: set[tuple[str, str]] = set()
    node_set = set(node_ids)
    for edge in network["edges"]:
        pair = (edge["from_node"], edge["to_node"])
        if pair in seen_pairs:
            issues.append(
                _issue(
                    "error",
                    "duplicate_directed_pair",
                    f"More than one edge connects {pair[0]} to {pair[1]}.",
                    feature_id=edge["id"],
                )
            )
        seen_pairs.add(pair)
        if edge["from_node"] not in node_set or edge["to_node"] not in node_set:
            issues.append(
                _issue(
                    "error",
                    "dangling_edge",
                    f"Edge {edge['id']} references a missing node.",
                    feature_id=edge["id"],
                )
            )
        if int(edge["lanes"]) < 1:
            issues.append(_issue("error", "invalid_lanes", f"Edge {edge['id']} has no lanes.", feature_id=edge["id"]))
        if float(edge["speed_kmh"]) <= 0 or float(edge["speed_kmh"]) > 200:
            issues.append(
                _issue("error", "invalid_speed", f"Edge {edge['id']} has an invalid speed.", feature_id=edge["id"])
            )
        if float(edge["length_m"]) <= 0:
            issues.append(
                _issue("error", "invalid_length", f"Edge {edge['id']} has a non-positive length.", feature_id=edge["id"])
            )
        for endpoint in (edge["geometry"][0], edge["geometry"][-1]):
            if not valid_lon_lat(endpoint[0], endpoint[1]):
                issues.append(
                    _issue("error", "invalid_geometry", f"Edge {edge['id']} has a coordinate outside the valid range.")
                )
    for node in network["nodes"]:
        if not valid_lon_lat(node["lon"], node["lat"]):
            issues.append(_issue("error", "invalid_node", f"Node {node['id']} has an invalid coordinate.", feature_id=node["id"]))
    for signal in network["signals"]:
        if signal["node_id"] not in node_set:
            issues.append(
                _issue("error", "dangling_signal", f"Signal {signal['id']} references a missing node.", feature_id=signal["id"])
            )
    for collection, code in (("bridges", "dangling_bridge"), ("tunnels", "dangling_tunnel")):
        for item in network[collection]:
            missing = [road_id for road_id in item["road_ids"] if not edges_for_road(network, road_id)]
            if missing:
                issues.append(
                    _issue("error", code, f"{item['id']} references missing roads: {', '.join(missing)}.", feature_id=item["id"])
                )
    for roundabout in network["roundabouts"]:
        if roundabout["node_id"] not in node_set:
            issues.append(
                _issue(
                    "error",
                    "dangling_roundabout",
                    f"Roundabout {roundabout['id']} references a missing node.",
                    feature_id=roundabout["id"],
                )
            )
    edge_id_set = set(edge_ids)
    for restriction in network["turn_restrictions"]:
        if restriction["from_edge"] not in edge_id_set or restriction["to_edge"] not in edge_id_set:
            issues.append(
                _issue(
                    "error",
                    "dangling_turn_restriction",
                    f"Turn restriction {restriction['id']} references a missing edge.",
                    feature_id=restriction["id"],
                )
            )
    for route in network["transit_routes"]:
        missing_nodes = [node_id for node_id in route.get("node_ids", []) if node_id not in node_set]
        if missing_nodes:
            issues.append(
                _issue(
                    "error",
                    "dangling_transit_stop",
                    f"Transit route {route['id']} references missing nodes: {', '.join(missing_nodes)}.",
                    feature_id=route["id"],
                )
            )
        missing_edges = [edge_id for edge_id in route.get("edge_ids", []) if edge_id not in edge_id_set]
        if missing_edges:
            issues.append(
                _issue(
                    "error",
                    "dangling_transit_edge",
                    f"Transit route {route['id']} references missing edges: {', '.join(missing_edges)}.",
                    feature_id=route["id"],
                )
            )
    for profile in network["demand_profiles"]:
        for pair in profile.get("od_pairs", []):
            if pair["origin"] not in node_set or pair["destination"] not in node_set:
                issues.append(
                    _issue(
                        "error",
                        "dangling_demand",
                        f"Demand profile {profile['id']} references a missing node.",
                        feature_id=profile["id"],
                    )
                )
            if int(pair["vehicles"]) < 0:
                issues.append(_issue("error", "negative_demand", "Vehicle counts cannot be negative.", feature_id=profile["id"]))
    for zone in network["land_uses"]:
        if int(zone.get("housing_units") or 0) < 0:
            issues.append(_issue("error", "negative_housing", f"Land use {zone['id']} has negative housing units.", feature_id=zone["id"]))

    if not network["nodes"]:
        issues.append(_issue("warning", "empty_network", "The network has no intersections."))
    elif network["edges"]:
        graph = nx.DiGraph()
        graph.add_nodes_from(node_ids)
        graph.add_edges_from((edge["from_node"], edge["to_node"]) for edge in network["edges"])
        undirected = graph.to_undirected()
        if graph.number_of_nodes() > 1 and not nx.is_connected(undirected):
            issues.append(
                _issue(
                    "warning",
                    "not_weakly_connected",
                    "The undirected road network is disconnected. Some places cannot reach others even if direction is ignored.",
                )
            )
        if graph.number_of_nodes() > 1 and nx.is_weakly_connected(graph) and not nx.is_strongly_connected(graph):
            issues.append(
                _issue(
                    "warning",
                    "not_strongly_connected",
                    "One-way restrictions mean at least one intersection cannot reach another. This may be intentional.",
                )
            )
        isolated = [node_id for node_id in node_ids if undirected.degree(node_id) == 0]
        if isolated:
            issues.append(
                _issue(
                    "warning",
                    "isolated_nodes",
                    "Intersections with no roads: " + ", ".join(isolated),
                )
            )
    names: dict[str, list[str]] = {}
    for road_id, road in road_catalog(network).items():
        names.setdefault(road["name"].lower(), []).append(road_id)
    for name, road_ids in names.items():
        if len(road_ids) > 1:
            issues.append(
                _issue(
                    "warning",
                    "duplicate_road_name",
                    f"Road name '{name}' is shared by {', '.join(road_ids)}. Refer to these roads by id.",
                )
            )
    return issues


def network_stats(network: dict[str, Any]) -> dict[str, int]:
    return {
        "nodes": len(network["nodes"]),
        "edges": len(network["edges"]),
        "roads": len(road_catalog(network)),
        "signals": len(network["signals"]),
        "roundabouts": len(network["roundabouts"]),
        "bridges": len(network["bridges"]),
        "tunnels": len(network["tunnels"]),
        "land_uses": len(network["land_uses"]),
        "transit_routes": len(network["transit_routes"]),
    }


def feature_detail(network: dict[str, Any], feature_id: str) -> dict[str, Any] | None:
    catalog = road_catalog(network)
    if feature_id in catalog:
        road = catalog[feature_id]
        directed = []
        for edge in road["edges"]:
            directed.append(
                {
                    "edge_id": edge["id"],
                    "from_node": edge["from_node"],
                    "to_node": edge["to_node"],
                    "direction": edge_cardinal(network, edge),
                    "lanes": edge["lanes"],
                    "speed_kmh": edge["speed_kmh"],
                    "length_m": edge["length_m"],
                    "bridge_id": edge.get("bridge_id"),
                    "tunnel_id": edge.get("tunnel_id"),
                }
            )
        directions = {item["direction"] for item in directed}
        return {
            "feature_type": "road",
            "feature_id": feature_id,
            "name": road["name"],
            "road_type": road["edges"][0]["road_type"],
            "oneway": len(directed) == 1,
            "directions": sorted(item for item in directions if item),
            "directed_edges": directed,
            "lanes_by_direction": [edge["lanes"] for edge in road["edges"]],
            "speed_kmh_by_direction": [edge["speed_kmh"] for edge in road["edges"]],
            "length_m": max(edge["length_m"] for edge in road["edges"]),
        }
    edge = get_edge(network, feature_id)
    if edge is not None:
        return feature_detail(network, edge["road_id"])
    node = get_node(network, feature_id)
    if node is not None:
        intersection = intersection_for_node(network, feature_id)
        signal = signal_for_node(network, feature_id)
        roundabout = next((item for item in network["roundabouts"] if item["node_id"] == feature_id), None)
        return {
            "feature_type": "intersection",
            "feature_id": node["id"],
            "name": node["name"],
            "lon": node["lon"],
            "lat": node["lat"],
            "kind": node["kind"],
            "control": None if intersection is None else intersection["control"],
            "signal": signal,
            "roundabout": roundabout,
            "incident_edge_ids": [item["id"] for item in incident_edges(network, feature_id)],
        }
    return None


def to_geojson(
    network: dict[str, Any],
    link_metrics: dict[str, dict[str, Any]] | None = None,
    changed_ids: set[str] | None = None,
) -> dict[str, Any]:
    link_metrics = link_metrics or {}
    changed_ids = changed_ids or set()
    road_features = []
    for edge in network["edges"]:
        metric = link_metrics.get(edge["id"]) or {}
        road_features.append(
            {
                "type": "Feature",
                "id": edge["id"],
                "geometry": {"type": "LineString", "coordinates": edge["geometry"]},
                "properties": {
                    "edge_id": edge["id"],
                    "road_id": edge["road_id"],
                    "name": edge["name"],
                    "from_node": edge["from_node"],
                    "to_node": edge["to_node"],
                    "lanes": edge["lanes"],
                    "free_flow_speed_kmh": edge["speed_kmh"],
                    "length_m": edge["length_m"],
                    "road_type": edge["road_type"],
                    "direction": edge_cardinal(network, edge),
                    "oneway": len(edges_for_road(network, edge["road_id"])) == 1,
                    "bridge_id": edge.get("bridge_id"),
                    "tunnel_id": edge.get("tunnel_id"),
                    "simulated_speed_kmh": metric.get("speed_kmh"),
                    "flow_vph": metric.get("flow_vph"),
                    "volume_capacity_ratio": metric.get("volume_capacity_ratio"),
                    "metric_quality": metric.get("quality"),
                    "has_metric": metric.get("speed_kmh") is not None,
                    "changed": edge["road_id"] in changed_ids or edge["id"] in changed_ids,
                },
            }
        )
    node_features = []
    for node in network["nodes"]:
        signal = signal_for_node(network, node["id"])
        intersection = intersection_for_node(network, node["id"])
        node_features.append(
            {
                "type": "Feature",
                "id": node["id"],
                "geometry": {"type": "Point", "coordinates": [node["lon"], node["lat"]]},
                "properties": {
                    "node_id": node["id"],
                    "name": node["name"],
                    "kind": node["kind"],
                    "control": None if intersection is None else intersection["control"],
                    "signal_id": None if signal is None else signal["id"],
                    "changed": node["id"] in changed_ids,
                },
            }
        )
    change_features = []
    for edge in network["edges"]:
        if edge["road_id"] in changed_ids or edge["id"] in changed_ids:
            change_features.append(
                {
                    "type": "Feature",
                    "id": f"change_{edge['id']}",
                    "geometry": {"type": "Point", "coordinates": edge_midpoint(edge)},
                    "properties": {"feature_id": edge["road_id"], "name": edge["name"], "change": "road"},
                }
            )
    for node in network["nodes"]:
        if node["id"] in changed_ids:
            change_features.append(
                {
                    "type": "Feature",
                    "id": f"change_{node['id']}",
                    "geometry": {"type": "Point", "coordinates": [node["lon"], node["lat"]]},
                    "properties": {"feature_id": node["id"], "name": node["name"], "change": "intersection"},
                }
            )
    return {
        "roads": {"type": "FeatureCollection", "features": road_features},
        "nodes": {"type": "FeatureCollection", "features": node_features},
        "changes": {"type": "FeatureCollection", "features": change_features},
        "bbox": bbox_of_nodes(network["nodes"]),
    }


def shortest_edge_path(
    network: dict[str, Any],
    origin: str,
    destination: str,
    weight_by_edge: dict[str, float] | None = None,
) -> list[dict[str, Any]] | None:
    graph = nx.DiGraph()
    graph.add_nodes_from(node["id"] for node in network["nodes"])
    for edge in network["edges"]:
        weight = edge["length_m"] / max(edge["speed_kmh"] * 1000.0 / 3600.0, 0.1)
        if weight_by_edge is not None:
            weight = weight_by_edge.get(edge["id"], weight)
        graph.add_edge(edge["from_node"], edge["to_node"], weight=weight, edge_id=edge["id"])
    try:
        node_path = nx.shortest_path(graph, origin, destination, weight="weight")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None
    by_pair = {(edge["from_node"], edge["to_node"]): edge for edge in network["edges"]}
    resolved = []
    for start, end in zip(node_path, node_path[1:]):
        edge = by_pair.get((start, end))
        if edge is None:
            return None
        resolved.append(edge)
    return resolved


def _roads_matching_label(catalog: dict[str, dict[str, Any]], label: str) -> list[dict[str, Any]]:
    cleaned = re.sub(r"^road\s+", "", label.strip(), flags=re.IGNORECASE)
    if cleaned in catalog:
        return [catalog[cleaned]]
    lowered = cleaned.lower()
    id_matches = [road for road_id, road in catalog.items() if road_id.lower() == lowered]
    if id_matches:
        return id_matches
    return [road for road in catalog.values() if road["name"].lower() == lowered]


def _roads_near_coordinate(roads: list[dict[str, Any]], coordinates: list[float]) -> list[dict[str, Any]]:
    lon, lat = float(coordinates[0]), float(coordinates[1])
    found = []
    for road in roads:
        distance = min(line_distance_m(lon, lat, edge["geometry"]) for edge in road["edges"])
        if distance <= COORD_MATCH_M:
            found.append(road)
    return found


def _nearest_nodes(network: dict[str, Any], coordinates: list[float], limit: int) -> list[dict[str, Any]]:
    lon, lat = float(coordinates[0]), float(coordinates[1])
    ranked = []
    for node in network["nodes"]:
        ranked.append(
            {
                "node": node,
                "distance_m": haversine_m(lon, lat, node["lon"], node["lat"]),
            }
        )
    ranked.sort(key=lambda item: item["distance_m"])
    return ranked[:limit]


def _intersection_of_roads(network: dict[str, Any], left: str, right: str) -> dict[str, Any]:
    left_roads = _roads_matching_label(road_catalog(network), left)
    right_roads = _roads_matching_label(road_catalog(network), right)
    if not left_roads or not right_roads:
        return {
            "status": "not_found",
            "message": f"Could not find both roads '{left}' and '{right}'.",
            "candidates": [],
        }
    left_nodes = {node_id for road in left_roads for edge in road["edges"] for node_id in (edge["from_node"], edge["to_node"])}
    right_nodes = {node_id for road in right_roads for edge in road["edges"] for node_id in (edge["from_node"], edge["to_node"])}
    common = sorted(left_nodes & right_nodes)
    if len(common) == 1:
        return {"status": "unique", "node": get_node(network, common[0]), "note": None}
    if len(common) > 1:
        return {
            "status": "ambiguous",
            "message": f"'{left}' and '{right}' meet at more than one intersection.",
            "candidates": [_node_summary(get_node(network, node_id)) for node_id in common],
        }
    return {
        "status": "not_found",
        "message": f"'{left}' and '{right}' do not share an intersection.",
        "candidates": [],
    }


def _valid_coordinate(coordinates: Any) -> bool:
    return (
        isinstance(coordinates, list)
        and len(coordinates) == 2
        and valid_lon_lat(coordinates[0], coordinates[1])
    )


def _issue(severity: str, code: str, message: str, feature_id: str | None = None) -> dict[str, Any]:
    issue = {"severity": severity, "code": code, "message": message}
    if feature_id:
        issue["feature_id"] = feature_id
    return issue
