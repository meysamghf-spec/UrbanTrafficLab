# API

Base path: `/api/v1`. Interactive schema: `/docs`.

Errors use `{ "error": { "code", "message", "details" } }`. Unknown request fields are rejected. There is no authentication.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health`, `/ready`, `/capabilities` | Process, database, SUMO, estimator, AI |
| POST | `/study-areas/sample` | Idempotent synthetic study |
| POST, GET | `/study-areas` | Create or list studies |
| GET | `/study-areas/{id}` | Study detail |
| POST | `/study-areas/{id}/import/geojson` | First baseline from GeoJSON |
| POST | `/study-areas/{id}/import/osm` | First baseline from Overpass |
| GET | `/study-areas/{id}/networks` | Versions |
| GET | `/networks/{id}` | Version, stats, document |
| GET | `/networks/{id}/geojson` | Directed GeoJSON |
| GET | `/networks/{id}/features/{feature_id}` | Road or intersection |
| POST | `/networks/{id}/validate` | Topology check |
| GET | `/map/layers` | Roads, nodes, changes, legend |
| POST | `/parse` | Prompt to reviewed operations |
| POST | `/operations/review` | Re-validate a plan |
| POST, GET | `/scenarios` | Create or list |
| GET | `/scenarios/{id}` | Scenario and operations |
| POST | `/scenarios/{id}/apply` | Apply another plan from the baseline |
| POST | `/scenarios/{id}/rollback` | Restore the previous version |
| POST | `/scenarios/{id}/clone` | Copy the scenario |
| GET | `/scenarios/{id}/history` | Events |
| POST | `/simulations` | `engine` is `sumo` or `estimator` |
| GET | `/simulations/{id}` | Status and metrics |
| POST | `/simulations/{id}/cancel` | Cancel a queued or running job |
| GET | `/simulations/{id}/metrics`, `/logs` | Metrics or redacted log |
| GET | `/comparisons` | Baseline versus scenario |
| POST | `/reports` | Build an artifact |
| GET | `/reports/{id}` | JSON body |
| GET | `/reports/{id}/download?format=` | `html`, `json`, `csv`, or `geojson` |

`POST /simulations` returns immediately when `UTL_SYNC_JOBS` is false. The status then moves through `QUEUED`, `PREPARING`, `VALIDATING`, `RUNNING`, and a terminal state: `SUCCEEDED`, `FAILED`, `CANCELLED`, or `BLOCKED_MISSING_DEPENDENCY`.

A SUMO request never falls back to the estimator. The blocked response sets `estimate_available` so the client can ask for an estimate explicitly.
