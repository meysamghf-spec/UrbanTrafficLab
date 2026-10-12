"""Initial schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "study_areas",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("slug", sa.String(120), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("bbox_json", sa.JSON(), nullable=True),
        sa.Column("attribution", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_study_areas_slug", "study_areas", ["slug"], unique=True)
    op.create_table(
        "network_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("study_area_id", sa.String(36), sa.ForeignKey("study_areas.id"), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("parent_id", sa.String(36), nullable=True),
        sa.Column("immutable", sa.Boolean(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("document_json", sa.JSON(), nullable=False),
        sa.Column("provenance_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_network_versions_study_area_id", "network_versions", ["study_area_id"])
    op.create_index("ix_network_versions_content_hash", "network_versions", ["content_hash"])
    op.create_table(
        "scenarios",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("study_area_id", sa.String(36), sa.ForeignKey("study_areas.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("baseline_version_id", sa.String(36), sa.ForeignKey("network_versions.id"), nullable=False),
        sa.Column("network_version_id", sa.String(36), sa.ForeignKey("network_versions.id"), nullable=False),
        sa.Column("version_history_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("validation_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_scenarios_study_area_id", "scenarios", ["study_area_id"])
    op.create_table(
        "scenario_operations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scenario_id", sa.String(36), sa.ForeignKey("scenarios.id"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("operation_type", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("validation_status", sa.String(32), nullable=False),
        sa.Column("apply_status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_scenario_operations_scenario_id", "scenario_operations", ["scenario_id"])
    op.create_table(
        "scenario_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scenario_id", sa.String(36), sa.ForeignKey("scenarios.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("detail_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_scenario_events_scenario_id", "scenario_events", ["scenario_id"])
    op.create_table(
        "simulation_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("study_area_id", sa.String(36), sa.ForeignKey("study_areas.id"), nullable=False),
        sa.Column("scenario_id", sa.String(36), nullable=True),
        sa.Column("network_version_id", sa.String(36), sa.ForeignKey("network_versions.id"), nullable=False),
        sa.Column("engine", sa.String(32), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("repeats", sa.Integer(), nullable=False),
        sa.Column("demand_profile_id", sa.String(80), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=True),
        sa.Column("link_metrics_json", sa.JSON(), nullable=True),
        sa.Column("timesteps_json", sa.JSON(), nullable=True),
        sa.Column("warnings_json", sa.JSON(), nullable=False),
        sa.Column("log_text", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("result_usability", sa.String(16), nullable=False),
        sa.Column("quality", sa.String(16), nullable=False),
        sa.Column("provenance_json", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_simulation_runs_study_area_id", "simulation_runs", ["study_area_id"])
    op.create_index("ix_simulation_runs_scenario_id", "simulation_runs", ["scenario_id"])
    op.create_table(
        "report_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("study_area_id", sa.String(36), sa.ForeignKey("study_areas.id"), nullable=False),
        sa.Column("scenario_id", sa.String(36), nullable=True),
        sa.Column("baseline_run_id", sa.String(36), nullable=True),
        sa.Column("scenario_run_id", sa.String(36), nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("html", sa.Text(), nullable=False),
        sa.Column("report_json", sa.JSON(), nullable=False),
        sa.Column("csv_text", sa.Text(), nullable=False),
        sa.Column("geojson", sa.JSON(), nullable=True),
        sa.Column("provenance_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_report_artifacts_study_area_id", "report_artifacts", ["study_area_id"])


def downgrade() -> None:
    op.drop_table("report_artifacts")
    op.drop_table("simulation_runs")
    op.drop_table("scenario_events")
    op.drop_table("scenario_operations")
    op.drop_table("scenarios")
    op.drop_table("network_versions")
    op.drop_table("study_areas")
