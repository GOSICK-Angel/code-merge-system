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

## Forks-profile authoring contract

`<repo>/.merge/forks-profile.yaml` is **optional** and accepts only four
top-level keys: `version`, `fork`, `removed_domains`, `rewritten_modules`.
The schema model is `ForksProfileYaml` (`src/models/forks_profile.py`),
distinct from the runtime `ForksProfile` which carries auto-computed
fields. Loader behavior:

- **`fork_only_features` / `migration_policy` are auto-computed every run**
  by `compute_auto_overlay()` (`src/tools/forks_profile_loader.py`) from
  `ForkDivergence.FORK_ONLY` paths and migration-glob numbering. yaml
  declarations of either field raise `ForksProfileError` with a migration
  message — they were deprecated in §9 PR-A and have no override path.
- **First-run wizard** offers `merge forks-profile init` automatically
  when fork-deleted file count ≥ `FORKS_PROFILE_INIT_THRESHOLD` (30,
  calibrated from insforge v2.1.0). Below the threshold the prompt is
  silent — auto overlay alone covers routing.
- **Drift detection** runs in the initialize phase whenever a yaml
  exists; ≥3 drift items populate `state.forks_profile_drift` and emit
  a `ctx.notify` summary. The drift text is appended to
  `MERGE_PLAN_<run_id>.md` so reviewers see staleness alongside the
  plan they are approving. Drift is best-effort — any drafter / git
  failure logs and continues without aborting the merge.

## Agent Contracts

Every agent that inherits from `BaseAgent` and opts in via `contract_name = "<name>"` has a yaml at `src/agents/contracts/<name>.yaml` that declares its input whitelist, output schema, allowed prompt gate IDs, forbidden behaviors, and collaboration pattern. See `src/agents/contracts/_schema.md` for the full spec.

Prompts are registered in `src/llm/prompts/gate_registry.py` under stable IDs (`P-*`, `PJ-*`, `CA-*`, `E-*`, `J-*`). Agents must reference gates by ID rather than importing prompt builders directly; `tests/unit/test_agent_contracts.py` verifies that every contract-declared gate is registered.

### Anti-Patterns (enforced by `tests/unit/test_agent_contracts.py`)

1. **Writing state from reviewer agents** — `judge`, `planner_judge`, `human_interface` must never produce a left-hand `state.<field> = ...` assignment. Use `ReadOnlyStateView` (wrap via `self.restricted_view(state)`) and let the Orchestrator persist results.
2. **Bypassing `BaseAgent._call_llm_with_retry`** — direct calls to `self.llm.complete(` / `self.llm.chat(` / `self.llm.generate(` are forbidden. The retry/error-classification/circuit-breaker layer must wrap every LLM call.
3. **Silently filling missing LLM output fields with defaults** — when a model returns an incomplete structure, raise `ModelOutputError`; never substitute a default and proceed.
4. **Referencing a prompt by importing its builder directly in an agent** — go through `get_gate("<ID>")`. Contracts must pre-declare which gate IDs the agent is allowed to invoke.
5. **Accessing a `MergeState` field not in the agent's contract `inputs`** — when `self.restricted_view(state)` is used, out-of-contract reads raise `FieldNotInContract`. Add the field to the yaml explicitly if it is genuinely needed.

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

`asyncio_mode = "auto"` is set globally — all async test functions run without explicit `@pytest.mark.asyncio`. Unit tests use `patch_llm_factory` to mock LLM calls; integration tests (`tests/integration/`) make real API calls and require valid `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`.

80% coverage is enforced in CI (`--cov-fail-under=80`). Run locally with `pytest tests/unit/ --cov=src --cov-report=term-missing`.

## Code Style

- Python 3.11+, `async`/`await` throughout
- Pydantic v2 syntax (`model_dump()`, `Field(default_factory=...)`, `@field_validator`)
- Immutable patterns — return new objects, never mutate in place
- Files stay under 800 lines; organize by feature layer (models → tools → llm → agents → core → cli)
- mypy strict mode is enforced — all new code must pass `mypy src`
