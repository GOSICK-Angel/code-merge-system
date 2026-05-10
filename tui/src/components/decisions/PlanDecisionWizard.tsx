import React, { useState, useCallback } from "react";
import { Box, Text, useInput } from "ink";
import { Badge, riskToBadgeVariant } from "../../ink/Badge.js";
import { Divider } from "../../ink/Divider.js";
import { KeyHint } from "../../ink/KeyHint.js";
import { useConnection } from "../../context/ConnectionContext.js";
import type { UserDecisionItem } from "../../state/types.js";

interface Props {
  items: UserDecisionItem[];
  isActive?: boolean;
}

type Phase = "deciding" | "review";

export function PlanDecisionWizard({ items, isActive = true }: Props) {
  const { send } = useConnection();

  const [localDecisions, setLocalDecisions] = useState<Map<string, string>>(
    () => new Map(items.filter((i) => i.user_choice).map((i) => [i.item_id, i.user_choice!]))
  );

  const [currentIdx, setCurrentIdx] = useState<number>(() => {
    const first = items.findIndex((i) => !i.user_choice);
    return first >= 0 ? first : 0;
  });

  const [highlightedOptionIdx, setHighlightedOptionIdx] = useState(0);
  const [phase, setPhase] = useState<Phase>("deciding");

  const currentItem = items[currentIdx];

  const advance = useCallback(() => {
    if (currentIdx < items.length - 1) {
      setCurrentIdx((prev) => prev + 1);
      setHighlightedOptionIdx(0);
    } else {
      setPhase("review");
    }
  }, [currentIdx, items.length]);

  const goBack = useCallback(() => {
    if (phase === "review") {
      setPhase("deciding");
      setCurrentIdx(items.length - 1);
      setHighlightedOptionIdx(0);
    } else if (currentIdx > 0) {
      setCurrentIdx((prev) => prev - 1);
      setHighlightedOptionIdx(0);
    }
  }, [phase, currentIdx, items.length]);

  const submitAll = useCallback(() => {
    const payload = Array.from(localDecisions.entries()).map(([item_id, user_choice]) => ({
      item_id,
      user_choice,
    }));
    send({ type: "submit_user_plan_decisions", payload: { items: payload } });
  }, [localDecisions, send]);

  useInput(
    (input, key) => {
      if (phase === "deciding" && currentItem) {
        if (key.upArrow) {
          setHighlightedOptionIdx((prev) => Math.max(0, prev - 1));
        } else if (key.downArrow) {
          setHighlightedOptionIdx((prev) => Math.min(currentItem.options.length - 1, prev + 1));
        } else if (key.return) {
          const opt = currentItem.options[highlightedOptionIdx];
          if (opt) {
            setLocalDecisions((prev) => new Map(prev).set(currentItem.item_id, opt.key));
            advance();
          }
        } else if (key.leftArrow || (key as Record<string, boolean>).backspace) {
          goBack();
        }
      } else if (phase === "review") {
        if (key.return || input === "a") {
          submitAll();
        } else if (key.leftArrow || (key as Record<string, boolean>).backspace || key.escape) {
          goBack();
        }
      }
    },
    { isActive }
  );

  if (phase === "review") {
    const allDecided = items.every((i) => localDecisions.has(i.item_id));
    return (
      <Box flexDirection="column">
        <Box paddingX={1}>
          <Text bold>
            Review — {localDecisions.size}/{items.length} decided
          </Text>
          {!allDecided && <Text color="yellow"> (some items skipped)</Text>}
        </Box>
        <Divider />
        <Box flexDirection="column" paddingX={1}>
          {items.map((item) => {
            const choiceKey = localDecisions.get(item.item_id);
            const chosenOpt = item.options.find((o) => o.key === choiceKey);
            return (
              <Box key={item.item_id} gap={1}>
                <Text color={chosenOpt ? "green" : "gray"}>{chosenOpt ? "✓" : "○"}</Text>
                <Text color={chosenOpt ? "white" : "gray"} dimColor={!chosenOpt}>
                  {item.file_path}
                </Text>
                {chosenOpt ? (
                  <Text color="green">→ {chosenOpt.label}</Text>
                ) : (
                  <Text color="yellow">not decided</Text>
                )}
              </Box>
            );
          })}
        </Box>
        <Divider />
        <KeyHint
          bindings={[
            { key: "⏎", label: "Submit all" },
            { key: "A", label: "Submit all" },
            { key: "←", label: "Back" },
          ]}
        />
      </Box>
    );
  }

  if (!currentItem) return null;

  return (
    <Box flexDirection="column">
      <Box paddingX={1} gap={2}>
        <Text bold color="white">
          {currentIdx + 1} / {items.length}
        </Text>
        <Text color="gray">{localDecisions.size} decided</Text>
      </Box>
      <Divider />
      <Box paddingX={1} flexDirection="column" gap={0}>
        <Box gap={1}>
          <Text bold color="white">
            {currentItem.file_path}
          </Text>
          <Badge
            label={currentItem.current_classification}
            variant={riskToBadgeVariant(currentItem.current_classification)}
          />
        </Box>
        {currentItem.description && (
          <Text color="gray" wrap="wrap">
            {currentItem.description}
          </Text>
        )}
        {currentItem.risk_context && currentItem.risk_context !== currentItem.description && (
          <Text color="cyan" wrap="wrap">
            {currentItem.risk_context}
          </Text>
        )}
      </Box>
      <Divider />
      <Box flexDirection="column" paddingX={1}>
        {currentItem.options.map((opt, i) => {
          const isHighlighted = i === highlightedOptionIdx;
          return (
            <Box key={opt.key} flexDirection="column">
              <Box gap={1}>
                <Text color={isHighlighted ? "cyan" : "gray"}>{isHighlighted ? "▸" : " "}</Text>
                <Text bold color={isHighlighted ? "cyan" : "white"}>
                  {opt.label}
                </Text>
              </Box>
              {isHighlighted && opt.description && (
                <Box paddingLeft={4}>
                  <Text color="gray" wrap="wrap">
                    {opt.description}
                  </Text>
                </Box>
              )}
            </Box>
          );
        })}
      </Box>
      <Divider />
      <KeyHint
        bindings={[
          { key: "↑↓", label: "Navigate" },
          { key: "⏎", label: "Confirm" },
          { key: "←", label: "Back" },
        ]}
      />
    </Box>
  );
}
