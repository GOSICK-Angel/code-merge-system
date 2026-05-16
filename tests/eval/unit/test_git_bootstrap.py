"""Tests for ``scripts.eval.git_bootstrap``.

Synthesises a minimal sample on the fly (base.tar + two patches) and
verifies that :func:`bootstrap_synthetic_repo` builds a 3-branch git
repository ready to feed the real ``merge`` CLI.
"""

from __future__ import annotations

import io
import subprocess
import tarfile
from pathlib import Path

import pytest

from scripts.eval import git_bootstrap as gb


FIXED_MTIME = 1767225600


def _make_tar(target: Path, files: dict[str, bytes]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(target, "w", format=tarfile.USTAR_FORMAT) as tf:
        for name in sorted(files):
            data = files[name]
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mtime = FIXED_MTIME
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(data))


def _write_sample(
    sample_dir: Path,
    *,
    base_files: dict[str, bytes],
    upstream_patch: str = "",
    fork_patch: str = "",
) -> None:
    sample_dir.mkdir(parents=True, exist_ok=True)
    _make_tar(sample_dir / "base.tar", base_files)
    # golden is unused by bootstrap but the schema includes it
    _make_tar(sample_dir / "golden.tar", base_files)
    (sample_dir / "upstream.patch").write_text(upstream_patch, encoding="utf-8")
    (sample_dir / "fork.patch").write_text(fork_patch, encoding="utf-8")
    (sample_dir / "meta.yaml").write_text(
        "sample_id: t1-0001\ntier: 1\ncategory: C\nexpected_human: false\n",
        encoding="utf-8",
    )


# Standard one-file patches reused across cases. The hunk replaces the
# only line in hello.txt so the diff is unambiguous regardless of git
# version.
_UPSTREAM_PATCH = (
    "--- a/hello.txt\n+++ b/hello.txt\n@@ -1 +1 @@\n-base content\n+upstream content\n"
)
_FORK_PATCH = (
    "--- a/hello.txt\n+++ b/hello.txt\n@@ -1 +1 @@\n-base content\n+fork content\n"
)


class TestBootstrap:
    def test_three_branch_layout(self, tmp_path: Path) -> None:
        sample = tmp_path / "sample"
        _write_sample(
            sample,
            base_files={"hello.txt": b"base content\n"},
            upstream_patch=_UPSTREAM_PATCH,
            fork_patch=_FORK_PATCH,
        )
        target = tmp_path / "repo"
        refs = gb.bootstrap_synthetic_repo(sample, target)

        assert refs.base != refs.upstream
        assert refs.base != refs.fork
        assert refs.upstream != refs.fork

        # main = fork branch (default checkout)
        head = subprocess.check_output(
            ["git", "-C", str(target), "rev-parse", "HEAD"], text=True
        ).strip()
        assert head == refs.fork

        # main file content is the fork-side value
        assert (target / "hello.txt").read_text() == "fork content\n"

        # upstream branch exists with the upstream commit at its tip
        upstream_head = subprocess.check_output(
            ["git", "-C", str(target), "rev-parse", "upstream"], text=True
        ).strip()
        assert upstream_head == refs.upstream

    def test_gitignore_blocks_merge_runtime(self, tmp_path: Path) -> None:
        sample = tmp_path / "sample"
        _write_sample(
            sample,
            base_files={"a.txt": b"x\n"},
            upstream_patch="",
            fork_patch="",
        )
        target = tmp_path / "repo"
        gb.bootstrap_synthetic_repo(sample, target)
        gitignore = (target / ".gitignore").read_text(encoding="utf-8")
        assert ".merge/" in gitignore
        # Drop a fake merge artifact and confirm git status ignores it.
        (target / ".merge").mkdir()
        (target / ".merge" / "runs").mkdir()
        status = subprocess.check_output(
            ["git", "-C", str(target), "status", "--porcelain"], text=True
        )
        assert ".merge" not in status

    def test_empty_patches_use_allow_empty(self, tmp_path: Path) -> None:
        sample = tmp_path / "sample"
        _write_sample(
            sample,
            base_files={"only.txt": b"unchanged\n"},
            upstream_patch="",
            fork_patch="",
        )
        target = tmp_path / "repo"
        refs = gb.bootstrap_synthetic_repo(sample, target)
        # All three shas still distinct (empty commits get unique shas
        # because the commit message + parent linkage differ).
        assert len({refs.base, refs.upstream, refs.fork}) == 3
        # Both refs point at the same tree as base.
        for branch in ("upstream", "main"):
            tree = subprocess.check_output(
                ["git", "-C", str(target), "rev-parse", f"{branch}^{{tree}}"], text=True
            ).strip()
            base_tree = subprocess.check_output(
                ["git", "-C", str(target), "rev-parse", f"{refs.base}^{{tree}}"],
                text=True,
            ).strip()
            assert tree == base_tree

    def test_one_sided_upstream(self, tmp_path: Path) -> None:
        """Real samples are commonly one-sided — fork untouched, upstream changes.

        Mirrors the typical monorepo plugin shape where one plugin subtree
        is touched only by the upstream side of the merge.
        """
        sample = tmp_path / "sample"
        _write_sample(
            sample,
            base_files={"hello.txt": b"base content\n"},
            upstream_patch=_UPSTREAM_PATCH,
            fork_patch="",
        )
        target = tmp_path / "repo"
        gb.bootstrap_synthetic_repo(sample, target)
        # main (fork) still has base content; upstream branch has new content.
        assert (target / "hello.txt").read_text() == "base content\n"
        upstream_blob = subprocess.check_output(
            ["git", "-C", str(target), "show", "upstream:hello.txt"], text=True
        )
        assert upstream_blob == "upstream content\n"


