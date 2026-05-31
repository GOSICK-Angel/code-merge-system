"""Tests for ``scripts.eval._fork_name_check``."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.eval._fork_name_check import (
    FORBIDDEN_TOKENS,
    main,
    scan_paths,
)


def _project_root_with(structure: dict[str, str], tmp_path: Path) -> Path:
    """Build a fake project tree under ``tmp_path`` and return its root.

    Keys are POSIX-style relative paths; values are file contents.
    """
    for rel, content in structure.items():
        target = tmp_path / Path(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return tmp_path


class TestScanPaths:
    def test_returns_empty_list_when_clean(self, tmp_path: Path) -> None:
        root = _project_root_with({"scripts/eval/clean.py": "x = 1\n"}, tmp_path)
        assert scan_paths([root / "scripts"], project_root=root) == []

    @pytest.mark.parametrize("token", FORBIDDEN_TOKENS)
    def test_flags_each_forbidden_token(self, tmp_path: Path, token: str) -> None:
        root = _project_root_with(
            {"scripts/eval/dirty.py": f'NAME = "{token}-fork"\n'}, tmp_path
        )
        hits = scan_paths([root / "scripts"], project_root=root)
        assert len(hits) == 1
        assert hits[0].path.name == "dirty.py"
        assert token in hits[0].snippet.lower()

    def test_word_boundary_does_not_match_substring(self, tmp_path: Path) -> None:
        # cvtemp / modify / insforgery are NOT forbidden tokens.
        root = _project_root_with(
            {
                "scripts/eval/safe.py": (
                    "MEMO = 'cvtemp results, modify later, insforgery'\n"
                )
            },
            tmp_path,
        )
        assert scan_paths([root / "scripts"], project_root=root) == []

    def test_case_insensitive(self, tmp_path: Path) -> None:
        root = _project_root_with({"scripts/eval/upper.py": "X = 'CVTE'\n"}, tmp_path)
        hits = scan_paths([root / "scripts"], project_root=root)
        assert len(hits) == 1

    def test_skips_fixture_whitelist_paths(self, tmp_path: Path) -> None:
        root = _project_root_with(
            {
                "tests/eval/datasets/tier1/sample.txt": "cvte\n",
                "tests/eval/fixtures/dummy.txt": "dify\n",
            },
            tmp_path,
        )
        assert (
            scan_paths(
                [
                    root / "tests" / "eval" / "datasets",
                    root / "tests" / "eval" / "fixtures",
                ],
                project_root=root,
            )
            == []
        )

    def test_does_not_skip_other_test_subpaths(self, tmp_path: Path) -> None:
        root = _project_root_with(
            {"tests/eval/unit/test_something.py": "X = 'cvte'\n"},
            tmp_path,
        )
        hits = scan_paths([root / "tests" / "eval"], project_root=root)
        assert len(hits) == 1
        assert hits[0].path.name == "test_something.py"

    def test_only_supported_suffixes_scanned(self, tmp_path: Path) -> None:
        root = _project_root_with(
            {"scripts/eval/data.bin": "cvte\n", "scripts/eval/README.md": "cvte\n"},
            tmp_path,
        )
        hits = scan_paths([root / "scripts"], project_root=root)
        # .md is supported, .bin is not.
        assert len(hits) == 1
        assert hits[0].path.name == "README.md"

    def test_skips_self_basename_even_if_passed_directly(self, tmp_path: Path) -> None:
        # Simulate a copy of the checker living somewhere unrelated.
        root = _project_root_with(
            {"scripts/eval/_fork_name_check.py": 'TOKENS = ("cvte",)\n'},
            tmp_path,
        )
        assert (
            scan_paths(
                [root / "scripts" / "eval" / "_fork_name_check.py"], project_root=root
            )
            == []
        )

    def test_real_repo_scripts_eval_passes(self) -> None:
        """End-to-end against the real repository — must stay green forever.

        This is the canonical Phase 0 GO-condition assertion.
        """
        repo_root = Path(__file__).resolve().parents[3]
        targets = [repo_root / "scripts" / "eval", repo_root / "tests" / "eval"]
        assert scan_paths(targets, project_root=repo_root) == []


class TestMainCli:
    def test_clean_returns_zero(self, tmp_path: Path) -> None:
        _project_root_with({"scripts/eval/x.py": "a = 1\n"}, tmp_path)
        rc = main([str(tmp_path / "scripts"), "--project-root", str(tmp_path)])
        assert rc == 0

    def test_dirty_returns_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _project_root_with({"scripts/eval/dirty.py": "X = 'cvte'\n"}, tmp_path)
        rc = main([str(tmp_path / "scripts"), "--project-root", str(tmp_path)])
        assert rc == 1
        captured = capsys.readouterr()
        assert "forbidden match" in captured.err.lower()
