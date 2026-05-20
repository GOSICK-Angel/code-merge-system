import { create } from "zustand";
import type {
  AgentActivityEvent,
  MergeStateSnapshot,
  ProviderName,
  SetupContext,
  SetupError,
  SetupReady,
  SetupTestResult,
} from "../types/state";
import type { StoreMode } from "../lib/classifyView";
import type { ConnState, DropReason } from "../ws/client";

const ACTIVITY_MAX = 200;

export type SetupStatus = "idle" | "submitting" | "ready" | "error";

export interface SetupTestState {
  status: "testing" | "done";
  result: SetupTestResult | null;
}

export interface PendingOutboundState {
  /** Frames currently buffered in the WS client (post-onDrop snapshot). */
  queued: number;
  /** Most recent drop wall-clock time (ms epoch); null when never dropped. */
  lastDropAt: number | null;
  /** Most recent drop's outbound message type, for surfacing in the UI. */
  lastDropType: string | null;
  /** Most recent drop reason — distinguishes "queued during reconnect"
   * (recoverable on next onopen) from "queue overflow" (data lost). */
  lastDropReason: DropReason | null;
}

interface RunStoreState {
  conn: ConnState;
  mode: StoreMode;
  snapshot: MergeStateSnapshot | null;
  activity: AgentActivityEvent[];
  lastCancelError: { reason: string; current_status: string } | null;
  pendingOutbound: PendingOutboundState;
  setupContext: SetupContext | null;
  setupStatus: SetupStatus;
  setupReady: SetupReady | null;
  setupError: SetupError | null;
  setupTestResults: Partial<Record<ProviderName, SetupTestState>>;
  setConn: (s: ConnState) => void;
  applySnapshot: (s: MergeStateSnapshot) => void;
  appendActivity: (e: AgentActivityEvent) => void;
  replaceActivity: (events: AgentActivityEvent[]) => void;
  setCancelError: (e: { reason: string; current_status: string }) => void;
  clearCancelError: () => void;
  recordOutboundDrop: (event: {
    reason: DropReason;
    type: string;
    queuedCount: number;
    at: number;
  }) => void;
  recordOutboundFlush: (event: {
    flushedCount: number;
    remainingCount: number;
    at: number;
  }) => void;
  clearPendingOutbound: () => void;
  applySetupSnapshot: (ctx: SetupContext) => void;
  markSetupSubmitting: () => void;
  applySetupReady: (ready: SetupReady) => void;
  applySetupError: (err: SetupError) => void;
  markSetupTesting: (provider: ProviderName) => void;
  applySetupTestResult: (result: SetupTestResult) => void;
}

export const useRunStore = create<RunStoreState>((set) => ({
  conn: "connecting",
  // Default to "run" so a session that connects straight into an
  // in-flight merge keeps rendering the dashboard without needing an
  // initial setup_snapshot. The bridge switches us into "setup" the
  // moment the first setup_snapshot lands.
  mode: "run",
  snapshot: null,
  activity: [],
  lastCancelError: null,
  pendingOutbound: {
    queued: 0,
    lastDropAt: null,
    lastDropType: null,
    lastDropReason: null,
  },
  setupContext: null,
  setupStatus: "idle",
  setupReady: null,
  setupError: null,
  setupTestResults: {},
  setConn: (s) => set({ conn: s }),
  // Receiving a state snapshot always means we're in run mode — even if
  // we were mid-setup, the orchestrator has now taken over and the form
  // is no longer authoritative.
  applySnapshot: (s) => set({ snapshot: s, mode: "run" }),
  appendActivity: (e) =>
    set((state) => {
      const next = [...state.activity, e];
      return {
        activity:
          next.length > ACTIVITY_MAX ? next.slice(-ACTIVITY_MAX) : next,
      };
    }),
  replaceActivity: (events) =>
    set({ activity: events.slice(-ACTIVITY_MAX) }),
  setCancelError: (e) => set({ lastCancelError: e }),
  clearCancelError: () => set({ lastCancelError: null }),
  recordOutboundDrop: (event) =>
    set({
      pendingOutbound: {
        queued: event.queuedCount,
        lastDropAt: event.at,
        lastDropType: event.type,
        lastDropReason: event.reason,
      },
    }),
  recordOutboundFlush: (event) =>
    set((state) => ({
      pendingOutbound: {
        queued: event.remainingCount,
        // Preserve the prior drop metadata when there's anything still
        // queued (the user still has an unresolved warning); clear it
        // only when the buffer is fully drained.
        lastDropAt:
          event.remainingCount === 0 ? null : state.pendingOutbound.lastDropAt,
        lastDropType:
          event.remainingCount === 0 ? null : state.pendingOutbound.lastDropType,
        lastDropReason:
          event.remainingCount === 0
            ? null
            : state.pendingOutbound.lastDropReason,
      },
    })),
  clearPendingOutbound: () =>
    set({
      pendingOutbound: {
        queued: 0,
        lastDropAt: null,
        lastDropType: null,
        lastDropReason: null,
      },
    }),
  applySetupSnapshot: (ctx) =>
    set({
      mode: "setup",
      setupContext: ctx,
      setupStatus: "idle",
      setupError: null,
    }),
  markSetupSubmitting: () =>
    set({ setupStatus: "submitting", setupError: null }),
  applySetupReady: (ready) =>
    set({ setupStatus: "ready", setupReady: ready, setupError: null }),
  applySetupError: (err) =>
    set({ setupStatus: "error", setupError: err }),
  markSetupTesting: (provider) =>
    set((state) => ({
      setupTestResults: {
        ...state.setupTestResults,
        [provider]: { status: "testing", result: null },
      },
    })),
  applySetupTestResult: (result) =>
    set((state) => ({
      setupTestResults: {
        ...state.setupTestResults,
        [result.provider]: { status: "done", result },
      },
    })),
}));
