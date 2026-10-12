# Deployment

This repository has not been deployed to a public host. The files below are local and CI configuration only.

## Local processes

SQLite needs no extra service:

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --port 8000
```

```bash
cd frontend
npm ci
npm run dev
```

Open `http://localhost:3000`. The browser calls `NEXT_PUBLIC_API_BASE_URL` (default `http://localhost:8000`).

## Docker Compose

```bash
docker compose up --build
```

Compose starts PostGIS, the API, and the frontend. The database password in `docker-compose.yml` is a local development value. Replace it before any shared deployment. Tables are created by the API on startup. `alembic upgrade head` is the migration path when you want versioned schema changes.

The frontend image bakes `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000` so a browser on the host can reach the published API port. The container name `backend` is not a URL the browser can use.

## SUMO in the image

The backend image installs Debian `sumo` and `sumo-tools` and sets `SUMO_HOME=/usr/share/sumo`. Confirm capability with `GET /api/v1/capabilities` after the container starts. A missing binary is reported as `BLOCKED_MISSING_DEPENDENCY`.

## What is not configured

- TLS, authentication, and user isolation
- A production process manager beyond the container command
- Object storage for simulation artifacts
- A hosted tile provider

Do not expose this API on the public internet without adding authentication. The service currently has none.
