"""Unit tests for the non-interactive forks-profile drafter.

``draft_forks_profile_file`` is the post-PR-3 entry point that the
Web UI launcher calls when the user ticks the "draft forks-profile"
checkbox during setup. The previous interactive
``_offer_forks_profile_draft`` + ``_draft_and_open_editor`` pair was
removed when the browser took over the wizard.

These tests pin three contracts the launcher relies on:
- Existing ``.merge/forks-profile.yaml`` is never overwritten
  (returns ``None``, leaves the file untouched).
- A real draft writes a yaml with the canonical "Auto-drafted"
  header — confirming we go through ``render_profile_yaml`` and not
  some stubbed code path.
- Git failures raise (the launcher catches and logs).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.cli.commands.setup import (
    FORKS_PROFILE_INIT_THRESHOLD,
    draft_forks_profile_file,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _init_repo_with_n_deletions(repo: Path, n_deleted: int) -> tuple[str, str]:
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "Test")

    base_dir = repo / "svc"
    base_dir.mkdir()
    for i in range(n_deleted):
        (base_dir / f"file_{i}.py").write_text(f"# file {i}\n", encoding="utf-8")
    (repo / "README.md").write_text("readme\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "merge-base")

    _git(repo, "checkout", "-q", "-b", "upstream-main")
    (repo / "upstream_only.py").write_text("u\n", encoding="utf-8")
    _git(repo, "add", "upstream_only.py")
    _git(repo, "commit", "-q", "-m", "upstream extends")

    _git(repo, "checkout", "-q", "-b", "fork-main", "main")
    if n_deleted:
        _git(repo, "rm", "-rq", "svc")
        _git(repo, "commit", "-q", "-m", f"fork drops {n_deleted} files")

    return "upstream-main", "fork-main"


class TestDraftForksProfileFile:
    def test_writes_yaml_when_not_present(self, tmp_path: Path) -> None:
        upstream, fork = _init_repo_with_n_deletions(
            tmp_path, FORKS_PROFILE_INIT_THRESHOLD + 5
        )
        out = draft_forks_profile_file(upstream, fork, str(tmp_path))
        assert out is not None
        text = out.read_text(encoding="utf-8")
        assert "version: 1" in text
        assert "Auto-drafted" in text

    def test_returns_none_when_profile_already_exists(self, tmp_path: Path) -> None:
        upstream, fork = _init_repo_with_n_deletions(
            tmp_path, FORKS_PROFILE_INIT_THRESHOLD + 5
        )
        merge_dir = tmp_path / ".merge"
        merge_dir.mkdir()
        existing = merge_dir / "forks-profile.yaml"
        existing.write_text("version: 1\n# preserved\n", encoding="utf-8")

        out = draft_forks_profile_file(upstream, fork, str(tmp_path))

        assert out is None
        assert existing.read_text(encoding="utf-8") == "version: 1\n# preserved\n"

    def test_raises_on_non_git_repo(self, tmp_path: Path) -> None:
        # No `git init` — the underlying GitTool / merge-base call
        # cannot succeed. ``draft_forks_profile_file`` propagates the
        # error so the launcher's try/except can log it and continue.
        with pytest.raises(Exception):
            draft_forks_profile_file("upstream/main", "HEAD", str(tmp_path))
