"""Graph construction, every operation type, isolation, and invalid edits."""

from __future__ import annotations

from app.domain.demand import find_profile
from app.domain.engine import apply_execution_policy, apply_plan, review_operations
from app.domain.network import (
    content_hash,
    edges_for_road,
    road_catalog,
    signal_for_node,
)
from app.domain.operations import draft_operation
from app.domain.sample import SAMPLE_ATTRIBUTION, build_sample_network


def network():
    return build_sample_network()


def apply(net, operation_type, label, parameters, decision="accepted"):
    operation = draft_operation(operation_type, label, parameters, operation_id="op_test", decision=decision)
    return apply_plan(net, [operation])


def test_sample_network_is_stable_directed_and_not_a_city():
    first = network()
    second = network()
    assert content_hash(first) == content_hash(second)
    assert len(first["nodes"]) == 6
    assert len(first["edges"]) == 14
    assert len(road_catalog(first)) == 7
    assert len(edges_for_road(first, "R1")) == 2
    assert first["meta"]["real_city"] is False
    assert "not a real city" in SAMPLE_ATTRIBUTION.lower() or "not derived" in SAMPLE_ATTRIBUTION.lower()
    assert find_profile(first, "morning_peak")["observed"] is False
    assert find_profile(first, "morning_peak")["synthetic"] is True


def test_make_r3_one_way_eastbound_and_reject_impossible_direction():
    net = network()
    original = content_hash(net)
    result = apply(net, "MODIFY_ROAD_DIRECTION", "R3", {"direction": "eastbound"})
    assert result["ok"] is True
    kept = edges_for_road(result["network"], "R3")
    assert [edge["id"] for edge in kept] == ["R3_J4_J5"]
    assert content_hash(net) == original
    blocked = apply(net, "MODIFY_ROAD_DIRECTION", "R6", {"direction": "eastbound"})
    assert blocked["ok"] is False
    assert content_hash(blocked["network"]) == original
    assert blocked["operations"][0]["errors"][0]["code"] == "direction_not_available"


def test_speed_lanes_add_and_remove_road():
    net = network()
    sped = apply(net, "MODIFY_SPEED_LIMIT", "R4", {"speed_kmh": 40})
    assert sped["ok"] is True
    assert {edge["speed_kmh"] for edge in edges_for_road(sped["network"], "R4")} == {40}
    lanes = apply(net, "MODIFY_LANE_COUNT", "R5", {"lanes": 3})
    assert {edge["lanes"] for edge in edges_for_road(lanes["network"], "R5")} == {3}
    added = apply(
        net,
        "ADD_ROAD",
        "River Way",
        {"name": "River Way", "from_node": "J1", "to_node": "J3", "lanes": 1, "speed_kmh": 40},
    )
    assert added["ok"] is True
    assert "River Way" in {road["name"] for road in road_catalog(added["network"]).values()}
    removed = apply(net, "REMOVE_ROAD", "R7", {})
    assert removed["ok"] is True
    assert edges_for_road(removed["network"], "R7") == []
    invalid = apply(net, "MODIFY_SPEED_LIMIT", "R4", {"speed_kmh": -5})
    assert invalid["ok"] is False
    assert content_hash(invalid["network"]) == content_hash(net)


