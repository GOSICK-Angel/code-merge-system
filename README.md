# CodeMergeSystem

A multi-agent system for automating code merges between upstream and fork branches, with semantic conflict resolution, risk-based routing, and human-in-the-loop escalation.

## Overview

The system orchestrates six specialized agents in a pipeline:

| Agent | Role | LLM |
|-------|------|-----|
| **Planner** | Analyzes diffs, generates phased merge plan | Claude Opus |
| **PlannerJudge** | Independently reviews merge plan quality | GPT-4o |
| **ConflictAnalyst** | Analyzes high-risk conflict semantics | Claude Sonnet |
| **Executor** | Applies file-level merge decisions | GPT-4o |
| **Judge** | Reviews merged results for correctness | Claude Opus |
| **HumanInterface** | Generates reports, collects human decisions | Claude Haiku |

Key design principles:
- Reviewer agents (Judge, PlannerJudge) use a different LLM provider than executor agents
- Human decisions are always explicit — no timeout-based auto-defaults
- All file writes are snapshotted before execution; failures auto-rollback
- Full checkpoint/resume support at every phase boundary

## Requirements

- Python 3.11+
- `ANTHROPIC_API_KEY` — for Planner, ConflictAnalyst, Judge, HumanInterface
- `OPENAI_API_KEY` — for PlannerJudge, Executor

## Installation

```bash
pip install -e ".[dev]"
```

## Usage

```bash
# Validate config and environment before running
merge validate --config config/my-merge.yaml

# Run full merge
merge run --config config/my-merge.yaml

# Dry run (analysis only, no file writes)
merge run --config config/my-merge.yaml --dry-run

# Resume from checkpoint after interruption
merge resume --run-id <run-id>

# Generate report from completed run
merge report --run-id <run-id> --output ./outputs
```

## Configuration

Create a YAML config file:

```yaml
upstream_ref: "upstream/main"
fork_ref: "feature/my-fork"
repo_path: "."
project_context: "A Python web service using FastAPI and PostgreSQL."
max_plan_revision_rounds: 2

agents:
  planner:
    provider: anthropic
    model: claude-opus-4-6
    api_key_env: ANTHROPIC_API_KEY
  planner_judge:
    provider: openai
    model: gpt-4o
    api_key_env: OPENAI_API_KEY
  executor:
    provider: openai
    model: gpt-4o
    temperature: 0.1
    api_key_env: OPENAI_API_KEY
  judge:
    provider: anthropic
    model: claude-opus-4-6
    temperature: 0.1
    api_key_env: ANTHROPIC_API_KEY

thresholds:
  auto_merge_confidence: 0.85
  human_escalation: 0.60

output:
  directory: ./outputs
  formats: [json, markdown]
```

See `config/default.yaml` for the full configuration reference.

## Development

```bash
# Run unit tests
pytest tests/unit/ -v

# Run all tests with coverage
pytest --cov=src tests/

# Type check
mypy src

# Lint
ruff check src/

# Format
ruff format src/
```

## Architecture

```
src/
├── models/     # Pydantic data models (config, state, plan, decision, etc.)
├── tools/      # Git operations, diff parsing, file classification, patch application
├── llm/        # LLM client abstraction, prompt templates, response parsers
├── agents/     # Six specialized agents
├── core/       # Orchestrator, state machine, checkpointing, message bus
└── cli/        # Click CLI (run, resume, report, validate)
```

See `doc/` for detailed design documentation:
- `doc/architecture.md` — directory structure and tech stack
- `doc/agents.md` — agent responsibilities and LLM configuration
- `doc/flow.md` — state machine and 6-phase execution flow
- `doc/data-models.md` — all Pydantic model definitions
- `doc/implementation-plan.md` — algorithm design and prompt frameworks
