/**
 * Fix 9: the Report view used to fire its ``fetch(/runs/<id>/merge_report_*.md)``
 * the moment any snapshot.runId was known, then crash with
 * "report not found (server returned HTML)" because the static server
 * falls back to the SPA index for missing artifacts. When the run is
 * still in flight (judge_reviewing, auto_merging, ...), the report
 * doesn't exist yet — but the user sees a misleading error.
 *
 * These tests pin the new behaviour:
 *   - non-terminal status → no fetch, friendly in-progress message
 *   - terminal status + 404 / HTML fallback → existing "report not
 *     found" error path still fires
 *   - terminal status + 200 markdown → markdown renders
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, waitFor } from "@testing-library/react";
import { useRunStore } from "../store/runStore";
import type { MergeStateSnapshot } from "../types/state";
import { Report } from "./Report";

const baseSnapshot: MergeStateSnapshot = {
  runId: "abc-123",
  status: "judge_reviewing",
  currentPhase: "judge_review",
  phaseResults: {},
  mergePlan: null,
  fileClassifications: {},
  fileDiffs: [],
  fileDecisionRecords: {},
  humanDecisionRequests: {},
  humanDecisions: {},
  judgeVerdict: null,
  judgeRepairRounds: 0,
  planReviewLog: [],
  reviewConclusion: null,
  pendingUserDecisions: [],
  gateHistory: [],
  errors: [],
  messages: [],
  memory: { phase_summaries: {}, entries: [] },
  createdAt: "2026-05-14T00:00:00",
};

const fetchSpy = vi.fn<typeof fetch>();

beforeEach(() => {
  fetchSpy.mockReset();
  vi.stubGlobal("fetch", fetchSpy);
  useRunStore.setState({
    conn: "open",
    snapshot: baseSnapshot,
    activity: [],
    lastCancelError: null,
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Report view (Fix 9)", () => {
  it("does NOT fetch while run is in flight", async () => {
    useRunStore.setState({
      snapshot: { ...baseSnapshot, status: "auto_merging" },
    });
    const { container } = render(<Report />);
    // Give any rogue effects a chance to fire.
    await new Promise((r) => setTimeout(r, 10));
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(container.textContent).toMatch(
      /Report will be generated once the run completes/i,
    );
    expect(container.textContent).toContain("auto_merging");
  });

  it("fetches once run reaches completed", async () => {
    fetchSpy.mockResolvedValue(
      new Response("# Final report\n\nAll good.", {
        status: 200,
        headers: { "content-type": "text/markdown" },
      }),
    );
    useRunStore.setState({
      snapshot: { ...baseSnapshot, status: "completed" },
    });
    const { container } = render(<Report />);
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledTimes(1);
    });
    expect(fetchSpy.mock.calls[0][0]).toContain("/runs/abc-123/merge_report_abc-123.md");
    await waitFor(() => {
      expect(container.textContent).toContain("Final report");
    });
  });

  it("fetches once run reaches failed", async () => {
    fetchSpy.mockResolvedValue(
      new Response("# Failure report", {
        status: 200,
        headers: { "content-type": "text/markdown" },
      }),
    );
    useRunStore.setState({
      snapshot: { ...baseSnapshot, status: "failed" },
    });
    render(<Report />);
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledTimes(1);
    });
  });

  it("terminal status + HTML fallback still surfaces the 'report not found' error", async () => {
    fetchSpy.mockResolvedValue(
      new Response("<!DOCTYPE html><html><body>SPA</body></html>", {
        status: 200,
        headers: { "content-type": "text/html" },
      }),
    );
    useRunStore.setState({
      snapshot: { ...baseSnapshot, status: "completed" },
    });
    const { container } = render(<Report />);
    await waitFor(() => {
      expect(container.textContent).toMatch(/report not found/i);
    });
  });
});
