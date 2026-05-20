/**
 * H3 hotfix front-end coverage: when the user submits a single MANUAL_PATCH
 * decision or a batch of drafts, the outgoing WS frame must include
 * ``reviewer_notes`` and ``custom_content``. M10: ``submit_decision``
 * payload uses ``filePath`` (legacy camelCase) by convention here, but
 * the back-end accepts both — see ``test_ws_bridge_h3_reviewer_fields.py``.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render } from "@testing-library/react";
import { useRunStore } from "../store/runStore";
import { useConflictDraftStore } from "../store/conflictDraftStore";
import { useWsClient } from "../ws/useWsClient";
import type { MergeStateSnapshot } from "../types/state";
import type { OutboundMessage, InboundMessage } from "../ws/messages";
import { ConflictResolution } from "./ConflictResolution";

// Mock useWsClient so we can spy on the outgoing send() payload while
// keeping all the production wiring (store dispatch, classifyView, etc).
const sendSpy = vi.fn<(msg: OutboundMessage) => void>();
vi.mock("../ws/useWsClient", () => ({
  useWsClient: vi.fn(),
}));

const baseSnapshot: MergeStateSnapshot = {
  runId: "run-1",
  status: "awaiting_human",
  currentPhase: "human_review",
  phaseResults: {},
  mergePlan: null,
  fileClassifications: {},
  fileDiffs: [],
  fileDecisionRecords: {},
  humanDecisionRequests: {
    "a.py": {
      request_id: "r-a",
      file_path: "a.py",
      priority: 5,
      conflict_points: [],
      context_summary: "ctx",
      upstream_change_summary: "u",
      fork_change_summary: "f",
      analyst_recommendation: "take_current",
      analyst_confidence: 0.8,
      analyst_rationale: "rat",
      options: [],
      human_decision: null,
      custom_content: null,
      reviewer_notes: null,
      related_files: [],
    },
    "b.py": {
      request_id: "r-b",
      file_path: "b.py",
      priority: 3,
      conflict_points: [],
      context_summary: "ctx",
      upstream_change_summary: "u",
      fork_change_summary: "f",
      analyst_recommendation: "take_target",
      analyst_confidence: 0.7,
      analyst_rationale: "rat",
      options: [],
      human_decision: null,
      custom_content: null,
      reviewer_notes: null,
      related_files: [],
    },
  },
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

beforeEach(() => {
  sendSpy.mockClear();
  useRunStore.setState({
    conn: "open",
    snapshot: baseSnapshot,
    activity: [],
    lastCancelError: null,
  });
  useConflictDraftStore.setState({ drafts: {}, selectedFile: null });
  vi.mocked(useWsClient).mockReturnValue({
    current: {
      send: sendSpy,
      close: vi.fn(),
      pendingCount: () => 0,
    },
  } as unknown as React.MutableRefObject<{
    send: (msg: OutboundMessage) => void;
    close: () => void;
    pendingCount: () => number;
  }>);
});

function makeClientRef(): React.MutableRefObject<{
  send: (msg: OutboundMessage) => void;
  close: () => void;
  pendingCount: () => number;
} | null> {
  return {
    current: {
      send: sendSpy,
      close: vi.fn(),
      pendingCount: () => 0,
    },
  };
}

// Suppress the InboundMessage import being unused
void ({} as InboundMessage);

describe("ConflictResolution submit payload (H3)", () => {
  it("single submit_decision includes reviewer_notes + custom_content", () => {
    act(() => {
      const store = useConflictDraftStore.getState();
      store.setDraftDecision("a.py", "manual_patch");
      store.setDraftNotes("a.py", "patch reviewed locally");
      store.setDraftCustomContent("a.py", "--- a/x\n+++ b/x\n");
      store.selectFile("a.py");
    });

    const ref = makeClientRef();
    const { getByText } = render(
      <ConflictResolution
        clientRef={
          ref as unknown as React.MutableRefObject<
            ReturnType<typeof useWsClient>["current"]
          >
        }
      />,
    );
    act(() => {
      getByText("Submit decision").click();
    });

    expect(sendSpy).toHaveBeenCalledTimes(1);
    const msg = sendSpy.mock.calls[0][0];
    expect(msg.type).toBe("submit_decision");
    if (msg.type !== "submit_decision") return;
    expect(msg.payload.filePath).toBe("a.py");
    expect(msg.payload.decision).toBe("manual_patch");
    expect(msg.payload.reviewer_notes).toBe("patch reviewed locally");
    expect(msg.payload.custom_content).toBe("--- a/x\n+++ b/x\n");
  });

  it("single submit_decision sends null for empty optional fields", () => {
    act(() => {
      const store = useConflictDraftStore.getState();
      store.setDraftDecision("a.py", "take_current");
      store.selectFile("a.py");
    });

    const ref = makeClientRef();
    const { getByText } = render(
      <ConflictResolution
        clientRef={
          ref as unknown as React.MutableRefObject<
            ReturnType<typeof useWsClient>["current"]
          >
        }
      />,
    );
    act(() => {
      getByText("Submit decision").click();
    });

    expect(sendSpy).toHaveBeenCalledTimes(1);
    const msg = sendSpy.mock.calls[0][0];
    if (msg.type !== "submit_decision") throw new Error("unexpected type");
    expect(msg.payload.reviewer_notes).toBeNull();
    expect(msg.payload.custom_content).toBeNull();
  });

  it("submit_all_drafts batch carries per-item reviewer_notes + custom_content", () => {
    act(() => {
      const store = useConflictDraftStore.getState();
      store.setDraftDecision("a.py", "manual_patch");
      store.setDraftCustomContent("a.py", "patch-A");
      store.setDraftNotes("a.py", "note-A");
      store.setDraftDecision("b.py", "take_target");
      // b.py left without reviewer_notes / custom_content — must
      // serialise as null per item.
      store.selectFile("a.py");
    });

    const ref = makeClientRef();
    const { getByText } = render(
      <ConflictResolution
        clientRef={
          ref as unknown as React.MutableRefObject<
            ReturnType<typeof useWsClient>["current"]
          >
        }
      />,
    );
    act(() => {
      getByText("Submit all drafts (2)").click();
    });

    expect(sendSpy).toHaveBeenCalledTimes(1);
    const msg = sendSpy.mock.calls[0][0];
    expect(msg.type).toBe("submit_conflict_decisions_batch");
    if (msg.type !== "submit_conflict_decisions_batch") return;
    expect(msg.payload.items).toHaveLength(2);
    const aItem = msg.payload.items.find((i) => i.file_path === "a.py")!;
    const bItem = msg.payload.items.find((i) => i.file_path === "b.py")!;
    expect(aItem.decision).toBe("manual_patch");
    expect(aItem.reviewer_notes).toBe("note-A");
    expect(aItem.custom_content).toBe("patch-A");
    expect(bItem.decision).toBe("take_target");
    expect(bItem.reviewer_notes).toBeNull();
    expect(bItem.custom_content).toBeNull();
  });
});

describe("ConflictResolution status pill (Fix 8)", () => {
  it("shows AWAITING_HUMAN suffix when status === 'awaiting_human'", () => {
    useRunStore.setState({ snapshot: { ...baseSnapshot, status: "awaiting_human" } });
    const ref = makeClientRef();
    const { container } = render(
      <ConflictResolution
        clientRef={
          ref as unknown as React.MutableRefObject<
            ReturnType<typeof useWsClient>["current"]
          >
        }
      />,
    );
    expect(container.textContent).toContain("AWAITING_HUMAN");
  });

  it("pill reflects judge_reviewing when stale snapshot lingers past plan_review", () => {
    useRunStore.setState({ snapshot: { ...baseSnapshot, status: "judge_reviewing" } });
    const ref = makeClientRef();
    const { container } = render(
      <ConflictResolution
        clientRef={
          ref as unknown as React.MutableRefObject<
            ReturnType<typeof useWsClient>["current"]
          >
        }
      />,
    );
    expect(container.textContent).toContain("JUDGE_REVIEWING");
    const pillCandidates = Array.from(
      container.querySelectorAll<HTMLElement>("[class*='pill']"),
    ).map((el) => el.textContent ?? "");
    expect(pillCandidates.some((t) => /AWAITING_HUMAN/.test(t))).toBe(false);
  });

  it("pill reflects auto_merging when orchestrator is mid-merge", () => {
    useRunStore.setState({ snapshot: { ...baseSnapshot, status: "auto_merging" } });
    const ref = makeClientRef();
    const { container } = render(
      <ConflictResolution
        clientRef={
          ref as unknown as React.MutableRefObject<
            ReturnType<typeof useWsClient>["current"]
          >
        }
      />,
    );
    expect(container.textContent).toContain("AUTO_MERGING");
  });
});
