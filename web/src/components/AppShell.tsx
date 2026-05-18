import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import type { ActiveView } from "../lib/classifyView";
import type { MergeStateSnapshot } from "../types/state";
import type { ConnState } from "../ws/client";
import { BgFx, AsciiBar, Pill } from "./brutalist";

interface NavEntry {
  id: ActiveView;
  num: string;
  label: string;
  hint: string;
}

const NAV: NavEntry[] = [
  { id: "dashboard", num: "L1", label: "DASHBOARD", hint: "live progress" },
  { id: "plan_review", num: "L2", label: "PLAN REVIEW", hint: "approve plan" },
  {
    id: "conflict_resolution",
    num: "L3",
    label: "CONFLICT",
    hint: "diff + decide",
  },
  { id: "judge_verdict", num: "L4", label: "JUDGE VERDICT", hint: "verdict" },
  { id: "report", num: "L5", label: "REPORT", hint: "summary · cost" },
];

interface Props {
  snapshot: MergeStateSnapshot | null;
  conn: ConnState;
  activeView: ActiveView;
  selectedView: ActiveView;
  onSelectView: (v: ActiveView) => void;
  onCancel: () => void;
  cancelDisabled: boolean;
  cancelTitle: string;
  children: ReactNode;
}

function deriveProgress(snapshot: MergeStateSnapshot | null): number {
  if (!snapshot) return 0;
  const recs = snapshot.fileDecisionRecords;
  const total = Object.keys(snapshot.fileClassifications).length;
  if (total === 0) return 0;
  const merged = Object.values(recs).filter((r) => r.success).length;
  return Math.min(100, (merged / total) * 100);
}

function deriveElapsed(snapshot: MergeStateSnapshot | null): string {
  if (!snapshot) return "—";
  const started = Date.parse(snapshot.createdAt);
  if (Number.isNaN(started)) return "—";
  const totalSec = Math.max(0, Math.floor((Date.now() - started) / 1000));
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  return `${m}m ${String(s).padStart(2, "0")}s`;
}

function shortHash(s: string | undefined | null, n = 8): string {
  if (!s) return "—";
  return s.length > n ? s.slice(0, n) : s;
}

function navBadge(
  entry: NavEntry,
  snapshot: MergeStateSnapshot | null,
  activeView: ActiveView,
): { text: string; kind: "alert" | "ok" | "" } | null {
  if (!snapshot) return null;
  if (entry.id === "plan_review") {
    const pending = snapshot.pendingUserDecisions.filter(
      (i) => i.user_choice === null,
    ).length;
    if (pending > 0) return { text: String(pending), kind: "alert" };
  }
  if (entry.id === "conflict_resolution") {
    const pending = Object.values(snapshot.humanDecisionRequests).filter(
      (r) => r.human_decision === null,
    ).length;
    if (pending > 0) return { text: String(pending), kind: "alert" };
  }
  if (entry.id === "judge_verdict") {
    if (
      snapshot.judgeVerdict !== null &&
      (snapshot.judgeResolution ?? null) === null
    ) {
      return { text: "OPEN", kind: "alert" };
    }
  }
  if (entry.id === "report") {
    if (snapshot.status === "completed") return { text: "✓", kind: "ok" };
    if (snapshot.status === "failed") return { text: "FAIL", kind: "alert" };
  }
  if (entry.id === activeView) return { text: "LIVE", kind: "" };
  return null;
}

