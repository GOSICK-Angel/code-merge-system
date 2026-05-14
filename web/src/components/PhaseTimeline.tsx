import type { MergeStateSnapshot, SystemStatus } from "../types/state";
import { SYSTEM_STATUS_ORDER } from "../types/state";
import { formatDuration, statusLabel } from "../lib/format";

interface Props {
  snapshot: MergeStateSnapshot | null;
}

// Map system status -> phase_results key (best-effort; phase_results uses the
// MergePhase enum value, not SystemStatus). For statuses that don't have a
// distinct phase entry we just render the label without elapsed.
const PHASE_KEY_HINT: Partial<Record<SystemStatus, string>> = {
  planning: "analysis",
  plan_reviewing: "plan_review",
  plan_revising: "plan_revising",
  auto_merging: "auto_merge",
  analyzing_conflicts: "conflict_analysis",
  awaiting_human: "human_review",
  judge_reviewing: "judge_review",
  generating_report: "report",
};

export function PhaseTimeline({ snapshot }: Props): JSX.Element {
  const current = snapshot?.status ?? "initialized";
  const elapsed = snapshot?.phaseElapsed ?? {};
  const currentIndex = SYSTEM_STATUS_ORDER.indexOf(current);

  return (
    <section className="px-4 py-3 border-b border-slate-800">
      <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-2">
        Phase timeline
      </h2>
      <ol className="flex gap-1 overflow-x-auto">
        {SYSTEM_STATUS_ORDER.map((s, idx) => {
          const isCurrent = s === current;
          const isPast = currentIndex >= 0 && idx < currentIndex;
          const key = PHASE_KEY_HINT[s];
          const elapsedSec = key ? elapsed[key] : null;
          const color = isCurrent
            ? "bg-sky-500 text-white border-sky-400"
            : isPast
              ? "bg-emerald-900/60 text-emerald-200 border-emerald-700"
              : "bg-slate-900 text-slate-500 border-slate-800";
          return (
            <li
              key={s}
              className={`flex-shrink-0 px-2 py-1 text-[10px] font-medium rounded border ${color}`}
              title={
                elapsedSec !== null && elapsedSec !== undefined
                  ? `Elapsed: ${formatDuration(elapsedSec)}`
                  : statusLabel(s)
              }
            >
              <div>{statusLabel(s)}</div>
              {elapsedSec !== null && elapsedSec !== undefined && (
                <div className="text-[9px] opacity-75">
                  {formatDuration(elapsedSec)}
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
