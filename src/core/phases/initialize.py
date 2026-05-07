from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from src.core.phases.base import Phase, PhaseContext, PhaseOutcome
from src.models.decision import (
    DecisionSource,
    FileDecisionRecord,
    MergeDecision,
)
from src.models.diff import (
    FileDiff,
    FileChangeCategory,
    ForkDivergence,
    FileStatus,
)
from src.models.state import MergeState, SystemStatus
from src.tools.diff_parser import build_file_diff, detect_language
from src.tools.file_classifier import (
    classify_all_files,
    classify_file,
    category_summary,
    compute_fork_divergence_map,
    compute_risk_score,
    is_security_sensitive,
    matches_any_pattern,
)
from src.tools.git_tool import GitTool
from src.tools.pollution_auditor import PollutionAuditor
from src.tools.scar_list_builder import ScarListBuilder
from src.tools.config_drift_detector import ConfigDriftDetector
from src.tools.commit_replayer import CommitReplayer
from src.tools.sync_point_detector import SyncPointDetector
from src.tools.interface_change_extractor import InterfaceChangeExtractor
from src.tools.reverse_impact_scanner import ReverseImpactScanner
from src.tools.forks_profile_loader import (
    ForksProfileError,
    find_migration_collision,
    find_removed_domain_match,
    find_rewritten_module_match,
    load_forks_profile,
    summarize_for_log,
)
from src.tools.diff_stasher import stash_upstream_diff
from src.cli.paths import get_diff_stash_dir
from src.models.forks_profile import (
    MigrationCollisionAction,
    MigrationCollisionRule,
    RewriteMergePolicy,
)

logger = logging.getLogger(__name__)


_D_MISSING_PREVIEW_LINES = 200

_BINARY_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".bmp",
        ".webp",
        ".tiff",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".7z",
        ".rar",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".eot",
        ".mp3",
        ".mp4",
        ".wav",
        ".ogg",
        ".flac",
        ".avi",
        ".mov",
        ".so",
        ".dylib",
        ".dll",
        ".exe",
        ".class",
        ".jar",
        ".pyc",
        ".db",
        ".sqlite",
        ".sqlite3",
    }
)


def _normalize_text_eof(content: bytes, file_path: str) -> bytes:
    """Append a trailing LF when the blob is plain text and lacks one.

    Avoids introducing trailing-newline diff noise when force-taking upstream
    files via `git show` — most editors/tools save text files with a final LF
    and `git diff` flags missing-EOL as a change.

    Binary files are detected by extension allowlist plus a NUL-byte probe
    over the first 8 KiB; binary content is returned unchanged.
    """
    if not content:
        return content
    suffix = Path(file_path).suffix.lower()
    if suffix in _BINARY_EXTENSIONS:
        return content
    if b"\x00" in content[:8192]:
        return content
    if not content.endswith(b"\n"):
        return content + b"\n"
    return content


def _parse_file_status(status_char: str) -> FileStatus:
    mapping = {
        "A": FileStatus.ADDED,
        "M": FileStatus.MODIFIED,
        "D": FileStatus.DELETED,
        "R": FileStatus.RENAMED,
    }
    return mapping.get(status_char.upper(), FileStatus.MODIFIED)


