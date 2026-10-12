"""Request bodies. Unknown fields are rejected."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StudyCreate(StrictRequest):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)


class BBoxRequest(StrictRequest):
    bbox: list[float] = Field(min_length=4, max_length=4)


class ParseRequest(StrictRequest):
    network_version_id: str = Field(min_length=1, max_length=36)
    prompt: str = Field(min_length=1, max_length=8000)
    parser: Literal["auto", "rules", "llm"] = "auto"


class ReviewRequest(StrictRequest):
    network_version_id: str = Field(min_length=1, max_length=36)
    operations: list[dict[str, Any]] = Field(max_length=100)


class ScenarioCreate(StrictRequest):
    study_area_id: str
    baseline_version_id: str | None = None
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    prompt: str = Field(default="", max_length=8000)
    operations: list[dict[str, Any]] | None = None
    execution_policy: Literal["review", "auto_valid"] = "review"


class ApplyRequest(StrictRequest):
    operations: list[dict[str, Any]] = Field(max_length=100)
    execution_policy: Literal["review", "auto_valid"] = "review"
    prompt: str = Field(default="", max_length=8000)


class SimulationCreate(StrictRequest):
    network_version_id: str
    scenario_id: str | None = None
    engine: Literal["sumo", "estimator"]
    demand_profile_id: str = "morning_peak"
    seed: int = Field(default=42, ge=0, le=2_000_000_000)
    repeats: int = Field(default=1, ge=1, le=10)
    include_generated_demand: bool = True


class ReportCreate(StrictRequest):
    study_area_id: str
    scenario_id: str | None = None
    baseline_run_id: str | None = None
    scenario_run_id: str | None = None
    title: str = Field(default="UrbanTrafficLab scenario report", max_length=200)
