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
    """Numbering-space ownership for sequential DB migrations.

    Reserved for P1 — schema is captured now so authors can populate it,
    but plan-stage routing currently emits a warning rather than acting.
    """

    fork_owns_numbers_above: int | None = Field(default=None, ge=0)
    upstream_take_target_max: int | None = Field(default=None, ge=0)
    on_collision: MigrationCollisionRule | None = None


class ForksProfile(BaseModel):
    """Top-level `.merge/forks-profile.yaml` schema."""

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
