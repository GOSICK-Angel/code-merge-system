"""Unit tests for the unified decisions schema (§7.2.2) and loader (§7.1.1).

Covers:
- ``parse_bundle`` accepts V2 (``version: 2`` + ``rounds``) and falls back
  to V1 single-round wrapping.
- V1 phase inference picks plan_review / conflict_resolution /
  judge_review based on which top-level keys are present.
- ``DecisionsBundle.take_round`` consumes the first matching round.
"""

from __future__ import annotations

from src.models.decisions_bundle import (
    DecisionPhase,
    DecisionsBundle,
    parse_bundle,
)


class TestParseBundleV2:
    def test_full_v2_bundle_round_trips(self):
        raw = {
            "version": 2,
            "rounds": [
                {
                    "phase": "plan_review",
                    "plan_approval": "approve",
                    "item_decisions": [
                        {"file_path": "a.py", "user_choice": "downgrade_risky"}
                    ],
                },
                {
                    "phase": "conflict_resolution",
                    "decisions": [{"file_path": "b.py", "decision": "take_target"}],
                },
                {"phase": "judge_review", "judge_resolution": "accept"},
            ],
        }
        bundle = parse_bundle(raw)
        assert bundle.version == 2
        assert len(bundle.rounds) == 3
        assert bundle.rounds[0].phase == DecisionPhase.PLAN_REVIEW
        assert bundle.rounds[0].plan_approval == "approve"
        assert bundle.rounds[0].item_decisions[0].user_choice == "downgrade_risky"
        assert bundle.rounds[1].decisions[0].decision == "take_target"
        assert bundle.rounds[2].judge_resolution == "accept"

    def test_take_round_consumes_first_match(self):
        bundle = parse_bundle(
            {
                "version": 2,
                "rounds": [
                    {"phase": "plan_review", "plan_approval": "approve"},
                    {"phase": "judge_review", "judge_resolution": "accept"},
                ],
            }
        )
        rnd = bundle.take_round(DecisionPhase.PLAN_REVIEW)
        assert rnd is not None and rnd.plan_approval == "approve"
        assert len(bundle.rounds) == 1
        assert bundle.take_round(DecisionPhase.PLAN_REVIEW) is None  # consumed

    def test_peek_round_does_not_consume(self):
        bundle = parse_bundle(
            {
                "version": 2,
                "rounds": [{"phase": "plan_review", "plan_approval": "approve"}],
            }
        )
        rnd1 = bundle.peek_round(DecisionPhase.PLAN_REVIEW)
        rnd2 = bundle.peek_round(DecisionPhase.PLAN_REVIEW)
        assert rnd1 is not None and rnd2 is not None
        assert len(bundle.rounds) == 1


class TestParseBundleV2AutoDetect:
    """A ``rounds:`` document must parse as V2 even when ``version: 2`` is
    omitted — otherwise the whole bundle silently collapses into a single
    empty plan_review round and the operator's decisions are dropped with no
    error (the failure mode observed in the zod merge test)."""

    def test_rounds_without_version_field_parses_as_v2(self):
        raw = {
            "rounds": [
                {
                    "phase": "conflict_resolution",
                    "decisions": [{"file_path": "b.py", "decision": "take_target"}],
                }
            ]
        }
        bundle = parse_bundle(raw)
        assert len(bundle.rounds) == 1
        assert bundle.rounds[0].phase == DecisionPhase.CONFLICT_RESOLUTION
        assert bundle.rounds[0].decisions[0].file_path == "b.py"
        assert bundle.rounds[0].decisions[0].decision == "take_target"

    def test_multi_round_without_version_field_preserved(self):
        raw = {
            "rounds": [
                {"phase": "plan_review", "plan_approval": "approve"},
                {"phase": "judge_review", "judge_resolution": "accept"},
            ]
        }
        bundle = parse_bundle(raw)
        assert len(bundle.rounds) == 2
        assert bundle.rounds[1].judge_resolution == "accept"


class TestParseBundleV1:
    def test_v1_plan_only_infers_plan_review(self):
        raw = {
            "plan_approval": "approve",
            "item_decisions": [{"file_path": "x.py", "user_choice": "downgrade_risky"}],
        }
        bundle = parse_bundle(raw)
        assert len(bundle.rounds) == 1
        assert bundle.rounds[0].phase == DecisionPhase.PLAN_REVIEW
        assert bundle.rounds[0].plan_approval == "approve"

    def test_v1_decisions_only_infers_conflict_resolution(self):
        raw = {"decisions": [{"file_path": "y.py", "decision": "take_target"}]}
        bundle = parse_bundle(raw)
        assert bundle.rounds[0].phase == DecisionPhase.CONFLICT_RESOLUTION

    def test_v1_judge_resolution_only_infers_judge_review(self):
        raw = {"judge_resolution": "accept"}
        bundle = parse_bundle(raw)
        assert bundle.rounds[0].phase == DecisionPhase.JUDGE_REVIEW

    def test_empty_dict_yields_single_default_round(self):
        bundle = parse_bundle({})
        assert len(bundle.rounds) == 1
        assert bundle.rounds[0].phase == DecisionPhase.PLAN_REVIEW

    def test_v1_skips_invalid_item_dicts(self):
        raw = {
            "item_decisions": [
                {"file_path": "ok.py", "user_choice": "x"},
                {"user_choice": "no_path"},  # missing file_path
                "not a dict",
            ]
        }
        bundle = parse_bundle(raw)
        assert len(bundle.rounds[0].item_decisions) == 1
        assert bundle.rounds[0].item_decisions[0].file_path == "ok.py"


class TestBundleEdgeCases:
    def test_take_round_returns_none_for_empty_bundle(self):
        bundle = DecisionsBundle(rounds=[])
        assert bundle.take_round(DecisionPhase.PLAN_REVIEW) is None
