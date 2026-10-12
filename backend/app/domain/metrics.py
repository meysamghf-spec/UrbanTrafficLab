"""Metric definitions and baseline/scenario comparison.

Missing values stay missing. A zero denominator does not produce an infinite
relative difference. No function in this module computes a statistical test.
"""

from __future__ import annotations

import math
from typing import Any

METRIC_SPECS: list[dict[str, str]] = [
    {"key": "completed_trips", "label": "Completed trips", "unit": "trips"},
    {"key": "departed_vehicles", "label": "Departed vehicles", "unit": "vehicles"},
    {"key": "arrived_vehicles", "label": "Arrived vehicles", "unit": "vehicles"},
    {"key": "unfinished_trips", "label": "Unfinished trips", "unit": "trips"},
    {"key": "unroutable_trips", "label": "Unroutable trips", "unit": "trips"},
    {"key": "mean_travel_time_s", "label": "Mean travel time", "unit": "s"},
    {"key": "mean_speed_kmh", "label": "Mean speed", "unit": "km/h"},
    {"key": "mean_time_loss_s", "label": "Mean time loss", "unit": "s"},
    {"key": "mean_waiting_time_s", "label": "Mean waiting time", "unit": "s"},
    {"key": "network_throughput_vph", "label": "Network throughput", "unit": "veh/h"},
]

COMPARISON_KEYS = (
    "engine",
    "seed",
    "repeats",
    "horizon_s",
    "simulation_end_s",
    "demand_fingerprint",
    "transit_fingerprint",
    "study_area_id",
)


def metric(
    key: str,
    value: float | int | None,
    *,
    quality: str,
    notes: list[str] | None = None,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    spec = next(item for item in METRIC_SPECS if item["key"] == key)
    missing = value is None or (isinstance(value, float) and math.isnan(value))
    return {
        "key": key,
        "label": spec["label"],
        "unit": spec["unit"],
        "value": None if missing else value,
        "missing": missing,
        "quality": quality,
        "notes": notes or [],
        "summary": summary,
    }


def blank_metrics(quality: str, note: str) -> dict[str, Any]:
    return {spec["key"]: metric(spec["key"], None, quality=quality, notes=[note]) for spec in METRIC_SPECS}


def relative_difference(baseline: float | int | None, scenario: float | int | None) -> dict[str, Any]:
    if baseline is None or scenario is None:
        return {"value": None, "note": "Relative difference is undefined because a value is missing."}
    if baseline == 0:
        return {"value": None, "note": "Relative difference is undefined because the baseline value is zero."}
    return {"value": (float(scenario) - float(baseline)) / abs(float(baseline)), "note": None}


def absolute_difference(baseline: float | int | None, scenario: float | int | None) -> float | None:
    if baseline is None or scenario is None:
        return None
    return float(scenario) - float(baseline)


def summarize(values: list[float | int | None]) -> dict[str, Any]:
    clean = [float(value) for value in values if value is not None and not (isinstance(value, float) and math.isnan(value))]
    if not clean:
        return {"mean": None, "stdev": None, "min": None, "max": None, "n": 0}
    mean = sum(clean) / len(clean)
    stdev = None
    if len(clean) > 1:
        variance = sum((item - mean) ** 2 for item in clean) / (len(clean) - 1)
        stdev = math.sqrt(variance)
    return {"mean": mean, "stdev": stdev, "min": min(clean), "max": max(clean), "n": len(clean)}


def comparison_rows(baseline_metrics: dict[str, Any] | None, scenario_metrics: dict[str, Any] | None) -> list[dict[str, Any]]:
    rows = []
    baseline_metrics = baseline_metrics or {}
    scenario_metrics = scenario_metrics or {}
    for spec in METRIC_SPECS:
        base = baseline_metrics.get(spec["key"]) or {}
        scen = scenario_metrics.get(spec["key"]) or {}
        base_value = base.get("value") if not base.get("missing", True) else None
        scen_value = scen.get("value") if not scen.get("missing", True) else None
        relative = relative_difference(base_value, scen_value)
        rows.append(
            {
                "key": spec["key"],
                "label": spec["label"],
                "unit": spec["unit"],
                "baseline": base_value,
                "scenario": scen_value,
                "absolute_difference": absolute_difference(base_value, scen_value),
                "relative_difference": relative["value"],
                "relative_note": relative["note"],
                "baseline_quality": base.get("quality"),
                "scenario_quality": scen.get("quality"),
                "baseline_missing": base_value is None,
                "scenario_missing": scen_value is None,
            }
        )
    return rows


def assess_comparability(baseline: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    notes: list[str] = []
    comparable = True
    base_cfg = baseline.get("comparability") or {}
    scen_cfg = scenario.get("comparability") or {}
    if baseline.get("status") != "SUCCEEDED" or scenario.get("status") != "SUCCEEDED":
        comparable = False
        notes.append("Both runs must succeed before their results can be treated as comparable.")
    if baseline.get("quality") != scenario.get("quality"):
        comparable = False
        notes.append("Result qualities differ. Estimated values are not interchangeable with SUMO results.")
    if baseline.get("result_usability") == "none" or scenario.get("result_usability") == "none":
        comparable = False
        notes.append("One or both runs do not have usable metrics.")
    labels = {
        "engine": "simulation engines",
        "seed": "random seeds",
        "repeats": "repeat counts",
        "horizon_s": "demand horizons",
        "simulation_end_s": "simulation end times",
        "demand_fingerprint": "demand profiles",
        "transit_fingerprint": "transit services included in the run",
        "study_area_id": "study areas",
    }
    for key in COMPARISON_KEYS:
        if base_cfg.get(key) != scen_cfg.get(key):
            comparable = False
            notes.append(f"The {labels[key]} differ, so the runs are not equivalent.")
    if base_cfg.get("study_area_id") == scen_cfg.get("study_area_id"):
        notes.append(
            "Network versions may differ because one run is a scenario. "
            "That difference is the edit being studied, not a change of study-area coverage."
        )
    notes.append("No statistical significance test was performed.")
    if baseline.get("quality") == "ESTIMATED" or scenario.get("quality") == "ESTIMATED":
        notes.append("Estimated metrics depend on the documented assignment assumptions.")
    return {"comparable": comparable, "notes": notes}
