import { useMemo, useState } from "react";
import type { WsClient } from "../ws/client";
import type { OutboundMessage } from "../ws/messages";
import { useRunStore } from "../store/runStore";
import { StatusBanner } from "../components/StatusBanner";
import type {
  JudgeIssuePayload,
  JudgeResolution,
  JudgeVerdict as JudgeVerdictType,
} from "../types/state";

interface Props {
  clientRef: React.MutableRefObject<WsClient | null>;
}

const SEVERITY_COLOR: Record<string, string> = {
  critical: "bg-rose-700 text-rose-50",
  high: "bg-rose-600 text-rose-50",
  medium: "bg-amber-600 text-amber-50",
  low: "bg-slate-600 text-slate-100",
  info: "bg-slate-700 text-slate-200",
  unknown: "bg-slate-700 text-slate-300",
};

function groupByFile(
  issues: JudgeIssuePayload[],
): Array<{ file_path: string; issues: JudgeIssuePayload[] }> {
  const map = new Map<string, JudgeIssuePayload[]>();
  for (const issue of issues) {
    const existing = map.get(issue.file_path);
    if (existing) existing.push(issue);
    else map.set(issue.file_path, [issue]);
  }
  return Array.from(map.entries()).map(([file_path, group]) => ({
    file_path,
    issues: group,
  }));
}

