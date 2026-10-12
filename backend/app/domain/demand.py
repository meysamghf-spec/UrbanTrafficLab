"""Demand profiles, transit paths, and land-use trip generation.

Generated trips are synthetic. They are never labeled as observed counts.
"""

from __future__ import annotations

from typing import Any

from app.domain.geo import haversine_m, polygon_centroid
from app.domain.network import content_hash, get_node, shortest_edge_path

DEFAULT_TRIP_RATE_PER_UNIT = 0.1
LAND_USE_PROFILE_ID = "land_use_generated"


def find_profile(network: dict[str, Any], profile_id: str) -> dict[str, Any] | None:
    for profile in network["demand_profiles"]:
        if profile["id"] == profile_id:
            return profile
    return None


def rebuild_land_use_demand(network: dict[str, Any]) -> None:
    kept = [profile for profile in network["demand_profiles"] if profile["id"] != LAND_USE_PROFILE_ID]
    zones = [zone for zone in network["land_uses"] if int(zone.get("housing_units") or 0) > 0]
    if not zones or not network["nodes"]:
        network["demand_profiles"] = kept
        return
    od_pairs: list[dict[str, Any]] = []
    total = 0
    for zone in zones:
        rate = float(zone.get("trip_rate_per_unit_peak") or DEFAULT_TRIP_RATE_PER_UNIT)
        trips = int(round(int(zone["housing_units"]) * rate))
        centroid = polygon_centroid(zone.get("geometry"))
        if centroid is None:
            centroid = [network["nodes"][0]["lon"], network["nodes"][0]["lat"]]
        origin = _nearest_node(network, centroid[0], centroid[1])
        if origin is None or trips <= 0:
            continue
        destinations = sorted(
            (node for node in network["nodes"] if node["id"] != origin["id"]),
            key=lambda node: node["id"],
        )
        if not destinations:
            continue
        base, remainder = divmod(trips, len(destinations))
        for index, destination in enumerate(destinations):
            count = base + (1 if index < remainder else 0)
            if count <= 0:
                continue
            od_pairs.append(
                {
                    "id": f"lu_{zone['id']}_{origin['id']}_{destination['id']}",
                    "origin": origin["id"],
                    "destination": destination["id"],
                    "vehicles": count,
                    "vehicle_class": "car",
                    "zone_id": zone["id"],
                }
            )
            total += count
    kept.append(
        {
            "id": LAND_USE_PROFILE_ID,
            "name": "Land-use generated peak",
            "peak": "generated",
            "seed": 42,
            "synthetic": True,
            "observed": False,
            "horizon_s": 3600,
            "vehicle_classes": [{"id": "car", "share": 1.0}],
            "od_pairs": od_pairs,
            "vehicles_total": total,
            "housing_units_total": sum(int(zone["housing_units"]) for zone in zones),
            "assumptions": [
                "Land-use trips are synthetic. They are not observed traffic counts.",
                "The trip rate is an assumption, not a calibrated rate.",
                "Origins are the network node nearest each zone centroid.",
                "Destinations are the other nodes, spread by the largest-remainder method.",
                "Peak-hour vehicle trips are not the same as person trips or daily volume.",
            ],
        }
    )
    network["demand_profiles"] = kept


def refresh_transit(network: dict[str, Any]) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    node_ids = {node["id"] for node in network["nodes"]}
    for route in network["transit_routes"]:
        original = list(route.get("node_ids") or [])
        route["node_ids"] = [node_id for node_id in original if node_id in node_ids]
        if route["node_ids"] != original:
            warnings.append(
                {
                    "code": "transit_stops_removed",
                    "message": (
                        f"Transit route {route['id']} referenced intersections that are no longer "
                        "in the network. Those stops were removed from the route."
                    ),
                }
            )
        if len(route["node_ids"]) < 2:
            route["edge_ids"] = []
            route["valid"] = False
            warnings.append(
                {
                    "code": "transit_route_invalid",
                    "message": f"Transit route {route['id']} does not have a connected path.",
                }
            )
            continue
        edge_ids: list[str] = []
        valid = True
        for start, end in zip(route["node_ids"], route["node_ids"][1:]):
            path = shortest_edge_path(network, start, end)
            if not path:
                valid = False
                break
            edge_ids.extend(edge["id"] for edge in path)
        route["edge_ids"] = edge_ids if valid else []
        route["valid"] = valid
        if not valid:
            warnings.append(
                {
                    "code": "transit_route_invalid",
                    "message": (
                        f"Transit route {route['id']} has no remaining path between its stops. "
                        "It will not be sent to the simulator."
                    ),
                }
            )
    return warnings


def select_profiles(
    network: dict[str, Any],
    demand_profile_id: str,
    include_generated: bool,
) -> list[dict[str, Any]]:
    profile = find_profile(network, demand_profile_id)
    if profile is None:
        raise KeyError(demand_profile_id)
    selected = [profile]
    if include_generated:
        generated = find_profile(network, LAND_USE_PROFILE_ID)
        if generated is not None:
            selected.append(generated)
    return selected


def total_vehicles(profiles: list[dict[str, Any]]) -> int:
    return sum(int(pair["vehicles"]) for profile in profiles for pair in profile["od_pairs"])


def demand_fingerprint(profiles: list[dict[str, Any]]) -> str:
    return content_hash(profiles)


def transit_fingerprint(network: dict[str, Any]) -> str:
    routes = [
        {
            "id": route["id"],
            "mode": route["mode"],
            "node_ids": route.get("node_ids") or [],
            "edge_ids": route.get("edge_ids") or [],
            "headway_min": route.get("headway_min"),
            "valid": route.get("valid", False),
        }
        for route in network["transit_routes"]
    ]
    return content_hash(routes)


def active_transit(network: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        route
        for route in network["transit_routes"]
        if route.get("valid") and route.get("edge_ids") and float(route.get("headway_min") or 0) > 0
    ]


def _nearest_node(network: dict[str, Any], lon: float, lat: float) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_distance = float("inf")
    for node in network["nodes"]:
        distance = haversine_m(lon, lat, node["lon"], node["lat"])
        if distance < best_distance:
            best = node
            best_distance = distance
    return best


def node_exists(network: dict[str, Any], node_id: str) -> bool:
    return get_node(network, node_id) is not None
