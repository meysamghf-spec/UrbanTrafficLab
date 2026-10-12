"""Version 1 HTTP routes."""

from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app import __version__
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import database_ready, get_db
from app.schemas.requests import (
    ApplyRequest,
    BBoxRequest,
    ParseRequest,
    ReportCreate,
    ReviewRequest,
    ScenarioCreate,
    SimulationCreate,
    StudyCreate,
)
from app.services import usecases

router = APIRouter()


@router.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "urbantrafficlab", "version": __version__}


@router.get("/ready", tags=["health"])
def ready() -> dict[str, str]:
    if not database_ready():
        raise AppError("Database is not ready.", status_code=503, code="not_ready")
    return {"status": "ready", "database": "ok"}


@router.get("/capabilities", tags=["health"])
def capabilities() -> dict[str, Any]:
    return usecases.capabilities()


@router.post("/study-areas/sample", tags=["study-areas"])
def bootstrap_sample(db: Session = Depends(get_db)) -> dict[str, Any]:
    return usecases.bootstrap_sample(db)


@router.post("/study-areas", tags=["study-areas"])
def create_study(body: StudyCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    return usecases.create_study(db, body.name, body.description)


@router.get("/study-areas", tags=["study-areas"])
def list_studies(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return usecases.list_studies(db, limit, offset)


@router.get("/study-areas/{study_id}", tags=["study-areas"])
def get_study(study_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return usecases.get_study(db, study_id)


@router.post("/study-areas/{study_id}/import/geojson", tags=["networks"])
async def import_geojson(study_id: str, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > get_settings().max_upload_bytes:
        raise AppError("Upload exceeds the configured size limit.", status_code=413, code="payload_too_large")
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AppError("Upload is not valid JSON.", code="invalid_json") from exc
    return usecases.import_network_geojson(db, study_id, document)


@router.post("/study-areas/{study_id}/import/osm", tags=["networks"])
def import_osm(study_id: str, body: BBoxRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    with httpx.Client() as client:
        return usecases.import_network_osm(db, study_id, body.bbox, client)


@router.get("/study-areas/{study_id}/networks", tags=["networks"])
def list_networks(
    study_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return usecases.list_networks(db, study_id, limit, offset)


@router.get("/networks/{version_id}", tags=["networks"])
def get_network(version_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return usecases.get_network(db, version_id)


@router.get("/networks/{version_id}/geojson", tags=["networks"])
def network_geojson(version_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.domain.importers import export_geojson
    from app.domain.network import copy_network

    version = usecases.get_network(db, version_id, include_document=True)
    return export_geojson(copy_network(version["document"]))


@router.get("/networks/{version_id}/features/{feature_id}", tags=["networks"])
def network_feature(version_id: str, feature_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return usecases.network_feature(db, version_id, feature_id)


@router.post("/networks/{version_id}/validate", tags=["networks"])
def validate_network(version_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return usecases.validate_version(db, version_id)


@router.get("/map/layers", tags=["map"])
def map_layers(
    network_version_id: str,
    run_id: str | None = None,
    scenario_id: str | None = None,
    timestep: float | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return usecases.map_layers(db, network_version_id, run_id, scenario_id, timestep)


@router.post("/parse", tags=["scenarios"])
def parse_prompt(body: ParseRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    return usecases.parse_prompt(db, body.network_version_id, body.prompt, body.parser)


@router.post("/operations/review", tags=["scenarios"])
def review_operations(body: ReviewRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    return usecases.review(db, body.network_version_id, body.operations)


@router.post("/scenarios", tags=["scenarios"])
def create_scenario(body: ScenarioCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    return usecases.create_scenario(db, body.model_dump())


@router.get("/scenarios", tags=["scenarios"])
def list_scenarios(
    study_area_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return usecases.list_scenarios(db, study_area_id, limit, offset)


@router.get("/scenarios/{scenario_id}", tags=["scenarios"])
def get_scenario(scenario_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return usecases.get_scenario(db, scenario_id)


@router.post("/scenarios/{scenario_id}/apply", tags=["scenarios"])
def apply_scenario(scenario_id: str, body: ApplyRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    return usecases.apply_to_scenario(db, scenario_id, body.operations, body.execution_policy, body.prompt)


@router.post("/scenarios/{scenario_id}/rollback", tags=["scenarios"])
def rollback_scenario(scenario_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return usecases.rollback_scenario(db, scenario_id)


@router.post("/scenarios/{scenario_id}/clone", tags=["scenarios"])
def clone_scenario(scenario_id: str, name: str | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    return usecases.clone_scenario(db, scenario_id, name)


@router.get("/scenarios/{scenario_id}/history", tags=["scenarios"])
def scenario_history(scenario_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return usecases.scenario_history(db, scenario_id)


@router.post("/simulations", tags=["simulation"])
def start_simulation(body: SimulationCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    return usecases.start_simulation(db, body.model_dump())


@router.get("/simulations/{run_id}", tags=["simulation"])
def get_simulation(run_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return usecases.get_simulation(db, run_id)


@router.post("/simulations/{run_id}/cancel", tags=["simulation"])
def cancel_simulation(run_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return usecases.cancel_simulation(db, run_id)


@router.get("/simulations/{run_id}/metrics", tags=["simulation"])
def simulation_metrics(run_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return usecases.simulation_metrics(db, run_id)


@router.get("/simulations/{run_id}/logs", tags=["simulation"])
def simulation_logs(run_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return usecases.simulation_logs(db, run_id)


@router.get("/comparisons", tags=["simulation"])
def compare(
    baseline_run_id: str,
    scenario_run_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return usecases.compare_runs(db, baseline_run_id, scenario_run_id)


@router.post("/reports", tags=["reports"])
def create_report(body: ReportCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    return usecases.create_report(db, body.model_dump())


@router.get("/reports/{report_id}", tags=["reports"])
def get_report(report_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    artifact = usecases.get_report(db, report_id)
    return {
        "id": artifact.id,
        "title": artifact.title,
        "report": artifact.report_json,
        "created_at": artifact.created_at.isoformat(),
    }


@router.get("/reports/{report_id}/download", tags=["reports"])
def download_report(
    report_id: str,
    format: str = Query(pattern="^(html|json|csv|geojson)$"),
    db: Session = Depends(get_db),
) -> Response:
    artifact = usecases.get_report(db, report_id)
    filename = f"urbantrafficlab-{artifact.id}.{ 'geojson' if format == 'geojson' else format }"
    if format == "html":
        return Response(
            content=artifact.html,
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    if format == "csv":
        return Response(
            content=artifact.csv_text,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    if format == "geojson":
        if artifact.geojson is None:
            raise AppError("This report has no spatial export.", code="no_geojson")
        return Response(
            content=json.dumps(artifact.geojson),
            media_type="application/geo+json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    return Response(
        content=json.dumps(artifact.report_json),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
