"""API workflow: sample network, scenario isolation, estimates, and exports."""

from __future__ import annotations

DEMO = (
    "Make road R3 one-way eastbound, increase the speed limit on R4 to 40 km/h, "
    "and add a signal at intersection J2."
)


def test_health_ready_and_capabilities(client):
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    ready = client.get("/api/v1/ready")
    assert ready.status_code == 200
    assert ready.json()["database"] == "ok"
    caps = client.get("/api/v1/capabilities").json()
    assert caps["estimator"]["label"] == "ESTIMATED"
    assert caps["ai"]["rule_based"] is True
    assert caps["ai"]["llm_configured"] is False
    assert caps["auth"]["enabled"] is False
    assert "sumo_path" not in caps["sumo"]
    assert "netconvert_path" not in caps["sumo"]


def test_sample_workflow_isolates_baseline_and_exports(client):
    study = client.post("/api/v1/study-areas/sample").json()
    assert study["slug"] == "synthetic-riverside-grid"
    assert study["kind"] == "synthetic"
    assert study["real_city"] is False
    assert "not a real city" in study["attribution"].lower() or "not derived" in study["attribution"].lower()
    again = client.post("/api/v1/study-areas/sample")
    assert again.status_code == 200
    assert again.json()["id"] == study["id"]

    baseline_id = study["baseline_network_version_id"]
    baseline = client.get(f"/api/v1/networks/{baseline_id}").json()
    assert baseline["immutable"] is True
    assert baseline["role"] == "baseline"
    assert baseline["stats"]["roads"] == 7
    feature = client.get(f"/api/v1/networks/{baseline_id}/features/R3").json()
    assert feature["feature_id"] == "R3"
    validation = client.post(f"/api/v1/networks/{baseline_id}/validate").json()
    assert validation["ok"] is True

    parsed = client.post(
        "/api/v1/parse",
        json={"network_version_id": baseline_id, "prompt": DEMO, "parser": "rules"},
    )
    assert parsed.status_code == 200
    operations = parsed.json()["operations"]
    assert [item["operation_type"] for item in operations] == [
        "MODIFY_ROAD_DIRECTION",
        "MODIFY_SPEED_LIMIT",
        "ADD_TRAFFIC_SIGNAL",
    ]
    assert [item["validation_status"] for item in operations] == ["valid", "valid", "valid_with_warnings"]
    for item in operations:
        item["decision"] = "accepted"

    created = client.post(
        "/api/v1/scenarios",
        json={
            "study_area_id": study["id"],
            "name": "Riverside edits",
            "prompt": DEMO,
            "operations": operations,
            "execution_policy": "review",
        },
    )
    assert created.status_code == 200
    scenario = created.json()
    assert scenario["status"] == "applied"
    assert scenario["baseline_hash"] == baseline["content_hash"]
    assert scenario["network_hash"] != baseline["content_hash"]
    assert all(item["apply_status"] == "applied" for item in scenario["operations"])

    unchanged = client.get(f"/api/v1/networks/{baseline_id}").json()
    assert unchanged["content_hash"] == baseline["content_hash"]
    r3 = client.get(f"/api/v1/networks/{scenario['network_version_id']}/features/R3").json()
    assert [edge["edge_id"] for edge in r3["directed_edges"]] == ["R3_J4_J5"]
    r4 = client.get(f"/api/v1/networks/{scenario['network_version_id']}/features/R4").json()
    assert {edge["speed_kmh"] for edge in r4["directed_edges"]} == {40.0}
    signal = client.get(f"/api/v1/networks/{scenario['network_version_id']}/features/J2").json()
    assert signal["signal"] is not None

    layers = client.get(
        "/api/v1/map/layers",
        params={"network_version_id": scenario["network_version_id"], "scenario_id": scenario["id"]},
    ).json()
    assert layers["synthetic"] is True
    assert layers["roads"]["type"] == "FeatureCollection"
    assert any(feature["properties"]["changed"] for feature in layers["roads"]["features"])
    assert "unavailable" in layers["data_note"].lower()

    baseline_run = client.post(
        "/api/v1/simulations",
        json={"network_version_id": baseline_id, "engine": "estimator", "demand_profile_id": "morning_peak", "seed": 42},
    )
    scenario_run = client.post(
        "/api/v1/simulations",
        json={
            "network_version_id": scenario["network_version_id"],
            "scenario_id": scenario["id"],
            "engine": "estimator",
            "demand_profile_id": "morning_peak",
            "seed": 42,
        },
    )
    assert baseline_run.status_code == 200
    assert scenario_run.status_code == 200
    base = baseline_run.json()
    edited = scenario_run.json()
    assert base["status"] == "SUCCEEDED"
    assert edited["status"] == "SUCCEEDED"
    assert base["quality"] == "ESTIMATED"
    assert edited["quality"] == "ESTIMATED"
    assert base["metrics"]["mean_waiting_time_s"]["missing"] is True
    assert any(item["code"] == "estimated_result" for item in base["warnings"])

    comparison = client.get(
        "/api/v1/comparisons",
        params={"baseline_run_id": base["id"], "scenario_run_id": edited["id"]},
    ).json()
    assert comparison["comparability"]["comparable"] is True
    speed = next(row for row in comparison["rows"] if row["key"] == "mean_speed_kmh")
    assert speed["baseline_quality"] == "ESTIMATED"
    assert speed["absolute_difference"] is not None

    report = client.post(
        "/api/v1/reports",
        json={
            "study_area_id": study["id"],
            "scenario_id": scenario["id"],
            "baseline_run_id": base["id"],
            "scenario_run_id": edited["id"],
        },
    )
    assert report.status_code == 200
    report_id = report.json()["id"]
    html = client.get(f"/api/v1/reports/{report_id}/download", params={"format": "html"})
    csv = client.get(f"/api/v1/reports/{report_id}/download", params={"format": "csv"})
    geo = client.get(f"/api/v1/reports/{report_id}/download", params={"format": "geojson"})
    assert html.status_code == 200
    assert "ESTIMATED" in html.text
    assert "decision-support" in html.text.lower() or "not a prediction" in html.text.lower() or "not guaranteed" in html.text.lower()
    assert csv.status_code == 200
    assert "ESTIMATED" in csv.text
    assert geo.status_code == 200
    assert geo.json()["type"] == "FeatureCollection"

    rolled = client.post(f"/api/v1/scenarios/{scenario['id']}/rollback")
    assert rolled.status_code == 200
    assert rolled.json()["network_version_id"] == baseline_id
    blocked = client.post(f"/api/v1/scenarios/{scenario['id']}/rollback")
    assert blocked.status_code == 409
    cloned = client.post(f"/api/v1/scenarios/{scenario['id']}/clone", params={"name": "Clone"})
    assert cloned.status_code == 200
    assert cloned.json()["id"] != scenario["id"]
    history = client.get(f"/api/v1/scenarios/{scenario['id']}/history").json()
    assert any(event["kind"] == "rollback" for event in history["events"])


