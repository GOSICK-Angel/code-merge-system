import { useEffect, useState } from "react";
import { useRunStore } from "../store/runStore";
import { StatusBanner } from "../components/StatusBanner";
import { CostCard } from "../components/CostCard";
import { DecisionCountsCard } from "../components/DecisionCountsCard";
import { renderMarkdown } from "../lib/markdown";

interface MemorySnapshot {
  phase_summaries: Record<string, unknown>;
  entries: Array<{ key?: string; value?: unknown; phase?: string }>;
}

function asMemorySnapshot(raw: unknown): MemorySnapshot {
  if (!raw || typeof raw !== "object") {
    return { phase_summaries: {}, entries: [] };
  }
  const obj = raw as Record<string, unknown>;
  return {
    phase_summaries: (obj.phase_summaries as Record<string, unknown>) ?? {},
    entries:
      (obj.entries as Array<{
        key?: string;
        value?: unknown;
        phase?: string;
      }>) ?? [],
  };
}

export function Report(): JSX.Element {
  const snapshot = useRunStore((s) => s.snapshot);
  const conn = useRunStore((s) => s.conn);
  const runId = snapshot?.runId ?? null;

  const [markdown, setMarkdown] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    setError(null);
    setMarkdown(null);
    fetch(`/runs/${runId}/merge_report.md`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.text();
      })
      .then((text) => {
        if (!cancelled) setMarkdown(text);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  const memory = asMemorySnapshot(snapshot?.memory);
  const status = snapshot?.status ?? null;
  const errors = snapshot?.errors ?? [];

  return (
    <div className="min-h-screen flex flex-col bg-slate-950 text-slate-100">
      <StatusBanner runId={runId} status={status} conn={conn} />
      <div
        className={`px-4 py-2 border-b ${
          status === "completed"
            ? "bg-emerald-900/40 border-emerald-800 text-emerald-100"
            : "bg-rose-900/40 border-rose-800 text-rose-100"
        } text-xs`}
      >
        Run {status === "completed" ? "completed" : "failed"} ·{" "}
        <code className="font-mono">{runId?.slice(0, 8) ?? "?"}</code>
      </div>
      <div className="flex flex-1 min-h-0">
        <aside className="w-72 flex-shrink-0 p-4 border-r border-slate-800 space-y-3 overflow-y-auto">
          <CostCard cost={snapshot?.costSummary ?? null} />
          <DecisionCountsCard counts={snapshot?.decisionRecordCounts} />
          {runId && (
            <div className="bg-slate-900 border border-slate-800 rounded p-3 space-y-1.5">
              <div className="text-xs text-slate-500 uppercase tracking-wider">
                Artifacts
              </div>
              <div className="text-xs flex flex-col gap-1">
                <a
                  href={`/runs/${runId}/merge_report.md`}
                  className="text-sky-300 hover:underline"
                  target="_blank"
                  rel="noopener"
                >
                  merge_report.md
                </a>
                <a
                  href={`/runs/${runId}/plan_review.md`}
                  className="text-sky-300 hover:underline"
                  target="_blank"
                  rel="noopener"
                >
                  plan_review.md
                </a>
                <a
                  href={`/runs/${runId}/checkpoint.json`}
                  className="text-sky-300 hover:underline"
                  download
                >
                  checkpoint.json ↓
                </a>
              </div>
            </div>
          )}
          <div className="bg-slate-900 border border-slate-800 rounded p-3 space-y-1.5">
            <div className="text-xs text-slate-500 uppercase tracking-wider">
              Memory ({memory.entries.length})
            </div>
            {memory.entries.length === 0 ? (
              <div className="text-xs text-slate-500 italic">
                No memory entries.
              </div>
            ) : (
              <ul className="space-y-0.5 max-h-72 overflow-y-auto">
                {memory.entries.slice(0, 50).map((entry, idx) => (
                  <li
                    key={entry.key ?? idx}
                    className="text-[11px] text-slate-300"
                  >
                    <span className="text-slate-500 font-mono mr-2">
                      {entry.phase ?? "—"}
                    </span>
                    <span className="truncate">
                      {String(entry.value ?? entry.key ?? "")}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </aside>
        <main className="flex-1 overflow-y-auto p-6">
          {error && (
            <div className="mb-4 px-3 py-2 rounded bg-rose-900/40 border border-rose-800 text-xs text-rose-200">
              Report not available: {error}
            </div>
          )}
          {markdown ? (
            <article className="max-w-none text-sm space-y-2">
              {renderMarkdown(markdown)}
            </article>
          ) : !error ? (
            <p className="text-xs text-slate-500 italic">Loading report...</p>
          ) : null}
          {errors.length > 0 && (
            <section className="mt-6">
              <h2 className="text-sm font-semibold text-rose-300 mb-2">
                Errors ({errors.length})
              </h2>
              <ul className="space-y-1 text-xs">
                {errors.slice(-10).map((e, idx) => (
                  <li
                    key={idx}
                    className="text-rose-300 font-mono leading-relaxed"
                  >
                    {e.message ?? JSON.stringify(e)}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </main>
      </div>
    </div>
  );
}
