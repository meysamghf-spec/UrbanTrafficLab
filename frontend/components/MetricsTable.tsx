import { formatNumber, formatRelative } from "@/lib/format";
import type { ComparisonRow, Metric } from "@/lib/types";

export function MetricsTable({
  metrics,
  rows,
}: {
  metrics: Record<string, Metric> | null;
  rows: ComparisonRow[] | null;
}) {
  if (!metrics && !rows) {
    return (
      <div className="notice notice-empty" role="status">
        <span className="notice-label">empty</span>
        <p>Road and network metrics appear after a simulation or estimate finishes.</p>
      </div>
    );
  }
  const source = rows
    ? rows
    : Object.values(metrics || {}).map((metric) => ({
        key: metric.key,
        label: metric.label,
        unit: metric.unit,
        baseline: metric.missing ? null : metric.value,
        scenario: null,
        absolute_difference: null,
        relative_difference: null,
        relative_note: metric.missing ? "Value is missing." : null,
        baseline_quality: metric.quality,
        scenario_quality: null,
      }));
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Metric</th>
            <th>Baseline</th>
            <th>Scenario</th>
            <th>Absolute</th>
            <th>Relative</th>
            <th>Quality</th>
          </tr>
        </thead>
        <tbody>
          {source.map((row) => (
            <tr key={row.key}>
              <td>
                {row.label}
                <span className="unit"> {row.unit}</span>
              </td>
              <td>{formatNumber(row.baseline)}</td>
              <td>{rows ? formatNumber(row.scenario) : "—"}</td>
              <td>{formatNumber(row.absolute_difference)}</td>
              <td>{formatRelative(row.relative_difference, row.relative_note)}</td>
              <td>{row.baseline_quality || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
