"""Static traffic assignment used only when a user asks for an estimate.

Results from this module are ESTIMATED. They are not SUMO output and they are
not observed traffic.
"""

from __future__ import annotations

from typing import Any

from app.domain.demand import active_transit, total_vehicles
from app.domain.metrics import metric, summarize
from app.domain.network import shortest_edge_path

BPR_ALPHA = 0.15
BPR_BETA = 4.0
LANE_CAPACITY_VPH = 1600.0
ASSIGNMENT_ITERATIONS = 4

ESTIMATOR_WARNINGS = [
    {
        "code": "estimated_result",
        "message": (
            "These figures are ESTIMATED by a static bureau-of-public-roads assignment. "
            "They are not a SUMO simulation and they are not measured traffic."
        ),
    },
    {
        "code": "static_assignment",
        "message": (
            "The estimator does not represent queues, signals, departure-time dynamics, "
            "or incomplete trips inside the hour. Waiting time and queue length are omitted."
        ),
    },
    {
        "code": "capacity_assumption",
        "message": f"Each lane is assumed to carry {LANE_CAPACITY_VPH:.0f} vehicles per hour. That capacity is not calibrated.",
    },
]


def estimate(network: dict[str, Any], profiles: list[dict[str, Any]], seed: int, repeats: int = 1) -> dict[str, Any]:
    """Run the deterministic assignment.

    The seed is recorded for provenance. The assignment itself does not draw
    random numbers, so repeats are identical and the sample standard deviation
    is zero when more than one repeat is requested.
    """
    del seed  # Recorded by the caller. The calculation does not use a random draw.
    runs = [assign_once(network, profiles) for _ in range(max(1, repeats))]
    quality = "ESTIMATED"
    metrics = {}
    keys = [
        "completed_trips",
        "departed_vehicles",
        "arrived_vehicles",
        "unfinished_trips",
        "unroutable_trips",
        "mean_travel_time_s",
        "mean_speed_kmh",
        "mean_time_loss_s",
        "network_throughput_vph",
    ]
    for key in keys:
        summary = summarize([run["metrics"][key] for run in runs])
        notes = []
        if repeats > 1:
            notes.append(
                "The analytical estimator is deterministic for a given network and demand. "
                "Repeats do not add driver variability, so the standard deviation is zero."
            )
        metrics[key] = metric(key, summary["mean"], quality=quality, notes=notes, summary=summary if repeats > 1 else None)
    metrics["mean_waiting_time_s"] = metric(
        "mean_waiting_time_s",
        None,
        quality=quality,
        notes=["Waiting time is not represented by the static estimator."],
    )
    warnings = list(ESTIMATOR_WARNINGS)
    if runs[0]["unroutable_details"]:
        warnings.append(
            {
                "code": "unroutable_demand",
                "message": "Some origin-destination pairs have no directed path and were not assigned.",
            }
        )
    if active_transit(network):
        warnings.append(
            {
                "code": "simplified_transit",
                "message": "Buses are included as vehicle trips without passengers or dwell time.",
            }
        )
    return {
        "metrics": metrics,
        "link_metrics": runs[0]["link_metrics"],
        "timesteps": [],
        "warnings": warnings,
        "unroutable_trips": runs[0]["metrics"]["unroutable_trips"],
        "quality": quality,
        "result_usability": "partial" if runs[0]["metrics"]["unroutable_trips"] else "full",
        "playback_available": False,
    }


