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
  MergePlanPayload,
  PendingUserDecision,
  PlanLayer,
  PlanPhaseBatch,
  PlanReviewRoundPayload,
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
      {rounds.map((r, i) => {
        const verdict = r.verdict_result.toLowerCase();
        const cls =
          verdict === "approved"
            ? "approved"
            : verdict === "revised" || verdict === "needs_revision"
              ? "revised"
              : "";
        return (
          <div key={i} className={`nego-round ${cls}`}>
            <div className="who">
              ROUND {r.round_number} ·{" "}
              <b>planner_judge</b>{" "}
              <span
                style={{
                  marginLeft: 4,
                  color:
                    cls === "approved"
                      ? "var(--green)"
                      : cls === "revised"
                        ? "var(--amber)"
                        : "var(--fg-2)",
                }}
              >
                {r.verdict_result}
              </span>
              <span className="dim" style={{ marginLeft: 8 }}>
                — {r.verdict_summary}
              </span>
            </div>
            {r.negotiation_messages.slice(0, 3).map((mm, j) => (
              <div
                key={j}
                className="msg"
                style={{
                  marginTop: 6,
                  paddingLeft: 14,
                  borderLeft: "1px solid var(--line)",
                }}
              >
                <span className="dim" style={{ fontSize: 10 }}>
                  {mm.sender}
                </span>
                {" · "}
                {mm.content}
              </div>
            ))}
          </div>
        );
      })}
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

  useEffect(() => {
    if (selectedId && items.some((i) => i.item_id === selectedId)) return;
    if (pending.length > 0) setSelectedId(pending[0].item_id);
    else if (items.length > 0) setSelectedId(items[0].item_id);
  }, [items, pending, selectedId]);

  const plan = snapshot?.mergePlan ?? null;
  const layers: PlanLayer[] = plan?.layers ?? [];
  const totalBatches = plan?.phases.length ?? 0;
  const rounds = snapshot?.planReviewLog ?? [];

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
            <Pill tone="orange" live>
              AWAITING_HUMAN · {pending.length}
            </Pill>
          )}
        </div>
      </div>

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
                const batches = batchesForLayer(plan, layer.layer_id);
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
                style={{ fontSize: 11, padding: "12px 0" }}
              >
                no pending plan items
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
                disabled={serverDecided || pending.length === 0}
                style={{ flex: 1, justifyContent: "center" }}
              >
                APPLY DEFAULT
              </button>
              <button
                type="button"
                className="btn primary"
                onClick={onApproveAll}
                disabled={serverDecided || pending.length === 0}
                style={{ flex: 1, justifyContent: "center" }}
              >
                APPROVE ALL
              </button>
              <button
                type="button"
                className="btn"
                onClick={onModify}
                disabled={serverDecided}
                style={{ flex: 1, justifyContent: "center" }}
              >
                MODIFY
              </button>
              <button
                type="button"
                className="btn danger"
                onClick={onReject}
                disabled={serverDecided}
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
