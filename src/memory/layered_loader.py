"""Three-layer memory loading: L0 (profile), L1 (phase essentials), L2 (file-relevant)."""

from __future__ import annotations

from src.memory.hit_tracker import MemoryHitTracker
from src.memory.store import MemoryStore

L1_MAX_PATTERNS = 5
L1_MAX_DECISIONS = 5
L2_MAX_ENTRIES = 8

# O-M3: when the store grows beyond these thresholds, the loader tightens
# the L2 cap so prompts stay under the context window. Checked from
# largest threshold downwards; first match wins.
_L2_DYNAMIC_CAPS: tuple[tuple[int, int], ...] = (
    (200, 4),
    (100, 6),
)

_PHASE_ORDER = ["planning", "auto_merge", "conflict_analysis", "judge_review"]


class LayeredMemoryLoader:
    def __init__(
        self,
        store: MemoryStore,
        tracker: MemoryHitTracker | None = None,
        min_relevance: float = 0.0,
        relevance_filter_threshold: int = 100,
    ) -> None:
        self._store = store
        self._tracker = tracker
        self._min_relevance = min_relevance
        self._relevance_filter_threshold = relevance_filter_threshold

    def load_for_agent(
        self,
        current_phase: str,
        file_paths: list[str] | None = None,
    ) -> str:
        sections: list[str] = []
        layer_counts: dict[str, int] = {
            "l0": 0,
            "l1_patterns": 0,
            "l1_decisions": 0,
            "l2": 0,
        }

        l0_text, l0_count = self._build_l0()
        if l0_text:
            sections.append(l0_text)
            layer_counts["l0"] = l0_count

        l1_text, l1_patterns, l1_decisions = self._build_l1(current_phase)
        if l1_text:
            sections.append(l1_text)
            layer_counts["l1_patterns"] = l1_patterns
            layer_counts["l1_decisions"] = l1_decisions

        if file_paths:
            l2_text, l2_count = self._build_l2(file_paths)
            if l2_text:
                sections.append(l2_text)
                layer_counts["l2"] = l2_count

        if self._tracker is not None:
            self._tracker.record_call(current_phase, layer_counts)  # type: ignore[arg-type]

        return "\n\n".join(sections) if sections else ""

    def _build_l0(self) -> tuple[str, int]:
        profile = self._store.codebase_profile
        if not profile:
            return "", 0
        lines = [f"- {k}: {v}" for k, v in profile.items()]
        return "## Project Profile\n" + "\n".join(lines), len(profile)

    def _build_l1(self, current_phase: str) -> tuple[str, int, int]:
        parts: list[str] = []
        patterns_count = 0
        decisions_count = 0

        current_summary = self._store.get_phase_summary(current_phase)
        if current_summary and current_summary.patterns_discovered:
            patterns = current_summary.patterns_discovered[:L1_MAX_PATTERNS]
            parts.append("Key patterns: " + "; ".join(patterns))
            patterns_count = len(patterns)

        prev_phase = _previous_phase(current_phase)
        if prev_phase:
            prev_summary = self._store.get_phase_summary(prev_phase)
            if prev_summary and prev_summary.key_decisions:
                decisions = prev_summary.key_decisions[:L1_MAX_DECISIONS]
                parts.append("Prior phase decisions: " + "; ".join(decisions))
                decisions_count = len(decisions)

        if not parts:
            return "", 0, 0
        return "## Phase Context\n" + "\n".join(parts), patterns_count, decisions_count

    def _build_l2(self, file_paths: list[str]) -> tuple[str, int]:
        cap = self._dynamic_l2_cap()
        min_rel = self._effective_min_relevance()
        relevant = self._store.get_relevant_context(
            file_paths, max_entries=cap, min_relevance=min_rel
        )
        if not relevant:
            return "", 0

        harmful_ids: frozenset[str] = (
            self._tracker.harmful_entry_ids()
            if self._tracker is not None
            else frozenset()
        )

        lines: list[str] = []
        injected_ids: list[str] = []
        for entry in relevant:
            if entry.entry_id in harmful_ids:
                continue
            if not _has_path_overlap(entry.file_paths, file_paths):
                continue
            label = entry.confidence_level.value.upper()
            lines.append(f"- [{label}] {entry.content}")
            injected_ids.append(entry.entry_id)

        if not lines:
            return "", 0
        if self._tracker is not None and injected_ids:
            self._tracker.record_injection(file_paths, injected_ids)
        return "## Relevant Patterns\n" + "\n".join(lines), len(lines)

    def _dynamic_l2_cap(self) -> int:
        count = self._store.entry_count
        for threshold, cap in _L2_DYNAMIC_CAPS:
            if count > threshold:
                return cap
        return L2_MAX_ENTRIES

    def _effective_min_relevance(self) -> float:
        if self._store.entry_count > self._relevance_filter_threshold:
            return self._min_relevance
        return 0.0


def _previous_phase(phase: str) -> str | None:
    try:
        idx = _PHASE_ORDER.index(phase)
        return _PHASE_ORDER[idx - 1] if idx > 0 else None
    except ValueError:
        return None


def _has_path_overlap(entry_paths: list[str], query_paths: list[str]) -> bool:
    if not entry_paths:
        return True
    for ep in entry_paths:
        for qp in query_paths:
            if ep == qp or ep.startswith(qp) or qp.startswith(ep):
                return True
    return False
