import React, { useState } from "react";
import { Box, useInput } from "ink";
import { StatusBar } from "../components/status/StatusBar.js";
import { PhaseTimeline } from "../components/status/PhaseTimeline.js";
import { AgentIndicator } from "../components/status/AgentIndicator.js";
import { FileList } from "../components/files/FileList.js";
import { RiskDistribution } from "../components/files/RiskDistribution.js";
import { SearchBar } from "../components/SearchBar.js";
import { Divider } from "../ink/Divider.js";
import { KeyHint } from "../ink/KeyHint.js";
import { useAppStore } from "../state/store.js";
import type { SystemStatus } from "../state/types.js";

const ALL_BINDINGS = [
  { key: "1", label: "Dash", always: true },
  { key: "2", label: "Plan", always: false },
  { key: "3", label: "Decide", always: false },
  { key: "5", label: "Judge", always: false },
  { key: "6", label: "Report", always: false },
  { key: "/", label: "Search", always: true },
  { key: "?", label: "Help", always: true },
  { key: "q", label: "Quit", always: true },
] as const;

function availableKeys(status: SystemStatus): Set<string> {
  const keys = new Set<string>();
  switch (status) {
    case "plan_reviewing":
    case "plan_revising":
    case "plan_dispute_pending":
      keys.add("2");
      break;
    case "awaiting_human":
    case "auto_merging":
      keys.add("2");
      keys.add("3");
      break;
    case "judge_reviewing":
      keys.add("2");
      keys.add("3");
      keys.add("5");
      break;
    case "generating_report":
    case "completed":
    case "failed":
    case "paused":
      keys.add("2");
      keys.add("3");
      keys.add("5");
      keys.add("6");
      break;
    default:
      break;
  }
  return keys;
}

export function DashboardScreen() {
  const [searching, setSearching] = useState(false);
  const setSearchQuery = useAppStore((s) => s.setSearchQuery);
  const status = useAppStore((s) => s.status);

  const available = availableKeys(status);
  const bindings = ALL_BINDINGS.filter((b) => b.always || available.has(b.key));

  useInput((input, key) => {
    if (!searching && input === "/") {
      setSearching(true);
    } else if (searching && key.escape) {
      setSearching(false);
      setSearchQuery("");
    }
  });

  return (
    <Box flexDirection="column">
      <StatusBar />
      <PhaseTimeline />
      <SearchBar isActive={searching} />
      <Divider />
      <Box flexDirection="row" minHeight={15}>
        <Box flexDirection="column" flexGrow={1}>
          <FileList height={15} isActive={!searching} />
        </Box>
        <Box flexDirection="column" width={25}>
          <AgentIndicator />
          <RiskDistribution />
        </Box>
      </Box>
      <Divider />
      <KeyHint bindings={bindings} />
    </Box>
  );
}
