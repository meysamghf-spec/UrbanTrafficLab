import type { Capabilities, SimulationRun } from "@/lib/types";

const TERMINAL = new Set(["SUCCEEDED", "FAILED", "CANCELLED", "BLOCKED_MISSING_DEPENDENCY"]);

export function SimulationPanel({
  capabilities,
  run,
  busy,
  onEstimate,
  onSumo,
  onCancel,
}: {
  capabilities: Capabilities | null;
  run: SimulationRun | null;
  busy: boolean;
  onEstimate: () => void;
  onSumo: () => void;
  onCancel: () => void;
}) {
  const sumoAvailable = capabilities?.sumo.available === true;
  const running = Boolean(run && !TERMINAL.has(run.status));
  return (
    <section className="panel">
      <h2>Simulation</h2>
      <p className="muted">
        {sumoAvailable
          ? `SUMO is available${capabilities?.sumo.version ? ` (${capabilities.sumo.version})` : ""}.`
          : `SUMO is not available. ${capabilities?.sumo.reason || "The server did not report a SUMO executable."}`}
      </p>
      <div className="row-actions">
        <button type="button" onClick={onSumo} disabled={busy || running}>
          Run SUMO
        </button>
        <button type="button" onClick={onEstimate} disabled={busy || running}>
          Run ESTIMATED assignment
        </button>
        <button type="button" onClick={onCancel} disabled={!running}>
          Cancel
        </button>
      </div>
      {!run ? <p className="muted">No simulation has been requested for this network version.</p> : null}
      {run?.status === "BLOCKED_MISSING_DEPENDENCY" ? (
        <div className="notice notice-error" role="alert">
          <span className="notice-label">blocked</span>
          <p>{run.blocked_reason || "SUMO execution is blocked."}</p>
          {run.estimate_available ? (
            <button type="button" onClick={onEstimate}>
              Run an explicitly labeled estimate
            </button>
          ) : null}
        </div>
      ) : null}
      {run && run.status !== "BLOCKED_MISSING_DEPENDENCY" ? (
        <dl className="facts">
          <div>
            <dt>Status</dt>
            <dd>{run.status}</dd>
          </div>
          <div>
            <dt>Quality</dt>
            <dd>{run.quality}</dd>
          </div>
          <div>
            <dt>Usability</dt>
            <dd>{run.result_usability}</dd>
          </div>
          <div>
            <dt>Seed</dt>
            <dd>{run.seed}</dd>
          </div>
        </dl>
      ) : null}
      {run?.error && run.status !== "BLOCKED_MISSING_DEPENDENCY" ? <p className="error-text">{run.error}</p> : null}
      {run?.warnings?.length ? (
        <ul className="warning-list">
          {run.warnings.map((warning) => (
            <li key={`${warning.code}-${warning.message}`}>{warning.message}</li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