def test_intersections_signals_roundabout_structures_and_turns():
    net = network()
    added = apply(net, "ADD_INTERSECTION", "Depot", {"node_id": "J9", "name": "Depot", "lon": 0.045, "lat": 0.02})
    assert added["ok"] is True
    assert any(node["id"] == "J9" for node in added["network"]["nodes"])
    blocked = apply(net, "REMOVE_INTERSECTION", "J2", {"remove_incident_roads": False})
    assert blocked["ok"] is False
    removed = apply(added["network"], "REMOVE_INTERSECTION", "J9", {"remove_incident_roads": False})
    assert removed["ok"] is True
    signal = apply(net, "ADD_TRAFFIC_SIGNAL", "J2", {"cycle_s": 90})
    assert signal["ok"] is True
    assert signal["operations"][0]["simplified"] is True
    assert signal_for_node(signal["network"], "J2")["cycle_s"] == 90
    modified = apply(signal["network"], "MODIFY_TRAFFIC_SIGNAL", "J2", {"cycle_s": 120})
    assert signal_for_node(modified["network"], "J2")["cycle_s"] == 120
    cleared = apply(modified["network"], "REMOVE_TRAFFIC_SIGNAL", "J2", {})
    assert signal_for_node(cleared["network"], "J2") is None
    roundabout = apply(net, "ADD_ROUNDABOUT", "J2", {"diameter_m": 28, "circulating_speed_kmh": 25})
    assert roundabout["ok"] is True
    assert roundabout["network"]["roundabouts"][0]["simplified"] is True
    changed = apply(roundabout["network"], "MODIFY_ROUNDABOUT", "J2", {"diameter_m": 32})
    assert changed["network"]["roundabouts"][0]["diameter_m"] == 32
    without = apply(changed["network"], "REMOVE_ROUNDABOUT", "J2", {})
    assert without["network"]["roundabouts"] == []
    too_few = apply(net, "ADD_ROUNDABOUT", "J1", {})
    assert too_few["ok"] is False
    bridge = apply(net, "ADD_BRIDGE", "R1", {"name": "Main Bridge"})
    assert bridge["network"]["bridges"][0]["road_ids"] == ["R1"]
    assert edges_for_road(bridge["network"], "R1")[0]["bridge_id"] is not None
    no_bridge = apply(bridge["network"], "REMOVE_BRIDGE", "R1", {})
    assert no_bridge["network"]["bridges"] == []
    tunnel = apply(net, "ADD_TUNNEL", "R2", {})
    assert tunnel["network"]["tunnels"]
    cleared_tunnel = apply(tunnel["network"], "REMOVE_TUNNEL", "R2", {})
    assert cleared_tunnel["network"]["tunnels"] == []
    restricted = apply(
        net,
        "REMOVE_ROAD_CONNECTION",
        "turn",
        {"from_edge": "R1_J1_J2", "to_edge": "R6_J2_J5"},
    )
    assert restricted["ok"] is True
    assert restricted["network"]["turn_restrictions"]
    restored = apply(
        restricted["network"],
        "ADD_ROAD_CONNECTION",
        "turn",
        {"from_edge": "R1_J1_J2", "to_edge": "R6_J2_J5"},
    )
    assert restored["ok"] is True
    assert restored["network"]["turn_restrictions"] == []


def test_land_use_transit_and_demand_are_explicitly_synthetic():
    net = network()
    developed = apply(
        net,
        "ADD_LAND_USE_DEVELOPMENT",
        "Northern District",
        {"name": "Northern District", "kind": "residential", "housing_units": 5000},
    )
    assert developed["ok"] is True
    zone = developed["network"]["land_uses"][0]
    assert zone["housing_units"] == 5000
    generated = find_profile(developed["network"], "land_use_generated")
    assert generated["synthetic"] is True
    assert generated["observed"] is False
    assert generated["vehicles_total"] == 500
    modified = apply(developed["network"], "MODIFY_LAND_USE", "Northern District", {"housing_units": 10})
    assert find_profile(modified["network"], "land_use_generated")["housing_units_total"] == 10
    transit = apply(
        net,
        "ADD_OR_MODIFY_PUBLIC_TRANSPORT",
        "Crosstown",
        {"name": "Crosstown", "mode": "bus", "from_node": "J4", "to_node": "J6", "headway_min": 10},
    )
    route = next(item for item in transit["network"]["transit_routes"] if item["name"] == "Crosstown")
    assert route["headway_min"] == 10
    assert route["edge_ids"]
    assert route["simplified"] is True
    scaled = apply(net, "MODIFY_DEMAND_PROFILE", "morning peak", {"action": "scale", "peak": "morning", "factor": 2})
    morning = find_profile(scaled["network"], "morning_peak")
    assert morning["od_pairs"][0]["vehicles"] == 12
    assert morning["observed"] is False


