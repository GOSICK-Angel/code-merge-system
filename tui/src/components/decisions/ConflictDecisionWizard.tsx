import React, { useState, useCallback, useEffect, useRef } from "react";
import { Box, Text, useInput } from "ink";
import { Divider } from "../../ink/Divider.js";
import { useConnection } from "../../context/ConnectionContext.js";
import { useAppStore } from "../../state/store.js";
import type { HumanDecisionRequest } from "../../state/types.js";

export type WizardPhase = "deciding" | "review" | "submitting";

interface Props {
  items: HumanDecisionRequest[];
  isActive?: boolean;
  onPhaseChange?: (phase: WizardPhase) => void;
}

function confidenceColor(pct: number): string {
  if (pct >= 80) return "green";
  if (pct >= 50) return "yellow";
  return "red";
}

export function ConflictDecisionWizard({
  items,
  isActive = true,
  onPhaseChange,
}: Props) {
  const { send } = useConnection();
  const status = useAppStore((s) => s.status);

  const seenIdsRef = useRef<Set<string>>(
    new Set(items.map((i) => i.file_path)),
  );

  const [roundIds, setRoundIds] = useState<Set<string>>(() => {
    const undecided = items
      .filter((i) => !i.human_decision)
      .map((i) => i.file_path);
    return new Set(
      undecided.length > 0 ? undecided : items.map((i) => i.file_path),
    );
  });
  const [roundNumber, setRoundNumber] = useState<number>(1);
  const [roundDecisions, setRoundDecisions] = useState<Map<string, string>>(
    () => new Map(),
  );
  const [currentIdx, setCurrentIdx] = useState<number>(0);
  const [highlightedOptionIdx, setHighlightedOptionIdx] = useState(0);
  const [phase, setPhase] = useState<WizardPhase>("deciding");

  const roundItems = items.filter((i) => roundIds.has(i.file_path));

  useEffect(() => {
    onPhaseChange?.(phase);
  }, [phase, onPhaseChange]);

  useEffect(() => {
    const seen = seenIdsRef.current;
    const newcomerIds: string[] = [];
    for (const it of items) {
      if (!seen.has(it.file_path)) {
        seen.add(it.file_path);
        if (!it.human_decision) newcomerIds.push(it.file_path);
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
      ([file_path, decision]) => ({ file_path, decision }),
    );
    send({
      type: "submit_conflict_decisions_batch",
      payload: { items: payload },
    });
    setPhase("submitting");
  }, [roundDecisions, send]);

  useInput(
    (input, key) => {
      if (phase === "deciding" && currentItem) {
        if (key.upArrow) {
          setHighlightedOptionIdx((prev) => Math.max(0, prev - 1));
        } else if (key.downArrow) {
          setHighlightedOptionIdx((prev) =>
            Math.min(currentItem.options.length - 1, prev + 1),
          );
        } else if (key.return) {
          const opt = currentItem.options[highlightedOptionIdx];
          if (opt) {
            setRoundDecisions((prev) =>
              new Map(prev).set(currentItem.file_path, opt.decision),
            );
            advance();
          }
        } else if (
          key.leftArrow ||
          (key as Record<string, boolean>).backspace
        ) {
          goBack();
        } else {
          const num = parseInt(input, 10);
          if (num >= 1 && num <= currentItem.options.length) {
            const opt = currentItem.options[num - 1];
            if (opt) {
              setRoundDecisions((prev) =>
                new Map(prev).set(currentItem.file_path, opt.decision),
              );
              advance();
            }
          }
        }
      } else if (phase === "review") {
        if (key.return || input === "a") {
          submitAll();
        } else if (
          key.leftArrow ||
          (key as Record<string, boolean>).backspace ||
          key.escape
        ) {
          goBack();
        }
      }
    },
    { isActive: isActive && phase !== "submitting" },
  );

  if (phase === "review" || phase === "submitting") {
    const skippedCount = roundItems.filter(
      (i) => !roundDecisions.has(i.file_path),
    ).length;
    const roundHeader =
      roundNumber === 1
        ? "Conflict Decisions"
        : `Conflict Decisions · Round ${roundNumber}`;
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
              Decisions are being executed; a fresh decision view will appear
              automatically if more conflicts surface.
            </Text>
          </Box>
        )}
        <Box flexDirection="column" paddingX={1}>
          {roundItems.map((item) => {
            const choice = roundDecisions.get(item.file_path);
            const chosenOpt = item.options.find((o) => o.decision === choice);
            return (
              <Box key={item.file_path} gap={1}>
                <Text color={chosenOpt ? "green" : "gray"}>
                  {chosenOpt ? "✓" : "○"}
                </Text>
                <Text
                  color={chosenOpt ? "white" : "gray"}
                  dimColor={!chosenOpt}
                >
                  {item.file_path}
                </Text>
                {chosenOpt ? (
                  <Text color="green">→ {chosenOpt.description}</Text>
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

  const confPct = Math.round(currentItem.analyst_confidence * 100);
  const confColor = confidenceColor(confPct);

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
          <Text color={confColor}>(analyst {confPct}%)</Text>
        </Box>
        {currentItem.context_summary && (
          <Text color="gray" wrap="wrap">
            {currentItem.context_summary}
          </Text>
        )}
        {currentItem.upstream_change_summary && (
          <Box gap={1}>
            <Text bold color="blue">
              Upstream:
            </Text>
            <Text color="gray" wrap="wrap">
              {currentItem.upstream_change_summary}
            </Text>
          </Box>
        )}
        {currentItem.fork_change_summary && (
          <Box gap={1}>
            <Text bold color="magenta">
              Fork:
            </Text>
            <Text color="gray" wrap="wrap">
              {currentItem.fork_change_summary}
            </Text>
          </Box>
        )}
        {currentItem.analyst_rationale && (
          <Box flexDirection="row" gap={1} marginTop={0}>
            <Text bold color="yellow">
              Why human:
            </Text>
            <Text color="cyan" wrap="wrap">
              {currentItem.analyst_rationale}
            </Text>
          </Box>
        )}
        {currentItem.conflict_points.length > 0 && (
          <Box flexDirection="column" marginTop={0}>
            <Text bold>Conflicts:</Text>
            {currentItem.conflict_points.slice(0, 5).map((cp, i) => (
              <Text key={i} color="yellow" wrap="wrap">
                • [{cp.severity}] {cp.description}
                {cp.line_range ? ` (${cp.line_range})` : ""}
              </Text>
            ))}
            {currentItem.conflict_points.length > 5 && (
              <Text color="gray">
                … and {currentItem.conflict_points.length - 5} more
              </Text>
            )}
          </Box>
        )}
      </Box>
      <Divider />
      <Box flexDirection="column" paddingX={1}>
        {currentItem.options.map((opt, i) => {
          const isHighlighted = i === highlightedOptionIdx;
          return (
            <Box key={opt.option_key} flexDirection="column">
              <Box gap={1}>
                <Text color={isHighlighted ? "cyan" : "gray"}>
                  {isHighlighted ? "▸" : " "}
                </Text>
                <Text color={isHighlighted ? "cyan" : "gray"}>{i + 1}.</Text>
                <Text bold color={isHighlighted ? "cyan" : "white"}>
                  {opt.description}
                </Text>
                {opt.risk_warning && (
                  <Text color="yellow">⚠ {opt.risk_warning}</Text>
                )}
              </Box>
            </Box>
          );
        })}
      </Box>
    </Box>
  );
}
