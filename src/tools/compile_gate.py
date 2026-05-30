"""P3/P4 (Wave 4): shared "is a compile/build gate configured?" predicate.

The always-on per-file syntax gate (``check_syntax``) is balance-only for
compiled languages (TS/JS/Go/Rust/Java/Kotlin) — it cannot catch a
brace-balanced merge that does not typecheck (a type error, an undefined-ref,
a wrong call signature). Real compile-level correctness depends entirely on the
operator having configured a post-merge compile/build gate: either
``build_check`` (``tsc --noEmit`` / ``go build`` …) or a ``gate`` command.

This predicate is the single source of truth for "does this run have a compile
gate at all?", used by:

- P4 preflight (``src/cli/preflight.py``) — a startup advisory when none is set.
- P3 report-time (``report_generation.py``) — a ``state.errors`` advisory when
  compiled-language files were auto-merged with no compile gate, and the opt-in
  ``build_check.require_for_compiled_langs`` soft gate.

Deliberately conservative (favours *not* nagging): any configured ``build_check``
or any non-empty ``gate`` command counts. An operator who set up any gating is
gate-aware; the advisory targets the "nothing configured at all" case, which is
the genuine silent-green hole. Per-language gate coverage ("your pytest gate
does not cover the TS files you merged") is a finer signal left for a future
refinement — see doc/review.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from src.models.config import MergeConfig
from src.models.decision import DecisionSource, MergeDecision
from src.models.state import MergeState
from src.tools.syntax_checker import balance_only_language_suffixes

_AUTO_MERGE_DECISIONS = frozenset(
    {
        MergeDecision.TAKE_CURRENT,
        MergeDecision.TAKE_TARGET,
        MergeDecision.SEMANTIC_MERGE,
        MergeDecision.MANUAL_PATCH,
    }
)


def has_compile_gate(config: MergeConfig) -> bool:
    """True when a post-merge compile/build gate is configured (``build_check``
    enabled with a command, or any ``gate`` command with a non-empty command).
    """
    bc = config.build_check
    if bc.enabled and bc.command.strip():
        return True
    gate = config.gate
    if gate.enabled and any(cmd.command.strip() for cmd in gate.commands):
        return True
    return False


def compiled_language_paths(file_paths: Iterable[str]) -> list[str]:
    """The subset of ``file_paths`` whose extension is a balance-only compiled
    language — i.e. files whose semantic correctness is NOT covered by the
    always-on syntax gate and needs a compile gate.
    """
    suffixes = balance_only_language_suffixes()
    return [fp for fp in file_paths if Path(fp).suffix.lower() in suffixes]


def auto_merged_compiled_paths_without_gate(state: MergeState) -> list[str]:
    """Compiled-language files that were auto-merged (a non-human take/merge
    decision) when NO compile gate is configured — exactly the set at risk of a
    silent uncompilable ``COMPLETED``. Empty when a compile gate exists, or when
    no compiled-language file was auto-merged. Sorted for stable reporting.
    """
    if has_compile_gate(state.config):
        return []
    merged = [
        fp
        for fp, rec in state.file_decision_records.items()
        if rec.decision in _AUTO_MERGE_DECISIONS
        and rec.decision_source != DecisionSource.HUMAN
    ]
    return sorted(compiled_language_paths(merged))
