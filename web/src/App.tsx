import { useEffect, useMemo, useState } from "react";
import { useRunStore } from "./store/runStore";
import { classifyView, type ActiveView } from "./lib/classifyView";
import { useWsClient } from "./ws/useWsClient";
import { AppShell } from "./components/AppShell";
import { RunDashboard } from "./views/RunDashboard";
import { ConflictResolution } from "./views/ConflictResolution";
import { PlanReview } from "./views/PlanReview";
import { JudgeVerdict } from "./views/JudgeVerdict";
import { Report } from "./views/Report";
import { Setup } from "./views/Setup";

export function App(): JSX.Element {
  const clientRef = useWsClient();
  const conn = useRunStore((s) => s.conn);
  const snapshot = useRunStore((s) => s.snapshot);
  const mode = useRunStore((s) => s.mode);
  const clearCancelError = useRunStore((s) => s.clearCancelError);

  const activeView = classifyView(snapshot, mode);
  const [selectedView, setSelectedView] = useState<ActiveView>(activeView);

  // Auto-route to whatever view the snapshot indicates is the "live"
  // checkpoint. Local clicks let the user inspect a different view in the
  // meantime, but the next state push snaps back so they don't miss the
  // gate that actually wants attention.
  useEffect(() => {
    setSelectedView(activeView);
  }, [activeView]);

  // Proactively pull the user into an actionable awaiting_human gate
  // (plan review / conflict / judge verdict). The effect above only reacts
  // to ``activeView`` *transitions*, so a manual detour to the dashboard
  // would otherwise strand the user there while the orchestrator is parked
  // waiting on their decision — forcing them to hunt for the nav "OPEN"
  // badge. Re-asserting on every snapshot push closes that gap. While
  // parked the snapshot is stable (the orchestrator emits no frames until
  // the user acts), so this never fights a deliberate step-away between
  // pushes.
  const atActionableGate =
    snapshot?.status === "awaiting_human" &&
    (activeView === "plan_review" ||
      activeView === "conflict_resolution" ||
      activeView === "judge_verdict");
  useEffect(() => {
    if (atActionableGate) setSelectedView(activeView);
  }, [atActionableGate, activeView, snapshot]);

  // Cancel is only honoured by the bridge at AWAITING_HUMAN (see
  // ``ws_bridge._handle_cancel_run``); from any other live status the
  // server replies with ``cancel_error`` and the existing toast on
  // RunDashboard surfaces the reason. Keep the button clickable so the
  // user can *learn* that fact instead of staring at a grey button, and
  // only hard-disable it once the run is genuinely over.
  const status = snapshot?.status;
  const isTerminal = status === "completed" || status === "failed";
  const inAwaitingHuman = status === "awaiting_human";
  const cancelDisabled = isTerminal;
  const cancelTitle = isTerminal
    ? "Run already finished"
    : inAwaitingHuman
      ? "Cancel the current run"
      : `Cancel only takes effect at AWAITING_HUMAN (current: ${status ?? "—"}) — kill the \`merge\` process to stop mid-phase`;
  const onCancel = useMemo(
    () => () => {
      clearCancelError();
      clientRef.current?.send({ type: "cancel_run", payload: {} });
    },
    [clearCancelError, clientRef],
  );

  const renderView = (): JSX.Element => {
    if (selectedView === "setup") return <Setup clientRef={clientRef} />;
    if (selectedView === "report") return <Report />;
    if (selectedView === "plan_review")
      return <PlanReview clientRef={clientRef} />;
    if (selectedView === "conflict_resolution")
      return <ConflictResolution clientRef={clientRef} />;
    if (selectedView === "judge_verdict")
      return <JudgeVerdict clientRef={clientRef} />;
    return (
      <RunDashboard clientRef={clientRef} onSelectView={setSelectedView} />
    );
  };

  return (
    <AppShell
      snapshot={snapshot}
      conn={conn}
      activeView={activeView}
      selectedView={selectedView}
      onSelectView={setSelectedView}
      onCancel={onCancel}
      cancelDisabled={cancelDisabled}
      cancelTitle={cancelTitle}
    >
      {renderView()}
    </AppShell>
  );
}
