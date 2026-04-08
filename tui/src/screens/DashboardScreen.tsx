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

const BINDINGS = [
  { key: "1", label: "Dash" },
  { key: "2", label: "Plan" },
  { key: "3", label: "Decide" },
  { key: "5", label: "Judge" },
  { key: "6", label: "Report" },
  { key: "/", label: "Search" },
  { key: "?", label: "Help" },
  { key: "q", label: "Quit" },
];

export function DashboardScreen() {
  const [searching, setSearching] = useState(false);
  const setSearchQuery = useAppStore((s) => s.setSearchQuery);

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
      <KeyHint bindings={BINDINGS} />
    </Box>
  );
}
