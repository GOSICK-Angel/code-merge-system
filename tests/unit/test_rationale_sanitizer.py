"""Tests for sanitize_hedging.

Regression: ConflictAnalyst rationale frequently contains hedging phrases
like 'without seeing actual content' / 'cannot be verified'. Downstream
Agents (especially Judge) include this rationale verbatim in their prompts
and echo it back as a real defect — producing false-positive FAIL verdicts
on otherwise-correct merges (zod run versions.ts).

The sanitizer strips hedging phrases from rationale text before it is
written into ConflictAnalysis, leaving the rest of the rationale intact.
"""

from __future__ import annotations

from src.llm.rationale_sanitizer import sanitize_hedging


class TestSanitizeHedging:
    def test_strips_without_seeing_actual_content(self):
        text = (
            "Both sides added version entries; without seeing actual content, "
            "semantic merge is appropriate to include both changes."
        )
        out = sanitize_hedging(text)
        assert "without seeing actual content" not in out.lower()
        assert "[analyst lacked source access]" in out
        assert "Both sides added version entries" in out
        assert "semantic merge is appropriate" in out

    def test_strips_cannot_be_verified(self):
        text = (
            "Fork-specific values cannot be verified, but merged content "
            "appears consistent."
        )
        out = sanitize_hedging(text)
        assert "cannot be verified" not in out.lower()
        assert "[analyst lacked source access]" in out
        assert "merged content" in out

    def test_strips_cannot_confirm(self):
        text = "We cannot confirm that the fork field was preserved."
        out = sanitize_hedging(text)
        assert "cannot confirm" not in out.lower()
        assert "[analyst lacked source access]" in out
        assert "fork field was preserved" in out

    def test_strips_unable_to_verify(self):
        text = "Unable to verify the merge result."
        out = sanitize_hedging(text)
        assert "unable to verify" not in out.lower()
        assert "[analyst lacked source access]" in out

    def test_strips_missing_original_file_content(self):
        text = "Missing original file content prevents a full check."
        out = sanitize_hedging(text)
        assert "missing original" not in out.lower()
        assert "[analyst lacked source access]" in out
        assert "prevents a full check" in out

    def test_clean_rationale_passes_through_unchanged(self):
        text = (
            "Upstream removed iso import and replaced iso.* with core._iso* "
            "calls. Fork added inst.week."
        )
        assert sanitize_hedging(text) == text

    def test_empty_string_passes_through(self):
        assert sanitize_hedging("") == ""


class TestParserSanitizesRationale:
    def test_parse_conflict_analysis_strips_hedging_from_rationale(self):
        from src.llm.response_parser import parse_conflict_analysis

        raw = {
            "conflict_type": "concurrent_modification",
            "recommended_strategy": "semantic_merge",
            "confidence": 0.7,
            "rationale": (
                "Both sides added version entries; without seeing actual "
                "content, semantic merge is appropriate."
            ),
        }
        analysis = parse_conflict_analysis(raw, file_path="versions.ts")
        assert "without seeing actual content" not in analysis.rationale.lower()
        assert "[analyst lacked source access]" in analysis.rationale
        assert "Both sides added version entries" in analysis.rationale
        # conflict_point rationale is also sanitized
        assert (
            "without seeing actual content"
            not in analysis.conflict_points[0].rationale.lower()
        )

    def test_parse_commit_round_analyses_strips_hedging(self):
        from src.llm.response_parser import parse_commit_round_analyses

        raw = {
            "files": [
                {
                    "file_path": "x.ts",
                    "conflict_type": "concurrent_modification",
                    "recommended_strategy": "semantic_merge",
                    "confidence": 0.7,
                    "rationale": (
                        "Both sides changed; cannot confirm fork preservation."
                    ),
                    "upstream_intent": {
                        "description": (
                            "Unable to verify intent without seeing actual content."
                        ),
                        "intent_type": "refactor",
                    },
                    "fork_intent": {
                        "description": "Adds inst.week pattern.",
                        "intent_type": "feature",
                    },
                }
            ]
        }
        result = parse_commit_round_analyses(raw, file_paths=["x.ts"])
        analysis = result["x.ts"]
        assert "cannot confirm" not in analysis.rationale.lower()
        assert "[analyst lacked source access]" in analysis.rationale

        cp = analysis.conflict_points[0]
        assert "unable to verify" not in cp.upstream_intent.description.lower()
        assert (
            "without seeing actual content"
            not in cp.upstream_intent.description.lower()
        )
        # untouched description preserved verbatim
        assert cp.fork_intent.description == "Adds inst.week pattern."

    def test_parse_strategy_unchanged_after_sanitize(self):
        """Sanitizing rationale must not change the merge strategy."""
        from src.llm.response_parser import parse_conflict_analysis

        raw = {
            "conflict_type": "concurrent_modification",
            "recommended_strategy": "semantic_merge",
            "confidence": 0.7,
            "rationale": "without seeing actual content",
        }
        analysis = parse_conflict_analysis(raw, file_path="any.ts")
        assert analysis.recommended_strategy.value == "semantic_merge"
        assert analysis.confidence == 0.7
