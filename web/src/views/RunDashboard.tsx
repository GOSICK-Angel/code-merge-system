import { useMemo } from "react";
import { useRunStore } from "../store/runStore";
import type { WsClient } from "../ws/client";
import { PhaseTimeline } from "../components/PhaseTimeline";
import { AgentActivityStream } from "../components/AgentActivityStream";
import { CostCard } from "../components/CostCard";
import { DecisionCountsCard } from "../components/DecisionCountsCard";
import { RiskBadge } from "../components/RiskBadge";
import { StatusBanner } from "../components/StatusBanner";

interface Props {
  clientRef: React.MutableRefObject<WsClient | null>;
}

export function RunDashboard({ clientRef }: Props): JSX.Element {
  const conn = useRunStore((s) => s.conn);
  const snapshot = useRunStore((s) => s.snapshot);
  const activity = useRunStore((s) => s.activity);
  const cancelError = useRunStore((s) => s.lastCancelError);
  const clearCancelError = useRunStore((s) => s.clearCancelError);

  const cancelDisabled = snapshot?.status !== "awaiting_human";

  // Both deps are stable: `clearCancelError` is a zustand action (stable
  // identity for the lifetime of the store), and `clientRef` is a React
  // ref object whose identity never changes (its `.current` may).
  const onCancel = useMemo(
    () => () => {
      clearCancelError();
      clientRef.current?.send({ type: "cancel_run", payload: {} });
    },
    [clearCancelError, clientRef],
  );

  return (
    <div className="min-h-screen flex flex-col bg-slate-950 text-slate-100">
      <StatusBanner
        runId={snapshot?.runId ?? null}
        status={snapshot?.status ?? null}
        conn={conn}
        onCancel={onCancel}
        cancelDisabled={cancelDisabled}
      />
      <PhaseTimeline snapshot={snapshot} />
      {cancelError && (
        <div
          className="px-4 py-2 bg-rose-900/40 border-b border-rose-800 text-xs text-rose-200 flex justify-between"
          role="alert"
        >
          <span>
            Cancel rejected: {cancelError.reason} (current status:{" "}
            <code>{cancelError.current_status}</code>)
          </span>
          <button
            type="button"
            onClick={clearCancelError}
            className="underline hover:text-rose-100"
          >
            dismiss
          </button>
        </div>
      )}
      <div className="flex flex-1 min-h-0">
        <aside className="w-72 flex-shrink-0 p-4 border-r border-slate-800 space-y-3">
          <CostCard cost={snapshot?.costSummary ?? null} />
          <DecisionCountsCard counts={snapshot?.decisionRecordCounts} />
          <RiskBadge snapshot={snapshot} />
        </aside>
        <AgentActivityStream events={activity} />
      </div>
    </div>
  );
}
