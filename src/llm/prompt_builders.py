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
from src.memory.hit_tracker import MemoryHitTracker
from src.memory.layered_loader import LayeredMemoryLoader
from src.memory.store import MemoryStore
from src.models.config import AgentLLMConfig

logger = logging.getLogger(__name__)

# O-C2: lowered thresholds so staged processing covers more Judge / Analyst
# calls. Previously only 2.6% of 990 calls entered the relevance-scoring path;
# most payloads <15k chars stayed un-compressed even when the actual output
# only needed 1–2k output tokens.
STAGED_THRESHOLD_LINES = 200
STAGED_THRESHOLD_CHARS = 8_000


class AgentPromptBuilder:
    def __init__(
        self,
        llm_config: AgentLLMConfig,
        memory_store: MemoryStore | None = None,
        memory_hit_tracker: MemoryHitTracker | None = None,
    ) -> None:
        self.llm_config = llm_config
        self.memory_store = memory_store
        self.memory_hit_tracker = memory_hit_tracker
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

    def build_memory_context_text(
        self,
        file_paths: list[str],
        current_phase: str | None = None,
    ) -> str:
        if current_phase is not None and self.memory_store is not None:
            loader = LayeredMemoryLoader(self.memory_store, self.memory_hit_tracker)
            return loader.load_for_agent(current_phase, file_paths)

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
        is_security_sensitive: bool = False,
    ) -> str:
        from src.llm.chunker import (
            ASTChunker,
            chunk_key,
            detect_language,
            render_file_staged,
        )
        from src.llm.relevance import RenderLevel, RelevanceScorer, ScoringContext

        line_count = content.count("\n") + 1
        if (
            line_count < STAGED_THRESHOLD_LINES
            and len(content) < STAGED_THRESHOLD_CHARS
        ):
            max_chars = int(budget_tokens * _CHARS_PER_TOKEN)
            return content[:max_chars]

        # When the whole file fits the budget, stage nothing — return it
        # verbatim. Relevance scoring drops every chunk below SIGNATURE_THRESHOLD
        # purely on score, so a large review budget can sit ~idle while the
        # rendered output is a near-empty set of fragments (the forgejo Judge
        # saw tokens=309/98789 then capped its verdict citing "truncated
        # content"). Returning the full file when it fits removes the false
        # truncation without ever exceeding budget.
        if estimate_tokens(content) <= budget_tokens:
            return content

        language = detect_language(file_path)
        chunks = ASTChunker.chunk(content, language)

        if not chunks:
            max_chars = int(budget_tokens * _CHARS_PER_TOKEN)
            return content[:max_chars]

        context = ScoringContext(
            diff_ranges=diff_ranges,
            conflict_ranges=conflict_ranges or [],
            is_security_sensitive=is_security_sensitive,
        )
        scorer = RelevanceScorer(context)
        levels = scorer.score_and_assign(chunks, budget_tokens)

        full_count = sum(1 for v in levels.values() if v == RenderLevel.FULL)
        sig_count = sum(1 for v in levels.values() if v == RenderLevel.SIGNATURE)
        drop_count = sum(1 for v in levels.values() if v == RenderLevel.DROP)
        used_tokens = sum(
            estimate_tokens(c.content)
            if levels.get(chunk_key(c)) == RenderLevel.FULL
            else estimate_tokens(c.signature)
            if levels.get(chunk_key(c)) == RenderLevel.SIGNATURE
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

        # Floor: relevance scoring can drop every chunk when a file has no diff
        # or conflict anchor (e.g. an upstream_only take_target file under Judge
        # review). render_file_staged would then emit only a content-free
        # "# ... (N sections omitted)" placeholder, which downstream LLMs — the
        # Judge in particular — mistake for an empty / unverifiable file. When
        # nothing real was rendered, fall back to the actual content (trimmed to
        # the token budget) instead of the placeholder.
        if used_tokens == 0:
            max_chars = int(budget_tokens * _CHARS_PER_TOKEN)
            return content[:max_chars]

        return render_file_staged(chunks, levels)
