import { useMemo } from "react";
import { useRunStore } from "../store/runStore";
import type { WsClient } from "../ws/client";
import type { ActiveView } from "../lib/classifyView";
import type { MergePlanPayload, MergeStateSnapshot } from "../types/state";
import {
  AgentGraph,
  ActivityStream,
  AsciiBar,
  Card,
  PhasePipe,
  Pill,
} from "../components/brutalist";
import type { AgentNode, PipePhase } from "../components/brutalist";

interface Props {
  // clientRef is part of the API every view exposes but the dashboard
  // currently only reads from the store — keep the prop so the App shell
  // doesn't need a special case.
  clientRef: React.MutableRefObject<WsClient | null>;
  // Optional: lets the Planner Summary card deep-link into the full Plan
  // Review view. Omitted when rendered outside the App shell (e.g. tests).
  onSelectView?: (v: ActiveView) => void;
}

const PHASE_ORDER: { id: string; label: string }[] = [
  { id: "analysis", label: "analysis" },
  { id: "plan_review", label: "plan_review" },
  { id: "plan_revising", label: "plan_revising" },
  { id: "auto_merge", label: "auto_merge" },
  { id: "conflict_analysis", label: "conflict_analysis" },
  { id: "human_review", label: "human_review" },
  { id: "judge_review", label: "judge_review" },
  { id: "report", label: "report" },
];

const RISK_BUCKETS: {
  key: string;
  match: (v: string) => boolean;
  color: string;
}[] = [
  {
    key: "auto_safe",
    match: (v) => v === "auto_safe",
    color: "var(--green)",
  },
  {
    key: "auto_risky",
    match: (v) => v === "auto_risky",
    color: "var(--orange)",
  },
  {
    key: "human_required",
    match: (v) => v === "human_required",
    color: "var(--red)",
  },
  {
    key: "deleted_only",
    match: (v) => v === "deleted_only" || v === "deleted",
    color: "var(--amber-dim)",
  },
  {
    key: "binary",
    match: (v) => v === "binary",
    color: "var(--teal-dim)",
  },
  {
    key: "excluded",
    match: (v) => v === "excluded" || v === "ignored",
    color: "var(--bg-hi)",
  },
];

const DECISION_TONES: Record<string, string> = {
  take_target: "green",
  take_current: "amber",
  semantic_merge: "teal",
  escalate_human: "red",
  manual_patch: "orange",
  skip: "",
  pending: "orange",
};

function tonelessColor(tone: string): string {
  if (tone === "amber") return "var(--amber)";
  if (tone === "green") return "var(--green)";
  if (tone === "orange") return "var(--orange)";
  if (tone === "red") return "var(--red)";
  if (tone === "teal") return "var(--teal)";
  return "var(--fg-2)";
}

function derivePhases(snapshot: MergeStateSnapshot | null): PipePhase[] {
  const results = snapshot?.phaseResults ?? {};
  const elapsed = snapshot?.phaseElapsed ?? {};
  return PHASE_ORDER.map((p) => {
    const r = results[p.id];
    const e = elapsed[p.id] ?? null;
    return {
      id: p.id,
      label: p.label,
      status: (r?.status ?? "pending") as PipePhase["status"],
      elapsed: e,
    };
  });
}

function deriveRisk(snapshot: MergeStateSnapshot | null): {
  total: number;
  counts: Record<string, number>;
} {
  const counts: Record<string, number> = {};
  for (const bucket of RISK_BUCKETS) counts[bucket.key] = 0;
  const classifications = Object.values(snapshot?.fileClassifications ?? {});
  for (const v of classifications) {
    const bucket = RISK_BUCKETS.find((b) => b.match(v));
    if (bucket) counts[bucket.key] += 1;
  }
  return { total: classifications.length, counts };
}

