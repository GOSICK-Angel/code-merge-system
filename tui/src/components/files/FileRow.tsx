import React from "react";
import { Box, Text } from "ink";
import { Badge, riskToBadgeVariant } from "../../ink/Badge.js";
import { shortenPath } from "../../utils/format.js";
import type { FileDiff } from "../../state/types.js";

interface FileRowProps {
  file: FileDiff;
  isSelected: boolean;
}

export function FileRow({ file, isSelected }: FileRowProps) {
  return (
    <Box flexDirection="row" gap={1}>
      <Text color={isSelected ? "cyan" : undefined}>
        {isSelected ? "▸" : " "}
      </Text>
      <Text color={isSelected ? "cyan" : "white"}>
        {shortenPath(file.file_path, 45)}
      </Text>
      <Badge label={file.risk_level.replace(/_/g, " ")} variant={riskToBadgeVariant(file.risk_level)} />
      <Text color="gray">
        +{file.lines_added} -{file.lines_deleted}
      </Text>
      {file.is_security_sensitive && <Text color="red">🔒</Text>}
    </Box>
  );
}
