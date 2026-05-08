"""Fork-level identity contract loaded from `.merge/forks-profile.yaml`.

The profile is authored by fork maintainers to declare structural divergences
that the upstream merge system cannot infer from git alone:

  - removed_domains       — entire feature areas the fork has dropped
  - rewritten_modules     — modules the fork has substantively re-implemented
  - fork_only_features    — paths that exist only in the fork
  - migration_policy      — DB migration numbering ownership rules

The schema is intentionally fork-agnostic: no field names, defaults, or
documentation references any specific upstream project. Targets that do
not ship a `forks-profile.yaml` get fully default behavior (no-op).

Consumed by:
  - InitializePhase  — pre-decides files matching removed_domains /
                       rewritten_modules before the AI flow.
  - JudgePhase       — (P0-2, separate change) downgrades critical issues
                       on paths the fork has intentionally removed/rewritten.
  - ExecutorPhase    — (P1, separate change) honors
                       `take_current_with_diff_note` policy by stashing the
                       upstream diff for human review.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RewriteMergePolicy(str, Enum):
    """Merge strategy for a fork-rewritten module when both sides change."""

    SEMANTIC_MERGE_WITH_ALERT = "semantic_merge_with_alert"
    ESCALATE_HUMAN = "escalate_human"
    TAKE_CURRENT_WITH_DIFF_NOTE = "take_current_with_diff_note"


class ForkIdentity(BaseModel):
    """Free-form metadata describing the fork. All fields optional.

    `extra="allow"` lets fork authors record additional metadata (registry,
    distribution, source_repo, etc.) without the schema rejecting it; the
    merge system only logs `name` and surfaces the rest as opaque context.
    """

    model_config = ConfigDict(extra="allow")

    name: str = ""
    upstream: str = ""
    positioning: str = ""


class RemovedDomain(BaseModel):
    """An entire feature area the fork has intentionally dropped."""

    name: str = Field(min_length=1)
    paths: list[str] = Field(default_factory=list)
    reason: str = ""
    removed_in: str = ""

    @field_validator("paths")
    @classmethod
    def _strip_blank_globs(cls, v: list[str]) -> list[str]:
        return [p.strip() for p in v if p and p.strip()]


class RewrittenModule(BaseModel):
    """A module the fork has substantively re-implemented vs upstream."""

    path: str = Field(min_length=1)
    policy: RewriteMergePolicy
    note: str = ""
    examples: list[str] = Field(default_factory=list)


class ForkOnlyFeature(BaseModel):
    """A subtree that exists only in the fork (not in upstream)."""

    path: str = Field(min_length=1)
    note: str = ""


class MigrationCollisionAction(str, Enum):
    ESCALATE_HUMAN = "escalate_human"
    TAKE_CURRENT = "take_current"


class MigrationCollisionRule(BaseModel):
    action: MigrationCollisionAction = MigrationCollisionAction.ESCALATE_HUMAN
    note: str = ""


class MigrationPolicy(BaseModel):
    """Numbering-space ownership for sequential migration files.

    Used by plan-stage routing to detect when upstream introduces a
    migration whose number falls inside the fork's reserved space, and
    apply ``on_collision.action`` to the file before the AI flow runs.

    ``path_globs`` is required for detection — leaving it empty disables
    the check (a number-only rule would otherwise hijack any path with a
    leading numeric component).
    """

    path_globs: list[str] = Field(
        default_factory=list,
        description=(
            "Path globs identifying which files are migrations (e.g. "
            "['**/migrations/*.sql', '**/db/migrate/*.py']). Empty = "
            "collision detection disabled."
        ),
    )
    fork_owns_numbers_above: int | None = Field(default=None, ge=0)
    upstream_take_target_max: int | None = Field(default=None, ge=0)
    on_collision: MigrationCollisionRule | None = None


DEPRECATED_YAML_FIELDS: tuple[str, ...] = ("fork_only_features", "migration_policy")
"""Top-level keys that yaml authors must NOT set anymore.

These fields are auto-computed from git divergence at the start of every
run and live only on the runtime ``ForksProfile`` constructed by the
loader. ``ForksProfileYaml.model_validate`` rejects yaml that still
declares them so a stale checked-in profile surfaces as a hard error
rather than silently shadowing the auto-computed view.
"""


class ForksProfileYaml(BaseModel):
    """Strict schema for the user-authored `.merge/forks-profile.yaml`.

    Distinct from the runtime :class:`ForksProfile`: yaml authors only
    declare the subset that requires human judgement
    (``removed_domains`` / ``rewritten_modules`` / ``fork``). The
    mechanical ``fork_only_features`` and ``migration_policy`` are
    derived from git at runtime and rejected here so a yaml never
    silently shadows the auto-computed view.
    """

    model_config = ConfigDict(extra="forbid")

    version: int = Field(default=1, ge=1)
    fork: ForkIdentity = Field(default_factory=ForkIdentity)
    removed_domains: list[RemovedDomain] = Field(default_factory=list)
    rewritten_modules: list[RewrittenModule] = Field(default_factory=list)


class ForksProfile(BaseModel):
    """Runtime effective fork profile.

    Combines yaml-authored fields (``removed_domains`` /
    ``rewritten_modules`` / ``fork``) with auto-computed fields
    (``fork_only_features`` / ``migration_policy``) the loader derives
    from the current git state on every run. Code paths that previously
    read directly from the yaml-loaded profile keep working — they just
    see the effective view now.
    """

    model_config = ConfigDict(extra="forbid")

    version: int = Field(default=1, ge=1)
    fork: ForkIdentity = Field(default_factory=ForkIdentity)
    removed_domains: list[RemovedDomain] = Field(default_factory=list)
    rewritten_modules: list[RewrittenModule] = Field(default_factory=list)
    fork_only_features: list[ForkOnlyFeature] = Field(default_factory=list)
    migration_policy: MigrationPolicy | None = None

    def is_empty(self) -> bool:
        return (
            not self.removed_domains
            and not self.rewritten_modules
            and not self.fork_only_features
            and self.migration_policy is None
        )
