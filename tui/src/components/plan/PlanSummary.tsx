import React from "react";
import { Box, Text } from "ink";
import { useAppStore } from "../../state/store.js";
import { Badge } from "../../ink/Badge.js";

export function PlanSummary() {
  const plan = useAppStore((s) => s.mergePlan);

  if (!plan) {
    return (
      <Box paddingX={1}>
        <Text color="gray">No plan available yet</Text>
      </Box>
    );
  }

  const rs = plan.risk_summary;

  return (
    <Box flexDirection="column" paddingX={1} gap={0}>
      <Box gap={1}>
        <Text bold>Plan</Text>
        <Text color="gray">{plan.plan_id.slice(0, 8)}</Text>
        <Text color="gray">|</Text>
        <Text color="gray">{plan.upstream_ref} → {plan.fork_ref}</Text>
      </Box>
      <Box gap={2}>
        <Badge label={`${rs.auto_safe_count} safe`} variant="success" />
        <Badge label={`${rs.auto_risky_count} risky`} variant="warning" />
        <Badge label={`${rs.human_required_count} human`} variant="danger" />
        <Badge label={`${rs.deleted_only_count} deleted`} variant="muted" />
        <Text color="gray">
          ({Math.round(rs.estimated_auto_merge_rate * 100)}% auto)
        </Text>
      </Box>
      {plan.special_instructions.length > 0 && (() => {
        const MAX_TOTAL_LINES = 14;
        const rendered: React.ReactElement[] = [];
        let usedLines = 0;
        let truncated = false;
        outer: for (let idx = 0; idx < plan.special_instructions.length; idx++) {
          const instr = plan.special_instructions[idx]!;
          const lines = instr.split("\n");
          for (let li = 0; li < lines.length; li++) {
            if (usedLines >= MAX_TOTAL_LINES) {
              truncated = true;
              break outer;
            }
            const raw = lines[li] ?? "";
            const display = raw.length > 200 ? raw.slice(0, 200) + "…" : raw;
            rendered.push(
              <Text key={`${idx}-${li}`} color="yellow">
                {li === 0 ? "  • " : "    "}
                {display}
              </Text>,
            );
            usedLines++;
          }
        }
        return (
          <Box flexDirection="column">
            <Text bold>Special Instructions:</Text>
            {rendered}
            {truncated && (
              <Text color="gray">  … more in plan report</Text>
            )}
          </Box>
        );
      })()}
      {plan.project_context_summary && (() => {
        const lines = plan.project_context_summary.split("\n").slice(0, 3);
        return (
          <Box flexDirection="column">
            {lines.map((line, i) => {
              const display = line.length > 160 ? line.slice(0, 160) + "…" : line;
              return (
                <Text key={i} color="gray">
                  {display}
                </Text>
              );
            })}
          </Box>
        );
      })()}
    </Box>
  );
}
