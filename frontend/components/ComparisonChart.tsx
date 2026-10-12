"use client";

import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { formatNumber } from "@/lib/format";
import type { ComparisonRow } from "@/lib/types";

export function ComparisonChart({ rows }: { rows: ComparisonRow[] }) {
  const chartRows = rows.filter(
    (row) =>
      (row.key === "mean_travel_time_s" || row.key === "mean_speed_kmh") &&
      (row.baseline !== null || row.scenario !== null),
  );
  if (chartRows.length === 0) {
    return (
      <div className="notice notice-empty" role="status">
        <span className="notice-label">empty</span>
        <p>Comparison charts appear after two usable runs exist.</p>
      </div>
    );
  }
  return (
    <div className="chart" aria-label="Baseline and scenario comparison">
      <ResponsiveContainer width="100%" height={240}>
        <BarChart data={chartRows}>
          <CartesianGrid stroke="#d5e0e6" vertical={false} />
          <XAxis dataKey="label" tick={{ fill: "#234", fontSize: 12 }} />
          <YAxis tick={{ fill: "#234", fontSize: 12 }} />
          <Tooltip formatter={(value) => formatNumber(typeof value === "number" ? value : null)} />
          <Legend />
          <Bar dataKey="baseline" name="Baseline" fill="#1f6f8b" />
          <Bar dataKey="scenario" name="Scenario" fill="#d9782d" />
        </BarChart>
      </ResponsiveContainer>
      <p className="muted">Units stay on each metric. Bars are not normalized across different units.</p>
    </div>
  );
}
