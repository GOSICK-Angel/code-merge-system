"""Phase 3: `merge optimize-prompts` CLI wiring (real gate, no LLM)."""

from __future__ import annotations

import json

from click.testing import CliRunner

from src.cli.main import cli


def test_generate_variants_for_real_system_gate():
    res = CliRunner().invoke(
        cli, ["optimize-prompts", "--gate", "J-SYSTEM", "--strategies", "stepwise"]
    )
    assert res.exit_code == 0
    assert "NOT auto-applied" in res.output
    assert "`stepwise`" in res.output


def test_unknown_gate_exits_nonzero():
    res = CliRunner().invoke(cli, ["optimize-prompts", "--gate", "NOPE"])
    assert res.exit_code == 1
    assert "Unknown gate" in res.output


def test_scores_with_golden_and_rollouts(tmp_path):
    golden = tmp_path / "golden.json"
    golden.write_text(
        json.dumps(
            [
                {"case_id": "c1", "expected_decision": "take_target"},
                {"case_id": "c2", "expected_decision": "HUMAN_REQUIRED"},
            ]
        ),
        encoding="utf-8",
    )
    rollouts = tmp_path / "rollouts.json"
    rollouts.write_text(
        json.dumps(
            {
                "baseline": {"c1": "take_target", "c2": "take_target"},
                "stepwise": {"c1": "take_target", "c2": "HUMAN_REQUIRED"},
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "report.json"
    res = CliRunner().invoke(
        cli,
        [
            "optimize-prompts",
            "--gate",
            "J-SYSTEM",
            "--strategies",
            "stepwise",
            "--golden",
            str(golden),
            "--rollouts",
            str(rollouts),
            "--out",
            str(out),
        ],
    )
    assert res.exit_code == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["winner_id"] == "stepwise"  # 1.0 vs baseline 0.5
