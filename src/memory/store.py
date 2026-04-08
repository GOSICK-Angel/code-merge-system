from __future__ import annotations

import logging
from collections import defaultdict

from src.memory.models import (
    MemoryEntry,
    MemoryEntryType,
    MergeMemory,
    PhaseSummary,
)

logger = logging.getLogger(__name__)

MAX_ENTRIES = 500
CONSOLIDATION_THRESHOLD = 300


class MemoryStore:
    def __init__(self, memory: MergeMemory | None = None) -> None:
        self._memory = memory or MergeMemory()

    def add_entry(self, entry: MemoryEntry) -> MemoryStore:
        entries = list(self._memory.entries) + [entry]
        if len(entries) > CONSOLIDATION_THRESHOLD:
            entries = _consolidate_entries(entries)
        if len(entries) > MAX_ENTRIES:
            entries = sorted(entries, key=lambda e: e.confidence, reverse=True)
            entries = entries[:MAX_ENTRIES]
        new_memory = self._memory.model_copy(update={"entries": entries})
        return MemoryStore(new_memory)

    def record_phase_summary(self, summary: PhaseSummary) -> MemoryStore:
        summaries = {**self._memory.phase_summaries, summary.phase: summary}
        new_memory = self._memory.model_copy(update={"phase_summaries": summaries})
        return MemoryStore(new_memory)

    def set_codebase_profile(self, key: str, value: str) -> MemoryStore:
        profile = {**self._memory.codebase_profile, key: value}
        new_memory = self._memory.model_copy(update={"codebase_profile": profile})
        return MemoryStore(new_memory)

    def query_by_path(self, file_path: str, limit: int = 5) -> list[MemoryEntry]:
        results: list[MemoryEntry] = []
        for entry in self._memory.entries:
            for fp in entry.file_paths:
                if file_path.startswith(fp) or fp.startswith(file_path):
                    results.append(entry)
                    break
        results.sort(key=lambda e: (e.confidence, e.created_at), reverse=True)
        return results[:limit]

    def query_by_tags(self, tags: list[str], limit: int = 5) -> list[MemoryEntry]:
        tag_set = set(tags)
        results: list[MemoryEntry] = []
        for entry in self._memory.entries:
            if tag_set & set(entry.tags):
                results.append(entry)
        results.sort(key=lambda e: (e.confidence, e.created_at), reverse=True)
        return results[:limit]

    def query_by_type(
        self, entry_type: MemoryEntryType, limit: int = 10
    ) -> list[MemoryEntry]:
        results = [e for e in self._memory.entries if e.entry_type == entry_type]
        results.sort(key=lambda e: (e.confidence, e.created_at), reverse=True)
        return results[:limit]

    def get_phase_summary(self, phase: str) -> PhaseSummary | None:
        return self._memory.phase_summaries.get(phase)

    def get_relevant_context(
        self, file_paths: list[str], max_entries: int = 10
    ) -> list[MemoryEntry]:
        scored: dict[str, tuple[float, MemoryEntry]] = {}
        for entry in self._memory.entries:
            path_score = 0.0
            for fp in file_paths:
                for efp in entry.file_paths:
                    if fp == efp:
                        path_score = max(path_score, 1.0)
                    elif fp.startswith(efp) or efp.startswith(fp):
                        common = len(_common_prefix(fp, efp))
                        path_score = max(path_score, common / max(len(fp), len(efp)))

            if path_score == 0.0 and not entry.file_paths:
                path_score = 0.1

            relevance = path_score * 0.5 + entry.confidence * 0.5
            if relevance > 0.0:
                scored[entry.entry_id] = (relevance, entry)

        ranked = sorted(scored.values(), key=lambda x: x[0], reverse=True)
        return [entry for _, entry in ranked[:max_entries]]

    def remove_superseded(self, current_phase: str) -> MemoryStore:
        """Remove entries from earlier phases that are superseded by the current phase.

        Phase ordering: planning < auto_merge < conflict_analysis < judge_review.
        Entries from prior phases that share file paths with entries from a later
        phase are considered superseded.
        """
        phase_order = {
            "planning": 0,
            "auto_merge": 1,
            "conflict_analysis": 2,
            "judge_review": 3,
        }
        current_rank = phase_order.get(current_phase, -1)
        if current_rank <= 0:
            return self

        current_phase_paths: set[str] = set()
        for entry in self._memory.entries:
            if entry.phase == current_phase:
                current_phase_paths.update(entry.file_paths)

        if not current_phase_paths:
            return self

        kept: list[MemoryEntry] = []
        removed = 0
        for entry in self._memory.entries:
            entry_rank = phase_order.get(entry.phase, -1)
            if (
                0 <= entry_rank < current_rank
                and entry.file_paths
                and set(entry.file_paths) <= current_phase_paths
            ):
                removed += 1
                continue
            kept.append(entry)

        if removed > 0:
            logger.info(
                "Removed %d superseded entries from phases before %s",
                removed,
                current_phase,
            )
            new_memory = self._memory.model_copy(update={"entries": kept})
            return MemoryStore(new_memory)
        return self

    def consolidate(self) -> MemoryStore:
        """Merge similar entries to reduce count while preserving information."""
        consolidated = _consolidate_entries(list(self._memory.entries))
        new_memory = self._memory.model_copy(update={"entries": consolidated})
        return MemoryStore(new_memory)

    def to_memory(self) -> MergeMemory:
        return self._memory.model_copy(deep=True)

    @staticmethod
    def from_memory(memory: MergeMemory) -> MemoryStore:
        return MemoryStore(memory.model_copy(deep=True))

    @property
    def entry_count(self) -> int:
        return len(self._memory.entries)

    @property
    def codebase_profile(self) -> dict[str, str]:
        return dict(self._memory.codebase_profile)


