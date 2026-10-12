# UrbanTrafficLab backend

FastAPI service for study areas, versioned road networks, scenario operations, SUMO, and the labeled analytical estimator.

Run it from this directory:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000
```

See the repository README for the full workflow. Tests: `pytest`. Lint: `ruff check app tests`.
