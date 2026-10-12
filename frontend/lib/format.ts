export function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "—";
  }
  if (Number.isInteger(value)) {
    return String(value);
  }
  return value.toFixed(digits);
}

export function formatRelative(value: number | null | undefined, note: string | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return note || "—";
  }
  const percent = value * 100;
  const sign = percent > 0 ? "+" : "";
  return `${sign}${percent.toFixed(1)}%`;
}

export function shortHash(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  return value.slice(0, 12);
}
