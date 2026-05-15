import { useRunStore } from "./store/runStore";
import { classifyView } from "./lib/classifyView";
import { useWsClient } from "./ws/useWsClient";
import { RunDashboard } from "./views/RunDashboard";
import { ConflictResolution } from "./views/ConflictResolution";
import { PlanReview } from "./views/PlanReview";

export function App(): JSX.Element {
  const clientRef = useWsClient();
  const snapshot = useRunStore((s) => s.snapshot);
  const view = classifyView(snapshot);

  if (view === "plan_review") {
    return <PlanReview clientRef={clientRef} />;
  }
  if (view === "conflict_resolution") {
    return <ConflictResolution clientRef={clientRef} />;
  }
  return <RunDashboard clientRef={clientRef} />;
}
