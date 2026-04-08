import React from "react";
import { Text } from "ink";
import type { VerdictType } from "../../state/types.js";

interface VerdictBadgeProps {
  verdict: VerdictType;
}

const VERDICT_CONFIG: Record<VerdictType, { color: string; icon: string }> = {
  pass: { color: "green", icon: "✓" },
  conditional: { color: "yellow", icon: "⚠" },
  fail: { color: "red", icon: "✗" },
};

export function VerdictBadge({ verdict }: VerdictBadgeProps) {
  const config = VERDICT_CONFIG[verdict] ?? { color: "gray", icon: "?" };
  return (
    <Text color={config.color} bold>
      {config.icon} {verdict.toUpperCase()}
    </Text>
  );
}
