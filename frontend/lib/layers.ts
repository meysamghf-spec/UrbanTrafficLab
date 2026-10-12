export const NO_RESULT_COLOR = "#8aa0b8";

const STOPS: { min: number; color: string }[] = [
  { min: 0.8, color: "#3ddc97" },
  { min: 0.6, color: "#b6e36a" },
  { min: 0.4, color: "#ffb020" },
  { min: 0.2, color: "#ff7a45" },
  { min: Number.NEGATIVE_INFINITY, color: "#ff5d73" },
];

export function speedRatio(
  simulatedSpeed: number | null | undefined,
  freeFlowSpeed: number | null | undefined,
): number | null {
  if (simulatedSpeed === null || simulatedSpeed === undefined) {
    return null;
  }
  if (freeFlowSpeed === null || freeFlowSpeed === undefined || freeFlowSpeed === 0) {
    return null;
  }
  return simulatedSpeed / freeFlowSpeed;
}

export function colorForSpeedRatio(ratio: number | null): string {
  if (ratio === null || Number.isNaN(ratio)) {
    return NO_RESULT_COLOR;
  }
  const stop = STOPS.find((item) => ratio >= item.min);
  return stop ? stop.color : NO_RESULT_COLOR;
}

export function trafficAvailabilityNote(hasMetric: boolean, quality: string | null | undefined): string {
  if (!hasMetric || !quality || quality === "NONE") {
    return "Traffic results are unavailable.";
  }
  if (quality === "ESTIMATED") {
    return "Link colors use ESTIMATED speeds. They are not SUMO results and not observed speeds.";
  }
  return "Link colors use speeds parsed from SUMO edge data.";
}

export type RoadPaint = {
  color: string;
  hasMetric: boolean;
};

export function paintRoad(properties: {
  simulated_speed_kmh?: number | null;
  free_flow_speed_kmh?: number | null;
  has_metric?: boolean;
}): RoadPaint {
  const hasMetric = Boolean(properties.has_metric) && properties.simulated_speed_kmh != null;
  return {
    hasMetric,
    color: colorForSpeedRatio(speedRatio(properties.simulated_speed_kmh, properties.free_flow_speed_kmh)),
  };
}