class TestErrors:
    def test_missing_artifact_rejected(self, tmp_path: Path) -> None:
        sample = tmp_path / "sample"
        sample.mkdir()
        # only base.tar, no patches
        _make_tar(sample / "base.tar", {"a.txt": b"x"})
        with pytest.raises(FileNotFoundError):
            gb.bootstrap_synthetic_repo(sample, tmp_path / "repo")

    def test_non_empty_target_rejected(self, tmp_path: Path) -> None:
        sample = tmp_path / "sample"
        _write_sample(sample, base_files={"a.txt": b"x\n"})
        target = tmp_path / "repo"
        target.mkdir()
        (target / "preexisting").write_text("x", encoding="utf-8")
        with pytest.raises(ValueError, match="non-empty"):
            gb.bootstrap_synthetic_repo(sample, target)

    def test_unsafe_tar_path_rejected(self, tmp_path: Path) -> None:
        sample = tmp_path / "sample"
        sample.mkdir()
        # synth tar with traversal path
        tar = sample / "base.tar"
        with tarfile.open(tar, "w", format=tarfile.USTAR_FORMAT) as tf:
            info = tarfile.TarInfo(name="../escape.txt")
            info.size = 1
            tf.addfile(info, io.BytesIO(b"x"))
        (sample / "upstream.patch").write_text("", encoding="utf-8")
        (sample / "fork.patch").write_text("", encoding="utf-8")
        with pytest.raises(ValueError, match="unsafe path"):
            gb.bootstrap_synthetic_repo(sample, tmp_path / "repo")

    def test_corrupt_patch_returns_nonzero_cli(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        sample = tmp_path / "sample"
        _write_sample(
            sample,
            base_files={"hello.txt": b"x\n"},
            upstream_patch="this is not a valid unified diff\n",
            fork_patch="",
        )
        rc = gb.main(["--sample", str(sample), "--out", str(tmp_path / "repo")])
        assert rc == 1
        assert "git_bootstrap" in capsys.readouterr().err


class TestCLI:
    def test_happy_cli_invocation(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        sample = tmp_path / "sample"
        _write_sample(
            sample,
            base_files={"hello.txt": b"base content\n"},
            upstream_patch=_UPSTREAM_PATCH,
            fork_patch=_FORK_PATCH,
        )
        target = tmp_path / "repo"
        rc = gb.main(["--sample", str(sample), "--out", str(target)])
        assert rc == 0
        stdout = capsys.readouterr().out
        assert "git_bootstrap: wrote" in stdout
        assert (target / ".git").is_dir()
