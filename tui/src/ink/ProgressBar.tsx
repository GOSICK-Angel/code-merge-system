import React from "react";
import { Text } from "ink";

interface ProgressBarProps {
  value: number;
  max: number;
  width?: number;
  color?: string;
}

export function ProgressBar({
  value,
  max,
  width = 30,
  color = "green",
}: ProgressBarProps) {
  const ratio = max > 0 ? Math.min(value / max, 1) : 0;
  const filled = Math.round(ratio * width);
  const empty = width - filled;
  const pct = Math.round(ratio * 100);

  return (
    <Text>
      <Text color="white">[</Text>
      <Text color={color}>{"█".repeat(filled)}</Text>
      <Text color="gray">{"░".repeat(empty)}</Text>
      <Text color="white">]</Text>
      <Text color="gray"> {pct}%</Text>
    </Text>
  );
}
