"""Tests for ``scripts.eval.sample_import``.

Builds a synthetic git repo in ``tmp_path``, performs a three-way merge,
and asserts that ``sample_import`` produces the canonical five-file
sample layout with byte-stable tar contents (same sha256 across hosts).
"""

from __future__ import annotations

import hashlib
import subprocess
import tarfile
from pathlib import Path

import pytest

from scripts.eval import sample_import as si


# ---------------------------------------------------------------------------
# Synthetic repo fixture
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    base_env = {
        "GIT_AUTHOR_NAME": "Eval",
        "GIT_AUTHOR_EMAIL": "eval@example.com",
        "GIT_COMMITTER_NAME": "Eval",
        "GIT_COMMITTER_EMAIL": "eval@example.com",
        "GIT_AUTHOR_DATE": "2025-12-31T16:00:00Z",
        "GIT_COMMITTER_DATE": "2025-12-31T16:00:00Z",
    }
    if env:
        base_env.update(env)
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env={**base_env, "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    return result.stdout


def _write(repo: Path, path: str, content: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


@pytest.fixture
def merged_repo(tmp_path: Path) -> tuple[Path, str]:
    """Synthetic repo with a real merge commit.

    Layout::

        main:  base ─── upstream
                 \\        \\
                  fork ── merge   (golden tree)

    Returns ``(repo, merge_sha)``.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "commit.gpgsign", "false")

    # base
    _write(repo, "hello.py", "def greet(name):\n    return f'Hi, {name}'\n")
    _git(repo, "add", "hello.py")
    _git(repo, "commit", "-q", "-m", "base")
    base_sha = _git(repo, "rev-parse", "HEAD").strip()

    # upstream side (extends signature)
    _write(
        repo,
        "hello.py",
        "def greet(name, loud=False):\n    msg = f'Hi, {name}'\n    return msg.upper() if loud else msg\n",
    )
    _git(repo, "commit", "-q", "-am", "upstream change")
    upstream_sha = _git(repo, "rev-parse", "HEAD").strip()

    # fork branch — start from base, add comment
    _git(repo, "checkout", "-q", "-b", "fork", base_sha)
    _write(
        repo,
        "hello.py",
        "# greeting helper\ndef greet(name):\n    return f'Hi, {name}'\n",
    )
    _git(repo, "commit", "-q", "-am", "fork change")

    # merge upstream into fork (creates merge commit; resolve by taking
    # both: signature change + comment). The merge call exits non-zero on
    # conflict — that's expected; we overwrite the file with the human-
    # curated resolution and commit.
    subprocess.run(
        ["git", "-C", str(repo), "merge", "--no-ff", "--no-commit", upstream_sha],
        env={
            "GIT_AUTHOR_NAME": "Eval",
            "GIT_AUTHOR_EMAIL": "eval@example.com",
            "GIT_COMMITTER_NAME": "Eval",
            "GIT_COMMITTER_EMAIL": "eval@example.com",
            "GIT_AUTHOR_DATE": "2025-12-31T16:00:00Z",
            "GIT_COMMITTER_DATE": "2025-12-31T16:00:00Z",
            "GIT_EDITOR": "true",
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        },
        capture_output=True,
        check=False,
    )
    _write(
        repo,
        "hello.py",
        "# greeting helper\ndef greet(name, loud=False):\n    msg = f'Hi, {name}'\n    return msg.upper() if loud else msg\n",
    )
    _git(repo, "add", "hello.py")
    _git(repo, "commit", "-q", "-m", "golden merge")
    merge_sha = _git(repo, "rev-parse", "HEAD").strip()
    return repo, merge_sha


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestImportFromMerge:
    def test_writes_five_artifacts(
        self, merged_repo: tuple[Path, str], tmp_path: Path
    ) -> None:
        repo, merge_sha = merged_repo
        out_root = tmp_path / "samples"
        rc = si.main(
            [
                "--repo",
                str(repo),
                "--sample-id",
                "t1-0099",
                "--tier",
                "1",
                "--out",
                str(out_root),
                "--from-merge",
                merge_sha,
            ]
        )
        assert rc == 0
        sample = out_root / "t1-0099"
        assert (sample / "base.tar").is_file()
        assert (sample / "golden.tar").is_file()
        assert (sample / "upstream.patch").is_file()
        assert (sample / "fork.patch").is_file()
        assert (sample / "meta.yaml").is_file()

    def test_tar_contents_match_three_refs(
        self, merged_repo: tuple[Path, str], tmp_path: Path
    ) -> None:
        repo, merge_sha = merged_repo
        out_root = tmp_path / "samples"
        si.main(
            [
                "--repo",
                str(repo),
                "--sample-id",
                "t1-0099",
                "--tier",
                "1",
                "--out",
                str(out_root),
                "--from-merge",
                merge_sha,
            ]
        )
        sample = out_root / "t1-0099"
        with tarfile.open(sample / "base.tar") as tf:
            base_files = {m.name for m in tf.getmembers()}
            base_member = tf.getmember("hello.py")
            assert base_member.mode == 0o644
            assert base_member.mtime == si.FIXED_MTIME
        with tarfile.open(sample / "golden.tar") as tf:
            golden_content = tf.extractfile("hello.py").read().decode()  # type: ignore[union-attr]
        assert "hello.py" in base_files
        assert "loud=False" in golden_content
        assert "greeting helper" in golden_content

    def test_meta_skeleton_marks_tbd_classification(
        self, merged_repo: tuple[Path, str], tmp_path: Path
    ) -> None:
        repo, merge_sha = merged_repo
        out_root = tmp_path / "samples"
        si.main(
            [
                "--repo",
                str(repo),
                "--sample-id",
                "t1-0099",
                "--tier",
                "1",
                "--out",
                str(out_root),
                "--from-merge",
                merge_sha,
            ]
        )
        meta = (out_root / "t1-0099" / "meta.yaml").read_text(encoding="utf-8")
        assert "sample_id: t1-0099" in meta
        assert "tier: 1" in meta
        assert "category: TBD" in meta
        assert "expected_risk: TBD" in meta
        assert "golden_strategy: TBD" in meta
        assert "notes_provenance:" in meta

    def test_tar_byte_stable_across_runs(
        self, merged_repo: tuple[Path, str], tmp_path: Path
    ) -> None:
        repo, merge_sha = merged_repo

        def _run(label: str) -> Path:
            out_root = tmp_path / label
            si.main(
                [
                    "--repo",
                    str(repo),
                    "--sample-id",
                    "t1-0099",
                    "--tier",
                    "1",
                    "--out",
                    str(out_root),
                    "--from-merge",
                    merge_sha,
                ]
            )
            return out_root / "t1-0099"

        a = _run("first")
        b = _run("second")
        for name in ("base.tar", "golden.tar"):
            sha_a = hashlib.sha256((a / name).read_bytes()).hexdigest()
            sha_b = hashlib.sha256((b / name).read_bytes()).hexdigest()
            assert sha_a == sha_b, f"{name} sha256 unstable: {sha_a} != {sha_b}"

    def test_patch_files_unified_diff(
        self, merged_repo: tuple[Path, str], tmp_path: Path
    ) -> None:
        repo, merge_sha = merged_repo
        out_root = tmp_path / "samples"
        si.main(
            [
                "--repo",
                str(repo),
                "--sample-id",
                "t1-0099",
                "--tier",
                "1",
                "--out",
                str(out_root),
                "--from-merge",
                merge_sha,
            ]
        )
        upstream = (out_root / "t1-0099" / "upstream.patch").read_text(encoding="utf-8")
        fork = (out_root / "t1-0099" / "fork.patch").read_text(encoding="utf-8")
        assert "--- a/hello.py" in upstream
        assert "+++ b/hello.py" in upstream
        assert "loud=False" in upstream
        assert "greeting helper" in fork


class TestErrors:
    def test_refuses_non_empty_target(
        self,
        merged_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo, merge_sha = merged_repo
        out_root = tmp_path / "samples"
        target = out_root / "t1-0099"
        target.mkdir(parents=True)
        (target / "preexisting").write_text("x", encoding="utf-8")
        rc = si.main(
            [
                "--repo",
                str(repo),
                "--sample-id",
                "t1-0099",
                "--tier",
                "1",
                "--out",
                str(out_root),
                "--from-merge",
                merge_sha,
            ]
        )
        assert rc == 1
        assert "refuse to overwrite" in capsys.readouterr().err

    def test_non_git_repo_rejected(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bare = tmp_path / "not-a-repo"
        bare.mkdir()
        rc = si.main(
            [
                "--repo",
                str(bare),
                "--sample-id",
                "t1-0099",
                "--tier",
                "1",
                "--out",
                str(tmp_path / "out"),
                "--from-merge",
                "deadbeef",
            ]
        )
        assert rc == 1

    def test_requires_from_merge_or_all_four_refs(
        self,
        merged_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo, _ = merged_repo
        rc = si.main(
            [
                "--repo",
                str(repo),
                "--sample-id",
                "t1-0099",
                "--tier",
                "1",
                "--out",
                str(tmp_path / "out"),
                "--base-ref",
                "HEAD",
            ]
        )
        assert rc == 1
        assert "must pass either --from-merge" in capsys.readouterr().err


class TestExplicitRefs:
    def test_all_four_refs_works(
        self, merged_repo: tuple[Path, str], tmp_path: Path
    ) -> None:
        repo, merge_sha = merged_repo
        # Resolve refs explicitly via the same logic the helper uses.
        refs = si._derive_refs_from_merge(repo, merge_sha)
        out_root = tmp_path / "samples"
        rc = si.main(
            [
                "--repo",
                str(repo),
                "--sample-id",
                "t1-0099",
                "--tier",
                "1",
                "--out",
                str(out_root),
                "--base-ref",
                refs.base,
                "--upstream-ref",
                refs.upstream,
                "--fork-ref",
                refs.fork,
                "--golden-ref",
                refs.golden,
            ]
        )
        assert rc == 0
        assert (out_root / "t1-0099" / "meta.yaml").is_file()


class TestAllFilesFlag:
    def test_all_files_captures_full_tree(
        self, merged_repo: tuple[Path, str], tmp_path: Path
    ) -> None:
        repo, merge_sha = merged_repo
        # add an untouched file to verify --all-files picks it up
        _write(repo, "untouched.md", "noise\n")
        _git(repo, "add", "untouched.md")
        _git(repo, "commit", "-q", "-m", "add untouched")
        # rebuild merge so untouched.md is present at golden
        new_merge = _git(repo, "rev-parse", "HEAD").strip()
        out_root = tmp_path / "samples"
        rc = si.main(
            [
                "--repo",
                str(repo),
                "--sample-id",
                "t1-0099",
                "--tier",
                "1",
                "--out",
                str(out_root),
                "--golden-ref",
                new_merge,
                "--base-ref",
                merge_sha + "^1",
                "--upstream-ref",
                merge_sha + "^2",
                "--fork-ref",
                merge_sha,
                "--all-files",
            ]
        )
        assert rc == 0
        with tarfile.open(out_root / "t1-0099" / "golden.tar") as tf:
            names = {m.name for m in tf.getmembers()}
        assert "untouched.md" in names


class TestPathFilter:
    """--path scopes capture to one or more subtrees (monorepo essential)."""

    def test_path_filter_excludes_files_outside_subtree(
        self, merged_repo: tuple[Path, str], tmp_path: Path
    ) -> None:
        repo, merge_sha = merged_repo
        # add a sibling file outside the scoped subtree on both sides so
        # the union would normally pick it up
        _git(repo, "checkout", "fork")
        _write(repo, "sibling.md", "fork side\n")
        _git(repo, "add", "sibling.md")
        _git(repo, "commit", "-q", "-m", "fork sibling")
        # nest hello.py under plugin-a/
        repo_plugin_dir = repo / "plugin-a"
        repo_plugin_dir.mkdir(exist_ok=True)
        (repo_plugin_dir / "readme.md").write_text(
            "plugin-a readme\n", encoding="utf-8"
        )
        _git(repo, "add", "plugin-a/readme.md")
        _git(repo, "commit", "-q", "-m", "plugin-a readme")
        new_fork = _git(repo, "rev-parse", "HEAD").strip()

        out_root = tmp_path / "samples"
        rc = si.main(
            [
                "--repo",
                str(repo),
                "--sample-id",
                "t1-0099",
                "--tier",
                "1",
                "--out",
                str(out_root),
                "--base-ref",
                merge_sha + "^1",
                "--upstream-ref",
                merge_sha + "^2",
                "--fork-ref",
                new_fork,
                "--golden-ref",
                new_fork,
                "--path",
                "plugin-a",
            ]
        )
        assert rc == 0
        with tarfile.open(out_root / "t1-0099" / "golden.tar") as tf:
            names = {m.name for m in tf.getmembers()}
        assert "plugin-a/readme.md" in names
        assert "sibling.md" not in names
        assert "hello.py" not in names
        meta = (out_root / "t1-0099" / "meta.yaml").read_text(encoding="utf-8")
        assert "paths: ['plugin-a']" in meta
