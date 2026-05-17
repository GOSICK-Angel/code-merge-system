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

  const cancelDisabled = snapshot?.status !== "awaiting_human";
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
    return <RunDashboard clientRef={clientRef} />;
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
    >
      {renderView()}
    </AppShell>
  );
}
