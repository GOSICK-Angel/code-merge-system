"""Shared O-L5 dispatcher for ``UserDecisionItem.user_choice`` → worktree writes.

Originally lived inline in ``auto_merge.py`` (O-L5). Extracted so the
``human_review`` phase can actualize ``pending_user_decisions`` produced by
``_surface_internal_escalations`` (方案 6 part1) — without this, surfaced
items with ``user_choice=take_target`` reached AWAITING_HUMAN, got a user
answer, but never wrote ``state.file_decision_records`` (the run dropped them
via the report-phase DROPPED backstop). Now both call sites share one
truth.

Behavior per choice:

* ``skip``               — record SKIP/HUMAN, no worktree write.
* ``manual_paste``       — write ``item.manual_resolution`` verbatim; if
                           missing, leave ESCALATE_HUMAN/HUMAN.
* ``union_additions``    — ``git merge-file --union`` from
                           ``state.merge_base_commit``; SEMANTIC_MERGE/HUMAN.
* ``take_target``        — write upstream content (bytes for binary).
* ``take_current``       — write fork content (bytes for binary).

The dispatcher mutates ``state.file_decision_records`` and returns the set of
file paths whose record it (re)wrote. Errors fall through to an
ESCALATE_HUMAN record so the report-phase DROPPED guard catches dispatch
failures rather than silently committing fork content.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.models.decision import DecisionSource, FileDecisionRecord, MergeDecision
from src.models.diff import FileStatus
from src.models.plan_review import UserDecisionItem
from src.models.state import MergeState
from src.tools.binary_assets import is_binary_asset

if TYPE_CHECKING:
    from src.tools.git_tool import GitTool

logger = logging.getLogger(__name__)


_TAKE_TARGET_KEYS = frozenset({"take_target"})
_TAKE_CURRENT_KEYS = frozenset({"take_current", "keep_head"})
_UNION_KEYS = frozenset({"union_additions"})
_MANUAL_PASTE_KEYS = frozenset({"manual_paste"})
_SKIP_KEYS = frozenset({"skip"})
_ACTIONABLE_KEYS = (
    _TAKE_TARGET_KEYS
    | _TAKE_CURRENT_KEYS
    | _UNION_KEYS
    | _MANUAL_PASTE_KEYS
    | _SKIP_KEYS
)


async def dispatch_user_choice(
    state: MergeState,
    git_tool: "GitTool",
    items: list[UserDecisionItem],
    *,
    phase: str,
    decision_source: DecisionSource = DecisionSource.HUMAN,
) -> set[str]:
    """Actualize ``user_choice`` for each item; return applied file paths.

    ``phase`` is recorded on every FileDecisionRecord (``auto_merge`` for the
    O-L5 call site, ``human_review`` for the surfaced-item call site).
    ``decision_source`` defaults to ``HUMAN`` — both call sites are
    user-decided. The O-L5 call site preserves AUTO_EXECUTOR for the
    exception branch (post-write apply failure) and that branch sets it
    explicitly inside this function.
    """
    applied: set[str] = set()
    for item in items:
        choice = item.user_choice
        if choice not in _ACTIONABLE_KEYS:
            continue
        fp = item.file_path
        if fp in applied:
            continue

        if choice in _SKIP_KEYS:
            state.file_decision_records[fp] = FileDecisionRecord(
                file_path=fp,
                file_status=FileStatus.MODIFIED,
                decision=MergeDecision.SKIP,
                decision_source=decision_source,
                confidence=1.0,
                rationale=(
                    "O-L5 skip: reviewer deferred this file to a "
                    "follow-up PR — fork content preserved untouched, "
                    "no working-tree write."
                ),
                phase=phase,
                agent="user_choice_executor",
            )
            applied.add(fp)
            continue

        if choice in _MANUAL_PASTE_KEYS:
            manual_content = item.manual_resolution
            if not manual_content:
                logger.warning(
                    "O-L5 manual_paste: %s selected manual_paste but "
                    "no manual_resolution provided; keeping ESCALATE_HUMAN",
                    fp,
                )
                state.file_decision_records[fp] = FileDecisionRecord(
                    file_path=fp,
                    file_status=FileStatus.MODIFIED,
                    decision=MergeDecision.ESCALATE_HUMAN,
                    decision_source=decision_source,
                    confidence=0.0,
                    rationale=(
                        "O-L5 manual_paste selected without "
                        "manual_resolution content; keeping ESCALATE_HUMAN."
                    ),
                    phase=phase,
                    agent="user_choice_executor",
                )
                applied.add(fp)
                continue
            try:
                from src.tools.patch_applier import apply_with_snapshot

                record = await apply_with_snapshot(
                    fp,
                    manual_content,
                    git_tool,
                    state,
                    phase=phase,
                    agent="user_choice_executor",
                    decision=MergeDecision.MANUAL_PATCH,
                    rationale=(
                        "O-L5 manual_paste: reviewer supplied the "
                        "resolved file content verbatim — written "
                        "bypassing LLM merge and 3-way merge."
                    ),
                )
            except Exception as exc:
                logger.warning("O-L5 manual_paste: failed to write %s: %s", fp, exc)
                record = FileDecisionRecord(
                    file_path=fp,
                    file_status=FileStatus.MODIFIED,
                    decision=MergeDecision.ESCALATE_HUMAN,
                    decision_source=decision_source,
                    confidence=0.0,
                    rationale=(
                        f"O-L5 manual_paste apply failed ({exc!r}); "
                        "keeping ESCALATE_HUMAN."
                    ),
                    phase=phase,
                    agent="user_choice_executor",
                )
            state.file_decision_records[fp] = record
            applied.add(fp)
            continue

        if choice in _UNION_KEYS:
            base_ref = state.merge_base_commit or ""
            union_content = (
                git_tool.three_way_merge_file_union(
                    base_ref,
                    state.config.fork_ref,
                    state.config.upstream_ref,
                    fp,
                )
                if base_ref
                else None
            )
            if union_content is None:
                logger.warning(
                    "O-L5 union_additions: cannot compute union for %s "
                    "(missing base_ref or input refs); leaving ESCALATE_HUMAN",
                    fp,
                )
                state.file_decision_records[fp] = FileDecisionRecord(
                    file_path=fp,
                    file_status=FileStatus.MODIFIED,
                    decision=MergeDecision.ESCALATE_HUMAN,
                    decision_source=decision_source,
                    confidence=0.0,
                    rationale=(
                        "O-L5 union_additions: git merge-file --union "
                        "returned None (missing base or invalid input); "
                        "keeping ESCALATE_HUMAN."
                    ),
                    phase=phase,
                    agent="user_choice_executor",
                )
                applied.add(fp)
                continue
            try:
                from src.tools.patch_applier import apply_with_snapshot

                record = await apply_with_snapshot(
                    fp,
                    union_content,
                    git_tool,
                    state,
                    phase=phase,
                    agent="user_choice_executor",
                    decision=MergeDecision.SEMANTIC_MERGE,
                    rationale=(
                        "O-L5 union_additions: applied git merge-file --union "
                        "per user choice — keeps additions from both fork "
                        "and upstream sides."
                    ),
                )
            except Exception as exc:
                logger.warning(
                    "O-L5 union_additions: failed to apply union for %s: %s",
                    fp,
                    exc,
                )
                record = FileDecisionRecord(
                    file_path=fp,
                    file_status=FileStatus.MODIFIED,
                    decision=MergeDecision.ESCALATE_HUMAN,
                    decision_source=decision_source,
                    confidence=0.0,
                    rationale=(
                        f"O-L5 union_additions apply failed ({exc!r}); "
                        "keeping ESCALATE_HUMAN."
                    ),
                    phase=phase,
                    agent="user_choice_executor",
                )
            state.file_decision_records[fp] = record
            applied.add(fp)
            continue

        ref = (
            state.config.upstream_ref
            if choice in _TAKE_TARGET_KEYS
            else state.config.fork_ref
        )
        decision_value = (
            MergeDecision.TAKE_TARGET
            if choice in _TAKE_TARGET_KEYS
            else MergeDecision.TAKE_CURRENT
        )
        try:
            if is_binary_asset(fp):
                from src.tools.patch_applier import apply_bytes_with_snapshot

                content_bytes = git_tool.get_file_bytes(ref, fp)
                if content_bytes is None:
                    raise RuntimeError(f"{ref}:{fp} bytes not found")
                record = await apply_bytes_with_snapshot(
                    fp,
                    content_bytes,
                    git_tool,
                    state,
                    phase=phase,
                    agent="user_choice_executor",
                    decision=decision_value,
                    rationale=(
                        f"O-L5: executing user_choice={item.user_choice!r} "
                        f"for {item.risk_context or item.item_id} via "
                        "binary-safe path"
                    ),
                )
            else:
                from src.tools.patch_applier import apply_with_snapshot

                content = git_tool.get_file_content(ref, fp)
                if content is None:
                    raise RuntimeError(f"{ref}:{fp} content not found")
                record = await apply_with_snapshot(
                    fp,
                    content,
                    git_tool,
                    state,
                    phase=phase,
                    agent="user_choice_executor",
                    decision=decision_value,
                    rationale=(
                        f"O-L5: executing user_choice={item.user_choice!r} "
                        f"for {item.risk_context or item.item_id}"
                    ),
                )
        except Exception as exc:
            logger.warning(
                "O-L5: failed to execute user_choice=%s for %s: %s",
                item.user_choice,
                fp,
                exc,
            )
            record = FileDecisionRecord(
                file_path=fp,
                file_status=FileStatus.MODIFIED,
                decision=MergeDecision.ESCALATE_HUMAN,
                decision_source=DecisionSource.AUTO_EXECUTOR,
                confidence=0.0,
                rationale=(
                    f"O-L5 execute user_choice={item.user_choice!r} failed "
                    f"({exc!r}); keeping ESCALATE_HUMAN."
                ),
                phase=phase,
                agent="user_choice_executor",
            )
        state.file_decision_records[fp] = record
        applied.add(fp)

    return applied
