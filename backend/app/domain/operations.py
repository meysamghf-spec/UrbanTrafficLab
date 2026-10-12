"""Typed network-editing operations.

Handlers mutate the supplied document only after validation succeeds. Callers
pass a copy when a failed operation must leave the stored network unchanged.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domain.demand import rebuild_land_use_demand
from app.domain.geo import haversine_m, valid_lon_lat
from app.domain.network import (
    add_directed_edge,
    edge_cardinal,
    edges_for_road,
    get_edge,
    get_node,
    incident_edges,
    intersection_for_node,
    make_edge,
    resolve_node,
    resolve_road,
    reverse_edge,
    road_catalog,
    shortest_edge_path,
    signal_for_node,
    valid_feature_id,
)

MPH_TO_KMH = 1.609344


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SpeedParams(StrictModel):
    speed_kmh: float | None = None
    speed_mph: float | None = None

    def canonical_kmh(self) -> float:
        if self.speed_kmh is not None and self.speed_mph is not None:
            raise ValueError("Provide speed in either km/h or mph, not both.")
        if self.speed_kmh is not None:
            return float(self.speed_kmh)
        if self.speed_mph is not None:
            return float(self.speed_mph) * MPH_TO_KMH
        raise ValueError("A speed limit is required.")


class DirectionParams(StrictModel):
    direction: str

    def cleaned(self) -> str:
        value = self.direction.lower().strip()
        allowed = {"eastbound", "westbound", "northbound", "southbound", "bidirectional"}
        if value not in allowed:
            raise ValueError("Direction must be a cardinal one-way direction or bidirectional.")
        return value


class LaneParams(StrictModel):
    lanes: int = Field(ge=1, le=8)


class AddRoadParams(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    from_node: str
    to_node: str
    road_id: str | None = None
    lanes: int = Field(default=1, ge=1, le=8)
    speed_kmh: float = Field(default=40, gt=0, le=200)
    bidirectional: bool = True
    road_type: str = "residential"


class AddIntersectionParams(StrictModel):
    node_id: str
    name: str = Field(min_length=1, max_length=120)
    lon: float
    lat: float


class RemoveIntersectionParams(StrictModel):
    remove_incident_roads: bool = False


class SignalParams(StrictModel):
    cycle_s: int = Field(default=90, ge=30, le=300)


class ModifySignalParams(StrictModel):
    cycle_s: int = Field(ge=30, le=300)


class EmptyParams(StrictModel):
    pass


class RoundaboutParams(StrictModel):
    diameter_m: float = Field(default=30, gt=0, le=500)
    circulating_speed_kmh: float = Field(default=25, gt=0, le=80)


class ModifyRoundaboutParams(StrictModel):
    diameter_m: float | None = Field(default=None, gt=0, le=500)
    circulating_speed_kmh: float | None = Field(default=None, gt=0, le=80)


class StructureParams(StrictModel):
    name: str | None = Field(default=None, max_length=120)


class TurnParams(StrictModel):
    from_edge: str | None = None
    to_edge: str | None = None
    from_road: str | None = None
    to_road: str | None = None


class LandUseParams(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = "residential"
    housing_units: int = Field(ge=0, le=100_000)
    trip_rate_per_unit_peak: float | None = Field(default=None, gt=0, le=5)


class ModifyLandUseParams(StrictModel):
    housing_units: int | None = Field(default=None, ge=0, le=100_000)
    kind: str | None = None
    trip_rate_per_unit_peak: float | None = Field(default=None, gt=0, le=5)


class TransitParams(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    mode: str = "bus"
    node_ids: list[str] | None = None
    from_node: str | None = None
    to_node: str | None = None
    headway_min: float = Field(default=15, gt=0, le=180)
    route_id: str | None = None


class DemandParams(StrictModel):
    action: str
    peaks: list[str] | None = None
    peak: str | None = None
    factor: float | None = Field(default=None, gt=0, le=10)
    seed: int | None = Field(default=None, ge=0, le=2_000_000_000)


PARAM_MODELS: dict[str, type[StrictModel]] = {
    "ADD_ROAD": AddRoadParams,
    "REMOVE_ROAD": EmptyParams,
    "MODIFY_ROAD_DIRECTION": DirectionParams,
    "MODIFY_LANE_COUNT": LaneParams,
    "MODIFY_SPEED_LIMIT": SpeedParams,
    "ADD_INTERSECTION": AddIntersectionParams,
    "REMOVE_INTERSECTION": RemoveIntersectionParams,
    "ADD_TRAFFIC_SIGNAL": SignalParams,
    "MODIFY_TRAFFIC_SIGNAL": ModifySignalParams,
    "REMOVE_TRAFFIC_SIGNAL": EmptyParams,
    "ADD_ROUNDABOUT": RoundaboutParams,
    "MODIFY_ROUNDABOUT": ModifyRoundaboutParams,
    "REMOVE_ROUNDABOUT": EmptyParams,
    "ADD_BRIDGE": StructureParams,
    "REMOVE_BRIDGE": EmptyParams,
    "ADD_TUNNEL": StructureParams,
    "REMOVE_TUNNEL": EmptyParams,
    "ADD_ROAD_CONNECTION": TurnParams,
    "REMOVE_ROAD_CONNECTION": TurnParams,
    "ADD_LAND_USE_DEVELOPMENT": LandUseParams,
    "MODIFY_LAND_USE": ModifyLandUseParams,
    "ADD_OR_MODIFY_PUBLIC_TRANSPORT": TransitParams,
    "MODIFY_DEMAND_PROFILE": DemandParams,
}

OPERATION_TYPES = tuple(PARAM_MODELS)


class Eval:
    def __init__(self) -> None:
        self.errors: list[dict[str, Any]] = []
        self.warnings: list[dict[str, Any]] = []
        self.preconditions: list[str] = []
        self.expected_effects: list[str] = []
        self.candidates: list[dict[str, Any]] = []
        self.simplified = False
        self.units: dict[str, str] = {}
        self.touched: list[str] = []
        self.resolved_target: dict[str, Any] | None = None
        self.reversibility = {"reversible": True, "strategy": "scenario_rollback"}

    def error(self, code: str, message: str) -> None:
        self.errors.append({"code": code, "message": message})

    def warn(self, code: str, message: str) -> None:
        self.warnings.append({"code": code, "message": message})

    @property
    def ok(self) -> bool:
        return not self.errors


def validation_status(ev: Eval) -> str:
    if any(item["code"] == "ambiguous" for item in ev.errors):
        return "ambiguous"
    if ev.errors:
        return "invalid"
    if ev.warnings:
        return "valid_with_warnings"
    return "valid"


def draft_operation(
    operation_type: str,
    label: str,
    parameters: dict[str, Any],
    *,
    operation_id: str,
    source: dict[str, Any] | None = None,
    decision: str = "pending",
    feature_id: str | None = None,
    coordinates: list[float] | None = None,
) -> dict[str, Any]:
    return {
        "id": operation_id,
        "operation_type": operation_type,
        "target": {"label": label, "feature_id": feature_id, "coordinates": coordinates},
        "parameters": parameters,
        "units": {},
        "source": source or {"kind": "user"},
        "preconditions": [],
        "expected_effects": [],
        "validation_status": "pending",
        "warnings": [],
        "errors": [],
        "candidates": [],
        "reversibility": {"reversible": True, "strategy": "scenario_rollback"},
        "simplified": False,
        "decision": decision,
        "touched": [],
    }


def evaluate(network: dict[str, Any], operation: dict[str, Any]) -> Eval:
    ev = Eval()
    operation_type = operation.get("operation_type")
    model = PARAM_MODELS.get(str(operation_type))
    if model is None:
        ev.error("unknown_operation", f"Unsupported operation type: {operation_type}.")
        ev.reversibility = {"reversible": False, "strategy": "not_applied"}
        return ev
    try:
        params = model.model_validate(operation.get("parameters") or {})
    except ValidationError as exc:
        ev.error("invalid_parameters", "Parameters do not match the operation schema.")
        ev.errors[-1]["details"] = _pydantic_details(exc)
        return ev
    handler = HANDLERS[str(operation_type)]
    handler(network, operation, params, ev)
    if not ev.ok:
        ev.expected_effects = []
    return ev


def merge_eval(operation: dict[str, Any], ev: Eval) -> dict[str, Any]:
    merged = dict(operation)
    merged["validation_status"] = validation_status(ev)
    merged["warnings"] = ev.warnings
    merged["errors"] = ev.errors
    merged["candidates"] = ev.candidates
    merged["preconditions"] = ev.preconditions
    merged["expected_effects"] = ev.expected_effects
    merged["simplified"] = ev.simplified
    merged["touched"] = ev.touched
    merged["reversibility"] = ev.reversibility
    if ev.units:
        merged["units"] = ev.units
    if ev.resolved_target is not None:
        merged["target"] = ev.resolved_target
    return merged


def HANDLER_SPEED(network: dict[str, Any], operation: dict[str, Any], params: SpeedParams, ev: Eval) -> None:
    ev.units = {"speed": "km/h"}
    road = _need_road(network, operation, ev)
    if road is None:
        return
    try:
        speed = params.canonical_kmh()
    except ValueError as exc:
        ev.error("invalid_parameters", str(exc))
        return
    if not 0 < speed <= 200:
        ev.error("invalid_speed", "Speed must be greater than 0 and at most 200 km/h.")
        return
    if speed > 130:
        ev.warn("high_speed", "A speed limit above 130 km/h is outside ordinary urban operation.")
    edges = road["edges"]
    previous = sorted({float(edge["speed_kmh"]) for edge in edges})
    if previous == [round(speed, 6)] or previous == [speed]:
        ev.warn("already_in_effect", "The road already has this speed limit.")
    ev.preconditions.append(f"Road {road['id']} exists.")
    ev.expected_effects.append(
        f"Set the speed limit on {road['id']} ({road['name']}) from {previous} km/h to {round(speed, 3)} km/h."
    )
    _touch_road(ev, road)
    for edge in edges_for_road(network, road["id"]):
        edge["speed_kmh"] = round(speed, 6)


def HANDLER_DIRECTION(network: dict[str, Any], operation: dict[str, Any], params: DirectionParams, ev: Eval) -> None:
    ev.units = {"direction": "cardinal"}
    road = _need_road(network, operation, ev)
    if road is None:
        return
    try:
        direction = params.cleaned()
    except ValueError as exc:
        ev.error("invalid_parameters", str(exc))
        return
    current = edges_for_road(network, road["id"])
    ev.preconditions.append(f"Road {road['id']} exists.")
    _touch_road(ev, road)
    if direction == "bidirectional":
        missing = [edge for edge in current if reverse_edge(network, edge)["id"] not in {item["id"] for item in current}]
        if not missing:
            ev.warn("already_in_effect", f"{road['id']} is already bidirectional.")
            ev.expected_effects.append(f"{road['id']} remains bidirectional.")
            return
        for edge in missing:
            add_directed_edge(network, reverse_edge(network, edge))
        ev.expected_effects.append(f"Restore the opposite direction on {road['id']} so it is bidirectional.")
        return
    matching = [edge for edge in current if edge_cardinal(network, edge) == direction]
    if not matching:
        ev.error(
            "direction_not_available",
            f"{road['id']} has no {direction} edge. The road was not changed.",
        )
        return
    remove_ids = {edge["id"] for edge in current if edge not in matching}
    if not remove_ids:
        ev.warn("already_in_effect", f"{road['id']} is already one-way {direction}.")
        ev.expected_effects.append(f"{road['id']} remains one-way {direction}.")
        return
    network["edges"] = [edge for edge in network["edges"] if edge["id"] not in remove_ids]
    _drop_restrictions(network, remove_ids)
    ev.expected_effects.append(
        f"Make {road['id']} ({road['name']}) one-way {direction} by removing {', '.join(sorted(remove_ids))}."
    )


def HANDLER_LANES(network: dict[str, Any], operation: dict[str, Any], params: LaneParams, ev: Eval) -> None:
    ev.units = {"lanes": "lanes"}
    road = _need_road(network, operation, ev)
    if road is None:
        return
    if params.lanes > 6:
        ev.warn("wide_section", "More than six lanes per direction is unusual and is stored as entered.")
    previous = sorted({int(edge["lanes"]) for edge in road["edges"]})
    ev.preconditions.append(f"Road {road['id']} exists.")
    ev.expected_effects.append(f"Set lane count on {road['id']} from {previous} to {params.lanes} per direction.")
    _touch_road(ev, road)
    for edge in edges_for_road(network, road["id"]):
        edge["lanes"] = int(params.lanes)


def HANDLER_ADD_ROAD(network: dict[str, Any], operation: dict[str, Any], params: AddRoadParams, ev: Eval) -> None:
    ev.units = {"lanes": "lanes", "speed": "km/h", "length": "m"}
    start = _need_named_node(network, params.from_node, ev, "from_node")
    end = _need_named_node(network, params.to_node, ev, "to_node")
    if start is None or end is None:
        return
    if start["id"] == end["id"]:
        ev.error("invalid_geometry", "A road needs two distinct intersections.")
        return
    road_id = params.road_id or _slug_id(params.name, "RD")
    if not valid_feature_id(road_id):
        ev.error("invalid_id", "Road ids must start with a letter and contain only letters, numbers, '_' or '-'.")
        return
    if road_id in road_catalog(network):
        road_id = _next_id(network, road_id)
        ev.warn("road_id_adjusted", f"The requested road id was in use. The new road id is {road_id}.")
    length = haversine_m(start["lon"], start["lat"], end["lon"], end["lat"])
    if length < 1:
        ev.error("invalid_geometry", "The intersections are less than 1 meter apart.")
        return
    ev.preconditions.append(f"Intersections {start['id']} and {end['id']} exist.")
    ev.expected_effects.append(
        f"Add road {road_id} ({params.name}) from {start['id']} to {end['id']} "
        f"with {params.lanes} lane(s) at {params.speed_kmh} km/h."
    )
    ev.touched = [road_id]
    ev.resolved_target = {"label": params.name, "feature_id": road_id, "coordinates": None}
    forward = make_edge(
        road_id=road_id,
        name=params.name,
        from_node=start,
        to_node=end,
        lanes=params.lanes,
        speed_kmh=params.speed_kmh,
        road_type=params.road_type,
    )
    add_directed_edge(network, forward)
    if params.bidirectional:
        add_directed_edge(
            network,
            make_edge(
                road_id=road_id,
                name=params.name,
                from_node=end,
                to_node=start,
                lanes=params.lanes,
                speed_kmh=params.speed_kmh,
                road_type=params.road_type,
            ),
        )
    else:
        ev.warn("oneway_new_road", "The new road is one-way from the first intersection to the second.")


def HANDLER_REMOVE_ROAD(network: dict[str, Any], operation: dict[str, Any], params: EmptyParams, ev: Eval) -> None:
    road = _need_road(network, operation, ev)
    if road is None:
        return
    edge_ids = {edge["id"] for edge in road["edges"]}
    ev.preconditions.append(f"Road {road['id']} exists.")
    ev.expected_effects.append(f"Remove road {road['id']} ({road['name']}) and its directed edges.")
    _touch_road(ev, road)
    _remove_road_record(network, road["id"], edge_ids, ev)


def HANDLER_ADD_INTERSECTION(network: dict[str, Any], operation: dict[str, Any], params: AddIntersectionParams, ev: Eval) -> None:
    ev.units = {"lon": "degrees", "lat": "degrees"}
    if not valid_feature_id(params.node_id):
        ev.error("invalid_id", "Intersection ids must start with a letter and contain only letters, numbers, '_' or '-'.")
        return
    if get_node(network, params.node_id) is not None:
        ev.error("already_exists", f"Intersection {params.node_id} already exists.")
        return
    if not valid_lon_lat(params.lon, params.lat):
        ev.error("invalid_geometry", "Longitude or latitude is outside the valid range.")
        return
    for node in network["nodes"]:
        if haversine_m(params.lon, params.lat, node["lon"], node["lat"]) < 5:
            ev.warn("very_close_node", f"The new intersection is within 5 meters of {node['id']}.")
    ev.preconditions.append(f"No intersection uses id {params.node_id}.")
    ev.expected_effects.append(f"Add intersection {params.node_id} ({params.name}). It is not connected until a road is added.")
    ev.touched = [params.node_id]
    ev.resolved_target = {"label": params.name, "feature_id": params.node_id, "coordinates": [params.lon, params.lat]}
    network["nodes"].append(
        {"id": params.node_id, "name": params.name, "lon": params.lon, "lat": params.lat, "kind": "intersection"}
    )
    network["intersections"].append({"node_id": params.node_id, "control": "uncontrolled"})
    ev.warn("isolated_until_connected", "The intersection has no roads yet.")


def HANDLER_REMOVE_INTERSECTION(
    network: dict[str, Any], operation: dict[str, Any], params: RemoveIntersectionParams, ev: Eval
) -> None:
    node = _need_node(network, operation, ev)
    if node is None:
        return
    incident = incident_edges(network, node["id"])
    if incident and not params.remove_incident_roads:
        ev.error(
            "incident_roads",
            "The intersection still has roads. Remove them explicitly or set remove_incident_roads=true.",
        )
        ev.candidates = [{"id": edge["road_id"], "name": edge["name"], "kind": "road"} for edge in incident]
        return
    ev.preconditions.append(f"Intersection {node['id']} exists.")
    ev.expected_effects.append(f"Remove intersection {node['id']} ({node['name']}).")
    ev.touched = [node["id"]]
    ev.resolved_target = {"label": node["name"], "feature_id": node["id"], "coordinates": [node["lon"], node["lat"]]}
    if incident:
        for road_id in sorted({edge["road_id"] for edge in incident}):
            edge_ids = {edge["id"] for edge in edges_for_road(network, road_id)}
            _remove_road_record(network, road_id, edge_ids, ev)
    network["nodes"] = [item for item in network["nodes"] if item["id"] != node["id"]]
    network["intersections"] = [item for item in network["intersections"] if item["node_id"] != node["id"]]
    network["signals"] = [item for item in network["signals"] if item["node_id"] != node["id"]]
    network["roundabouts"] = [item for item in network["roundabouts"] if item["node_id"] != node["id"]]


def HANDLER_ADD_SIGNAL(network: dict[str, Any], operation: dict[str, Any], params: SignalParams, ev: Eval) -> None:
    ev.units = {"cycle_s": "s"}
    node = _need_node(network, operation, ev)
    if node is None:
        return
    if signal_for_node(network, node["id"]) is not None:
        ev.error("already_exists", f"Intersection {node['id']} already has a traffic signal.")
        return
    if any(item["node_id"] == node["id"] for item in network["roundabouts"]):
        ev.error("control_conflict", "Remove the roundabout before adding a signal at this intersection.")
        return
    _mark_simplified(
        ev,
        "Fixed-time signal with two simplified phases. Turning conflicts, pedestrian phases, and detector timing are not modeled.",
    )
    green = max(10, (params.cycle_s - 10) // 2)
    signal = {
        "id": f"SIG_{node['id']}",
        "node_id": node["id"],
        "cycle_s": int(params.cycle_s),
        "phases": [
            {"id": "ph1", "green_s": green, "yellow_s": 3, "all_red_s": 2, "movements": "simplified_all_approaches"},
            {"id": "ph2", "green_s": green, "yellow_s": 3, "all_red_s": 2, "movements": "simplified_all_approaches"},
        ],
        "simplified": True,
        "control_notes": "Phase movements are placeholders. SUMO will build a default signal program from the junction type.",
    }
    ev.preconditions.append(f"Intersection {node['id']} exists and has no signal.")
    ev.expected_effects.append(f"Add simplified fixed-time signal {signal['id']} at {node['id']} with a {params.cycle_s} s cycle.")
    ev.touched = [node["id"], signal["id"]]
    ev.resolved_target = {"label": node["name"], "feature_id": node["id"], "coordinates": [node["lon"], node["lat"]]}
    network["signals"].append(signal)
    _set_control(network, node["id"], "signal")


def HANDLER_MODIFY_SIGNAL(network: dict[str, Any], operation: dict[str, Any], params: ModifySignalParams, ev: Eval) -> None:
    ev.units = {"cycle_s": "s"}
    node = _need_node(network, operation, ev)
    if node is None:
        return
    signal = signal_for_node(network, node["id"])
    if signal is None:
        ev.error("not_found", f"Intersection {node['id']} has no traffic signal to modify.")
        return
    _mark_simplified(ev, "Updated cycle length keeps simplified phase movements.")
    green = max(10, (params.cycle_s - 10) // 2)
    signal["cycle_s"] = int(params.cycle_s)
    signal["phases"] = [
        {"id": "ph1", "green_s": green, "yellow_s": 3, "all_red_s": 2, "movements": "simplified_all_approaches"},
        {"id": "ph2", "green_s": green, "yellow_s": 3, "all_red_s": 2, "movements": "simplified_all_approaches"},
    ]
    ev.preconditions.append(f"Signal at {node['id']} exists.")
    ev.expected_effects.append(f"Set the cycle at {signal['id']} to {params.cycle_s} seconds.")
    ev.touched = [node["id"], signal["id"]]
    ev.resolved_target = {"label": node["name"], "feature_id": node["id"], "coordinates": [node["lon"], node["lat"]]}


def HANDLER_REMOVE_SIGNAL(network: dict[str, Any], operation: dict[str, Any], params: EmptyParams, ev: Eval) -> None:
    node = _need_node(network, operation, ev)
    if node is None:
        return
    signal = signal_for_node(network, node["id"])
    if signal is None:
        ev.error("not_found", f"Intersection {node['id']} has no traffic signal.")
        return
    ev.preconditions.append(f"Signal at {node['id']} exists.")
    ev.expected_effects.append(f"Remove signal {signal['id']} and return {node['id']} to uncontrolled operation.")
    ev.touched = [node["id"], signal["id"]]
    ev.resolved_target = {"label": node["name"], "feature_id": node["id"], "coordinates": [node["lon"], node["lat"]]}
    network["signals"] = [item for item in network["signals"] if item["id"] != signal["id"]]
    _set_control(network, node["id"], "uncontrolled")


def HANDLER_ADD_ROUNDABOUT(network: dict[str, Any], operation: dict[str, Any], params: RoundaboutParams, ev: Eval) -> None:
    ev.units = {"diameter_m": "m", "circulating_speed_kmh": "km/h"}
    node = _need_node(network, operation, ev)
    if node is None:
        return
    if any(item["node_id"] == node["id"] for item in network["roundabouts"]):
        ev.error("already_exists", f"Intersection {node['id']} is already a roundabout.")
        return
    if signal_for_node(network, node["id"]) is not None:
        ev.error("control_conflict", "Remove the traffic signal before converting this intersection to a roundabout.")
        return
    legs = {edge["road_id"] for edge in incident_edges(network, node["id"])}
    if len(legs) < 3:
        ev.error("insufficient_legs", "A roundabout in this model needs at least three approach roads.")
        return
    _mark_simplified(
        ev,
        "Circulating geometry, entry flare, and deflection are not built. The simulator uses a right-before-left junction as an approximation.",
    )
    item = {
        "id": f"RBT_{node['id']}",
        "node_id": node["id"],
        "diameter_m": float(params.diameter_m),
        "circulating_speed_kmh": float(params.circulating_speed_kmh),
        "simplified": True,
        "notes": "Physical roundabout geometry is not modeled.",
    }
    ev.preconditions.append(f"Intersection {node['id']} has at least three approach roads and no signal.")
    ev.expected_effects.append(f"Mark {node['id']} as simplified roundabout {item['id']} and cap approach speed.")
    ev.touched = [node["id"], item["id"]]
    ev.resolved_target = {"label": node["name"], "feature_id": node["id"], "coordinates": [node["lon"], node["lat"]]}
    network["roundabouts"].append(item)
    _set_control(network, node["id"], "roundabout")
    for edge in incident_edges(network, node["id"]):
        edge["roundabout_id"] = item["id"]
        edge["speed_kmh"] = round(min(float(edge["speed_kmh"]), float(params.circulating_speed_kmh)), 6)


def HANDLER_MODIFY_ROUNDABOUT(
    network: dict[str, Any], operation: dict[str, Any], params: ModifyRoundaboutParams, ev: Eval
) -> None:
    ev.units = {"diameter_m": "m", "circulating_speed_kmh": "km/h"}
    node = _need_node(network, operation, ev)
    if node is None:
        return
    item = next((row for row in network["roundabouts"] if row["node_id"] == node["id"]), None)
    if item is None:
        ev.error("not_found", f"Intersection {node['id']} has no roundabout.")
        return
    if params.diameter_m is None and params.circulating_speed_kmh is None:
        ev.error("invalid_parameters", "Provide a diameter or a circulating speed.")
        return
    _mark_simplified(ev, "Only the stored diameter and approach speed change. Geometry stays simplified.")
    if params.diameter_m is not None:
        item["diameter_m"] = float(params.diameter_m)
    if params.circulating_speed_kmh is not None:
        item["circulating_speed_kmh"] = float(params.circulating_speed_kmh)
        for edge in incident_edges(network, node["id"]):
            edge["speed_kmh"] = round(min(float(edge["speed_kmh"]), float(params.circulating_speed_kmh)), 6)
    ev.preconditions.append(f"Roundabout at {node['id']} exists.")
    ev.expected_effects.append(f"Update simplified roundabout {item['id']}.")
    ev.touched = [node["id"], item["id"]]
    ev.resolved_target = {"label": node["name"], "feature_id": node["id"], "coordinates": [node["lon"], node["lat"]]}


def HANDLER_REMOVE_ROUNDABOUT(network: dict[str, Any], operation: dict[str, Any], params: EmptyParams, ev: Eval) -> None:
    node = _need_node(network, operation, ev)
    if node is None:
        return
    item = next((row for row in network["roundabouts"] if row["node_id"] == node["id"]), None)
    if item is None:
        ev.error("not_found", f"Intersection {node['id']} has no roundabout.")
        return
    ev.warn("speeds_not_restored", "Approach speeds that were lowered for the roundabout are not automatically restored.")
    ev.preconditions.append(f"Roundabout at {node['id']} exists.")
    ev.expected_effects.append(f"Remove roundabout {item['id']} and return {node['id']} to uncontrolled operation.")
    ev.touched = [node["id"], item["id"]]
    ev.resolved_target = {"label": node["name"], "feature_id": node["id"], "coordinates": [node["lon"], node["lat"]]}
    network["roundabouts"] = [row for row in network["roundabouts"] if row["id"] != item["id"]]
    for edge in incident_edges(network, node["id"]):
        if edge.get("roundabout_id") == item["id"]:
            edge["roundabout_id"] = None
    _set_control(network, node["id"], "uncontrolled")


def HANDLER_ADD_BRIDGE(network: dict[str, Any], operation: dict[str, Any], params: StructureParams, ev: Eval) -> None:
    _add_structure(network, operation, params, ev, "bridges", "bridge", "BR")


def HANDLER_ADD_TUNNEL(network: dict[str, Any], operation: dict[str, Any], params: StructureParams, ev: Eval) -> None:
    _add_structure(network, operation, params, ev, "tunnels", "tunnel", "TN")


def HANDLER_REMOVE_BRIDGE(network: dict[str, Any], operation: dict[str, Any], params: EmptyParams, ev: Eval) -> None:
    _remove_structure(network, operation, ev, "bridges", "bridge", "bridge_id")


def HANDLER_REMOVE_TUNNEL(network: dict[str, Any], operation: dict[str, Any], params: EmptyParams, ev: Eval) -> None:
    _remove_structure(network, operation, ev, "tunnels", "tunnel", "tunnel_id")


def HANDLER_ADD_CONNECTION(network: dict[str, Any], operation: dict[str, Any], params: TurnParams, ev: Eval) -> None:
    pair = _resolve_turn(network, params, ev)
    if pair is None:
        return
    from_edge, to_edge = pair
    existing = _restriction(network, from_edge["id"], to_edge["id"])
    if existing is None:
        ev.error("already_allowed", "That turn is already allowed. No different connection was created.")
        return
    network["turn_restrictions"] = [item for item in network["turn_restrictions"] if item["id"] != existing["id"]]
    ev.preconditions.append("The turn is currently restricted and the edges meet at one intersection.")
    ev.expected_effects.append(f"Allow the turn from {from_edge['id']} to {to_edge['id']}.")
    ev.touched = [from_edge["id"], to_edge["id"]]
    ev.resolved_target = {"label": f"{from_edge['id']} -> {to_edge['id']}", "feature_id": from_edge["road_id"], "coordinates": None}


def HANDLER_REMOVE_CONNECTION(network: dict[str, Any], operation: dict[str, Any], params: TurnParams, ev: Eval) -> None:
    pair = _resolve_turn(network, params, ev)
    if pair is None:
        return
    from_edge, to_edge = pair
    if _restriction(network, from_edge["id"], to_edge["id"]) is not None:
        ev.error("already_restricted", "That turn is already restricted.")
        return
    restriction = {
        "id": f"TR_{from_edge['id']}_{to_edge['id']}",
        "from_edge": from_edge["id"],
        "to_edge": to_edge["id"],
        "node_id": from_edge["to_node"],
    }
    network["turn_restrictions"].append(restriction)
    ev.preconditions.append("The two directed edges meet and the turn is currently allowed.")
    ev.expected_effects.append(f"Prohibit the turn from {from_edge['id']} to {to_edge['id']}.")
    ev.touched = [from_edge["id"], to_edge["id"], restriction["id"]]
    ev.resolved_target = {"label": restriction["id"], "feature_id": from_edge["road_id"], "coordinates": None}


def HANDLER_ADD_LAND_USE(network: dict[str, Any], operation: dict[str, Any], params: LandUseParams, ev: Eval) -> None:
    ev.units = {"housing_units": "dwellings", "trip_rate_per_unit_peak": "veh/unit/peak-hour"}
    rate = float(params.trip_rate_per_unit_peak or 0.1)
    zone = _zone_by_name(network, params.name) or _zone_from_target(network, operation)
    _mark_simplified(
        ev,
        f"Trip generation uses an assumed rate of {rate} peak-hour vehicle trips per housing unit. This is not a calibrated or observed demand.",
    )
    if zone is None:
        zone_id = _next_id(network, _slug_id(params.name, "LU"))
        centroid = _study_centroid(network)
        zone = {
            "id": zone_id,
            "name": params.name,
            "kind": params.kind,
            "housing_units": int(params.housing_units),
            "trip_rate_per_unit_peak": rate,
            "trip_rate_source": "assumption",
            "geometry": _fallback_polygon(centroid),
            "assumptions": ["Zone geometry is a diagrammatic square around the network, not a surveyed parcel."],
        }
        network["land_uses"].append(zone)
        ev.warn("geometry_assumed", "No parcel geometry was supplied. A diagrammatic polygon was created.")
        ev.expected_effects.append(f"Create land-use zone {zone_id} with {params.housing_units} housing units.")
    else:
        zone["housing_units"] = int(zone.get("housing_units") or 0) + int(params.housing_units)
        zone["kind"] = params.kind
        zone["trip_rate_per_unit_peak"] = rate
        zone["trip_rate_source"] = "assumption"
        ev.expected_effects.append(
            f"Add {params.housing_units} housing units to {zone['name']}, for a total of {zone['housing_units']}."
        )
    ev.preconditions.append("Housing units and the trip rate are non-negative inputs, not measured counts.")
    ev.touched = [zone["id"]]
    ev.resolved_target = {"label": zone["name"], "feature_id": zone["id"], "coordinates": None}
    rebuild_land_use_demand(network)


def HANDLER_MODIFY_LAND_USE(network: dict[str, Any], operation: dict[str, Any], params: ModifyLandUseParams, ev: Eval) -> None:
    ev.units = {"housing_units": "dwellings", "trip_rate_per_unit_peak": "veh/unit/peak-hour"}
    zone = _zone_from_target(network, operation)
    if zone is None:
        label = str((operation.get("target") or {}).get("label") or "")
        ev.error("not_found", f"No land-use zone matched '{label}'.")
        return
    if params.housing_units is None and params.kind is None and params.trip_rate_per_unit_peak is None:
        ev.error("invalid_parameters", "Provide housing units, a land-use kind, or a trip rate.")
        return
    _mark_simplified(ev, "Land-use edits change synthetic trip generation only. They are not observed counts.")
    if params.housing_units is not None:
        zone["housing_units"] = int(params.housing_units)
    if params.kind is not None:
        zone["kind"] = params.kind
    if params.trip_rate_per_unit_peak is not None:
        zone["trip_rate_per_unit_peak"] = float(params.trip_rate_per_unit_peak)
        zone["trip_rate_source"] = "assumption"
    ev.preconditions.append(f"Land-use zone {zone['id']} exists.")
    ev.expected_effects.append(f"Update {zone['name']} ({zone['id']}).")
    ev.touched = [zone["id"]]
    ev.resolved_target = {"label": zone["name"], "feature_id": zone["id"], "coordinates": None}
    rebuild_land_use_demand(network)


def HANDLER_TRANSIT(network: dict[str, Any], operation: dict[str, Any], params: TransitParams, ev: Eval) -> None:
    ev.units = {"headway_min": "min"}
    if params.mode not in {"bus", "tram"}:
        ev.error("invalid_parameters", "Supported transit modes are bus and tram.")
        return
    node_ids = list(params.node_ids or [])
    if not node_ids and params.from_node and params.to_node:
        start = _need_named_node(network, params.from_node, ev, "from_node")
        end = _need_named_node(network, params.to_node, ev, "to_node")
        if start is None or end is None:
            return
        path = shortest_edge_path(network, start["id"], end["id"])
        if not path:
            ev.error("no_path", "No path connects the requested transit stops.")
            return
        node_ids = [start["id"], *[edge["to_node"] for edge in path]]
    if len(node_ids) < 2:
        ev.error("invalid_parameters", "A transit route needs at least two stops.")
        return
    for node_id in node_ids:
        if get_node(network, node_id) is None:
            ev.error("not_found", f"Transit stop {node_id} is not an intersection in this network.")
            return
    route = None
    if params.route_id:
        route = next((item for item in network["transit_routes"] if item["id"] == params.route_id), None)
    if route is None:
        route = next((item for item in network["transit_routes"] if item["name"].lower() == params.name.lower()), None)
    created = route is None
    if route is None:
        route_id = params.route_id or _next_id(network, _slug_id(params.name, "PT"))
        if not valid_feature_id(route_id):
            ev.error("invalid_id", "Transit route ids must start with a letter.")
            return
        route = {"id": route_id, "notes": []}
        network["transit_routes"].append(route)
    route.update(
        {
            "name": params.name,
            "mode": params.mode,
            "node_ids": node_ids,
            "edge_ids": [],
            "headway_min": float(params.headway_min),
            "simplified": True,
            "valid": True,
            "notes": ["Passenger boarding, alighting, and dwell time are not modeled."],
        }
    )
    path_edges: list[str] = []
    for start_id, end_id in zip(node_ids, node_ids[1:]):
        path = shortest_edge_path(network, start_id, end_id)
        if not path:
            ev.error("no_path", f"No path connects {start_id} to {end_id}.")
            network["transit_routes"] = [item for item in network["transit_routes"] if item is not route or not created]
            return
        path_edges.extend(edge["id"] for edge in path)
    route["edge_ids"] = path_edges
    _mark_simplified(ev, "The route is a simplified vehicle flow. It does not model passengers or stop dwell.")
    ev.preconditions.append("Every stop exists and a directed path connects consecutive stops.")
    verb = "Add" if created else "Update"
    ev.expected_effects.append(f"{verb} {params.mode} route {route['id']} ({params.name}) every {params.headway_min} minutes.")
    ev.touched = [route["id"], *node_ids]
    ev.resolved_target = {"label": params.name, "feature_id": route["id"], "coordinates": None}


def HANDLER_DEMAND(network: dict[str, Any], operation: dict[str, Any], params: DemandParams, ev: Eval) -> None:
    action = params.action
    if action == "select_peaks":
        peaks = [peak.lower() for peak in (params.peaks or [])]
        if not peaks or any(peak not in {"morning", "evening"} for peak in peaks):
            ev.error("invalid_parameters", "select_peaks accepts morning and evening.")
            return
        missing = [peak for peak in peaks if f"{peak}_peak" not in {item["id"] for item in network["demand_profiles"]}]
        if missing:
            ev.error("not_found", "Missing demand profiles: " + ", ".join(f"{peak}_peak" for peak in missing))
            return
        network["meta"]["comparison_peaks"] = peaks
        ev.preconditions.append("The requested peak profiles already exist.")
        ev.expected_effects.append("Record comparison peaks: " + ", ".join(peaks) + ".")
        ev.touched = [f"{peak}_peak" for peak in peaks]
        ev.warn("comparison_intent", "This records which peaks to compare. It does not by itself run a simulation.")
        return
    if action == "scale":
        if params.peak not in {"morning", "evening"} or params.factor is None:
            ev.error("invalid_parameters", "scale requires a morning or evening peak and a factor.")
            return
        profile = next((item for item in network["demand_profiles"] if item["id"] == f"{params.peak}_peak"), None)
        if profile is None:
            ev.error("not_found", f"Demand profile {params.peak}_peak was not found.")
            return
        ev.units = {"factor": "1", "vehicles": "vehicles"}
        for pair in profile["od_pairs"]:
            pair["vehicles"] = int(round(int(pair["vehicles"]) * float(params.factor)))
        profile["synthetic"] = True
        profile["observed"] = False
        assumptions = list(profile.get("assumptions") or [])
        assumptions.append(f"Vehicle counts were scaled by {params.factor} and rounded to integers.")
        profile["assumptions"] = assumptions
        ev.warn("synthetic_demand", "Scaled counts remain synthetic demand, not observed traffic.")
        ev.preconditions.append(f"Profile {profile['id']} exists.")
        ev.expected_effects.append(f"Scale {profile['id']} vehicle counts by {params.factor}.")
        ev.touched = [profile["id"]]
        return
    if action == "set_seed":
        if params.peak not in {"morning", "evening"} or params.seed is None:
            ev.error("invalid_parameters", "set_seed requires a morning or evening peak and a seed.")
            return
        profile = next((item for item in network["demand_profiles"] if item["id"] == f"{params.peak}_peak"), None)
        if profile is None:
            ev.error("not_found", f"Demand profile {params.peak}_peak was not found.")
            return
        profile["seed"] = int(params.seed)
        ev.preconditions.append(f"Profile {profile['id']} exists.")
        ev.expected_effects.append(f"Set the random seed for {profile['id']} to {params.seed}.")
        ev.touched = [profile["id"]]
        return
    ev.error("invalid_parameters", "Demand action must be select_peaks, scale, or set_seed.")


HANDLERS = {
    "ADD_ROAD": HANDLER_ADD_ROAD,
    "REMOVE_ROAD": HANDLER_REMOVE_ROAD,
    "MODIFY_ROAD_DIRECTION": HANDLER_DIRECTION,
    "MODIFY_LANE_COUNT": HANDLER_LANES,
    "MODIFY_SPEED_LIMIT": HANDLER_SPEED,
    "ADD_INTERSECTION": HANDLER_ADD_INTERSECTION,
    "REMOVE_INTERSECTION": HANDLER_REMOVE_INTERSECTION,
    "ADD_TRAFFIC_SIGNAL": HANDLER_ADD_SIGNAL,
    "MODIFY_TRAFFIC_SIGNAL": HANDLER_MODIFY_SIGNAL,
    "REMOVE_TRAFFIC_SIGNAL": HANDLER_REMOVE_SIGNAL,
    "ADD_ROUNDABOUT": HANDLER_ADD_ROUNDABOUT,
    "MODIFY_ROUNDABOUT": HANDLER_MODIFY_ROUNDABOUT,
    "REMOVE_ROUNDABOUT": HANDLER_REMOVE_ROUNDABOUT,
    "ADD_BRIDGE": HANDLER_ADD_BRIDGE,
    "REMOVE_BRIDGE": HANDLER_REMOVE_BRIDGE,
    "ADD_TUNNEL": HANDLER_ADD_TUNNEL,
    "REMOVE_TUNNEL": HANDLER_REMOVE_TUNNEL,
    "ADD_ROAD_CONNECTION": HANDLER_ADD_CONNECTION,
    "REMOVE_ROAD_CONNECTION": HANDLER_REMOVE_CONNECTION,
    "ADD_LAND_USE_DEVELOPMENT": HANDLER_ADD_LAND_USE,
    "MODIFY_LAND_USE": HANDLER_MODIFY_LAND_USE,
    "ADD_OR_MODIFY_PUBLIC_TRANSPORT": HANDLER_TRANSIT,
    "MODIFY_DEMAND_PROFILE": HANDLER_DEMAND,
}


def _need_road(network: dict[str, Any], operation: dict[str, Any], ev: Eval) -> dict[str, Any] | None:
    resolved = resolve_road(network, operation.get("target") or {})
    if resolved["status"] == "unique":
        road = resolved["road"]
        ev.resolved_target = {
            "label": (operation.get("target") or {}).get("label") or road["name"],
            "feature_id": road["id"],
            "coordinates": (operation.get("target") or {}).get("coordinates"),
        }
        if resolved.get("note"):
            ev.warn("resolved_by_coordinates", resolved["note"])
        return road
    if resolved["status"] == "ambiguous":
        ev.error("ambiguous", resolved["message"])
        ev.candidates = resolved["candidates"]
        return None
    ev.error("not_found", resolved["message"])
    return None


def _need_node(network: dict[str, Any], operation: dict[str, Any], ev: Eval) -> dict[str, Any] | None:
    resolved = resolve_node(network, operation.get("target") or {})
    if resolved["status"] == "unique":
        node = resolved["node"]
        ev.resolved_target = {
            "label": (operation.get("target") or {}).get("label") or node["name"],
            "feature_id": node["id"],
            "coordinates": (operation.get("target") or {}).get("coordinates"),
        }
        if resolved.get("note"):
            ev.warn("resolved_by_coordinates", resolved["note"])
        return node
    if resolved["status"] == "ambiguous":
        ev.error("ambiguous", resolved["message"])
        ev.candidates = resolved["candidates"]
        return None
    ev.error("not_found", resolved["message"])
    return None


def _need_named_node(network: dict[str, Any], label: str, ev: Eval, role: str) -> dict[str, Any] | None:
    resolved = resolve_node(network, {"label": label, "feature_id": None, "coordinates": None})
    if resolved["status"] == "unique":
        return resolved["node"]
    if resolved["status"] == "ambiguous":
        ev.error("ambiguous", f"{role}: {resolved['message']}")
        ev.candidates = resolved["candidates"]
        return None
    ev.error("not_found", f"{role}: {resolved['message']}")
    return None


def _touch_road(ev: Eval, road: dict[str, Any]) -> None:
    ev.touched = [road["id"], *[edge["id"] for edge in road["edges"]]]


def _remove_road_record(network: dict[str, Any], road_id: str, edge_ids: set[str], ev: Eval) -> None:
    network["edges"] = [edge for edge in network["edges"] if edge["road_id"] != road_id]
    _drop_restrictions(network, edge_ids)
    for key, label in (("bridges", "bridge"), ("tunnels", "tunnel")):
        kept = []
        for item in network[key]:
            remaining = [item_id for item_id in item["road_ids"] if item_id != road_id]
            if remaining == item["road_ids"]:
                kept.append(item)
                continue
            ev.warn(
                f"{label}_reference_updated",
                f"{label.capitalize()} {item['id']} no longer references removed road {road_id}.",
            )
            if remaining:
                item["road_ids"] = remaining
                kept.append(item)
            else:
                ev.warn(f"{label}_removed", f"{label.capitalize()} {item['id']} was removed because it has no roads left.")
        network[key] = kept


def _drop_restrictions(network: dict[str, Any], edge_ids: set[str]) -> None:
    network["turn_restrictions"] = [
        item
        for item in network["turn_restrictions"]
        if item["from_edge"] not in edge_ids and item["to_edge"] not in edge_ids
    ]


def _set_control(network: dict[str, Any], node_id: str, control: str) -> None:
    row = intersection_for_node(network, node_id)
    if row is None:
        network["intersections"].append({"node_id": node_id, "control": control})
    else:
        row["control"] = control


def _mark_simplified(ev: Eval, message: str) -> None:
    ev.simplified = True
    ev.warn("simplified_model", message)


def _add_structure(
    network: dict[str, Any],
    operation: dict[str, Any],
    params: StructureParams,
    ev: Eval,
    collection: str,
    label: str,
    prefix: str,
) -> None:
    road = _need_road(network, operation, ev)
    if road is None:
        return
    if any(road["id"] in item["road_ids"] for item in network[collection]):
        ev.error("already_exists", f"Road {road['id']} is already on a {label}.")
        return
    _mark_simplified(
        ev,
        f"The {label} is a network attribute. Clearance, structural capacity, grades, and ventilation are not modeled.",
    )
    item_id = _next_id(network, f"{prefix}_{road['id']}")
    item = {
        "id": item_id,
        "name": params.name or f"{road['name']} {label}",
        "road_ids": [road["id"]],
        "simplified": True,
        "notes": "Physical structure is not modeled.",
    }
    field = "bridge_id" if label == "bridge" else "tunnel_id"
    for edge in edges_for_road(network, road["id"]):
        edge[field] = item_id
    network[collection].append(item)
    ev.preconditions.append(f"Road {road['id']} exists and is not already a {label}.")
    ev.expected_effects.append(f"Mark {road['id']} as simplified {label} {item_id}.")
    ev.touched = [road["id"], item_id]


def _remove_structure(
    network: dict[str, Any],
    operation: dict[str, Any],
    ev: Eval,
    collection: str,
    label: str,
    field: str,
) -> None:
    target = operation.get("target") or {}
    feature_id = str(target.get("feature_id") or target.get("label") or "")
    item = next((row for row in network[collection] if row["id"] == feature_id), None)
    road = None
    if item is None:
        road = _need_road(network, operation, ev)
        if road is None:
            return
        item = next((row for row in network[collection] if road["id"] in row["road_ids"]), None)
        if item is None:
            ev.error("not_found", f"Road {road['id']} has no {label}.")
            return
    ev.preconditions.append(f"{label.capitalize()} {item['id']} exists.")
    ev.expected_effects.append(f"Remove {label} {item['id']}.")
    ev.touched = [item["id"], *item["road_ids"]]
    ev.resolved_target = {"label": item["name"], "feature_id": item["id"], "coordinates": None}
    for edge in network["edges"]:
        if edge.get(field) == item["id"]:
            edge[field] = None
    network[collection] = [row for row in network[collection] if row["id"] != item["id"]]


def _resolve_turn(network: dict[str, Any], params: TurnParams, ev: Eval) -> tuple[dict[str, Any], dict[str, Any]] | None:
    candidates = _turn_candidates(network, params)
    if not candidates:
        ev.error("not_found", "No directed turn connects the requested edges.")
        return None
    if len(candidates) > 1:
        ev.error("ambiguous", "More than one directed turn matches. Select edge ids.")
        ev.candidates = [
            {
                "id": f"{start['id']}->{end['id']}",
                "name": f"{start['id']} -> {end['id']}",
                "kind": "turn",
                "from_edge": start["id"],
                "to_edge": end["id"],
            }
            for start, end in candidates
        ]
        return None
    return candidates[0]


def _turn_candidates(network: dict[str, Any], params: TurnParams) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    from_edges = _edge_choices(network, params.from_edge, params.from_road)
    to_edges = _edge_choices(network, params.to_edge, params.to_road)
    pairs = []
    for start in from_edges:
        for end in to_edges:
            if start["to_node"] == end["from_node"] and start["id"] != end["id"]:
                pairs.append((start, end))
    return pairs


def _edge_choices(network: dict[str, Any], edge_id: str | None, road_id: str | None) -> list[dict[str, Any]]:
    if edge_id:
        edge = get_edge(network, edge_id)
        return [] if edge is None else [edge]
    if road_id:
        return edges_for_road(network, road_id)
    return []


def _restriction(network: dict[str, Any], from_edge: str, to_edge: str) -> dict[str, Any] | None:
    for item in network["turn_restrictions"]:
        if item["from_edge"] == from_edge and item["to_edge"] == to_edge:
            return item
    return None


def _zone_by_name(network: dict[str, Any], name: str) -> dict[str, Any] | None:
    for zone in network["land_uses"]:
        if zone["name"].lower() == name.lower():
            return zone
    return None


def _zone_from_target(network: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any] | None:
    target = operation.get("target") or {}
    feature_id = str(target.get("feature_id") or "")
    label = str(target.get("label") or "")
    for zone in network["land_uses"]:
        if feature_id and zone["id"] == feature_id:
            return zone
        if label and zone["name"].lower() == label.lower():
            return zone
        if label and zone["id"].lower() == label.lower():
            return zone
    return None


def _study_centroid(network: dict[str, Any]) -> list[float]:
    if not network["nodes"]:
        return [0.0, 0.0]
    lon = sum(node["lon"] for node in network["nodes"]) / len(network["nodes"])
    lat = sum(node["lat"] for node in network["nodes"]) / len(network["nodes"])
    return [lon, lat]


def _fallback_polygon(centroid: list[float]) -> dict[str, Any]:
    lon, lat = centroid
    delta_lon = 0.004
    delta_lat = 0.003
    ring = [
        [lon - delta_lon, lat + 0.004],
        [lon + delta_lon, lat + 0.004],
        [lon + delta_lon, lat + 0.004 + delta_lat],
        [lon - delta_lon, lat + 0.004 + delta_lat],
        [lon - delta_lon, lat + 0.004],
    ]
    return {"type": "Polygon", "coordinates": [ring]}


def _slug_id(name: str, prefix: str) -> str:
    cleaned = "".join(char if char.isalnum() else "_" for char in name.upper())
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    value = f"{prefix}_{cleaned}"[:48]
    if not valid_feature_id(value):
        value = f"{prefix}_ZONE"
    return value


def _next_id(network: dict[str, Any], base: str) -> str:
    used = set(road_catalog(network))
    used.update(node["id"] for node in network["nodes"])
    used.update(item["id"] for collection in ("bridges", "tunnels", "signals", "roundabouts", "land_uses", "transit_routes") for item in network[collection])
    if base not in used:
        return base
    index = 2
    while f"{base}_{index}" in used:
        index += 1
    return f"{base}_{index}"


def _pydantic_details(exc: ValidationError) -> list[dict[str, Any]]:
    details = []
    for item in exc.errors():
        details.append({"loc": [str(part) for part in item.get("loc", [])], "msg": item.get("msg", "")})
    return details
