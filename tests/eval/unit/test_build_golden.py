"""Unit tests for the LLM-judgment golden-set builder (scripts/eval/_golden)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.eval._common import read_json
from scripts.eval._golden import (
    GATE_DECISION_VOCAB,
    GoldenBuildError,
    build_golden_sets,
)
from scripts.eval.build_golden import cmd_build
from src.tools.prompt_optimizer import GoldenCase


def _write_sample(
    root: Path,
    sample_id: str,
    *,
    tier: int = 1,
    category: str = "C",
    expected_human: bool = True,
    judgment_intensive: bool | None = None,
    golden_decisions: list[dict[str, str]] | None = None,
) -> None:
    sample_dir = root / "tier1" / "samples" / sample_id
    sample_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        f"sample_id: {sample_id}",
        f"tier: {tier}",
        f"category: {category}",
        f"expected_human: {str(expected_human).lower()}",
    ]
    if judgment_intensive is not None:
        lines.append(f"judgment_intensive: {str(judgment_intensive).lower()}")
    if golden_decisions is not None:
        lines.append("golden_decisions:")
        for entry in golden_decisions:
            lines.append(f"  - gate_id: {entry['gate_id']}")
            lines.append(f"    expected_decision: {entry['expected_decision']}")
    (sample_dir / "meta.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_groups_judgment_intensive_cases_by_gate(tmp_path: Path) -> None:
    _write_sample(
        tmp_path,
        "t1-9001",
        golden_decisions=[
            {"gate_id": "CA-SYSTEM", "expected_decision": "escalate_human"},
            {"gate_id": "P-RISK-SCORE-SYSTEM", "expected_decision": "human_required"},
        ],
        judgment_intensive=True,
    )

    result = build_golden_sets(tmp_path, tiers=(1,))

    assert set(result) == {"CA-SYSTEM", "P-RISK-SCORE-SYSTEM"}
    assert result["CA-SYSTEM"] == [
        GoldenCase(case_id="t1-9001", expected_decision="escalate_human")
    ]
    assert result["P-RISK-SCORE-SYSTEM"] == [
        GoldenCase(case_id="t1-9001", expected_decision="human_required")
    ]


def test_excludes_non_judgment_intensive_and_unlabelled(tmp_path: Path) -> None:
    # Plain sample with no field -> excluded.
    _write_sample(tmp_path, "t1-9001")
    # Explicitly false -> excluded even with decisions present.
    _write_sample(
        tmp_path,
        "t1-9002",
        judgment_intensive=False,
        golden_decisions=[
            {"gate_id": "CA-SYSTEM", "expected_decision": "escalate_human"}
        ],
    )

    assert build_golden_sets(tmp_path, tiers=(1,)) == {}


def test_judgment_intensive_without_decisions_is_noop(tmp_path: Path) -> None:
    _write_sample(tmp_path, "t1-9001", judgment_intensive=True)

    assert build_golden_sets(tmp_path, tiers=(1,)) == {}


def test_rejects_decision_outside_gate_vocabulary(tmp_path: Path) -> None:
    _write_sample(
        tmp_path,
        "t1-9001",
        judgment_intensive=True,
        golden_decisions=[{"gate_id": "CA-SYSTEM", "expected_decision": "pass"}],
    )

    with pytest.raises(GoldenBuildError, match="not a valid CA-SYSTEM decision"):
        build_golden_sets(tmp_path, tiers=(1,))


def test_rejects_unknown_gate(tmp_path: Path) -> None:
    _write_sample(
        tmp_path,
        "t1-9001",
        judgment_intensive=True,
        golden_decisions=[{"gate_id": "X-SYSTEM", "expected_decision": "fail"}],
    )

    with pytest.raises(GoldenBuildError, match="unknown golden gate"):
        build_golden_sets(tmp_path, tiers=(1,))


def test_output_is_deterministic_and_sorted(tmp_path: Path) -> None:
    for sid in ("t1-9003", "t1-9001", "t1-9002"):
        _write_sample(
            tmp_path,
            sid,
            judgment_intensive=True,
            golden_decisions=[{"gate_id": "J-SYSTEM", "expected_decision": "fail"}],
        )

    result = build_golden_sets(tmp_path, tiers=(1,))

    assert [c.case_id for c in result["J-SYSTEM"]] == ["t1-9001", "t1-9002", "t1-9003"]


def test_cmd_build_writes_optimize_prompts_golden_json(tmp_path: Path) -> None:
    _write_sample(
        tmp_path / "data",
        "t1-9001",
        judgment_intensive=True,
        golden_decisions=[
            {"gate_id": "CA-SYSTEM", "expected_decision": "escalate_human"}
        ],
    )
    out_dir = tmp_path / "golden"

    rc = cmd_build(tmp_path / "data", out_dir, tiers=(1,))

    assert rc == 0
    payload = read_json(out_dir / "CA-SYSTEM.golden.json")
    assert payload == [{"case_id": "t1-9001", "expected_decision": "escalate_human"}]
    # Shape must round-trip through the production GoldenCase validator.
    assert GoldenCase.model_validate(payload[0]).case_id == "t1-9001"


def test_cmd_build_writes_nothing_when_no_golden_cases(tmp_path: Path) -> None:
    _write_sample(tmp_path / "data", "t1-9001")
    out_dir = tmp_path / "golden"

    rc = cmd_build(tmp_path / "data", out_dir, tiers=(1,))

    assert rc == 0
    assert not out_dir.exists() or not list(out_dir.glob("*.json"))


def test_gate_vocab_tracks_production_enums() -> None:
    # Guards against drift: a renamed decision value must surface here. The
    # actionable decisions each gate emits for a judgment-intensive case must
    # stay valid; sentinel risk levels (binary / excluded) may also appear.
    assert GATE_DECISION_VOCAB["J-SYSTEM"] == frozenset({"pass", "conditional", "fail"})
    assert {"auto_safe", "auto_risky", "human_required"} <= GATE_DECISION_VOCAB[
        "P-RISK-SCORE-SYSTEM"
    ]
    assert "escalate_human" in GATE_DECISION_VOCAB["CA-SYSTEM"]


# --- real-dataset guards -----------------------------------------------------
#
# These read the committed dataset (not tmp_path) so an accidental meta.yaml
# edit, or a forgotten `python -m scripts.eval.build_golden` after one, fails
# CI instead of silently shipping a stale golden set. Mirrors `lock --verify`.


def test_real_dataset_covers_full_decision_face() -> None:
    from scripts.eval.build_golden import DEFAULT_DATASETS_DIR

    golden = build_golden_sets(DEFAULT_DATASETS_DIR, tiers=(1, 2, 3))
    seen = {
        gate_id: {case.expected_decision for case in cases}
        for gate_id, cases in golden.items()
    }
    # Both escalation (negative) and auto-merge (positive) faces are seeded,
    # and the judge gate covers both its pass and fail verdicts.
    assert {"pass", "fail"} <= seen["J-SYSTEM"]
    assert {"auto_safe", "auto_risky", "human_required"} <= seen["P-RISK-SCORE-SYSTEM"]
    assert {"semantic_merge", "escalate_human"} <= seen["CA-SYSTEM"]


def test_committed_golden_json_in_sync_with_meta() -> None:
    from scripts.eval.build_golden import DEFAULT_DATASETS_DIR, DEFAULT_OUT_DIR

    golden = build_golden_sets(DEFAULT_DATASETS_DIR, tiers=(1, 2, 3))
    for gate_id, cases in golden.items():
        on_disk = read_json(DEFAULT_OUT_DIR / f"{gate_id}.golden.json")
        expected = [case.model_dump(mode="json") for case in cases]
        assert on_disk == expected, (
            f"{gate_id}.golden.json is stale; "
            "re-run `python -m scripts.eval.build_golden`"
        )
