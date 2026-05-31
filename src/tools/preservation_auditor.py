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
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from src.models.decision import MergeDecision
from src.models.diff import FileChangeCategory
from src.models.state import MergeState
from src.tools.gate_skip import gate_skip_entry

logger = logging.getLogger(__name__)


DEFAULT_MIN_FORK_LINES: int = 50
SECURITY_MIN_FORK_LINES: int = 0
DEFAULT_FORK_SURVIVAL_FLOOR: float = 0.7
# A distinctive line must be substantive to count — pure punctuation / brackets
# (`}`, `});`, `]`) recur everywhere and would make the line-level signal noisy.
_MIN_DISTINCTIVE_LINES: int = 5


class _GitToolLike(Protocol):
    repo_path: Path

    def get_file_hash(self, ref: str, file_path: str) -> str | None: ...
    def get_worktree_blob_sha(self, file_path: str) -> str | None: ...
    def get_file_content(self, ref: str, file_path: str) -> str | None: ...


class PreservationLoss(BaseModel):
    file_path: str
    fork_lines_changed: int
    decision: MergeDecision
    reason: str


def _substantive(line: str) -> bool:
    stripped = line.strip()
    return len(stripped) >= 6 and any(ch.isalnum() for ch in stripped)


def fork_distinctive_lines(base: str, fork: str, upstream: str) -> set[str]:
    """Substantive lines present in *fork* but in neither *base* nor *upstream*.

    These are the fork's own additions that upstream never independently
    introduced — content that has no legitimate reason to vanish from a faithful
    merge. Comparison is whitespace-insensitive per line and set-based, so
    re-indentation and reordering do not register as loss.
    """
    fork_l = {ln.strip() for ln in fork.splitlines() if _substantive(ln)}
    base_l = {ln.strip() for ln in base.splitlines() if _substantive(ln)}
    up_l = {ln.strip() for ln in upstream.splitlines() if _substantive(ln)}
    return fork_l - base_l - up_l


def fork_survival_shortfall(
    base: str, fork: str, upstream: str, merged: str
) -> tuple[int, int]:
    """Return ``(dropped, distinctive_total)`` — how many of the fork's
    distinctive lines are absent from the merged worktree. ``(0, 0)`` when
    there are too few distinctive lines to judge.
    """
    distinctive = fork_distinctive_lines(base, fork, upstream)
    if len(distinctive) < _MIN_DISTINCTIVE_LINES:
        return 0, 0
    merged_l = {ln.strip() for ln in merged.splitlines()}
    dropped = sum(1 for d in distinctive if d not in merged_l)
    return dropped, len(distinctive)


def _safe_content(git_tool: _GitToolLike, ref: str, fp: str) -> str | None:
    try:
        return git_tool.get_file_content(ref, fp)
    except Exception:
        return None


def _read_worktree(git_tool: _GitToolLike, fp: str) -> str | None:
    try:
        abs_path = git_tool.repo_path / fp
        if not abs_path.exists():
            return None
        return abs_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def audit_fork_preservation(
    state: MergeState,
    git_tool: _GitToolLike,
    *,
    min_fork_lines: int | None = None,
    survival_floor: float | None = None,
) -> list[PreservationLoss]:
    """Return one ``PreservationLoss`` per C-class file that likely lost fork
    content, via two complementary signals:

    1. **Wholesale drop** — worktree blob byte-equals upstream despite a
       material fork delta vs merge_base (the original P1-1 check).
    2. **Partial drop** (#11) — worktree is NOT byte-equal to upstream, but at
       least ``survival_floor`` of the fork's DISTINCTIVE lines (present in fork
       but in neither merge_base nor upstream) are absent from the merge. Catches
       the partial / sub-floor loss the byte-equality check misses entirely.

    Audits ``original_file_paths`` (#11) so native-3way files already drained
    from ``batch.file_paths`` are still checked — they are the highest-risk
    deterministic blends and were previously invisible to this gate.

    Per-file materiality floor is ``min_fork_lines`` (config:
    ``thresholds.preservation_min_fork_lines``), forced to 0 for
    security-sensitive files so even a one-line fork customization is audited.

    Read-only: returns findings; the caller (auto_merge) routes them to conflict
    analysis or human. Never a hard veto — a false positive costs one re-analysis.

    Skips files already at ``ESCALATE_HUMAN`` and files where a needed blob is
    unresolvable.
    """
    if state.merge_plan is None:
        return []

    if min_fork_lines is None:
        min_fork_lines = (
            getattr(state.config.thresholds, "preservation_min_fork_lines", None)
            or DEFAULT_MIN_FORK_LINES
        )
    if survival_floor is None:
        survival_floor = (
            getattr(state.config.thresholds, "preservation_fork_survival_floor", None)
            or DEFAULT_FORK_SURVIVAL_FLOOR
        )

    upstream_ref = state.config.upstream_ref
    fork_ref = state.config.fork_ref
    merge_base = state.merge_base_commit or ""
    fork_diffs = {fd.file_path: fd for fd in state.file_diffs}
    losses: list[PreservationLoss] = []
    seen: set[str] = set()

    for batch in state.merge_plan.phases:
        if batch.change_category != FileChangeCategory.C:
            continue

        for fp in batch.original_file_paths or batch.file_paths:
            if fp in seen:
                continue
            seen.add(fp)
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
            threshold = (
                SECURITY_MIN_FORK_LINES if fd.is_security_sensitive else min_fork_lines
            )
            if fork_lines < threshold:
                continue

            upstream_sha = git_tool.get_file_hash(upstream_ref, fp)
            worktree_sha = git_tool.get_worktree_blob_sha(fp)
            if upstream_sha is None or worktree_sha is None:
                # P1: this C-class file (material fork delta) should resolve on
                # both upstream and worktree; an unreadable blob silently skips
                # the whole preservation check for it. Record so a systemic git
                # failure surfaces as partial_failure rather than a clean pass.
                state.errors.append(
                    gate_skip_entry(
                        "preservation_audit",
                        fp,
                        "upstream/worktree blob unreadable "
                        "(git read failed or file absent at ref)",
                    )
                )
                continue

            decision = (
                existing.decision if existing is not None else MergeDecision.TAKE_TARGET
            )

            if worktree_sha == upstream_sha:
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
                continue

            # Partial-drop line-level check — only on files that survived the
            # byte-equality test. Best-effort: any missing blob skips the file.
            base_content = (
                _safe_content(git_tool, merge_base, fp) if merge_base else None
            )
            fork_content = _safe_content(git_tool, fork_ref, fp)
            upstream_content = _safe_content(git_tool, upstream_ref, fp)
            merged_content = _read_worktree(git_tool, fp)
            if (
                base_content is None
                or fork_content is None
                or upstream_content is None
                or merged_content is None
            ):
                # P1: worktree != upstream confirmed (both blobs read OK), so a
                # None content read here is a genuine read failure that silently
                # disabled the line-level partial-drop check for this file.
                state.errors.append(
                    gate_skip_entry(
                        "preservation_line_check",
                        fp,
                        "base/fork/upstream/merged content unreadable — "
                        "line-level fork-survival check skipped",
                    )
                )
                continue
            dropped, total = fork_survival_shortfall(
                base_content, fork_content, upstream_content, merged_content
            )
            if total and dropped / total >= survival_floor:
                losses.append(
                    PreservationLoss(
                        file_path=fp,
                        fork_lines_changed=fork_lines,
                        decision=decision,
                        reason=(
                            f"{dropped}/{total} fork-distinctive lines absent from "
                            f"the merge (>= {survival_floor:.0%} floor) — partial "
                            f"fork-content loss during {decision.value}."
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
