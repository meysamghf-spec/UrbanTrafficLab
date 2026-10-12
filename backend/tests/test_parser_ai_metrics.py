"""Prompt parsing, provider failures, metrics, and report labels."""

from __future__ import annotations

import json

import httpx
from app.domain.engine import review_operations
from app.domain.llm import ProviderError, propose_operations
from app.domain.metrics import (
    absolute_difference,
    assess_comparability,
    comparison_rows,
    relative_difference,
)
from app.domain.parser import parse_rules, split_clauses
from app.domain.redact import redact
from app.domain.report import build_report
from app.domain.sample import build_sample_network
from app.services.usecases import _parse

DEMO = "Make road R3 one-way eastbound, increase the speed limit on R4 to 40 km/h, and add a signal at intersection J2."


def test_demo_prompt_and_clause_splitting():
    parsed = parse_rules(DEMO)
    types = [item["operation_type"] for item in parsed["operations"]]
    assert types == ["MODIFY_ROAD_DIRECTION", "MODIFY_SPEED_LIMIT", "ADD_TRAFFIC_SIGNAL"]
    assert parsed["unparsed_clauses"] == []
    reviewed = review_operations(build_sample_network(), parsed["operations"])
    assert [item["validation_status"] for item in reviewed] == ["valid", "valid", "valid_with_warnings"]
    assert reviewed[2]["target"]["feature_id"] == "J2"
    assert "purple bikes" not in DEMO
    unknown = parse_rules("Paint the bikes purple.")
    assert unknown["unparsed_clauses"] == ["Paint the bikes purple"]
    assert unknown["operations"] == []


def test_longer_prompt_reports_ambiguity_and_keeps_other_operations():
    prompt = (
        "In the downtown study area, make Main Street one-way eastbound, "
        "add a traffic signal at the intersection of Main Street and First Avenue, "
        "and construct a residential development with 5,000 housing units in the northern district. "
        "Compare morning and evening peak traffic."
    )
    clauses = split_clauses(prompt)
    assert any(clause.lower().startswith("construct") for clause in clauses)
    parsed = parse_rules(prompt)
    reviewed = review_operations(build_sample_network(), parsed["operations"])
    by_type = {item["operation_type"]: item for item in reviewed}
    assert by_type["MODIFY_ROAD_DIRECTION"]["validation_status"] == "ambiguous"
    assert by_type["ADD_TRAFFIC_SIGNAL"]["target"]["feature_id"] == "J1"
    assert by_type["ADD_LAND_USE_DEVELOPMENT"]["parameters"]["housing_units"] == 5000
    assert by_type["MODIFY_DEMAND_PROFILE"]["parameters"]["peaks"] == ["morning", "evening"]
    assert any("downtown" in note.lower() for note in parsed["notes"])


def test_speed_unit_conversion_and_supported_commands():
    parsed = parse_rules("Set the speed limit on R4 to 25 mph.")
    assert parsed["operations"][0]["parameters"]["speed_kmh"] == round(25 * 1.609344, 6)
    commands = [
        "Set lanes on R5 to 2.",
        "Remove road R7.",
        "Add a road named River Way from J1 to J3 with 2 lanes at 40 km/h.",
        "Add a roundabout at J2.",
        "Remove the roundabout at J2.",
        "Add a bridge on R1.",
        "Remove the bridge on R1.",
        "Add a tunnel on R2.",
        "Remove the tunnel on R2.",
        "Remove the signal at J2.",
        "Set the signal at J2 cycle to 120 seconds.",
        "Add a bus route named Harbor from J1 to J6 every 12 minutes.",
        "Scale morning demand by 1.5.",
        "Remove the turn from R1_J1_J2 to R6_J2_J5.",
    ]
    for command in commands:
        result = parse_rules(command)
        assert result["operations"], command
        assert result["unparsed_clauses"] == []


def test_llm_schema_timeout_and_secret_redaction():
    def ok_handler(_request):
        payload = {
            "operations": [
                {
                    "operation_type": "MODIFY_SPEED_LIMIT",
                    "target": {"label": "R4"},
                    "parameters": {"speed_kmh": 40},
                }
            ]
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload)}}]})

    proposed = propose_operations(
        "set R4 to 40",
        {"roads": []},
        api_key="sk-test-secret-value",
        base_url="https://example.test/v1",
        model="fixture",
        timeout_s=2,
        max_retries=0,
        transport=httpx.MockTransport(ok_handler),
    )
    assert proposed["operations"][0]["operation_type"] == "MODIFY_SPEED_LIMIT"
    assert "sk-test-secret-value" not in proposed["raw_redacted"]

    def timeout_handler(_request):
        raise httpx.TimeoutException("timed out")

    try:
        propose_operations(
            "set R4",
            {},
            api_key="sk-test-secret-value",
            base_url="https://example.test/v1",
            model="fixture",
            timeout_s=1,
            max_retries=1,
            transport=httpx.MockTransport(timeout_handler),
            sleeper=lambda _seconds: None,
        )
        raise AssertionError("timeout should fail")
    except ProviderError as exc:
        assert exc.code == "llm_timeout"

    def bad_handler(_request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "{\"nope\": true}"}}]})

    try:
        propose_operations(
            "set R4",
            {},
            api_key="sk-test-secret-value",
            base_url="https://example.test/v1",
            model="fixture",
            timeout_s=1,
            max_retries=0,
            transport=httpx.MockTransport(bad_handler),
        )
        raise AssertionError("invalid schema should fail")
    except ProviderError as exc:
        assert exc.code == "llm_invalid_schema"
    assert "sk-test-secret-value" not in redact("Authorization: Bearer sk-test-secret-value", ["sk-test-secret-value"])
    assert "[path]" in redact("wrote /tmp/utl/net.xml", [])


