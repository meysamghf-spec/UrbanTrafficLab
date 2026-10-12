"""Persistent entities.

Network geometry lives in the versioned JSON document. Baseline rows are
marked immutable and the service layer refuses to update them.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StudyArea(Base):
    __tablename__ = "study_areas"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(32))
    bbox_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    attribution: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NetworkVersion(Base):
    __tablename__ = "network_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    study_area_id: Mapped[str] = mapped_column(ForeignKey("study_areas.id"), index=True)
    label: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(32))
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    immutable: Mapped[bool] = mapped_column(Boolean, default=False)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    document_json: Mapped[dict] = mapped_column(JSON)
    provenance_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Scenario(Base):
    __tablename__ = "scenarios"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    study_area_id: Mapped[str] = mapped_column(ForeignKey("study_areas.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    prompt: Mapped[str] = mapped_column(Text, default="")
    baseline_version_id: Mapped[str] = mapped_column(ForeignKey("network_versions.id"))
    network_version_id: Mapped[str] = mapped_column(ForeignKey("network_versions.id"))
    version_history_json: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    validation_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ScenarioOperation(Base):
    __tablename__ = "scenario_operations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scenario_id: Mapped[str] = mapped_column(ForeignKey("scenarios.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    operation_type: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[dict] = mapped_column(JSON)
    decision: Mapped[str] = mapped_column(String(16), default="pending")
    validation_status: Mapped[str] = mapped_column(String(32), default="pending")
    apply_status: Mapped[str] = mapped_column(String(32), default="not_applied")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ScenarioEvent(Base):
    __tablename__ = "scenario_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scenario_id: Mapped[str] = mapped_column(ForeignKey("scenarios.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    detail_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SimulationRun(Base):
    __tablename__ = "simulation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    study_area_id: Mapped[str] = mapped_column(ForeignKey("study_areas.id"), index=True)
    scenario_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    network_version_id: Mapped[str] = mapped_column(ForeignKey("network_versions.id"))
    engine: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(40))
    seed: Mapped[int] = mapped_column(Integer)
    repeats: Mapped[int] = mapped_column(Integer, default=1)
    demand_profile_id: Mapped[str] = mapped_column(String(80))
    config_json: Mapped[dict] = mapped_column(JSON)
    metrics_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    link_metrics_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    timesteps_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    warnings_json: Mapped[list] = mapped_column(JSON)
    log_text: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_usability: Mapped[str] = mapped_column(String(16), default="none")
    quality: Mapped[str] = mapped_column(String(16), default="NONE")
    provenance_json: Mapped[dict] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReportArtifact(Base):
    __tablename__ = "report_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    study_area_id: Mapped[str] = mapped_column(ForeignKey("study_areas.id"), index=True)
    scenario_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    baseline_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    scenario_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    html: Mapped[str] = mapped_column(Text)
    report_json: Mapped[dict] = mapped_column(JSON)
    csv_text: Mapped[str] = mapped_column(Text)
    geojson: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    provenance_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
