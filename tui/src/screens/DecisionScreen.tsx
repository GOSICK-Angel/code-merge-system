import React, { useState } from "react";
import { Box, Text } from "ink";
import { StatusBar } from "../components/status/StatusBar.js";
import {
  ConflictDecisionWizard,
  type WizardPhase,
} from "../components/decisions/ConflictDecisionWizard.js";
import { Divider } from "../ink/Divider.js";
import { KeyHint } from "../ink/KeyHint.js";
import { useAppStore } from "../state/store.js";
import type { HumanDecisionRequest } from "../state/types.js";

export function DecisionScreen() {
  const requests = useAppStore((s) => s.humanDecisionRequests);
  const [wizardPhase, setWizardPhase] = useState<WizardPhase>("deciding");

  const requestList: HumanDecisionRequest[] = Object.values(requests).sort(
    (a, b) => a.priority - b.priority,
  );

  if (requestList.length === 0) {
    return (
      <Box flexDirection="column">
        <StatusBar />
        <Divider />
        <Box paddingX={1}>
          <Text color="gray">No pending conflict decisions.</Text>
        </Box>
      </Box>
    );
  }

  const maxOpts = Math.max(...requestList.map((r) => r.options.length));
  const bindings =
    wizardPhase === "submitting"
      ? [{ key: "?", label: "Help" }]
      : wizardPhase === "review"
        ? [
            { key: "⏎", label: "Submit" },
            { key: "←", label: "Revise" },
          ]
        : [
            { key: "↑↓", label: "Select" },
            { key: `1-${maxOpts}`, label: "Quick pick" },
            { key: "⏎", label: "Confirm" },
            { key: "←", label: "Prev" },
          ];

  return (
    <Box flexDirection="column">
      <StatusBar />
      <Divider />
      <ConflictDecisionWizard
        items={requestList}
        isActive={true}
        onPhaseChange={setWizardPhase}
      />
      <Divider />
      <KeyHint bindings={bindings} />
    </Box>
  );
}
