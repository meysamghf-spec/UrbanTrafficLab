"""Study, scenario, simulation, and report workflows."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.domain.demand import demand_fingerprint, select_profiles, transit_fingerprint
from app.domain.engine import (
    apply_execution_policy,
    apply_plan,
    review_operations,
    touched_feature_ids,
)
from app.domain.estimator import demand_vehicle_count, estimate
from app.domain.geo import bbox_of_nodes as _bbox_of_nodes
from app.domain.importers import ImportError_, export_geojson, import_geojson
from app.domain.llm import (
    ProviderError,
    llm_is_configured,
    network_summary_for_model,
    propose_operations,
)
from app.domain.metrics import (
    METRIC_SPECS,
    assess_comparability,
    comparison_rows,
    metric,
    summarize,
)
from app.domain.network import (
    content_hash,
    copy_network,
    feature_detail,
    network_stats,
    to_geojson,
    validate_network,
)
from app.domain.osm import OverpassError, fetch_osm_network
from app.domain.parser import parse_rules
from app.domain.redact import redact
from app.domain.report import build_report
from app.domain.sample import SAMPLE_ATTRIBUTION, SAMPLE_NAME, SAMPLE_SLUG, build_sample_network
from app.domain.sumo import detect_sumo, public_capability, run_sumo
from app.models.entities import (
    NetworkVersion,
    ReportArtifact,
    Scenario,
    ScenarioEvent,
    ScenarioOperation,
    SimulationRun,
    StudyArea,
)
from app.workers.jobs import REGISTRY, schedule


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid.uuid4())


def capabilities() -> dict[str, Any]:
    settings = get_settings()
    sumo = detect_sumo(settings.sumo_binary, settings.sumo_home)
    dialect = settings.database_url.split(":", 1)[0]
    return {
        "sumo": public_capability(sumo),
        "estimator": {
            "available": True,
            "label": "ESTIMATED",
            "description": (
                "Static traffic assignment. Outputs are estimates, not SUMO results "
                "and not observed traffic."
            ),
        },
        "ai": {
            "rule_based": True,
            "llm_configured": settings.llm_configured,
            "provider": settings.llm_provider if settings.llm_configured else "none",
        },
        "database": {"dialect": "sqlite" if dialect.startswith("sqlite") else dialect},
        "map": {
            "tile_url_configured": bool(settings.map_tile_url),
            "note": "Basemap tiles are optional. The road network draws without them.",
        },
        "auth": {
            "enabled": False,
            "note": "This deployment has no user authentication. Do not expose it as a multi-user service without adding one.",
        },
    }


def bootstrap_sample(db: Session) -> dict[str, Any]:
    existing = db.scalar(select(StudyArea).where(StudyArea.slug == SAMPLE_SLUG))
    if existing is not None:
        return present_study(db, existing)
    study = StudyArea(
        id=new_id(),
        slug=SAMPLE_SLUG,
        name=SAMPLE_NAME,
        description=(
            "A small synthetic grid used to demonstrate editing, simulation, and comparison. "
            "It is not a real city."
        ),
        kind="synthetic",
        bbox_json=_bbox_of_nodes(build_sample_network()["nodes"]),
        attribution=SAMPLE_ATTRIBUTION,
        created_at=utcnow(),
    )
    document = build_sample_network()
    version = _new_version(
        study,
        document,
        label="Synthetic baseline",
        role="baseline",
        immutable=True,
        parent_id=None,
        provenance={
            "source": "synthetic_sample",
            "attribution": SAMPLE_ATTRIBUTION,
            "generator": "urbantrafficlab.sample.v1",
            "retrieved_at": None,
            "real_city": False,
        },
    )
    db.add(study)
    db.flush()
    db.add(version)
    db.commit()
    return present_study(db, study)


def create_study(db: Session, name: str, description: str) -> dict[str, Any]:
    study = StudyArea(
        id=new_id(),
        slug=_unique_slug(db, name),
        name=name.strip(),
        description=description,
        kind="custom",
        bbox_json=None,
        attribution="User-created study. No road network has been imported yet.",
        created_at=utcnow(),
    )
    db.add(study)
    db.commit()
    return present_study(db, study)


def list_studies(db: Session, limit: int, offset: int) -> dict[str, Any]:
    return _page(db, select(StudyArea).order_by(StudyArea.created_at.desc()), limit, offset, lambda row: present_study(db, row))


def get_study(db: Session, study_id: str) -> dict[str, Any]:
    return present_study(db, _study(db, study_id))


def import_network_geojson(db: Session, study_id: str, document: dict[str, Any]) -> dict[str, Any]:
    study = _study(db, study_id)
    _reject_if_baseline_exists(db, study.id)
    try:
        imported = import_geojson(document, max_features=get_settings().max_features)
    except ImportError_ as exc:
        raise AppError(str(exc), code=exc.code) from exc
    version = _new_version(
        study,
        imported["network"],
        label="Imported GeoJSON baseline",
        role="baseline",
        immutable=True,
        parent_id=None,
        provenance={
            "source": "geojson_import",
            "attribution": "Imported by the user as GeoJSON. Source license depends on the file.",
            "warnings": imported["warnings"],
            "real_city": False,
        },
    )
    study.kind = "geojson"
    study.bbox_json = _bbox_of_nodes(imported["network"]["nodes"])
    study.attribution = version.provenance_json["attribution"]
    db.add(version)
    db.commit()
    return {"study": present_study(db, study), "network": present_network(version, include_document=False), "warnings": imported["warnings"]}


def import_network_osm(db: Session, study_id: str, bbox: list[float], client: Any) -> dict[str, Any]:
    study = _study(db, study_id)
    _reject_if_baseline_exists(db, study.id)
    settings = get_settings()
    try:
        fetched = fetch_osm_network(
            bbox,
            client=client,
            overpass_url=settings.osm_overpass_url,
            timeout_s=settings.osm_timeout_s,
            max_retries=settings.osm_max_retries,
            max_span=settings.osm_max_span_degrees,
            cache_dir=Path(settings.cache_dir) / "osm",
        )
    except OverpassError as exc:
        raise AppError(str(exc), code=exc.code) from exc
    version = _new_version(
        study,
        fetched["network"],
        label="OpenStreetMap baseline",
        role="baseline",
        immutable=True,
        parent_id=None,
        provenance={
            "source": "openstreetmap",
            "attribution": fetched["attribution"],
            "retrieval": fetched["source"],
            "cache_hit": fetched["cache_hit"],
            "retrieved_at": utcnow().isoformat(),
            "warnings": fetched["warnings"],
            "real_city": True,
        },
    )
    study.kind = "osm"
    study.bbox_json = bbox
    study.attribution = fetched["attribution"]
    db.add(version)
    db.commit()
    return {
        "study": present_study(db, study),
        "network": present_network(version, include_document=False),
        "warnings": fetched["warnings"],
        "cache_hit": fetched["cache_hit"],
    }


def list_networks(db: Session, study_id: str, limit: int, offset: int) -> dict[str, Any]:
    _study(db, study_id)
    stmt = (
        select(NetworkVersion)
        .where(NetworkVersion.study_area_id == study_id)
        .order_by(NetworkVersion.created_at.desc())
    )
    return _page(db, stmt, limit, offset, lambda row: present_network(row, include_document=False))


def get_network(db: Session, version_id: str, include_document: bool = True) -> dict[str, Any]:
    return present_network(_version(db, version_id), include_document=include_document)


def network_feature(db: Session, version_id: str, feature_id: str) -> dict[str, Any]:
    version = _version(db, version_id)
    detail = feature_detail(copy_network(version.document_json), feature_id)
    if detail is None:
        raise AppError(f"Feature '{feature_id}' was not found.", status_code=404, code="not_found")
    return detail


def validate_version(db: Session, version_id: str) -> dict[str, Any]:
    version = _version(db, version_id)
    issues = validate_network(copy_network(version.document_json))
    error_count = sum(1 for issue in issues if issue["severity"] == "error")
    return {
        "network_version_id": version.id,
        "content_hash": version.content_hash,
        "ok": error_count == 0,
        "issues": issues,
        "error_count": error_count,
        "warning_count": sum(1 for issue in issues if issue["severity"] == "warning"),
    }


def map_layers(
    db: Session,
    network_version_id: str,
    run_id: str | None = None,
    scenario_id: str | None = None,
    timestep: float | None = None,
) -> dict[str, Any]:
    version = _version(db, network_version_id)
    network = copy_network(version.document_json)
    link_metrics: dict[str, dict[str, Any]] = {}
    quality = None
    data_note = "Traffic results are unavailable. Roads are shown without a congestion scale."
    if run_id:
        run = _run(db, run_id)
        quality = run.quality
        frames = run.timesteps_json or []
        if timestep is not None and frames:
            frame = min(frames, key=lambda item: abs(float(item["t_s"]) - float(timestep)))
            for edge_id, values in frame.get("edges", {}).items():
                link_metrics[edge_id] = values
            data_note = f"Showing simulation time {frame['t_s']} s."
        else:
            for item in run.link_metrics_json or []:
                link_metrics[item["edge_id"]] = item
        if quality == "ESTIMATED":
            data_note = "Link colors use ESTIMATED speeds. They are not SUMO results and not observed speeds."
        elif quality == "SIMULATED" and not any(item.get("speed_kmh") is not None for item in link_metrics.values()):
            data_note = "This SUMO run has no link-level speed to display."
        elif quality == "NONE":
            data_note = "This run has no usable traffic results."
        elif quality == "SIMULATED":
            data_note = "Link colors use speeds parsed from SUMO edge data."
    changed: set[str] = set()
    if scenario_id:
        scenario = _scenario(db, scenario_id)
        operations = _operations(db, scenario.id)
        changed = touched_feature_ids([row.payload_json | {"apply_status": row.apply_status} for row in operations])
    layers = to_geojson(network, link_metrics, changed)
    study = _study(db, version.study_area_id)
    return {
        **layers,
        "attribution": study.attribution,
        "data_note": data_note,
        "quality": quality,
        "legend": _legend(quality),
        "synthetic": study.kind == "synthetic",
    }


def parse_prompt(db: Session, network_version_id: str, prompt: str, parser: str) -> dict[str, Any]:
    version = _version(db, network_version_id)
    network = copy_network(version.document_json)
    parsed = _parse(network, prompt, parser)
    reviewed = review_operations(network, parsed["operations"])
    return {
        "parser": parsed["parser"],
        "llm_attempted": parsed["llm_attempted"],
        "llm_error": parsed["llm_error"],
        "prompt_sha256": parsed["prompt_sha256"],
        "notes": parsed["notes"],
        "unparsed_clauses": parsed["unparsed_clauses"],
        "operations": reviewed,
    }


def review(db: Session, network_version_id: str, operations: list[dict[str, Any]]) -> dict[str, Any]:
    version = _version(db, network_version_id)
    return {"operations": review_operations(copy_network(version.document_json), operations)}


def create_scenario(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    study = _study(db, payload["study_area_id"])
    baseline = _baseline_for(db, study.id, payload.get("baseline_version_id"))
    operations = payload.get("operations")
    if operations is None:
        scenario = _insert_scenario(
            db,
            study=study,
            baseline=baseline,
            name=payload["name"],
            description=payload.get("description") or "",
            prompt=payload.get("prompt") or "",
            network_version_id=baseline.id,
            status="draft",
            validation={"issues": [], "message": "Draft scenario. No operations have been applied."},
            history=[baseline.id],
        )
        _event(db, scenario, "created", {"name": scenario.name})
        db.commit()
        return present_scenario(db, scenario)
    return _apply_to_new_or_existing(
        db,
        study=study,
        baseline=baseline,
        scenario=None,
        name=payload["name"],
        description=payload.get("description") or "",
        prompt=payload.get("prompt") or "",
        operations=operations,
        policy=payload.get("execution_policy") or "review",
    )


def apply_to_scenario(db: Session, scenario_id: str, operations: list[dict[str, Any]], policy: str, prompt: str) -> dict[str, Any]:
    scenario = _scenario(db, scenario_id)
    study = _study(db, scenario.study_area_id)
    baseline = _version(db, scenario.baseline_version_id)
    if baseline.study_area_id != study.id:
        raise AppError("Baseline does not belong to this study.", code="invalid_reference")
    return _apply_to_new_or_existing(
        db,
        study=study,
        baseline=baseline,
        scenario=scenario,
        name=scenario.name,
        description=scenario.description,
        prompt=prompt or scenario.prompt,
        operations=operations,
        policy=policy,
    )


def rollback_scenario(db: Session, scenario_id: str) -> dict[str, Any]:
    scenario = _scenario(db, scenario_id)
    history = list(scenario.version_history_json or [])
    if len(history) <= 1:
        raise AppError("The scenario is already at the baseline.", status_code=409, code="nothing_to_rollback")
    removed = history.pop()
    scenario.version_history_json = history
    scenario.network_version_id = history[-1]
    scenario.status = "rolled_back"
    scenario.updated_at = utcnow()
    _event(db, scenario, "rollback", {"removed_version_id": removed, "restored_version_id": history[-1]})
    db.commit()
    return present_scenario(db, scenario)


def clone_scenario(db: Session, scenario_id: str, name: str | None = None) -> dict[str, Any]:
    source = _scenario(db, scenario_id)
    current = _version(db, source.network_version_id)
    clone = _insert_scenario(
        db,
        study=_study(db, source.study_area_id),
        baseline=_version(db, source.baseline_version_id),
        name=name or f"{source.name} copy",
        description=source.description,
        prompt=source.prompt,
        network_version_id=source.network_version_id,
        status=source.status,
        validation=dict(source.validation_json or {}),
        history=list(source.version_history_json or []),
    )
    if current.id != source.baseline_version_id:
        copied = _new_version(
            _study(db, source.study_area_id),
            copy_network(current.document_json),
            label=f"{clone.name} network",
            role="scenario",
            immutable=False,
            parent_id=source.baseline_version_id,
            provenance={
                "source": "scenario_clone",
                "cloned_from_version": current.id,
                "parent_scenario": source.id,
            },
        )
        db.add(copied)
        clone.network_version_id = copied.id
        clone.version_history_json = [source.baseline_version_id, copied.id]
    for row in _operations(db, source.id):
        db.add(
            ScenarioOperation(
                id=new_id(),
                scenario_id=clone.id,
                sequence=row.sequence,
                operation_type=row.operation_type,
                payload_json=copy_network(row.payload_json) if isinstance(row.payload_json, dict) else row.payload_json,
                decision=row.decision,
                validation_status=row.validation_status,
                apply_status=row.apply_status,
                created_at=utcnow(),
            )
        )
    _event(db, clone, "cloned", {"source_scenario_id": source.id})
    db.commit()
    return present_scenario(db, clone)


def list_scenarios(db: Session, study_area_id: str | None, limit: int, offset: int) -> dict[str, Any]:
    stmt = select(Scenario).order_by(Scenario.created_at.desc())
    if study_area_id:
        _study(db, study_area_id)
        stmt = stmt.where(Scenario.study_area_id == study_area_id)
    return _page(db, stmt, limit, offset, lambda row: present_scenario(db, row, include_operations=False))


def get_scenario(db: Session, scenario_id: str) -> dict[str, Any]:
    return present_scenario(db, _scenario(db, scenario_id))


def scenario_history(db: Session, scenario_id: str) -> dict[str, Any]:
    scenario = _scenario(db, scenario_id)
    events = db.scalars(
        select(ScenarioEvent).where(ScenarioEvent.scenario_id == scenario.id).order_by(ScenarioEvent.created_at.asc())
    ).all()
    return {
        "scenario_id": scenario.id,
        "events": [
            {
                "id": event.id,
                "kind": event.kind,
                "detail": event.detail_json,
                "created_at": _iso(event.created_at),
            }
            for event in events
        ],
    }


def start_simulation(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    version = _version(db, payload["network_version_id"])
    scenario = _scenario(db, payload["scenario_id"]) if payload.get("scenario_id") else None
    if scenario and scenario.study_area_id != version.study_area_id:
        raise AppError("Scenario and network belong to different studies.", code="invalid_reference")
    network = copy_network(version.document_json)
    try:
        profiles = select_profiles(network, payload["demand_profile_id"], payload.get("include_generated_demand", True))
    except KeyError as exc:
        raise AppError(f"Demand profile '{payload['demand_profile_id']}' was not found.", code="not_found", status_code=404) from exc
    vehicle_count = demand_vehicle_count(network, profiles)
    if vehicle_count > settings.max_vehicles:
        raise AppError(
            f"Demand has {vehicle_count} vehicle trips, above the limit of {settings.max_vehicles}.",
            code="demand_too_large",
        )
    hard = [issue for issue in validate_network(network) if issue["severity"] == "error"]
    engine = payload["engine"]
    seed = int(payload.get("seed", 42))
    repeats = int(payload.get("repeats", 1))
    horizon = int(profiles[0]["horizon_s"])
    simulation_end = horizon + settings.simulation_end_buffer_s if engine == "sumo" else horizon
    comparability = {
        "engine": engine,
        "seed": seed,
        "repeats": repeats,
        "horizon_s": horizon,
        "simulation_end_s": simulation_end,
        "demand_fingerprint": demand_fingerprint(profiles),
        "transit_fingerprint": transit_fingerprint(network),
        "study_area_id": version.study_area_id,
    }
    assumptions: list[str] = []
    for profile in profiles:
        assumptions.extend(profile.get("assumptions") or [])
        if profile.get("synthetic"):
            assumptions.append(f"Profile {profile['id']} is synthetic demand, not an observed count.")
    run = SimulationRun(
        id=new_id(),
        study_area_id=version.study_area_id,
        scenario_id=None if scenario is None else scenario.id,
        network_version_id=version.id,
        engine=engine,
        status="QUEUED",
        seed=seed,
        repeats=repeats,
        demand_profile_id=payload["demand_profile_id"],
        config_json={
            "comparability": comparability,
            "include_generated_demand": payload.get("include_generated_demand", True),
            "vehicle_count": vehicle_count,
            "assumptions": assumptions,
            "network_hash": version.content_hash,
        },
        metrics_json=None,
        link_metrics_json=None,
        timesteps_json=None,
        warnings_json=[],
        log_text="",
        error=None,
        exit_code=None,
        result_usability="none",
        quality="NONE",
        provenance_json={
            "engine": engine,
            "seed": seed,
            "repeats": repeats,
            "network_hash": version.content_hash,
            "demand_profile_id": payload["demand_profile_id"],
            "demand_synthetic": all(profile.get("synthetic", True) for profile in profiles),
            "demand_observed": any(profile.get("observed", False) for profile in profiles),
            "assumptions": assumptions,
            "simulator_version": None,
        },
        started_at=None,
        finished_at=None,
        duration_s=None,
        created_at=utcnow(),
    )
    if hard:
        run.status = "FAILED"
        run.error = hard[0]["message"]
        run.warnings_json = hard
        run.result_usability = "none"
        run.finished_at = utcnow()
    elif engine == "sumo":
        cap = detect_sumo(settings.sumo_binary, settings.sumo_home)
        if not cap.available:
            run.status = "BLOCKED_MISSING_DEPENDENCY"
            run.error = cap.reason
            run.warnings_json = [{"code": "missing_sumo", "message": cap.reason or "SUMO is not available."}]
            run.provenance_json = {**run.provenance_json, "estimate_available": True}
            run.finished_at = utcnow()
    db.add(run)
    db.commit()
    if run.status == "QUEUED":
        REGISTRY.reset(run.id)
        if settings.sync_jobs:
            execute_run(run.id, session=db)
        else:
            schedule(execute_run, run.id)
        db.refresh(run)
    return present_run(run)


def execute_run(run_id: str, session: Session | None = None) -> None:
    from app.db.session import get_session_factory

    own_session = session is None
    db = session or get_session_factory()()
    try:
        run = db.get(SimulationRun, run_id)
        if run is None or run.status != "QUEUED":
            return
        if REGISTRY.cancelled(run_id):
            _finish_cancelled(run)
            db.commit()
            return
        run.status = "PREPARING"
        run.started_at = utcnow()
        db.commit()
        version = db.get(NetworkVersion, run.network_version_id)
        assert version is not None
        network = copy_network(version.document_json)
        profiles = select_profiles(
            network,
            run.demand_profile_id,
            bool(run.config_json.get("include_generated_demand", True)),
        )
        settings = get_settings()
        if run.engine == "estimator":
            run.status = "RUNNING"
            db.commit()
            result = estimate(network, profiles, run.seed, run.repeats)
            _store_success(run, result, simulator_version=None)
        else:
            run.status = "VALIDATING"
            db.commit()
            issues = [issue for issue in validate_network(network) if issue["severity"] == "error"]
            if issues:
                run.status = "FAILED"
                run.error = issues[0]["message"]
                run.warnings_json = issues
                run.finished_at = utcnow()
                db.commit()
                return
            run.status = "RUNNING"
            db.commit()
            result = _run_sumo_repeats(run, network, profiles, settings)
            if result["status"] != "SUCCEEDED":
                _store_terminal(run, result)
            else:
                _store_success(run, result, simulator_version=result.get("version"))
        db.commit()
    finally:
        if own_session:
            db.close()


def cancel_simulation(db: Session, run_id: str) -> dict[str, Any]:
    run = _run(db, run_id)
    if run.status in {"SUCCEEDED", "FAILED", "CANCELLED", "BLOCKED_MISSING_DEPENDENCY"}:
        raise AppError("The run is already finished.", status_code=409, code="already_finished")
    REGISTRY.cancel(run.id)
    if run.status == "QUEUED":
        _finish_cancelled(run)
        db.commit()
    return present_run(run)


def get_simulation(db: Session, run_id: str) -> dict[str, Any]:
    return present_run(_run(db, run_id))


def simulation_metrics(db: Session, run_id: str) -> dict[str, Any]:
    run = _run(db, run_id)
    return {
        "run_id": run.id,
        "status": run.status,
        "quality": run.quality,
        "result_usability": run.result_usability,
        "metrics": run.metrics_json,
        "link_metrics": run.link_metrics_json or [],
        "warnings": run.warnings_json or [],
    }


def simulation_logs(db: Session, run_id: str) -> dict[str, Any]:
    run = _run(db, run_id)
    log = redact(run.log_text or "", [get_settings().llm_api_key])
    return {"run_id": run.id, "log": log[-50_000:], "truncated": len(run.log_text or "") > 50_000}


def compare_runs(db: Session, baseline_run_id: str, scenario_run_id: str) -> dict[str, Any]:
    baseline = _run(db, baseline_run_id)
    scenario = _run(db, scenario_run_id)
    comparability = assess_comparability(_run_compare_view(baseline), _run_compare_view(scenario))
    return {
        "baseline_run": present_run(baseline),
        "scenario_run": present_run(scenario),
        "comparability": comparability,
        "rows": comparison_rows(baseline.metrics_json, scenario.metrics_json),
        "limitations": [
            "No statistical significance test was performed.",
            "A difference is not a prediction of real-world traffic.",
        ],
    }


def create_report(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    study = _study(db, payload["study_area_id"])
    scenario = _scenario(db, payload["scenario_id"]) if payload.get("scenario_id") else None
    baseline_run = _run(db, payload["baseline_run_id"]) if payload.get("baseline_run_id") else None
    scenario_run = _run(db, payload["scenario_run_id"]) if payload.get("scenario_run_id") else None
    if scenario is None and baseline_run is None and scenario_run is None:
        raise AppError("A report needs a scenario or at least one simulation run.", code="nothing_to_report")
    baseline_version = None
    if scenario is not None:
        baseline_version = _version(db, scenario.baseline_version_id)
        network_for_geo = _version(db, scenario.network_version_id)
    elif baseline_run is not None:
        network_for_geo = _version(db, baseline_run.network_version_id)
        baseline_version = network_for_geo if network_for_geo.role == "baseline" else None
    else:
        network_for_geo = _version(db, scenario_run.network_version_id) if scenario_run else None
    operations = []
    validation_warnings = []
    if scenario is not None:
        operations = [row.payload_json | {"apply_status": row.apply_status} for row in _operations(db, scenario.id)]
        validation_warnings = (scenario.validation_json or {}).get("integrity_warnings") or []
    context = {
        "title": payload.get("title") or "UrbanTrafficLab scenario report",
        "study_area": {"id": study.id, "name": study.name, "kind": study.kind, "attribution": study.attribution},
        "baseline": None
        if baseline_version is None
        else {
            "id": baseline_version.id,
            "label": baseline_version.label,
            "content_hash": baseline_version.content_hash,
            "immutable": baseline_version.immutable,
            "provenance": baseline_version.provenance_json,
        },
        "scenario": None
        if scenario is None
        else {
            "id": scenario.id,
            "name": scenario.name,
            "description": scenario.description,
            "prompt": scenario.prompt,
            "status": scenario.status,
            "network_hash": _version(db, scenario.network_version_id).content_hash,
        },
        "operations": operations,
        "validation_warnings": validation_warnings,
        "demand": _demand_context(baseline_run, scenario_run),
        "baseline_run": None if baseline_run is None else _report_run(baseline_run),
        "scenario_run": None if scenario_run is None else _report_run(scenario_run),
        "provenance": {
            "generated_at": utcnow().isoformat(),
            "generator": "urbantrafficlab.report.v1",
            "study_area_id": study.id,
            "scenario_id": None if scenario is None else scenario.id,
        },
    }
    rendered = build_report(context)
    artifact = ReportArtifact(
        id=new_id(),
        study_area_id=study.id,
        scenario_id=None if scenario is None else scenario.id,
        baseline_run_id=None if baseline_run is None else baseline_run.id,
        scenario_run_id=None if scenario_run is None else scenario_run.id,
        title=context["title"],
        html=rendered["html"],
        report_json=rendered["report"],
        csv_text=rendered["csv"],
        geojson=None if network_for_geo is None else export_geojson(copy_network(network_for_geo.document_json)),
        provenance_json=context["provenance"],
        created_at=utcnow(),
    )
    db.add(artifact)
    db.commit()
    return {"id": artifact.id, "title": artifact.title, "created_at": _iso(artifact.created_at)}


def get_report(db: Session, report_id: str) -> ReportArtifact:
    artifact = db.get(ReportArtifact, report_id)
    if artifact is None:
        raise AppError("Report not found.", status_code=404, code="not_found")
    return artifact


def refuse_immutable_update(db: Session, version_id: str) -> None:
    """Used by tests and any future edit path. Baselines cannot be overwritten."""
    version = _version(db, version_id)
    if version.immutable:
        raise AppError("Baseline network versions are immutable.", status_code=409, code="immutable_baseline")
    raise AppError("Network versions are replaced by new rows, not updated.", status_code=409, code="versioned_network")


def present_study(db: Session, study: StudyArea) -> dict[str, Any]:
    baseline = _find_baseline(db, study.id)
    return {
        "id": study.id,
        "slug": study.slug,
        "name": study.name,
        "description": study.description,
        "kind": study.kind,
        "bbox": study.bbox_json,
        "attribution": study.attribution,
        "baseline_network_version_id": None if baseline is None else baseline.id,
        "created_at": _iso(study.created_at),
        "real_city": study.kind == "osm",
    }


def present_network(version: NetworkVersion, include_document: bool) -> dict[str, Any]:
    document = copy_network(version.document_json)
    payload = {
        "id": version.id,
        "study_area_id": version.study_area_id,
        "label": version.label,
        "role": version.role,
        "parent_id": version.parent_id,
        "immutable": version.immutable,
        "content_hash": version.content_hash,
        "provenance": version.provenance_json,
        "stats": network_stats(document),
        "bbox": _bbox_of_nodes(document["nodes"]),
        "created_at": _iso(version.created_at),
    }
    if include_document:
        payload["document"] = document
    return payload


def present_scenario(db: Session, scenario: Scenario, include_operations: bool = True) -> dict[str, Any]:
    baseline = _version(db, scenario.baseline_version_id)
    current = _version(db, scenario.network_version_id)
    payload: dict[str, Any] = {
        "id": scenario.id,
        "study_area_id": scenario.study_area_id,
        "name": scenario.name,
        "description": scenario.description,
        "prompt": scenario.prompt,
        "baseline_version_id": scenario.baseline_version_id,
        "network_version_id": scenario.network_version_id,
        "baseline_hash": baseline.content_hash,
        "network_hash": current.content_hash,
        "status": scenario.status,
        "version_history": list(scenario.version_history_json or []),
        "validation": scenario.validation_json,
        "created_at": _iso(scenario.created_at),
        "updated_at": _iso(scenario.updated_at),
    }
    if include_operations:
        payload["operations"] = [
            row.payload_json | {"apply_status": row.apply_status, "decision": row.decision}
            for row in _operations(db, scenario.id)
        ]
    return payload


def present_run(run: SimulationRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "study_area_id": run.study_area_id,
        "scenario_id": run.scenario_id,
        "network_version_id": run.network_version_id,
        "engine": run.engine,
        "status": run.status,
        "quality": run.quality,
        "result_usability": run.result_usability,
        "seed": run.seed,
        "repeats": run.repeats,
        "demand_profile_id": run.demand_profile_id,
        "metrics": run.metrics_json,
        "link_metrics": run.link_metrics_json or [],
        "timesteps": run.timesteps_json or [],
        "warnings": run.warnings_json or [],
        "error": run.error,
        "exit_code": run.exit_code,
        "blocked_reason": run.error if run.status == "BLOCKED_MISSING_DEPENDENCY" else None,
        "estimate_available": True,
        "provenance": run.provenance_json,
        "config": {
            "comparability": (run.config_json or {}).get("comparability"),
            "vehicle_count": (run.config_json or {}).get("vehicle_count"),
            "assumptions": (run.config_json or {}).get("assumptions") or [],
            "network_hash": (run.config_json or {}).get("network_hash"),
        },
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "duration_s": run.duration_s,
        "created_at": _iso(run.created_at),
        "playback_available": bool(run.timesteps_json),
    }


def _apply_to_new_or_existing(
    db: Session,
    *,
    study: StudyArea,
    baseline: NetworkVersion,
    scenario: Scenario | None,
    name: str,
    description: str,
    prompt: str,
    operations: list[dict[str, Any]],
    policy: str,
) -> dict[str, Any]:
    network = copy_network(baseline.document_json)
    reviewed = apply_execution_policy(review_operations(network, operations), policy)
    if not any(item.get("decision") == "accepted" for item in reviewed):
        raise AppError(
            "Accept at least one operation before applying it. Warnings stay visible until you accept them.",
            status_code=422,
            code="nothing_to_apply",
        )
    plan = apply_plan(network, reviewed)
    created = scenario is None
    if scenario is None:
        scenario = _insert_scenario(
            db,
            study=study,
            baseline=baseline,
            name=name,
            description=description,
            prompt=prompt,
            network_version_id=baseline.id,
            status="draft",
            validation={},
            history=[baseline.id],
        )
    else:
        scenario.prompt = prompt
        scenario.updated_at = utcnow()
        for row in _operations(db, scenario.id):
            db.delete(row)
    for index, operation in enumerate(plan["operations"]):
        db.add(
            ScenarioOperation(
                id=new_id(),
                scenario_id=scenario.id,
                sequence=index,
                operation_type=str(operation.get("operation_type")),
                payload_json=operation,
                decision=str(operation.get("decision") or "pending"),
                validation_status=str(operation.get("validation_status") or "pending"),
                apply_status=str(operation.get("apply_status") or "not_applied"),
                created_at=utcnow(),
            )
        )
    scenario.validation_json = {
        "ok": plan["ok"],
        "message": plan["message"],
        "integrity_errors": plan["integrity_errors"],
        "integrity_warnings": plan["integrity_warnings"],
    }
    if plan["ok"]:
        version = _new_version(
            study,
            plan["network"],
            label=f"{scenario.name} network",
            role="scenario",
            immutable=False,
            parent_id=baseline.id,
            provenance={
                "source": "scenario_apply",
                "baseline_id": baseline.id,
                "baseline_hash": baseline.content_hash,
                "operation_ids": [item.get("id") for item in plan["operations"] if item.get("apply_status") == "applied"],
            },
        )
        db.add(version)
        scenario.network_version_id = version.id
        scenario.status = "applied"
        scenario.version_history_json = [baseline.id, version.id]
    else:
        scenario.network_version_id = baseline.id
        scenario.status = "failed"
        scenario.version_history_json = [baseline.id]
    scenario.updated_at = utcnow()
    _event(db, scenario, "applied" if plan["ok"] else "apply_failed", {"ok": plan["ok"], "message": plan["message"], "created": created})
    db.commit()
    return present_scenario(db, scenario)


def _parse(network: dict[str, Any], prompt: str, parser: str) -> dict[str, Any]:
    settings = get_settings()
    if len(prompt) > settings.max_prompt_chars:
        raise AppError("Prompt exceeds the configured length limit.", code="prompt_too_large")
    notes: list[str] = []
    llm_error = None
    llm_attempted = False
    operations = None
    unparsed: list[str] = []
    prompt_sha256 = ""
    parser_name = "rule_based"
    use_llm = parser == "llm" or (parser == "auto" and settings.llm_configured)
    if parser == "llm" and not settings.llm_configured:
        raise AppError(
            "The language-model parser was requested, but no provider is configured.",
            code="llm_not_configured",
        )
    if use_llm and llm_is_configured(settings.llm_provider, settings.llm_api_key, settings.llm_base_url, settings.llm_model):
        llm_attempted = True
        try:
            proposed = propose_operations(
                prompt,
                network_summary_for_model(network),
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
                model=settings.llm_model,
                timeout_s=settings.llm_timeout_s,
                max_retries=settings.llm_max_retries,
            )
            operations = proposed["operations"]
            parser_name = "llm"
            import hashlib

            prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        except ProviderError as exc:
            llm_error = {"code": exc.code, "message": redact(str(exc), [settings.llm_api_key])}
            if parser == "llm":
                return {
                    "parser": "llm",
                    "llm_attempted": True,
                    "llm_error": llm_error,
                    "prompt_sha256": __import__("hashlib").sha256(prompt.encode("utf-8")).hexdigest(),
                    "notes": ["The language model did not return a usable plan."],
                    "unparsed_clauses": [],
                    "operations": [],
                }
            notes.append("The language-model request failed. The rule-based parser was used instead.")
    if operations is None:
        ruled = parse_rules(prompt)
        operations = ruled["operations"]
        unparsed = ruled["unparsed_clauses"]
        notes.extend(ruled["notes"])
        prompt_sha256 = ruled["prompt_sha256"]
        parser_name = "rule_based"
    return {
        "parser": parser_name,
        "llm_attempted": llm_attempted,
        "llm_error": llm_error,
        "prompt_sha256": prompt_sha256,
        "notes": notes,
        "unparsed_clauses": unparsed,
        "operations": operations,
    }


def _run_sumo_repeats(run: SimulationRun, network: dict[str, Any], profiles: list[dict[str, Any]], settings: Any) -> dict[str, Any]:
    results = []
    for index in range(run.repeats):
        if REGISTRY.cancelled(run.id):
            return {"status": "CANCELLED", "error": "The simulation was cancelled.", "log": "", "warnings": [], "metrics": None, "link_metrics": [], "timesteps": [], "quality": "NONE", "result_usability": "none", "exit_code": None, "version": None, "duration_s": 0}
        with TemporaryDirectory(prefix="utl_") as directory:
            result = run_sumo(
                network,
                profiles,
                seed=run.seed + index,
                workdir=Path(directory),
                timeout_s=settings.simulation_timeout_s,
                end_buffer_s=settings.simulation_end_buffer_s,
                cancel_check=lambda: REGISTRY.cancelled(run.id),
                capability=detect_sumo(settings.sumo_binary, settings.sumo_home),
            )
        results.append(result)
        if result["status"] != "SUCCEEDED":
            return result
    if run.repeats == 1:
        return results[0]
    metrics = {}
    for spec in METRIC_SPECS:
        values = []
        for result in results:
            item = (result.get("metrics") or {}).get(spec["key"]) or {}
            values.append(None if item.get("missing", True) else item.get("value"))
        summary = summarize(values)
        metrics[spec["key"]] = metric(
            spec["key"],
            summary["mean"],
            quality="SIMULATED",
            notes=["Value is the mean of repeated SUMO runs. No significance test was performed."],
            summary=summary,
        )
    combined = dict(results[0])
    combined["metrics"] = metrics
    combined["warnings"] = [
        *(results[0].get("warnings") or []),
        {
            "code": "repeat_map",
            "message": f"Map link metrics show repeat 1 of {run.repeats}. Summary metrics are means.",
        },
    ]
    combined["duration_s"] = sum(float(item.get("duration_s") or 0) for item in results)
    return combined


def _store_success(run: SimulationRun, result: dict[str, Any], simulator_version: str | None) -> None:
    run.status = "SUCCEEDED"
    run.quality = result["quality"]
    run.result_usability = result["result_usability"]
    run.metrics_json = result["metrics"]
    run.link_metrics_json = result.get("link_metrics") or []
    run.timesteps_json = result.get("timesteps") or []
    run.warnings_json = result.get("warnings") or []
    run.log_text = redact(result.get("log") or "", [get_settings().llm_api_key])
    run.exit_code = result.get("exit_code")
    run.error = None
    run.duration_s = result.get("duration_s")
    run.finished_at = utcnow()
    run.provenance_json = {
        **run.provenance_json,
        "simulator_version": simulator_version,
        "quality": result["quality"],
        "result_usability": result["result_usability"],
    }


def _store_terminal(run: SimulationRun, result: dict[str, Any]) -> None:
    run.status = result["status"]
    run.quality = result.get("quality") or "NONE"
    run.result_usability = result.get("result_usability") or "none"
    run.metrics_json = result.get("metrics")
    run.link_metrics_json = result.get("link_metrics") or []
    run.timesteps_json = result.get("timesteps") or []
    run.warnings_json = result.get("warnings") or []
    run.log_text = redact(result.get("log") or "", [get_settings().llm_api_key])
    run.exit_code = result.get("exit_code")
    run.error = result.get("error")
    run.duration_s = result.get("duration_s")
    run.finished_at = utcnow()
    if run.status == "BLOCKED_MISSING_DEPENDENCY":
        run.provenance_json = {**run.provenance_json, "estimate_available": True}


def _finish_cancelled(run: SimulationRun) -> None:
    run.status = "CANCELLED"
    run.error = "The simulation was cancelled."
    run.quality = "NONE"
    run.result_usability = "none"
    run.finished_at = utcnow()
    run.warnings_json = [{"code": "cancelled", "message": "The simulation was cancelled."}]


def _run_compare_view(run: SimulationRun) -> dict[str, Any]:
    return {
        "status": run.status,
        "quality": run.quality,
        "result_usability": run.result_usability,
        "comparability": (run.config_json or {}).get("comparability") or {},
    }


def _report_run(run: SimulationRun) -> dict[str, Any]:
    view = present_run(run)
    view["config"] = run.config_json
    return view


def _demand_context(baseline: SimulationRun | None, scenario: SimulationRun | None) -> dict[str, Any]:
    runs = []
    for label, run in (("baseline", baseline), ("scenario", scenario)):
        if run is None:
            continue
        runs.append(
            {
                "label": label,
                "demand_profile_id": run.demand_profile_id,
                "seed": run.seed,
                "assumptions": (run.config_json or {}).get("assumptions") or [],
                "synthetic": (run.provenance_json or {}).get("demand_synthetic"),
                "observed": (run.provenance_json or {}).get("demand_observed"),
            }
        )
    return {"runs": runs}


def _legend(quality: str | None) -> dict[str, Any]:
    return {
        "metric": "speed ratio (simulated speed / free-flow speed)",
        "unit": "1",
        "quality": quality,
        "stops": [
            {"label": ">= 0.8 free-flow", "color": "#3ddc97"},
            {"label": "0.6 – 0.8", "color": "#b6e36a"},
            {"label": "0.4 – 0.6", "color": "#ffb020"},
            {"label": "0.2 – 0.4", "color": "#ff7a45"},
            {"label": "< 0.2", "color": "#ff5d73"},
            {"label": "No speed result", "color": "#8aa0b8"},
        ],
    }


def _new_version(
    study: StudyArea,
    document: dict[str, Any],
    *,
    label: str,
    role: str,
    immutable: bool,
    parent_id: str | None,
    provenance: dict[str, Any],
) -> NetworkVersion:
    return NetworkVersion(
        id=new_id(),
        study_area_id=study.id,
        label=label,
        role=role,
        parent_id=parent_id,
        immutable=immutable,
        content_hash=content_hash(document),
        document_json=document,
        provenance_json=provenance,
        created_at=utcnow(),
    )


def _insert_scenario(
    db: Session,
    *,
    study: StudyArea,
    baseline: NetworkVersion,
    name: str,
    description: str,
    prompt: str,
    network_version_id: str,
    status: str,
    validation: dict[str, Any],
    history: list[str],
) -> Scenario:
    scenario = Scenario(
        id=new_id(),
        study_area_id=study.id,
        name=name,
        description=description,
        prompt=prompt,
        baseline_version_id=baseline.id,
        network_version_id=network_version_id,
        version_history_json=history,
        status=status,
        validation_json=validation,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(scenario)
    db.flush()
    return scenario


def _event(db: Session, scenario: Scenario, kind: str, detail: dict[str, Any]) -> None:
    db.add(
        ScenarioEvent(
            id=new_id(),
            scenario_id=scenario.id,
            kind=kind,
            detail_json=detail,
            created_at=utcnow(),
        )
    )


def _baseline_for(db: Session, study_id: str, version_id: str | None) -> NetworkVersion:
    if version_id:
        version = _version(db, version_id)
        if version.study_area_id != study_id or version.role != "baseline":
            raise AppError("That network is not the baseline for this study.", code="invalid_baseline")
        return version
    baseline = _find_baseline(db, study_id)
    if baseline is None:
        raise AppError("Import or load a baseline network before creating a scenario.", code="baseline_missing")
    return baseline


def _find_baseline(db: Session, study_id: str) -> NetworkVersion | None:
    return db.scalar(
        select(NetworkVersion)
        .where(NetworkVersion.study_area_id == study_id, NetworkVersion.role == "baseline")
        .order_by(NetworkVersion.created_at.asc())
    )


def _reject_if_baseline_exists(db: Session, study_id: str) -> None:
    if _find_baseline(db, study_id) is not None:
        raise AppError(
            "This study already has an immutable baseline. Create a new study to import a different network.",
            status_code=409,
            code="baseline_exists",
        )


def _study(db: Session, study_id: str) -> StudyArea:
    study = db.get(StudyArea, study_id)
    if study is None:
        raise AppError("Study area not found.", status_code=404, code="not_found")
    return study


def _version(db: Session, version_id: str) -> NetworkVersion:
    version = db.get(NetworkVersion, version_id)
    if version is None:
        raise AppError("Network version not found.", status_code=404, code="not_found")
    return version


def _scenario(db: Session, scenario_id: str) -> Scenario:
    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise AppError("Scenario not found.", status_code=404, code="not_found")
    return scenario


def _run(db: Session, run_id: str) -> SimulationRun:
    run = db.get(SimulationRun, run_id)
    if run is None:
        raise AppError("Simulation run not found.", status_code=404, code="not_found")
    return run


def _operations(db: Session, scenario_id: str) -> list[ScenarioOperation]:
    return list(
        db.scalars(
            select(ScenarioOperation)
            .where(ScenarioOperation.scenario_id == scenario_id)
            .order_by(ScenarioOperation.sequence.asc())
        ).all()
    )


def _unique_slug(db: Session, name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:80] or "study"
    slug = base
    suffix = 2
    while db.scalar(select(StudyArea.id).where(StudyArea.slug == slug)):
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


def _page(db: Session, stmt, limit: int, offset: int, presenter) -> dict[str, Any]:
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    rows = list(db.scalars(stmt).all())
    total = len(rows)
    selected = rows[offset : offset + limit]
    return {"items": [presenter(row) for row in selected], "total": total, "limit": limit, "offset": offset}


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


# Remove the mistaken placeholder import if it survived editing.