def test_provider_failure_falls_back_to_rules(monkeypatch):
    class Settings:
        llm_configured = True
        llm_provider = "openai_compatible"
        llm_api_key = "sk-test-secret-value"
        llm_base_url = "https://example.test/v1"
        llm_model = "fixture"
        llm_timeout_s = 1
        llm_max_retries = 0
        max_prompt_chars = 8000

    monkeypatch.setattr("app.services.usecases.get_settings", lambda: Settings())

    def boom(*_args, **_kwargs):
        raise ProviderError("provider unavailable", "llm_timeout")

    monkeypatch.setattr("app.services.usecases.propose_operations", boom)
    parsed = _parse(build_sample_network(), DEMO, "auto")
    assert parsed["parser"] == "rule_based"
    assert parsed["llm_error"]["code"] == "llm_timeout"
    assert parsed["operations"]
    forced = _parse(build_sample_network(), DEMO, "llm")
    assert forced["operations"] == []
    assert forced["llm_error"]["code"] == "llm_timeout"


def test_relative_difference_and_non_equivalent_runs():
    assert relative_difference(0, 5)["value"] is None
    assert relative_difference(None, 5)["value"] is None
    assert relative_difference(10, 15)["value"] == 0.5
    assert absolute_difference(None, 1) is None
    rows = comparison_rows(
        {"mean_travel_time_s": {"value": 0, "missing": False, "quality": "ESTIMATED"}},
        {"mean_travel_time_s": {"value": 12, "missing": False, "quality": "ESTIMATED"}},
    )
    travel = next(row for row in rows if row["key"] == "mean_travel_time_s")
    assert travel["relative_difference"] is None
    assert travel["absolute_difference"] == 12
    comparison = assess_comparability(
        {
            "status": "SUCCEEDED",
            "quality": "ESTIMATED",
            "result_usability": "full",
            "comparability": {"engine": "estimator", "seed": 1, "study_area_id": "s"},
        },
        {
            "status": "SUCCEEDED",
            "quality": "SIMULATED",
            "result_usability": "full",
            "comparability": {"engine": "sumo", "seed": 1, "study_area_id": "s"},
        },
    )
    assert comparison["comparable"] is False
    assert any("significance" in note.lower() for note in comparison["notes"])


def test_report_keeps_warnings_and_does_not_call_estimates_observed():
    report = build_report(
        {
            "title": "Demo",
            "study_area": {"name": "Synthetic Riverside Grid", "kind": "synthetic"},
            "baseline": {"content_hash": "abc"},
            "scenario": {"name": "Edit", "prompt": "test"},
            "operations": [
                {
                    "id": "op_1",
                    "operation_type": "ADD_TRAFFIC_SIGNAL",
                    "apply_status": "applied",
                    "expected_effects": ["Add a signal"],
                    "warnings": [{"code": "simplified_model", "message": "Signal timing is simplified."}],
                }
            ],
            "validation_warnings": [{"code": "note", "message": "<script>alert(1)</script>"}],
            "baseline_run": {
                "id": "b",
                "status": "SUCCEEDED",
                "engine": "estimator",
                "quality": "ESTIMATED",
                "result_usability": "full",
                "seed": 42,
                "demand_profile_id": "morning_peak",
                "metrics": {"completed_trips": {"value": 20, "missing": False, "quality": "ESTIMATED"}},
                "warnings": [{"code": "estimated_result", "message": "These figures are ESTIMATED."}],
                "provenance": {"simulator_version": None},
                "config": {"comparability": {"engine": "estimator", "seed": 42, "study_area_id": "s"}},
            },
            "scenario_run": {
                "id": "c",
                "status": "SUCCEEDED",
                "engine": "estimator",
                "quality": "ESTIMATED",
                "result_usability": "full",
                "seed": 42,
                "demand_profile_id": "morning_peak",
                "metrics": {"completed_trips": {"value": 20, "missing": False, "quality": "ESTIMATED"}},
                "warnings": [],
                "provenance": {},
                "config": {"comparability": {"engine": "estimator", "seed": 42, "study_area_id": "s"}},
            },
            "provenance": {"generator": "test"},
        }
    )
    assert "Signal timing is simplified." in report["html"]
    assert "ESTIMATED" in report["html"]
    assert "<script>" not in report["html"]
    assert "&lt;script&gt;" in report["html"]
    assert "not observed" in report["html"].lower() or "not measured" in report["html"].lower()
    assert "simplified" in report["csv"].lower() or "ESTIMATED" in report["csv"]
