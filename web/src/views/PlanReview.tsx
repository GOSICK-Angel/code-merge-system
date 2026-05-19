import { useEffect, useMemo, useState } from "react";
import type { WsClient } from "../ws/client";
import type { OutboundMessage } from "../ws/messages";
import { useRunStore } from "../store/runStore";
import {
  commitApprove,
  commitModify,
  commitReject,
  usePlanReviewDraftStore,
} from "../store/planReviewDraftStore";
import type {
  CategorySummaryPayload,
  MergePlanPayload,
  PendingUserDecision,
  PlanLayer,
  PlanPhaseBatch,
  PlanReviewRoundPayload,
  ReviewConclusionPayload,
  RiskSummaryPayload,
} from "../types/state";
import { Card, Pill } from "../components/brutalist";
import type { PillTone } from "../components/brutalist";

interface Props {
  clientRef: React.MutableRefObject<WsClient | null>;
}

function riskTone(risk: string): PillTone {
  if (risk === "auto_safe") return "green";
  if (risk === "auto_risky") return "orange";
  if (risk === "human_required") return "red";
  if (risk === "binary") return "teal";
  return "";
}

function batchesForLayer(
  plan: MergePlanPayload | null,
  layerId: number,
): PlanPhaseBatch[] {
  if (!plan) return [];
  return plan.phases.filter((b) => b.layer_id === layerId);
}

const PHASE_LAYER_LABELS: Record<string, string> = {
  auto_merge: "Auto merge",
  conflict_analysis: "Conflict analysis",
};

// When the planner emits phases without a layer breakdown (layer_id=None on
// every batch), fall back to grouping by `phase` so the MERGE PLAN card
// still renders something useful instead of "no merge plan available yet".
function syntheticLayers(plan: MergePlanPayload | null): {
  layers: PlanLayer[];
  batchesById: Map<number, PlanPhaseBatch[]>;
} {
  if (!plan) return { layers: [], batchesById: new Map() };
  const groups = new Map<string, PlanPhaseBatch[]>();
  for (const b of plan.phases) {
    const key = b.phase || "unknown";
    const arr = groups.get(key) ?? [];
    arr.push(b);
    groups.set(key, arr);
  }
  const layers: PlanLayer[] = [];
  const batchesById = new Map<number, PlanPhaseBatch[]>();
  let id = 0;
  for (const [phase, batches] of groups) {
    layers.push({
      layer_id: id,
      name: PHASE_LAYER_LABELS[phase] ?? phase,
      description: "",
      depends_on: [],
    });
    batchesById.set(id, batches);
    id += 1;
  }
  return { layers, batchesById };
}

const RISK_BUCKETS: {
  key: string;
  field: keyof RiskSummaryPayload;
  color: string;
  tone: PillTone;
}[] = [
  { key: "auto_safe", field: "auto_safe_count", color: "var(--green)", tone: "green" },
  { key: "auto_risky", field: "auto_risky_count", color: "var(--orange)", tone: "orange" },
  { key: "human_required", field: "human_required_count", color: "var(--red)", tone: "red" },
  { key: "deleted_only", field: "deleted_only_count", color: "var(--amber-dim)", tone: "amber" },
  { key: "binary", field: "binary_count", color: "var(--teal-dim)", tone: "teal" },
  { key: "excluded", field: "excluded_count", color: "var(--bg-hi)", tone: "" },
];

const CATEGORY_BUCKETS: {
  key: string;
  field: keyof CategorySummaryPayload;
  color: string;
}[] = [
  { key: "A · unchanged", field: "a_unchanged", color: "var(--fg-3)" },
  { key: "B · upstream only", field: "b_upstream_only", color: "var(--green)" },
  { key: "C · both changed", field: "c_both_changed", color: "var(--orange)" },
  { key: "D · missing", field: "d_missing", color: "var(--red)" },
  { key: "D · extra", field: "d_extra", color: "var(--amber)" },
  { key: "E · current only", field: "e_current_only", color: "var(--teal)" },
];

