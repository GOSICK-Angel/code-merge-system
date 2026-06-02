"""Phase B (P-γ-3) TB-U-01 .. TB-U-07 — R2 SampleMeta validation.

Maps to test/FINAL.md §2.2:
- TB-U-01/02/03: SampleMeta accepts valid r2 meta.yaml shapes (B/D/E)
- TB-U-04: --from-merge ref derivation via subprocess.run mock
- TB-U-06/07: tier range validator (pydantic v2 Field(ge=1, le=3))
"""

from __future__ import annotations

import subprocess

import pytest
import yaml
from pydantic import ValidationError
from pytest_mock import MockerFixture

from scripts.eval._schemas import SampleMeta


def _r2_meta(category: str = "B", tier: int = 2) -> dict[str, object]:
    return {
        "sample_id": "r2-0002",
        "tier": tier,
        "category": category,
        "loss_class": None,
        "expected_human": False,
        "description": "synthetic test fixture",
    }


def test_TB_U_01_meta_b_parses_clean() -> None:
    meta = SampleMeta.model_validate(_r2_meta(category="B"))
    assert meta.category == "B"
    assert meta.tier == 2
    assert meta.expected_human is False


def test_TB_U_02_meta_d_freetext_accepted() -> None:
    # category is free-text str (locks v2 [test]-4): D-style values pass through
    meta = SampleMeta.model_validate(_r2_meta(category="D"))
    assert meta.category == "D"


def test_TB_U_03_meta_e_freetext_accepted() -> None:
    meta = SampleMeta.model_validate(_r2_meta(category="E"))
    assert meta.category == "E"


def test_TB_U_06_tier_above_range_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        SampleMeta.model_validate(_r2_meta(tier=4))
    assert "tier" in str(exc.value)
    assert "less_than_equal" in str(exc.value) or "<=" in str(exc.value)


def test_TB_U_07_tier_below_range_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        SampleMeta.model_validate(_r2_meta(tier=0))
    assert "tier" in str(exc.value)
    assert "greater_than_equal" in str(exc.value) or ">=" in str(exc.value)


def test_TB_U_04_from_merge_ref_derivation(mocker: MockerFixture) -> None:
    """Mock subprocess.run to verify the 4-ref derivation rule:

        base = git merge-base ^1 ^2
        upstream = ^2
        fork = ^1
        golden = <merge-sha>

    (sample_import.py:11-21 docstring contract; locks v1 [plan] §C1)
    """

    def fake_git_run(
        cmd: list[str] | str, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        args = cmd if isinstance(cmd, list) else cmd.split()
        verb_idx = next(i for i, a in enumerate(args) if a == "git") + 3
        verb = args[verb_idx]
        rest = args[verb_idx + 1 :]
        out = subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if verb == "rev-parse":
            ref = rest[-1].replace("^{commit}", "")
            mapping = {
                "<sha>^1": "fffffff1111111111111111111111111111111111",
                "<sha>^2": "fffffff2222222222222222222222222222222222",
                "<sha>": "ffffff00000000000000000000000000000000000",
            }
            out.stdout = mapping.get(ref, ref) + "\n"
        elif verb == "merge-base":
            out.stdout = "bbbbbb00000000000000000000000000000000000\n"
        return out

    mocker.patch("scripts.eval.sample_import.subprocess.run", side_effect=fake_git_run)
    from scripts.eval import sample_import as si
    from argparse import Namespace
    from pathlib import Path

    args = Namespace(
        repo="/fake/repo",
        from_merge="<sha>",
        base_ref=None,
        upstream_ref=None,
        fork_ref=None,
        golden_ref=None,
    )
    refs = si._resolve_refs(Path("/fake/repo"), args)
    assert refs.base.startswith("bbbbbb")
    assert refs.upstream.startswith("fffffff2")
    assert refs.fork.startswith("fffffff1")
    assert refs.golden.startswith("ffffff0")


def test_r2_committed_samples_parse_clean() -> None:
    """Smoke test: every committed r2 sample's meta.yaml parses to SampleMeta."""
    from pathlib import Path

    root = (
        Path(__file__).resolve().parents[3]
        / "tests"
        / "eval"
        / "datasets"
        / "r2"
        / "samples"
    )
    sample_dirs = sorted(p for p in root.iterdir() if p.is_dir())
    assert len(sample_dirs) >= 5, f"expected >=5 r2 samples, found {len(sample_dirs)}"
    for sd in sample_dirs:
        meta = yaml.safe_load((sd / "meta.yaml").read_text())
        parsed = SampleMeta.model_validate(meta)
        assert parsed.tier == 2
        assert parsed.category in {"A", "B", "C", "D", "E"}
