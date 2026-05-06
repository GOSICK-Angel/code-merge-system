"""P2-1 tests: ScarListBuilder.build_from_feature_commits.

Existing ``build()`` only catches restore/revert/compat-fix keywords. dify
plugin daemon (and most "live fork" repos) protect customization through
positive-add commits instead — ``feat: add Cvte SSO``, ``add custom auth``,
``implement plugin reload``. Those commits introduce fork-only files that
must be preserved on next merge.

Contract:
- ``build_from_feature_commits(repo_path, fork_ref, base_ref)`` walks the
  ``base_ref..fork_ref`` range (commits in fork but not in base) and emits
  one ``Scar`` per commit whose subject matches ``feat:`` / ``feature:`` /
  ``add `` / ``implement`` (case-insensitive) by default.
- ``Scar.pattern_kind`` is ``"feature"`` for these.
- Range exclusion is enforced: a feature-add commit reachable from
  ``base_ref`` must NOT be returned.
- ``materialize_as_customizations`` already handles the new kind because
  it just stringifies ``representative.pattern_kind`` in the description.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from src.tools.scar_list_builder import (
    DEFAULT_FEATURE_PATTERNS,
    Scar,
    ScarListBuilder,
)


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


class TestDefaultFeaturePatterns:
    def test_default_patterns_include_feat_add_implement(self) -> None:
        joined = " ".join(DEFAULT_FEATURE_PATTERNS).lower()
        assert "feat" in joined
        assert "add" in joined
        assert "implement" in joined


class TestBuildFromFeatureCommits:
    def test_picks_up_feat_commit_in_fork_range(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        base_sha = _commit(repo, "init", {"README.md": "x\n"})
        _branch(repo, "fork")
        _commit(repo, "feat: add Cvte SSO", {"src/auth/cvte.py": "pass\n"})

        builder = ScarListBuilder()
        scars = builder.build_from_feature_commits(repo, "fork", base_sha)

        subjects = [s.commit_subject for s in scars]
        assert any("Cvte SSO" in s for s in subjects)
        assert all(s.pattern_kind == "feature" for s in scars)

    def test_picks_up_add_and_implement_subjects(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        base = _commit(repo, "init", {"r.md": "x\n"})
        _branch(repo, "fork")
        _commit(repo, "add custom auth header", {"src/h.py": "pass\n"})
        _commit(repo, "implement plugin reload", {"src/r.py": "pass\n"})
        _commit(repo, "chore: bump deps", {"deps.txt": "x\n"})

        builder = ScarListBuilder()
        scars = builder.build_from_feature_commits(repo, "fork", base)
        subjects = [s.commit_subject for s in scars]
        assert any("custom auth header" in s for s in subjects)
        assert any("plugin reload" in s for s in subjects)
        assert not any("bump deps" in s for s in subjects)

    def test_excludes_commits_reachable_from_base(self, tmp_path: Path) -> None:
        """A feat commit on ``main`` (base) must NOT appear in fork-only range."""
        repo = _init_repo(tmp_path)
        _commit(repo, "init", {"r.md": "x\n"})
        feat_on_main = _commit(repo, "feat: shared upstream feature", {"a.py": "x\n"})
        _branch(repo, "fork")
        _commit(repo, "feat: fork-only", {"b.py": "y\n"})

        builder = ScarListBuilder()
        scars = builder.build_from_feature_commits(repo, "fork", feat_on_main)
        subjects = [s.commit_subject for s in scars]
        assert any("fork-only" in s for s in subjects)
        assert not any("shared upstream feature" in s for s in subjects)

    def test_files_populated_from_commit_diff(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        base = _commit(repo, "init", {"r.md": "x\n"})
        _branch(repo, "fork")
        _commit(
            repo,
            "feat: add new module",
            {"src/m.py": "pass\n", "tests/test_m.py": "pass\n"},
        )

        builder = ScarListBuilder()
        scars = builder.build_from_feature_commits(repo, "fork", base)
        assert len(scars) == 1
        assert set(scars[0].files) == {"src/m.py", "tests/test_m.py"}

    def test_empty_range_returns_empty(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        sha = _commit(repo, "init", {"r.md": "x\n"})
        builder = ScarListBuilder()
        scars = builder.build_from_feature_commits(repo, sha, sha)
        assert scars == []

    def test_invalid_repo_returns_empty(self, tmp_path: Path) -> None:
        builder = ScarListBuilder()
        scars = builder.build_from_feature_commits(
            tmp_path / "no_such_repo", "fork", "main"
        )
        assert scars == []

    def test_custom_feature_patterns_override(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        base = _commit(repo, "init", {"r.md": "x\n"})
        _branch(repo, "fork")
        _commit(repo, "feat: should be ignored", {"a.py": "x\n"})
        _commit(repo, "CVTE: pin custom auth", {"b.py": "y\n"})

        builder = ScarListBuilder()
        scars = builder.build_from_feature_commits(
            repo, "fork", base, feature_patterns=[r"^CVTE:"]
        )
        subjects = [s.commit_subject for s in scars]
        assert any("CVTE: pin custom auth" in s for s in subjects)
        assert not any("should be ignored" in s for s in subjects)


class TestMaterializeFeatureScars:
    def test_feature_scars_become_file_exists_customizations(self) -> None:
        scars = [
            Scar(
                commit_sha="abc",
                commit_subject="feat: add Cvte auth",
                files=["src/cvte/auth.py"],
                pattern_kind="feature",
            )
        ]
        builder = ScarListBuilder()
        entries = builder.materialize_as_customizations(scars, [])
        assert len(entries) == 1
        entry = entries[0]
        assert entry.source == "scar_learned"
        assert entry.files == ["src/cvte/auth.py"]
        assert any(v.type == "file_exists" for v in entry.verification)
        assert "feature" in entry.description.lower()
