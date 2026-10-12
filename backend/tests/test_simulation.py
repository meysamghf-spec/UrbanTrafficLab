"""SUMO capability, output parsing, and honest failure states.

The integration test at the bottom runs the real SUMO binary when it is installed.
Unit tests that replace the binary do not claim that SUMO executed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.domain.sample import build_sample_network
from app.domain.sumo import SumoCapability, detect_sumo, parse_outputs, run_sumo
from app.services import usecases

TRIPINFO = """<?xml version="1.0" encoding="UTF-8"?>
<tripinfos>
  <tripinfo id="a" duration="100" routeLength="1000" waitingTime="5" timeLoss="12" arrival="100"/>
  <tripinfo id="b" duration="0" routeLength="500" waitingTime="0" timeLoss="0" arrival="-1"/>
  <tripinfo id="c" duration="50" routeLength="0" waitingTime="0" timeLoss="0" arrival="50"/>
</tripinfos>
"""

SUMMARY = """<?xml version="1.0" encoding="UTF-8"?>
<summary>
  <step time="0" loaded="3" inserted="0" running="0" waiting="3" ended="0" arrived="0" collisions="0" teleports="0"/>
  <step time="3600" loaded="3" inserted="2" running="0" waiting="1" ended="2" arrived="2" collisions="0" teleports="0"/>
</summary>
"""

EDGEDATA = """<?xml version="1.0" encoding="UTF-8"?>
<meandata>
  <interval begin="0" end="600" id="utl">
    <edge id="R1_J1_J2" sampledSeconds="120" entered="10" speed="10" occupancy="5" waitingTime="2"/>
    <edge id=":J2_0" sampledSeconds="1" entered="1" speed="1" occupancy="1" waitingTime="0"/>
    <edge id="R3_J4_J5" sampledSeconds="0" entered="0" speed="-1" occupancy="-1" waitingTime="0"/>
  </interval>
</meandata>
"""


def test_parse_outputs_handles_missing_zero_and_internal_edges(tmp_path: Path):
    empty = parse_outputs(tmp_path, horizon_s=3600, unroutable=0)
    assert empty["tripinfo_ok"] is False
    assert empty["metrics"]["completed_trips"]["value"] == 0
    assert empty["metrics"]["mean_travel_time_s"]["missing"] is True
    assert empty["metrics"]["network_throughput_vph"]["value"] == 0
    (tmp_path / "tripinfo.xml").write_text(TRIPINFO, encoding="utf-8")
    (tmp_path / "summary.xml").write_text(SUMMARY, encoding="utf-8")
    (tmp_path / "edgedata.xml").write_text(EDGEDATA, encoding="utf-8")
    parsed = parse_outputs(tmp_path, horizon_s=3600, unroutable=1)
    assert parsed["metrics"]["completed_trips"]["value"] == 2
    assert parsed["metrics"]["unfinished_trips"]["value"] == 1
    assert parsed["metrics"]["mean_travel_time_s"]["value"] == 75
    assert parsed["metrics"]["mean_speed_kmh"]["value"] == pytest.approx(18.0)
    assert parsed["metrics"]["network_throughput_vph"]["value"] == pytest.approx(2.0)
    edge_ids = {item["edge_id"] for item in parsed["link_metrics"]}
    assert ":J2_0" not in edge_ids
    assert "R1_J1_J2" in edge_ids
    slow = next(item for item in parsed["link_metrics"] if item["edge_id"] == "R3_J4_J5")
    assert slow["speed_kmh"] is None
    assert slow["flow_vph"] == 0
    zero_horizon = parse_outputs(tmp_path, horizon_s=0, unroutable=0)
    assert zero_horizon["metrics"]["network_throughput_vph"]["missing"] is True


def test_missing_sumo_is_blocked_without_invented_metrics(client, monkeypatch):
    monkeypatch.setattr(
        usecases,
        "detect_sumo",
        lambda *_args, **_kwargs: SumoCapability(False, None, None, None, "SUMO executable was not found."),
    )
    study = client.post("/api/v1/study-areas/sample").json()
    response = client.post(
        "/api/v1/simulations",
        json={"network_version_id": study["baseline_network_version_id"], "engine": "sumo", "seed": 7},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "BLOCKED_MISSING_DEPENDENCY"
    assert body["quality"] == "NONE"
    assert body["metrics"] is None
    assert "SUMO" in (body["blocked_reason"] or "")
    assert body["estimate_available"] is True
    logs = client.get(f"/api/v1/simulations/{body['id']}/logs").json()
    assert "/tmp" not in logs["log"]


def test_sumo_process_failure_is_not_labeled_successful(client, monkeypatch):
    monkeypatch.setattr(
        usecases,
        "detect_sumo",
        lambda *_args, **_kwargs: SumoCapability(True, "sumo", "netconvert", "Eclipse SUMO 1.18.0", None),
    )

    def fail(*_args, **_kwargs):
        return {
            "status": "FAILED",
            "exit_code": 1,
            "log": "sumo failed at /tmp/utl_secret/net.net.xml",
            "metrics": None,
            "link_metrics": [],
            "timesteps": [],
            "warnings": [{"code": "sumo_failed", "message": "SUMO exited with status 1."}],
            "quality": "NONE",
            "result_usability": "none",
            "version": "Eclipse SUMO 1.18.0",
            "duration_s": 0.2,
            "error": "SUMO exited with status 1.",
        }

    monkeypatch.setattr("app.services.usecases.run_sumo", fail)
    study = client.post("/api/v1/study-areas/sample").json()
    response = client.post(
        "/api/v1/simulations",
        json={"network_version_id": study["baseline_network_version_id"], "engine": "sumo", "seed": 7},
    )
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["quality"] == "NONE"
    assert body["metrics"] is None
    assert body["exit_code"] == 1
    logs = client.get(f"/api/v1/simulations/{body['id']}/logs").json()
    assert "/tmp/utl_secret" not in logs["log"]


@pytest.mark.integration
def test_real_sumo_execution_on_sample_network(tmp_path: Path):
    capability = detect_sumo()
    if not capability.available:
        pytest.skip(f"SUMO is not installed: {capability.reason}")
    network = build_sample_network()
    profile = next(item for item in network["demand_profiles"] if item["id"] == "morning_peak")
    result = run_sumo(
        network,
        [profile],
        seed=42,
        workdir=tmp_path,
        timeout_s=120,
        end_buffer_s=900,
        capability=capability,
    )
    assert result["status"] == "SUCCEEDED", result.get("log")
    assert result["quality"] == "SIMULATED"
    assert result["exit_code"] == 0
    assert result["version"]
    assert "SUMO" in result["version"]
    assert result["metrics"]["departed_vehicles"]["value"] is not None
    assert result["metrics"]["departed_vehicles"]["quality"] == "SIMULATED"
    assert result["result_usability"] in {"full", "partial"}
    if result["result_usability"] == "full":
        assert result["link_metrics"]
        assert result["playback_available"] is True
    assert not any(item["edge_id"].startswith(":") for item in result["link_metrics"])
