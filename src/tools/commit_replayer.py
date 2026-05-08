from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.models.diff import FileChangeCategory
from src.models.state import MergeState
from src.tools.git_tool import GitTool

logger = logging.getLogger(__name__)

REPLAYABLE_CATEGORIES = frozenset({FileChangeCategory.B, FileChangeCategory.D_MISSING})

# O-R4: stop attempting partial cherry-picks after this many consecutive
# failures. On heavily diverged forks every per-file cherry-pick fails
# (paths no longer legal vs commit's parent tree) and each abort costs
# real wall-clock time. Bail fast and let the apply path handle the rest.
_PARTIAL_REPLAY_FAILURE_BAIL = 3


@dataclass
class ReplayResult:
    replayed_shas: list[str] = field(default_factory=list)
    failed_shas: list[str] = field(default_factory=list)
    replayed_files: list[str] = field(default_factory=list)
    partial_replays: list[dict[str, Any]] = field(default_factory=list)
    strategy_used: dict[str, str] = field(default_factory=dict)


class CommitReplayer:
    def classify_commits(
        self,
        commits: list[dict[str, Any]],
        file_categories: dict[str, FileChangeCategory],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        replayable: list[dict[str, Any]] = []
        non_replayable: list[dict[str, Any]] = []

        for commit in commits:
            files: list[str] = commit.get("files", [])
            if not files:
                non_replayable.append(commit)
                continue

            all_clean = all(
                file_categories.get(f) in REPLAYABLE_CATEGORIES for f in files
            )
            if all_clean:
                replayable.append(commit)
            else:
                non_replayable.append(commit)

        return replayable, non_replayable

    def classify_commits_with_partial(
        self,
        commits: list[dict[str, Any]],
        file_categories: dict[str, FileChangeCategory],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """O-R1: 3-way split — fully replayable, partially replayable, unreplayable.

        Partially replayable commits have ≥1 replayable file AND ≥1
        non-replayable file; caller may cherry-pick just the replayable subset
        and fall back to apply for the rest.
        """
        fully: list[dict[str, Any]] = []
        partial: list[dict[str, Any]] = []
        none: list[dict[str, Any]] = []
        for commit in commits:
            files: list[str] = commit.get("files", [])
            if not files:
                none.append(commit)
                continue
            clean = [
                f for f in files if file_categories.get(f) in REPLAYABLE_CATEGORIES
            ]
            dirty = [
                f for f in files if file_categories.get(f) not in REPLAYABLE_CATEGORIES
            ]
            if clean and not dirty:
                fully.append(commit)
            elif clean and dirty:
                enriched = dict(commit)
                enriched["_replay_files"] = clean
                enriched["_fallback_files"] = dirty
                partial.append(enriched)
            else:
                none.append(commit)
        return fully, partial, none

    async def replay_clean_commits(
        self,
        git_tool: GitTool,
        replayable: list[dict[str, Any]],
        state: MergeState,
    ) -> ReplayResult:
        result = ReplayResult()

        for commit in replayable:
            sha: str = commit["sha"]
            before_sha = git_tool.get_head_sha()
            # O-R3: walk the strategy ladder instead of single default try.
            ok, strategy = git_tool.cherry_pick_strategy_ladder(sha)
            if ok:
                after_sha = git_tool.get_head_sha()
                # O-B5: track files that *actually* changed in the worktree
                # rather than commit.files. Strategy options like `-X theirs`
                # may resolve some files to HEAD content (no change), and
                # empty cherry-picks may produce no new commit at all.
                # Marking those files "replayed" hides them from the
                # downstream take_target path and causes B-class drift that
                # only Judge can catch, at huge LLM cost.
                actual_files = git_tool.diff_files_between(before_sha, after_sha)
                result.replayed_shas.append(sha)
                result.replayed_files.extend(actual_files)
                result.strategy_used[sha] = strategy
                logger.info(
                    "Cherry-picked %s via %s: %s (%d files actually changed, "
                    "%d in commit)",
                    sha[:8],
                    strategy,
                    commit.get("message", ""),
                    len(actual_files),
                    len(commit.get("files", [])),
                )
            else:
                git_tool.cherry_pick_abort()
                result.failed_shas.append(sha)
                logger.warning(
                    "Cherry-pick failed for %s after strategy ladder "
                    "(last=%s): %s — will fall back to apply",
                    sha[:8],
                    strategy,
                    commit.get("message", ""),
                )

        state.replayed_commits = list(result.replayed_shas)
        state.replayed_files = list(result.replayed_files)
        return result

    async def replay_partial_commits(
        self,
        git_tool: GitTool,
        partial_commits: list[dict[str, Any]],
        result: ReplayResult,
    ) -> None:
        """O-R1: per-file cherry-pick for mixed commits.

        For each partial commit, apply the replayable subset via
        ``cherry_pick_per_file`` and commit with the original author and
        message (prefixed to mark it as a partial replay). Non-replayable
        files from the same commit are expected to be handled by the regular
        apply pipeline.
        """
        consecutive_failures = 0
        bailed = False
        for commit in partial_commits:
            sha: str = commit["sha"]
            keep_files: list[str] = list(commit.get("_replay_files", []))
            if not keep_files:
                continue
            if bailed:
                logger.info(
                    "Partial cherry-pick skipped for %s (bail-out active) — "
                    "all files fall back to apply",
                    sha[:8],
                )
                continue
            ok, applied = git_tool.cherry_pick_per_file(sha, keep_files)
            if not ok or not applied:
                logger.warning(
                    "Partial cherry-pick failed for %s (keep=%d) — "
                    "all files fall back to apply",
                    sha[:8],
                    len(keep_files),
                )
                git_tool.cherry_pick_abort()
                consecutive_failures += 1
                if consecutive_failures >= _PARTIAL_REPLAY_FAILURE_BAIL:
                    logger.warning(
                        "O-R4: %d consecutive partial cherry-pick failures — "
                        "bailing out, remaining %d commits will skip "
                        "per-file cherry-pick and go straight to apply",
                        consecutive_failures,
                        max(
                            0, len(partial_commits) - partial_commits.index(commit) - 1
                        ),
                    )
                    bailed = True
                continue
            consecutive_failures = 0
            author_name, author_email, message = git_tool.get_commit_author_and_message(
                sha
            )
            commit_msg = (
                f"{message}\n\n"
                f"(partial cherry-pick from {sha[:8]}; "
                f"applied {len(applied)} file(s), "
                f"{len(commit.get('_fallback_files', []))} file(s) fell back to apply)"
            )
            new_sha = git_tool.commit_with_author(
                commit_msg, author_name or "merger", author_email or "merger@local"
            )
            if not new_sha:
                logger.warning(
                    "Partial cherry-pick of %s staged but commit failed", sha[:8]
                )
                continue
            result.replayed_shas.append(sha)
            result.replayed_files.extend(applied)
            result.strategy_used[sha] = "per-file"
            result.partial_replays.append(
                {
                    "original_sha": sha,
                    "new_sha": new_sha,
                    "applied_files": applied,
                    "fallback_files": list(commit.get("_fallback_files", [])),
                }
            )
            logger.info(
                "Partial cherry-pick %s → %s (%d applied, %d fallback)",
                sha[:8],
                new_sha[:8],
                len(applied),
                len(commit.get("_fallback_files", [])),
            )

    def collect_upstream_messages(
        self,
        git_tool: GitTool,
        merge_base: str,
        upstream_ref: str,
        files: list[str],
    ) -> str:
        seen: set[str] = set()
        lines: list[str] = []
        for fp in files:
            msgs = git_tool.get_commit_messages(fp, upstream_ref, limit=5)
            for msg in msgs:
                if msg not in seen:
                    seen.add(msg)
                    lines.append(f"- {msg}")
        return "\n".join(lines)
