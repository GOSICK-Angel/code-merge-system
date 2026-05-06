"""P1-1: fork preservation gate.

Detects C-class files (both sides changed) whose worktree blob ended up
byte-equal to upstream after auto_merge. P0-1 already removed the most
obvious silent ``TAKE_TARGET`` shortcut, but the same fork-loss symptom can
still arise via:

- ``SEMANTIC_MERGE`` whose LLM output coincidentally byte-equals upstream
  (the model "merged" by adopting upstream wholesale).
- Future regressions reintroducing implicit take_target paths.
- Cherry-pick / replay paths where fork's customizer commits never get
  applied on top.

The auditor consumes whatever the executor produced (no LLM cost) and
returns one ``PreservationLoss`` per likely-dropped file so the caller can
re-route to ConflictAnalyst or escalate to human.

Symmetry note: ``_b_class_sanity_check`` in ``auto_merge.py`` enforces the
opposite invariant for B-class (worktree MUST equal upstream). The two
checks share the same blob-sha primitive but flag opposite outcomes for
opposite categories.
"""

from __future__ import annotations

import logging
from typing import Protocol

from pydantic import BaseModel

from src.models.decision import MergeDecision
from src.models.diff import FileChangeCategory
from src.models.state import MergeState

logger = logging.getLogger(__name__)


DEFAULT_MIN_FORK_LINES: int = 50


class _GitToolLike(Protocol):
    def get_file_hash(self, ref: str, file_path: str) -> str | None: ...
    def get_worktree_blob_sha(self, file_path: str) -> str | None: ...


class PreservationLoss(BaseModel):
    file_path: str
    fork_lines_changed: int
    decision: MergeDecision
    reason: str


def audit_fork_preservation(
    state: MergeState,
    git_tool: _GitToolLike,
    *,
    min_fork_lines: int = DEFAULT_MIN_FORK_LINES,
) -> list[PreservationLoss]:
    """Return one ``PreservationLoss`` per C-class file whose worktree blob
    equals the upstream blob despite the fork having a material delta vs
    merge_base.

    Skips:
    - Files already escalated to ``ESCALATE_HUMAN`` (downstream already
      handles them).
    - Files whose fork-side ``lines_added + lines_deleted`` is below
      ``min_fork_lines`` (small drift overrides are plausibly intentional).
    - Files where either blob sha is unresolvable (D_MISSING / D_EXTRA-style
      asymmetry — equality is undefined).
    """
    if state.merge_plan is None:
        return []

    upstream_ref = state.config.upstream_ref
    fork_diffs = {fd.file_path: fd for fd in state.file_diffs}
    losses: list[PreservationLoss] = []

    for batch in state.merge_plan.phases:
        if batch.change_category != FileChangeCategory.C:
            continue

        for fp in batch.file_paths:
            existing = state.file_decision_records.get(fp)
            if (
                existing is not None
                and existing.decision == MergeDecision.ESCALATE_HUMAN
            ):
                continue

            fd = fork_diffs.get(fp)
            if fd is None:
                continue
            fork_lines = fd.lines_added + fd.lines_deleted
            if fork_lines < min_fork_lines:
                continue

            upstream_sha = git_tool.get_file_hash(upstream_ref, fp)
            worktree_sha = git_tool.get_worktree_blob_sha(fp)
            if upstream_sha is None or worktree_sha is None:
                continue
            if worktree_sha != upstream_sha:
                continue

            decision = (
                existing.decision if existing is not None else MergeDecision.TAKE_TARGET
            )
            losses.append(
                PreservationLoss(
                    file_path=fp,
                    fork_lines_changed=fork_lines,
                    decision=decision,
                    reason=(
                        f"fork had {fork_lines} lines of delta vs merge_base "
                        f"but worktree byte-equals upstream — fork content "
                        f"likely silently dropped during {decision.value}."
                    ),
                )
            )

    if losses:
        logger.warning(
            "P1-1 preservation audit: %d C-class file(s) appear to have "
            "lost fork content (worktree==upstream). First 5: %s",
            len(losses),
            [loss.file_path for loss in losses[:5]],
        )
    return losses
