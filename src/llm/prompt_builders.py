from __future__ import annotations

import logging

from src.llm.context import (
    ContextPriority,
    ContextSection,
    TokenBudget,
    _CHARS_PER_TOKEN,
    estimate_tokens,
    get_context_window,
)
from src.memory.store import MemoryStore
from src.models.config import AgentLLMConfig

logger = logging.getLogger(__name__)

STAGED_THRESHOLD_LINES = 500
STAGED_THRESHOLD_CHARS = 15_000


class AgentPromptBuilder:
    def __init__(
        self,
        llm_config: AgentLLMConfig,
        memory_store: MemoryStore | None = None,
    ) -> None:
        self.llm_config = llm_config
        self.memory_store = memory_store
        self.budget = TokenBudget(
            model=llm_config.model,
            context_window=get_context_window(llm_config.model),
            reserved_for_output=llm_config.max_tokens,
        )

    def _build_memory_section(self, file_paths: list[str]) -> ContextSection | None:
        if self.memory_store is None:
            return None

        entries = self.memory_store.get_relevant_context(file_paths, max_entries=8)
        if not entries:
            return None

        lines = [f"- {entry.content}" for entry in entries]

        phase_summaries: list[str] = []
        for phase in ("planning", "auto_merge", "conflict_analysis"):
            ps = self.memory_store.get_phase_summary(phase)
            if ps and ps.patterns_discovered:
                phase_summaries.append(
                    f"  {phase}: {'; '.join(ps.patterns_discovered[:3])}"
                )

        parts = ["# Prior Knowledge"]
        if lines:
            parts.append("## Relevant Patterns")
            parts.extend(lines)
        if phase_summaries:
            parts.append("## Phase Insights")
            parts.extend(phase_summaries)

        return ContextSection(
            name="memory_context",
            content="\n".join(parts),
            priority=ContextPriority.MEDIUM,
            can_truncate=True,
            truncation_strategy="tail",
        )

    def compute_content_budget(self, fixed_prompt_text: str) -> int:
        fixed_tokens = estimate_tokens(fixed_prompt_text)
        available = self.budget.available - fixed_tokens
        return max(0, int(available * 3.5))

    def build_memory_context_text(self, file_paths: list[str]) -> str:
        section = self._build_memory_section(file_paths)
        if section is None:
            return ""
        return section.content

    def build_staged_content(
        self,
        content: str,
        file_path: str,
        diff_ranges: list[tuple[int, int]],
        budget_tokens: int,
        conflict_ranges: list[tuple[int, int]] | None = None,
        security_patterns: list[str] | None = None,
    ) -> str:
        from src.llm.chunker import ASTChunker, detect_language, render_file_staged
        from src.llm.relevance import RenderLevel, RelevanceScorer, ScoringContext

        line_count = content.count("\n") + 1
        if (
            line_count < STAGED_THRESHOLD_LINES
            and len(content) < STAGED_THRESHOLD_CHARS
        ):
            max_chars = int(budget_tokens * _CHARS_PER_TOKEN)
            return content[:max_chars]

        language = detect_language(file_path)
        chunks = ASTChunker.chunk(content, language)

        if not chunks:
            max_chars = int(budget_tokens * _CHARS_PER_TOKEN)
            return content[:max_chars]

        context = ScoringContext(
            diff_ranges=diff_ranges,
            conflict_ranges=conflict_ranges or [],
            security_patterns=security_patterns or [],
        )
        scorer = RelevanceScorer(context)
        levels = scorer.score_and_assign(chunks, budget_tokens)

        full_count = sum(1 for v in levels.values() if v == RenderLevel.FULL)
        sig_count = sum(1 for v in levels.values() if v == RenderLevel.SIGNATURE)
        drop_count = sum(1 for v in levels.values() if v == RenderLevel.DROP)
        used_tokens = sum(
            estimate_tokens(c.content)
            if levels.get(c.name) == RenderLevel.FULL
            else estimate_tokens(c.signature)
            if levels.get(c.name) == RenderLevel.SIGNATURE
            else 0
            for c in chunks
        )
        logger.info(
            "Staged processing: file=%s, chunks=%d, full=%d, signature=%d, drop=%d, tokens=%d/%d",
            file_path,
            len(chunks),
            full_count,
            sig_count,
            drop_count,
            used_tokens,
            budget_tokens,
        )

        return render_file_staged(chunks, levels)
