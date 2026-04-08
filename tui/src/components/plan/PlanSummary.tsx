import React from "react";
import { Box, Text } from "ink";
import { useAppStore } from "../../state/store.js";
import { Badge } from "../../ink/Badge.js";
import { ProgressBar } from "../../ink/ProgressBar.js";

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
      <Box gap={1}>
        <Text>Auto-merge rate:</Text>
        <ProgressBar
          value={Math.round(rs.estimated_auto_merge_rate * 100)}
          max={100}
          width={20}
        />
      </Box>
      <Box gap={2}>
        <Badge label={`${rs.auto_safe_count} safe`} variant="success" />
        <Badge label={`${rs.auto_risky_count} risky`} variant="warning" />
        <Badge label={`${rs.human_required_count} human`} variant="danger" />
        <Badge label={`${rs.deleted_only_count} deleted`} variant="muted" />
      </Box>
      {plan.special_instructions.length > 0 && (
        <Box flexDirection="column">
          <Text bold>Special Instructions:</Text>
          {plan.special_instructions.map((instr, i) => (
            <Text key={i} color="yellow">  • {instr}</Text>
          ))}
        </Box>
      )}
      <Text color="gray">{plan.project_context_summary}</Text>
    </Box>
  );
}
