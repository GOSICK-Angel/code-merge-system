import type { CostSummary } from "../types/state";
import { formatCurrency, formatTokens } from "../lib/format";

interface Props {
  cost: CostSummary | null | undefined;
}

export function CostCard({ cost }: Props): JSX.Element {
  const totalCost = typeof cost?.total_cost_usd === "number" ? cost.total_cost_usd : undefined;
  const totalTokens = typeof cost?.total_tokens === "number" ? cost.total_tokens : undefined;

  return (
    <div className="bg-slate-900 border border-slate-800 rounded p-3">
      <div className="text-xs text-slate-500 uppercase tracking-wider mb-1">
        Cost
      </div>
      <div className="text-lg font-semibold text-slate-100">
        {formatCurrency(totalCost)}
      </div>
      <div className="text-xs text-slate-400 mt-1">
        {formatTokens(totalTokens)} tokens
      </div>
    </div>
  );
}
