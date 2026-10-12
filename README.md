# UrbanTrafficLab

UrbanTrafficLab is a web application for editing a road network, keeping those edits in isolated scenarios, running a SUMO simulation when the simulator is installed, and comparing the result with an explicitly labeled estimate when it is not.

The included Synthetic Riverside Grid is not a real city. Supported prompts work without an AI API key.

## What you can do

1. Load the synthetic sample, or import GeoJSON / a small OpenStreetMap extract.
2. Inspect roads and intersections on a pan-and-zoom map.
3. Turn a prompt into structured operations, then accept or reject each one.
4. Apply the accepted operations to a scenario. The baseline version stays immutable.
5. Run SUMO, or see the exact reason it is blocked and run the ESTIMATED assignment instead.
6. Compare baseline and scenario metrics, color links when speeds exist, and export HTML, CSV, JSON, and GeoJSON.

## Layout

| Path | Contents |
| --- | --- |
| `backend/` | FastAPI, scenario engine, SUMO adapter, tests |
| `frontend/` | Next.js studio |
| `docs/` | Data model, methodology, API, deployment, limitations |
| `examples/` | Demo prompt and a tiny GeoJSON network |
| `infra/docker/` | Service images |
| `.github/workflows/ci.yml` | Backend and frontend checks |

## Prerequisites

- Python 3.11+ (3.12 is used in CI)
- Node.js 22
- Optional: Eclipse SUMO (`sumo` and `netconvert`)
- Optional: Docker, for Compose
- Optional: an OpenAI-compatible API key, only if you want the language-model parser

### SUMO

| System | Install |
| --- | --- |
| Debian / Ubuntu | `sudo apt-get install sumo sumo-tools` |
| macOS (Homebrew) | `brew install sumo` |
| Windows | Install the Eclipse SUMO release and add its `bin` directory to `PATH` |

Set `SUMO_HOME` if the tools cannot find their data directory. The adapter uses `/usr/share/sumo` when that directory exists and `SUMO_HOME` is empty. `GET /api/v1/capabilities` reports whether both executables were found. It does not include filesystem paths.

## Configure

```bash
cp .env.example .env
```

Leave `UTL_LLM_PROVIDER=none` to use the rule-based parser. Credentials are read only by the backend.

## Run locally

Terminal 1:

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000
```

Terminal 2:

```bash
cd frontend
npm ci
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). The page loads the synthetic sample. API docs are at [http://localhost:8000/docs](http://localhost:8000/docs).

SQLite is created at `backend/data/urbantrafficlab.db`. No database server is required for this path.

### Sample scenario

The prompt box is filled with:

> Make road R3 one-way eastbound, increase the speed limit on R4 to 40 km/h, and add a signal at intersection J2.

Parse it, accept the three operations (the signal carries a simplified-model warning), and apply them. Run the estimate on the baseline and on the scenario, then compare. If SUMO is installed, **Run SUMO** executes it. If it is not, the status is `BLOCKED_MISSING_DEPENDENCY` and the estimate button stays separate.

## Tests

```bash
cd backend && . .venv/bin/activate && ruff check app tests && pytest
cd frontend && npm test && npm run lint && npm run build
```

`pytest -m integration` is the real SUMO test. It skips when the binaries are absent. Other tests never call Overpass or an AI provider.

Schema migration, if you use it:

```bash
cd backend && UTL_DATABASE_URL=sqlite:///./data/urbantrafficlab.db alembic upgrade head
```

The API also creates tables on startup.

## Docker

```bash
docker compose up --build
```

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). Compose was not executed as a production deployment in this repository's development notes unless a later verification says so.

## Map data

The default map has no third-party tiles. Set `NEXT_PUBLIC_MAP_TILE_URL` to a raster template if you have a provider. OpenStreetMap data, when imported, is attributed and used under the ODbL. Do not treat a tile endpoint as unlimited or free unless that provider says so.

## Further reading

- [docs/architecture/OVERVIEW.md](docs/architecture/OVERVIEW.md)
- [docs/DATA_MODEL.md](docs/DATA_MODEL.md)
- [docs/SIMULATION_METHODOLOGY.md](docs/SIMULATION_METHODOLOGY.md)
- [docs/API.md](docs/API.md)
- [docs/LIMITATIONS.md](docs/LIMITATIONS.md)
