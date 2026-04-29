import pytest

from src.models.diff import FileDiff, FileStatus, RiskLevel
from src.models.config import FileClassifierConfig, SecuritySensitiveConfig
from src.tools.file_classifier import compute_risk_score, matches_any_pattern


def _make_file_diff(
    file_path: str = "src/main.py",
    lines_added: int = 10,
    lines_deleted: int = 5,
    lines_changed: int = 10,
    conflict_count: int = 0,
    is_security_sensitive: bool = False,
    hunks: list | None = None,
    file_status: FileStatus = FileStatus.MODIFIED,
) -> FileDiff:
    return FileDiff(
        file_path=file_path,
        file_status=file_status,
        risk_level=RiskLevel.AUTO_SAFE,
        risk_score=0.0,
        lines_added=lines_added,
        lines_deleted=lines_deleted,
        lines_changed=lines_changed,
        conflict_count=conflict_count,
        hunks=hunks or [],
        is_security_sensitive=is_security_sensitive,
    )


def test_security_sensitive_always_high_risk():
    config = FileClassifierConfig(
        security_sensitive=SecuritySensitiveConfig(
            patterns=["**/auth/**", "**/*.key"],
            always_require_human=True,
        )
    )
    fd = _make_file_diff(file_path="src/auth/login.py")
    score = compute_risk_score(fd, config)
    assert score >= 0.8, f"Security-sensitive file must have risk >= 0.8, got {score}"

    fd2 = _make_file_diff(file_path="secrets/api.key", is_security_sensitive=True)
    score2 = compute_risk_score(fd2, config)
    assert score2 >= 0.8, f"Security-sensitive file must have risk >= 0.8, got {score2}"


def test_always_take_target_always_low_risk():
    config = FileClassifierConfig(
        always_take_target_patterns=["**/*.lock", "**/generated/**"],
    )
    fd = _make_file_diff(
        file_path="poetry.lock",
        lines_added=1000,
        lines_deleted=500,
        lines_changed=1000,
    )
    score = compute_risk_score(fd, config)
    assert score == 0.1, f"always_take_target file must have score == 0.1, got {score}"


def test_risk_score_weights_sum_to_one():
    weights = {
        "size": 0.15,
        "conflict_density": 0.35,
        "change_ratio": 0.20,
        "file_type": 0.20,
        "security": 0.10,
    }
    total = sum(weights.values())
    assert abs(total - 1.0) < 1e-9, f"Weights must sum to 1.0, got {total}"


# ---------------------------------------------------------------------------
# matches_any_pattern — anchored glob semantics
# ---------------------------------------------------------------------------


class TestMatchesAnyPattern:
    @pytest.mark.parametrize(
        "path",
        [
            ".github/workflows/ci.yml",
            ".github/workflows/sub/build.yml",
            ".github/workflows/deeply/nested/file.yml",
        ],
    )
    def test_trailing_doublestar_matches_under_dir(self, path):
        assert matches_any_pattern(path, [".github/workflows/**"])

    @pytest.mark.parametrize(
        "path",
        [
            "cmd/commandline/plugin/templates/.github/workflows/plugin-publish.yml",
            "vendor/.github/workflows/x.yml",
            "a/b/.github/workflows/c.yml",
        ],
    )
    def test_trailing_doublestar_does_not_match_nested_segment(self, path):
        # Regression: previously the lstrip("**/").lstrip("*") fallback +
        # f"*{normalized}" turned ".github/workflows/**" into a contains-match.
        assert not matches_any_pattern(path, [".github/workflows/**"])

    def test_leading_doublestar_matches_at_any_depth(self):
        for p in [
            "license/key.go",
            "internal/core/license/key.go",
            "a/b/c/license/key.go",
        ]:
            assert matches_any_pattern(p, ["**/license/**"])

    def test_doublestar_lock_glob(self):
        assert matches_any_pattern("Cargo.lock", ["**/*.lock"])
        assert matches_any_pattern("a/b/Cargo.lock", ["**/*.lock"])
        assert not matches_any_pattern("Cargo.lockfile", ["**/*.lock"])

    def test_anchored_prefix(self):
        assert matches_any_pattern(
            "internal/core/local_runtime/foo.go",
            ["internal/core/local_runtime/**"],
        )
        # Same suffix but different anchor — must NOT match
        assert not matches_any_pattern(
            "x/internal/core/local_runtime/foo.go",
            ["internal/core/local_runtime/**"],
        )

    def test_single_star_does_not_cross_slash(self):
        assert matches_any_pattern("a/foo.go", ["a/*.go"])
        assert not matches_any_pattern("a/b/foo.go", ["a/*.go"])

    def test_question_mark_single_char_only(self):
        assert matches_any_pattern("a/foo.go", ["a/fo?.go"])
        assert not matches_any_pattern("a/fooo.go", ["a/fo?.go"])

    def test_bare_basename_pattern_matches_anywhere(self):
        # Backward-compat: patterns without "/" match against basename.
        assert matches_any_pattern("a/b/c/private_key.pem", ["*_key*"])
        assert matches_any_pattern("private_key.pem", ["*_key*"])

    def test_empty_patterns_returns_false(self):
        assert not matches_any_pattern("anything.go", [])

    def test_special_regex_chars_in_pattern_are_escaped(self):
        # Pattern with regex meta-characters must be treated literally.
        assert matches_any_pattern("a/b+c.txt", ["a/b+c.txt"])
        assert not matches_any_pattern("a/bc.txt", ["a/b+c.txt"])
        assert matches_any_pattern("a/(brackets).txt", ["a/(brackets).txt"])
