import React, { useState, useCallback, useEffect, useRef } from "react";
import { Box, Text, useInput } from "ink";
import { Badge, riskToBadgeVariant } from "../../ink/Badge.js";
import { Divider } from "../../ink/Divider.js";
import { useConnection } from "../../context/ConnectionContext.js";
import { useAppStore } from "../../state/store.js";
import type { UserDecisionItem } from "../../state/types.js";

export type WizardPhase = "deciding" | "review" | "submitting";

function parseChangeZones(diff: string): string[] {
  return diff
    .split("\n")
    .filter((line) => line.startsWith("@@"))
    .map((line) => {
      const match = line.match(/@@[^@]+@@\s*(.*)/);
      return match?.[1]?.trim() ?? "";
    });
}

interface Props {
  items: UserDecisionItem[];
  isActive?: boolean;
  onPhaseChange?: (phase: WizardPhase) => void;
}

export function PlanDecisionWizard({ items, isActive = true, onPhaseChange }: Props) {
  const { send } = useConnection();
  const status = useAppStore((s) => s.status);

  const seenItemIdsRef = useRef<Set<string>>(
    new Set(items.map((i) => i.item_id)),
  );

  const [roundIds, setRoundIds] = useState<Set<string>>(() => {
    const undecided = items.filter((i) => !i.user_choice).map((i) => i.item_id);
    return new Set(undecided.length > 0 ? undecided : items.map((i) => i.item_id));
  });
  const [roundNumber, setRoundNumber] = useState<number>(1);
  const [roundDecisions, setRoundDecisions] = useState<Map<string, string>>(
    () => new Map(),
  );
  const [currentIdx, setCurrentIdx] = useState<number>(0);
  const [highlightedOptionIdx, setHighlightedOptionIdx] = useState(0);
  const [phase, setPhase] = useState<WizardPhase>("deciding");

  const roundItems = items.filter((i) => roundIds.has(i.item_id));

  useEffect(() => {
    onPhaseChange?.(phase);
  }, [phase, onPhaseChange]);

  useEffect(() => {
    const seen = seenItemIdsRef.current;
    const newcomerIds: string[] = [];
    for (const it of items) {
      if (!seen.has(it.item_id)) {
        seen.add(it.item_id);
        if (!it.user_choice) newcomerIds.push(it.item_id);
      }
    }

    if (phase === "submitting") {
      if (newcomerIds.length > 0) {
        setRoundIds(new Set(newcomerIds));
        setRoundNumber((n) => n + 1);
        setRoundDecisions(new Map());
        setCurrentIdx(0);
        setHighlightedOptionIdx(0);
        setPhase("deciding");
      } else if (status !== "awaiting_human") {
        setPhase("review");
      }
    }
  }, [items, phase, status]);

  const currentItem = roundItems[currentIdx];

  const advance = useCallback(() => {
    if (currentIdx < roundItems.length - 1) {
      setCurrentIdx((prev) => prev + 1);
      setHighlightedOptionIdx(0);
    } else {
      setPhase("review");
    }
  }, [currentIdx, roundItems.length]);

  const goBack = useCallback(() => {
    if (phase === "review") {
      setPhase("deciding");
      setCurrentIdx(Math.max(0, roundItems.length - 1));
      setHighlightedOptionIdx(0);
    } else if (currentIdx > 0) {
      setCurrentIdx((prev) => prev - 1);
      setHighlightedOptionIdx(0);
    }
  }, [phase, currentIdx, roundItems.length]);

  const submitAll = useCallback(() => {
    const payload = Array.from(roundDecisions.entries()).map(
      ([item_id, user_choice]) => ({ item_id, user_choice }),
    );
    send({ type: "submit_user_plan_decisions", payload: { items: payload } });
    setPhase("submitting");
  }, [roundDecisions, send]);

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
            setRoundDecisions((prev) => new Map(prev).set(currentItem.item_id, opt.key));
            advance();
          }
        } else if (key.leftArrow || (key as Record<string, boolean>).backspace) {
          goBack();
        } else {
          const num = parseInt(input, 10);
          if (num >= 1 && num <= currentItem.options.length) {
            const opt = currentItem.options[num - 1];
            if (opt) {
              setRoundDecisions((prev) => new Map(prev).set(currentItem.item_id, opt.key));
              advance();
            }
          }
        }
      } else if (phase === "review") {
        if (key.return || input === "a") {
          submitAll();
        } else if (key.leftArrow || (key as Record<string, boolean>).backspace || key.escape) {
          goBack();
        }
      }
    },
    { isActive: isActive && phase !== "submitting" }
  );

  if (phase === "review" || phase === "submitting") {
    const skippedCount = roundItems.filter(
      (i) => !roundDecisions.has(i.item_id),
    ).length;
    const roundHeader =
      roundNumber === 1 ? "Decisions" : `Decisions · Round ${roundNumber}`;
    return (
      <Box flexDirection="column">
        <Box paddingX={1} gap={1}>
          <Text bold>{roundHeader}</Text>
          <Text color="gray">|</Text>
          <Text>
            {roundDecisions.size}/{roundItems.length} decided
          </Text>
          {skippedCount > 0 && (
            <Text color="yellow">({skippedCount} skipped)</Text>
          )}
        </Box>
        {phase === "review" ? (
          <Box
            paddingX={1}
            marginY={0}
            borderStyle="single"
            borderColor="cyan"
            flexDirection="column"
          >
            <Box gap={2}>
              <Text bold color="cyan">
                ⏎ Submit this round
              </Text>
              <Text color="gray">|</Text>
              <Text color="yellow">← Revise last item</Text>
              <Text color="gray">|</Text>
              <Text color="gray">o Back to overview</Text>
            </Box>
          </Box>
        ) : (
          <Box
            paddingX={1}
            marginY={0}
            borderStyle="single"
            borderColor="yellow"
            flexDirection="column"
          >
            <Text bold color="yellow">
              ⏳ Waiting for backend — no action needed.
            </Text>
            <Text color="gray">
              Auto-merge may surface additional items; a fresh decision view
              will appear automatically. Press [o] for overview.
            </Text>
          </Box>
        )}
        <Box flexDirection="column" paddingX={1}>
          {roundItems.map((item) => {
            const choiceKey = roundDecisions.get(item.item_id);
            const chosenOpt = item.options.find((o) => o.key === choiceKey);
            return (
              <Box key={item.item_id} gap={1}>
                <Text color={chosenOpt ? "green" : "gray"}>
                  {chosenOpt ? "✓" : "○"}
                </Text>
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
      </Box>
    );
  }

  if (!currentItem) return null;

  return (
    <Box flexDirection="column">
      <Box paddingX={1} gap={2}>
        <Text bold color="white">
          {currentIdx + 1} / {roundItems.length}
        </Text>
        <Text color="gray">
          {roundDecisions.size} decided
          {roundNumber > 1 ? ` · Round ${roundNumber}` : ""}
        </Text>
        {roundNumber > 1 && (
          <Text bold color="magenta">
            ✨ NEW from auto-merge
          </Text>
        )}
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
        {currentItem.risk_context &&
          !currentItem.description.includes(currentItem.risk_context) && (
            <Box flexDirection="row" gap={1} marginTop={0}>
              <Text color="yellow" bold>
                Risk:
              </Text>
              <Box flexDirection="column">
                {currentItem.risk_context.split("\n").map((line, i) => (
                  <Text key={i} color="cyan" wrap="wrap">
                    {line}
                  </Text>
                ))}
              </Box>
            </Box>
          )}
        {currentItem.conflict_preview && (() => {
          const zones = parseChangeZones(currentItem.conflict_preview);
          const allLines = currentItem.conflict_preview.split("\n");
          const MAX_LINES = 15;
          const visibleLines = allLines.slice(0, MAX_LINES);
          const hiddenCount = allLines.length - visibleLines.length;
          return (
            <Box
              flexDirection="column"
              marginTop={1}
              borderStyle="single"
              borderColor="gray"
              paddingX={1}
            >
              <Box gap={2}>
                <Text bold color="yellow">
                  ── Divergence ──
                </Text>
                {zones.length > 0 && (
                  <Text color="magenta">
                    {zones.length} change {zones.length === 1 ? "zone" : "zones"}
                    {zones.some(Boolean) && ": "}
                    {zones.filter(Boolean).slice(0, 3).join(" · ")}
                    {zones.filter(Boolean).length > 3 && " …"}
                  </Text>
                )}
              </Box>
              {visibleLines.map((line, i) => {
                const color = line.startsWith("+")
                  ? "green"
                  : line.startsWith("-")
                    ? "red"
                    : line.startsWith("@@")
                      ? "magenta"
                      : "gray";
                return (
                  <Text key={i} color={color}>
                    {line}
                  </Text>
                );
              })}
              {hiddenCount > 0 && (
                <Text color="gray">… {hiddenCount} more lines</Text>
              )}
            </Box>
          );
        })()}
      </Box>
      <Divider />
      <Box flexDirection="column" paddingX={1}>
        {currentItem.options.map((opt, i) => {
          const isHighlighted = i === highlightedOptionIdx;
          return (
            <Box key={opt.key} flexDirection="column">
              <Box gap={1}>
                <Text color={isHighlighted ? "cyan" : "gray"}>{isHighlighted ? "▸" : " "}</Text>
                <Text color={isHighlighted ? "cyan" : "gray"}>{i + 1}.</Text>
                <Text bold color={isHighlighted ? "cyan" : "white"}>
                  {opt.label}
                </Text>
              </Box>
              {isHighlighted && opt.description && (
                <Box paddingLeft={5}>
                  <Text color="gray" wrap="wrap">
                    {opt.description}
                  </Text>
                </Box>
              )}
            </Box>
          );
        })}
      </Box>
    </Box>
  );
}