function deriveAgents(snapshot: MergeStateSnapshot | null): AgentNode[] {
  const byAgent = snapshot?.costSummary?.by_agent ?? {};
  const phase = snapshot?.currentPhase ?? "";
  const phaseHint = phase.split("_")[0] ?? "";
  return Object.entries(byAgent).map(([id, v]) => ({
    id,
    role: id.replace(/_/g, " "),
    status:
      phaseHint && id.toLowerCase().includes(phaseHint.toLowerCase())
        ? "busy"
        : "idle",
    cost: v.cost_usd ?? 0,
    tokens: v.tokens ?? 0,
  }));
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

const PLANNER_BUCKETS: { key: string; field: keyof PlannerBucketCounts; color: string }[] = [
  { key: "auto_safe", field: "auto_safe_count", color: "var(--green)" },
  { key: "auto_risky", field: "auto_risky_count", color: "var(--orange)" },
  { key: "human_required", field: "human_required_count", color: "var(--red)" },
  { key: "deleted_only", field: "deleted_only_count", color: "var(--amber-dim)" },
  { key: "binary", field: "binary_count", color: "var(--teal-dim)" },
  { key: "excluded", field: "excluded_count", color: "var(--bg-hi)" },
];

interface PlannerBucketCounts {
  auto_safe_count: number;
  auto_risky_count: number;
  human_required_count: number;
  deleted_only_count: number;
  binary_count: number;
  excluded_count: number;
}

function PlannerSummaryStrip({
  plan,
  onOpenPlanReview,
}: {
  plan: MergePlanPayload;
  onOpenPlanReview?: () => void;
}): JSX.Element {
  const r = plan.risk_summary;
  const rate = Math.max(0, Math.min(1, r.estimated_auto_merge_rate ?? 0));
  const ratePct = (rate * 100).toFixed(1);
  const ctx = plan.project_context_summary?.trim() ?? "";
  const instr = plan.special_instructions?.length ?? 0;

  return (
    <div className="hairline mb-2" style={{ padding: "10px 14px" }}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "auto 1fr auto",
          alignItems: "center",
          gap: 18,
        }}
      >
        <div
          style={{
            fontFamily: "var(--mono)",
            fontSize: 10,
            letterSpacing: "0.08em",
            color: "var(--fg-3)",
          }}
        >
          › PLANNER SUMMARY
        </div>
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: 14,
            justifyContent: "flex-start",
            fontFamily: "var(--mono)",
            fontSize: 11.5,
          }}
        >
          {PLANNER_BUCKETS.map((b) => (
            <div
              key={b.key}
              style={{ display: "flex", alignItems: "center", gap: 6 }}
            >
              <span
                style={{
                  width: 8,
                  height: 8,
                  background: b.color,
                  display: "inline-block",
                }}
              />
              <code style={{ color: "var(--fg-2)" }}>{b.key}</code>
              <span
                style={{
                  color: "var(--fg-0)",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {r[b.field].toLocaleString()}
              </span>
            </div>
          ))}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              marginLeft: 6,
            }}
          >
            <span className="dim" style={{ fontSize: 10 }}>
              est. auto-merge
            </span>
            <div
              style={{
                width: 120,
                height: 4,
                background: "var(--bg-3)",
                position: "relative",
              }}
            >
              <div
                style={{
                  position: "absolute",
                  inset: 0,
                  width: `${ratePct}%`,
                  background: "var(--green)",
                }}
              />
            </div>
            <span
              style={{
                color: "var(--fg-0)",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {ratePct}%
            </span>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          {instr > 0 && (
            <span className="dim" style={{ fontSize: 10 }}>
              {instr} special instr.
            </span>
          )}
          {onOpenPlanReview && (
            <button
              type="button"
              className="btn ghost"
              onClick={onOpenPlanReview}
              style={{ fontSize: 10 }}
            >
              open plan review →
            </button>
          )}
        </div>
      </div>
      {ctx && (
        <div
          className="dim"
          title={ctx}
          style={{
            marginTop: 6,
            fontSize: 11,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          context: {ctx}
        </div>
      )}
    </div>
  );
}

interface BudgetBarProps {
  spent: number;
  limit: number | null;
  warnPct: number;
}

function BudgetBar({ spent, limit, warnPct }: BudgetBarProps): JSX.Element | null {
  if (limit === null || limit <= 0) {
    return null;
  }
  const ratio = Math.min(1, spent / limit);
  const widthPct = ratio * 100;
  let state: "ok" | "warn" | "exceeded" = "ok";
  let color = "var(--green)";
  if (ratio >= 1) {
    state = "exceeded";
    color = "var(--red)";
  } else if (ratio >= warnPct) {
    state = "warn";
    color = "var(--orange)";
  }
  return (
    <div
      data-testid="budget-bar"
      data-state={state}
      style={{
        marginTop: 6,
        height: 4,
        background: "var(--bg-2)",
        position: "relative",
      }}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(widthPct)}
    >
      <div
        style={{
          width: `${widthPct}%`,
          height: "100%",
          background: color,
          transition: "width 240ms ease",
        }}
      />
    </div>
  );
}

export function RunDashboard(props: Props): JSX.Element {
  const { onSelectView } = props;
  const snapshot = useRunStore((s) => s.snapshot);
  const activity = useRunStore((s) => s.activity);
  const cancelError = useRunStore((s) => s.lastCancelError);
  const clearCancelError = useRunStore((s) => s.clearCancelError);

  const phases = useMemo(() => derivePhases(snapshot), [snapshot]);
  const risk = useMemo(() => deriveRisk(snapshot), [snapshot]);
  const agents = useMemo(() => deriveAgents(snapshot), [snapshot]);

  const totalCost = snapshot?.costSummary?.total_cost_usd ?? 0;
  const totalTokens = snapshot?.costSummary?.total_tokens ?? 0;
  // U2 budget progress bar inputs — null limit means the cap is disabled,
  // in which case BudgetBar renders nothing.
  const budgetLimit = snapshot?.costSummary?.limit_usd ?? null;
  const budgetWarnPct = snapshot?.costSummary?.warn_pct ?? 0.8;

  const merged = Object.values(snapshot?.fileDecisionRecords ?? {}).filter(
    (r) => r.success,
  ).length;
  const pendingPlan = (snapshot?.pendingUserDecisions ?? []).filter(
    (i) => i.user_choice === null,
  ).length;
  const pendingConflict = Object.values(
    snapshot?.humanDecisionRequests ?? {},
  ).filter((r) => r.human_decision === null).length;
  const pendingTotal = pendingPlan + pendingConflict;

  const elapsed = deriveElapsed(snapshot);
  const decisionCounts = snapshot?.decisionRecordCounts ?? {};
  const decisionTotal = Object.values(decisionCounts).reduce(
    (a, b) => a + b,
    0,
  );

  return (
    <div>
      <div
        className="row between mb-2"
        style={{ alignItems: "flex-end" }}
      >
        <div>
          <h1>
            Live merge —{" "}
            <span className="dim">
              {snapshot?.mergePlan?.upstream_ref?.split("/")[0] ?? "—"}
            </span>
          </h1>
          <div className="subhead">
            <code style={{ color: "var(--fg-1)" }}>
              {snapshot?.mergePlan?.upstream_ref ?? "—"}
            </code>
            <span className="dim"> ──▶ </span>
            <code style={{ color: "var(--fg-1)" }}>
              {snapshot?.mergePlan?.fork_ref ?? "—"}
            </code>
            <span className="dim" style={{ marginLeft: 14 }}>
              base ·{" "}
              <code>
                {(snapshot?.mergePlan?.merge_base_commit ?? "—").slice(0, 12)}
              </code>
            </span>
          </div>
        </div>
        <div className="row" style={{ gap: 10 }}>
          <Pill tone="amber" live>
            {(snapshot?.status ?? "INITIALIZING").toString().toUpperCase()}
          </Pill>
          <Pill tone="">
            RUN{" "}
            <span style={{ color: "var(--fg-0)", marginLeft: 4 }}>
              {(snapshot?.runId ?? "—").slice(0, 8)}
            </span>
          </Pill>
        </div>
      </div>

      {cancelError && (
        <div
          className="hairline mb-2"
          style={{
            padding: "10px 14px",
            background: "color-mix(in oklch, var(--red), transparent 88%)",
            color: "var(--fg-0)",
            fontSize: 11.5,
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            borderColor: "var(--red-dim)",
          }}
          role="alert"
        >
          <span>
            Cancel rejected: {cancelError.reason} (current status:{" "}
            <code>{cancelError.current_status}</code>)
          </span>
          <button
            type="button"
            className="btn ghost"
            onClick={clearCancelError}
          >
            dismiss
          </button>
        </div>
      )}

      <div className="dash-stat-row">
        <div className="stat green">
          <div className="label">Files merged</div>
          <div className="value">
            {merged.toLocaleString()}
            <span style={{ color: "var(--fg-3)", fontSize: 18 }}>
              /{risk.total.toLocaleString()}
            </span>
          </div>
          <div className="sub">
            {risk.total > 0
              ? `${((merged / risk.total) * 100).toFixed(1)}%`
              : "—"}{" "}
            · phase {snapshot?.currentPhase ?? "—"}
          </div>
        </div>
        <div className="stat">
          <div className="label">Elapsed</div>
          <div className="value">{elapsed}</div>
          <div className="sub">since {snapshot?.createdAt?.slice(0, 19) ?? "—"}</div>
        </div>
        <div className="stat orange">
          <div className="label">Pending decisions</div>
          <div className="value">
            {pendingTotal}
            <span style={{ color: "var(--fg-3)", fontSize: 18 }}> open</span>
          </div>
          <div className="sub">
            {pendingPlan} plan · {pendingConflict} conflict
          </div>
        </div>
        <div className="stat teal">
          <div className="label">Run cost</div>
          <div className="value">${totalCost.toFixed(2)}</div>
          <div className="sub">
            {(totalTokens / 1000).toFixed(0)}K tokens · {agents.length} agents
          </div>
          <BudgetBar
            spent={totalCost}
            limit={budgetLimit}
            warnPct={budgetWarnPct}
          />
        </div>
      </div>

      {snapshot?.mergePlan && (
        <PlannerSummaryStrip
          plan={snapshot.mergePlan}
          onOpenPlanReview={
            onSelectView ? () => onSelectView("plan_review") : undefined
          }
        />
      )}

      <div className="dash-grid">
        <div className="col">
          <Card title="› PHASE PIPELINE" hint="orchestrator.run()">
            <PhasePipe phases={phases} />
          </Card>
          <Card
            title="› RISK DISTRIBUTION"
            hint={`${risk.total} files`}
          >
            {risk.total > 0 ? (
              <>
                <div className="bar stacked mb-2">
                  {RISK_BUCKETS.map((b) => {
                    const pct =
                      risk.total > 0
                        ? (100 * risk.counts[b.key]) / risk.total
                        : 0;
                    return (
                      <div
                        key={b.key}
                        className="seg"
                        style={{ width: `${pct}%`, background: b.color }}
                      />
                    );
                  })}
                </div>
                {RISK_BUCKETS.map((b) => {
                  const n = risk.counts[b.key];
                  const pct = risk.total > 0 ? (100 * n) / risk.total : 0;
                  return (
                    <div key={b.key} className="risk-row">
                      <div className="lbl">
                        <span
                          className="swatch"
                          style={{ background: b.color }}
                        />
                        <code>{b.key}</code>
                      </div>
                      <div>
                        <AsciiBar pct={pct} width={16} />
                      </div>
                      <div className="num">{n.toLocaleString()}</div>
                    </div>
                  );
                })}
              </>
            ) : (
              <div
                className="dim"
                style={{ fontSize: 11, padding: "12px 0" }}
              >
                no classifications yet
              </div>
            )}
          </Card>
        </div>

        <Card
          title="› AGENT TOPOLOGY"
          hint={`${agents.length} agents · ${agents.filter((a) => a.status === "busy").length} busy`}
          style={{ display: "flex", flexDirection: "column" }}
        >
          {agents.length > 0 ? (
            <div style={{ position: "relative" }}>
              <AgentGraph agents={agents} width={640} height={460} />
            </div>
          ) : (
            <div
              className="dim"
              style={{
                fontSize: 11,
                padding: "48px 0",
                textAlign: "center",
              }}
            >
              waiting for agents to register cost ...
            </div>
          )}
        </Card>

        <Card
          title="› AGENT ACTIVITY"
          hint="live"
          pad={false}
          style={{ display: "flex", flexDirection: "column" }}
        >
          <ActivityStream events={activity} />
        </Card>
      </div>

      <div
        className="dash-stat-row mt-2"
        style={{ gridTemplateColumns: "1.4fr 1fr 1fr" }}
      >
        <Card
          title="› COST BY AGENT"
          hint={`$${totalCost.toFixed(2)} · ${(totalTokens / 1000).toFixed(0)}K tok`}
        >
          {agents.length === 0 ? (
            <div
              className="dim"
              style={{ fontSize: 11, padding: "12px 0" }}
            >
              no cost data yet
            </div>
          ) : (
            agents.map((a) => {
              const pct = totalCost > 0 ? (a.cost / totalCost) * 100 : 0;
              return (
                <div key={a.id} className="cost-grid">
                  <div className="who">{a.id}</div>
                  <div className="val">
                    ${a.cost.toFixed(2)}{" "}
                    <span
                      className="dim"
                      style={{ fontSize: 10 }}
                    >
                      ({pct.toFixed(0)}%)
                    </span>
                  </div>
                  <div className="mini-bar">
                    <div className="f" style={{ width: `${pct}%` }} />
                  </div>
                </div>
              );
            })
          )}
        </Card>

        <Card title="› DECISIONS" hint="record_counts">
          {decisionTotal === 0 ? (
            <div
              className="dim"
              style={{ fontSize: 11, padding: "12px 0" }}
            >
              no decisions recorded
            </div>
          ) : (
            Object.entries(decisionCounts).map(([k, v]) => {
              const tone = DECISION_TONES[k] ?? "";
              const pct = decisionTotal > 0 ? (v / decisionTotal) * 100 : 0;
              return (
                <div
                  key={k}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "auto 1fr auto",
                    gap: 10,
                    alignItems: "center",
                    padding: "6px 0",
                    borderBottom: "1px dashed var(--line)",
                    fontSize: 11.5,
                  }}
                >
                  <code style={{ color: tonelessColor(tone) }}>{k}</code>
                  <div
                    style={{
                      height: 4,
                      background: "var(--bg-3)",
                      position: "relative",
                    }}
                  >
                    <div
                      style={{
                        position: "absolute",
                        inset: 0,
                        width: `${pct}%`,
                        background: tonelessColor(tone),
                      }}
                    />
                  </div>
                  <span
                    style={{
                      fontVariantNumeric: "tabular-nums",
                      color: "var(--fg-0)",
                      minWidth: 50,
                      textAlign: "right",
                    }}
                  >
                    {v.toLocaleString()}
                  </span>
                </div>
              );
            })
          )}
        </Card>

        <Card title="› RECENT PHASES" hint="elapsed">
          <div style={{ fontFamily: "var(--mono)", fontSize: 11.5 }}>
            {Object.entries(snapshot?.phaseResults ?? {}).length === 0 ? (
              <div
                className="dim"
                style={{ fontSize: 11, padding: "12px 0" }}
              >
                no phase results yet
              </div>
            ) : (
              Object.entries(snapshot?.phaseResults ?? {}).map(
                ([phase, result]) => {
                  const elapsedSec =
                    snapshot?.phaseElapsed?.[phase] ?? null;
                  const tone =
                    result.status === "completed"
                      ? "var(--green)"
                      : result.status === "failed"
                        ? "var(--red)"
                        : result.status === "running"
                          ? "var(--accent)"
                          : "var(--fg-3)";
                  return (
                    <div
                      key={phase}
                      style={{
                        display: "grid",
                        gridTemplateColumns: "1fr auto auto",
                        gap: 12,
                        padding: "6px 0",
                        borderBottom: "1px dashed var(--line)",
                        alignItems: "center",
                      }}
                    >
                      <code style={{ color: "var(--fg-0)" }}>{phase}</code>
                      <span
                        className="pill"
                        style={{
                          fontSize: 9,
                          color: tone,
                          borderColor: tone,
                        }}
                      >
                        {result.status}
                      </span>
                      <span
                        className="dim"
                        style={{
                          fontVariantNumeric: "tabular-nums",
                          minWidth: 44,
                          textAlign: "right",
                        }}
                      >
                        {elapsedSec !== null
                          ? `${Math.floor(elapsedSec)}s`
                          : "—"}
                      </span>
                    </div>
                  );
                },
              )
            )}
          </div>
        </Card>
      </div>
    </div>
  );
}
