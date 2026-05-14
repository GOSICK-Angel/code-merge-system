import { useEffect, useMemo, useRef } from "react";
import { useRunStore } from "../store/runStore";
import { createWsClient, type WsClient } from "../ws/client";
import { PhaseTimeline } from "../components/PhaseTimeline";
import { AgentActivityStream } from "../components/AgentActivityStream";
import { CostCard } from "../components/CostCard";
import { DecisionCountsCard } from "../components/DecisionCountsCard";
import { RiskBadge } from "../components/RiskBadge";
import { StatusBanner } from "../components/StatusBanner";

export function RunDashboard(): JSX.Element {
  const conn = useRunStore((s) => s.conn);
  const snapshot = useRunStore((s) => s.snapshot);
  const activity = useRunStore((s) => s.activity);
  const cancelError = useRunStore((s) => s.lastCancelError);
  const setConn = useRunStore((s) => s.setConn);
  const applySnapshot = useRunStore((s) => s.applySnapshot);
  const appendActivity = useRunStore((s) => s.appendActivity);
  const replaceActivity = useRunStore((s) => s.replaceActivity);
  const setCancelError = useRunStore((s) => s.setCancelError);
  const clearCancelError = useRunStore((s) => s.clearCancelError);

  const clientRef = useRef<WsClient | null>(null);

  useEffect(() => {
    const client = createWsClient({
      onState: setConn,
      onMessage: (msg) => {
        switch (msg.type) {
          case "state_snapshot":
          case "state_patch":
            applySnapshot(msg.payload);
            break;
          case "agent_activity":
            appendActivity(msg.payload);
            break;
          case "agent_activity_replay":
            replaceActivity(msg.payload.events);
            break;
          case "cancel_error":
            setCancelError(msg.payload);
            break;
        }
      },
    });
    clientRef.current = client;
    return () => {
      client.close();
      clientRef.current = null;
    };
  }, [setConn, applySnapshot, appendActivity, replaceActivity, setCancelError]);

  const cancelDisabled = snapshot?.status !== "awaiting_human";

  const onCancel = useMemo(
    () => () => {
      clearCancelError();
      clientRef.current?.send({ type: "cancel_run", payload: {} });
    },
    [clearCancelError],
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
