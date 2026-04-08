from __future__ import annotations

import os
from collections import Counter

from src.memory.models import MemoryEntry, MemoryEntryType, PhaseSummary
from src.models.decision import FileDecisionRecord
from src.models.state import MergeState

_MAX_KEY_DECISIONS = 10
_MAX_PATTERNS = 10
_DIR_DOMINANCE_THRESHOLD = 0.7


class PhaseSummarizer:
    def summarize_planning(
        self, state: MergeState
    ) -> tuple[PhaseSummary, list[MemoryEntry]]:
        entries: list[MemoryEntry] = []
        stats: dict[str, int | float] = {}

        category_counts: Counter[str] = Counter()
        for cat in state.file_categories.values():
            category_counts[cat.value] += 1

        stats.update(dict(category_counts))
        stats["total_files"] = len(state.file_categories)

        decisions: list[str] = []
        if state.merge_plan:
            total_phases = len(state.merge_plan.phases)
            stats["total_phases"] = total_phases
            decisions.append(f"Plan generated with {total_phases} batches")

            if state.merge_plan.category_summary:
                cs = state.merge_plan.category_summary
                decisions.append(
                    f"Classification: {cs.b_upstream_only} B-class, "
                    f"{cs.c_both_changed} C-class, "
                    f"{cs.d_missing} D-missing"
                )

        patterns: list[str] = []
        if category_counts.get("both_changed", 0) > 0:
            c_files = [
                fp
                for fp, cat in state.file_categories.items()
                if cat.value == "both_changed"
            ]
            dir_counts = _count_by_directory(c_files)
            for dir_path, count in dir_counts.most_common(3):
                if count >= 3:
                    patterns.append(
                        f"{count} C-class (both-changed) files in {dir_path}/"
                    )
                    entries.append(
                        MemoryEntry(
                            entry_type=MemoryEntryType.PATTERN,
                            phase="planning",
                            content=f"{count} files with both-side changes in {dir_path}/",
                            file_paths=[dir_path],
                            tags=["c_class", "conflict_prone", dir_path],
                            confidence=0.9,
                        )
                    )

        summary = PhaseSummary(
            phase="planning",
            files_processed=len(state.file_categories),
            key_decisions=decisions[:_MAX_KEY_DECISIONS],
            patterns_discovered=patterns[:_MAX_PATTERNS],
            statistics=stats,
        )
        return summary, entries

    def summarize_auto_merge(
        self, state: MergeState
    ) -> tuple[PhaseSummary, list[MemoryEntry]]:
        entries: list[MemoryEntry] = []
        records = state.file_decision_records

        decision_counts: Counter[str] = Counter()
        for record in records.values():
            decision_counts[record.decision.value] += 1

        stats: dict[str, int | float] = {
            "files_merged": len(records),
            **dict(decision_counts),
        }

        decisions: list[str] = []
        if records:
            decisions.append(
                f"Processed {len(records)} files: "
                + ", ".join(f"{v} {k}" for k, v in decision_counts.most_common())
            )

        patterns: list[str] = []
        dir_decisions = _group_decisions_by_directory(records)
        for dir_path, dir_records in dir_decisions.items():
            if len(dir_records) < 3:
                continue
            dominant = Counter(r.decision.value for r in dir_records).most_common(1)
            if dominant:
                decision_name, count = dominant[0]
                ratio = count / len(dir_records)
                if ratio >= _DIR_DOMINANCE_THRESHOLD:
                    pattern_text = (
                        f"{dir_path}/: {count}/{len(dir_records)} files used "
                        f"'{decision_name}' strategy"
                    )
                    patterns.append(pattern_text)
                    entries.append(
                        MemoryEntry(
                            entry_type=MemoryEntryType.PATTERN,
                            phase="auto_merge",
                            content=pattern_text,
                            file_paths=[dir_path],
                            tags=["merge_strategy", decision_name, dir_path],
                            confidence=min(0.95, 0.7 + ratio * 0.3),
                        )
                    )

        summary = PhaseSummary(
            phase="auto_merge",
            files_processed=len(records),
            key_decisions=decisions[:_MAX_KEY_DECISIONS],
            patterns_discovered=patterns[:_MAX_PATTERNS],
            statistics=stats,
        )
        return summary, entries

    def summarize_conflict_analysis(
        self, state: MergeState
    ) -> tuple[PhaseSummary, list[MemoryEntry]]:
        entries: list[MemoryEntry] = []
        analyses = state.conflict_analyses

        type_counts: Counter[str] = Counter()
        strategy_counts: Counter[str] = Counter()
        for analysis in analyses.values():
            type_counts[analysis.conflict_type.value] += 1
            strategy_counts[analysis.recommended_strategy.value] += 1

        stats: dict[str, int | float] = {
            "files_analyzed": len(analyses),
            **{f"conflict_{k}": v for k, v in type_counts.items()},
            **{f"strategy_{k}": v for k, v in strategy_counts.items()},
        }

        decisions: list[str] = []
        if analyses:
            decisions.append(f"Analyzed {len(analyses)} conflict files")
            for ctype, count in type_counts.most_common(3):
                decisions.append(f"{count} files with {ctype} conflicts")

        patterns: list[str] = []
        for ctype, count in type_counts.most_common():
            if count >= 3:
                files = [
                    fp for fp, a in analyses.items() if a.conflict_type.value == ctype
                ]
                dirs = _count_by_directory(files)
                top_dir = dirs.most_common(1)
                location = f" (mostly in {top_dir[0][0]}/)" if top_dir else ""
                pattern_text = f"Recurring {ctype} conflicts ({count} files){location}"
                patterns.append(pattern_text)
                entries.append(
                    MemoryEntry(
                        entry_type=MemoryEntryType.PATTERN,
                        phase="conflict_analysis",
                        content=pattern_text,
                        file_paths=files[:5],
                        tags=["conflict_type", ctype],
                        confidence=0.85,
                    )
                )

        summary = PhaseSummary(
            phase="conflict_analysis",
            files_processed=len(analyses),
            key_decisions=decisions[:_MAX_KEY_DECISIONS],
            patterns_discovered=patterns[:_MAX_PATTERNS],
            statistics=stats,
        )
        return summary, entries

    def summarize_judge_review(
        self, state: MergeState
    ) -> tuple[PhaseSummary, list[MemoryEntry]]:
        entries: list[MemoryEntry] = []
        verdicts = state.judge_verdicts_log

        stats: dict[str, int | float] = {
            "total_rounds": len(verdicts),
            "repair_rounds": state.judge_repair_rounds,
        }

        decisions: list[str] = []
        if verdicts:
            verdict_types = [v.get("verdict", "unknown") for v in verdicts]
            decisions.append(
                f"Judge review: {len(verdicts)} rounds, "
                f"verdicts: {', '.join(verdict_types)}"
            )

        patterns: list[str] = []
        issue_types: Counter[str] = Counter()
        for verdict_log in verdicts:
            for issue in verdict_log.get("issues", []):
                issue_type = issue.get("issue_type", "unknown")
                issue_types[issue_type] += 1

        for issue_type, count in issue_types.most_common(3):
            if count >= 2:
                pattern_text = (
                    f"Recurring judge issue: {issue_type} ({count} occurrences)"
                )
                patterns.append(pattern_text)
                entries.append(
                    MemoryEntry(
                        entry_type=MemoryEntryType.PATTERN,
                        phase="judge_review",
                        content=pattern_text,
                        tags=["judge_issue", issue_type],
                        confidence=0.8,
                    )
                )

        summary = PhaseSummary(
            phase="judge_review",
            files_processed=0,
            key_decisions=decisions[:_MAX_KEY_DECISIONS],
            patterns_discovered=patterns[:_MAX_PATTERNS],
            statistics=stats,
        )
        return summary, entries


def _count_by_directory(file_paths: list[str]) -> Counter[str]:
    dirs: Counter[str] = Counter()
    for fp in file_paths:
        parts = fp.split(os.sep)
        if len(parts) > 1:
            dirs[os.sep.join(parts[:2])] += 1
        else:
            dirs["."] += 1
    return dirs


def _group_decisions_by_directory(
    records: dict[str, FileDecisionRecord],
) -> dict[str, list[FileDecisionRecord]]:
    groups: dict[str, list[FileDecisionRecord]] = {}
    for fp, record in records.items():
        parts = fp.split(os.sep)
        dir_key = os.sep.join(parts[:2]) if len(parts) > 1 else "."
        groups.setdefault(dir_key, []).append(record)
    return groups
