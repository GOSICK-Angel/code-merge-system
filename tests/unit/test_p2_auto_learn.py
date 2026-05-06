"""P2-3 tests: auto_learn orchestration + default-enabled rollout.

P2-1 (feature commits) and the existing ``build()`` (restore/revert) are
useful primitives, but until P2-3 nothing in production wired them up —
``grep -rn ScarListBuilder src/core/`` returned nothing. The auto_learn
helper is the single entry point that merges both signals and emits
ready-to-append ``CustomizationEntry`` objects, and ``ScarLearningConfig``
flips to enabled-by-default so zero-config repos (like dify plugin daemon)
get protection out of the box.

Contract:
- ``ScarLearningConfig().enabled`` is ``True`` by default.
- ``ScarListBuilder.auto_learn(repo_path, fork_ref, base_ref)`` combines
  restore-style scars (from ``build``) with feature-style scars (from
  ``build_from_feature_commits``), then materializes once.
- Files already covered by ``existing`` customizations are skipped.
- Invalid repos return an empty list (matches existing builder semantics).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from src.models.config import (
    CustomizationEntry,
    CustomizationVerification,
    ScarLearningConfig,
)
from src.tools.scar_list_builder import ScarListBuilder


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "T"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return repo


def _commit(repo: Path, message: str, files: dict[str, str]) -> str:
    for fname, content in files.items():
        path = repo / fname
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def _branch(repo: Path, name: str) -> None:
    subprocess.run(
        ["git", "checkout", "-b", name],
        cwd=repo,
        check=True,
        capture_output=True,
    )


class TestDefaultEnabled:
    def test_scar_learning_config_default_enabled(self) -> None:
        cfg = ScarLearningConfig()
        assert cfg.enabled is True

    def test_explicit_disable_still_works(self) -> None:
        cfg = ScarLearningConfig(enabled=False)
        assert cfg.enabled is False


class TestAutoLearn:
    def test_combines_restore_and_feature_scars(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        base = _commit(repo, "init", {"r.md": "x\n"})
        _branch(repo, "fork")
        _commit(repo, "feat: add Cvte SSO", {"src/auth/cvte.py": "pass\n"})
        _commit(
            repo,
            "fix: restore deleted handler",
            {"src/handler.py": "pass\n"},
        )

        builder = ScarListBuilder()
        entries = builder.auto_learn(repo, "fork", base)

        files = {fp for e in entries for fp in e.files}
        assert "src/auth/cvte.py" in files
        assert "src/handler.py" in files
        assert all(e.source == "scar_learned" for e in entries)

    def test_respects_existing_customizations(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        base = _commit(repo, "init", {"r.md": "x\n"})
        _branch(repo, "fork")
        _commit(
            repo,
            "feat: add Cvte SSO",
            {"src/auth/cvte.py": "pass\n"},
        )

        existing = [
            CustomizationEntry(
                name="manual",
                files=["src/auth/cvte.py"],
                verification=[
                    CustomizationVerification(
                        type="file_exists", files=["src/auth/cvte.py"]
                    )
                ],
            )
        ]
        builder = ScarListBuilder()
        entries = builder.auto_learn(repo, "fork", base, existing=existing)
        new_files = {fp for e in entries for fp in e.files}
        assert "src/auth/cvte.py" not in new_files

    def test_invalid_repo_returns_empty(self, tmp_path: Path) -> None:
        builder = ScarListBuilder()
        entries = builder.auto_learn(tmp_path / "no_such_repo", "fork", "main")
        assert entries == []

    def test_no_matching_commits_returns_empty(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        base = _commit(repo, "init", {"r.md": "x\n"})
        _branch(repo, "fork")
        _commit(repo, "chore: bump deps", {"deps.txt": "x\n"})

        builder = ScarListBuilder()
        entries = builder.auto_learn(repo, "fork", base)
        assert entries == []

    def test_custom_pattern_overrides_passed_through(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        base = _commit(repo, "init", {"r.md": "x\n"})
        _branch(repo, "fork")
        _commit(repo, "feat: should match feature default", {"a.py": "x\n"})
        _commit(repo, "CVTE: pin", {"b.py": "y\n"})

        builder = ScarListBuilder()
        entries = builder.auto_learn(
            repo,
            "fork",
            base,
            grep_patterns=["XYZZY_NO_MATCH"],
            feature_patterns=[r"^CVTE:"],
        )
        files = {fp for e in entries for fp in e.files}
        assert "b.py" in files
        assert "a.py" not in files
