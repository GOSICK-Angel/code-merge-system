import { useState } from "react";
import type { PlanReviewRoundPayload } from "../types/state";

interface Props {
  rounds: PlanReviewRoundPayload[];
}

const VERDICT_COLOR: Record<string, string> = {
  approved: "bg-emerald-700 text-emerald-100",
  needs_revision: "bg-amber-700 text-amber-100",
  rejected: "bg-rose-700 text-rose-100",
};

const ACTION_COLOR: Record<string, string> = {
  accept: "text-emerald-400",
  reject: "text-rose-400",
  discuss: "text-sky-400",
};

export function NegotiationTimeline({ rounds }: Props): JSX.Element {
  const [openRound, setOpenRound] = useState<number | null>(
    rounds.length > 0 ? rounds[rounds.length - 1].round_number : null,
  );

  if (rounds.length === 0) {
    return (
      <section className="p-3 text-xs text-slate-500 italic">
        No planner ↔ planner_judge negotiation rounds yet.
      </section>
    );
  }

  return (
    <section>
      <header className="px-3 py-2">
        <h2 className="text-xs font-medium text-slate-400 uppercase tracking-wider">
          Negotiation timeline ({rounds.length} round{rounds.length === 1 ? "" : "s"})
        </h2>
      </header>
      <ol className="px-2 pb-2 space-y-1.5">
        {rounds.map((r) => {
          const open = openRound === r.round_number;
          const verdictColor =
            VERDICT_COLOR[r.verdict_result] ?? "bg-slate-700 text-slate-100";
          return (
            <li
              key={r.round_number}
              className="rounded border border-slate-800"
            >
              <button
                type="button"
                onClick={() => setOpenRound(open ? null : r.round_number)}
                className="w-full px-2 py-1.5 flex items-center gap-2 text-left hover:bg-slate-900/60"
              >
                <span className="text-slate-500 w-4">{open ? "▾" : "▸"}</span>
                <span className="text-xs font-mono text-slate-200">
                  R{r.round_number}
                </span>
                <span
                  className={`text-[10px] px-1.5 py-0.5 rounded uppercase tracking-wide ${verdictColor}`}
                >
                  {r.verdict_result}
                </span>
                <span className="text-[10px] text-slate-500 ml-auto">
                  {r.issues_count} issue{r.issues_count === 1 ? "" : "s"} ·{" "}
                  {r.planner_responses.length} response
                  {r.planner_responses.length === 1 ? "" : "s"} ·{" "}
                  {r.plan_diff.length} diff
                </span>
              </button>
              {open && (
                <div className="px-3 pb-3 space-y-2 text-[11px]">
                  {r.verdict_summary && (
                    <p className="text-slate-300 leading-relaxed">
                      {r.verdict_summary}
                    </p>
                  )}
                  {r.planner_responses.length > 0 && (
                    <div>
                      <div className="text-slate-500 uppercase tracking-wider mb-1">
                        Planner responses
                      </div>
                      <ul className="space-y-0.5 font-mono">
                        {r.planner_responses.map((pr) => (
                          <li key={pr.issue_id} className="flex gap-2">
                            <span
                              className={`w-16 ${
                                ACTION_COLOR[pr.action] ?? "text-slate-400"
                              }`}
                            >
                              {pr.action}
                            </span>
                            <span className="text-slate-400 truncate max-w-[180px]">
                              {pr.file_path}
                            </span>
                            <span className="text-slate-300 flex-1 truncate">
                              {pr.reason}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {r.plan_diff.length > 0 && (
                    <div>
                      <div className="text-slate-500 uppercase tracking-wider mb-1">
                        Plan diff
                      </div>
                      <ul className="space-y-0.5 font-mono">
                        {r.plan_diff.map((d, idx) => (
                          <li key={idx} className="flex gap-2">
                            <span className="text-slate-400 truncate max-w-[180px]">
                              {d.file_path}
                            </span>
                            <span className="text-slate-300">
                              {d.old_risk} → {d.new_risk}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {r.negotiation_messages.length > 0 && (
                    <div>
                      <div className="text-slate-500 uppercase tracking-wider mb-1">
                        Messages
                      </div>
                      <ul className="space-y-1">
                        {r.negotiation_messages.map((m, idx) => (
                          <li key={idx}>
                            <span
                              className={
                                m.sender === "planner_judge"
                                  ? "text-sky-400"
                                  : "text-amber-400"
                              }
                            >
                              {m.sender}
                            </span>
                            <span className="text-slate-300 ml-2">
                              {m.content}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