export function AppShell({
  snapshot,
  conn,
  activeView,
  selectedView,
  onSelectView,
  onCancel,
  cancelDisabled,
  cancelTitle,
  children,
}: Props): JSX.Element {
  const [clock, setClock] = useState<string>(() =>
    new Date().toISOString().slice(11, 19),
  );
  useEffect(() => {
    const id = setInterval(
      () => setClock(new Date().toISOString().slice(11, 19)),
      1000,
    );
    return () => clearInterval(id);
  }, []);

  const progress = useMemo(() => deriveProgress(snapshot), [snapshot]);
  const elapsed = useMemo(() => deriveElapsed(snapshot), [snapshot]);

  const plan = snapshot?.mergePlan;
  const repo = plan?.upstream_ref?.split("/")[0] ?? "—";
  const upstream = plan?.upstream_ref ?? "—";
  const fork = plan?.fork_ref ?? "—";

  const agents = useMemo(() => {
    const byAgent = snapshot?.costSummary?.by_agent ?? {};
    return Object.entries(byAgent).map(([id, v]) => ({
      id,
      cost: v.cost_usd ?? 0,
      tokens: v.tokens ?? 0,
      busy: snapshot?.currentPhase
        ? id.toLowerCase().includes(snapshot.currentPhase.split("_")[0] ?? "")
        : false,
    }));
  }, [snapshot]);

  const statusPillTone = (() => {
    const s = snapshot?.status;
    if (s === "completed") return "green";
    if (s === "failed") return "red";
    if (s === "awaiting_human") return "red";
    if (s === "paused") return "orange";
    return "amber";
  })();

  const isSetup = activeView === "setup";

  return (
    <>
      <BgFx scanline={1} />
      <div className="brut-app">
        <header className="brut-topbar">
          <div className="brand">
            <span className="mark" />
            <span>
              MERGE<span style={{ color: "var(--accent)" }}>.SYS</span>
            </span>
            <span className="v">v0.9.3</span>
          </div>
          {!isSetup && (
          <div className="meta">
            <div className="kv">
              <span className="k">repo</span>
              <span className="v">{repo}</span>
            </div>
            <div className="kv">
              <span className="k">upstream</span>
              <span className="v">{upstream}</span>
            </div>
            <div className="kv">
              <span className="k">fork</span>
              <span className="v">{fork}</span>
            </div>
            <div className="kv">
              <span className="k">run</span>
              <span className="v">{shortHash(snapshot?.runId, 8)}</span>
            </div>
            <div className="kv">
              <span className="k">UTC</span>
              <span
                className="v"
                style={{ fontVariantNumeric: "tabular-nums" }}
              >
                {clock}
              </span>
            </div>
          </div>
          )}
          {isSetup && (
            <div className="meta">
              <div className="kv">
                <span className="k">mode</span>
                <span className="v" style={{ color: "var(--accent)" }}>
                  SETUP
                </span>
              </div>
              <div className="kv">
                <span className="k">UTC</span>
                <span
                  className="v"
                  style={{ fontVariantNumeric: "tabular-nums" }}
                >
                  {clock}
                </span>
              </div>
            </div>
          )}
          <div className="actions">
            {!isSetup && (
              <button
                type="button"
                className="btn danger"
                onClick={onCancel}
                disabled={cancelDisabled}
                title={cancelTitle}
              >
                ⌥ CANCEL
              </button>
            )}
          </div>
        </header>

        {!isSetup && (
        <div className="brut-runstrip">
          <Pill
            tone={statusPillTone as "amber" | "green" | "orange" | "red"}
            live
          >
            {(snapshot?.status ?? "INITIALIZING")
              .toString()
              .toUpperCase()
              .replace("_", " ")}
          </Pill>
          <div className="row" style={{ gap: 12, alignItems: "center" }}>
            <span className="dim">PHASE</span>
            <span
              style={{
                color: "var(--fg-0)",
                fontFamily: "var(--mono)",
              }}
            >
              {snapshot?.currentPhase ?? "—"}
            </span>
            <span className="dim">·</span>
            <span className="dim">PROGRESS</span>
            <AsciiBar pct={progress} width={36} />
            <span className="pct">{progress.toFixed(1)}%</span>
            <span className="dim">·</span>
            <span className="dim">
              ELAPSED{" "}
              <span style={{ color: "var(--fg-0)" }}>{elapsed}</span>
            </span>
          </div>
          <span className="dim">
            ws://localhost:8765{" "}
            <span
              style={{
                color:
                  conn === "open"
                    ? "var(--green)"
                    : conn === "connecting"
                      ? "var(--amber)"
                      : "var(--red)",
              }}
            >
              ● {conn}
            </span>
          </span>
        </div>
        )}

        <div className={`brut-work${isSetup ? " setup" : ""}`}>
          {isSetup ? null : (
          <aside className="brut-sidebar">
            <div className="sect">› VIEWS</div>
            {NAV.map((n) => {
              const badge = navBadge(n, snapshot, activeView);
              return (
                <div
                  key={n.id}
                  className={`nav-item ${selectedView === n.id ? "active" : ""}`}
                  onClick={() => onSelectView(n.id)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(ev) => {
                    if (ev.key === "Enter" || ev.key === " ") {
                      ev.preventDefault();
                      onSelectView(n.id);
                    }
                  }}
                >
                  <span className="num">{n.num}</span>
                  <span>
                    <div style={{ color: "inherit" }}>{n.label}</div>
                    <div
                      style={{
                        fontSize: 10,
                        color: "var(--fg-3)",
                        letterSpacing: "0.06em",
                      }}
                    >
                      {n.hint}
                    </div>
                  </span>
                  {badge && (
                    <span className={`badge ${badge.kind}`}>{badge.text}</span>
                  )}
                </div>
              );
            })}

            {agents.length > 0 && (
              <>
                <div className="sect" style={{ marginTop: 12 }}>
                  › AGENTS
                </div>
                {agents.map((a) => (
                  <div
                    key={a.id}
                    className="nav-item"
                    style={{ cursor: "default", padding: "6px 18px" }}
                  >
                    <span
                      className="num"
                      style={{
                        color: a.busy ? "var(--accent)" : "var(--fg-3)",
                      }}
                    >
                      ●
                    </span>
                    <span>
                      <div
                        style={{
                          color: a.busy ? "var(--fg-0)" : "var(--fg-2)",
                          fontSize: 11,
                          fontFamily: "var(--mono)",
                        }}
                      >
                        {a.id}
                      </div>
                      <div
                        style={{ fontSize: 9, color: "var(--fg-3)" }}
                      >
                        {a.busy ? "busy" : "idle"}
                      </div>
                    </span>
                    <span
                      style={{
                        fontSize: 9,
                        color: "var(--fg-3)",
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      ${a.cost.toFixed(2)}
                    </span>
                  </div>
                ))}
              </>
            )}

            <div
              style={{
                marginTop: "auto",
                padding: "16px 18px",
                borderTop: "1px solid var(--line)",
                fontSize: 10,
                color: "var(--fg-3)",
              }}
            >
              <div
                style={{
                  fontFamily: "var(--mono)",
                  whiteSpace: "pre",
                  lineHeight: 1.2,
                }}
              >
                {`╔════════════════╗
║ checkpoint.lck ║
║ memory.db   OK ║
║ ws ${conn.padEnd(11)}║
╚════════════════╝`}
              </div>
            </div>
          </aside>
          )}

          <main className="brut-main">{children}</main>
        </div>

        <footer className="brut-footbar">
          <div className="lhs">
            {isSetup ? (
              <>
                <span className="blink">setup.wizard</span>
                <span>● waiting for submit</span>
              </>
            ) : (
              <>
                <span className="blink">orchestrator.heartbeat</span>
                <span>● run {shortHash(snapshot?.runId, 8)}</span>
                <span>
                  ● phase{" "}
                  <span style={{ color: "var(--fg-1)" }}>
                    {snapshot?.currentPhase ?? "—"}
                  </span>
                </span>
              </>
            )}
          </div>
          <div className="rhs">
            <span>ws {conn}</span>
            <span style={{ color: "var(--accent)" }}>● LIVE</span>
          </div>
        </footer>
      </div>
    </>
  );
}
