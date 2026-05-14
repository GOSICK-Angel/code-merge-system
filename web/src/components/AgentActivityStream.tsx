import { useEffect, useRef } from "react";
import type { AgentActivityEvent } from "../types/state";
import { formatDuration } from "../lib/format";

interface Props {
  events: AgentActivityEvent[];
}

const EVENT_COLOR: Record<AgentActivityEvent["event_type"], string> = {
  start: "text-sky-400",
  progress: "text-slate-300",
  complete: "text-emerald-400",
  error: "text-rose-400",
};

export function AgentActivityStream({ events }: Props): JSX.Element {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (ref.current) {
      ref.current.scrollTop = ref.current.scrollHeight;
    }
  }, [events.length]);

  if (events.length === 0) {
    return (
      <section className="flex-1 p-4">
        <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-2">
          Agent activity
        </h2>
        <p className="text-sm text-slate-500 italic">No activity yet.</p>
      </section>
    );
  }

  return (
    <section className="flex-1 flex flex-col p-4 min-h-0">
      <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-2">
        Agent activity ({events.length})
      </h2>
      <div
        ref={ref}
        className="flex-1 overflow-y-auto font-mono text-xs space-y-1 pr-2"
      >
        {events.map((e, idx) => (
          <div key={idx} className="flex gap-2 leading-relaxed">
            <span className="text-slate-600 flex-shrink-0 w-16">
              {e.phase}
            </span>
            <span className="text-slate-300 flex-shrink-0 w-24 truncate">
              {e.agent}
            </span>
            <span className={`flex-1 ${EVENT_COLOR[e.event_type]}`}>
              {e.action}
            </span>
            {e.elapsed !== null && (
              <span className="text-slate-500 flex-shrink-0">
                {formatDuration(e.elapsed)}
              </span>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
