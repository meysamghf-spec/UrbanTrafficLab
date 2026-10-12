"""Eclipse SUMO adapter.

The adapter shells out with an argument list. It never treats a missing
executable, a non-zero exit, or incomplete outputs as a successful simulation.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.domain.demand import active_transit
from app.domain.metrics import metric
from app.domain.network import shortest_edge_path
from app.domain.redact import redact

CancelCheck = Callable[[], bool]


@dataclass
class SumoCapability:
    available: bool
    sumo_path: str | None
    netconvert_path: str | None
    version: str | None
    reason: str | None


def detect_sumo(sumo_binary: str = "", sumo_home: str = "") -> SumoCapability:
    sumo_path = sumo_binary or shutil.which("sumo")
    netconvert_path = shutil.which("netconvert")
    home = sumo_home or os.environ.get("SUMO_HOME", "")
    if home:
        sumo_path = sumo_path or str(Path(home) / "bin" / "sumo")
        netconvert_path = netconvert_path or str(Path(home) / "bin" / "netconvert")
    if sumo_path and not Path(sumo_path).exists():
        sumo_path = None
    if netconvert_path and not Path(netconvert_path).exists():
        netconvert_path = None
    if not sumo_path or not netconvert_path:
        missing = []
        if not sumo_path:
            missing.append("sumo")
        if not netconvert_path:
            missing.append("netconvert")
        return SumoCapability(
            available=False,
            sumo_path=sumo_path,
            netconvert_path=netconvert_path,
            version=None,
            reason="Required executable not found: " + ", ".join(missing) + ".",
        )
    version = _version(sumo_path)
    return SumoCapability(True, sumo_path, netconvert_path, version, None)


def public_capability(capability: SumoCapability) -> dict[str, Any]:
    return {
        "available": capability.available,
        "version": capability.version,
        "reason": capability.reason,
    }


def run_sumo(
    network: dict[str, Any],
    profiles: list[dict[str, Any]],
    *,
    seed: int,
    workdir: Path,
    timeout_s: int,
    end_buffer_s: int,
    cancel_check: CancelCheck | None = None,
    capability: SumoCapability | None = None,
) -> dict[str, Any]:
    capability = capability or detect_sumo()
    if not capability.available or not capability.sumo_path or not capability.netconvert_path:
        return _blocked(capability.reason or "SUMO is not available.")
    workdir.mkdir(parents=True, exist_ok=True)
    horizon_s = int(profiles[0]["horizon_s"]) if profiles else 3600
    simulation_end_s = horizon_s + max(0, end_buffer_s)
    routes = build_routes(network, profiles, horizon_s)
    write_inputs(network, routes["routes"], workdir, horizon_s)
    net_argv = [
        capability.netconvert_path,
        "--node-files",
        "nodes.nod.xml",
        "--edge-files",
        "edges.edg.xml",
        "--output-file",
        "net.net.xml",
        "--no-turnarounds",
        "true",
    ]
    if (workdir / "connections.con.xml").exists():
        net_argv.extend(["--connection-files", "connections.con.xml"])
    net_result = _run_process(net_argv, workdir, timeout_s, cancel_check)
    if net_result["cancelled"]:
        return _cancelled(net_result["log"])
    if net_result["exit_code"] != 0:
        return _failed("netconvert failed.", net_result["log"], net_result["exit_code"], capability.version)
    if routes["unroutable"]:
        # Still run the vehicles that have paths. Unroutable trips are reported.
        pass
    sumo_argv = [
        capability.sumo_path,
        "-n",
        "net.net.xml",
        "-r",
        "routes.rou.xml",
        "-a",
        "additional.xml",
        "--summary-output",
        "summary.xml",
        "--tripinfo-output",
        "tripinfo.xml",
        "--tripinfo-output.write-unfinished",
        "--no-step-log",
        "--duration-log.statistics",
        "--seed",
        str(seed),
        "--end",
        str(simulation_end_s),
        "--time-to-teleport",
        "-1",
        "--collision.action",
        "warn",
    ]
    started = time.monotonic()
    sumo_result = _run_process(sumo_argv, workdir, timeout_s, cancel_check)
    duration_s = time.monotonic() - started
    log = redact(net_result["log"] + "\n" + sumo_result["log"])[-50_000:]
    if sumo_result["cancelled"]:
        return _cancelled(log, duration_s=duration_s)
    if sumo_result["timed_out"]:
        failed = _failed("SUMO exceeded the configured timeout.", log, None, capability.version)
        failed["duration_s"] = duration_s
        return failed
    parsed = parse_outputs(workdir, horizon_s=horizon_s, unroutable=routes["unroutable"])
    warnings = list(parsed["warnings"])
    warnings.extend(routes["warnings"])
    if sumo_result["exit_code"] != 0:
        failed = _failed(
            f"SUMO exited with status {sumo_result['exit_code']}.",
            log,
            sumo_result["exit_code"],
            capability.version,
        )
        failed["duration_s"] = duration_s
        failed["warnings"] = warnings
        if parsed["parsed_any"]:
            failed["metrics"] = parsed["metrics"]
            failed["link_metrics"] = parsed["link_metrics"]
            failed["result_usability"] = "partial"
            failed["warnings"].append(
                {
                    "code": "incomplete_failure",
                    "message": "SUMO failed. Any parsed numbers are incomplete and are not a successful simulation.",
                }
            )
        return failed
    if not parsed["summary_ok"] or not parsed["tripinfo_ok"]:
        failed = _failed(
            "SUMO exited successfully but required trip or summary outputs were missing.",
            log,
            0,
            capability.version,
        )
        failed["duration_s"] = duration_s
        failed["warnings"] = warnings
        return failed
    usability = "full" if parsed["edgedata_ok"] else "partial"
    if not parsed["edgedata_ok"]:
        warnings.append(
            {
                "code": "missing_link_metrics",
                "message": "SUMO did not produce link-level edge data. Road coloring is unavailable for this run.",
            }
        )
    return {
        "status": "SUCCEEDED",
        "exit_code": 0,
        "log": log,
        "metrics": parsed["metrics"],
        "link_metrics": parsed["link_metrics"],
        "timesteps": parsed["timesteps"],
        "warnings": warnings,
        "quality": "SIMULATED",
        "result_usability": usability,
        "version": capability.version,
        "duration_s": duration_s,
        "error": None,
        "simulation_end_s": simulation_end_s,
        "horizon_s": horizon_s,
        "playback_available": bool(parsed["timesteps"]),
    }


def build_routes(network: dict[str, Any], profiles: list[dict[str, Any]], horizon_s: int) -> dict[str, Any]:
    routes: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    unroutable = 0
    for profile in profiles:
        for pair in profile["od_pairs"]:
            path = shortest_edge_path(network, pair["origin"], pair["destination"])
            vehicles = int(pair["vehicles"])
            if not path or vehicles <= 0:
                if vehicles > 0:
                    unroutable += vehicles
                    warnings.append(
                        {
                            "code": "unroutable_demand",
                            "message": (
                                f"{vehicles} vehicles from {pair['origin']} to {pair['destination']} "
                                "have no directed path and were not sent to SUMO."
                            ),
                        }
                    )
                continue
            routes.append(
                {
                    "id": f"{profile['id']}_{pair['id']}",
                    "edges": [edge["id"] for edge in path],
                    "vehicles": vehicles,
                    "vtype": "bus" if pair.get("vehicle_class") == "bus" else "car",
                    "synthetic": bool(profile.get("synthetic", True)),
                }
            )
    for transit in active_transit(network):
        count = max(1, int(horizon_s / (float(transit["headway_min"]) * 60.0)))
        routes.append(
            {
                "id": f"transit_{transit['id']}",
                "edges": list(transit["edge_ids"]),
                "vehicles": count,
                "vtype": "bus" if transit.get("mode") == "bus" else "car",
                "synthetic": True,
            }
        )
        warnings.append(
            {
                "code": "simplified_transit",
                "message": (
                    f"Transit route {transit['id']} is a simplified {transit.get('mode', 'bus')} flow "
                    "without passenger boarding or dwell time."
                ),
            }
        )
    return {"routes": routes, "warnings": warnings, "unroutable": unroutable}


def write_inputs(
    network: dict[str, Any],
    routes: list[dict[str, Any]],
    workdir: Path,
    horizon_s: int = 3600,
) -> None:
    if not network["nodes"]:
        raise ValueError("The network has no intersections to simulate.")
    west = min(node["lon"] for node in network["nodes"])
    south = min(node["lat"] for node in network["nodes"])
    nodes_xml = ET.Element("nodes")
    for node in network["nodes"]:
        x, y = _project(node["lon"], node["lat"], west, south)
        node_type = "priority"
        if any(signal["node_id"] == node["id"] for signal in network["signals"]):
            node_type = "traffic_light"
        elif any(item["node_id"] == node["id"] for item in network["roundabouts"]):
            node_type = "right_before_left"
        ET.SubElement(nodes_xml, "node", {"id": node["id"], "x": f"{x:.3f}", "y": f"{y:.3f}", "type": node_type})
    edges_xml = ET.Element("edges")
    for edge in network["edges"]:
        attrib = {
            "id": edge["id"],
            "from": edge["from_node"],
            "to": edge["to_node"],
            "numLanes": str(int(edge["lanes"])),
            "speed": f"{float(edge['speed_kmh']) / 3.6:.6f}",
            "length": f"{float(edge['length_m']):.3f}",
            "priority": _priority(edge.get("road_type")),
            "name": edge.get("name") or edge["id"],
        }
        element = ET.SubElement(edges_xml, "edge", attrib)
        if edge.get("bridge_id"):
            ET.SubElement(element, "param", {"key": "bridge", "value": str(edge["bridge_id"])})
        if edge.get("tunnel_id"):
            ET.SubElement(element, "param", {"key": "tunnel", "value": str(edge["tunnel_id"])})
    routes_xml = ET.Element("routes")
    ET.SubElement(
        routes_xml,
        "vType",
        {"id": "car", "accel": "2.6", "decel": "4.5", "sigma": "0.5", "length": "5.0", "minGap": "2.5", "maxSpeed": "16.67"},
    )
    ET.SubElement(
        routes_xml,
        "vType",
        {"id": "bus", "accel": "1.2", "decel": "4.0", "sigma": "0.5", "length": "12.0", "minGap": "3.0", "maxSpeed": "13.89"},
    )
    for route in routes:
        ET.SubElement(routes_xml, "route", {"id": route["id"], "edges": " ".join(route["edges"])})
        ET.SubElement(
            routes_xml,
            "flow",
            {
                "id": f"{route['id']}_flow",
                "type": route["vtype"],
                "route": route["id"],
                "begin": "0",
                "end": str(int(horizon_s)),
                "number": str(int(route["vehicles"])),
                "departLane": "best",
                "departSpeed": "avg",
            },
        )
    if not routes:
        # SUMO still needs a vType. An empty flow set is valid; the run reports zero trips.
        pass
    additional = ET.Element("additional")
    ET.SubElement(additional, "edgeData", {"id": "utl", "file": "edgedata.xml", "period": "600"})
    _write_xml(workdir / "nodes.nod.xml", nodes_xml)
    _write_xml(workdir / "edges.edg.xml", edges_xml)
    _write_xml(workdir / "routes.rou.xml", routes_xml)
    _write_xml(workdir / "additional.xml", additional)
    restrictions = network.get("turn_restrictions") or []
    if restrictions:
        connections = ET.Element("connections")
        for restriction in restrictions:
            ET.SubElement(
                connections,
                "connection",
                {"from": restriction["from_edge"], "to": restriction["to_edge"], "disallow": "all"},
            )
        _write_xml(workdir / "connections.con.xml", connections)


def parse_outputs(workdir: Path, *, horizon_s: int, unroutable: int) -> dict[str, Any]:
    trip_path = workdir / "tripinfo.xml"
    summary_path = workdir / "summary.xml"
    edge_path = workdir / "edgedata.xml"
    warnings: list[dict[str, str]] = []
    trips = _parse_tripinfo(trip_path) if trip_path.exists() else None
    summary = _parse_summary(summary_path) if summary_path.exists() else None
    intervals = _parse_edgedata(edge_path) if edge_path.exists() else None
    if trips is None:
        warnings.append({"code": "missing_tripinfo", "message": "SUMO tripinfo output was not found."})
    if summary is None:
        warnings.append({"code": "missing_summary", "message": "SUMO summary output was not found."})
    completed = trips["completed"] if trips else []
    unfinished_records = trips["unfinished"] if trips else []
    durations = [item["duration"] for item in completed if item["duration"] is not None]
    losses = [item["time_loss"] for item in completed if item["time_loss"] is not None]
    waits = [item["waiting"] for item in completed if item["waiting"] is not None]
    speeds = [item["speed_kmh"] for item in completed if item["speed_kmh"] is not None]
    arrived = summary["arrived"] if summary and summary["arrived"] is not None else len(completed)
    departed = summary["inserted"] if summary and summary["inserted"] is not None else len(completed) + len(unfinished_records)
    unfinished = None
    if summary and summary["inserted"] is not None and summary["arrived"] is not None:
        unfinished = max(0, int(summary["inserted"]) - int(summary["arrived"])) + unroutable
    elif trips is not None:
        unfinished = len(unfinished_records) + unroutable
    hours = horizon_s / 3600.0 if horizon_s else None
    throughput = None if hours in (None, 0) or arrived is None else arrived / hours
    quality = "SIMULATED"
    metrics = {
        "completed_trips": metric("completed_trips", len(completed), quality=quality),
        "departed_vehicles": metric("departed_vehicles", departed, quality=quality),
        "arrived_vehicles": metric("arrived_vehicles", arrived, quality=quality),
        "unfinished_trips": metric("unfinished_trips", unfinished, quality=quality),
        "unroutable_trips": metric("unroutable_trips", unroutable, quality=quality, notes=["Counted before SUMO because no directed path exists."]),
        "mean_travel_time_s": metric(
            "mean_travel_time_s",
            None if not durations else sum(durations) / len(durations),
            quality=quality,
            notes=["Mean duration of completed trips. Unfinished trips are excluded from the mean."],
        ),
        "mean_speed_kmh": metric(
            "mean_speed_kmh",
            None if not speeds else sum(speeds) / len(speeds),
            quality=quality,
            notes=["Unweighted mean of completed-trip route length divided by duration."],
        ),
        "mean_time_loss_s": metric(
            "mean_time_loss_s",
            None if not losses else sum(losses) / len(losses),
            quality=quality,
        ),
        "mean_waiting_time_s": metric(
            "mean_waiting_time_s",
            None if not waits else sum(waits) / len(waits),
            quality=quality,
        ),
        "network_throughput_vph": metric(
            "network_throughput_vph",
            throughput,
            quality=quality,
            notes=["Arrived vehicles divided by the demand horizon in hours."],
        ),
    }
    if summary and summary.get("collisions"):
        warnings.append({"code": "collisions", "message": f"SUMO reported {summary['collisions']} collisions."})
    if summary and summary.get("teleports"):
        warnings.append({"code": "teleports", "message": f"SUMO reported {summary['teleports']} teleports."})
    link_metrics, timesteps = _link_metrics(intervals)
    return {
        "metrics": metrics,
        "link_metrics": link_metrics,
        "timesteps": timesteps,
        "warnings": warnings,
        "tripinfo_ok": trips is not None,
        "summary_ok": summary is not None,
        "edgedata_ok": intervals is not None,
        "parsed_any": trips is not None or summary is not None,
    }


def _parse_tripinfo(path: Path) -> dict[str, list[dict[str, Any]]]:
    root = ET.parse(path).getroot()
    completed = []
    unfinished = []
    for element in root.findall("tripinfo"):
        arrival = _float_attr(element, "arrival")
        duration = _float_attr(element, "duration")
        route_length = _float_attr(element, "routeLength")
        waiting = _float_attr(element, "waitingTime")
        time_loss = _float_attr(element, "timeLoss")
        speed = None
        if route_length is not None and duration is not None and duration > 0:
            speed = route_length / duration * 3.6
        record = {
            "id": element.get("id"),
            "duration": duration,
            "route_length_m": route_length,
            "waiting": waiting,
            "time_loss": time_loss,
            "speed_kmh": speed,
        }
        if arrival is None or arrival < 0:
            unfinished.append(record)
        else:
            completed.append(record)
    return {"completed": completed, "unfinished": unfinished}


def _parse_summary(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    steps = root.findall("step")
    if not steps:
        return {"inserted": None, "arrived": None, "collisions": None, "teleports": None}
    last = steps[-1]
    return {
        "inserted": _int_attr(last, "inserted"),
        "arrived": _int_attr(last, "arrived"),
        "collisions": _int_attr(last, "collisions") or 0,
        "teleports": _int_attr(last, "teleports") or 0,
        "time": _float_attr(last, "time"),
    }


def _parse_edgedata(path: Path) -> list[dict[str, Any]]:
    root = ET.parse(path).getroot()
    intervals = []
    for interval in root.findall("interval"):
        begin = _float_attr(interval, "begin") or 0.0
        end = _float_attr(interval, "end") or begin
        edges = []
        for edge in interval.findall("edge"):
            entered = _float_attr(edge, "entered")
            sampled = _float_attr(edge, "sampledSeconds")
            speed = _float_attr(edge, "speed")
            occupancy = _float_attr(edge, "occupancy")
            waiting = _float_attr(edge, "waitingTime")
            if speed is not None and speed < 0:
                speed = None
            edges.append(
                {
                    "edge_id": edge.get("id"),
                    "entered": entered,
                    "sampled_seconds": sampled,
                    "speed_m_s": speed,
                    "occupancy": None if occupancy is None or occupancy < 0 else occupancy,
                    "waiting_time_s": waiting,
                }
            )
        intervals.append({"begin_s": begin, "end_s": end, "edges": edges})
    return intervals


def _link_metrics(intervals: list[dict[str, Any]] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not intervals:
        return [], []
    aggregate: dict[str, dict[str, float]] = {}
    timesteps = []
    for interval in intervals:
        duration = max(float(interval["end_s"]) - float(interval["begin_s"]), 1.0)
        frame: dict[str, Any] = {}
        for edge in interval["edges"]:
            edge_id = edge["edge_id"]
            if not edge_id or edge_id.startswith(":"):
                continue
            bucket = aggregate.setdefault(edge_id, {"entered": 0.0, "sampled": 0.0, "speed_weight": 0.0, "wait": 0.0, "occupancy": 0.0, "occupancy_n": 0.0})
            entered = edge["entered"] or 0.0
            bucket["entered"] += entered
            sampled = edge["sampled_seconds"] or 0.0
            bucket["sampled"] += sampled
            if edge["speed_m_s"] is not None and sampled > 0:
                bucket["speed_weight"] += edge["speed_m_s"] * sampled
            if edge["waiting_time_s"] is not None:
                bucket["wait"] += edge["waiting_time_s"]
            if edge["occupancy"] is not None:
                bucket["occupancy"] += edge["occupancy"]
                bucket["occupancy_n"] += 1
            speed_kmh = None if edge["speed_m_s"] is None else edge["speed_m_s"] * 3.6
            frame[edge_id] = {
                "speed_kmh": speed_kmh,
                "flow_vph": entered / duration * 3600.0,
                "occupancy": edge["occupancy"],
                "quality": "SIMULATED",
            }
        timesteps.append({"t_s": interval["end_s"], "edges": frame})
    total_duration = max(intervals[-1]["end_s"] - intervals[0]["begin_s"], 1.0)
    link_metrics = []
    for edge_id, bucket in sorted(aggregate.items()):
        speed = None
        if bucket["sampled"] > 0 and bucket["speed_weight"] > 0:
            speed = bucket["speed_weight"] / bucket["sampled"] * 3.6
        occupancy = None if bucket["occupancy_n"] == 0 else bucket["occupancy"] / bucket["occupancy_n"]
        link_metrics.append(
            {
                "edge_id": edge_id,
                "flow_vph": bucket["entered"] / total_duration * 3600.0,
                "speed_kmh": speed,
                "occupancy": occupancy,
                "waiting_time_s": bucket["wait"],
                "queue_length_m": None,
                "volume_capacity_ratio": None,
                "quality": "SIMULATED",
            }
        )
    return link_metrics, timesteps


def _run_process(
    argv: list[str],
    workdir: Path,
    timeout_s: int,
    cancel_check: CancelCheck | None,
) -> dict[str, Any]:
    env = os.environ.copy()
    if not env.get("SUMO_HOME") and Path("/usr/share/sumo").exists():
        env["SUMO_HOME"] = "/usr/share/sumo"
    process = subprocess.Popen(
        argv,
        cwd=workdir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        env=env,
    )
    deadline = time.monotonic() + timeout_s
    cancelled = False
    timed_out = False
    while process.poll() is None:
        if cancel_check and cancel_check():
            _kill(process)
            cancelled = True
            break
        if time.monotonic() > deadline:
            _kill(process)
            timed_out = True
            break
        time.sleep(0.05)
    stdout, stderr = process.communicate()
    log = redact(f"$ {' '.join(Path(part).name if part.startswith(str(workdir)) else part for part in argv)}\n{stdout or ''}{stderr or ''}")
    return {
        "exit_code": process.returncode,
        "log": log,
        "cancelled": cancelled,
        "timed_out": timed_out,
        "argv": argv,
    }


def _kill(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        process.kill()


def _project(lon: float, lat: float, origin_lon: float, origin_lat: float) -> tuple[float, float]:
    from app.domain.geo import to_local_meters

    return to_local_meters(lon, lat, origin_lon, origin_lat)


def _priority(road_type: str | None) -> str:
    return {
        "motorway": "13",
        "trunk": "11",
        "primary": "9",
        "secondary": "7",
        "tertiary": "6",
        "residential": "4",
        "unclassified": "5",
    }.get(road_type or "", "5")


def _write_xml(path: Path, element: ET.Element) -> None:
    tree = ET.ElementTree(element)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="unicode", xml_declaration=True)


def _version(sumo_path: str) -> str | None:
    try:
        completed = subprocess.run(
            [sumo_path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (completed.stdout or "") + (completed.stderr or "")
    for line in text.splitlines():
        if "Version" in line:
            return line.strip()
    return text.strip().splitlines()[0] if text.strip() else None


def _float_attr(element: ET.Element, name: str) -> float | None:
    raw = element.get(name)
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value


def _int_attr(element: ET.Element, name: str) -> int | None:
    value = _float_attr(element, name)
    if value is None:
        return None
    return int(value)


def _blocked(reason: str) -> dict[str, Any]:
    return {
        "status": "BLOCKED_MISSING_DEPENDENCY",
        "exit_code": None,
        "log": "",
        "metrics": None,
        "link_metrics": [],
        "timesteps": [],
        "warnings": [{"code": "missing_sumo", "message": reason}],
        "quality": "NONE",
        "result_usability": "none",
        "version": None,
        "duration_s": 0.0,
        "error": reason,
        "playback_available": False,
    }


def _failed(message: str, log: str, exit_code: int | None, version: str | None) -> dict[str, Any]:
    return {
        "status": "FAILED",
        "exit_code": exit_code,
        "log": log,
        "metrics": None,
        "link_metrics": [],
        "timesteps": [],
        "warnings": [{"code": "sumo_failed", "message": message}],
        "quality": "NONE",
        "result_usability": "none",
        "version": version,
        "duration_s": 0.0,
        "error": message,
        "playback_available": False,
    }


def _cancelled(log: str, duration_s: float = 0.0) -> dict[str, Any]:
    return {
        "status": "CANCELLED",
        "exit_code": None,
        "log": log,
        "metrics": None,
        "link_metrics": [],
        "timesteps": [],
        "warnings": [{"code": "cancelled", "message": "The simulation was cancelled."}],
        "quality": "NONE",
        "result_usability": "none",
        "version": None,
        "duration_s": duration_s,
        "error": "The simulation was cancelled.",
        "playback_available": False,
    }
