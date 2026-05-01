"""Regression: GitPython's repo.git.show() default-strips trailing whitespace,
which silently turned every B-class take_target into a 1-byte drift and
triggered the dual circuit-breaker (B-class drift > 100 + cost ceiling).
get_file_content must preserve byte-for-byte equality with `git show`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    return repo


def _commit_blob(repo: Path, rel_path: str, payload: bytes) -> str:
    fpath = repo / rel_path
    fpath.parent.mkdir(parents=True, exist_ok=True)
    fpath.write_bytes(payload)
    subprocess.run(
        ["git", "add", "-A"], cwd=str(repo), check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", f"add {rel_path}"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    blob_hash = subprocess.run(
        ["git", "ls-tree", "HEAD", "--", rel_path],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()[2]
    return blob_hash


def _hash_object_of_str(repo: Path, content: str) -> str:
    result = subprocess.run(
        ["git", "hash-object", "--stdin"],
        cwd=str(repo),
        input=content.encode("utf-8"),
        check=True,
        capture_output=True,
    )
    return result.stdout.decode().strip()


@pytest.mark.parametrize(
    "label,payload",
    [
        ("trailing_newline", b"alpha\nbeta\n"),
        ("no_trailing_newline", b"alpha\nbeta"),
        ("multiple_trailing_newlines", b"alpha\nbeta\n\n\n"),
        ("only_newline", b"\n"),
        ("empty", b""),
    ],
    ids=lambda v: v if isinstance(v, str) else "payload",
)
def test_get_file_content_preserves_trailing_bytes(
    tmp_path: Path, label: str, payload: bytes
) -> None:
    from src.tools.git_tool import GitTool

    repo = _init_repo(tmp_path)
    expected_blob = _commit_blob(repo, "f.txt", payload)

    tool = GitTool(str(repo))
    content = tool.get_file_content("HEAD", "f.txt")

    assert content is not None, f"{label}: get_file_content returned None"
    assert (
        _hash_object_of_str(repo, content) == expected_blob
    ), f"{label}: roundtrip blob hash differs from `git ls-tree`"


def test_b_class_take_target_writes_byte_equal_to_upstream(tmp_path: Path) -> None:
    """Plain regression for the actual bug: read upstream content,
    write to disk, expect worktree blob hash == upstream blob hash."""
    from src.tools.git_tool import GitTool

    repo = _init_repo(tmp_path)
    payload = b"requires-python = \">=3.12\"\ndependencies = [\n]\n"
    upstream_blob = _commit_blob(repo, "tools/x/pyproject.toml", payload)

    tool = GitTool(str(repo))
    content = tool.get_file_content("HEAD", "tools/x/pyproject.toml")
    assert content is not None

    target = repo / "tools/x/pyproject.toml"
    target.write_text(content, encoding="utf-8")

    worktree_blob = tool.get_worktree_blob_sha("tools/x/pyproject.toml")
    assert (
        worktree_blob == upstream_blob
    ), "take_target write produced a blob that drifts from upstream — the trailing-newline bug"
