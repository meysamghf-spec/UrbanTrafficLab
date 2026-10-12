"""Scenario reports.

HTML is escaped. Synthetic demand and estimated metrics keep those labels.
Warnings are included even when the metric tables are empty.
"""

from __future__ import annotations

import csv
import html
import io
from typing import Any

from app.domain.metrics import METRIC_SPECS, assess_comparability, comparison_rows


def build_report(context: dict[str, Any]) -> dict[str, Any]:
    comparison = None
    if context.get("baseline_run") and context.get("scenario_run"):
        comparison = {
            "comparability": assess_comparability(
                _comparability_input(context["baseline_run"]),
                _comparability_input(context["scenario_run"]),
            ),
            "rows": comparison_rows(
                context["baseline_run"].get("metrics"),
                context["scenario_run"].get("metrics"),
            ),
        }
    warnings = _collect_warnings(context)
    body = {
        "title": context.get("title") or "UrbanTrafficLab scenario report",
        "disclaimer": (
            "Simulation outputs are decision-support results. They are not guaranteed "
            "predictions of real-world traffic. Synthetic demand and estimated metrics "
            "are not measured observations."
        ),
        "study_area": context.get("study_area"),
        "baseline": context.get("baseline"),
        "scenario": context.get("scenario"),
        "operations": context.get("operations") or [],
        "demand": context.get("demand"),
        "baseline_run": _public_run(context.get("baseline_run")),
        "scenario_run": _public_run(context.get("scenario_run")),
        "comparison": comparison,
        "warnings": warnings,
        "provenance": context.get("provenance") or {},
        "limitations": _limitations(context),
        "execution_status": _execution_status(context),
    }
    return {
        "report": body,
        "html": render_html(body),
        "csv": render_csv(body),
    }


def render_html(body: dict[str, Any]) -> str:
    parts = [
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'><title>",
        _esc(body["title"]),
        "</title><style>",
        "body{font-family:Segoe UI,Helvetica,sans-serif;margin:2rem;color:#1c2430;line-height:1.45}",
        "h1,h2{font-weight:650} table{border-collapse:collapse;width:100%;margin:1rem 0}",
        "th,td{border:1px solid #d5dde8;padding:.4rem .55rem;text-align:left;vertical-align:top}",
        "th{background:#f3f6fa}.banner{background:#fff4d6;border:1px solid #e2c36a;padding:.8rem 1rem}",
        ".warn{background:#fff1f0;border:1px solid #f0b4b4;padding:.6rem .8rem;margin:.4rem 0}",
        "</style></head><body>",
        f"<h1>{_esc(body['title'])}</h1>",
        f"<p class='banner'>{_esc(body['disclaimer'])}</p>",
        "<h2>Study area</h2>",
        _definition(body.get("study_area") or {}),
        "<h2>Baseline</h2>",
        _definition(body.get("baseline") or {}),
        "<h2>Scenario</h2>",
        _definition(body.get("scenario") or {}),
        "<h2>Operations</h2>",
        _operations_table(body.get("operations") or []),
        "<h2>Demand</h2>",
        f"<pre>{_esc(json_pretty(body.get('demand')))}</pre>",
        "<h2>Simulation</h2>",
        "<h3>Baseline run</h3>",
        _definition(body.get("baseline_run") or {}),
        "<h3>Scenario run</h3>",
        _definition(body.get("scenario_run") or {}),
        "<h2>Comparison</h2>",
        _comparison_table(body.get("comparison")),
        "<h2>Warnings</h2>",
        _warnings_html(body.get("warnings") or []),
        "<h2>Provenance</h2>",
        f"<pre>{_esc(json_pretty(body.get('provenance')))}</pre>",
        "<h2>Limitations</h2><ul>",
        "".join(f"<li>{_esc(item)}</li>" for item in body.get("limitations") or []),
        "</ul><h2>Execution status</h2>",
        f"<p>{_esc(body.get('execution_status'))}</p>",
        "</body></html>",
    ]
    return "".join(parts)


def render_csv(body: dict[str, Any]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "metric",
            "unit",
            "baseline",
            "scenario",
            "absolute_difference",
            "relative_difference",
            "baseline_quality",
            "scenario_quality",
            "comparable",
            "warnings",
        ]
    )
    comparison = body.get("comparison") or {}
    rows = comparison.get("rows") or []
    comparable = (comparison.get("comparability") or {}).get("comparable")
    warning_text = " | ".join(item["message"] for item in body.get("warnings") or []) or "No warnings recorded."
    if not rows:
        for spec in METRIC_SPECS:
            writer.writerow([spec["key"], spec["unit"], "", "", "", "", "", "", comparable, warning_text])
    for row in rows:
        writer.writerow(
            [
                row["key"],
                row["unit"],
                _csv_value(row["baseline"]),
                _csv_value(row["scenario"]),
                _csv_value(row["absolute_difference"]),
                _csv_value(row["relative_difference"]),
                row.get("baseline_quality"),
                row.get("scenario_quality"),
                comparable,
                warning_text,
            ]
        )
    return buffer.getvalue()


