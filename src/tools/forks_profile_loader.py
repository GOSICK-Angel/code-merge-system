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

import yaml
from pydantic import ValidationError

from src.models.forks_profile import (
    ForksProfile,
    MigrationCollisionRule,
    RemovedDomain,
    RewrittenModule,
)
from src.tools.file_classifier import matches_any_pattern


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

    try:
        profile = ForksProfile.model_validate(data)
    except ValidationError as e:
        raise ForksProfileError(
            f"forks-profile schema validation failed at {profile_path}: {e}"
        ) from e

    logger.info(
        "Loaded forks-profile from %s: %d removed_domain(s), "
        "%d rewritten_module(s), %d fork_only_feature(s), "
        "migration_policy=%s",
        profile_path,
        len(profile.removed_domains),
        len(profile.rewritten_modules),
        len(profile.fork_only_features),
        "set" if profile.migration_policy is not None else "unset",
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
            reason = domain.reason or "n/a"
            lines.append(f"- {domain.name}: {reason}")
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
            lines.append(
                f"- matches removed_domains[{domain_hit.name}] "
                f"(reason={domain_hit.reason or 'n/a'})"
            )
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
]
