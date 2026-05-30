"""Quality-gate regression net for ``parse_merge_result`` + the
``elision_detector`` family (prose preamble, truncation, elision).

These tests pin the behaviour that prevents LLM hallucinations
(chain-of-thought leaking into a source file, output truncated at
``max_tokens``, echo-back of elision markers) from reaching
``apply_with_snapshot``. Each gate is exercised in isolation so a
regression on one detector does not silently mask the others.
"""

from __future__ import annotations

import pytest

from src.llm.client import LLMResponse, ParseError
from src.llm.response_parser import parse_merge_result
from src.tools.elision_detector import (
    has_elision,
    has_prose_preamble,
    looks_truncated,
)


class TestProsePreamble:
    """``has_prose_preamble`` — first line conversational hint detector."""

    @pytest.mark.parametrize(
        "first_line",
        [
            "Looking at the current content, I'll merge them as follows:",
            "Looking at this file, here is the result:",
            "Here is the merged file:",
            "Here's the merged content:",
            "Let me merge these two versions:",
            "Let me combine the changes...",
            "I'll merge the upstream and fork versions.",
            "I will produce the merged output:",
            "I have combined both versions:",
            "Sure, here is the merged file:",
            "Okay, here are the changes:",
            "Based on the analysis, the merged file is:",
            "Based on my comparison of both versions:",
            "After analyzing the diff, here is the merge:",
            "After comparing the two versions:",
            "The merged file looks like this:",
            "The merged content combines both:",
            "To merge these two files, I'll take:",
        ],
    )
    def test_matches_known_preambles(self, first_line: str):
        content = f"{first_line}\n\npackage main\n\nfunc main() {{}}"
        hit, sample = has_prose_preamble(content)
        assert hit, f"should flag preamble: {first_line!r}"
        assert sample is not None
        assert sample.startswith(first_line[:30])

    @pytest.mark.parametrize(
        "clean",
        [
            "package main\n\nfunc main() {}",
            "// Copyright 2024 The Foo Authors.\npackage main",
            "/* SPDX-License-Identifier: MIT */\n#include <stdio.h>",
            "from __future__ import annotations\n\nimport os",
            "<!DOCTYPE html>\n<html>",
            "# Project README\n\nA collaboration tool.",
            "// Looking at this codebase, I've been refactoring...",  # comment, mid-sentence
            "",
        ],
    )
    def test_does_not_flag_clean_content(self, clean: str):
        hit, _ = has_prose_preamble(clean)
        assert not hit, f"should NOT flag clean content: {clean!r}"

    def test_fenced_output_is_deferred_to_stripper(self):
        # Caller (parse_merge_result) is expected to unfence first.
        # has_prose_preamble defers when it sees a fence opener.
        content = "```go\nLooking at this, I'd merge...\n```"
        hit, _ = has_prose_preamble(content)
        assert not hit


class TestLooksTruncated:
    """``looks_truncated`` — heuristic length + tail-shape check."""

    def test_no_sizes_means_no_flag(self):
        # Without input-size reference the heuristic refuses to fire —
        # avoiding false positives on legitimate one-liners.
        content = "plain text content"
        hit, _ = looks_truncated(content)
        assert not hit

    def test_healthy_tail_with_sizes_is_clean(self):
        content = "package main\n\nfunc main() {}\n"
        hit, _ = looks_truncated(content, current_size=200, target_size=200)
        assert not hit

    def test_dramatic_shortness_with_unhealthy_tail_fires(self):
        # 50 chars vs 2000-char inputs; tail is mid-identifier.
        content = "package main\n\nfunc main() {\n\tsettings.authorized_integ"
        hit, sample = looks_truncated(content, current_size=2000, target_size=2000)
        assert hit
        assert sample is not None
        assert "authorized_integ" in sample

    def test_dramatic_shortness_with_healthy_tail_now_fires(self):
        # Behavior change (#9B): a merge dramatically shorter than both inputs
        # is flagged EVEN WHEN the tail ends in a healthy terminator. The old
        # ends_healthy short-circuit let a clean mid-file elision (drop a
        # function, close the file with `}`) pass regardless of how much was
        # deleted — the dominant silent code-loss mode. 28 chars from 2000-char
        # inputs is well under the 60% floor, so it now fires.
        content = "package main\n\nfunc main() {}\n"
        hit, _ = looks_truncated(content, current_size=2000, target_size=2000)
        assert hit

    def test_tiny_inputs_below_min_size_are_not_length_checked(self):
        # The length-shortfall branch is guarded by a minimum smaller-input
        # size so a legitimately short merge of two short files never misfires.
        content = "x"  # 1 char; below the min-size guard, so not flagged
        hit, _ = looks_truncated(content, current_size=120, target_size=120)
        assert not hit

    def test_size_within_range_is_clean_even_with_unusual_tail(self):
        # A template file ending with `{{ .Foo }}` would have last char
        # `}` (healthy). But files ending with an identifier and within
        # length range should NOT be flagged — the length guard
        # protects against false positives.
        content = "function template() {\n  return data\n}\nfoo"
        hit, _ = looks_truncated(content, current_size=50, target_size=50)
        assert not hit


