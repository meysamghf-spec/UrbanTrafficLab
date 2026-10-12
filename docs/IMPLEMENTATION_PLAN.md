# Implementation plan

UrbanTrafficLab started from a repository that contained a one-line README and an MIT license. The application is a monorepo: a FastAPI backend, a Next.js studio, and sample data that does not require a live map service or an AI key.

## Decisions

- Geometry is stored as GeoJSON inside versioned network documents. SQLite is the default database. PostgreSQL is optional through `UTL_DATABASE_URL`. PostGIS is available in Docker Compose, but the ORM does not issue spatial SQL.
- A baseline is an immutable `NetworkVersion`. Scenario edits insert a new version. The baseline row is never updated.
- The rule-based parser handles the documented commands, including the Riverside demo prompt, without an API key. An OpenAI-compatible provider is optional and server-side only.
- SUMO 1.18 is invoked with an argument list when `sumo` and `netconvert` are installed. If they are missing, the run is `BLOCKED_MISSING_DEPENDENCY`. The analytical estimator is a separate, explicitly labeled `ESTIMATED` result.
- Simulations run in a background thread. Tests set `UTL_SYNC_JOBS=1` so the request waits for the result. Redis is not required.
- The map draws the network without a tile server. `NEXT_PUBLIC_MAP_TILE_URL` is an optional raster template.

## Status

| Area | State |
| --- | --- |
| Domain model, operations, parser, estimator, SUMO adapter | Implemented |
| Versioned API, reports, Alembic migration | Implemented |
| Sample network and demo prompt | Implemented |
| Next.js studio, MapLibre, charts, exports | Implemented |
| Pytest, Vitest, GitHub Actions, Docker Compose | Implemented |
| Live city calibration and multi-user authentication | Not implemented |

## Verification

Run the commands in the repository README. Record the actual pytest, Vitest, and production-build results rather than assuming they passed. The SUMO integration test is marked `integration` and runs the installed binary; a mocked failure test does not count as a SUMO success.
