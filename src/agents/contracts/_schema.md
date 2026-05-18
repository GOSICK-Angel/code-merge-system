# Agent Contracts

Each `<name>.yaml` in this directory is the **single source of truth** for one
agent's behavioral contract.  The schema is defined and validated by
[`src/agents/contract.py`](../contract.py) (`AgentContract` Pydantic model).

## Fields

| Field | Type | Purpose |
|---|---|---|
| `name` | str | Must equal the file stem and the `AgentRegistry` key. |
| `version` | int | Contract schema revision (≥ 0). Must be bumped when prompt content, aggregation rules, or input/output schema change (see [Versioning](#versioning)). All 7 shipped yaml declare `version: 1`; default `0` is a forward-compat fallback for future yaml that omit the field. |
| `inputs` | list[str] | Whitelist of `MergeState` attributes the agent may read. Access to fields outside this list raises `FieldNotInContract` when the agent runs against a restricted `ReadOnlyStateView`. |
| `output_schema` | str | Name of the Pydantic model the agent's `run()` returns (or wraps in an `AgentMessage.payload`). |
| `gates` | list[str] | Prompt gate IDs (registered in `src/llm/prompts/gate_registry.py`) that this agent is permitted to invoke. |
| `forbidden` | list[ForbiddenRule] | Behaviors the agent must never exhibit. Enforced by `tests/unit/test_agent_contracts.py` via AST/text scan plus runtime assertions where practical. |
| `collaboration` | enum | `compute` / `review_only` / `propose_then_confirm`. Controls how the orchestrator and HumanInterface interpret outputs. |
| `requires_human_options` | bool | When true, any user-facing decision must render ≥2 labeled options with a recommended pick (CCGS "Ask→Options→Decide" pattern). |

## Forbidden rules

| Rule | Meaning | How it is enforced |
|---|---|---|
| `writes_state` | Agent must not mutate `MergeState` directly. | Static scan: no left-hand assignment `state\.\w+\s*=` in the agent module. Runtime: `ReadOnlyStateView` raises `PermissionError` on `__setattr__`. |
| `direct_llm_call` | Agent must not bypass `BaseAgent._call_llm_with_retry`. | Static scan: `self.llm.complete(` / `self.llm.chat(` not allowed outside `base_agent.py`. |
| `fills_missing_fields_with_defaults` | Agent must not silently substitute defaults for absent LLM output fields. | Static scan: no `... or <literal>` / `.get(..., <literal>)` over known required fields. (Partial — relies on reviewer discipline.) |

## Collaboration patterns

- **`compute`** — pure function.  Orchestrator passes inputs, agent returns
  output.  No user interaction.  Examples: `planner`, `conflict_analyst`,
  `executor`.
- **`review_only`** — agent receives a `ReadOnlyStateView` and returns a
  verdict.  Must never write.  Examples: `judge`, `planner_judge`.
- **`propose_then_confirm`** — before any final commit, the agent presents ≥2
  options with a recommendation and waits for explicit user approval.  Used by
  `human_interface`.

## Runtime loading

Agents opt in by setting a class attribute:

```python
class PlannerJudgeAgent(BaseAgent):
    contract_name = "planner_judge"
```

`BaseAgent.contract` is a lazily-loaded property.  Agents that do not declare
`contract_name` behave exactly as before (backward compatible).

## Versioning

The `version` field is an integer revision counter that downstream consumers
(cache keys, snapshot compatibility checks, telemetry) use to detect when an
agent's effective behavior has changed.

**Bump `version` (by +1) when any of the following changes**:

1. **Prompt content** — the text or template registered under any `gates:` ID
   for this agent is modified in a way that changes model output (whitespace
   / typo fixes do not require a bump).
2. **Aggregation rules** — internal reducers (e.g. `_aggregate_chunked_analyses`,
   `_merge_batch_plans`) change their precedence, threshold, or penalty logic.
3. **Input / output schema** — fields are added to / removed from `inputs`,
   `output_schema` changes name or shape, or a new gate is registered that the
   agent may invoke.

**Do not bump for**:

- Pure refactors that preserve I/O and prompt text.
- Comment / docstring edits.
- Adding tests.

When bumping, update the `version: N` line in the yaml only; the loader
accepts any `int >= 0`. Cross-run caches (introduced in U3) include the
contract version in their key so a bump invalidates stale entries
automatically.

## Adding a new contract

1. Create `<name>.yaml` in this directory.
2. Declare the minimum `inputs` set — under-declaring is safer than
   over-declaring; tests will fail loudly when the agent reaches for a missing
   field.
3. Register any new prompt IDs in `src/llm/prompts/gate_registry.py`.
4. Set `contract_name` on the agent class.
5. Add coverage in `tests/unit/test_agent_contracts.py` if the agent has
   contract-specific invariants beyond the generic checks.