class TestParseMergeResultGates:
    """End-to-end gate chain in ``parse_merge_result``."""

    def test_max_tokens_stop_reason_is_refused(self):
        # The truncation gate fires before any text analysis — even a
        # syntactically clean tail is refused if the provider flagged
        # truncation.
        resp = LLMResponse(
            text="package main\n\nfunc main() {}\n", stop_reason="max_tokens"
        )
        with pytest.raises(ParseError, match="truncated at provider boundary"):
            parse_merge_result(resp)

    def test_openai_length_is_refused(self):
        # The client normalises OpenAI ``length`` to ``max_tokens``,
        # but we accept the raw value too so a future change in
        # normalisation can't silently bypass the gate.
        resp = LLMResponse(text="ok", stop_reason="length")
        with pytest.raises(ParseError, match="truncated"):
            parse_merge_result(resp)

    def test_output_too_large_sentinel_is_refused(self):
        # When the LLM cooperates with the prompt's "emit
        # OUTPUT_TOO_LARGE instead of truncating" contract, the parser
        # routes the same way as a max_tokens drop — escalate, don't
        # write to disk.
        resp = LLMResponse(text="OUTPUT_TOO_LARGE\n", stop_reason="stop")
        with pytest.raises(ParseError, match="OUTPUT_TOO_LARGE"):
            parse_merge_result(resp)

    def test_prose_preamble_is_refused(self):
        resp = LLMResponse(
            text="Looking at the current content, I'll merge them as follows:\n\npackage main\nfunc main() {}",
            stop_reason="stop",
        )
        with pytest.raises(ParseError, match="conversational preamble"):
            parse_merge_result(resp)

    def test_elision_marker_is_refused(self):
        resp = LLMResponse(
            text="package main\n\n# ... (3 sections omitted)\n\nfunc bar() {}",
            stop_reason="stop",
        )
        with pytest.raises(ParseError, match="elision marker"):
            parse_merge_result(resp)

    def test_heuristic_truncation_is_refused_with_sizes(self):
        # Output is 40 chars vs 2000 input — < 60% threshold — and tail
        # is mid-identifier (no terminator). Both signals required.
        resp = LLMResponse(
            text="package main\n\nfunc main() {\n\tsettings.authorized_integ",
            stop_reason="stop",
        )
        with pytest.raises(ParseError, match="truncated mid-line"):
            parse_merge_result(resp, current_size=2000, target_size=2000)

    def test_clean_output_passes(self):
        resp = LLMResponse(
            text='package main\n\nfunc main() {\n\tprint("ok")\n}\n',
            stop_reason="stop",
        )
        result = parse_merge_result(resp, current_size=50, target_size=50)
        assert "func main" in result

    def test_fenced_clean_output_passes(self):
        resp = LLMResponse(
            text="```go\npackage main\n\nfunc main() {}\n```",
            stop_reason="stop",
        )
        result = parse_merge_result(resp, current_size=50, target_size=50)
        assert result.strip().startswith("package main")
        assert "```" not in result

    def test_legacy_string_input_still_works(self):
        # Pre-LLMResponse callers (tests, dispute path) passed raw str.
        # The gate skips ``stop_reason`` check (no metadata available)
        # but the other gates still fire.
        result = parse_merge_result("package main\n\nfunc main() {}")
        assert "func main" in result

    def test_legacy_dict_input_still_works(self):
        # Even older shape — caller passed ``{"content": "..."}``.
        result = parse_merge_result({"content": "package main\n"})
        assert "package main" in result


class TestElisionDetectorRegression:
    """Ensure the new ``has_prose_preamble`` / ``looks_truncated``
    helpers did not break ``has_elision`` — the original detector
    must still work standalone (callers like ``apply_with_snapshot``
    rely on it)."""

    def test_python_style_elision(self):
        assert has_elision("# ... (3 sections omitted)")[0]

    def test_html_style_elision(self):
        assert has_elision("<!-- ... (5 sections omitted) -->")[0]

    def test_clean_content_passes(self):
        assert not has_elision("package main\n\nfunc main() {}")[0]


class TestEffectiveChunkSize:
    """#9D: chunk size is coupled to the executor's max_tokens output budget so a
    chunk pair's merged output cannot exceed the model's output ceiling and
    self-truncate."""

    def _agent(self, max_tokens: int):
        from src.agents.executor_agent import ExecutorAgent
        from src.models.config import AgentLLMConfig

        return ExecutorAgent(
            AgentLLMConfig(
                provider="openai",
                model="deepseek-v4-pro",
                api_key_env="OPENAI_API_KEY",
                max_tokens=max_tokens,
            )
        )

    def _state(self, chunk_size_chars: int):
        from unittest.mock import MagicMock

        st = MagicMock()
        st.config.chunk_size_chars = chunk_size_chars
        return st

    def test_small_max_tokens_caps_below_configured(self):
        # max_tokens=8192 -> output-safe ~11468 < configured 20000.
        agent = self._agent(8192)
        eff = agent._effective_chunk_size(self._state(20000))
        assert eff < 20000
        # A chunk pair's merged output (~2*eff chars) must fit under max_tokens.
        assert (2 * eff) / 3.5 < 8192

    def test_large_max_tokens_keeps_configured(self):
        # max_tokens=32768 -> output-safe ~45875 > configured 20000, so config wins.
        agent = self._agent(32768)
        assert agent._effective_chunk_size(self._state(20000)) == 20000

    def test_never_below_floor(self):
        agent = self._agent(512)
        assert agent._effective_chunk_size(self._state(20000)) >= 2000
