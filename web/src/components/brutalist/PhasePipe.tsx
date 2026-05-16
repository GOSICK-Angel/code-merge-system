import { formatDuration } from "../../lib/format";

export interface PipePhase {
  id: string;
  label: string;
  status: "pending" | "running" | "completed" | "failed" | "skipped";
  elapsed: number | null;
}

interface Props {
  phases: PipePhase[];
}

function statusClass(s: PipePhase["status"]): string {
  if (s === "completed") return "done";
  if (s === "running") return "run";
  return "pending";
}

function statusGlyph(s: PipePhase["status"], i: number): string {
  if (s === "completed") return "✓";
  if (s === "running") return "›";
  if (s === "failed") return "✗";
  if (s === "skipped") return "·";
  return String(i + 1);
}

export function PhasePipe({ phases }: Props): JSX.Element {
  return (
    <div className="pipe">
      {phases.map((p, i) => (
        <div key={p.id} className={`step ${statusClass(p.status)}`}>
          <div className="glyph">{statusGlyph(p.status, i)}</div>
          <div>
            <div
              style={{
                color: "var(--fg-0)",
                fontSize: 11.5,
                fontFamily: "var(--mono)",
              }}
            >
              {p.label}
            </div>
            <div className="meta">
              {p.status.replace("_", " ").toUpperCase()}
            </div>
          </div>
          <div className="meta">{formatDuration(p.elapsed)}</div>
        </div>
      ))}
    </div>
  );
}
