# Architecture

```
browser (Next.js)
    |
    | /api/v1
    v
FastAPI
    |-- scenario engine (validate, apply, rollback)
    |-- rule parser and optional LLM adapter
    |-- SUMO subprocess adapter
    |-- static BPR estimator
    |-- report renderer
    v
SQLite or PostgreSQL
    network documents, scenarios, runs, reports
```

The frontend never receives provider credentials. It talks only to the versioned HTTP API.

Network documents are directed graphs. A bidirectional road is two edges that share a `road_id`. Scenario application copies the baseline document, applies accepted operations in one transaction, and stores a new network version when the whole plan is valid.

SUMO conversion writes node, edge, route, and edge-data files in a temporary directory and runs `netconvert` then `sumo`. Logs returned to clients are redacted. Public capability responses omit filesystem paths.

The estimator uses a four-iteration method of successive averages with a BPR function. Its quality field is `ESTIMATED`. Comparison refuses to treat an estimate and a SUMO run as equivalent.