function PlannerSummary({
  plan,
  pending,
  onFocusFile,
}: {
  plan: MergePlanPayload;
  pending: PendingUserDecision[];
  onFocusFile: (filePath: string) => void;
}): JSX.Element {
  const r = plan.risk_summary;
  const c = plan.category_summary;
  const rate = Math.max(0, Math.min(1, r.estimated_auto_merge_rate ?? 0));
  const ratePct = (rate * 100).toFixed(1);
  const total = r.total_files ?? 0;
  const ctx = plan.project_context_summary?.trim() ?? "";
  const instructions = plan.special_instructions ?? [];
  const pendingByPath = useMemo(() => {
    const m = new Map<string, string>();
    for (const p of pending) m.set(p.file_path, p.item_id);
    return m;
  }, [pending]);
  const top = (r.top_risk_files ?? []).slice(0, 8);

  return (
    <Card
      title="› PLANNER SUMMARY"
      hint={`${total.toLocaleString()} files · est. auto-merge ${ratePct}%`}
    >
      <div className="risk-grid">
        {RISK_BUCKETS.map((b) => {
          const n = (r[b.field] as number) ?? 0;
          const pct = total > 0 ? (100 * n) / total : 0;
          return (
            <div key={b.key} className="risk-row">
              <div className="lbl">
                <span
                  className="swatch"
                  style={{ background: b.color }}
                />
                <code>{b.key}</code>
              </div>
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
                    background: b.color,
                  }}
                />
              </div>
              <div className="num">{n.toLocaleString()}</div>
            </div>
          );
        })}
      </div>

      <div
        style={{
          marginTop: 10,
          display: "flex",
          alignItems: "center",
          gap: 10,
        }}
      >
        <span className="dim" style={{ fontSize: 10, minWidth: 110 }}>
          estimated auto-merge
        </span>
        <div
          style={{
            flex: 1,
            height: 6,
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
            fontFamily: "var(--mono)",
            fontSize: 11,
            fontVariantNumeric: "tabular-nums",
            minWidth: 56,
            textAlign: "right",
          }}
        >
          {ratePct}%
        </span>
      </div>

      {c && (
        <div
          style={{
            marginTop: 12,
            paddingTop: 10,
            borderTop: "1px dashed var(--line)",
          }}
        >
          <div
            className="dim"
            style={{ fontSize: 10, marginBottom: 6, letterSpacing: "0.06em" }}
          >
            CHANGE CATEGORY
          </div>
          <div
            style={{ display: "flex", flexWrap: "wrap", gap: 8, fontSize: 11 }}
          >
            {CATEGORY_BUCKETS.map((b) => {
              const n = (c[b.field] as number) ?? 0;
              if (n === 0) return null;
              return (
                <span
                  key={b.key}
                  className="pill"
                  style={{
                    borderColor: b.color,
                    color: "var(--fg-1)",
                    fontFamily: "var(--mono)",
                    fontSize: 10,
                  }}
                >
                  <span
                    style={{
                      width: 6,
                      height: 6,
                      background: b.color,
                      display: "inline-block",
                      marginRight: 6,
                    }}
                  />
                  {b.key}{" "}
                  <span
                    style={{
                      color: "var(--fg-0)",
                      marginLeft: 4,
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {n.toLocaleString()}
                  </span>
                </span>
              );
            })}
          </div>
        </div>
      )}

      {top.length > 0 && (
        <div
          style={{
            marginTop: 12,
            paddingTop: 10,
            borderTop: "1px dashed var(--line)",
          }}
        >
          <div
            className="dim"
            style={{ fontSize: 10, marginBottom: 6, letterSpacing: "0.06em" }}
          >
            TOP RISK FILES
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {top.map((fp) => {
              const isPending = pendingByPath.has(fp);
              return (
                <button
                  key={fp}
                  type="button"
                  onClick={() => onFocusFile(fp)}
                  className="btn ghost"
                  style={{
                    textAlign: "left",
                    fontFamily: "var(--mono)",
                    fontSize: 11,
                    padding: "4px 8px",
                    color: isPending ? "var(--fg-0)" : "var(--fg-2)",
                    cursor: isPending ? "pointer" : "default",
                  }}
                  disabled={!isPending}
                  title={
                    isPending
                      ? "focus this file in HUMAN_REQUIRED"
                      : "not pending — view-only"
                  }
                >
                  {fp}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {ctx && (
        <div
          style={{
            marginTop: 12,
            paddingTop: 10,
            borderTop: "1px dashed var(--line)",
          }}
        >
          <div
            className="dim"
            style={{ fontSize: 10, marginBottom: 6, letterSpacing: "0.06em" }}
          >
            PROJECT CONTEXT
          </div>
          <div
            style={{
              fontSize: 11.5,
              color: "var(--fg-1)",
              whiteSpace: "pre-wrap",
              lineHeight: 1.5,
            }}
          >
            {ctx}
          </div>
        </div>
      )}

      {instructions.length > 0 && (
        <details
          style={{
            marginTop: 12,
            paddingTop: 10,
            borderTop: "1px dashed var(--line)",
          }}
        >
          <summary
            className="dim"
            style={{
              fontSize: 10,
              letterSpacing: "0.06em",
              cursor: "pointer",
              userSelect: "none",
            }}
          >
            SPECIAL INSTRUCTIONS · {instructions.length}
          </summary>
          <ul
            style={{
              marginTop: 6,
              paddingLeft: 18,
              fontSize: 11.5,
              color: "var(--fg-1)",
              lineHeight: 1.6,
            }}
          >
            {instructions.map((line, i) => {
              const lines = line.split("\n");
              const isLong = lines.length > 4 || line.length > 300;
              const preview = lines.slice(0, 3).join("\n");
              const rest = lines.slice(3).join("\n");
              return (
                <li key={i} style={{ fontFamily: "var(--mono)" }}>
                  {!isLong ? (
                    line
                  ) : (
                    <details>
                      <summary
                        style={{ cursor: "pointer", userSelect: "none" }}
                      >
                        {preview}
                        <span className="dim"> …</span>
                      </summary>
                      <span style={{ whiteSpace: "pre-wrap" }}>{rest}</span>
                    </details>
                  )}
                </li>
              );
            })}
          </ul>
        </details>
      )}
    </Card>
  );
}

// Drop the "[seg1: revision_needed(2 issues); ...]" tail that planner_judge
// appends to verdict_summary — readable in JSON, noise in the UI. Keep the
// short lead sentence ("Reviewed 23 segments covering 1782 files. ...").
function trimVerdictSummary(s: string): string {
  if (!s) return "";
  const idx = s.indexOf("[seg");
  return (idx >= 0 ? s.slice(0, idx) : s).trim();
}

function verdictTone(verdict: string): PillTone {
  const v = verdict.toLowerCase();
  if (v === "approved") return "green";
  if (v === "revised" || v === "needs_revision" || v === "revision_needed")
    return "amber";
  return "";
}

function NegotiationRound({
  r,
}: {
  r: PlanReviewRoundPayload;
}): JSX.Element {
  const verdict = r.verdict_result.toLowerCase();
  const cls =
    verdict === "approved"
      ? "approved"
      : verdict === "revised" ||
          verdict === "needs_revision" ||
          verdict === "revision_needed"
        ? "revised"
        : "";

  const accepted = r.planner_responses.filter(
    (p) => p.action.toLowerCase() === "accept",
  ).length;
  const rejected = r.planner_responses.filter(
    (p) => p.action.toLowerCase() === "reject",
  ).length;
  const discussed = Math.max(
    0,
    r.planner_responses.length - accepted - rejected,
  );
  const diffCount = r.plan_diff.length;
  const summary = trimVerdictSummary(r.verdict_summary);
  const topIssues = r.issues_detail.slice(0, 5);
  const moreIssues = Math.max(0, r.issues_detail.length - topIssues.length);
  const topDiffs = r.plan_diff.slice(0, 5);
  const moreDiffs = Math.max(0, diffCount - topDiffs.length);

  return (
    <div className={`nego-round ${cls}`}>
      <div className="who">
        ROUND {r.round_number} · <b>planner_judge</b>{" "}
        <span style={{ marginLeft: 6 }}>
          <Pill tone={verdictTone(r.verdict_result)}>{r.verdict_result}</Pill>
        </span>
      </div>

      <div className="nego-counts">
        <span className="pill" title="judge-flagged issues this round">
          issues <b>{r.issues_count}</b>
        </span>
        {r.planner_responses.length > 0 && (
          <>
            <span
              className="pill"
              style={{ borderColor: "var(--green)", color: "var(--fg-1)" }}
              title="planner accepted these issues"
            >
              accepted <b>{accepted}</b>
            </span>
            <span
              className="pill"
              style={{ borderColor: "var(--red)", color: "var(--fg-1)" }}
              title="planner rejected these issues"
            >
              rejected <b>{rejected}</b>
            </span>
            {discussed > 0 && (
              <span className="pill" title="still under discussion">
                discussing <b>{discussed}</b>
              </span>
            )}
          </>
        )}
        {diffCount > 0 && (
          <span
            className="pill"
            style={{ borderColor: "var(--amber)", color: "var(--fg-1)" }}
            title="files whose risk class was changed in this round"
          >
            risk changes <b>{diffCount}</b>
          </span>
        )}
      </div>

      {summary && <div className="nego-summary">{summary}</div>}

      {r.planner_revision_summary && (
        <div className="nego-revision">
          <span className="dim">PLANNER › </span>
          {r.planner_revision_summary}
        </div>
      )}

      {topIssues.length > 0 && (
        <div className="nego-issues">
          <div className="nego-section-h">JUDGE ISSUES</div>
          {topIssues.map((it, j) => {
            const fp = (it.file_path as string) ?? "(file?)";
            const cur = (it.current as string) ?? "";
            const sug = (it.suggested as string) ?? "";
            const reason = (it.reason as string) ?? "";
            return (
              <div key={j} className="nego-issue">
                <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                  <code className="fp">{fp}</code>
                  {cur && sug && (
                    <span className="risk-shift">
                      <code>{cur}</code>
                      <span className="arrow"> → </span>
                      <code>{sug}</code>
                    </span>
                  )}
                </div>
                {reason && <div className="why">{reason}</div>}
              </div>
            );
          })}
          {moreIssues > 0 && (
            <div className="dim more">+ {moreIssues} more issues</div>
          )}
        </div>
      )}

      {topDiffs.length > 0 && (
        <div className="nego-diff">
          <div className="nego-section-h">RISK CHANGES APPLIED</div>
          {topDiffs.map((d, j) => (
            <div key={j} className="nego-diff-row">
              <code className="fp">{d.file_path}</code>
              <span className="risk-shift">
                <code>{d.old_risk}</code>
                <span className="arrow"> → </span>
                <code>{d.new_risk}</code>
              </span>
            </div>
          ))}
          {moreDiffs > 0 && (
            <div className="dim more">+ {moreDiffs} more changes</div>
          )}
        </div>
      )}

      {r.negotiation_messages.length > 0 && (
        <details className="nego-raw">
          <summary className="dim">
            raw negotiation_messages · {r.negotiation_messages.length}
          </summary>
          {r.negotiation_messages.map((mm, j) => (
            <div key={j} className="msg">
              <span className="dim" style={{ fontSize: 10 }}>
                {mm.sender}
              </span>
              {" · "}
              {mm.content}
            </div>
          ))}
        </details>
      )}
    </div>
  );
}

const CONCLUSION_COPY: Record<
  string,
  { tone: PillTone; headline: string; body: string }
> = {
  max_rounds: {
    tone: "amber",
    headline: "Plan did not converge — max revision rounds reached",
    body: "Planner and judge could not agree within the configured budget. Approving keeps the last revised plan; modify to give the planner more guidance; reject to abort this run.",
  },
  stalled: {
    tone: "amber",
    headline: "Plan revision stalled",
    body: "Two consecutive judge rounds raised the same issues — the planner is not making progress. Your sign-off is required before the run can move on.",
  },
  llm_failure: {
    tone: "red",
    headline: "LLM error during plan revision",
    body: "The planner/judge loop terminated because of an LLM error. Inspect the negotiation log below before approving.",
  },
  converged: {
    tone: "green",
    headline: "Plan converged",
    body: "Planner and judge agreed on the plan. Confirm to proceed to auto_merge.",
  },
};

function ConclusionBanner({
  conclusion,
}: {
  conclusion: ReviewConclusionPayload;
}): JSX.Element {
  const reason = (conclusion.reason || "").toLowerCase();
  const copy =
    CONCLUSION_COPY[reason] ?? {
      tone: "amber" as PillTone,
      headline: `Plan review ended: ${conclusion.reason || "unknown"}`,
      body:
        conclusion.summary ||
        "The planner/judge loop terminated. Your sign-off is required before the run can move on.",
    };
  return (
    <div className={`conclusion-banner mb-2 tone-${copy.tone || "neutral"}`}>
      <div className="row" style={{ gap: 10, alignItems: "center" }}>
        <Pill tone={copy.tone}>{conclusion.reason || "concluded"}</Pill>
        <div className="headline">{copy.headline}</div>
      </div>
      <div className="body">{copy.body}</div>
      <div className="meta">
        <span>
          round <b>{conclusion.final_round}</b> / max{" "}
          <b>{conclusion.max_rounds}</b>
        </span>
        <span>
          total <b>{conclusion.total_rounds}</b>
        </span>
        {conclusion.pending_decisions_count > 0 && (
          <span>
            pending decisions <b>{conclusion.pending_decisions_count}</b>
          </span>
        )}
      </div>
      {conclusion.summary &&
        conclusion.summary.trim() !== copy.body.trim() && (
          <div className="summary">{conclusion.summary}</div>
        )}
    </div>
  );
}

function NegotiationTimeline({
  rounds,
}: {
  rounds: PlanReviewRoundPayload[];
}): JSX.Element {
  if (rounds.length === 0) {
    return (
      <div className="dim" style={{ fontSize: 11, padding: "10px 0" }}>
        no negotiation rounds yet
      </div>
    );
  }
  return (
    <div className="negotiation">
      {rounds.map((r, i) => (
        <NegotiationRound key={i} r={r} />
      ))}
    </div>
  );
}

function PendingDecisionCard({
  item,
  active,
  draftChoice,
  draftInput,
  onSelect,
  onSetChoice,
  onSetInput,
  onClear,
  decidedServerSide,
}: {
  item: PendingUserDecision;
  active: boolean;
  draftChoice: string | undefined;
  draftInput: string | undefined;
  onSelect: () => void;
  onSetChoice: (key: string) => void;
  onSetInput: (input: string) => void;
  onClear: () => void;
  decidedServerSide: boolean;
}): JSX.Element {
  const cls =
    item.current_classification === "human_required" ? "red" : "amber";
  return (
    <div className={`pending-item ${active ? "active" : ""}`}>
      <div className="row between" style={{ alignItems: "flex-start" }}>
        <div className="grow">
          <div className="fp" onClick={onSelect}>
            {item.file_path}
          </div>
          <div className="row mt-1">
            <Pill tone={cls as PillTone}>
              {item.current_classification ?? "pending"}
            </Pill>
            {draftChoice && (
              <Pill tone="amber">draft: {draftChoice}</Pill>
            )}
            {item.user_choice && (
              <Pill tone="green">committed: {item.user_choice}</Pill>
            )}
          </div>
        </div>
      </div>
      {item.description && (
        <div className="why">
          <span className="dimmer">RATIONALE › </span>
          {item.description}
        </div>
      )}
      {item.options.length > 0 && (
        <div
          className="row mt-1"
          style={{ flexWrap: "wrap", gap: 6 }}
        >
          {item.options.map((opt) => (
            <button
              key={opt.key}
              type="button"
              onClick={() => onSetChoice(opt.key)}
              disabled={decidedServerSide}
              title={opt.description}
              style={{
                fontSize: 10.5,
                padding: "3px 7px",
                background:
                  draftChoice === opt.key
                    ? "color-mix(in oklch, var(--accent), transparent 85%)"
                    : "var(--bg-0)",
                border: `1px solid ${
                  draftChoice === opt.key ? "var(--accent)" : "var(--line)"
                }`,
                color:
                  draftChoice === opt.key
                    ? "var(--accent)"
                    : "var(--fg-2)",
                cursor: decidedServerSide ? "not-allowed" : "pointer",
                fontFamily: "var(--mono)",
              }}
            >
              {opt.label}
            </button>
          ))}
        </div>
      )}
      <textarea
        value={draftInput ?? ""}
        onChange={(e) => onSetInput(e.target.value)}
        disabled={decidedServerSide}
        placeholder="optional reviewer note for this item ..."
        style={{
          marginTop: 8,
          width: "100%",
          minHeight: 44,
          background: "var(--bg-0)",
          border: "1px solid var(--line)",
          color: "var(--fg-1)",
          fontFamily: "var(--mono)",
          fontSize: 11,
          padding: 8,
          resize: "vertical",
        }}
      />
      <div className="actions">
        {draftChoice && !decidedServerSide && (
          <button
            type="button"
            className="btn ghost"
            onClick={onClear}
          >
            ✗ CLEAR
          </button>
        )}
      </div>
    </div>
  );
}

export function PlanReview({ clientRef }: Props): JSX.Element {
  const snapshot = useRunStore((s) => s.snapshot);

  const drafts = usePlanReviewDraftStore((s) => s.drafts);
  const notes = usePlanReviewDraftStore((s) => s.notes);
  const setNotes = usePlanReviewDraftStore((s) => s.setNotes);
  const setDraft = usePlanReviewDraftStore((s) => s.setDraft);
  const setDraftInput = usePlanReviewDraftStore((s) => s.setDraftInput);
  const clearDraft = usePlanReviewDraftStore((s) => s.clearDraft);
  const applyRecommendedToAll = usePlanReviewDraftStore(
    (s) => s.applyRecommendedToAll,
  );

  const [selectedId, setSelectedId] = useState<string | null>(null);

  const items = useMemo<PendingUserDecision[]>(
    () => snapshot?.pendingUserDecisions ?? [],
    [snapshot],
  );
  const pending = useMemo(
    () => items.filter((i) => i.user_choice === null),
    [items],
  );
  const draftedCount = useMemo(
    () => pending.filter((i) => drafts[i.item_id]?.user_choice).length,
    [pending, drafts],
  );

  const serverDecided = snapshot?.planHumanReview != null;
  // Bridge `_apply_plan_review` only takes effect while the orchestrator
  // is parked in HUMAN_REVIEW awaiting our signal — if the run already
  // moved on to AUTO_MERGING / JUDGE_REVIEWING / ... a REJECT click sets
  // `state.plan_human_review` into a phase that no longer reads it, so
  // patches keep landing on fork_ref. Gate every batch action on the
  // status the bridge can actually act on.
  const inAwaitingHuman = snapshot?.status === "awaiting_human";

  useEffect(() => {
    if (selectedId && items.some((i) => i.item_id === selectedId)) return;
    if (pending.length > 0) setSelectedId(pending[0].item_id);
    else if (items.length > 0) setSelectedId(items[0].item_id);
  }, [items, pending, selectedId]);

  const plan = snapshot?.mergePlan ?? null;
  const rawLayers: PlanLayer[] = plan?.layers ?? [];
  const synthetic = useMemo(() => syntheticLayers(plan), [plan]);
  const usingSynthetic = rawLayers.length === 0 && (plan?.phases.length ?? 0) > 0;
  const layers: PlanLayer[] = usingSynthetic ? synthetic.layers : rawLayers;
  const totalBatches = plan?.phases.length ?? 0;
  const rounds = snapshot?.planReviewLog ?? [];
  const conclusion = snapshot?.reviewConclusion ?? null;
  // Plan-level sign-off mode: planner/judge loop terminated (conclusion
  // recorded) but the human hasn't responded yet. APPROVE / MODIFY /
  // REJECT should be live even when there are zero per-file pending
  // items — the reviewer is signing off on the plan as a whole.
  const awaitingPlanSignoff =
    !serverDecided && conclusion != null && inAwaitingHuman;
  const canSubmit =
    !serverDecided && inAwaitingHuman && (pending.length > 0 || awaitingPlanSignoff);

  const send = (msg: OutboundMessage) => clientRef.current?.send(msg);

  const onApproveAll = () => commitApprove(send, pending, drafts, notes);
  const onReject = () => commitReject(send, notes);
  const onModify = () => commitModify(send, pending, drafts, notes);
  const onApplyRecommended = () => applyRecommendedToAll(pending);

  return (
    <div>
      <div
        className="row between mb-2"
        style={{ alignItems: "flex-end" }}
      >
        <div>
          <h1>
            Plan review —{" "}
            <span className="dim">
              {rounds.length > 0
                ? `${rounds.length} round${rounds.length > 1 ? "s" : ""}`
                : "awaiting"}
            </span>
          </h1>
          <div className="subhead">
            {layers.length} layers · {totalBatches} batches ·{" "}
            <b style={{ color: "var(--fg-0)" }}>{pending.length}</b> require
            human approval before <code>auto_merge</code>
          </div>
        </div>
        <div className="row">
          {serverDecided ? (
            <Pill tone="green">
              {snapshot?.planHumanReview?.decision ?? "DECIDED"}
            </Pill>
          ) : (
            (() => {
              // Pill always tracks ``snapshot.status`` — never assume
              // the view is mounted only during awaiting_human. A stale
              // WS snapshot can leave this page rendered after the
              // orchestrator already advanced; the suffix (plan sign-off
              // / pending count) only applies inside the awaiting_human
              // branch.
              const status = (snapshot?.status ?? "—").toUpperCase();
              if (!inAwaitingHuman) {
                return <Pill tone="">{status}</Pill>;
              }
              const suffix = awaitingPlanSignoff
                ? " · plan sign-off"
                : ` · ${pending.length}`;
              return (
                <Pill tone="orange" live>
                  {status}
                  {suffix}
                </Pill>
              );
            })()
          )}
        </div>
      </div>

      {awaitingPlanSignoff && conclusion && (
        <ConclusionBanner conclusion={conclusion} />
      )}

      {plan && (
        <div className="mb-2">
          <PlannerSummary
            plan={plan}
            pending={pending}
            onFocusFile={(fp) => {
              const match = pending.find((p) => p.file_path === fp);
              if (match) setSelectedId(match.item_id);
            }}
          />
        </div>
      )}

      <div className="plan-grid">
        <div className="col">
          <Card title="› MERGE PLAN — LAYERS" hint={`${totalBatches} batches`}>
            {layers.length === 0 ? (
              <div
                className="dim"
                style={{ fontSize: 11, padding: "12px 0" }}
              >
                no merge plan available yet
              </div>
            ) : (
              layers.map((layer) => {
                const batches = usingSynthetic
                  ? synthetic.batchesById.get(layer.layer_id) ?? []
                  : batchesForLayer(plan, layer.layer_id);
                return (
                  <div key={layer.layer_id} className="layer-block">
                    <div className="lhead">
                      <div className="left">
                        <span className="layer-id">L{layer.layer_id}</span>
                        <span style={{ color: "var(--fg-0)" }}>
                          {layer.name}
                        </span>
                        <span className="dim">
                          · {batches.length} batches
                        </span>
                      </div>
                      <div className="dim" style={{ fontSize: 11 }}>
                        {layer.depends_on.length === 0
                          ? "no deps"
                          : `depends_on: [${layer.depends_on.join(", ")}]`}
                      </div>
                    </div>
                    <div className="batches">
                      {batches.slice(0, 12).map((b) => {
                        const head = b.file_paths[0] ?? "(no files)";
                        const more = Math.max(0, b.file_paths.length - 1);
                        return (
                          <div key={b.batch_id} className="batch">
                            <div>
                              <div className="id">{b.batch_id}</div>
                              <div className="name">
                                {head}
                                {more > 0 && (
                                  <span className="dim">
                                    {" "}
                                    + {more} more
                                  </span>
                                )}
                              </div>
                            </div>
                            <div className="meta">
                              <Pill tone={riskTone(b.risk_level)}>
                                {b.risk_level}
                              </Pill>
                            </div>
                            <div
                              className="meta"
                              style={{ fontFamily: "var(--mono)" }}
                            >
                              {b.file_paths.length}{" "}
                              <span className="dim">
                                file{b.file_paths.length === 1 ? "" : "s"}
                              </span>
                            </div>
                          </div>
                        );
                      })}
                      {batches.length > 12 && (
                        <div
                          className="dim"
                          style={{ fontSize: 10, padding: "4px 10px" }}
                        >
                          + {batches.length - 12} more batches
                        </div>
                      )}
                    </div>
                  </div>
                );
              })
            )}
          </Card>

          <Card
            title="› PLANNER ↔ JUDGE NEGOTIATION"
            hint={`${rounds.length} round${rounds.length === 1 ? "" : "s"}`}
          >
            <NegotiationTimeline rounds={rounds} />
          </Card>
        </div>

        <div className="col">
          <Card
            title={`› HUMAN_REQUIRED · ${pending.length}`}
            hint={`drafted ${draftedCount}/${pending.length}`}
          >
            {items.length === 0 ? (
              <div
                className="dim"
                style={{ fontSize: 11, padding: "12px 0", lineHeight: 1.6 }}
              >
                {awaitingPlanSignoff ? (
                  <>
                    no per-file decisions required — planner classified every
                    file as <code>auto_safe</code> / <code>auto_risky</code>.
                    Use the actions below to{" "}
                    <b style={{ color: "var(--fg-1)" }}>approve</b>,{" "}
                    <b style={{ color: "var(--fg-1)" }}>modify</b> (re-run the
                    planner with notes) or{" "}
                    <b style={{ color: "var(--fg-1)" }}>reject</b> the plan as
                    a whole.
                  </>
                ) : (
                  "no pending plan items"
                )}
              </div>
            ) : (
              items.map((u) => {
                const active = u.item_id === selectedId;
                const d = drafts[u.item_id];
                return (
                  <PendingDecisionCard
                    key={u.item_id}
                    item={u}
                    active={active}
                    draftChoice={d?.user_choice}
                    draftInput={d?.user_input}
                    onSelect={() => setSelectedId(u.item_id)}
                    onSetChoice={(k) => setDraft(u.item_id, k)}
                    onSetInput={(v) => setDraftInput(u.item_id, v)}
                    onClear={() => clearDraft(u.item_id)}
                    decidedServerSide={serverDecided}
                  />
                );
              })
            )}
          </Card>

          <Card title="› BATCH ACTIONS">
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              disabled={serverDecided}
              placeholder="shared reviewer_notes for approve / reject / modify ..."
              style={{
                width: "100%",
                minHeight: 56,
                background: "var(--bg-0)",
                border: "1px solid var(--line)",
                color: "var(--fg-1)",
                fontFamily: "var(--mono)",
                fontSize: 11,
                padding: 8,
                marginBottom: 10,
                resize: "vertical",
              }}
            />
            <div
              className="row"
              style={{ flexWrap: "wrap", gap: 8 }}
            >
              <button
                type="button"
                className="btn"
                onClick={onApplyRecommended}
                disabled={serverDecided || pending.length === 0 || !inAwaitingHuman}
                title={
                  !inAwaitingHuman
                    ? `run is ${snapshot?.status ?? "—"} — orchestrator is not waiting on plan review`
                    : undefined
                }
                style={{ flex: 1, justifyContent: "center" }}
              >
                APPLY DEFAULT
              </button>
              <button
                type="button"
                className="btn primary"
                onClick={onApproveAll}
                disabled={!canSubmit}
                style={{ flex: 1, justifyContent: "center" }}
                title={
                  !inAwaitingHuman && !serverDecided
                    ? `run is ${snapshot?.status ?? "—"} — orchestrator is not waiting on plan review`
                    : awaitingPlanSignoff && pending.length === 0
                      ? "approve the non-converged plan as-is"
                      : undefined
                }
              >
                {awaitingPlanSignoff && pending.length === 0
                  ? "APPROVE PLAN"
                  : "APPROVE ALL"}
              </button>
              <button
                type="button"
                className="btn"
                onClick={onModify}
                disabled={serverDecided || !inAwaitingHuman}
                title={
                  !inAwaitingHuman
                    ? `run is ${snapshot?.status ?? "—"} — orchestrator is not waiting on plan review`
                    : undefined
                }
                style={{ flex: 1, justifyContent: "center" }}
              >
                MODIFY
              </button>
              <button
                type="button"
                className="btn danger"
                onClick={onReject}
                disabled={serverDecided || !inAwaitingHuman}
                title={
                  !inAwaitingHuman
                    ? `run is ${snapshot?.status ?? "—"} — orchestrator is not waiting on plan review`
                    : undefined
                }
                style={{ flex: 1, justifyContent: "center" }}
              >
                REJECT
              </button>
            </div>
            <div
              className="dim mt-2"
              style={{ fontSize: 10.5, lineHeight: 1.6 }}
            >
              <code>submit_user_plan_decisions</code> +{" "}
              <code>submit_plan_review</code> · two-step protocol per plan
              v1.1 §P1-3
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
