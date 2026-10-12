import { describe, expect, it } from "vitest";

import { formatNumber, formatRelative } from "@/lib/format";

describe("format", () => {
  it("keeps missing values blank and avoids extra precision", () => {
    expect(formatNumber(null)).toBe("—");
    expect(formatNumber(undefined)).toBe("—");
    expect(formatNumber(12)).toBe("12");
    expect(formatNumber(12.3456)).toBe("12.35");
  });

  it("renders relative change and zero-baseline notes", () => {
    expect(formatRelative(0.1, null)).toBe("+10.0%");
    expect(formatRelative(-0.25, null)).toBe("-25.0%");
    expect(formatRelative(null, "Relative difference is undefined because the baseline value is zero.")).toContain(
      "zero",
    );
  });
});
