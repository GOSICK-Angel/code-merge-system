# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install -e ".[dev]"          # install with dev deps
pytest tests/unit/               # unit tests only
pytest                           # all tests
pytest -k "test_name"            # single test
mypy src                         # type check (strict mode)
ruff check src/                  # lint
ruff format src/                 # format
merge --help                     # CLI entry point

# TUI development (requires Python backend on ws://localhost:8765)
cd tui && npm run start       # start TUI
cd tui && npm run dev         # watch mode
cd tui && npm run build       # TypeScript type check (tsc --noEmit)

# One-stop flow
merge <target-branch>            # interactive TUI — auto-setup on first run
merge <target-branch> --no-tui  # plain text output
merge <target-branch> --ci      # CI mode (no interaction, JSON summary to stdout)
merge <target-branch> --dry-run # analysis only, no merge
merge <target-branch> -r        # force reconfiguration wizard

# Utility subcommands
merge resume --run-id <id>       # resume from checkpoint
merge validate --config <path>   # validate config + env vars
```

## Required Environment Variables

Each agent reads its API key from its own env var — no key is hardcoded:

| Agent | Env var |
|-------|---------|
| planner, conflict_analyst, judge, human_interface | `ANTHROPIC_API_KEY` |
| planner_judge, executor | `OPENAI_API_KEY` |

Run `merge validate --config <path>` to check all vars before running.

Integration tests (`tests/integration/`) require real API keys and are **not run in CI** — execute locally with `pytest tests/integration/ -v`.

## Architecture Constraints

These are load-bearing design rules enforced by unit tests — do not violate them:

- **No `TIMEOUT_DEFAULT`** — `DecisionSource` enum has no timeout-based value; human decisions must be explicit
- **No `human_decision_timeout_hours`** — `MergeConfig` has no such field
- **Judge / PlannerJudge are read-only** — their `run()` receives `ReadOnlyStateView`; all writes go through Orchestrator
- **Executor must snapshot before writing** — use `apply_with_snapshot()` in `patch_applier.py`; never write files directly
- **Plan dispute does not modify `risk_level`** — `raise_plan_dispute()` only appends to `state.plan_disputes`
- **HumanInterface never fills defaults** — skipped items keep `ESCALATE_HUMAN` status until the user explicitly decides
- **Plan revision limit** — when `plan_revision_rounds >= max_plan_revision_rounds`, transition to `AWAITING_HUMAN`, not `FAILED`
- **Plan human review** — after PlannerJudge approves the plan, the system checks `pending_user_decisions`. If any files are `HUMAN_REQUIRED`, it transitions to `AWAITING_HUMAN` for human sign-off. If no files need human decisions (all files are auto-mergeable), the system skips `AWAITING_HUMAN` and transitions directly to `AUTO_MERGING`. For non-converged plans (MAX_ROUNDS / STALLED / LLM_FAILURE), `AWAITING_HUMAN` is always required. A `plan_review_<run_id>.md` report is generated regardless.

## Configuration

Config is YAML-driven. Each agent has its own `AgentLLMConfig` (provider, model, `api_key_env`). The `agents` block in `MergeConfig` is the authoritative per-agent config; the top-level `llm` block is a legacy global default.

Key config thresholds: `risk_score_low=0.3`, `risk_score_high=0.6`, `auto_merge_confidence=0.85`. Files matching `security_sensitive.patterns` are forced to `HUMAN_REQUIRED`.

### `.merge/` Directory (production mode)

When run inside a target project (pip-installed), all artifacts are written under `<repo>/.merge/`:

```
<repo>/.merge/
  config.yaml          # auto-generated on first run by `merge <branch>`
  .env                 # API keys (gitignored automatically)
  .gitignore           # auto-generated: ignores .env and runs/
  plans/               # MERGE_PLAN_*.md reports (replaces MERGE_RECORD/)
  runs/<run_id>/
    checkpoint.json    # single rolling checkpoint
    merge_report.md
    plan_review.md
```

API key resolution order: shell env vars → `.merge/.env` → `~/.config/code-merge-system/.env`

## Git Workflow

Branches: `feature/<name>`, `fix/<name>`, `chore/<name>`. PRs squash-merged into `main`.

## Testing Notes

`asyncio_mode = "auto"` is set globally — all async test functions run without explicit `@pytest.mark.asyncio`. Integration tests mock LLM calls via `patch_llm_factory` fixture; avoid real API calls in unit tests.

## Code Style

- Python 3.11+, `async`/`await` throughout
- Pydantic v2 syntax (`model_dump()`, `Field(default_factory=...)`, `@field_validator`)
- Immutable patterns — return new objects, never mutate in place
- Files stay under 800 lines; organize by feature layer (models → tools → llm → agents → core → cli)
- mypy strict mode is enforced — all new code must pass `mypy src`
