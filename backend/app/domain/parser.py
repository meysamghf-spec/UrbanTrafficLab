"""Deterministic command parser.

The parser only recognizes supported clauses. Anything else is returned as an
unparsed clause so the user can see that it was not turned into an edit.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from app.domain.operations import draft_operation

MPH_TO_KMH = 1.609344


def parse_rules(prompt: str) -> dict[str, Any]:
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    operations: list[dict[str, Any]] = []
    notes: list[str] = []
    unparsed: list[str] = []
    for clause in split_clauses(prompt):
        parsed = _parse_clause(clause, prompt_hash, len(operations) + 1)
        if parsed is None:
            unparsed.append(clause)
            continue
        if parsed["kind"] == "note":
            notes.append(parsed["note"])
            continue
        operations.append(parsed["operation"])
    return {
        "operations": operations,
        "notes": notes,
        "unparsed_clauses": unparsed,
        "prompt_sha256": prompt_hash,
    }


def split_clauses(prompt: str) -> list[str]:
    text = prompt.strip()
    text = re.sub(r"(\d),(\d{3})\b", r"\1\2", text)
    clauses: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        for part in re.split(r"[;,]", sentence):
            clause = part.strip(" \n\t.")
            clause = re.sub(r"^(?:and|then)\s+", "", clause, flags=re.IGNORECASE).strip()
            if clause:
                clauses.append(clause)
    return clauses


def _parse_clause(clause: str, prompt_hash: str, number: int) -> dict[str, Any] | None:
    source = {"kind": "rule_based_parser", "clause": clause, "prompt_sha256": prompt_hash}
    scope = re.fullmatch(r"(?:in|within)\s+the\s+(.+?)\s+study\s+area", clause, flags=re.IGNORECASE)
    if scope:
        return {"kind": "note", "note": f"Study-area context noted: {scope.group(1)}. The current network is still used."}
    compare = re.fullmatch(
        r"compare\s+(?:the\s+)?(morning|evening|am|pm)\s+and\s+(evening|morning|am|pm)\s+peak(?:\s+traffic)?",
        clause,
        flags=re.IGNORECASE,
    )
    if compare:
        peaks = [_peak_name(compare.group(1)), _peak_name(compare.group(2))]
        return _op(number, "MODIFY_DEMAND_PROFILE", "demand", {"action": "select_peaks", "peaks": peaks}, source)
    direction = re.fullmatch(
        r"make\s+(?:road\s+)?(.+?)\s+one-way\s+(eastbound|westbound|northbound|southbound)",
        clause,
        flags=re.IGNORECASE,
    )
    if direction:
        return _op(
            number,
            "MODIFY_ROAD_DIRECTION",
            direction.group(1).strip(),
            {"direction": direction.group(2).lower()},
            source,
        )
    speed = re.fullmatch(
        r"(?:increase|decrease|set|change)\s+the\s+speed\s+limit\s+on\s+(?:road\s+)?(.+?)\s+to\s+(\d+(?:\.\d+)?)\s*(km/h|kph|kmh|mph)?",
        clause,
        flags=re.IGNORECASE,
    ) or re.fullmatch(
        r"(?:set|change)\s+(?:road\s+)?(.+?)\s+speed\s+limit\s+to\s+(\d+(?:\.\d+)?)\s*(km/h|kph|kmh|mph)?",
        clause,
        flags=re.IGNORECASE,
    )
    if speed:
        return _op(number, "MODIFY_SPEED_LIMIT", speed.group(1).strip(), _speed_params(speed.group(2), speed.group(3)), source)
    lanes = re.fullmatch(
        r"(?:set|change)\s+(?:the\s+)?(?:number\s+of\s+)?lanes\s+on\s+(?:road\s+)?(.+?)\s+to\s+(\d+)",
        clause,
        flags=re.IGNORECASE,
    )
    if lanes:
        return _op(number, "MODIFY_LANE_COUNT", lanes.group(1).strip(), {"lanes": int(lanes.group(2))}, source)
    signal = re.fullmatch(
        r"add\s+(?:a\s+)?(?:traffic\s+)?signal\s+at\s+(?:the\s+)?(?:intersection\s+(?:of\s+)?)?(.+)",
        clause,
        flags=re.IGNORECASE,
    )
    if signal:
        return _op(number, "ADD_TRAFFIC_SIGNAL", signal.group(1).strip(), {"cycle_s": 90}, source)
    remove_signal = re.fullmatch(
        r"remove\s+(?:the\s+)?(?:traffic\s+)?signal\s+at\s+(?:the\s+)?(?:intersection\s+(?:of\s+)?)?(.+)",
        clause,
        flags=re.IGNORECASE,
    )
    if remove_signal:
        return _op(number, "REMOVE_TRAFFIC_SIGNAL", remove_signal.group(1).strip(), {}, source)
    modify_signal = re.fullmatch(
        r"(?:set|change)\s+the\s+signal\s+at\s+(.+?)\s+cycle\s+to\s+(\d+)\s*(?:seconds|s)?",
        clause,
        flags=re.IGNORECASE,
    )
    if modify_signal:
        return _op(
            number,
            "MODIFY_TRAFFIC_SIGNAL",
            modify_signal.group(1).strip(),
            {"cycle_s": int(modify_signal.group(2))},
            source,
        )
    add_roundabout = re.fullmatch(r"add\s+a\s+roundabout\s+at\s+(.+)", clause, flags=re.IGNORECASE)
    if add_roundabout:
        return _op(number, "ADD_ROUNDABOUT", add_roundabout.group(1).strip(), {}, source)
    remove_roundabout = re.fullmatch(r"remove\s+(?:the\s+)?roundabout\s+at\s+(.+)", clause, flags=re.IGNORECASE)
    if remove_roundabout:
        return _op(number, "REMOVE_ROUNDABOUT", remove_roundabout.group(1).strip(), {}, source)
    modify_roundabout = re.fullmatch(
        r"(?:set|change)\s+the\s+roundabout\s+at\s+(.+?)\s+diameter\s+to\s+(\d+(?:\.\d+)?)\s*(?:m|meters)?",
        clause,
        flags=re.IGNORECASE,
    )
    if modify_roundabout:
        return _op(
            number,
            "MODIFY_ROUNDABOUT",
            modify_roundabout.group(1).strip(),
            {"diameter_m": float(modify_roundabout.group(2))},
            source,
        )
    add_bridge = re.fullmatch(r"add\s+a\s+bridge\s+on\s+(?:road\s+)?(.+)", clause, flags=re.IGNORECASE)
    if add_bridge:
        return _op(number, "ADD_BRIDGE", add_bridge.group(1).strip(), {}, source)
    remove_bridge = re.fullmatch(r"remove\s+(?:the\s+)?bridge\s+on\s+(?:road\s+)?(.+)", clause, flags=re.IGNORECASE)
    if remove_bridge:
        return _op(number, "REMOVE_BRIDGE", remove_bridge.group(1).strip(), {}, source)
    add_tunnel = re.fullmatch(r"add\s+a\s+tunnel\s+on\s+(?:road\s+)?(.+)", clause, flags=re.IGNORECASE)
    if add_tunnel:
        return _op(number, "ADD_TUNNEL", add_tunnel.group(1).strip(), {}, source)
    remove_tunnel = re.fullmatch(r"remove\s+(?:the\s+)?tunnel\s+on\s+(?:road\s+)?(.+)", clause, flags=re.IGNORECASE)
    if remove_tunnel:
        return _op(number, "REMOVE_TUNNEL", remove_tunnel.group(1).strip(), {}, source)
    add_road = re.fullmatch(
        r"add\s+a\s+road\s+named\s+(.+?)\s+from\s+(.+?)\s+to\s+(.+?)(?:\s+with\s+(\d+)\s+lanes)?(?:\s+at\s+(\d+(?:\.\d+)?)\s*(km/h|mph)?)?",
        clause,
        flags=re.IGNORECASE,
    )
    if add_road:
        params: dict[str, Any] = {
            "name": add_road.group(1).strip(),
            "from_node": add_road.group(2).strip(),
            "to_node": add_road.group(3).strip(),
        }
        if add_road.group(4):
            params["lanes"] = int(add_road.group(4))
        if add_road.group(5):
            params.update(_speed_params(add_road.group(5), add_road.group(6)))
            if "speed_kmh" in params and "speed" not in params:
                pass
            speed_params = _speed_params(add_road.group(5), add_road.group(6))
            params["speed_kmh"] = speed_params.get("speed_kmh")
        return _op(number, "ADD_ROAD", add_road.group(1).strip(), params, source)
    remove_road = re.fullmatch(r"remove\s+(?:road\s+)?(.+)", clause, flags=re.IGNORECASE)
    # Keep this late so "remove the signal/bridge/..." is handled above.
    development = re.fullmatch(
        r"(?:construct|add|build)\s+a\s+(residential|commercial|mixed-use|mixed)\s+development\s+with\s+(\d+)\s+housing\s+units(?:\s+in\s+(?:the\s+)?(.+))?",
        clause,
        flags=re.IGNORECASE,
    )
    if development:
        kind = development.group(1).lower()
        if kind == "mixed":
            kind = "mixed-use"
        place = (development.group(3) or kind).strip()
        return _op(
            number,
            "ADD_LAND_USE_DEVELOPMENT",
            place,
            {"name": place, "kind": "residential" if kind == "residential" else kind, "housing_units": int(development.group(2))},
            source,
        )
    modify_land = re.fullmatch(
        r"(?:set|change)\s+(?:land\s+use\s+)?(.+?)\s+to\s+(\d+)\s+housing\s+units",
        clause,
        flags=re.IGNORECASE,
    )
    if modify_land:
        return _op(
            number,
            "MODIFY_LAND_USE",
            modify_land.group(1).strip(),
            {"housing_units": int(modify_land.group(2))},
            source,
        )
    transit = re.fullmatch(
        r"add\s+(?:a\s+)?(bus|tram)\s+route\s+named\s+(.+?)\s+from\s+(.+?)\s+to\s+(.+?)\s+every\s+(\d+(?:\.\d+)?)\s+minutes",
        clause,
        flags=re.IGNORECASE,
    )
    if transit:
        return _op(
            number,
            "ADD_OR_MODIFY_PUBLIC_TRANSPORT",
            transit.group(2).strip(),
            {
                "name": transit.group(2).strip(),
                "mode": transit.group(1).lower(),
                "from_node": transit.group(3).strip(),
                "to_node": transit.group(4).strip(),
                "headway_min": float(transit.group(5)),
            },
            source,
        )
    scale = re.fullmatch(
        r"scale\s+(morning|evening)\s+demand\s+by\s+(\d+(?:\.\d+)?)",
        clause,
        flags=re.IGNORECASE,
    )
    if scale:
        return _op(
            number,
            "MODIFY_DEMAND_PROFILE",
            f"{scale.group(1).lower()} peak",
            {"action": "scale", "peak": scale.group(1).lower(), "factor": float(scale.group(2))},
            source,
        )
    use_peak = re.fullmatch(r"use\s+(morning|evening)\s+peak\s+demand", clause, flags=re.IGNORECASE)
    if use_peak:
        peak = use_peak.group(1).lower()
        return _op(number, "MODIFY_DEMAND_PROFILE", f"{peak} peak", {"action": "select_peaks", "peaks": [peak]}, source)
    turn = re.fullmatch(
        r"(remove|add)\s+the\s+(?:connection|turn)\s+from\s+(.+?)\s+to\s+(.+)",
        clause,
        flags=re.IGNORECASE,
    )
    if turn:
        op_type = "REMOVE_ROAD_CONNECTION" if turn.group(1).lower() == "remove" else "ADD_ROAD_CONNECTION"
        start = turn.group(2).strip()
        end = turn.group(3).strip()
        params = _turn_params(start, end)
        return _op(number, op_type, f"{start} -> {end}", params, source)
    if remove_road and not re.match(r"remove\s+the\s+", clause, flags=re.IGNORECASE):
        return _op(number, "REMOVE_ROAD", remove_road.group(1).strip(), {}, source)
    return None


def _op(number: int, operation_type: str, label: str, parameters: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "operation",
        "operation": draft_operation(
            operation_type,
            label,
            parameters,
            operation_id=f"op_{number}",
            source=source,
        ),
    }


def _speed_params(value: str, unit: str | None) -> dict[str, float]:
    speed = float(value)
    if unit and unit.lower() == "mph":
        return {"speed_kmh": round(speed * MPH_TO_KMH, 6)}
    return {"speed_kmh": speed}


def _peak_name(value: str) -> str:
    lowered = value.lower()
    if lowered in {"am", "morning"}:
        return "morning"
    return "evening"


def _turn_params(start: str, end: str) -> dict[str, str]:
    params: dict[str, str] = {}
    if "_" in start:
        params["from_edge"] = start
    else:
        params["from_road"] = start
    if "_" in end:
        params["to_edge"] = end
    else:
        params["to_road"] = end
    return params
