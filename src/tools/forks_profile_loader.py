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
from pathlib import Path

import yaml
from pydantic import ValidationError

from src.cli.paths import get_forks_profile_path
from src.models.forks_profile import (
    ForksProfile,
    RemovedDomain,
    RewrittenModule,
)
from src.tools.file_classifier import matches_any_pattern

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
    profile_path = get_forks_profile_path(str(repo_path))
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


__all__ = [
    "ForksProfileError",
    "load_forks_profile",
    "find_removed_domain_match",
    "find_rewritten_module_match",
    "summarize_for_log",
]
