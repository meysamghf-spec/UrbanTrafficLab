import { describe, expect, it } from "vitest";

import { colorForSpeedRatio, paintRoad, speedRatio, trafficAvailabilityNote, NO_RESULT_COLOR } from "@/lib/layers";

describe("map layers", () => {
  it("colors speed ratios and leaves missing metrics neutral", () => {
    expect(speedRatio(40, 50)).toBeCloseTo(0.8);
    expect(colorForSpeedRatio(0.8)).toBe("#3ddc97");
    expect(colorForSpeedRatio(0.1)).toBe("#ff5d73");
    expect(speedRatio(null, 50)).toBeNull();
    expect(speedRatio(10, 0)).toBeNull();
    const painted = paintRoad({ simulated_speed_kmh: null, free_flow_speed_kmh: 50, has_metric: false });
    expect(painted.hasMetric).toBe(false);
    expect(painted.color).toBe(NO_RESULT_COLOR);
  });

  it("states when traffic results are unavailable or estimated", () => {
    expect(trafficAvailabilityNote(false, null)).toBe("Traffic results are unavailable.");
    expect(trafficAvailabilityNote(true, "ESTIMATED")).toContain("ESTIMATED");
    expect(trafficAvailabilityNote(true, "SIMULATED")).toContain("SUMO");
  });
});
