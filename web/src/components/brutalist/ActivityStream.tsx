import { useEffect, useRef } from "react";
import type { AgentActivityEvent } from "../../types/state";

interface Props {
  events: AgentActivityEvent[];
}

function formatElapsed(elapsed: number | null): string {
  if (elapsed === null || elapsed === undefined) return "--:--:--";
  const total = Math.max(0, Math.floor(elapsed));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return [h, m, s].map((n) => String(n).padStart(2, "0")).join(":");
}

export function ActivityStream({ events }: Props): JSX.Element {
  const scroller = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (scroller.current) {
      scroller.current.scrollTop = scroller.current.scrollHeight;
    }
  }, [events.length]);

  return (
    <div
      className="stream"
      ref={scroller}
      style={{ overflowY: "auto" }}
    >
      {events.length === 0 && (
        <div
          style={{
            padding: "12px var(--pad)",
            color: "var(--fg-3)",
            fontSize: 11,
          }}
        >
          waiting for agent activity ...
        </div>
      )}
      {events.map((e, i) => (
        <div key={i} className={`line ${e.event_type}`}>
          <span className="t">{formatElapsed(e.elapsed)}</span>
          <span className="a">{e.agent}</span>
          <span className="m">
            <b>{e.action}</b>
            {e.phase && (
              <span style={{ color: "var(--fg-3)", marginLeft: 6 }}>
                · {e.phase}
              </span>
            )}
          </span>
        </div>
      ))}
      <div className="caret">
        <span>orchestrator</span>
      </div>
    </div>
  );
}
