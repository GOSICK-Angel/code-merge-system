import { useState } from "react";
import type { ConflictPoint } from "../types/state";

interface Props {
  cp: ConflictPoint;
  idx: number;
}

const SEVERITY_COLOR: Record<ConflictPoint["severity"], string> = {
  high: "border-rose-500 bg-rose-900/40",
  medium: "border-amber-500 bg-amber-900/40",
  low: "border-slate-500 bg-slate-900/40",
};

const SEVERITY_DOT: Record<ConflictPoint["severity"], string> = {
  high: "bg-rose-400",
  medium: "bg-amber-400",
  low: "bg-slate-400",
};

export function ConflictPointMarker({ cp, idx }: Props): JSX.Element {
  const [open, setOpen] = useState(false);
  return (
    <div className={`text-xs border-l-2 pl-2 py-1 ${SEVERITY_COLOR[cp.severity]}`}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 text-left"
      >
        <span
          className={`inline-block h-2 w-2 rounded-full ${SEVERITY_DOT[cp.severity]}`}
        />
        <span className="text-slate-400 font-mono w-6">#{idx + 1}</span>
        <span className="flex-1 text-slate-200 truncate">{cp.description}</span>
        {cp.line_range && (
          <span className="text-slate-500 font-mono">{cp.line_range}</span>
        )}
        <span className="text-slate-500">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="mt-2 space-y-1.5 text-[11px] leading-relaxed">
          {cp.upstream_intent && (
            <div>
              <span className="text-sky-400 font-medium">Upstream intent</span>
              <span className="text-slate-500"> · {cp.upstream_intent.intent_type}</span>
              <span className="text-slate-500"> · confidence{" "}
                {cp.upstream_intent.confidence.toFixed(2)}
              </span>
              <div className="text-slate-300 ml-3">
                {cp.upstream_intent.description}
              </div>
            </div>
          )}
          {cp.fork_intent && (
            <div>
              <span className="text-emerald-400 font-medium">Fork intent</span>
              <span className="text-slate-500"> · {cp.fork_intent.intent_type}</span>
              <span className="text-slate-500"> · confidence{" "}
                {cp.fork_intent.confidence.toFixed(2)}
              </span>
              <div className="text-slate-300 ml-3">
                {cp.fork_intent.description}
              </div>
            </div>
          )}
          <div>
            <span className="text-slate-400 font-medium">Rationale: </span>
            <span className="text-slate-300">{cp.rationale}</span>
          </div>
          {cp.risk_factors.length > 0 && (
            <div>
              <span className="text-slate-400 font-medium">Risk factors: </span>
              {cp.risk_factors.map((r, i) => (
                <span
                  key={i}
                  className="inline-block px-1.5 py-0.5 mr-1 mt-1 rounded bg-slate-800 text-slate-300 font-mono"
                >
                  {r}
                </span>
              ))}
            </div>
          )}
          {cp.suggested_decision && (
            <div className="text-slate-400">
              Analyst suggests:{" "}
              <code className="text-slate-200">{cp.suggested_decision}</code>
              {cp.can_coexist !== null && (
                <span>
                  {" · can coexist: "}
                  <code className="text-slate-200">
                    {cp.can_coexist ? "yes" : "no"}
                  </code>
                </span>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
