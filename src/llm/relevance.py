"""Relevance scoring for code chunks with budget-aware render level assignment.

Scores each CodeChunk (0.0–1.0) based on diff overlap, conflict regions,
security patterns, and cross-references, then assigns one of three render
levels — FULL, SIGNATURE, or DROP — while respecting a token budget.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum

from src.llm.chunker import ChunkKind, CodeChunk
from src.llm.context import estimate_tokens

logger = logging.getLogger(__name__)


class RenderLevel(StrEnum):
    FULL = "full"
    SIGNATURE = "signature"
    DROP = "drop"


FULL_THRESHOLD = 0.6
SIGNATURE_THRESHOLD = 0.2

_BASE_SCORES: dict[ChunkKind, float] = {
    ChunkKind.FUNCTION: 0.15,
    ChunkKind.METHOD: 0.15,
    ChunkKind.CLASS: 0.20,
    ChunkKind.IMPORT: 0.10,
    ChunkKind.STATEMENT: 0.05,
    ChunkKind.COMMENT: 0.00,
    ChunkKind.MODULE: 0.10,
    ChunkKind.UNKNOWN: 0.05,
}

_ENTRY_POINT_NAMES = {
    "main",
    "init",
    "main",
    "constructor",
    "setup",
    "teardown",
}


@dataclass(frozen=True)
class ScoringContext:
    diff_ranges: list[tuple[int, int]]
    conflict_ranges: list[tuple[int, int]] = field(default_factory=list)
    security_patterns: list[str] = field(default_factory=list)
    referenced_names: frozenset[str] = field(default_factory=frozenset)


def _ranges_overlap(a: range, b: range) -> bool:
    return a.start < b.stop and b.start < a.stop


def _ranges_adjacent(a: range, b: range, margin: int = 10) -> bool:
    expanded_a = range(max(0, a.start - margin), a.stop + margin)
    return _ranges_overlap(expanded_a, b)


class RelevanceScorer:
    def __init__(self, context: ScoringContext) -> None:
        self._context = context

    def score_chunk(self, chunk: CodeChunk) -> float:
        score = _BASE_SCORES.get(chunk.kind, 0.05)
        score += self._diff_overlap_score(chunk)
        score += self._conflict_score(chunk)
        score += self._security_score(chunk)
        score += self._reference_score(chunk)
        score += self._entry_point_score(chunk)
        return min(1.0, score)

    def score_and_assign(
        self,
        chunks: list[CodeChunk],
        budget_tokens: int,
    ) -> dict[str, RenderLevel]:
        if not chunks:
            return {}

        scored = [(c, self.score_chunk(c)) for c in chunks]

        full_contents = " ".join(c.content for c, s in scored if s >= FULL_THRESHOLD)

        boosted: list[tuple[CodeChunk, float]] = []
        for chunk, score in scored:
            if score < FULL_THRESHOLD and chunk.name in full_contents:
                score = min(1.0, score + 0.3)
            boosted.append((chunk, score))

        levels: dict[str, RenderLevel] = {}
        for chunk, score in boosted:
            if score >= FULL_THRESHOLD:
                levels[chunk.name] = RenderLevel.FULL
            elif score >= SIGNATURE_THRESHOLD:
                levels[chunk.name] = RenderLevel.SIGNATURE
            else:
                levels[chunk.name] = RenderLevel.DROP

        levels = self._demote_to_fit(boosted, levels, budget_tokens)

        full_count = sum(1 for v in levels.values() if v == RenderLevel.FULL)
        sig_count = sum(1 for v in levels.values() if v == RenderLevel.SIGNATURE)
        drop_count = sum(1 for v in levels.values() if v == RenderLevel.DROP)
        logger.debug(
            "Scored %d chunks: full=%d, signature=%d, drop=%d",
            len(chunks),
            full_count,
            sig_count,
            drop_count,
        )

        return levels

    def _diff_overlap_score(self, chunk: CodeChunk) -> float:
        chunk_range = range(chunk.start_line, chunk.end_line + 1)
        for start, end in self._context.diff_ranges:
            diff_range = range(start, end + 1)
            if _ranges_overlap(chunk_range, diff_range):
                return 0.6
            if _ranges_adjacent(chunk_range, diff_range, margin=10):
                return 0.2
        return 0.0

    def _conflict_score(self, chunk: CodeChunk) -> float:
        chunk_range = range(chunk.start_line, chunk.end_line + 1)
        for start, end in self._context.conflict_ranges:
            if _ranges_overlap(chunk_range, range(start, end + 1)):
                return 0.5
        return 0.0

    def _security_score(self, chunk: CodeChunk) -> float:
        content_lower = chunk.content.lower()
        for pattern in self._context.security_patterns:
            if pattern.lower() in content_lower:
                return 0.3
        return 0.0

    def _reference_score(self, chunk: CodeChunk) -> float:
        if chunk.name in self._context.referenced_names:
            return 0.3
        return 0.0

    def _entry_point_score(self, chunk: CodeChunk) -> float:
        name_normalized = chunk.name.lower().strip("_")
        if name_normalized in _ENTRY_POINT_NAMES:
            return 0.2
        return 0.0

    def _demote_to_fit(
        self,
        scored: list[tuple[CodeChunk, float]],
        levels: dict[str, RenderLevel],
        budget_tokens: int,
    ) -> dict[str, RenderLevel]:
        def _total_tokens() -> int:
            total = 0
            for chunk, _ in scored:
                level = levels.get(chunk.name, RenderLevel.DROP)
                if level == RenderLevel.FULL:
                    total += estimate_tokens(chunk.content)
                elif level == RenderLevel.SIGNATURE:
                    total += estimate_tokens(chunk.signature)
            return total

        full_by_score = sorted(
            [(c, s) for c, s in scored if levels.get(c.name) == RenderLevel.FULL],
            key=lambda x: x[1],
        )
        for chunk, _score in full_by_score:
            if _total_tokens() <= budget_tokens:
                break
            levels[chunk.name] = RenderLevel.SIGNATURE

        sig_by_score = sorted(
            [(c, s) for c, s in scored if levels.get(c.name) == RenderLevel.SIGNATURE],
            key=lambda x: x[1],
        )
        for chunk, _score in sig_by_score:
            if _total_tokens() <= budget_tokens:
                break
            levels[chunk.name] = RenderLevel.DROP

        return levels