def test_ambiguous_target_does_not_change_a_different_road():
    net = network()
    original = content_hash(net)
    operation = draft_operation(
        "MODIFY_SPEED_LIMIT",
        "Main Street",
        {"speed_kmh": 40},
        operation_id="op_amb",
        decision="accepted",
    )
    result = apply_plan(net, [operation])
    assert result["ok"] is False
    assert result["operations"][0]["validation_status"] == "ambiguous"
    assert {item["id"] for item in result["operations"][0]["candidates"]} == {"R1", "R2"}
    assert content_hash(result["network"]) == original
    missing = draft_operation(
        "MODIFY_SPEED_LIMIT",
        "ignored",
        {"speed_kmh": 40},
        operation_id="op_missing",
        decision="accepted",
        feature_id="R999",
    )
    missed = apply_plan(net, [missing])
    assert missed["ok"] is False
    assert "not substituted" in missed["operations"][0]["errors"][0]["message"].lower()


def test_failed_operation_rolls_back_the_whole_transaction():
    net = network()
    original = content_hash(net)
    operations = [
        draft_operation("MODIFY_SPEED_LIMIT", "R4", {"speed_kmh": 40}, operation_id="ok", decision="accepted"),
        draft_operation("REMOVE_ROAD", "NO_SUCH_ROAD", {}, operation_id="bad", decision="accepted"),
    ]
    result = apply_plan(net, operations)
    assert result["ok"] is False
    assert content_hash(result["network"]) == original
    assert result["operations"][0]["apply_status"] == "rolled_back"
    assert result["operations"][1]["apply_status"] == "failed"


def test_auto_policy_keeps_warnings_pending():
    net = network()
    operations = review_operations(
        net,
        [
            draft_operation("MODIFY_SPEED_LIMIT", "R4", {"speed_kmh": 40}, operation_id="speed"),
            draft_operation("ADD_TRAFFIC_SIGNAL", "J2", {"cycle_s": 90}, operation_id="signal"),
        ],
    )
    decided = apply_execution_policy(operations, "auto_valid")
    by_id = {item["id"]: item for item in decided}
    assert by_id["speed"]["decision"] == "accepted"
    assert by_id["signal"]["decision"] == "pending"
    assert by_id["signal"]["warnings"]


def test_road_removal_does_not_leave_a_bridge_reference():
    net = network()
    bridged = apply(net, "ADD_BRIDGE", "R7", {"name": "Third Bridge"})
    removed = apply(bridged["network"], "REMOVE_ROAD", "R7", {})
    assert removed["ok"] is True
    assert removed["network"]["bridges"] == []
    assert all(edge.get("bridge_id") != "BR_R7" for edge in removed["network"]["edges"])


def test_coordinate_can_disambiguate_without_guessing_past_tolerance():
    net = network()
    operation = draft_operation(
        "MODIFY_SPEED_LIMIT",
        "Main Street",
        {"speed_kmh": 45},
        operation_id="coord",
        decision="accepted",
        coordinates=[0.007, 0.020],
    )
    reviewed = review_operations(net, [operation])
    assert reviewed[0]["validation_status"] in {"valid", "valid_with_warnings"}
    assert reviewed[0]["target"]["feature_id"] == "R1"
    far = draft_operation(
        "MODIFY_SPEED_LIMIT",
        "Main Street",
        {"speed_kmh": 45},
        operation_id="far",
        decision="accepted",
        coordinates=[10, 10],
    )
    far_review = review_operations(net, [far])
    assert far_review[0]["validation_status"] == "ambiguous"
