import { useRunStore } from "./store/runStore";
import { classifyView } from "./lib/classifyView";
import { useWsClient } from "./ws/useWsClient";
import { RunDashboard } from "./views/RunDashboard";
import { ConflictResolution } from "./views/ConflictResolution";

export function App(): JSX.Element {
  const clientRef = useWsClient();
  const snapshot = useRunStore((s) => s.snapshot);
  const view = classifyView(snapshot);

  if (view === "conflict_resolution") {
    return <ConflictResolution clientRef={clientRef} />;
  }
  return <RunDashboard clientRef={clientRef} />;
}
