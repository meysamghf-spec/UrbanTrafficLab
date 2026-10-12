"""Alembic creates the same tables the application expects."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_alembic_upgrade_head(tmp_path: Path):
    database = tmp_path / "migrated.db"
    env = os.environ.copy()
    env["UTL_DATABASE_URL"] = f"sqlite:///{database}"
    backend = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    connection = sqlite3.connect(database)
    try:
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        connection.close()
    assert {
        "study_areas",
        "network_versions",
        "scenarios",
        "scenario_operations",
        "scenario_events",
        "simulation_runs",
        "report_artifacts",
    } <= names
