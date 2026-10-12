import type { Operation } from "@/lib/types";

export function OperationList({
  operations,
  onDecision,
  onSelectCandidate,
}: {
  operations: Operation[];
  onDecision: (id: string, decision: "accepted" | "rejected" | "pending") => void;
  onSelectCandidate: (id: string, featureId: string) => void;
}) {
  if (operations.length === 0) {
    return <StatusEmpty message="No structured operations yet. Parse a supported command to review a change plan." />;
  }
  return (
    <ol className="operations">
      {operations.map((operation) => (
        <li key={operation.id} className={`operation status-${operation.validation_status}`}>
          <header>
            <strong>{operation.operation_type}</strong>
            <span>{operation.validation_status.replaceAll("_", " ")}</span>
          </header>
          <p>
            Target: {operation.target.feature_id || operation.target.label || "unspecified"}
            {operation.simplified ? " · simplified model" : ""}
          </p>
          <p className="muted">Decision: {operation.decision}</p>
          {operation.warnings.map((warning) => (
            <p key={warning.code} className="warning">
              Warning: {warning.message}
            </p>
          ))}
          {operation.errors.map((error) => (
            <p key={error.code} className="error-text">
              {error.message}
            </p>
          ))}
          {operation.validation_status === "ambiguous" && operation.candidates.length > 0 ? (
            <div className="candidates">
              <p>Select the matching feature. Nothing is edited until you do.</p>
              {operation.candidates.map((candidate) => {
                const featureId = candidate.feature_id || candidate.id || "";
                return (
                  <button key={featureId} type="button" onClick={() => onSelectCandidate(operation.id, featureId)}>
                    {featureId}
                    {candidate.name ? ` · ${candidate.name}` : ""}
                  </button>
                );
              })}
            </div>
          ) : null}
          <div className="row-actions">
            <button type="button" onClick={() => onDecision(operation.id, "accepted")}>
              Accept
            </button>
            <button type="button" onClick={() => onDecision(operation.id, "rejected")}>
              Reject
            </button>
            <button type="button" onClick={() => onDecision(operation.id, "pending")}>
              Reset
            </button>
          </div>
        </li>
      ))}
    </ol>
  );
}

function StatusEmpty({ message }: { message: string }) {
  return (
    <div className="notice notice-empty" role="status">
      <span className="notice-label">empty</span>
      <p>{message}</p>
    </div>
  );
}
