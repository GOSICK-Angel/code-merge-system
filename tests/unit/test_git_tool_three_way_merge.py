"""Tests for ``GitTool.three_way_merge_file``.

Backs the P-γ-1.5-A fix: C-class files where fork and upstream edited
disjoint line ranges (e.g. fork ``author`` line 1 + upstream
``version`` line 37 in manifest.yaml) should resolve via git's native
3-way merge without invoking the LLM executor — which empirically
picks ``take_target`` and drops the fork change.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.tools.git_tool import GitTool


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(repo)],
        check=True,
        capture_output=True,
    )
    for k, v in (("user.email", "t@t"), ("user.name", "T")):
        subprocess.run(
            ["git", "config", k, v], cwd=str(repo), check=True, capture_output=True
        )
    return repo


def _commit(repo: Path, rel: str, content: str, msg: str) -> str:
    fpath = repo / rel
    fpath.parent.mkdir(parents=True, exist_ok=True)
    fpath.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", msg], cwd=str(repo), check=True, capture_output=True
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _branch_from(repo: Path, branch: str, base_sha: str) -> None:
    subprocess.run(
        ["git", "checkout", "-q", "-b", branch, base_sha],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )


_BASE_YAML = """\
author: langgenius
created_at: '2024-09-20'
description:
  en_US: Plugin
name: gaode
version: 0.0.2
"""

_FORK_YAML = """\
author: cvte
created_at: '2024-09-20'
description:
  en_US: Plugin
name: gaode
version: 0.0.2
"""

_UPSTREAM_YAML = """\
author: langgenius
created_at: '2024-09-20'
description:
  en_US: Plugin
name: gaode
version: 0.0.3
"""

_EXPECTED_CLEAN = """\
author: cvte
created_at: '2024-09-20'
description:
  en_US: Plugin
name: gaode
version: 0.0.3
"""


class TestThreeWayMergeClean:
    """t1-0003 / t1-0004 manifest.yaml shape — fork edits line 1, upstream
    edits last line. ``git merge-file`` resolves deterministically."""

    def test_disjoint_line_edits_merge_cleanly(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        base_sha = _commit(repo, "manifest.yaml", _BASE_YAML, "base")
        _branch_from(repo, "upstream", base_sha)
        _commit(repo, "manifest.yaml", _UPSTREAM_YAML, "upstream change")
        subprocess.run(
            ["git", "checkout", "-q", "main"],
            cwd=str(repo),
            check=True,
            capture_output=True,
        )
        _commit(repo, "manifest.yaml", _FORK_YAML, "fork change")

        gt = GitTool(str(repo))
        result = gt.three_way_merge_file(
            base_ref=base_sha,
            ours_ref="main",
            theirs_ref="upstream",
            file_path="manifest.yaml",
        )
        assert result == _EXPECTED_CLEAN

    def test_no_change_passthrough(self, tmp_path: Path) -> None:
        """All three sides identical — merge equals input (idempotent)."""
        repo = _init_repo(tmp_path)
        base_sha = _commit(repo, "a.txt", "hello\n", "base")
        _branch_from(repo, "upstream", base_sha)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "upstream noop"],
            cwd=str(repo),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "checkout", "-q", "main"],
            cwd=str(repo),
            check=True,
            capture_output=True,
        )

        gt = GitTool(str(repo))
        result = gt.three_way_merge_file(base_sha, "main", "upstream", "a.txt")
        assert result == "hello\n"


class TestThreeWayMergeConflict:
    """Conflicting line edits return ``None`` — caller must escalate."""

    def test_overlapping_edits_return_none(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        base_sha = _commit(repo, "f.py", "x = 1\ny = 2\n", "base")
        _branch_from(repo, "upstream", base_sha)
        _commit(repo, "f.py", "x = 99\ny = 2\n", "upstream")
        subprocess.run(
            ["git", "checkout", "-q", "main"],
            cwd=str(repo),
            check=True,
            capture_output=True,
        )
        _commit(repo, "f.py", "x = 42\ny = 2\n", "fork")

        gt = GitTool(str(repo))
        result = gt.three_way_merge_file(base_sha, "main", "upstream", "f.py")
        assert result is None

    def test_missing_ref_returns_none(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _commit(repo, "a.txt", "x\n", "base")
        gt = GitTool(str(repo))
        result = gt.three_way_merge_file(
            base_ref="nonexistent-ref",
            ours_ref="main",
            theirs_ref="main",
            file_path="a.txt",
        )
        assert result is None

    def test_missing_file_at_ref_returns_none(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _commit(repo, "exists.txt", "x\n", "base")
        gt = GitTool(str(repo))
        result = gt.three_way_merge_file(
            base_ref="main",
            ours_ref="main",
            theirs_ref="main",
            file_path="absent.txt",
        )
        assert result is None


class TestThreeWayMergeSideEffectFree:
    """The merge must not touch the worktree, index, or HEAD."""

    def test_no_worktree_mutation(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        base_sha = _commit(repo, "m.yaml", _BASE_YAML, "base")
        _branch_from(repo, "upstream", base_sha)
        _commit(repo, "m.yaml", _UPSTREAM_YAML, "upstream")
        subprocess.run(
            ["git", "checkout", "-q", "main"],
            cwd=str(repo),
            check=True,
            capture_output=True,
        )
        _commit(repo, "m.yaml", _FORK_YAML, "fork")

        before_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        before_status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo),
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        before_disk = (repo / "m.yaml").read_text(encoding="utf-8")

        gt = GitTool(str(repo))
        result = gt.three_way_merge_file(base_sha, "main", "upstream", "m.yaml")
        assert result == _EXPECTED_CLEAN

        after_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        after_status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo),
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        after_disk = (repo / "m.yaml").read_text(encoding="utf-8")

        assert after_head == before_head
        assert after_status == before_status
        assert after_disk == before_disk
