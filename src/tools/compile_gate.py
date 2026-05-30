"""P3/P4 (Wave 4): shared "is a compile/build gate configured?" predicate.

The always-on per-file syntax gate (``check_syntax``) is balance-only for
compiled languages (TS/JS/Go/Rust/Java/Kotlin) — it cannot catch a
brace-balanced merge that does not typecheck (a type error, an undefined-ref,
a wrong call signature). Real compile-level correctness depends entirely on the
operator having configured a post-merge compile/build gate: either
``build_check`` (``tsc --noEmit`` / ``go build`` …) or a ``gate`` command.

Two predicates, coarse → fine:

- ``has_compile_gate(config)`` — "is *any* compile/build gate configured at all?"
  Coarse boolean, used by the P4 preflight (``src/cli/preflight.py``) startup
  nag, which has no merged file-set to reason about per-language.
- ``gate_covered_suffixes(config)`` — W5 W4 per-language refinement: *which*
  compiled-language suffixes the configured gate(s) actually cover. The P3
  report-time advisory (``report_generation.py``) and the opt-in
  ``build_check.require_for_compiled_langs`` soft gate use it so a Python-only or
  lint-only gate no longer suppresses the advisory for the TS/Go/Rust/Java files
  it does not actually typecheck.

Coverage classification stays deliberately conservative on *unknown* commands
(favours *not* nagging): a recognised compile tool (``tsc``/``go build``/
``cargo``/``javac`` …) covers its language; a recognised lint/format/test tool
(``eslint``/``ruff``/``prettier`` …) covers nothing — ESLint and friends lint,
they do not typecheck, so they must not suppress the advisory; and an opaque,
unrecognised command (a bundler/script like ``pnpm run build`` or ``make`` that
we cannot attribute to a language) is conservatively treated as covering
everything, because such commands usually do compile the whole tree and flagging
them would be nag fatigue. The advisory therefore only ever *narrows* suppression
for gates we can positively attribute to a strict language subset.
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

# Balance-only language groups (mirror ``_BALANCE_SPECS`` in syntax_checker).
_TS_JS = frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"})
_GO = frozenset({".go"})
_RUST = frozenset({".rs"})
_JVM = frozenset({".java", ".kt"})

# Registered ``baseline_parser`` id → the compiled-language suffixes a gate using
# that parser actually *typecheck/compile*-covers. Lint/format/test parsers and
# Python parsers map to the empty set (Python is not balance-only; lint ≠
# compile) — they are RECOGNISED (so they do not trigger the conservative
# unknown-command fallback) but cover nothing of the at-risk set.
_PARSER_LANG_SUFFIXES: dict[str, frozenset[str]] = {
    "tsc_errors": _TS_JS,
    "go_test_json": _GO,
    "cargo_test_json": _RUST,
    "junit_xml": _JVM,
    "eslint_json": frozenset(),
    "ruff_json": frozenset(),
    "mypy_json": frozenset(),
    "basedpyright_json": frozenset(),
    "pytest_summary": frozenset(),
}

# Substring tokens of a *compile-capable* command → the suffixes it covers. A
# command may match several (e.g. ``tsc && go build``); coverage is the union.
_COMPILE_TOKENS: tuple[tuple[tuple[str, ...], frozenset[str]], ...] = (
    (("tsc", "vue-tsc"), _TS_JS),
    (("go build", "go vet", "go test"), _GO),
    (("cargo", "rustc"), _RUST),
    (("javac", "gradle", "mvn", "maven"), _JVM),
)

# Substring tokens of a command we RECOGNISE as lint/format/test-only — it covers
# no balance-only language, but being recognised it does NOT fall through to the
# conservative "unknown ⇒ covers everything" rule (the headline fix: a ruff- or
# eslint-only gate must keep flagging the compiled files it does not typecheck).
_NONCOMPILE_TOKENS: frozenset[str] = frozenset(
    {
        "eslint",
        "prettier",
        "biome",
        "stylelint",
        "ruff",
        "flake8",
        "pylint",
        "black",
        "isort",
        "mypy",
        "pyright",
        "basedpyright",
        "pytest",
    }
)


def _classify_command(command: str) -> frozenset[str] | None:
    """The compiled-language suffixes a single gate/build command covers.

    Returns a (possibly empty) suffix set when the command is RECOGNISED — a
    compile tool maps to its language(s), a lint/format/test tool maps to the
    empty set. Returns ``None`` when the command is UNRECOGNISED (an opaque
    bundler/script); the caller treats ``None`` as "conservatively covers
    everything" to avoid nag fatigue.
    """
    c = command.lower()
    covered: set[str] = set()
    matched = False
    for tokens, suffixes in _COMPILE_TOKENS:
        if any(tok in c for tok in tokens):
            covered |= suffixes
            matched = True
    if matched:
        return frozenset(covered)
    if any(tok in c for tok in _NONCOMPILE_TOKENS):
        return frozenset()
    return None


def gate_covered_suffixes(config: MergeConfig) -> frozenset[str]:
    """The compiled-language suffixes the configured compile/build gate(s) cover.

    Per-language refinement of :func:`has_compile_gate`. A Python-only or
    lint-only gate covers nothing of the balance-only set (so it no longer
    suppresses the advisory for TS/Go/Rust/Java files); an opaque, unattributable
    command is conservatively treated as covering everything. Empty when nothing
    relevant is configured.
    """
    all_suffixes = balance_only_language_suffixes()
    covered: set[str] = set()

    bc = config.build_check
    if bc.enabled and bc.command.strip():
        res = _classify_command(bc.command)
        if res is None:
            return all_suffixes
        covered |= res

    gate = config.gate
    if gate.enabled:
        for cmd in gate.commands:
            if not cmd.command.strip():
                continue
            parser = cmd.baseline_parser.strip()
            parser_suffixes = _PARSER_LANG_SUFFIXES.get(parser)
            if parser and parser_suffixes is not None:
                covered |= parser_suffixes
                continue
            res = _classify_command(cmd.command)
            if res is None:
                return all_suffixes
            covered |= res

    return frozenset(covered)


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
    decision) and are NOT covered by a configured compile gate for their
    language — exactly the set at risk of a silent uncompilable ``COMPLETED``.

    Per-language (W5 W4): a file is at risk only when its suffix is outside
    :func:`gate_covered_suffixes`, so a TS file auto-merged under a Python-only
    or lint-only gate is still flagged, while a co-merged file whose language the
    gate does cover is not. Empty when every auto-merged compiled file is gate-
    covered (incl. the conservative opaque-command full-coverage case). Sorted
    for stable reporting.
    """
    covered = gate_covered_suffixes(state.config)
    merged = [
        fp
        for fp, rec in state.file_decision_records.items()
        if rec.decision in _AUTO_MERGE_DECISIONS
        and rec.decision_source != DecisionSource.HUMAN
    ]
    return sorted(
        fp
        for fp in compiled_language_paths(merged)
        if Path(fp).suffix.lower() not in covered
    )
