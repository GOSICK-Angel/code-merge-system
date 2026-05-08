"""Unit tests for the first-run wizard's forks-profile draft offer.

`_offer_forks_profile_draft` is called once during interactive setup
after the config has been written. It surveys the fork's deleted-file
count and, when the divergence crosses
``FORKS_PROFILE_INIT_THRESHOLD``, asks the user whether to generate a
draft yaml + open it in ``$EDITOR``.

The trigger uses ``git diff --diff-filter=D`` directly so the prompt
runs in milliseconds even on large repos; the heavyweight drafter
only fires after explicit user consent.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from src.cli.commands.setup import (
    FORKS_PROFILE_INIT_THRESHOLD,
    _offer_forks_profile_draft,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _init_repo_with_n_deletions(repo: Path, n_deleted: int) -> tuple[str, str]:
    """Build a repo where the fork branch has deleted ``n_deleted`` files.

    Returns ``(upstream_ref, fork_ref)`` so the test can call the offer
    function with the same refs the real wizard would use.
    """
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


class TestThresholdGate:
    def test_below_threshold_silently_skips(self, tmp_path: Path):
        upstream, fork = _init_repo_with_n_deletions(
            tmp_path, FORKS_PROFILE_INIT_THRESHOLD - 5
        )
        with patch("src.cli.commands.setup.Confirm.ask") as mock_confirm:
            _offer_forks_profile_draft(upstream, fork, str(tmp_path))
        mock_confirm.assert_not_called()
        assert not (tmp_path / ".merge" / "forks-profile.yaml").exists()

    def test_at_or_above_threshold_prompts_user(self, tmp_path: Path):
        upstream, fork = _init_repo_with_n_deletions(
            tmp_path, FORKS_PROFILE_INIT_THRESHOLD + 2
        )
        with patch(
            "src.cli.commands.setup.Confirm.ask", return_value=False
        ) as mock_confirm:
            _offer_forks_profile_draft(upstream, fork, str(tmp_path))
        mock_confirm.assert_called_once()


class TestExistingProfileShortCircuits:
    def test_skips_when_profile_already_exists(self, tmp_path: Path):
        upstream, fork = _init_repo_with_n_deletions(
            tmp_path, FORKS_PROFILE_INIT_THRESHOLD + 5
        )
        merge_dir = tmp_path / ".merge"
        merge_dir.mkdir()
        (merge_dir / "forks-profile.yaml").write_text("version: 1\n", encoding="utf-8")
        with patch("src.cli.commands.setup.Confirm.ask") as mock_confirm:
            _offer_forks_profile_draft(upstream, fork, str(tmp_path))
        mock_confirm.assert_not_called()


class TestUserDeclines:
    def test_no_decision_does_not_write_yaml(self, tmp_path: Path):
        upstream, fork = _init_repo_with_n_deletions(
            tmp_path, FORKS_PROFILE_INIT_THRESHOLD + 5
        )
        with patch("src.cli.commands.setup.Confirm.ask", return_value=False):
            _offer_forks_profile_draft(upstream, fork, str(tmp_path))
        assert not (tmp_path / ".merge" / "forks-profile.yaml").exists()


class TestUserAccepts:
    def test_yes_writes_yaml_and_invokes_editor(self, tmp_path: Path):
        upstream, fork = _init_repo_with_n_deletions(
            tmp_path, FORKS_PROFILE_INIT_THRESHOLD + 5
        )
        with (
            patch("src.cli.commands.setup.Confirm.ask", return_value=True),
            patch("click.edit") as mock_edit,
        ):
            _offer_forks_profile_draft(upstream, fork, str(tmp_path))
        profile = tmp_path / ".merge" / "forks-profile.yaml"
        assert profile.exists()
        text = profile.read_text(encoding="utf-8")
        assert "version: 1" in text
        # Auto-drafted is the canonical header from render_profile_yaml —
        # confirms we go through the production drafter, not a test path.
        assert "Auto-drafted" in text
        mock_edit.assert_called_once()
        kwargs = mock_edit.call_args.kwargs
        assert kwargs.get("filename") == str(profile)

    def test_yes_with_editor_failure_still_keeps_yaml(self, tmp_path: Path):
        upstream, fork = _init_repo_with_n_deletions(
            tmp_path, FORKS_PROFILE_INIT_THRESHOLD + 5
        )
        with (
            patch("src.cli.commands.setup.Confirm.ask", return_value=True),
            patch("click.edit", side_effect=RuntimeError("no editor")),
        ):
            _offer_forks_profile_draft(upstream, fork, str(tmp_path))
        profile = tmp_path / ".merge" / "forks-profile.yaml"
        assert profile.exists()


class TestGitFailureSilentlySkips:
    def test_non_git_repo_does_not_raise(self, tmp_path: Path):
        # No `git init` — `git merge-base` will fail.
        with patch("src.cli.commands.setup.Confirm.ask") as mock_confirm:
            _offer_forks_profile_draft("upstream/main", "HEAD", str(tmp_path))
        mock_confirm.assert_not_called()