def _collect_warnings(context: dict[str, Any]) -> list[dict[str, str]]:
    warnings = []
    for item in context.get("validation_warnings") or []:
        warnings.append({"code": item.get("code", "validation"), "message": item.get("message", "")})
    for operation in context.get("operations") or []:
        for item in operation.get("warnings") or []:
            warnings.append({"code": item.get("code", "operation"), "message": item.get("message", "")})
    for run_key in ("baseline_run", "scenario_run"):
        run = context.get(run_key) or {}
        for item in run.get("warnings") or []:
            warnings.append({"code": item.get("code", "simulation"), "message": item.get("message", "")})
    if not warnings:
        warnings.append({"code": "none", "message": "No warnings were recorded."})
    return warnings


def _limitations(context: dict[str, Any]) -> list[str]:
    items = [
        "Results depend on demand quality, route choice, and the traffic model.",
        "Road geometry alone is not enough to predict real traffic.",
        "No statistical significance test was performed.",
        "Simplified signals, roundabouts, bridges, tunnels, and transit do not include full physical design.",
    ]
    for run in (context.get("baseline_run"), context.get("scenario_run")):
        if run and run.get("quality") == "ESTIMATED":
            items.append("At least one run is an analytical estimate, not a SUMO execution.")
            break
    return items


def _execution_status(context: dict[str, Any]) -> str:
    runs = [context.get("baseline_run"), context.get("scenario_run")]
    states = [run.get("status") for run in runs if run]
    if not states:
        return "No simulation was run for this report."
    return "Run status: " + ", ".join(str(state) for state in states)


def _public_run(run: dict[str, Any] | None) -> dict[str, Any] | None:
    if not run:
        return None
    return {
        "id": run.get("id"),
        "status": run.get("status"),
        "engine": run.get("engine"),
        "quality": run.get("quality"),
        "result_usability": run.get("result_usability"),
        "seed": run.get("seed"),
        "demand_profile_id": run.get("demand_profile_id"),
        "simulator_version": (run.get("provenance") or {}).get("simulator_version"),
        "error": run.get("error"),
    }


def _comparability_input(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": run.get("status"),
        "quality": run.get("quality"),
        "result_usability": run.get("result_usability"),
        "comparability": (run.get("config") or {}).get("comparability") or {},
    }


def _operations_table(operations: list[dict[str, Any]]) -> str:
    if not operations:
        return "<p>No operations were recorded.</p>"
    rows = ["<table><tr><th>ID</th><th>Type</th><th>Status</th><th>Effects</th><th>Warnings</th></tr>"]
    for operation in operations:
        effects = "; ".join(operation.get("expected_effects") or [])
        warnings = "; ".join(item.get("message", "") for item in operation.get("warnings") or [])
        rows.append(
            "<tr><td>"
            + _esc(operation.get("id"))
            + "</td><td>"
            + _esc(operation.get("operation_type"))
            + "</td><td>"
            + _esc(operation.get("apply_status") or operation.get("validation_status"))
            + "</td><td>"
            + _esc(effects)
            + "</td><td>"
            + _esc(warnings)
            + "</td></tr>"
        )
    rows.append("</table>")
    return "".join(rows)


def _comparison_table(comparison: dict[str, Any] | None) -> str:
    if not comparison:
        return "<p>No comparison was available.</p>"
    notes = "".join(f"<li>{_esc(note)}</li>" for note in comparison["comparability"]["notes"])
    header = (
        "<p>Comparable: "
        + _esc(comparison["comparability"]["comparable"])
        + "</p><ul>"
        + notes
        + "</ul><table><tr><th>Metric</th><th>Unit</th><th>Baseline</th><th>Scenario</th>"
        + "<th>Absolute difference</th><th>Relative difference</th><th>Baseline quality</th><th>Scenario quality</th></tr>"
    )
    rows = [header]
    for row in comparison["rows"]:
        rows.append(
            "<tr><td>"
            + _esc(row["label"])
            + "</td><td>"
            + _esc(row["unit"])
            + "</td><td>"
            + _esc(_csv_value(row["baseline"]))
            + "</td><td>"
            + _esc(_csv_value(row["scenario"]))
            + "</td><td>"
            + _esc(_csv_value(row["absolute_difference"]))
            + "</td><td>"
            + _esc(_csv_value(row["relative_difference"]))
            + "</td><td>"
            + _esc(row.get("baseline_quality"))
            + "</td><td>"
            + _esc(row.get("scenario_quality"))
            + "</td></tr>"
        )
    rows.append("</table>")
    return "".join(rows)


def _warnings_html(warnings: list[dict[str, str]]) -> str:
    return "".join(f"<div class='warn'><strong>{_esc(item['code'])}</strong> {_esc(item['message'])}</div>" for item in warnings)


def _definition(data: dict[str, Any]) -> str:
    if not data:
        return "<p>Not available.</p>"
    rows = ["<table>"]
    for key, value in data.items():
        if isinstance(value, dict | list):
            rendered = json_pretty(value)
        else:
            rendered = "" if value is None else str(value)
        rows.append(f"<tr><th>{_esc(key)}</th><td>{_esc(rendered)}</td></tr>")
    rows.append("</table>")
    return "".join(rows)


def json_pretty(value: Any) -> str:
    import json

    return json.dumps(value, indent=2, sort_keys=True, default=str)


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value)