def assign_once(network: dict[str, Any], profiles: list[dict[str, Any]]) -> dict[str, Any]:
    horizon_s = int(profiles[0]["horizon_s"]) if profiles else 3600
    demand = _grouped_demand(network, profiles, horizon_s)
    edges = network["edges"]
    free_flow = {}
    capacity = {}
    for edge in edges:
        speed_m_s = max(float(edge["speed_kmh"]) * 1000.0 / 3600.0, 0.1)
        free_flow[edge["id"]] = float(edge["length_m"]) / speed_m_s
        capacity[edge["id"]] = max(float(edge["lanes"]) * LANE_CAPACITY_VPH, 1.0)
    times = dict(free_flow)
    flows = {edge["id"]: 0.0 for edge in edges}
    for iteration in range(1, ASSIGNMENT_ITERATIONS + 1):
        assigned = {edge["id"]: 0.0 for edge in edges}
        for item in demand:
            path = shortest_edge_path(network, item["origin"], item["destination"], times)
            if not path:
                continue
            for edge in path:
                assigned[edge["id"]] += item["vph"]
        for edge in edges:
            edge_id = edge["id"]
            if iteration == 1:
                flows[edge_id] = assigned[edge_id]
            else:
                weight = 1.0 / iteration
                flows[edge_id] = (1.0 - weight) * flows[edge_id] + weight * assigned[edge_id]
            ratio = flows[edge_id] / capacity[edge_id]
            times[edge_id] = free_flow[edge_id] * (1.0 + BPR_ALPHA * (ratio**BPR_BETA))
    completed_distance = 0.0
    completed_time = 0.0
    free_time = 0.0
    completed_vehicles = 0
    unroutable = 0
    unroutable_details = []
    for item in demand:
        path = shortest_edge_path(network, item["origin"], item["destination"], times)
        vehicles = item["vehicles"]
        if not path:
            unroutable += vehicles
            unroutable_details.append({"origin": item["origin"], "destination": item["destination"], "vehicles": vehicles})
            continue
        path_time = sum(times[edge["id"]] for edge in path)
        path_free = sum(free_flow[edge["id"]] for edge in path)
        path_distance = sum(float(edge["length_m"]) for edge in path)
        completed_vehicles += vehicles
        completed_time += vehicles * path_time
        free_time += vehicles * path_free
        completed_distance += vehicles * path_distance
    mean_travel = None if completed_vehicles == 0 else completed_time / completed_vehicles
    mean_speed = None
    if completed_time > 0:
        mean_speed = (completed_distance / completed_time) * 3.6
    mean_loss = None if completed_vehicles == 0 else (completed_time - free_time) / completed_vehicles
    hours = horizon_s / 3600.0 if horizon_s else None
    throughput = None if not hours else completed_vehicles / hours
    link_metrics = []
    for edge in edges:
        travel = times[edge["id"]]
        speed = None if travel <= 0 else float(edge["length_m"]) / travel * 3.6
        cap = capacity[edge["id"]]
        link_metrics.append(
            {
                "edge_id": edge["id"],
                "road_id": edge["road_id"],
                "flow_vph": flows[edge["id"]],
                "travel_time_s": travel,
                "free_flow_time_s": free_flow[edge["id"]],
                "speed_kmh": speed,
                "volume_capacity_ratio": None if cap <= 0 else flows[edge["id"]] / cap,
                "occupancy": None,
                "queue_length_m": None,
                "waiting_time_s": None,
                "quality": "ESTIMATED",
            }
        )
    return {
        "metrics": {
            "completed_trips": completed_vehicles,
            "departed_vehicles": completed_vehicles,
            "arrived_vehicles": completed_vehicles,
            "unfinished_trips": unroutable,
            "unroutable_trips": unroutable,
            "mean_travel_time_s": mean_travel,
            "mean_speed_kmh": mean_speed,
            "mean_time_loss_s": mean_loss,
            "network_throughput_vph": throughput,
        },
        "link_metrics": link_metrics,
        "unroutable_details": unroutable_details,
    }


def _grouped_demand(network: dict[str, Any], profiles: list[dict[str, Any]], horizon_s: int) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], int] = {}
    for profile in profiles:
        for pair in profile["od_pairs"]:
            key = (pair["origin"], pair["destination"])
            grouped[key] = grouped.get(key, 0) + int(pair["vehicles"])
    for route in active_transit(network):
        if len(route["node_ids"]) < 2:
            continue
        count = max(1, int(horizon_s / (float(route["headway_min"]) * 60.0)))
        key = (route["node_ids"][0], route["node_ids"][-1])
        grouped[key] = grouped.get(key, 0) + count
    hours = horizon_s / 3600.0 if horizon_s else 1.0
    return [
        {
            "origin": origin,
            "destination": destination,
            "vehicles": vehicles,
            "vph": vehicles / hours,
        }
        for (origin, destination), vehicles in sorted(grouped.items())
    ]


def demand_vehicle_count(network: dict[str, Any], profiles: list[dict[str, Any]]) -> int:
    horizon_s = int(profiles[0]["horizon_s"]) if profiles else 3600
    return total_vehicles(profiles) + sum(
        max(1, int(horizon_s / (float(route["headway_min"]) * 60.0))) for route in active_transit(network)
    )