def test_rejects_invalid_requests_and_unaccepted_plans(client):
    study = client.post("/api/v1/study-areas/sample").json()
    baseline_id = study["baseline_network_version_id"]
    unknown = client.post(
        "/api/v1/parse",
        json={"network_version_id": baseline_id, "prompt": "Paint the bikes purple.", "parser": "rules"},
    )
    assert unknown.status_code == 200
    assert unknown.json()["operations"] == []
    empty = client.post(
        "/api/v1/scenarios",
        json={"study_area_id": study["id"], "name": "Empty", "operations": unknown.json()["operations"]},
    )
    assert empty.status_code == 422
    assert empty.json()["error"]["code"] == "nothing_to_apply"
    bad = client.post("/api/v1/simulations", json={"engine": "magic"})
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "validation_error"
    missing = client.get("/api/v1/networks/does-not-exist")
    assert missing.status_code == 404
    oversized = client.post(
        f"/api/v1/study-areas/{study['id']}/import/geojson",
        content=b"{" + b"x" * 20,
        headers={"content-type": "application/json", "content-length": "9000000"},
    )
    assert oversized.status_code == 413


def test_auto_policy_keeps_warnings_pending(client):
    study = client.post("/api/v1/study-areas/sample").json()
    baseline_id = study["baseline_network_version_id"]
    parsed = client.post(
        "/api/v1/parse",
        json={"network_version_id": baseline_id, "prompt": DEMO, "parser": "auto"},
    ).json()
    created = client.post(
        "/api/v1/scenarios",
        json={
            "study_area_id": study["id"],
            "name": "Auto",
            "operations": parsed["operations"],
            "execution_policy": "auto_valid",
        },
    )
    assert created.status_code == 200
    by_type = {item["operation_type"]: item for item in created.json()["operations"]}
    assert by_type["MODIFY_SPEED_LIMIT"]["apply_status"] == "applied"
    assert by_type["MODIFY_ROAD_DIRECTION"]["apply_status"] == "applied"
    assert by_type["ADD_TRAFFIC_SIGNAL"]["decision"] == "pending"
    assert by_type["ADD_TRAFFIC_SIGNAL"]["apply_status"] == "not_applied"
    assert by_type["ADD_TRAFFIC_SIGNAL"]["warnings"]
