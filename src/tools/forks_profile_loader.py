"""Load and query the optional `.merge/forks-profile.yaml` fork-identity file.

Behavior:
  - Missing file        → returns ``None`` (caller treats as no-op).
  - Empty file          → returns ``None``.
  - YAML parse error    → raises ``ForksProfileError``.
  - Schema violation    → raises ``ForksProfileError``.

The match helpers reuse the path-glob semantics defined in
``src.tools.file_classifier.matches_any_pattern`` so that profile globs
behave identically to ``always_take_*_patterns`` in user-facing config.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import ValidationError

from src.models.forks_profile import (
    DEPRECATED_YAML_FIELDS,
    ForkOnlyFeature,
    ForksProfile,
    ForksProfileYaml,
    MigrationCollisionAction,
    MigrationCollisionRule,
    MigrationPolicy,
    RemovedDomain,
    RewrittenModule,
)
from src.tools.file_classifier import matches_any_pattern

if TYPE_CHECKING:
    from src.tools.git_tool import GitTool


# Inlined to avoid `tools → cli` import dependency (cli/__init__ eagerly
# pulls in main, which registers commands, which pull tools modules — a
# cycle when this loader is on the import path of any tool that the cli
# transitively reaches). Keep the constant in sync with
# ``src.cli.paths.get_forks_profile_path``.
_FORKS_PROFILE_FILENAME = "forks-profile.yaml"
_MERGE_DIR = ".merge"


def _resolve_default_profile_path(repo_path: str | Path) -> Path:
    return Path(repo_path) / _MERGE_DIR / _FORKS_PROFILE_FILENAME


logger = logging.getLogger(__name__)


class ForksProfileError(ValueError):
    """Raised when a forks-profile.yaml exists but cannot be loaded."""


def load_forks_profile(repo_path: str | Path = ".") -> ForksProfile | None:
    """Load `<repo>/.merge/forks-profile.yaml` if present.

    Returns ``None`` when the file is absent or empty so callers can
    skip routing without a try/except. YAML / schema failures raise
    ``ForksProfileError`` because a present-but-broken profile likely
    represents author intent that must be surfaced, not silently dropped.
    """
    profile_path = _resolve_default_profile_path(repo_path)
    if not profile_path.exists():
        return None

    try:
        raw = profile_path.read_text(encoding="utf-8")
    except OSError as e:
        raise ForksProfileError(
            f"failed to read forks-profile at {profile_path}: {e}"
        ) from e

    if not raw.strip():
        return None

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise ForksProfileError(
            f"forks-profile YAML parse failed at {profile_path}: {e}"
        ) from e

    if data is None:
        return None
    if not isinstance(data, dict):
        raise ForksProfileError(
            f"forks-profile root must be a mapping, got {type(data).__name__} "
            f"at {profile_path}"
        )

    deprecated_present = [k for k in DEPRECATED_YAML_FIELDS if k in data]
    if deprecated_present:
        raise ForksProfileError(
            f"forks-profile.yaml at {profile_path} declares deprecated "
            f"field(s) {deprecated_present}. These are now auto-computed "
            "from git divergence on every run; remove these sections from "
            "your yaml. Only `version`, `fork`, `removed_domains`, and "
            "`rewritten_modules` are user-authored."
        )

    try:
        yaml_profile = ForksProfileYaml.model_validate(data)
    except ValidationError as e:
        raise ForksProfileError(
            f"forks-profile schema validation failed at {profile_path}: {e}"
        ) from e

    profile = ForksProfile(
        version=yaml_profile.version,
        fork=yaml_profile.fork,
        removed_domains=yaml_profile.removed_domains,
        rewritten_modules=yaml_profile.rewritten_modules,
    )

    logger.info(
        "Loaded forks-profile (yaml-side) from %s: %d removed_domain(s), "
        "%d rewritten_module(s)",
        profile_path,
        len(profile.removed_domains),
        len(profile.rewritten_modules),
    )
    return profile


def find_removed_domain_match(
    profile: ForksProfile, file_path: str
) -> RemovedDomain | None:
    """Return the first removed_domain whose path-globs contain ``file_path``.

    Iteration follows YAML declaration order; first match wins so authors
    can express override priority by ordering.
    """
    for domain in profile.removed_domains:
        if domain.paths and matches_any_pattern(file_path, domain.paths):
            return domain
    return None


def find_rewritten_module_match(
    profile: ForksProfile, file_path: str
) -> RewrittenModule | None:
    """Return the first rewritten_module whose ``path`` glob matches."""
    for module in profile.rewritten_modules:
        if module.path and matches_any_pattern(file_path, [module.path]):
            return module
    return None


def summarize_for_log(profile: ForksProfile) -> str:
    """Human-readable one-liner used in ctx.notify() and structured logs."""
    name = profile.fork.name or "<unnamed>"
    return (
        f"forks-profile[{name}] "
        f"removed_domains={len(profile.removed_domains)} "
        f"rewritten_modules={len(profile.rewritten_modules)} "
        f"fork_only_features={len(profile.fork_only_features)} "
        f"migration_policy={'yes' if profile.migration_policy else 'no'}"
    )


def is_path_profile_pinned(profile: ForksProfile, file_path: str) -> bool:
    """True if the path matches any removed_domain or rewritten_module.

    Used by judge to downgrade deterministic CRITICAL/HIGH issues to INFO
    when divergence on this path is the explicit fork-author intent.
    """
    if find_rewritten_module_match(profile, file_path) is not None:
        return True
    if find_removed_domain_match(profile, file_path) is not None:
        return True
    return False


_ANALYST_MAX_DOMAINS = 8
_ANALYST_MAX_MODULES = 8


def format_analyst_context(profile: ForksProfile, file_path: str) -> str:
    """Build a compact, fork-agnostic context block for ConflictAnalyst.

    Returns ``""`` when the profile has nothing useful to inject; callers
    can splice the result into existing context with simple concatenation.
    """
    if profile.is_empty() and not profile.fork.positioning:
        return ""

    lines: list[str] = ["## Fork identity (forks-profile.yaml)"]
    if profile.fork.positioning:
        lines.append(f"Positioning: {profile.fork.positioning}")

    if profile.removed_domains:
        lines.append("")
        lines.append("Removed domains (fork dropped these areas):")
        for domain in profile.removed_domains[:_ANALYST_MAX_DOMAINS]:
            # Empty ``reason`` (drafter found no delete-commit evidence)
            # is dropped here rather than padded with ``n/a`` so the LLM
            # context isn't polluted with placeholder noise.
            if domain.reason:
                lines.append(f"- {domain.name}: {domain.reason}")
            else:
                lines.append(f"- {domain.name}")
        if len(profile.removed_domains) > _ANALYST_MAX_DOMAINS:
            extra = len(profile.removed_domains) - _ANALYST_MAX_DOMAINS
            lines.append(f"- (+{extra} more)")

    if profile.rewritten_modules:
        lines.append("")
        lines.append("Rewritten modules (fork re-implemented; respect policy):")
        for module in profile.rewritten_modules[:_ANALYST_MAX_MODULES]:
            note = module.note or ""
            tail = f" — {note}" if note else ""
            lines.append(f"- {module.path}: {module.policy.value}{tail}")
        if len(profile.rewritten_modules) > _ANALYST_MAX_MODULES:
            extra = len(profile.rewritten_modules) - _ANALYST_MAX_MODULES
            lines.append(f"- (+{extra} more)")

    domain_hit = find_removed_domain_match(profile, file_path)
    module_hit = find_rewritten_module_match(profile, file_path)
    if domain_hit is not None or module_hit is not None:
        lines.append("")
        lines.append("This file:")
        if module_hit is not None:
            lines.append(
                f"- matches rewritten_modules[{module_hit.path}] "
                f"policy={module_hit.policy.value}"
            )
        if domain_hit is not None:
            if domain_hit.reason:
                lines.append(
                    f"- matches removed_domains[{domain_hit.name}] "
                    f"(reason={domain_hit.reason})"
                )
            else:
                lines.append(f"- matches removed_domains[{domain_hit.name}]")
        lines.append(
            "Avoid recommending take_target on paths the fork has "
            "deliberately dropped or rewritten."
        )

    return "\n".join(lines)


_MIGRATION_NUMBER_RE = re.compile(r"\d+")


def extract_migration_number(file_path: str) -> int | None:
    """Return the first contiguous digit run in the file's basename.

    Covers the common migration-file naming styles:

    - ``026_payments.sql``                → 26
    - ``0001_initial.py``                 → 1
    - ``20240101120000_widgets.sql``      → 20240101120000
    - ``V1__init.sql`` (Flyway)           → 1
    - ``schema.sql``                      → ``None``

    Pure basename inspection — directory components are ignored so the
    helper is filesystem-agnostic.
    """
    basename = Path(file_path).name
    match = _MIGRATION_NUMBER_RE.search(basename)
    if match is None:
        return None
    return int(match.group(0))


def find_migration_collision(
    profile: ForksProfile, file_path: str
) -> tuple[int, MigrationCollisionRule] | None:
    """Detect upstream migration files that collide with the fork's
    reserved numbering space.

    Returns ``(number, rule)`` when ``file_path``:
      1. matches one of ``migration_policy.path_globs``,
      2. has an extractable migration number, and
      3. that number is **strictly greater than** ``upstream_take_target_max``.

    A missing ``upstream_take_target_max`` falls back to
    ``fork_owns_numbers_above`` (the half-open interval is the same when
    the policy is consistent). When neither bound is set, returns
    ``None`` — the policy author opted out of numeric checks.

    The ``rule`` returned is ``migration_policy.on_collision`` if the
    profile sets one; otherwise a default ``escalate_human`` rule is
    synthesised so callers always have a definite action to take.
    """
    policy = profile.migration_policy
    if policy is None or not policy.path_globs:
        return None
    if not matches_any_pattern(file_path, policy.path_globs):
        return None
    number = extract_migration_number(file_path)
    if number is None:
        return None

    upper_bound: int | None = policy.upstream_take_target_max
    if upper_bound is None:
        # ``fork_owns_numbers_above = N`` means fork owns N+1.., so the
        # last safe upstream number is N itself.
        upper_bound = policy.fork_owns_numbers_above
    if upper_bound is None:
        return None
    if number <= upper_bound:
        return None

    rule = policy.on_collision or MigrationCollisionRule()
    return (number, rule)


def compute_auto_overlay(
    git_tool: "GitTool",
    *,
    merge_base: str,
    fork_ref: str,
    upstream_ref: str,
    migration_globs: list[str] | None = None,
    cluster_min_files: int | None = None,
) -> tuple[list[ForkOnlyFeature], MigrationPolicy | None]:
    """Compute the mechanically-derivable subset of the fork profile.

    Replaces what fork maintainers used to author by hand:
      - ``fork_only_features``: every FORK_ONLY path, clustered to a
        deepest-common-prefix glob.
      - ``migration_policy``: emitted only when fork-only migrations
        occupy numbers strictly above the upstream max; uses
        ``DEFAULT_MIGRATION_GLOBS`` when ``migration_globs`` is None.

    The git lookups are deferred imports so module load order stays
    clean: this loader is on the import path of every agent that touches
    state, but the drafter pulls in tree-sitter and other heavy deps
    only relevant to ``merge forks-profile init``.
    """
    from src.tools.forks_profile_drafter import (
        DEFAULT_MIGRATION_GLOBS,
        draft_fork_only_features,
        draft_migration_policy,
    )
    from src.models.diff import ForkDivergence
    from src.tools.file_classifier import compute_fork_divergence_map

    divergence = compute_fork_divergence_map(
        merge_base=merge_base,
        head_ref=fork_ref,
        upstream_ref=upstream_ref,
        git_tool=git_tool,
    )

    drafted_features = draft_fork_only_features(
        divergence, cluster_min_files=cluster_min_files
    )
    auto_features = [
        ForkOnlyFeature(path=f.path, note=f.note) for f in drafted_features
    ]

    globs = list(migration_globs) if migration_globs else list(DEFAULT_MIGRATION_GLOBS)
    base_files = git_tool.list_files(merge_base) if merge_base else []
    fork_files = git_tool.list_files(fork_ref)
    fork_only_paths = [
        p for p, d in divergence.items() if d == ForkDivergence.FORK_ONLY
    ]
    drafted_policy = draft_migration_policy(
        base_files=base_files,
        fork_files=fork_files,
        fork_only_files=fork_only_paths,
        path_globs=globs,
    )
    auto_migration: MigrationPolicy | None = None
    if drafted_policy is not None:
        auto_migration = MigrationPolicy(
            path_globs=list(drafted_policy.path_globs),
            fork_owns_numbers_above=drafted_policy.fork_owns_numbers_above,
            upstream_take_target_max=drafted_policy.upstream_take_target_max,
            on_collision=MigrationCollisionRule(
                action=MigrationCollisionAction(drafted_policy.on_collision),
            ),
        )

    return auto_features, auto_migration


def build_effective_profile(
    yaml_profile: ForksProfile | None,
    auto_features: list[ForkOnlyFeature],
    auto_migration: MigrationPolicy | None,
) -> ForksProfile | None:
    """Combine the yaml-loaded profile with auto-computed overlay fields.

    Returns ``None`` only when both inputs contribute nothing — the
    caller can short-circuit routing in that case. Otherwise the
    effective profile carries:

      - yaml side: ``fork`` / ``removed_domains`` / ``rewritten_modules``
      - auto side: ``fork_only_features`` / ``migration_policy``

    The function is pure — no git, no I/O — so initialize phase can
    construct it deterministically from already-collected data.
    """
    if yaml_profile is None and not auto_features and auto_migration is None:
        return None

    base = yaml_profile or ForksProfile()
    return base.model_copy(
        update={
            "fork_only_features": auto_features,
            "migration_policy": auto_migration,
        }
    )


__all__ = [
    "ForksProfileError",
    "load_forks_profile",
    "find_removed_domain_match",
    "find_rewritten_module_match",
    "is_path_profile_pinned",
    "format_analyst_context",
    "extract_migration_number",
    "find_migration_collision",
    "summarize_for_log",
    "compute_auto_overlay",
    "build_effective_profile",
]
