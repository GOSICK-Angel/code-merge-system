import { useRunStore } from "./store/runStore";
import { classifyView } from "./lib/classifyView";
import { useWsClient } from "./ws/useWsClient";
import { RunDashboard } from "./views/RunDashboard";
import { ConflictResolution } from "./views/ConflictResolution";
import { PlanReview } from "./views/PlanReview";
import { JudgeVerdict } from "./views/JudgeVerdict";
import { Report } from "./views/Report";

export function App(): JSX.Element {
  const clientRef = useWsClient();
  const snapshot = useRunStore((s) => s.snapshot);
  const view = classifyView(snapshot);

  if (view === "report") {
    return <Report />;
  }
  if (view === "plan_review") {
    return <PlanReview clientRef={clientRef} />;
  }
  if (view === "conflict_resolution") {
    return <ConflictResolution clientRef={clientRef} />;
  }
  if (view === "judge_verdict") {
    return <JudgeVerdict clientRef={clientRef} />;
  }
  return <RunDashboard clientRef={clientRef} />;
}
