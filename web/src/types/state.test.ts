import { describe, expect, it } from "vitest";
import { totalTokenCount, type CostSummary } from "./state";

describe("totalTokenCount", () => {
  it("sums input + output from the breakdown object", () => {
    const cs: CostSummary = {
      total_tokens: { input: 42047, output: 9353, cache_read: 0, cache_write: 0 },
    };
    expect(totalTokenCount(cs)).toBe(51400);
  });

  it("never returns NaN for a breakdown object (the original bug)", () => {
    const cs = {
      total_tokens: { input: 100, output: 50 },
    } as CostSummary;
    const tokens = totalTokenCount(cs);
    expect(Number.isNaN(tokens)).toBe(false);
    expect((tokens / 1000).toFixed(0)).toBe("0");
  });

  it("accepts a legacy bare number", () => {
    expect(totalTokenCount({ total_tokens: 12345 })).toBe(12345);
  });

  it("returns 0 for missing / null / malformed data", () => {
    expect(totalTokenCount(null)).toBe(0);
    expect(totalTokenCount(undefined)).toBe(0);
    expect(totalTokenCount({})).toBe(0);
    expect(totalTokenCount({ total_tokens: null })).toBe(0);
    expect(totalTokenCount({ total_tokens: Number.NaN })).toBe(0);
  });
});
