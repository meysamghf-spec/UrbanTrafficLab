"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ComparisonChart } from "@/components/ComparisonChart";
import { MetricsTable } from "@/components/MetricsTable";
import { OperationList } from "@/components/OperationList";
import { SimulationPanel } from "@/components/SimulationPanel";
import { StatusNotice } from "@/components/StatusNotice";
import { api, downloadUrl } from "@/lib/api";
import { formatNumber, shortHash } from "@/lib/format";
import type {
  Capabilities,
  ComparisonRow,
  MapLayers,
  Operation,
  Scenario,
  SimulationRun,
  Study,
} from "@/lib/types";

const MapCanvas = dynamic(() => import("@/components/MapCanvas").then((mod) => mod.MapCanvas), {
  ssr: false,
  loading: () => <StatusNotice state="loading" message="Loading the map." />,
});

const DEMO_PROMPT =
  "Make road R3 one-way eastbound, increase the speed limit on R4 to 40 km/h, and add a signal at intersection J2.";

const TERMINAL = new Set(["SUCCEEDED", "FAILED", "CANCELLED", "BLOCKED_MISSING_DEPENDENCY"]);

export function Dashboard() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [studies, setStudies] = useState<Study[]>([]);
  const [study, setStudy] = useState<Study | null>(null);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [scenario, setScenario] = useState<Scenario | null>(null);
  const [view, setView] = useState<"baseline" | "scenario">("baseline");
  const [layers, setLayers] = useState<MapLayers | null>(null);
  const [prompt, setPrompt] = useState(DEMO_PROMPT);
  const [operations, setOperations] = useState<Operation[]>([]);
  const [notes, setNotes] = useState<string[]>([]);
  const [unparsed, setUnparsed] = useState<string[]>([]);
  const [feature, setFeature] = useState<Record<string, unknown> | null>(null);
  const [baselineRun, setBaselineRun] = useState<SimulationRun | null>(null);
  const [scenarioRun, setScenarioRun] = useState<SimulationRun | null>(null);
  const [runTarget, setRunTarget] = useState<"baseline" | "scenario">("baseline");
  const [comparison, setComparison] = useState<{ comparable: boolean; notes: string[]; rows: ComparisonRow[] } | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [profile, setProfile] = useState("morning_peak");
  const [seed, setSeed] = useState(42);
  const [timestep, setTimestep] = useState<number | "">("");
  const [history, setHistory] = useState<{ kind: string; created_at: string }[]>([]);
  const [logText, setLogText] = useState("");
  const [studyName, setStudyName] = useState("Custom study");
  const [bbox, setBbox] = useState("0,0,0.02,0.02");

  const networkVersionId = useMemo(() => {
    if (view === "scenario" && scenario) {
      return scenario.network_version_id;
    }
    return study?.baseline_network_version_id || null;
  }, [view, scenario, study]);

  const activeRun = runTarget === "scenario" ? scenarioRun : baselineRun;

  const refreshStudies = useCallback(async (selectedId?: string) => {
    const page = await api<{ items: Study[] }>("/api/v1/study-areas?limit=50");
    setStudies(page.items);
    const next = page.items.find((item) => item.id === selectedId) || page.items[0] || null;
    setStudy(next);
    return next;
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function boot() {
      try {
        const [caps] = await Promise.all([
          api<Capabilities>("/api/v1/capabilities"),
          api<Study>("/api/v1/study-areas/sample", { method: "POST" }),
        ]);
        if (cancelled) {
          return;
        }
        setCapabilities(caps);
        await refreshStudies();
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "The application could not reach the API.");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }
    void boot();
    return () => {
      cancelled = true;
    };
  }, [refreshStudies]);

  useEffect(() => {
    if (!networkVersionId) {
      setLayers(null);
      return;
    }
    const params = new URLSearchParams({ network_version_id: networkVersionId });
    if (scenario && view === "scenario") {
      params.set("scenario_id", scenario.id);
    }
    if (activeRun?.status === "SUCCEEDED" && activeRun.network_version_id === networkVersionId) {
      params.set("run_id", activeRun.id);
    }
    if (timestep !== "") {
      params.set("timestep", String(timestep));
    }
    let cancelled = false;
    api<MapLayers>(`/api/v1/map/layers?${params.toString()}`)
      .then((payload) => {
        if (!cancelled) {
          setLayers(payload);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Map layers could not be loaded.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [networkVersionId, scenario, view, activeRun, timestep]);

  useEffect(() => {
    if (!activeRun || TERMINAL.has(activeRun.status)) {
      return;
    }
    let attempts = 0;
    const timer = window.setInterval(() => {
      attempts += 1;
      if (attempts > 80) {
        window.clearInterval(timer);
        return;
      }
      api<SimulationRun>(`/api/v1/simulations/${activeRun.id}`)
        .then((run) => {
          if (runTarget === "scenario") {
            setScenarioRun(run);
          } else {
            setBaselineRun(run);
          }
          if (TERMINAL.has(run.status)) {
            window.clearInterval(timer);
          }
        })
        .catch(() => window.clearInterval(timer));
    }, 1500);
    return () => window.clearInterval(timer);
  }, [activeRun, runTarget]);

  async function selectStudy(id: string) {
    const next = studies.find((item) => item.id === id) || null;
    setStudy(next);
    setScenario(null);
    setOperations([]);
    setView("baseline");
    setBaselineRun(null);
    setScenarioRun(null);
    setComparison(null);
    setFeature(null);
    setHistory([]);
  }

  async function loadSample() {
    setBusy(true);
    setError(null);
    try {
      const loaded = await api<Study>("/api/v1/study-areas/sample", { method: "POST" });
      await refreshStudies(loaded.id);
      setScenario(null);
      setView("baseline");
    } catch (err) {
      setError(err instanceof Error ? err.message : "The sample network could not be loaded.");
    } finally {
      setBusy(false);
    }
  }

  async function createStudy() {
    setBusy(true);
    setError(null);
    try {
      const created = await api<Study>("/api/v1/study-areas", {
        method: "POST",
        body: JSON.stringify({ name: studyName, description: "Custom study area. Import a network before editing." }),
      });
      await refreshStudies(created.id);
      setScenario(null);
      setView("baseline");
    } catch (err) {
      setError(err instanceof Error ? err.message : "The study could not be created.");
    } finally {
      setBusy(false);
    }
  }

  async function importGeoJson(file: File) {
    if (!study) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const text = await file.text();
      await api(`/api/v1/study-areas/${study.id}/import/geojson`, {
        method: "POST",
        body: text,
        headers: { "content-type": "application/json" },
      });
      await refreshStudies(study.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "GeoJSON import failed.");
    } finally {
      setBusy(false);
    }
  }

  async function importOsm() {
    if (!study) {
      return;
    }
    const parts = bbox.split(",").map((item) => Number(item.trim()));
    if (parts.length !== 4 || parts.some((item) => Number.isNaN(item))) {
      setError("Enter a bbox as west,south,east,north.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api(`/api/v1/study-areas/${study.id}/import/osm`, {
        method: "POST",
        body: JSON.stringify({ bbox: parts }),
      });
      await refreshStudies(study.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "OpenStreetMap import failed.");
    } finally {
      setBusy(false);
    }
  }

  async function parsePrompt() {
    if (!study?.baseline_network_version_id) {
      setError("Load a baseline network before parsing a prompt.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const parsed = await api<{ operations: Operation[]; notes: string[]; unparsed_clauses: string[] }>("/api/v1/parse", {
        method: "POST",
        body: JSON.stringify({
          network_version_id: study.baseline_network_version_id,
          prompt,
          parser: "auto",
        }),
      });
      setOperations(parsed.operations);
      setNotes(parsed.notes);
      setUnparsed(parsed.unparsed_clauses);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The prompt could not be parsed.");
    } finally {
      setBusy(false);
    }
  }

  function setDecision(id: string, decision: "accepted" | "rejected" | "pending") {
    setOperations((current) => current.map((item) => (item.id === id ? { ...item, decision } : item)));
  }

  async function selectCandidate(id: string, featureId: string) {
    if (!study?.baseline_network_version_id) {
      return;
    }
    const next = operations.map((item) =>
      item.id === id ? { ...item, target: { ...item.target, feature_id: featureId }, decision: "pending" } : item,
    );
    setBusy(true);
    try {
      const reviewed = await api<{ operations: Operation[] }>("/api/v1/operations/review", {
        method: "POST",
        body: JSON.stringify({ network_version_id: study.baseline_network_version_id, operations: next }),
      });
      setOperations(reviewed.operations);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The selected feature could not be reviewed.");
    } finally {
      setBusy(false);
    }
  }

  async function applyScenario() {
    if (!study) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await api<Scenario>("/api/v1/scenarios", {
        method: "POST",
        body: JSON.stringify({
          study_area_id: study.id,
          name: "Reviewed scenario",
          prompt,
          operations,
          execution_policy: "review",
        }),
      });
      setScenario(created);
      setView("scenario");
      const events = await api<{ events: { kind: string; created_at: string }[] }>(
        `/api/v1/scenarios/${created.id}/history`,
      );
      setHistory(events.events);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The scenario was not applied.");
    } finally {
      setBusy(false);
    }
  }

  async function rollback() {
    if (!scenario) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const next = await api<Scenario>(`/api/v1/scenarios/${scenario.id}/rollback`, { method: "POST" });
      setScenario(next);
      setView("baseline");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Rollback failed.");
    } finally {
      setBusy(false);
    }
  }

  async function cloneScenario() {
    if (!scenario) {
      return;
    }
    setBusy(true);
    try {
      const next = await api<Scenario>(`/api/v1/scenarios/${scenario.id}/clone?name=Scenario%20copy`, { method: "POST" });
      setScenario(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Clone failed.");
    } finally {
      setBusy(false);
    }
  }

  async function runSimulation(engine: "sumo" | "estimator") {
    const versionId =
      runTarget === "scenario" && scenario ? scenario.network_version_id : study?.baseline_network_version_id;
    if (!versionId) {
      setError("Choose a network version before running a simulation.");
      return;
    }
    setBusy(true);
    setError(null);
    setComparison(null);
    try {
      const run = await api<SimulationRun>("/api/v1/simulations", {
        method: "POST",
        body: JSON.stringify({
          network_version_id: versionId,
          scenario_id: runTarget === "scenario" ? scenario?.id : null,
          engine,
          demand_profile_id: profile,
          seed,
          repeats: 1,
        }),
      });
      if (runTarget === "scenario") {
        setScenarioRun(run);
      } else {
        setBaselineRun(run);
      }
      const logs = await api<{ log: string }>(`/api/v1/simulations/${run.id}/logs`);
      setLogText(logs.log);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The simulation request failed.");
    } finally {
      setBusy(false);
    }
  }

  async function cancelRun() {
    if (!activeRun) {
      return;
    }
    try {
      const run = await api<SimulationRun>(`/api/v1/simulations/${activeRun.id}/cancel`, { method: "POST" });
      if (runTarget === "scenario") {
        setScenarioRun(run);
      } else {
        setBaselineRun(run);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Cancellation failed.");
    }
  }

  async function compare() {
    if (!baselineRun || !scenarioRun) {
      setError("Run the baseline and the scenario before comparing them.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await api<{
        comparability: { comparable: boolean; notes: string[] };
        rows: ComparisonRow[];
      }>(`/api/v1/comparisons?baseline_run_id=${baselineRun.id}&scenario_run_id=${scenarioRun.id}`);
      setComparison({ ...result.comparability, rows: result.rows });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Comparison failed.");
    } finally {
      setBusy(false);
    }
  }

  async function exportReport(format: "html" | "json" | "csv" | "geojson") {
    if (!study) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await api<{ id: string }>("/api/v1/reports", {
        method: "POST",
        body: JSON.stringify({
          study_area_id: study.id,
          scenario_id: scenario?.id || null,
          baseline_run_id: baselineRun?.id || null,
          scenario_run_id: scenarioRun?.id || null,
        }),
      });
      window.open(downloadUrl(`/api/v1/reports/${created.id}/download?format=${format}`), "_blank", "noopener");
    } catch (err) {
      setError(err instanceof Error ? err.message : "The report could not be exported.");
    } finally {
      setBusy(false);
    }
  }

  async function inspect(featureId: string) {
    if (!networkVersionId) {
      return;
    }
    try {
      const detail = await api<Record<string, unknown>>(
        `/api/v1/networks/${networkVersionId}/features/${encodeURIComponent(featureId)}`,
      );
      setFeature(detail);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The feature could not be loaded.");
    }
  }

  const playbackTimes = activeRun?.timesteps?.map((frame) => frame.t_s) || [];

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">UrbanTrafficLab</p>
          <h1>Scenario studio</h1>
        </div>
        <p className="muted top-note">
          Rule-based editing works without an AI key.
          {capabilities?.ai.llm_configured ? " An optional language model is configured on the server." : " No language model is configured."}
        </p>
      </header>

      {loading ? <StatusNotice state="loading" message="Loading the sample study and simulator capabilities." /> : null}
      {error ? <StatusNotice state="error" message={error} /> : null}
      {study?.kind === "synthetic" ? (
        <StatusNotice
          state="info"
          message={`${study.name} is a synthetic sample. It is not a real city and it is not derived from OpenStreetMap. ${study.attribution}`}
        />
      ) : null}
      {study && !study.baseline_network_version_id ? (
        <StatusNotice state="empty" message="This study has no road network yet. Import GeoJSON or OpenStreetMap data." />
      ) : null}

      <section className="layout">
        <aside className="column">
          <section className="panel">
            <h2>Study area</h2>
            <label>
              Current study
              <select value={study?.id || ""} onChange={(event) => void selectStudy(event.target.value)}>
                {studies.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <div className="row-actions">
              <button type="button" onClick={() => void loadSample()} disabled={busy}>
                Load synthetic sample
              </button>
            </div>
            <label>
              New study name
              <input value={studyName} onChange={(event) => setStudyName(event.target.value)} />
            </label>
            <button type="button" onClick={() => void createStudy()} disabled={busy}>
              Create empty study
            </button>
            <label>
              GeoJSON baseline
              <input
                type="file"
                accept="application/geo+json,application/json,.geojson,.json"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) {
                    void importGeoJson(file);
                  }
                }}
              />
            </label>
            <label>
              OSM bbox (west,south,east,north)
              <input value={bbox} onChange={(event) => setBbox(event.target.value)} />
            </label>
            <button type="button" onClick={() => void importOsm()} disabled={busy || Boolean(study?.baseline_network_version_id)}>
              Import OpenStreetMap
            </button>
            <p className="muted">
              A study keeps one immutable baseline. Import is available before that baseline exists. OSM requests use the
              server Overpass setting and can fail when the service is unreachable.
            </p>
          </section>

          <section className="panel">
            <h2>Prompt</h2>
            <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} rows={6} />
            <button type="button" onClick={() => void parsePrompt()} disabled={busy}>
              Parse into operations
            </button>
            {notes.map((note) => (
              <p key={note} className="muted">
                {note}
              </p>
            ))}
            {unparsed.length > 0 ? <p className="warning">Unparsed: {unparsed.join("; ")}</p> : null}
            <OperationList operations={operations} onDecision={setDecision} onSelectCandidate={(id, featureId) => void selectCandidate(id, featureId)} />
            <div className="row-actions">
              <button type="button" onClick={() => void applyScenario()} disabled={busy || operations.length === 0}>
                Apply accepted operations
              </button>
              <button type="button" onClick={() => void rollback()} disabled={busy || !scenario}>
                Roll back
              </button>
              <button type="button" onClick={() => void cloneScenario()} disabled={busy || !scenario}>
                Clone
              </button>
            </div>
            {scenario ? (
              <p className="muted">
                {scenario.name} is {scenario.status}. Baseline hash {shortHash(scenario.baseline_hash)}. Scenario hash{" "}
                {shortHash(scenario.network_hash)}.
              </p>
            ) : null}
          </section>
        </aside>

        <section className="map-column">
          <div className="map-toolbar">
            <label>
              Network
              <select value={view} onChange={(event) => setView(event.target.value as "baseline" | "scenario")}>
                <option value="baseline">Baseline</option>
                <option value="scenario" disabled={!scenario}>
                  Scenario
                </option>
              </select>
            </label>
            <label>
              Playback time (s)
              <select
                value={timestep}
                onChange={(event) => setTimestep(event.target.value === "" ? "" : Number(event.target.value))}
                disabled={playbackTimes.length === 0}
              >
                <option value="">Aggregate</option>
                {playbackTimes.map((time) => (
                  <option key={time} value={time}>
                    {time}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="map-frame">
            {layers ? <MapCanvas layers={layers} onSelect={(featureId) => void inspect(featureId)} /> : <StatusNotice state="empty" message="No map layer is loaded." />}
            {layers ? (
              <aside className="legend">
                <h3>Legend</h3>
                <p>{layers.data_note}</p>
                <p className="muted">{layers.legend.metric}</p>
                <ul>
                  {layers.legend.stops.map((stop) => (
                    <li key={stop.label}>
                      <i style={{ background: stop.color }} />
                      {stop.label}
                    </li>
                  ))}
                </ul>
                <p className="muted">{layers.attribution}</p>
              </aside>
            ) : null}
          </div>
          <section className="panel">
            <h2>Selected feature</h2>
            {feature ? <FeatureFacts feature={feature} /> : <p className="muted">Select a road or intersection on the map.</p>}
          </section>
        </section>

        <aside className="column">
          <section className="panel">
            <h2>Run settings</h2>
            <label>
              Run against
              <select value={runTarget} onChange={(event) => setRunTarget(event.target.value as "baseline" | "scenario")}>
                <option value="baseline">Baseline</option>
                <option value="scenario" disabled={!scenario}>
                  Scenario
                </option>
              </select>
            </label>
            <label>
              Demand profile
              <select value={profile} onChange={(event) => setProfile(event.target.value)}>
                <option value="morning_peak">Morning peak (synthetic)</option>
                <option value="evening_peak">Evening peak (synthetic)</option>
              </select>
            </label>
            <label>
              Seed
              <input type="number" min={0} value={seed} onChange={(event) => setSeed(Number(event.target.value))} />
            </label>
          </section>
          <SimulationPanel
            capabilities={capabilities}
            run={activeRun}
            busy={busy}
            onEstimate={() => void runSimulation("estimator")}
            onSumo={() => void runSimulation("sumo")}
            onCancel={() => void cancelRun()}
          />
          <section className="panel">
            <h2>Comparison</h2>
            <button type="button" onClick={() => void compare()} disabled={busy || !baselineRun || !scenarioRun}>
              Compare baseline and scenario
            </button>
            {comparison ? (
              <>
                <p>{comparison.comparable ? "These runs use comparable settings." : "These runs are not equivalent."}</p>
                <ul className="warning-list">
                  {comparison.notes.map((note) => (
                    <li key={note}>{note}</li>
                  ))}
                </ul>
              </>
            ) : null}
            <ComparisonChart rows={comparison?.rows || []} />
            <MetricsTable metrics={activeRun?.metrics || null} rows={comparison?.rows || null} />
          </section>
          <section className="panel">
            <h2>Export and provenance</h2>
            <div className="row-actions">
              <button type="button" onClick={() => void exportReport("html")} disabled={busy || (!scenario && !baselineRun)}>
                HTML report
              </button>
              <button type="button" onClick={() => void exportReport("csv")} disabled={busy || (!scenario && !baselineRun)}>
                CSV
              </button>
              <button type="button" onClick={() => void exportReport("json")} disabled={busy || (!scenario && !baselineRun)}>
                JSON
              </button>
              <button type="button" onClick={() => void exportReport("geojson")} disabled={busy || (!scenario && !baselineRun)}>
                GeoJSON
              </button>
            </div>
            <h3>Assumptions</h3>
            <ul>
              {(activeRun?.config.assumptions || ["Demand assumptions appear after a run."]).map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
            <h3>History</h3>
            {history.length === 0 ? <p className="muted">Scenario events appear after a scenario is saved.</p> : null}
            <ul>
              {history.map((event) => (
                <li key={`${event.kind}-${event.created_at}`}>
                  {event.kind} · {event.created_at}
                </li>
              ))}
            </ul>
            <h3>Logs</h3>
            <pre className="log">{logText || "Simulation logs appear after a run. Paths and secrets are redacted."}</pre>
          </section>
        </aside>
      </section>
    </main>
  );
}

function FeatureFacts({ feature }: { feature: Record<string, unknown> }) {
  const entries = Object.entries(feature).filter(([, value]) => value !== null && typeof value !== "object");
  return (
    <dl className="facts">
      {entries.map(([key, value]) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd>{typeof value === "number" ? formatNumber(value, 3) : String(value)}</dd>
        </div>
      ))}
      {Array.isArray(feature.directed_edges) ? (
        <div>
          <dt>directed edges</dt>
          <dd>{(feature.directed_edges as { edge_id: string }[]).map((edge) => edge.edge_id).join(", ")}</dd>
        </div>
      ) : null}
    </dl>
  );
}
