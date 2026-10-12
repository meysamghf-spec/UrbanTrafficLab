export type ApiErrorBody = {
  error?: { code?: string; message?: string; details?: unknown[] };
};

export type Study = {
  id: string;
  slug: string;
  name: string;
  description: string;
  kind: string;
  bbox: [number, number, number, number] | null;
  attribution: string;
  baseline_network_version_id: string | null;
  real_city: boolean;
};

export type NetworkSummary = {
  id: string;
  study_area_id: string;
  label: string;
  role: string;
  immutable: boolean;
  content_hash: string;
  provenance: Record<string, unknown>;
  stats: Record<string, number>;
  bbox: [number, number, number, number] | null;
};

export type Issue = { code: string; message: string };

export type Operation = {
  id: string;
  operation_type: string;
  target: { label?: string | null; feature_id?: string | null; coordinates?: number[] | null };
  parameters: Record<string, unknown>;
  validation_status: string;
  warnings: Issue[];
  errors: Issue[];
  candidates: { feature_id?: string; id?: string; name?: string; label?: string }[];
  decision: string;
  simplified?: boolean;
  expected_effects?: string[];
  apply_status?: string;
  source?: { kind?: string };
};

export type Scenario = {
  id: string;
  name: string;
  status: string;
  prompt: string;
  baseline_version_id: string;
  network_version_id: string;
  baseline_hash: string;
  network_hash: string;
  operations: Operation[];
  validation: { ok?: boolean; message?: string; integrity_warnings?: Issue[] };
  version_history: string[];
};

export type Metric = {
  key: string;
  label: string;
  unit: string;
  value: number | null;
  missing: boolean;
  quality: string;
  notes?: string[];
};

export type SimulationRun = {
  id: string;
  network_version_id: string;
  status: string;
  quality: string;
  result_usability: string;
  engine: string;
  seed: number;
  metrics: Record<string, Metric> | null;
  warnings: Issue[];
  error: string | null;
  blocked_reason: string | null;
  estimate_available: boolean;
  provenance: Record<string, unknown>;
  playback_available: boolean;
  timesteps: { t_s: number }[];
  duration_s: number | null;
  exit_code: number | null;
  config: { assumptions?: string[]; vehicle_count?: number; comparability?: Record<string, unknown> };
};

export type ComparisonRow = {
  key: string;
  label: string;
  unit: string;
  baseline: number | null;
  scenario: number | null;
  absolute_difference: number | null;
  relative_difference: number | null;
  relative_note: string | null;
  baseline_quality: string | null;
  scenario_quality: string | null;
};

export type FeatureCollection = {
  type: "FeatureCollection";
  features: {
    type: "Feature";
    id?: string | number;
    geometry: { type: string; coordinates: number[] | number[][] | number[][][] };
    properties: Record<string, unknown>;
  }[];
};

export type MapLayers = {
  roads: FeatureCollection;
  nodes: FeatureCollection;
  changes: FeatureCollection;
  bbox: [number, number, number, number] | null;
  attribution: string;
  data_note: string;
  quality: string | null;
  synthetic: boolean;
  legend: {
    metric: string;
    unit: string;
    quality: string | null;
    stops: { label: string; color: string }[];
  };
};

export type Capabilities = {
  sumo: { available: boolean; version: string | null; reason: string | null };
  estimator: { available: boolean; label: string; description: string };
  ai: { rule_based: boolean; llm_configured: boolean; provider: string };
  map: { tile_url_configured: boolean; note: string };
  auth: { enabled: boolean; note: string };
};
