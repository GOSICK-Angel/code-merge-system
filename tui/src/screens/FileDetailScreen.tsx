import React from "react";
import { Box, Text } from "ink";
import { StatusBar } from "../components/status/StatusBar.js";
import { DiffView } from "../ink/DiffView.js";
import { Badge, riskToBadgeVariant } from "../ink/Badge.js";
import { Divider } from "../ink/Divider.js";
import { KeyHint } from "../ink/KeyHint.js";
import { useAppStore } from "../state/store.js";

export function FileDetailScreen() {
  const selectedFile = useAppStore((s) => s.selectedFile);
  const fileDiffs = useAppStore((s) => s.fileDiffs);
  const classifications = useAppStore((s) => s.fileClassifications);
  const decisionRecords = useAppStore((s) => s.fileDecisionRecords);
  const humanRequests = useAppStore((s) => s.humanDecisionRequests);

  const diff = fileDiffs.find((fd) => fd.file_path === selectedFile);
  const risk = selectedFile ? classifications[selectedFile] : undefined;
  const record = selectedFile ? decisionRecords[selectedFile] : undefined;
  const humanReq = selectedFile ? humanRequests[selectedFile] : undefined;

  if (!selectedFile || !diff) {
    return (
      <Box flexDirection="column">
        <StatusBar />
        <Box paddingX={1}>
          <Text color="gray">No file selected. Press Esc to go back.</Text>
        </Box>
        <KeyHint bindings={[{ key: "Esc", label: "Back" }]} />
      </Box>
    );
  }

  return (
    <Box flexDirection="column">
      <StatusBar />
      <Box flexDirection="column" paddingX={1}>
        <Box gap={1}>
          <Text bold>{diff.file_path}</Text>
          {risk && (
            <Badge label={risk.replace(/_/g, " ")} variant={riskToBadgeVariant(risk)} />
          )}
          {diff.is_security_sensitive && <Text color="red">SECURITY</Text>}
        </Box>
        <Box gap={2}>
          <Text color="green">+{diff.lines_added}</Text>
          <Text color="red">-{diff.lines_deleted}</Text>
          {diff.language && <Text color="gray">{diff.language}</Text>}
          {diff.change_category && (
            <Text color="gray">cat:{diff.change_category}</Text>
          )}
          <Text color="gray">risk:{diff.risk_score.toFixed(2)}</Text>
        </Box>
        {record && (
          <Box gap={1}>
            <Text>Decision:</Text>
            <Text color={record.success ? "green" : "red"} bold>
              {record.decision} ({record.strategy_used})
            </Text>
            {record.error && <Text color="red">{record.error}</Text>}
          </Box>
        )}
        {humanReq && (
          <Box flexDirection="column">
            <Box gap={1}>
              <Text>Analyst:</Text>
              <Text bold>{String(humanReq.analyst_recommendation)}</Text>
              <Text color="gray">
                ({Math.round(humanReq.analyst_confidence * 100)}%)
              </Text>
            </Box>
            <Text color="gray">{humanReq.analyst_rationale}</Text>
            {humanReq.conflict_points.map((cp, i) => (
              <Text key={i} color="yellow">
                • [{cp.severity}] {cp.description}
              </Text>
            ))}
          </Box>
        )}
      </Box>
      <Divider />
      <Box flexDirection="column" paddingX={1} flexGrow={1}>
        <Text bold>Diff</Text>
        <DiffView diff={diff.raw_diff || "(no diff available)"} maxLines={40} />
      </Box>
      <Divider />
      <KeyHint bindings={[{ key: "Esc", label: "Back" }]} />
    </Box>
  );
}
