# Data model

Persistent tables:

| Table | Role |
| --- | --- |
| `study_areas` | Named area, kind (`synthetic`, `custom`, `geojson`, `osm`), attribution, bbox |
| `network_versions` | Immutable or scenario document, content hash, provenance |
| `scenarios` | Name, prompt, baseline version, current version, history, validation |
| `scenario_operations` | Ordered operation payload, decision, validation status, apply status |
| `scenario_events` | Create, apply, rollback, and clone events |
| `simulation_runs` | Engine, status, seed, metrics, link metrics, timesteps, warnings, redacted log |
| `report_artifacts` | HTML, JSON, CSV, GeoJSON, provenance |

The network document (`schema_version` 1) holds:

- `nodes`, `edges`
- `intersections`, `signals`, `bridges`, `tunnels`, `roundabouts`
- `land_uses`, `transit_routes`, `turn_restrictions`
- `demand_profiles`, `meta`

Edge identifiers are `{road_id}_{from}_{to}`. One-way roads keep a single directed edge. Removing a road also drops its turn restrictions and structure references.

Demand profiles record `synthetic` and `observed`. The sample morning and evening profiles are synthetic. Land-use trip generation creates `land_use_generated` only when a zone has housing units. The default rate is 0.1 vehicle trips per unit per peak hour and is an assumption.

Stable hashes are SHA-256 of canonical JSON. Baseline rows set `immutable=true` and `role=baseline`.
