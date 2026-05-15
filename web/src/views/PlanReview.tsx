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
import type { PendingUserDecision } from "../types/state";
import { StatusBanner } from "../components/StatusBanner";
import { PlanTree } from "../components/PlanTree";
import { NegotiationTimeline } from "../components/NegotiationTimeline";
import { PendingDecisionsList } from "../components/PendingDecisionsList";
import { PlanReviewBatchBar } from "../components/PlanReviewBatchBar";
import { PlanDecisionDrawer } from "../components/PlanDecisionDrawer";

interface Props {
  clientRef: React.MutableRefObject<WsClient | null>;
}

export function PlanReview({ clientRef }: Props): JSX.Element {
  const snapshot = useRunStore((s) => s.snapshot);
  const conn = useRunStore((s) => s.conn);

  const drafts = usePlanReviewDraftStore((s) => s.drafts);
  const notes = usePlanReviewDraftStore((s) => s.notes);
  const setNotes = usePlanReviewDraftStore((s) => s.setNotes);
  const applyRecommendedToAll = usePlanReviewDraftStore(
    (s) => s.applyRecommendedToAll,
  );

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState<boolean>(false);

  const items = useMemo<PendingUserDecision[]>(
    () => snapshot?.pendingUserDecisions ?? [],
    [snapshot],
  );
  const pending = useMemo(
    () => items.filter((i) => i.user_choice === null),
    [items],
  );
  const draftedCount = useMemo(
    () =>
      pending.filter((i) => drafts[i.item_id]?.user_choice).length,
    [pending, drafts],
  );
  const unrecommendedCount = useMemo(
    () => pending.filter((i) => !i.options[0]).length,
    [pending],
  );

  const serverDecided = useMemo(() => {
    // M13 — read the authoritative ``plan_human_review`` snapshot field
    // instead of deriving from ``pending``. The previous derived
    // expression was logically equivalent to ``false`` (items inside
    // ``pending`` are by definition undecided), which meant the panel
    // was never read-only during the ~300 ms snapshot debounce window
    // after Submit — a double-click could send two pairs of frames.
    return snapshot?.planHumanReview != null;
  }, [snapshot]);

  // Auto-select first pending item.
  useEffect(() => {
    if (selectedId && items.some((i) => i.item_id === selectedId)) return;
    if (pending.length > 0) setSelectedId(pending[0].item_id);
    else if (items.length > 0) setSelectedId(items[0].item_id);
  }, [items, pending, selectedId]);

  const currentItem = useMemo(
    () => items.find((i) => i.item_id === selectedId) ?? null,
    [items, selectedId],
  );

  const onApplyRecommended = () => {
    applyRecommendedToAll(pending);
  };

  const send = (msg: OutboundMessage) => {
    clientRef.current?.send(msg);
  };

  const onApproveAll = () => {
    commitApprove(send, pending, drafts, notes);
  };

  const onReject = () => {
    commitReject(send, notes);
  };

  const onModify = () => {
    commitModify(send, pending, drafts, notes);
  };

  return (
    <div className="min-h-screen flex flex-col bg-slate-950 text-slate-100">
      <StatusBanner
        runId={snapshot?.runId ?? null}
        status={snapshot?.status ?? null}
        conn={conn}
      />
      <PlanReviewBatchBar
        pendingCount={pending.length}
        draftedCount={draftedCount}
        unrecommendedCount={unrecommendedCount}
        notes={notes}
        onNotesChange={setNotes}
        onApplyRecommended={onApplyRecommended}
        onApproveAll={onApproveAll}
        onReject={onReject}
        onModify={onModify}
        serverDecided={serverDecided}
      />
      <div className="flex flex-1 min-h-0">
        <PendingDecisionsList
          items={items}
          drafts={drafts}
          selectedId={selectedId}
          onSelect={(id) => {
            setSelectedId(id);
            setDrawerOpen(true);
          }}
        />
        <div className="flex-1 overflow-y-auto">
          <PlanTree plan={snapshot?.mergePlan ?? null} />
          <NegotiationTimeline rounds={snapshot?.planReviewLog ?? []} />
        </div>
      </div>
      <PlanDecisionDrawer
        item={currentItem}
        open={drawerOpen && currentItem !== null}
        onClose={() => setDrawerOpen(false)}
      />
    </div>
  );
}
