"""P4 (Wave 4): config preflight advisories.

A run's correctness on auto-merged files rests on operator-supplied config —
an adequate ``max_tokens`` (so chunked merges do not self-truncate-and-escalate)
and a real compile gate (so a brace-balanced-but-uncompilable merge does not
reach COMPLETED). Both fail *silently* today when absent/undersized. This module
surfaces them as non-fatal advisories.

Surfaced both on ``merge validate`` (explicit) and at the start of a real
``merge`` run (so the operator sees them without having to ask). Lives in its
own module so both ``cli/main.py`` (validate) and ``cli/commands/run.py`` (run)
can import it without an import cycle.

Advisories are deliberately tuned to stay silent on the default config and fire
only on the genuinely-risky shapes (e.g. a small-``max_tokens`` proxy model, or
a repo with no compile gate at all).
"""

from __future__ import annotations

from src.models.config import MergeConfig
from src.tools.compile_gate import has_compile_gate

# #9D couples the executor's chunk size to its output budget: a chunk pair emits
# ~2*chunk chars; at ~3.5 chars/token, staying under 0.8*max_tokens means
# chunk < 1.4*max_tokens chars. The executor auto-clamps to that, so it only
# risks (marginal, token-density-driven) truncation when the configured
# chunk_size_chars meets/exceeds the clamp — i.e. zero headroom beyond the 0.8
# factor. Below the clamp the configured size stands with spare budget.
_CHUNK_OUTPUT_TOKENS_RATIO = 1.4

# An analyst emits a compact structured analysis; below this it cannot reliably
# emit even a small verdict and may truncate.
_MIN_ANALYST_MAX_TOKENS = 2048

# OpenAI reasoning models share hidden-reasoning + visible budget; below this the
# visible answer can come back empty (the client auto-bumps, but warn so the
# operator sets it explicitly).
_REASONING_MIN_MAX_TOKENS = 32768


def config_preflight_warnings(config: MergeConfig) -> list[str]:
    """Non-fatal advisories about silently-degraded behavior. Empty list on a
    well-provisioned config."""
    warnings: list[str] = []
    warnings.extend(_chunk_truncation_warnings(config))
    warnings.extend(_reasoning_floor_warnings(config))
    warnings.extend(_compile_gate_warnings(config))
    warnings.extend(_dependency_graph_warnings(config))
    return warnings


def _chunk_truncation_warnings(config: MergeConfig) -> list[str]:
    warnings: list[str] = []
    executor = config.agents.executor
    safe_for_executor = int(executor.max_tokens * _CHUNK_OUTPUT_TOKENS_RATIO)
    if config.chunk_size_chars >= safe_for_executor:
        warnings.append(
            f"chunk_size_chars={config.chunk_size_chars} meets/exceeds the "
            f"executor's output budget (max_tokens={executor.max_tokens} → "
            f"~{safe_for_executor} chars): large/dense file chunks may truncate "
            f"at the model's output cap and escalate instead of merging. Raise "
            f"agents.executor.max_tokens (>= {int(config.chunk_size_chars / _CHUNK_OUTPUT_TOKENS_RATIO)}) "
            f"or lower chunk_size_chars (<= {safe_for_executor})."
        )

    analyst = config.agents.conflict_analyst
    if analyst.max_tokens < _MIN_ANALYST_MAX_TOKENS:
        warnings.append(
            f"agents.conflict_analyst.max_tokens={analyst.max_tokens} is very "
            f"small (< {_MIN_ANALYST_MAX_TOKENS}); the conflict analysis JSON may "
            f"truncate. Note: unlike the executor, the analyst's chunking is NOT "
            f"auto-clamped to its output budget — keep its max_tokens adequate."
        )
    return warnings


def _reasoning_floor_warnings(config: MergeConfig) -> list[str]:
    from src.llm.client import _is_openai_reasoning_model

    warnings: list[str] = []
    for field_name in type(config.agents).model_fields:
        agent_cfg = getattr(config.agents, field_name)
        if (
            _is_openai_reasoning_model(agent_cfg.model)
            and agent_cfg.max_tokens < _REASONING_MIN_MAX_TOKENS
        ):
            warnings.append(
                f"agents.{field_name} uses reasoning model '{agent_cfg.model}' "
                f"with max_tokens={agent_cfg.max_tokens} < "
                f"{_REASONING_MIN_MAX_TOKENS}: hidden reasoning tokens can exhaust "
                f"the budget and return empty content (auto-bumped at runtime — "
                f"set it explicitly to avoid surprise)."
            )
    return warnings


def _compile_gate_warnings(config: MergeConfig) -> list[str]:
    if has_compile_gate(config):
        return []
    return [
        "no build_check or gate command is configured — the always-on per-file "
        "syntax gate is BALANCE-ONLY for compiled languages (TS/JS/Go/Rust/Java) "
        "and cannot catch a brace-balanced merge that does not typecheck. For "
        "production merges of compiled languages, configure build_check.command "
        "(e.g. 'tsc --noEmit', 'go build ./...') so type errors fail the verdict "
        "instead of reaching COMPLETED."
    ]


def _dependency_graph_warnings(config: MergeConfig) -> list[str]:
    if not config.dependency_graph.enabled:
        return []
    from src.tools.dep_extractors.treesitter_extractor import (
        missing_grammar_languages,
    )

    missing = missing_grammar_languages(config.dependency_graph.languages)
    if not missing:
        return []
    return [
        f"dependency_graph.enabled=true but tree-sitter grammar(s) for {missing} "
        f"are unavailable — the graph will silently yield no edges for these "
        f'languages and every graph consumer becomes a no-op. Install with: pip install ".[ast]"'
    ]
