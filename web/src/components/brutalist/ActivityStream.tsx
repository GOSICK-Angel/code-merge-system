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
  // Track whether the user is "pinned to bottom" so we only auto-scroll on
  // new events when they were already following the live tail. If they
  // scrolled up to inspect older lines, leave them there.
  const pinnedToBottom = useRef(true);

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    if (pinnedToBottom.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [events.length]);

  const handleScroll = (e: React.UIEvent<HTMLDivElement>): void => {
    const el = e.currentTarget;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    pinnedToBottom.current = distance < 24;
  };

  return (
    <div
      className="stream"
      ref={scroller}
      onScroll={handleScroll}
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
