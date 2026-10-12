"""Review and apply a batch of operations as one transaction.

A failed accepted operation restores the input document. Earlier operations in
that batch are not kept. The caller is responsible for refusing to persist a
baseline document.
"""

from __future__ import annotations

from typing import Any

from app.domain.demand import rebuild_land_use_demand, refresh_transit
from app.domain.network import content_hash, copy_network, validate_network
from app.domain.operations import evaluate, merge_eval


def review_operations(network: dict[str, Any], operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate operations in order.

    Pending and accepted operations are previewed as if applied, so a later
    operation can refer to a feature introduced earlier in the same plan.
    Rejected operations are validated but do not change the preview.
    """
    working = copy_network(network)
    reviewed: list[dict[str, Any]] = []
    for operation in operations:
        decision = operation.get("decision") or "pending"
        ev = evaluate(copy_network(working), operation)
        merged = merge_eval(operation, ev)
        merged["decision"] = decision
        reviewed.append(merged)
        if decision != "rejected" and ev.ok:
            trial = copy_network(working)
            trial_eval = evaluate(trial, operation)
            if trial_eval.ok and not _hard_errors(trial):
                working = trial
    return reviewed


def apply_plan(network: dict[str, Any], operations: list[dict[str, Any]]) -> dict[str, Any]:
    original = copy_network(network)
    working = copy_network(network)
    results: list[dict[str, Any]] = []
    failed = False
    failure_message = None
    for operation in operations:
        decision = operation.get("decision") or "pending"
        if decision != "accepted":
            preview = merge_eval(operation, evaluate(copy_network(working), operation))
            preview["decision"] = decision
            preview["apply_status"] = "rejected" if decision == "rejected" else "not_applied"
            results.append(preview)
            continue
        if failed:
            preview = merge_eval(operation, evaluate(copy_network(original), operation))
            preview["decision"] = "accepted"
            preview["apply_status"] = "skipped"
            results.append(preview)
            continue
        trial = copy_network(working)
        ev = evaluate(trial, operation)
        merged = merge_eval(operation, ev)
        merged["decision"] = "accepted"
        if not ev.ok or _hard_errors(trial):
            failed = True
            if ev.ok:
                for issue in _hard_errors(trial):
                    merged["errors"].append(issue)
                merged["validation_status"] = "invalid"
            merged["apply_status"] = "failed"
            failure_message = merged["errors"][0]["message"] if merged["errors"] else "The operation failed validation."
            results.append(merged)
            continue
        working = trial
        merged["apply_status"] = "applied_in_transaction"
        results.append(merged)
    if failed:
        for item in results:
            if item["apply_status"] == "applied_in_transaction":
                item["apply_status"] = "rolled_back"
                item["warnings"] = [
                    *item["warnings"],
                    {
                        "code": "transaction_aborted",
                        "message": "This operation was not saved because another operation in the same transaction failed.",
                    },
                ]
        return {
            "ok": False,
            "network": original,
            "operations": results,
            "integrity_errors": [],
            "integrity_warnings": [],
            "message": failure_message,
            "network_hash": content_hash(original),
        }
    derived_warnings = refresh_transit(working)
    rebuild_land_use_demand(working)
    issues = validate_network(working)
    hard = [issue for issue in issues if issue["severity"] == "error"]
    soft = [issue for issue in issues if issue["severity"] == "warning"]
    if hard:
        for item in results:
            if item["apply_status"] == "applied_in_transaction":
                item["apply_status"] = "rolled_back"
        return {
            "ok": False,
            "network": original,
            "operations": results,
            "integrity_errors": hard,
            "integrity_warnings": soft,
            "message": hard[0]["message"],
            "network_hash": content_hash(original),
        }
    for item in results:
        if item["apply_status"] == "applied_in_transaction":
            item["apply_status"] = "applied"
    return {
        "ok": True,
        "network": working,
        "operations": results,
        "integrity_errors": [],
        "integrity_warnings": [*soft, *[{"severity": "warning", **item} for item in derived_warnings]],
        "message": "Applied accepted operations.",
        "network_hash": content_hash(working),
    }


def apply_execution_policy(operations: list[dict[str, Any]], policy: str) -> list[dict[str, Any]]:
    """Accept only clean operations when the policy says so.

    Operations with warnings stay pending so material warnings cannot be hidden
    by an automatic policy.
    """
    if policy != "auto_valid":
        return operations
    updated = []
    for operation in operations:
        item = dict(operation)
        if item.get("decision") in {None, "pending"} and validation_status_of(item) == "valid":
            item["decision"] = "accepted"
        updated.append(item)
    return updated


def validation_status_of(operation: dict[str, Any]) -> str:
    if operation.get("errors"):
        if any(error.get("code") == "ambiguous" for error in operation["errors"]):
            return "ambiguous"
        return "invalid"
    if operation.get("warnings"):
        return "valid_with_warnings"
    if operation.get("validation_status") in {"valid", "valid_with_warnings", "invalid", "ambiguous"}:
        return str(operation["validation_status"])
    return "pending"


def _hard_errors(network: dict[str, Any]) -> list[dict[str, Any]]:
    return [issue for issue in validate_network(network) if issue["severity"] == "error"]


def touched_feature_ids(operations: list[dict[str, Any]]) -> set[str]:
    touched: set[str] = set()
    for operation in operations:
        if operation.get("apply_status") == "applied":
            touched.update(operation.get("touched") or [])
    return touched
