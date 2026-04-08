import React from "react";
import { Box, Text } from "ink";
import { useAppStore } from "../../state/store.js";

const PHASES = [
  { key: "analysis", label: "Init" },
  { key: "plan_review", label: "Plan" },
  { key: "plan_revising", label: "Revise" },
  { key: "auto_merge", label: "Merge" },
  { key: "conflict_analysis", label: "Conflicts" },
  { key: "human_review", label: "Human" },
  { key: "judge_review", label: "Judge" },
  { key: "report", label: "Report" },
] as const;

export function PhaseTimeline() {
  const currentPhase = useAppStore((s) => s.currentPhase);
  const phaseResults = useAppStore((s) => s.phaseResults);
  const status = useAppStore((s) => s.status);

  return (
    <Box flexDirection="row" gap={1} paddingX={1}>
      {PHASES.map((p) => {
        const result = phaseResults[p.key];
        let icon: string;
        let color: string;

        if (result?.status === "completed") {
          icon = "✓";
          color = "green";
        } else if (result?.status === "failed") {
          icon = "✗";
          color = "red";
        } else if (p.key === currentPhase && status !== "completed" && status !== "failed") {
          icon = "▸";
          color = "cyan";
        } else if (result?.status === "running") {
          icon = "▸";
          color = "cyan";
        } else {
          icon = "○";
          color = "gray";
        }

        return (
          <Box key={p.key}>
            <Text color={color}>
              {icon} {p.label}
            </Text>
          </Box>
        );
      })}
    </Box>
  );
}
