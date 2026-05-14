import type { ConnState } from "../ws/client";
import type { SystemStatus } from "../types/state";
import { statusLabel } from "../lib/format";

const STATUS_COLOR: Record<SystemStatus, string> = {
  initialized: "bg-slate-600",
  planning: "bg-sky-600",
  plan_reviewing: "bg-violet-600",
  plan_revising: "bg-amber-600",
  auto_merging: "bg-emerald-600",
  plan_dispute_pending: "bg-amber-700",
  analyzing_conflicts: "bg-orange-600",
  awaiting_human: "bg-rose-600",
  judge_reviewing: "bg-indigo-600",
  generating_report: "bg-cyan-600",
  completed: "bg-emerald-700",
  failed: "bg-rose-800",
  paused: "bg-slate-500",
};

interface Props {
  runId: string | null;
  status: SystemStatus | null;
  conn: ConnState;
  onCancel?: () => void;
  cancelDisabled?: boolean;
}

export function StatusBanner({
  runId,
  status,
  conn,
  onCancel,
  cancelDisabled,
}: Props): JSX.Element {
  const color = status ? STATUS_COLOR[status] : "bg-slate-700";
  const connDot =
    conn === "open"
      ? "bg-emerald-400"
      : conn === "connecting"
        ? "bg-amber-400"
        : "bg-rose-500";
  return (
    <header className="flex items-center justify-between px-4 py-3 border-b border-slate-800 bg-slate-900/60 backdrop-blur">
      <div className="flex items-center gap-3">
        <span
          className={`inline-block h-2.5 w-2.5 rounded-full ${connDot}`}
          aria-label={`Connection ${conn}`}
        />
        <h1 className="text-base font-semibold text-slate-100">
          Code Merge System
        </h1>
        {runId && (
          <code className="text-xs text-slate-400 font-mono">
            run {runId.slice(0, 8)}
          </code>
        )}
      </div>
      <div className="flex items-center gap-3">
        {status && (
          <span
            className={`px-2 py-1 text-xs font-medium uppercase tracking-wide text-white rounded ${color}`}
          >
            {statusLabel(status)}
          </span>
        )}
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            disabled={cancelDisabled}
            title={
              cancelDisabled
                ? "Cancel is only available at the AWAITING_HUMAN gate."
                : "Cancel run"
            }
            className="px-3 py-1 text-xs font-medium rounded border border-slate-700 text-slate-200 hover:bg-slate-800 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            Cancel run
          </button>
        )}
      </div>
    </header>
  );
}
