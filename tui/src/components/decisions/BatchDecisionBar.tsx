import React from "react";
import { Box, Text, useInput } from "ink";
import { useAppStore } from "../../state/store.js";
import { selectPendingDecisions, selectDecidedCount, selectTotalDecisionCount } from "../../state/selectors.js";
import { useConnection } from "../../context/ConnectionContext.js";

interface BatchDecisionBarProps {
  isActive?: boolean;
}

export function BatchDecisionBar({ isActive = false }: BatchDecisionBarProps) {
  const pending = useAppStore(selectPendingDecisions);
  const decided = useAppStore(selectDecidedCount);
  const total = useAppStore(selectTotalDecisionCount);
  const { send } = useConnection();

  useInput(
    (input) => {
      if (input === "A") {
        for (const req of pending) {
          send({
            type: "submit_decision",
            payload: {
              filePath: req.file_path,
              decision: String(req.analyst_recommendation),
            },
          });
        }
      }
    },
    { isActive }
  );

  return (
    <Box gap={2} paddingX={1}>
      <Text>
        Decisions: <Text color="green">{decided}</Text>/<Text>{total}</Text>
      </Text>
      <Text color="gray">|</Text>
      <Text color="gray">{pending.length} pending</Text>
      {pending.length > 0 && (
        <>
          <Text color="gray">|</Text>
          <Text color="yellow">[Shift+A] Accept all recommendations</Text>
        </>
      )}
    </Box>
  );
}