def _common_prefix(a: str, b: str) -> str:
    prefix_len = 0
    for ca, cb in zip(a, b):
        if ca != cb:
            break
        prefix_len += 1
    return a[:prefix_len]


def _consolidate_entries(entries: list[MemoryEntry]) -> list[MemoryEntry]:
    """Group entries by (phase, entry_type, primary_tag) and merge each group."""
    groups: dict[tuple[str, str, str], list[MemoryEntry]] = defaultdict(list)
    ungroupable: list[MemoryEntry] = []

    for entry in entries:
        primary_tag = entry.tags[0] if entry.tags else ""
        key = (entry.phase, entry.entry_type.value, primary_tag)
        groups[key].append(entry)

    result: list[MemoryEntry] = []
    for key, group in groups.items():
        if len(group) <= 2:
            result.extend(group)
            continue

        merged = _merge_entry_group(group)
        result.append(merged)

    result.extend(ungroupable)
    logger.debug("Consolidation: %d entries -> %d entries", len(entries), len(result))
    return result


def _merge_entry_group(group: list[MemoryEntry]) -> MemoryEntry:
    """Merge a list of similar entries into one consolidated entry."""
    all_paths: list[str] = []
    all_tags: set[str] = set()
    contents: list[str] = []
    max_confidence = 0.0
    latest_time = group[0].created_at

    for entry in group:
        for fp in entry.file_paths:
            if fp not in all_paths:
                all_paths.append(fp)
        all_tags.update(entry.tags)
        contents.append(entry.content)
        max_confidence = max(max_confidence, entry.confidence)
        if entry.created_at > latest_time:
            latest_time = entry.created_at

    unique_contents = list(dict.fromkeys(contents))
    if len(unique_contents) > 5:
        merged_content = f"[{len(unique_contents)} patterns consolidated] " + "; ".join(
            unique_contents[:5]
        )
    else:
        merged_content = "; ".join(unique_contents)

    boosted_confidence = min(0.98, max_confidence + 0.05 * (len(group) - 1))

    return MemoryEntry(
        entry_type=group[0].entry_type,
        phase=group[0].phase,
        content=merged_content,
        file_paths=all_paths[:20],
        tags=sorted(all_tags)[:10],
        confidence=round(boosted_confidence, 3),
        created_at=latest_time,
    )
