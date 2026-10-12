import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { OperationList } from "@/components/OperationList";
import { SimulationPanel } from "@/components/SimulationPanel";
import { StatusNotice } from "@/components/StatusNotice";
import type { Capabilities, Operation, SimulationRun } from "@/lib/types";

const capabilities: Capabilities = {
  sumo: { available: false, version: null, reason: "SUMO executable was not found." },
  estimator: { available: true, label: "ESTIMATED", description: "Static assignment." },
  ai: { rule_based: true, llm_configured: false, provider: "none" },
  map: { tile_url_configured: false, note: "No tiles." },
  auth: { enabled: false, note: "No authentication." },
};

const blocked: SimulationRun = {
  id: "run1",
  network_version_id: "net1",
  status: "BLOCKED_MISSING_DEPENDENCY",
  quality: "NONE",
  result_usability: "none",
  engine: "sumo",
  seed: 42,
  metrics: null,
  warnings: [],
  error: "SUMO executable was not found.",
  blocked_reason: "SUMO executable was not found.",
  estimate_available: true,
  provenance: {},
  playback_available: false,
  timesteps: [],
  duration_s: null,
  exit_code: null,
  config: {},
};

describe("status and simulation panels", () => {
  it("shows loading and error states", () => {
    const { unmount } = render(<StatusNotice state="loading" message="Loading the sample network." />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading the sample network.");
    unmount();
    render(<StatusNotice state="error" message="The API is unreachable." />);
    expect(screen.getByRole("alert")).toHaveTextContent("The API is unreachable.");
  });

  it("shows the blocked SUMO reason and an estimate action", () => {
    const onEstimate = vi.fn();
    render(
      <SimulationPanel
        capabilities={capabilities}
        run={blocked}
        busy={false}
        onEstimate={onEstimate}
        onSumo={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("SUMO executable was not found.");
    fireEvent.click(screen.getByRole("button", { name: "Run an explicitly labeled estimate" }));
    expect(onEstimate).toHaveBeenCalledOnce();
  });
});

describe("operation review", () => {
  it("shows warnings and ambiguous candidates", async () => {
    const onSelect = vi.fn();
    const operations: Operation[] = [
      {
        id: "op1",
        operation_type: "ADD_TRAFFIC_SIGNAL",
        target: { label: "J2", feature_id: "J2" },
        parameters: {},
        validation_status: "valid_with_warnings",
        warnings: [{ code: "simplified_model", message: "The signal is a fixed-time placeholder." }],
        errors: [],
        candidates: [],
        decision: "pending",
      },
      {
        id: "op2",
        operation_type: "MODIFY_ROAD_DIRECTION",
        target: { label: "Main Street", feature_id: null },
        parameters: { direction: "eastbound" },
        validation_status: "ambiguous",
        warnings: [],
        errors: [{ code: "ambiguous", message: "Main Street matches more than one road." }],
        candidates: [
          { feature_id: "R1", name: "Main Street" },
          { feature_id: "R2", name: "Main Street" },
        ],
        decision: "pending",
      },
    ];
    render(<OperationList operations={operations} onDecision={vi.fn()} onSelectCandidate={onSelect} />);
    expect(screen.getByText(/fixed-time placeholder/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /R1/ }));
    expect(onSelect).toHaveBeenCalledWith("op2", "R1");
  });
});
