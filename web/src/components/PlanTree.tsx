import { useMemo, useState } from "react";
import type { MergePlanPayload } from "../types/state";

interface Props {
  plan: MergePlanPayload | null;
}

const RISK_COLOR: Record<string, string> = {
  low: "text-emerald-400",
  medium: "text-amber-400",
  high: "text-rose-400",
  critical: "text-rose-500 font-bold",
};

const CATEGORY_ICON: Record<string, string> = {
  cosmetic: "✎",
  refactor: "↻",
  bugfix: "✓",
  feature: "+",
  breaking: "!",
  dependency: "□",
  test: "T",
  docs: "¶",
  configuration: "⚙",
};

export function PlanTree({ plan }: Props): JSX.Element {
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set());
  const layers = useMemo(() => plan?.layers ?? [], [plan]);
  const phases = useMemo(() => plan?.phases ?? [], [plan]);

  const phasesByLayer = useMemo(() => {
    const grouped: Record<number, typeof phases> = {};
    for (const p of phases) {
      (grouped[p.layer_id] ??= []).push(p);
    }
    return grouped;
  }, [phases]);

  function toggle(layerId: number): void {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(layerId)) {
        next.delete(layerId);
      } else {
        next.add(layerId);
      }
      return next;
    });
  }

  if (!plan) {
    return (
      <section className="p-3 text-xs text-slate-500 italic">
        Plan not loaded yet.
      </section>
    );
  }

  return (
    <section className="border-b border-slate-800">
      <header className="px-3 py-2 flex items-center justify-between">
        <h2 className="text-xs font-medium text-slate-400 uppercase tracking-wider">
          Plan layers ({layers.length})
        </h2>
        <span className="text-[10px] text-slate-500 font-mono">
          {phases.length} batches · plan {plan.plan_id.slice(0, 8)}
        </span>
      </header>
      <ul className="px-2 pb-2 space-y-1.5">
        {layers.map((layer) => {
          const isCollapsed = collapsed.has(layer.layer_id);
          const layerPhases = phasesByLayer[layer.layer_id] ?? [];
          return (
            <li key={layer.layer_id} className="rounded border border-slate-800">
              <button
                type="button"
                onClick={() => toggle(layer.layer_id)}
                className="w-full px-2 py-1.5 flex items-center gap-2 text-left hover:bg-slate-900/60"
              >
                <span className="text-slate-500 w-4">
                  {isCollapsed ? "▸" : "▾"}
                </span>
                <span className="text-xs text-slate-200 font-medium">
                  L{layer.layer_id} · {layer.name}
                </span>
                <span className="text-[10px] text-slate-500 font-mono ml-auto">
                  {layerPhases.length} batch
                  {layerPhases.length === 1 ? "" : "es"}
                </span>
              </button>
              {!isCollapsed && (
                <ul className="px-2 pb-2 space-y-0.5">
                  {layerPhases.map((b) => (
                    <li key={b.batch_id} className="text-xs">
                      <div className="flex items-baseline gap-2 py-1">
                        <span
                          className={`font-mono w-4 ${
                            RISK_COLOR[b.risk_level] ?? "text-slate-400"
                          }`}
                          title={`risk: ${b.risk_level}`}
                        >
                          {b.change_category
                            ? (CATEGORY_ICON[b.change_category] ?? "•")
                            : "•"}
                        </span>
                        <span className="text-slate-300 font-mono flex-1 truncate">
                          {b.batch_id} · {b.file_paths.length} file
                          {b.file_paths.length === 1 ? "" : "s"}
                        </span>
                      </div>
                      <ul className="pl-6 text-[11px] text-slate-500 font-mono">
                        {b.file_paths.slice(0, 5).map((fp) => (
                          <li key={fp} className="truncate">
                            {fp}
                          </li>
                        ))}
                        {b.file_paths.length > 5 && (
                          <li className="italic text-slate-600">
                            +{b.file_paths.length - 5} more
                          </li>
                        )}
                      </ul>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