export function JudgeVerdict({ clientRef }: Props): JSX.Element {
  const snapshot = useRunStore((s) => s.snapshot);
  const conn = useRunStore((s) => s.conn);
  const verdict: JudgeVerdictType | null = snapshot?.judgeVerdict ?? null;
  const resolution: JudgeResolution | null = snapshot?.judgeResolution ?? null;
  const rerunRound = snapshot?.rerunRound ?? 0;
  const maxRerunRounds = snapshot?.maxRerunRounds ?? 0;

  const [pendingAction, setPendingAction] = useState<JudgeResolution | null>(
    null,
  );

  const send = (msg: OutboundMessage) => clientRef.current?.send(msg);

  const submit = (r: JudgeResolution) => {
    if (resolution !== null) return; // server-decided guard
    setPendingAction(r);
    send({ type: "submit_judge_resolution", payload: { resolution: r } });
  };

  const groupedIssues = useMemo(
    () => groupByFile(verdict?.issues ?? []),
    [verdict],
  );

  if (!verdict) {
    return (
      <div className="min-h-screen flex flex-col bg-slate-950 text-slate-100">
        <StatusBanner
          runId={snapshot?.runId ?? null}
          status={snapshot?.status ?? null}
          conn={conn}
        />
        <div className="flex-1 flex items-center justify-center text-slate-500 text-sm">
          No judge verdict in state.
        </div>
      </div>
    );
  }

  const decided = resolution !== null || pendingAction !== null;
  const effectiveResolution = resolution ?? pendingAction;

  return (
    <div className="min-h-screen flex flex-col bg-slate-950 text-slate-100">
      <StatusBanner
        runId={snapshot?.runId ?? null}
        status={snapshot?.status ?? null}
        conn={conn}
      />
      {verdict.veto_triggered && (
        <div className="px-4 py-3 bg-rose-900/70 border-b border-rose-700 text-sm" role="alert">
          <div className="font-semibold text-rose-100 mb-1">
            ⛔ Judge veto triggered
          </div>
          <div className="text-rose-100">
            {verdict.veto_reason ?? "(no reason recorded)"}
          </div>
        </div>
      )}
      <header className="px-4 py-3 border-b border-slate-800 flex flex-wrap items-center gap-4">
        <div className="flex-1">
          <h1 className="text-sm font-semibold text-slate-100">
            Judge verdict ·{" "}
            <span className="font-mono text-amber-300">{verdict.verdict}</span>
          </h1>
          <p className="text-xs text-slate-400 mt-1">{verdict.summary}</p>
        </div>
        <div className="text-xs text-slate-400 grid grid-cols-2 gap-x-4 gap-y-0.5">
          <span>Reviewed:</span>
          <span className="text-slate-100 font-mono">{verdict.reviewed_files_count}</span>
          <span>Failed:</span>
          <span className="text-rose-400 font-mono">{verdict.failed_files.length}</span>
          <span>Critical:</span>
          <span className="text-rose-400 font-mono">{verdict.critical_issues_count}</span>
          <span>High:</span>
          <span className="text-amber-400 font-mono">{verdict.high_issues_count}</span>
          <span>Confidence:</span>
          <span className="text-slate-100 font-mono">
            {verdict.overall_confidence.toFixed(2)}
          </span>
          {maxRerunRounds > 0 && (
            <>
              <span>Rerun:</span>
              <span className="text-slate-100 font-mono">
                {rerunRound} / {maxRerunRounds}
              </span>
            </>
          )}
        </div>
      </header>
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-5">
        {verdict.failed_files.length > 0 && (
          <section>
            <h2 className="text-xs text-slate-500 uppercase tracking-wider mb-2">
              Failed files ({verdict.failed_files.length})
            </h2>
            <ul className="text-xs font-mono space-y-0.5">
              {verdict.failed_files.map((fp) => (
                <li key={fp} className="text-rose-300">
                  {fp}
                </li>
              ))}
            </ul>
          </section>
        )}
        {groupedIssues.length > 0 && (
          <section>
            <h2 className="text-xs text-slate-500 uppercase tracking-wider mb-2">
              Issues ({verdict.issues.length})
            </h2>
            <div className="space-y-3">
              {groupedIssues.map((group) => (
                <div
                  key={group.file_path}
                  className="border border-slate-800 rounded"
                >
                  <div className="px-2 py-1.5 bg-slate-900/60 text-xs font-mono text-slate-200 border-b border-slate-800">
                    {group.file_path}
                    <span className="ml-2 text-slate-500">
                      ({group.issues.length} issue
                      {group.issues.length === 1 ? "" : "s"})
                    </span>
                  </div>
                  <ul className="divide-y divide-slate-800">
                    {group.issues.map((issue, idx) => (
                      <li
                        key={issue.issue_id ?? idx}
                        className="px-2 py-2 text-xs space-y-1"
                      >
                        <div className="flex items-center gap-2">
                          <span
                            className={`px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wide ${
                              SEVERITY_COLOR[issue.severity] ??
                              SEVERITY_COLOR.unknown
                            }`}
                          >
                            {issue.severity}
                          </span>
                          <span className="text-slate-300 font-mono text-[11px]">
                            {issue.issue_type}
                          </span>
                          {issue.must_fix_before_merge && (
                            <span className="text-[10px] text-rose-300">
                              must-fix
                            </span>
                          )}
                          {issue.affected_lines.length > 0 && (
                            <span className="text-[10px] text-slate-500 ml-auto font-mono">
                              lines {issue.affected_lines.join(", ")}
                            </span>
                          )}
                        </div>
                        <p className="text-slate-300 leading-relaxed">
                          {issue.description}
                        </p>
                        {issue.suggested_fix && (
                          <p className="text-slate-400 text-[11px] border-l-2 border-amber-700 pl-2">
                            <span className="text-amber-400 font-medium">
                              Suggested fix:{" "}
                            </span>
                            {issue.suggested_fix}
                          </p>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </section>
        )}
        {verdict.repair_instructions.length > 0 && (
          <section>
            <h2 className="text-xs text-slate-500 uppercase tracking-wider mb-2">
              Repair instructions ({verdict.repair_instructions.length})
            </h2>
            <ul className="space-y-1.5">
              {verdict.repair_instructions.map((r, idx) => (
                <li
                  key={r.source_issue_id ?? idx}
                  className="border border-slate-800 rounded px-2 py-1.5 text-xs space-y-0.5"
                >
                  <div className="flex items-center gap-2">
                    <span className="text-slate-300 font-mono text-[11px]">
                      {r.file_path}
                    </span>
                    {r.severity && (
                      <span
                        className={`text-[10px] px-1 py-0.5 rounded ${
                          SEVERITY_COLOR[r.severity] ??
                          SEVERITY_COLOR.unknown
                        }`}
                      >
                        {r.severity}
                      </span>
                    )}
                    <span
                      className={`text-[10px] ml-auto ${
                        r.is_repairable ? "text-emerald-400" : "text-rose-400"
                      }`}
                    >
                      {r.is_repairable ? "repairable" : "manual"}
                    </span>
                  </div>
                  <p className="text-slate-300">{r.instruction}</p>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
      <footer className="px-4 py-3 border-t border-slate-800 bg-slate-900/40 flex items-center justify-between gap-4">
        <div className="text-xs text-slate-400">
          {decided ? (
            <span className="text-emerald-400">
              Resolved: <code>{effectiveResolution}</code>{" "}
              {resolution === null && (
                <span className="text-slate-500">(awaiting server ack)</span>
              )}
            </span>
          ) : (
            <span>Choose an action to resume the orchestrator</span>
          )}
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => submit("abort")}
            disabled={decided}
            title="Abort the run; checkpoint preserved for resume"
            className="text-xs px-3 py-1.5 rounded border border-rose-700 text-rose-200 hover:bg-rose-900/30 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            Abort
          </button>
          <button
            type="button"
            onClick={() => submit("rerun")}
            disabled={decided || rerunRound >= maxRerunRounds && maxRerunRounds > 0}
            title={
              maxRerunRounds > 0 && rerunRound >= maxRerunRounds
                ? "Rerun budget exhausted"
                : "Clear failed-file decisions and rerun auto-merge"
            }
            className="text-xs px-3 py-1.5 rounded border border-amber-700 text-amber-200 hover:bg-amber-900/30 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            Rerun
          </button>
          <button
            type="button"
            onClick={() => submit("accept")}
            disabled={decided}
            title="Accept the verdict as-is and proceed"
            className="text-xs px-3 py-1.5 rounded bg-emerald-700 hover:bg-emerald-600 text-white disabled:opacity-40 disabled:cursor-not-allowed font-medium"
          >
            Accept
          </button>
        </div>
      </footer>
    </div>
  );
}
