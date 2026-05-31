import { useEffect, useMemo, useState } from "react";
import { useRunStore } from "../store/runStore";
import { Card, Pill } from "../components/brutalist";
import { renderMarkdown } from "../lib/markdown";
import { totalTokenCount } from "../types/state";

interface MemoryEntry {
  key?: string;
  value?: unknown;
  phase?: string;
}

interface MemorySnapshot {
  phase_summaries: Record<string, unknown>;
  entries: MemoryEntry[];
}

function asMemorySnapshot(raw: unknown): MemorySnapshot {
  if (!raw || typeof raw !== "object") {
    return { phase_summaries: {}, entries: [] };
  }
  const obj = raw as Record<string, unknown>;
  return {
    phase_summaries: (obj.phase_summaries as Record<string, unknown>) ?? {},
    entries:
      (obj.entries as MemoryEntry[]) ??
      ([] as MemoryEntry[]),
  };
}

function shortValue(v: unknown): string {
  if (typeof v === "string") return v;
  if (v === null || v === undefined) return "—";
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

export function Report(): JSX.Element {
  const snapshot = useRunStore((s) => s.snapshot);
  const runId = snapshot?.runId ?? null;
  const runStatus = snapshot?.status ?? null;
  // Report markdown is written by ReportGeneration phase at run end —
  // fetching before terminal status guarantees a 404 (or the SPA HTML
  // fallback) and confuses the user with "report not found" while the
  // run is genuinely in flight. Gate the fetch on terminal state.
  const isTerminal = runStatus === "completed" || runStatus === "failed";

  const [markdown, setMarkdown] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!runId || !isTerminal) {
      setMarkdown(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setError(null);
    setMarkdown(null);
    fetch(`/runs/${runId}/merge_report_${runId}.md`)
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        // Defense in depth: if the static server quietly falls back
        // to the SPA index.html for a missing artifact, the response
        // is HTML — never markdown. Reject it explicitly so we don't
        // render the page's own DOCTYPE as report content.
        const ctype = res.headers.get("content-type") ?? "";
        if (ctype.includes("text/html")) {
          throw new Error("report not found (server returned HTML)");
        }
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
  }, [runId, isTerminal]);

  const memory = useMemo(
    () => asMemorySnapshot(snapshot?.memory),
    [snapshot],
  );
  const status = snapshot?.status ?? null;
  const errors = snapshot?.errors ?? [];

  const totalCost = snapshot?.costSummary?.total_cost_usd ?? 0;
  const totalTokens = totalTokenCount(snapshot?.costSummary);
  const byAgent = snapshot?.costSummary?.by_agent ?? {};

  const decisionCounts = snapshot?.decisionRecordCounts ?? {};
  const decisionTotal = Object.values(decisionCounts).reduce(
    (a, b) => a + b,
    0,
  );

  const completed = status === "completed";

  return (
    <div>
      <div
        className="row between mb-2"
        style={{ alignItems: "flex-end" }}
      >
        <div>
          <h1>
            Run report —{" "}
            <span className="dim">
              {completed ? "COMPLETED" : (status ?? "—").toString().toUpperCase()}
            </span>
          </h1>
          <div className="subhead">
            run <code>{runId?.slice(0, 8) ?? "—"}</code> · cost $
            {totalCost.toFixed(2)} · {(totalTokens / 1000).toFixed(0)}K tokens
          </div>
        </div>
        <div className="row" style={{ gap: 8 }}>
          <Pill tone={completed ? "green" : "red"} live={!completed}>
            {(status ?? "UNKNOWN").toString().toUpperCase()}
          </Pill>
          {runId && (
            <>
              <a
                href={`/runs/${runId}/merge_report_${runId}.md`}
                target="_blank"
                rel="noopener"
                className="btn"
              >
                ⬇ merge_report.md
              </a>
              <a
                href={`/runs/${runId}/plan_review_${runId}.md`}
                target="_blank"
                rel="noopener"
                className="btn"
              >
                ⬇ plan_review.md
              </a>
              <a
                href={`/runs/${runId}/checkpoint.json`}
                download
                className="btn"
              >
                ⬇ checkpoint.json
              </a>
            </>
          )}
        </div>
      </div>

      {errors.length > 0 && (
        <div
          className="hairline mb-2"
          style={{
            padding: "10px 14px",
            background: "color-mix(in oklch, var(--red), transparent 88%)",
            color: "var(--fg-0)",
            fontSize: 11.5,
            borderColor: "var(--red-dim)",
          }}
          role="alert"
        >
          <div className="upcase" style={{ marginBottom: 6 }}>
            Errors · {errors.length}
          </div>
          <ul
            style={{
              margin: 0,
              paddingLeft: 18,
              listStyle: "disc",
              color: "var(--fg-1)",
            }}
          >
            {errors.slice(-5).map((e, i) => (
              <li
                key={i}
                style={{
                  fontFamily: "var(--mono)",
                  fontSize: 11,
                  marginBottom: 2,
                }}
              >
                {e.message ?? JSON.stringify(e)}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="report-grid">
        <Card
          title="› MERGE_REPORT.MD"
          hint={runId ? `runs/${runId.slice(0, 8)}/merge_report.md` : "—"}
          pad={false}
        >
          {error && (
            <div
              style={{
                padding: "10px 14px",
                background: "color-mix(in oklch, var(--red), transparent 88%)",
                color: "var(--fg-0)",
                fontSize: 11.5,
                borderBottom: "1px solid var(--red-dim)",
              }}
              role="alert"
            >
              Report not available: {error}
            </div>
          )}
          {markdown ? (
            <article
              className="md"
              style={{ maxHeight: 720, overflowY: "auto" }}
            >
              {renderMarkdown(markdown)}
            </article>
          ) : !error && !isTerminal ? (
            <div
              className="dim"
              style={{ padding: 16, fontSize: 11.5 }}
            >
              Report will be generated once the run completes — current
              status: <code>{runStatus ?? "—"}</code>
            </div>
          ) : !error ? (
            <div
              className="dim"
              style={{ padding: 16, fontSize: 11.5 }}
            >
              loading report ...
            </div>
          ) : null}
        </Card>

        <div className="col">
          <Card title="› FINAL COST">
            <div className="row between mb-2">
              <span className="upcase">total</span>
              <span
                style={{
                  fontFamily: "var(--sans)",
                  fontSize: 28,
                  fontWeight: 600,
                  color: "var(--accent)",
                }}
              >
                ${totalCost.toFixed(2)}
              </span>
            </div>
            <div
              className="dim mb-2"
              style={{ fontSize: 11 }}
            >
              {(totalTokens / 1000).toFixed(0)}K tokens
            </div>
            {Object.entries(byAgent).length === 0 ? (
              <div
                className="dim"
                style={{ fontSize: 11, padding: "8px 0" }}
              >
                no per-agent breakdown available
              </div>
            ) : (
              Object.entries(byAgent).map(([who, v]) => {
                const cost = v.cost_usd ?? 0;
                const pct = totalCost > 0 ? (cost / totalCost) * 100 : 0;
                return (
                  <div key={who} className="cost-grid">
                    <div className="who">{who}</div>
                    <div className="val">${cost.toFixed(2)}</div>
                    <div className="mini-bar">
                      <div
                        className="f"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>
                );
              })
            )}
          </Card>

          {decisionTotal > 0 && (
            <Card
              title="› DECISIONS"
              hint={`${decisionTotal} records`}
            >
              {Object.entries(decisionCounts).map(([k, v]) => (
                <div
                  key={k}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1fr auto",
                    padding: "6px 0",
                    borderBottom: "1px dashed var(--line)",
                    fontSize: 11.5,
                  }}
                >
                  <code style={{ color: "var(--fg-1)" }}>{k}</code>
                  <span
                    style={{
                      fontVariantNumeric: "tabular-nums",
                      color: "var(--fg-0)",
                    }}
                  >
                    {v.toLocaleString()}
                  </span>
                </div>
              ))}
            </Card>
          )}

          <Card
            title="› LEARNED MEMORY"
            hint={`${memory.entries.length} entries`}
          >
            {memory.entries.length === 0 ? (
              <div
                className="dim"
                style={{ fontSize: 11, padding: "8px 0" }}
              >
                no memory entries
              </div>
            ) : (
              <div style={{ display: "flex", flexWrap: "wrap" }}>
                {memory.entries.slice(0, 24).map((entry, i) => (
                  <div key={entry.key ?? i} className="memchip">
                    {entry.phase && <span className="k">{entry.phase}</span>}
                    <span
                      title={shortValue(entry.value ?? entry.key)}
                      style={{
                        maxWidth: 220,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                    >
                      {shortValue(entry.value ?? entry.key)}
                    </span>
                  </div>
                ))}
                {memory.entries.length > 24 && (
                  <div
                    className="dim"
                    style={{
                      fontSize: 10,
                      width: "100%",
                      marginTop: 4,
                    }}
                  >
                    + {memory.entries.length - 24} more entries
                  </div>
                )}
              </div>
            )}
          </Card>

          {runId && (
            <Card title="› ARTIFACTS">
              {[
                [`merge_report_${runId}.md`, "merge_report.md", "view"],
                [`plan_review_${runId}.md`, "plan_review.md", "view"],
                ["checkpoint.json", "checkpoint.json", "download"],
              ].map(([f, label, kind]) => (
                <div
                  key={f}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1fr auto",
                    gap: 10,
                    padding: "6px 0",
                    borderBottom: "1px dashed var(--line)",
                    alignItems: "center",
                    fontSize: 11.5,
                  }}
                >
                  <code style={{ color: "var(--fg-0)" }}>{label}</code>
                  <a
                    href={`/runs/${runId}/${f}`}
                    target={kind === "view" ? "_blank" : undefined}
                    rel={kind === "view" ? "noopener" : undefined}
                    download={kind === "download" ? true : undefined}
                    className="btn ghost"
                    style={{ padding: "2px 8px", fontSize: 10 }}
                  >
                    ⬇
                  </a>
                </div>
              ))}
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
