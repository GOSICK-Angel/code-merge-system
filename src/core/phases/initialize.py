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
    FileStatus,
)
from src.models.state import MergeState, SystemStatus
from src.tools.diff_parser import build_file_diff, detect_language
from src.tools.file_classifier import (
    classify_all_files,
    classify_file,
    category_summary,
    compute_risk_score,
    is_security_sensitive,
    matches_any_pattern,
)
from src.tools.git_tool import GitTool
from src.tools.pollution_auditor import PollutionAuditor
from src.tools.config_drift_detector import ConfigDriftDetector
from src.tools.commit_replayer import CommitReplayer
from src.tools.sync_point_detector import SyncPointDetector
from src.tools.interface_change_extractor import InterfaceChangeExtractor
from src.tools.reverse_impact_scanner import ReverseImpactScanner

logger = logging.getLogger(__name__)


_D_MISSING_PREVIEW_LINES = 200

_BINARY_EXTENSIONS = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".ico", ".bmp", ".webp", ".tiff",
        ".pdf", ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
        ".woff", ".woff2", ".ttf", ".otf", ".eot",
        ".mp3", ".mp4", ".wav", ".ogg", ".flac", ".avi", ".mov",
        ".so", ".dylib", ".dll", ".exe", ".class", ".jar", ".pyc",
        ".db", ".sqlite", ".sqlite3",
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
    diff_lines = [f"--- /dev/null", f"+++ b/{file_path}", f"@@ -0,0 +1,{len(preview)} @@"]
    diff_lines.extend(f"+{line}" for line in preview)
    if truncated:
        diff_lines.append(f"\\ ... ({len(lines) - _D_MISSING_PREVIEW_LINES} more lines not shown)")
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

        forced_paths = self._apply_forced_decisions(state, ctx, file_categories)
        if forced_paths:
            actionable_paths -= forced_paths
            ctx.notify(
                "orchestrator",
                f"Force-decision policy pre-resolved {len(forced_paths)} files; "
                f"{len(actionable_paths)} remain for AI flow",
            )

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
            fd = fd.model_copy(
                update={
                    "language": language,
                    "is_security_sensitive": sensitive,
                    "change_category": cat,
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
            logger.error(
                "Force TAKE_TARGET write failed for %s: %s", file_path, e
            )
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
