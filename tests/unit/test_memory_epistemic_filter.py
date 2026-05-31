"""Detect "model gave up" rationales before they poison memory.

Zod run cc477e1b uncovered a self-amplifying loop: an early run produced
"Without seeing actual file content, both sides made small changes" for
versions.ts; that string landed in memory; the next run's analyst saw
it under # Prior Knowledge and echoed it back even though the new prompt
had 21KB of real diff content. After four generations 37% of the memory
store was these epistemically empty entries.

The predicate flags the surface markers used by Claude when it abandons
specific analysis: "without seeing", "no actual diff content", "no actual
conflict markers", "based on prior pattern decisions". Hits skip the
write — they were never useful to begin with.
"""

from __future__ import annotations

import pytest

from src.memory.summarizer import _is_epistemically_empty


class TestIsEpistemicallyEmptyPositiveMarkers:
    @pytest.mark.parametrize(
        "rationale",
        [
            "Without seeing actual file content, both sides made small changes.",
            "Without file content, combining both is appropriate.",
            "No actual diff content available. Based on patterns...",
            "No actual conflict markers present (conflict count is 0).",
            "Based on prior pattern decisions for this exact file, semantic_merge.",
            "Pattern decisions for this exact file suggest take_target.",
            # Variants observed in the post-fix zod run that the substring
            # list missed — drove the move to regex patterns.
            "No diff content available. Recommending semantic_merge.",
            "Without diff content, the volume signal favors take_target.",
            "Without diff content available, the merge looks safe.",
            "Based on prior phase decisions for this file, take_target.",
            "pattern of prior decisions (take_target for this file) favor take_target",
            "LLM analysis skipped — circuit breaker open",
            "Reason: circuit breaker open after 3 failures.",
        ],
    )
    def test_failure_markers_flagged(self, rationale: str) -> None:
        assert _is_epistemically_empty(rationale)

    def test_case_insensitive(self) -> None:
        assert _is_epistemically_empty(
            "WITHOUT SEEING actual file content the merge looks fine."
        )

    def test_marker_anywhere_in_text(self) -> None:
        # The marker can appear mid-rationale, not just at the start.
        assert _is_epistemically_empty(
            "Fork added a helper. Without seeing the diff, we recommend take_target."
        )


class TestIsEpistemicallyEmptyNegativeCases:
    def test_empty_rationale_not_flagged(self) -> None:
        # Empty is handled by other code paths; this predicate only catches
        # the specific "I gave up" surface markers.
        assert not _is_epistemically_empty("")

    def test_none_safe(self) -> None:
        assert not _is_epistemically_empty(None)

    @pytest.mark.parametrize(
        "rationale",
        [
            "Upstream modified the cidrv6 regex; fork added cidrv6Mapped.",
            "Take_target is safe — fork's 2-line change is in dead code.",
            "Both sides added new schema entries to versions.ts at line 42.",
            "Concurrent modification: upstream's transform refactor "
            "conflicts with fork's catch handler addition.",
        ],
    )
    def test_substantive_rationales_pass(self, rationale: str) -> None:
        assert not _is_epistemically_empty(rationale)

    def test_natural_use_of_word_pattern_not_flagged(self) -> None:
        # "pattern" alone is fine — only the specific epistemic-failure
        # phrase "based on prior pattern decisions" is rejected.
        assert not _is_epistemically_empty(
            "Upstream introduced the visitor pattern; fork still uses the old "
            "switch-based dispatcher."
        )