def _count_diff_lines(
    git_tool: GitTool, base: str, head: str, file_path: str
) -> tuple[int, int]:
    """Return (added, deleted) line counts for `git diff base..head -- file`.

    Used to surface the upstream-side delta to the planner so a C-class file
    with a small fork delta but a large upstream refactor still gets routed
    to ConflictAnalyst instead of being silently overwritten.
    """
    raw = git_tool.get_unified_diff(base, head, file_path)
    if not raw:
        return 0, 0
    added = sum(
        1
        for line in raw.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    deleted = sum(
        1
        for line in raw.splitlines()
        if line.startswith("-") and not line.startswith("---")
    )
    return added, deleted


def _build_added_file_diff(git_tool: GitTool, ref: str, file_path: str) -> str:
    """Return a pseudo-unified-diff for a D_MISSING file (new in upstream_ref).

    Shows the first _D_MISSING_PREVIEW_LINES lines of the file with '+' prefix
    so the planner can assess content and risk without just seeing a path.
    """
    if Path(file_path).suffix.lower() in _BINARY_EXTENSIONS:
        return ""
    content: str | None = git_tool.get_file_content(ref, file_path)
    if not content:
        return ""
    lines = content.splitlines()
    truncated = len(lines) > _D_MISSING_PREVIEW_LINES
    preview = lines[:_D_MISSING_PREVIEW_LINES]
    diff_lines = [
        f"--- /dev/null",
        f"+++ b/{file_path}",
        f"@@ -0,0 +1,{len(preview)} @@",
    ]
    diff_lines.extend(f"+{line}" for line in preview)
    if truncated:
        diff_lines.append(
            f"\\ ... ({len(lines) - _D_MISSING_PREVIEW_LINES} more lines not shown)"
        )
    return "\n".join(diff_lines)


class InitializePhase(Phase):
    name = "initialize"

    async def execute(self, state: MergeState, ctx: PhaseContext) -> PhaseOutcome:
        await asyncio.to_thread(self._run_sync, state, ctx)
        ctx.state_machine.transition(
            state, SystemStatus.PLANNING, "initialization complete"
        )
        return PhaseOutcome(
            target_status=SystemStatus.PLANNING,
            reason="initialization complete",
            checkpoint_tag="after_init",
        )

    def _run_sync(self, state: MergeState, ctx: PhaseContext) -> None:
        self._resolve_project_context(state, ctx)
        self._check_untracked_files(state, ctx)
        ctx.notify("orchestrator", "Computing merge base")
        git_merge_base = ctx.git_tool.get_merge_base(
            state.config.upstream_ref, state.config.fork_ref
        )
        merge_base = git_merge_base

        migration_cfg = state.config.migration
        if migration_cfg.merge_base_override:
            merge_base = migration_cfg.merge_base_override
            logger.info("Using merge_base_override: %s", merge_base)
        elif migration_cfg.auto_detect_sync_point:
            ctx.notify("orchestrator", "Detecting migration sync-point")
            detector = SyncPointDetector(
                sync_ratio_threshold=migration_cfg.sync_detection_threshold,
                min_synced_files=migration_cfg.min_synced_files,
            )
            result = detector.detect(
                ctx.git_tool,
                merge_base,
                state.config.fork_ref,
                state.config.upstream_ref,
            )
            state.migration_info = result
            if result.detected:
                logger.info(
                    "Migration detected: %d/%d upstream-changed files synced "
                    "(%.0f%%), effective merge-base: %s",
                    result.synced_file_count,
                    result.upstream_changed_file_count,
                    result.sync_ratio * 100,
                    result.effective_merge_base,
                )
                merge_base = result.effective_merge_base

        state.merge_base_commit = merge_base

        if state.config.scar_learning.enabled:
            try:
                learned = ScarListBuilder().auto_learn(
                    repo_path=Path(state.config.repo_path).resolve(),
                    fork_ref=state.config.fork_ref,
                    base_ref=merge_base,
                    since=state.config.scar_learning.since,
                    grep_patterns=(state.config.scar_learning.grep_patterns or None),
                    existing=state.config.customizations,
                )
            except Exception as e:
                logger.warning("Scar auto-learn failed: %s", e)
                learned = []
            if learned and state.config.scar_learning.auto_append_to_customizations:
                state.config = state.config.model_copy(
                    update={
                        "customizations": (list(state.config.customizations) + learned)
                    }
                )
                logger.info(
                    "Scar auto-learn appended %d customization(s) "
                    "(restore + feature commits)",
                    len(learned),
                )

        ctx.notify("orchestrator", "Classifying files (three-way)")
        file_categories = classify_all_files(
            merge_base,
            state.config.fork_ref,
            state.config.upstream_ref,
            ctx.git_tool,
        )

        ctx.notify("orchestrator", f"Classified {len(file_categories)} files")
        auditor = PollutionAuditor(ctx.git_tool)
        pollution_report = auditor.audit(
            merge_base,
            state.config.fork_ref,
            state.config.upstream_ref,
            file_categories,
        )
        state.pollution_audit = pollution_report
        if pollution_report.has_pollution:
            logger.info(
                "Pollution audit: %d files reclassified from %d prior merge commits",
                pollution_report.reclassified_count,
                len(pollution_report.prior_merge_commits),
            )
            file_categories = auditor.apply_corrections(
                file_categories, pollution_report
            )

        state.file_categories = file_categories

        fork_div_map = compute_fork_divergence_map(
            merge_base,
            state.config.fork_ref,
            state.config.upstream_ref,
            ctx.git_tool,
        )
        state.fork_divergence_map = {fp: fd.value for fp, fd in fork_div_map.items()}
        logger.info(
            "Fork-divergence map: %d files (fork_modified=%d fork_deleted=%d "
            "fork_only=%d upstream_added=%d upstream_only_change=%d unchanged=%d)",
            len(fork_div_map),
            sum(1 for v in fork_div_map.values() if v == ForkDivergence.FORK_MODIFIED),
            sum(1 for v in fork_div_map.values() if v == ForkDivergence.FORK_DELETED),
            sum(1 for v in fork_div_map.values() if v == ForkDivergence.FORK_ONLY),
            sum(1 for v in fork_div_map.values() if v == ForkDivergence.UPSTREAM_ADDED),
            sum(
                1
                for v in fork_div_map.values()
                if v == ForkDivergence.UPSTREAM_ONLY_CHANGE
            ),
            sum(1 for v in fork_div_map.values() if v == ForkDivergence.UNCHANGED),
        )

        cat_counts = category_summary(file_categories)
        logger.info(
            "Three-way classification: A=%d B=%d C=%d D-missing=%d D-extra=%d E=%d",
            cat_counts.get("unchanged", 0),
            cat_counts.get("upstream_only", 0),
            cat_counts.get("both_changed", 0),
            cat_counts.get("upstream_new", 0),
            cat_counts.get("current_only", 0),
            cat_counts.get("current_only_change", 0),
        )

        actionable_categories = {
            FileChangeCategory.B,
            FileChangeCategory.C,
            FileChangeCategory.D_MISSING,
        }
        actionable_paths = {
            fp for fp, cat in file_categories.items() if cat in actionable_categories
        }

        profile_paths = self._apply_forks_profile_routing(state, ctx, file_categories)
        if profile_paths:
            actionable_paths -= profile_paths

        forced_paths = self._apply_forced_decisions(state, ctx, file_categories)
        if forced_paths:
            actionable_paths -= forced_paths
            ctx.notify(
                "orchestrator",
                f"Force-decision policy pre-resolved {len(forced_paths)} files; "
                f"{len(actionable_paths)} remain for AI flow",
            )

        self._seed_fork_profile_l0(state, ctx, file_categories)

        ctx.notify(
            "orchestrator",
            f"Building diffs for {len(actionable_paths)} actionable files",
        )
        changed_files = ctx.git_tool.get_changed_files(
            merge_base, state.config.fork_ref
        )
        file_diffs: list[FileDiff] = []

        changed_paths_map: dict[str, str] = {fp: sc for sc, fp in changed_files}

        for file_path in sorted(actionable_paths):
            status_char = changed_paths_map.get(file_path, "M")
            cat = file_categories[file_path]

            if cat == FileChangeCategory.D_MISSING:
                file_status = FileStatus.ADDED
                raw_diff = _build_added_file_diff(
                    ctx.git_tool, state.config.upstream_ref, file_path
                )
            else:
                raw_diff = ctx.git_tool.get_unified_diff(
                    merge_base, state.config.fork_ref, file_path
                )
                file_status = _parse_file_status(status_char)

            language = detect_language(file_path)
            fd = build_file_diff(file_path, raw_diff, file_status)
            sensitive = is_security_sensitive(file_path, state.config.file_classifier)

            upstream_added, upstream_deleted = _count_diff_lines(
                ctx.git_tool, merge_base, state.config.upstream_ref, file_path
            )

            fd = fd.model_copy(
                update={
                    "language": language,
                    "is_security_sensitive": sensitive,
                    "change_category": cat,
                    "upstream_lines_added": upstream_added,
                    "upstream_lines_deleted": upstream_deleted,
                }
            )
            score = compute_risk_score(fd, state.config.file_classifier)
            fd = fd.model_copy(update={"risk_score": score})
            risk_level = classify_file(fd, state.config.file_classifier)
            fd = fd.model_copy(update={"risk_level": risk_level})
            file_diffs.append(fd)

        state.file_diffs = file_diffs

        upstream_renames = ctx.git_tool.detect_renames(
            merge_base, state.config.upstream_ref
        )
        fork_renames = ctx.git_tool.detect_renames(merge_base, state.config.fork_ref)
        seen: set[tuple[str, str]] = set()
        rename_pairs: list[tuple[str, str]] = []
        for pair in upstream_renames + fork_renames:
            if pair not in seen:
                seen.add(pair)
                rename_pairs.append(pair)
        state.rename_pairs = rename_pairs
        if rename_pairs:
            ctx.notify(
                "orchestrator",
                f"Rename detection: {len(rename_pairs)} rename pair(s) found "
                f"(upstream={len(upstream_renames)}, fork={len(fork_renames)})",
            )

        if state.config.history.enabled:
            ctx.notify("orchestrator", "Enumerating upstream commits for replay")
            upstream_commits = ctx.git_tool.list_commits(
                merge_base, state.config.upstream_ref
            )
            replayer = CommitReplayer()
            fully, partial, none = replayer.classify_commits_with_partial(
                upstream_commits, file_categories
            )
            state.upstream_commits = upstream_commits
            state.replayable_commits = fully
            state.partial_replayable_commits = partial
            state.non_replayable_commits = none
            logger.info(
                "Commit replay classification: %d fully-replayable, "
                "%d partially-replayable, %d non-replayable "
                "out of %d total upstream commits (O-R1)",
                len(fully),
                len(partial),
                len(none),
                len(upstream_commits),
            )

        drift_detector = ConfigDriftDetector(Path(state.config.repo_path).resolve())
        env_files, docker_env_files = drift_detector.find_env_files()
        if env_files or docker_env_files:
            drift_report = drift_detector.detect_drift_from_files(
                env_files=env_files,
                docker_env_files=docker_env_files,
            )
            state.config_drifts = drift_report
            if drift_report.has_drifts:
                logger.info(
                    "Config drift detection: %d drifts found across %d keys",
                    drift_report.drift_count,
                    drift_report.total_keys_checked,
                )

        if state.config.reverse_impact.enabled:
            self._run_reverse_impact(state, ctx, merge_base)

    def _apply_forced_decisions(
        self,
        state: MergeState,
        ctx: PhaseContext,
        file_categories: dict[str, FileChangeCategory],
    ) -> set[str]:
        """Pre-decide files matching always_take_upstream/current_patterns
        before they enter the AI flow. Returns the set of paths consumed.

        always_take_upstream_patterns -> MergeDecision.TAKE_TARGET (file
        content is checked-out from upstream_ref into the working tree).
        always_take_current_patterns  -> MergeDecision.TAKE_CURRENT (no
        write needed; D_MISSING paths stay absent).
        Upstream wins on overlap: a path matching both lists is forced
        to TAKE_TARGET (more explicit "must come from upstream").
        """
        fc = state.config.file_classifier
        upstream_patterns = list(fc.always_take_upstream_patterns)
        # Legacy alias kept functional: always_take_target_patterns shares
        # the same semantic ("force take upstream") and is honored here so
        # both names work.
        upstream_patterns += list(fc.always_take_target_patterns)
        current_patterns = list(fc.always_take_current_patterns)

        if not upstream_patterns and not current_patterns:
            return set()

        forced_target: list[tuple[str, FileChangeCategory]] = []
        forced_current: list[tuple[str, FileChangeCategory]] = []
        for fp, cat in file_categories.items():
            if fp in state.file_decision_records:
                # forks-profile routing already pre-decided this file;
                # never overwrite a higher-priority decision.
                continue
            if upstream_patterns and matches_any_pattern(fp, upstream_patterns):
                forced_target.append((fp, cat))
                continue
            if current_patterns and matches_any_pattern(fp, current_patterns):
                forced_current.append((fp, cat))

        consumed: set[str] = set()

        for fp, cat in forced_target:
            self._force_take_target(state, ctx, fp, cat)
            consumed.add(fp)

        for fp, cat in forced_current:
            self._force_take_current(state, fp, cat)
            consumed.add(fp)

        if forced_target or forced_current:
            logger.info(
                "Force-decision policy: %d TAKE_TARGET, %d TAKE_CURRENT "
                "pre-resolved before AI flow",
                len(forced_target),
                len(forced_current),
            )
        return consumed

    def _force_take_target(
        self,
        state: MergeState,
        ctx: PhaseContext,
        file_path: str,
        category: FileChangeCategory,
    ) -> None:
        upstream_ref = state.config.upstream_ref
        repo_root = Path(state.config.repo_path).resolve()
        target_path = repo_root / file_path
        write_status = "kept_present"
        try:
            content = ctx.git_tool.get_file_bytes(upstream_ref, file_path)
            if content is None:
                if target_path.exists():
                    target_path.unlink()
                    write_status = "deleted"
                else:
                    write_status = "absent_noop"
            else:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                normalized = _normalize_text_eof(content, file_path)
                target_path.write_bytes(normalized)
                write_status = "written_from_upstream"
        except Exception as e:
            logger.error("Force TAKE_TARGET write failed for %s: %s", file_path, e)
            raise

        file_status = (
            FileStatus.ADDED
            if category == FileChangeCategory.D_MISSING
            else FileStatus.MODIFIED
        )
        state.file_decision_records[file_path] = FileDecisionRecord(
            file_path=file_path,
            file_status=file_status,
            decision=MergeDecision.TAKE_TARGET,
            decision_source=DecisionSource.AUTO_PLANNER,
            confidence=1.0,
            rationale=(
                f"matched always_take_upstream_patterns "
                f"(category={category.value}, write={write_status})"
            ),
            phase="initialize",
            agent="force_decision_policy",
        )

    def _seed_fork_profile_l0(
        self,
        state: MergeState,
        ctx: PhaseContext,
        file_categories: dict[str, FileChangeCategory],
    ) -> None:
        """P1-1: write fork-pinned 'deleted features' summary into L0.

        Aggregates files matching ``always_take_current_patterns`` and
        notes how many fall into each category (D_MISSING = fork removed
        the file, B/C = fork retains its own version). Subsequent phases
        see this via ``LayeredMemoryLoader._build_l0`` so judge / analyst
        can reason about "fork = upstream minus these features" without
        re-deriving it from scratch.
        """
        fc = state.config.file_classifier
        current_patterns = list(fc.always_take_current_patterns)
        if not current_patterns:
            return

        deleted: list[str] = []
        retained: list[str] = []
        for fp, cat in file_categories.items():
            if not matches_any_pattern(fp, current_patterns):
                continue
            if cat == FileChangeCategory.D_MISSING:
                deleted.append(fp)
            else:
                retained.append(fp)

        if not deleted and not retained:
            return

        parts: list[str] = []
        if deleted:
            sample = ", ".join(sorted(deleted)[:8])
            more = f" (+{len(deleted) - 8} more)" if len(deleted) > 8 else ""
            parts.append(
                f"Fork explicitly removed {len(deleted)} upstream file(s) "
                f"(D_MISSING + always_take_current): {sample}{more}"
            )
        if retained:
            sample = ", ".join(sorted(retained)[:8])
            more = f" (+{len(retained) - 8} more)" if len(retained) > 8 else ""
            parts.append(
                f"Fork pins {len(retained)} customised file(s) against "
                f"upstream changes: {sample}{more}"
            )
        parts.append(
            "Treat divergence on these paths as expected, not as a merge regression."
        )

        try:
            ctx.memory_store.update_codebase_profile_inplace(
                "fork_deleted_features",
                " ".join(parts),
            )
        except AttributeError:
            logger.debug(
                "memory_store does not support update_codebase_profile_inplace; "
                "skipping fork-profile L0 seed"
            )

    def _force_take_current(
        self,
        state: MergeState,
        file_path: str,
        category: FileChangeCategory,
    ) -> None:
        # Working tree is already at fork_ref content; D_MISSING paths
        # are naturally absent. No file I/O needed.
        if category == FileChangeCategory.D_MISSING:
            file_status = FileStatus.DELETED
        elif category == FileChangeCategory.D_EXTRA:
            file_status = FileStatus.ADDED
        else:
            file_status = FileStatus.MODIFIED
        state.file_decision_records[file_path] = FileDecisionRecord(
            file_path=file_path,
            file_status=file_status,
            decision=MergeDecision.TAKE_CURRENT,
            decision_source=DecisionSource.AUTO_PLANNER,
            confidence=1.0,
            rationale=(
                f"matched always_take_current_patterns "
                f"(category={category.value}, no write needed)"
            ),
            phase="initialize",
            agent="force_decision_policy",
        )

    def _apply_forks_profile_routing(
        self,
        state: MergeState,
        ctx: PhaseContext,
        file_categories: dict[str, FileChangeCategory],
    ) -> set[str]:
        """Apply `.merge/forks-profile.yaml` routing before the AI flow.

        Priority is higher than `always_take_*_patterns`: a path matching
        both is decided here and skipped by `_apply_forced_decisions`.

        Match precedence per file:
          1. rewritten_modules — explicit per-module policy wins.
          2. removed_domains   — bulk "fork dropped this area" → TAKE_CURRENT.

        Returns the set of paths whose decision was recorded.
        """
        repo_path = state.config.repo_path
        try:
            profile = load_forks_profile(repo_path)
        except ForksProfileError as e:
            logger.error("forks-profile load failed: %s", e)
            ctx.notify(
                "orchestrator",
                f"⚠ forks-profile.yaml present but invalid — skipping routing: {e}",
            )
            return set()

        state.forks_profile = profile
        if profile is None or profile.is_empty():
            return set()

        ctx.notify("orchestrator", summarize_for_log(profile))

        consumed: set[str] = set()
        rewritten_counts: dict[str, int] = {}
        removed_count = 0
        migration_collisions: list[tuple[str, int]] = []

        for fp, cat in file_categories.items():
            module = find_rewritten_module_match(profile, fp)
            if module is not None:
                if self._route_rewritten_module(
                    state, ctx, fp, cat, module.path, module.policy, module.note
                ):
                    consumed.add(fp)
                    rewritten_counts[module.policy.value] = (
                        rewritten_counts.get(module.policy.value, 0) + 1
                    )
                continue

            domain = find_removed_domain_match(profile, fp)
            if domain is not None:
                self._record_profile_take_current(
                    state,
                    fp,
                    cat,
                    rationale=(
                        f"forks-profile.removed_domains[{domain.name}] "
                        f"(category={cat.value}, reason={domain.reason or 'n/a'})"
                    ),
                )
                consumed.add(fp)
                removed_count += 1
                continue

            # Migration collision detection runs only on D_MISSING entries
            # (upstream-introduced files). Fork-side migrations are by
            # definition outside upstream's reach and don't collide.
            if cat != FileChangeCategory.D_MISSING:
                continue
            collision = find_migration_collision(profile, fp)
            if collision is None:
                continue
            number, rule = collision
            self._route_migration_collision(state, fp, cat, number, rule)
            consumed.add(fp)
            migration_collisions.append((fp, number))

        if consumed or rewritten_counts or migration_collisions:
            logger.info(
                "forks-profile routing: %d removed_domains, %s rewritten_modules, "
                "%d migration collisions pre-resolved before AI flow",
                removed_count,
                rewritten_counts or "{}",
                len(migration_collisions),
            )
            if migration_collisions:
                preview = ", ".join(f"{fp}#{n}" for fp, n in migration_collisions[:5])
                more = (
                    f" (+{len(migration_collisions) - 5} more)"
                    if len(migration_collisions) > 5
                    else ""
                )
                ctx.notify(
                    "orchestrator",
                    f"forks-profile: {len(migration_collisions)} migration "
                    f"collision(s) detected: {preview}{more}",
                )
        return consumed

    def _route_rewritten_module(
        self,
        state: MergeState,
        ctx: PhaseContext,
        file_path: str,
        category: FileChangeCategory,
        module_path: str,
        policy: RewriteMergePolicy,
        note: str,
    ) -> bool:
        """Apply a single rewritten_modules entry. Returns True if the file
        was force-decided (and should be removed from actionable_paths).

        `semantic_merge_with_alert` deliberately does NOT force a decision —
        the file flows through the normal AI path with an advisory log so
        ConflictAnalyst can pick up the alert via state.forks_profile.
        """
        if policy == RewriteMergePolicy.SEMANTIC_MERGE_WITH_ALERT:
            logger.warning(
                "forks-profile rewritten_module[%s] policy=semantic_merge_with_alert "
                "for %s — flagged for elevated analyst review (note=%s)",
                module_path,
                file_path,
                note or "n/a",
            )
            return False

        if policy == RewriteMergePolicy.ESCALATE_HUMAN:
            self._record_profile_escalate_human(
                state,
                file_path,
                category,
                rationale=(
                    f"forks-profile.rewritten_modules[{module_path}] "
                    f"policy=escalate_human (category={category.value}, "
                    f"note={note or 'n/a'})"
                ),
            )
            return True

        if policy == RewriteMergePolicy.TAKE_CURRENT_WITH_DIFF_NOTE:
            stash_note = self._stash_upstream_diff_at_plan(state, ctx, file_path)
            rationale = (
                f"forks-profile.rewritten_modules[{module_path}] "
                f"policy=take_current_with_diff_note (category={category.value}, "
                f"note={note or 'n/a'})"
            )
            if stash_note:
                rationale = f"{rationale}; {stash_note}"
            self._record_profile_take_current(
                state,
                file_path,
                category,
                rationale=rationale,
            )
            return True

        return False

    def _stash_upstream_diff_at_plan(
        self, state: MergeState, ctx: PhaseContext, file_path: str
    ) -> str | None:
        """Capture `git diff <merge_base>..<upstream> -- <file>` to a patch
        file so a human reviewer can integrate the upstream delta later.

        Returns a short rationale fragment with the patch path, or ``None``
        when prerequisites (merge_base / git_tool / non-empty diff) are
        missing — never raises, since failure to stash must not block
        plan-stage routing.
        """
        merge_base = state.merge_base_commit or ""
        upstream_ref = state.config.upstream_ref
        if not merge_base or not upstream_ref or ctx.git_tool is None:
            return None
        stash_dir = get_diff_stash_dir(state.config.repo_path, state.run_id)
        try:
            patch_path = stash_upstream_diff(
                file_path,
                merge_base,
                upstream_ref,
                ctx.git_tool,
                stash_dir,
            )
        except Exception as exc:
            logger.warning(
                "forks-profile: stash_upstream_diff failed for %s: %s",
                file_path,
                exc,
            )
            return None
        if patch_path is None:
            return None
        return (
            f"upstream delta stashed at {patch_path} "
            "(apply with `git apply --3way` to integrate manually)"
        )

    def _route_migration_collision(
        self,
        state: MergeState,
        file_path: str,
        category: FileChangeCategory,
        number: int,
        rule: MigrationCollisionRule,
    ) -> None:
        """Apply a migration_policy.on_collision verdict to ``file_path``.

        ``escalate_human`` (default) writes an ESCALATE_HUMAN decision so
        the operator must reconcile the numbering manually; ``take_current``
        keeps the fork's view (D_MISSING stays absent) and records the
        collision in the rationale for audit.
        """
        rationale = (
            f"forks-profile.migration_policy collision: number={number} "
            f"(category={category.value}); "
            f"action={rule.action.value}"
        )
        if rule.note:
            rationale = f"{rationale}; note={rule.note}"

        if rule.action == MigrationCollisionAction.TAKE_CURRENT:
            self._record_profile_take_current(state, file_path, category, rationale)
        else:
            self._record_profile_escalate_human(state, file_path, category, rationale)

    def _record_profile_take_current(
        self,
        state: MergeState,
        file_path: str,
        category: FileChangeCategory,
        rationale: str,
    ) -> None:
        if category == FileChangeCategory.D_MISSING:
            file_status = FileStatus.DELETED
        elif category == FileChangeCategory.D_EXTRA:
            file_status = FileStatus.ADDED
        else:
            file_status = FileStatus.MODIFIED
        state.file_decision_records[file_path] = FileDecisionRecord(
            file_path=file_path,
            file_status=file_status,
            decision=MergeDecision.TAKE_CURRENT,
            decision_source=DecisionSource.AUTO_PLANNER,
            confidence=1.0,
            rationale=rationale,
            phase="initialize",
            agent="forks_profile_routing",
        )

    def _record_profile_escalate_human(
        self,
        state: MergeState,
        file_path: str,
        category: FileChangeCategory,
        rationale: str,
    ) -> None:
        if category == FileChangeCategory.D_MISSING:
            file_status = FileStatus.ADDED
        elif category == FileChangeCategory.D_EXTRA:
            file_status = FileStatus.ADDED
        else:
            file_status = FileStatus.MODIFIED
        state.file_decision_records[file_path] = FileDecisionRecord(
            file_path=file_path,
            file_status=file_status,
            decision=MergeDecision.ESCALATE_HUMAN,
            decision_source=DecisionSource.AUTO_PLANNER,
            confidence=1.0,
            rationale=rationale,
            phase="initialize",
            agent="forks_profile_routing",
        )

    def _check_untracked_files(self, state: MergeState, ctx: PhaseContext) -> None:
        """Warn the user about untracked files in the working tree.

        Untracked files (e.g. `_assets/*.png` from prior plugin work) can
        collide with upstream paths during merge and surprise the user
        when the run rewrites the working directory. This is informational
        only — does not abort the run.
        """
        try:
            entries = ctx.git_tool.get_status()
        except Exception as exc:
            logger.debug("untracked file check failed: %s", exc)
            return

        untracked = [path for code, path in entries if code == "??"]
        if not untracked:
            return

        preview = ", ".join(untracked[:5])
        if len(untracked) > 5:
            preview = f"{preview}, ... (+{len(untracked) - 5} more)"
        logger.warning(
            "Working tree has %d untracked file(s) — they will not be "
            "merged automatically and may collide with upstream paths. "
            "Consider `git add`, `git stash -u`, or `git clean -fd` before "
            "running merge. First files: %s",
            len(untracked),
            preview,
        )
        ctx.notify(
            "orchestrator",
            f"⚠ {len(untracked)} untracked file(s) in working tree "
            f"(e.g. {preview}). Consider git add/stash/clean before merge.",
        )

    def _resolve_project_context(self, state: MergeState, ctx: PhaseContext) -> None:
        repo_root = Path(state.config.repo_path).resolve()
        parts: list[str] = []

        claude_md = repo_root / "CLAUDE.md"
        if claude_md.exists():
            content = claude_md.read_text(encoding="utf-8").strip()
            if content:
                parts.append(content)
                logger.info(
                    "Loaded project context from CLAUDE.md (%d chars)", len(content)
                )

        readme = repo_root / "README.md"
        if readme.exists():
            lines = readme.read_text(encoding="utf-8").splitlines()[:200]
            content = "\n".join(lines).strip()
            if content:
                parts.append(content)
                logger.info("Loaded README.md excerpt (%d lines)", min(200, len(lines)))

        if state.config.project_context:
            parts.insert(0, state.config.project_context.strip())

        merged = "\n\n---\n\n".join(filter(None, parts))

        if not merged:
            logger.warning(
                "No project context found (CLAUDE.md, README.md, or config "
                "project_context). Run `merge init` to generate a CLAUDE.md "
                "for better merge decisions."
            )
            ctx.notify(
                "orchestrator",
                "⚠ No project context found — run `merge init` for better decisions",
            )
        else:
            state.config = state.config.model_copy(update={"project_context": merged})
            logger.info("Resolved project context: %d chars total", len(merged))

    def _run_reverse_impact(
        self, state: MergeState, ctx: PhaseContext, merge_base: str
    ) -> None:
        """P1-1 Phase 0.5: extract upstream interface changes and scan
        fork-only files for dangling references."""
        ctx.notify("orchestrator", "Extracting upstream interface changes")

        upstream_ref = state.config.upstream_ref
        changed_files = {
            fp
            for fp, cat in state.file_categories.items()
            if cat in (FileChangeCategory.B, FileChangeCategory.C)
        }
        if not changed_files:
            return

        extractor = InterfaceChangeExtractor()
        pairs: list[tuple[str, str | None, str | None]] = []
        for fp in sorted(changed_files):
            base_content = ctx.git_tool.get_file_content(merge_base, fp)
            upstream_content = ctx.git_tool.get_file_content(upstream_ref, fp)
            pairs.append((fp, base_content, upstream_content))

        interface_changes = extractor.extract_from_paths(pairs)
        state.interface_changes = interface_changes
        if not interface_changes:
            logger.info("Phase 0.5: no upstream interface changes detected")
            return

        logger.info(
            "Phase 0.5: %d upstream interface changes extracted across %d files",
            len(interface_changes),
            len({c.file_path for c in interface_changes}),
        )

        fork_only = {
            fp
            for fp, cat in state.file_categories.items()
            if cat == FileChangeCategory.D_EXTRA
        }
        for entry in state.config.customizations:
            fork_only.update(entry.files)

        scanner = ReverseImpactScanner(
            repo_path=Path(state.config.repo_path).resolve(),
            max_files_per_symbol=state.config.reverse_impact.max_files_per_symbol,
        )
        reverse_impacts = scanner.scan(
            interface_changes,
            fork_only_files=fork_only,
            extra_globs=state.config.reverse_impact.extra_scan_globs,
        )
        state.reverse_impacts = reverse_impacts
        if reverse_impacts:
            logger.warning(
                "Phase 0.5: %d upstream symbols still referenced in fork-only scope",
                len(reverse_impacts),
            )
