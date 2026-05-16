import { useEffect, useMemo, useState } from "react";

export interface AgentNode {
  id: string;
  role: string;
  status: "busy" | "idle";
  tokens: number;
  cost: number;
}

interface Props {
  agents: AgentNode[];
  width?: number;
  height?: number;
}

export function AgentGraph({
  agents,
  width = 640,
  height = 460,
}: Props): JSX.Element {
  const cx = width / 2;
  const cy = height / 2;
  const rX = width * 0.40;
  const rY = height * 0.40;

  const positions = useMemo(() => {
    const n = Math.max(agents.length, 1);
    return agents.map((a, i) => {
      const angle = -Math.PI / 2 + (i * 2 * Math.PI) / n;
      return {
        ...a,
        x: cx + rX * Math.cos(angle),
        y: cy + rY * Math.sin(angle),
      };
    });
  }, [agents, cx, cy, rX, rY]);

  const edges = positions.map((p, i) => ({
    id: `e${i}`,
    x1: cx,
    y1: cy,
    x2: p.x,
    y2: p.y,
    active: p.status === "busy",
  }));

  const [tick, setTick] = useState(0);
  useEffect(() => {
    let raf = 0;
    const start = performance.now();
    const loop = (t: number) => {
      setTick((t - start) / 1000);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);

  const activeEdges = edges.filter((e) => e.active);

  return (
    <div className="agraph" style={{ width: "100%", height }}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="xMidYMid meet"
      >
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

        {edges.map((e) => (
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

        {activeEdges.map((e, idx) => {
          const phase = (tick * 0.7 + idx * 0.27) % 1;
          const tt = phase < 0.5 ? phase * 2 : 1 - (phase - 0.5) * 2;
          const x = e.x1 + (e.x2 - e.x1) * tt;
          const y = e.y1 + (e.y2 - e.y1) * tt;
          return (
            <circle
              key={`lum-${e.id}`}
              cx={x}
              cy={y}
              r="3.5"
              className="lumen"
            />
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

      {positions.map((p) => (
        <div
          key={p.id}
          className={`node ${p.status}`}
          style={{ left: p.x, top: p.y }}
        >
          <div className="role">{p.role}</div>
          <div className="name">{p.id}</div>
          <div className="stat-line">
            <span>{(p.tokens / 1000).toFixed(1)}K tok</span>
            <span className="v">${p.cost.toFixed(2)}</span>
          </div>
        </div>
      ))}
    </div>
  );
}
