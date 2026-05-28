"""PR-B Slice 3: parser reads semantic_compatibility + non-fatal channel.

Both parser entry points (single-file `parse_conflict_analysis` and the
commit-round batched `parse_commit_round_analyses`) must:

  1. read the LLM's `semantic_compatibility` value into the model
  2. append a `grounding_warnings` entry when the field is missing, so
     reviewers see "the LLM didn't tell us how the changes interact"
     without the whole round bisecting
  3. append a `grounding_warnings` entry when intent descriptions are
     suspiciously short (< 10 chars) — that's the "comparable changes"
     vagueness anti-pattern

We deliberately do NOT raise ModelOutputError: a single missing field
should not abort the analyst round (precedent: PR-A / PR-D-A.2 used
the same warning channel).
"""

from __future__ import annotations

import json

import pytest

from src.llm.response_parser import (
    parse_commit_round_analyses,
    parse_conflict_analysis,
)


def _base_payload(
    *,
    semantic_compatibility: str | None = "compatible",
    upstream_desc: str = "Added a strict ISO parser to handle leap seconds",
    fork_desc: str = "Renamed parseDate to parseISODate for clarity",
    rationale: str = "Fork rename and upstream addition touch different code paths",
) -> dict:
    body: dict = {
        "conflict_type": "concurrent_modification",
        "recommended_strategy": "semantic_merge",
        "confidence": 0.8,
        "can_coexist": True,
        "rationale": rationale,
        "upstream_intent": {
            "description": upstream_desc,
            "intent_type": "feature",
            "confidence": 0.8,
        },
        "fork_intent": {
            "description": fork_desc,
            "intent_type": "refactor",
            "confidence": 0.8,
        },
    }
    if semantic_compatibility is not None:
        body["semantic_compatibility"] = semantic_compatibility
    return body


class TestParseConflictAnalysisSemanticCompatibility:
    @pytest.mark.parametrize("value", ["compatible", "incompatible", "orthogonal"])
    def test_reads_three_states(self, value: str) -> None:
        raw = json.dumps(_base_payload(semantic_compatibility=value))
        ca = parse_conflict_analysis(raw, file_path="src/x.ts")
        assert ca.semantic_compatibility == value

    def test_missing_field_records_warning(self) -> None:
        raw = json.dumps(_base_payload(semantic_compatibility=None))
        ca = parse_conflict_analysis(raw, file_path="src/x.ts")
        assert ca.semantic_compatibility is None
        joined = "\n".join(ca.grounding_warnings)
        assert "semantic_compatibility" in joined

    def test_unknown_value_records_warning_and_drops_to_none(self) -> None:
        raw = json.dumps(_base_payload(semantic_compatibility="maybe"))
        ca = parse_conflict_analysis(raw, file_path="src/x.ts")
        assert ca.semantic_compatibility is None
        joined = "\n".join(ca.grounding_warnings)
        assert "semantic_compatibility" in joined

    def test_short_intent_descriptions_record_warning(self) -> None:
        raw = json.dumps(
            _base_payload(
                upstream_desc="upgrade",
                fork_desc="refactor",
            )
        )
        ca = parse_conflict_analysis(raw, file_path="src/x.ts")
        joined = "\n".join(ca.grounding_warnings)
        assert "vague" in joined.lower() or "short" in joined.lower()


class TestParseCommitRoundSemanticCompatibility:
    def _round_payload(self, **overrides) -> str:
        entry = _base_payload(**overrides)
        entry["file_path"] = "src/x.ts"
        return json.dumps({"files": [entry]})

    @pytest.mark.parametrize("value", ["compatible", "incompatible", "orthogonal"])
    def test_reads_three_states(self, value: str) -> None:
        raw = self._round_payload(semantic_compatibility=value)
        out = parse_commit_round_analyses(raw, file_paths=["src/x.ts"])
        assert out["src/x.ts"].semantic_compatibility == value

    def test_missing_field_records_warning(self) -> None:
        raw = self._round_payload(semantic_compatibility=None)
        out = parse_commit_round_analyses(raw, file_paths=["src/x.ts"])
        ca = out["src/x.ts"]
        assert ca.semantic_compatibility is None
        joined = "\n".join(ca.grounding_warnings)
        assert "semantic_compatibility" in joined
