import { useEffect, useMemo, useRef, useState } from "react";
import type { AgentRuntime } from "../../lib/agents";

export interface AgentNode {
  id: string;
  role: string;
  tokens: number;
  cost: number;
}

interface Props {
  agents: AgentNode[];
  runtime: AgentRuntime;
  width?: number;
  height?: number;
}

// A comm edge stays drawn/animated for this long after it fired.
const COMM_TTL_SECONDS = 6;

function fmtElapsed(seconds: number): string {
  if (seconds < 0) seconds = 0;
  if (seconds < 60) return `${seconds.toFixed(0)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}m ${String(s).padStart(2, "0")}s`;
}

export function AgentGraph({
  agents,
  runtime,
  width = 640,
  height = 460,
}: Props): JSX.Element {
  // The SVG scales to its container via viewBox, but the agent node overlays
  // are absolutely positioned in raw pixels. Driving the geometry off the
  // measured container width (rather than the fixed `width` prop) keeps the
  // nodes aligned with the SVG and inside the card on any column size.
  const containerRef = useRef<HTMLDivElement>(null);
  const [measuredWidth, setMeasuredWidth] = useState(width);
  useEffect(() => {
    const el = containerRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w && w > 0) setMeasuredWidth(w);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const w = measuredWidth;
  const h = height;
  const cx = w / 2;
  const cy = h / 2;
  const rX = w * 0.4;
  const rY = h * 0.4;

  const positions = useMemo(() => {
    const n = Math.max(agents.length, 1);
    const map: Record<string, { x: number; y: number }> = {};
    const list = agents.map((a, i) => {
      const angle = -Math.PI / 2 + (i * 2 * Math.PI) / n;
      const x = cx + rX * Math.cos(angle);
      const y = cy + rY * Math.sin(angle);
      map[a.id] = { x, y };
      return { ...a, x, y };
    });
    return { list, map };
  }, [agents, cx, cy, rX, rY]);

  // One ticking clock drives both the live elapsed timers and the comm-edge
  // fade-out, plus the lumen animation along active edges.
  const [tick, setTick] = useState(0);
  const [nowSec, setNowSec] = useState(() => Date.now() / 1000);
  useEffect(() => {
    let raf = 0;
    const start = performance.now();
    const loop = (t: number) => {
      setTick((t - start) / 1000);
      setNowSec(Date.now() / 1000);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);

  // Star edges: orchestrator ↔ each agent, lit while the agent is running.
  const starEdges = positions.list.map((p) => ({
    id: `s-${p.id}`,
    x1: cx,
    y1: cy,
    x2: p.x,
    y2: p.y,
    active: runtime.states[p.id]?.running ?? false,
  }));

  // Live communication edges (directed agent → agent), faded by age.
  const liveComms = runtime.comms
    .map((c) => ({ ...c, age: nowSec - c.at }))
    .filter((c) => c.at > 0 && c.age <= COMM_TTL_SECONDS)
    .map((c) => {
      const a = positions.map[c.from];
      const b = positions.map[c.to];
      if (!a || !b) return null;
      return { ...c, ax: a.x, ay: a.y, bx: b.x, by: b.y };
    })
    .filter((c): c is NonNullable<typeof c> => c !== null);

  const activeStar = starEdges.filter((e) => e.active);

  return (
    <div ref={containerRef} className="agraph" style={{ width: "100%", height: h }}>
      <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="xMidYMid meet">
        <defs>
          <marker
            id="comm-arrow"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--amber)" />
          </marker>
        </defs>

        <circle
          cx={cx}
          cy={cy}
          r={Math.min(rX, rY) * 0.55}
          stroke="var(--line)"
          fill="none"
          strokeDasharray="2 4"
        />
        <circle
          cx={cx}
          cy={cy}
          r={Math.min(rX, rY) * 0.78}
          stroke="var(--line)"
          fill="none"
          strokeDasharray="2 6"
        />

        {starEdges.map((e) => (
          <line
            key={e.id}
            x1={e.x1}
            y1={e.y1}
            x2={e.x2}
            y2={e.y2}
            className={`edge ${e.active ? "active" : ""}`}
            strokeDasharray={e.active ? "0" : "3 5"}
          />
        ))}

        {activeStar.map((e, idx) => {
          const phase = (tick * 0.7 + idx * 0.27) % 1;
          const tt = phase < 0.5 ? phase * 2 : 1 - (phase - 0.5) * 2;
          const x = e.x1 + (e.x2 - e.x1) * tt;
          const y = e.y1 + (e.y2 - e.y1) * tt;
          return <circle key={`lum-${e.id}`} cx={x} cy={y} r="3.5" className="lumen" />;
        })}

        {/* Directed communication edges (agent → agent). */}
        {liveComms.map((c, i) => {
          const opacity = Math.max(0.15, 1 - c.age / COMM_TTL_SECONDS);
          const mx = (c.ax + c.bx) / 2;
          const my = (c.ay + c.by) / 2;
          return (
            <g key={`comm-${i}-${c.at}`} opacity={opacity}>
              <line
                x1={c.ax}
                y1={c.ay}
                x2={c.bx}
                y2={c.by}
                stroke="var(--amber)"
                strokeWidth="1.5"
                markerEnd="url(#comm-arrow)"
              />
              <text
                x={mx}
                y={my - 4}
                fill="var(--amber)"
                fontSize="9"
                fontFamily="var(--mono)"
                textAnchor="middle"
              >
                {c.from} ▸ {c.to}: {c.label}
              </text>
            </g>
          );
        })}

        <g>
          <polygon
            points={`${cx},${cy - 24} ${cx + 24},${cy} ${cx},${cy + 24} ${cx - 24},${cy}`}
            fill="var(--bg-2)"
            stroke="var(--accent)"
            strokeWidth="1.5"
          />
          <polygon
            points={`${cx},${cy - 12} ${cx + 12},${cy} ${cx},${cy + 12} ${cx - 12},${cy}`}
            fill="var(--accent)"
            opacity="0.4"
          />
        </g>
      </svg>

      <div className="center-label" style={{ marginTop: 38 }}>
        <div className="core">ORCHESTRATOR</div>
        <div style={{ marginTop: 4 }}>state_machine.run()</div>
      </div>

      {positions.list.map((p) => {
        const st = runtime.states[p.id];
        const running = st?.running ?? false;
        const elapsed =
          running && st?.startedAt != null
            ? fmtElapsed(nowSec - st.startedAt)
            : st?.lastElapsed != null
              ? fmtElapsed(st.lastElapsed)
              : null;
        return (
          <div
            key={p.id}
            className={`node ${running ? "busy" : "idle"}`}
            style={{ left: p.x, top: p.y }}
          >
            <div className="role">{p.role}</div>
            <div className="name">{p.id}</div>
            <div className="stat-line">
              <span>
                {running ? st?.action || "running" : "idle"}
                {elapsed ? ` · ${elapsed}` : ""}
              </span>
            </div>
            <div className="stat-line">
              <span>{((p.tokens || 0) / 1000).toFixed(1)}K tok</span>
              <span className="v">×{st?.calls ?? 0}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
