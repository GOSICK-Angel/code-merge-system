---
name: explain-arch
description: Quick architecture reference for CodeMergeSystem. Use when you need to understand the state machine, phase sequence, agent responsibilities, or data flow before making changes.
---

Provide a concise architecture overview of CodeMergeSystem covering:

## State Machine & Phase Sequence

List all phases in execution order with their trigger conditions and exit transitions:
INITIALIZING → CONFLICT_ANALYSIS → PLAN_REVIEW → (AWAITING_HUMAN?) → AUTO_MERGING → REPORT_GENERATION → DONE / FAILED

For each phase, note: what it does, key state fields it reads/writes, and what can cause it to transition to AWAITING_HUMAN.

## Agent Responsibilities

| Agent | Role | Read-only? | LLM Provider |
|-------|------|-----------|--------------|
| planner | Produces MergePlan (batch file assignments + risk scores) | No | Anthropic |
| planner_judge | Reviews MergePlan, returns verdict + issues | Yes (ReadOnlyStateView) | OpenAI |
| conflict_analyst | Analyzes individual conflict hunks | No | Anthropic |
| executor | Applies patches to working tree | No | OpenAI |
| judge | Final quality review | Yes (ReadOnlyStateView) | Anthropic |
| human_interface | Presents decisions to user via Web UI | No | Anthropic |

## Key Data Models

Explain the relationship between: MergeState, MergePlan, FileDiff, PendingDecision, PhaseResult, PlanReviewRound.

## Checkpoint & Resume

Single rolling checkpoint.json per run. MergeState is serialized via model_dump(mode="json"). Resume reconstructs via model_validate from checkpoint.

## Web UI Communication

Python ws_bridge.py ↔ WebSocket (default port 8765) ↔ React Web UI (Vite, web/src) → Zustand store updates.

---

After explaining, ask if the user wants to dive deeper into any specific subsystem.
