import { useEffect, useState } from "react";
import { resolveWsUrl } from "./ws/client";

type ConnState = "connecting" | "open" | "closed";

interface StateSnapshot {
  runId?: string;
  status?: string;
  currentPhase?: string;
}

export function App(): JSX.Element {
  const [conn, setConn] = useState<ConnState>("connecting");
  const [snapshot, setSnapshot] = useState<StateSnapshot | null>(null);
  const [lastReason, setLastReason] = useState<string>("");

  useEffect(() => {
    const url = resolveWsUrl();
    const ws = new WebSocket(url);

    ws.onopen = () => {
      setConn("open");
      console.log("WS connected:", url);
    };
    ws.onclose = () => setConn("closed");
    ws.onerror = (e) => console.error("WS error:", e);
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "state_snapshot" || msg.type === "state_patch") {
          setSnapshot(msg.payload as StateSnapshot);
        } else if (msg.type === "agent_activity") {
          setLastReason(`${msg.payload?.agent ?? "?"}: ${msg.payload?.action ?? ""}`);
        }
      } catch (err) {
        console.error("Failed to parse WS message:", err);
      }
    };

    return () => {
      ws.close();
    };
  }, []);

  return (
    <main style={{ fontFamily: "system-ui", padding: 24 }}>
      <h1>Code Merge System</h1>
      <p>
        WS: <strong>{conn}</strong> ({resolveWsUrl()})
      </p>
      {snapshot ? (
        <section>
          <p>
            Run: <code>{snapshot.runId ?? "(none)"}</code>
          </p>
          <p>
            Status: <strong>{snapshot.status ?? "(unknown)"}</strong>
          </p>
          <p>
            Phase: <code>{snapshot.currentPhase ?? "(unknown)"}</code>
          </p>
          {lastReason && (
            <p>
              Latest activity: <code>{lastReason}</code>
            </p>
          )}
        </section>
      ) : (
        <p>Waiting for first state snapshot...</p>
      )}
    </main>
  );
}
