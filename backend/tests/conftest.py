"""Test configuration.

Environment variables are set before the application is imported so the suite
uses an in-memory database and runs simulations on the request thread.
"""

from __future__ import annotations

import os

os.environ["UTL_DATABASE_URL"] = "sqlite://"
os.environ["UTL_SYNC_JOBS"] = "1"
os.environ["UTL_LLM_PROVIDER"] = "none"
os.environ["UTL_LLM_API_KEY"] = ""
os.environ["UTL_CORS_ORIGINS"] = "http://localhost:3000"

import app.models.entities  # noqa: F401
import pytest
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_engine, reset_engine
from app.main import app
from app.workers.jobs import REGISTRY
from fastapi.testclient import TestClient

get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _fresh_database():
    get_settings.cache_clear()
    reset_engine()
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    REGISTRY.clear()
    yield
    Base.metadata.drop_all(engine)
    reset_engine()
    REGISTRY.clear()


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client
