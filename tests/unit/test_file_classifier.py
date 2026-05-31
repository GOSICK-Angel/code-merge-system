import pytest

from src.models.diff import FileChangeCategory, FileDiff, FileStatus, RiskLevel
from src.models.config import (
    ComplexityConfig,
    FileClassifierConfig,
    MergeConfig,
    SecuritySensitiveConfig,
)
from src.tools.file_classifier import (
    classify_file,
    compute_complexity,
    compute_risk_score,
    matches_any_pattern,
)


def _make_file_diff(
    file_path: str = "src/main.py",
    lines_added: int = 10,
    lines_deleted: int = 5,
    lines_changed: int = 10,
    conflict_count: int = 0,
    is_security_sensitive: bool = False,
    hunks: list | None = None,
    file_status: FileStatus = FileStatus.MODIFIED,
    change_category: FileChangeCategory | None = None,
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
        change_category=change_category,
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


def test_d_missing_skips_change_ratio_dimension():
    # Regression (Issue 3): a brand-new upstream-only .py file used to
    # ride the change_ratio cap straight into AUTO_RISKY (~0.42) even
    # without any conflict. After dropping change_ratio for D_MISSING
    # and renormalising, the same file lands well below the AUTO_RISKY
    # boundary (0.30).
    config = FileClassifierConfig()
    fd = FileDiff(
        file_path="models/openai/tools/new_handler.py",
        file_status=FileStatus.ADDED,
        risk_level=RiskLevel.AUTO_SAFE,
        risk_score=0.0,
        lines_added=120,
        lines_deleted=0,
        lines_changed=120,
        conflict_count=0,
        hunks=[],
        is_security_sensitive=False,
        change_category=FileChangeCategory.D_MISSING,
    )
    score = compute_risk_score(fd, config)
    assert score < 0.30, (
        f"Upstream-new file with no conflict must stay under AUTO_RISKY "
        f"(0.30), got {score}"
    )


def test_risk_hint_pattern_does_not_floor_to_human_required():
    # Regression (Issue 4): file path *containing* "credentials" used to
    # match the strict pattern list and floor the score to 0.8 (forcing
    # HUMAN_REQUIRED) — bricking test fixtures like
    # ``test_validate_credentials.py``. The default config now routes
    # such weak filename matches through ``risk_hint_patterns``, which
    # only adds a small bump.
    config = FileClassifierConfig()  # uses production defaults
    fd = _make_file_diff(
        file_path="models/openai_api_compatible/tests/test_validate_credentials.py",
        lines_added=50,
        lines_deleted=0,
        lines_changed=50,
    )
    score = compute_risk_score(fd, config)
    assert score < 0.7, (
        f"Weak-signal credential filename must NOT be floored to 0.8; got {score}"
    )


def test_strict_security_pattern_still_floors_to_human_required():
    # Counter-test (Issue 4): the strong-signal patterns (.env / *.pem /
    # exact ``credentials.py``) must still floor the score so the
    # always_require_human path keeps working for real secrets.
    config = FileClassifierConfig()  # uses production defaults
    for path in (
        "src/foo/.env",
        "src/foo/.env.local",
        "src/foo/private.pem",
        "src/foo/credentials.py",
    ):
        fd = _make_file_diff(file_path=path, lines_added=5, lines_deleted=2)
        score = compute_risk_score(fd, config)
        assert score >= 0.8, (
            f"Strong-signal security path {path!r} must floor to 0.8, got {score}"
        )


class TestCClassRiskFloor:
    """Pre-merge, conflict markers do not exist yet so the
    conflict_density dimension of compute_risk_score is always 0 for
    C-class files. The c_class_risk_floor guard lifts these scores into
    the auto_risky band (>= 0.3) so ConflictAnalyst still gets a look.
    """

    def test_c_class_score_floored_to_default(self):
        config = FileClassifierConfig()
        fd = _make_file_diff(
            file_path="models/user/user.go",
            lines_added=5,
            lines_deleted=3,
            lines_changed=8,
            change_category=FileChangeCategory.C,
        )
        score = compute_risk_score(fd, config)
        assert score >= config.c_class_risk_floor, (
            f"C-class file must floor to {config.c_class_risk_floor}, got {score}"
        )

    def test_b_class_not_floored(self):
        config = FileClassifierConfig()
        fd = _make_file_diff(
            file_path="docs/README.md",
            lines_added=5,
            lines_deleted=3,
            lines_changed=8,
            change_category=FileChangeCategory.B,
        )
        score = compute_risk_score(fd, config)
        assert score < config.c_class_risk_floor, (
            f"B-class file must NOT be floored, got {score}"
        )

    def test_c_class_floor_does_not_lower_higher_score(self):
        # If raw score already exceeds the floor (e.g. large diff in .go),
        # the floor must not lower it.
        config = FileClassifierConfig(c_class_risk_floor=0.4)
        fd = _make_file_diff(
            file_path="src/core/big_refactor.py",
            lines_added=400,
            lines_deleted=400,
            lines_changed=400,
            change_category=FileChangeCategory.C,
        )
        score = compute_risk_score(fd, config)
        assert score > 0.4

    def test_security_pattern_still_dominates_c_class_floor(self):
        # security_sensitive.patterns floor (0.8) must still win over the
        # C-class floor (0.4) — auth/** paths route to human_required.
        config = FileClassifierConfig(
            security_sensitive=SecuritySensitiveConfig(patterns=["**/auth/**"]),
        )
        fd = _make_file_diff(
            file_path="models/auth/auth_token.go",
            lines_added=5,
            lines_deleted=3,
            change_category=FileChangeCategory.C,
        )
        score = compute_risk_score(fd, config)
        assert score >= 0.8

    def test_always_take_target_dominates_c_class_floor(self):
        config = FileClassifierConfig(
            always_take_target_patterns=["**/*.lock"],
            c_class_risk_floor=0.5,
        )
        fd = _make_file_diff(
            file_path="poetry.lock",
            lines_added=100,
            lines_deleted=50,
            change_category=FileChangeCategory.C,
        )
        score = compute_risk_score(fd, config)
        assert score == 0.1

    def test_c_class_with_no_content_change_not_floored(self):
        # A C-class entry with no line-level change (pure rename, etc.)
        # should not be artificially escalated.
        config = FileClassifierConfig()
        fd = _make_file_diff(
            file_path="src/util.py",
            lines_added=0,
            lines_deleted=0,
            lines_changed=0,
            change_category=FileChangeCategory.C,
        )
        score = compute_risk_score(fd, config)
        assert score < config.c_class_risk_floor


# ---------------------------------------------------------------------------
# compute_complexity — LLM-worthiness signal
# ---------------------------------------------------------------------------


def _hunk(idx: int, conflict: bool = False):
    from src.models.diff import DiffHunk

    return DiffHunk(
        hunk_id=f"h{idx}",
        start_line_current=idx * 10,
        end_line_current=idx * 10 + 5,
        start_line_target=idx * 10,
        end_line_target=idx * 10 + 5,
        content_current="a",
        content_target="b",
        content_base=None,
        has_conflict=conflict,
    )


class TestComputeComplexity:
    def test_trivial_file_scores_low(self) -> None:
        fd = _make_file_diff(lines_added=1, lines_deleted=0, lines_changed=1)
        assert compute_complexity(fd, ComplexityConfig()) < 0.2

    def test_large_change_lifts_size_dimension(self) -> None:
        small = _make_file_diff(lines_changed=2)
        large = _make_file_diff(lines_changed=500)
        cfg = ComplexityConfig()
        assert compute_complexity(large, cfg) > compute_complexity(small, cfg)

    def test_many_hunks_lift_score(self) -> None:
        few = _make_file_diff(hunks=[_hunk(i) for i in range(1)])
        many = _make_file_diff(hunks=[_hunk(i) for i in range(10)])
        cfg = ComplexityConfig()
        assert compute_complexity(many, cfg) > compute_complexity(few, cfg)

    def test_conflict_count_lifts_score(self) -> None:
        clean = _make_file_diff(conflict_count=0)
        conflicted = _make_file_diff(conflict_count=5)
        cfg = ComplexityConfig()
        assert compute_complexity(conflicted, cfg) > compute_complexity(clean, cfg)

    def test_score_is_bounded(self) -> None:
        fd = _make_file_diff(
            lines_changed=10_000,
            conflict_count=999,
            hunks=[_hunk(i, conflict=True) for i in range(50)],
        )
        score = compute_complexity(fd, ComplexityConfig())
        assert 0.0 <= score <= 1.0

    def test_fanout_absent_redistributes_weight(self) -> None:
        """With fanout=None the remaining four dimensions rescale so a
        file that maxes all of them still reaches ~1.0 (the dropped
        w_fanout does not cap the achievable maximum)."""
        fd = _make_file_diff(
            lines_changed=10_000,
            conflict_count=999,
            hunks=[_hunk(i, conflict=True) for i in range(50)],
        )
        score = compute_complexity(fd, ComplexityConfig(), fanout=None)
        assert score == pytest.approx(1.0, abs=0.01)

    def test_fanout_present_contributes(self) -> None:
        fd = _make_file_diff(lines_changed=2, conflict_count=0)
        cfg = ComplexityConfig()
        without = compute_complexity(fd, cfg, fanout=0.0)
        with_fanout = compute_complexity(fd, cfg, fanout=1.0)
        assert with_fanout > without


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


# ---------------------------------------------------------------------------
# classify_file — conflict_count > 0 → HUMAN_REQUIRED (P0 fix)
# ---------------------------------------------------------------------------


class TestClassifyFileConflictEscalation:
    def _fd(self, conflict_count: int = 0, risk_score: float = 0.3) -> FileDiff:
        return FileDiff(
            file_path="models/auth/auth_token.go",
            file_status=FileStatus.MODIFIED,
            risk_level=RiskLevel.AUTO_SAFE,
            risk_score=risk_score,
            lines_added=9,
            lines_deleted=0,
            lines_changed=9,
            conflict_count=conflict_count,
            hunks=[],
        )

    def test_conflict_count_zero_uses_risk_score(self):
        config = FileClassifierConfig()
        fd = self._fd(conflict_count=0, risk_score=0.2)
        assert classify_file(fd, config) == RiskLevel.AUTO_SAFE

    def test_conflict_count_nonzero_forces_human_required(self):
        config = FileClassifierConfig()
        fd = self._fd(conflict_count=1, risk_score=0.2)
        assert classify_file(fd, config) == RiskLevel.HUMAN_REQUIRED

    def test_conflict_count_nonzero_overrides_low_risk_score(self):
        config = FileClassifierConfig()
        fd = self._fd(conflict_count=2, risk_score=0.1)
        assert classify_file(fd, config) == RiskLevel.HUMAN_REQUIRED

    def test_excluded_file_stays_excluded_despite_conflict(self):
        config = FileClassifierConfig(excluded_patterns=["models/auth/**"])
        fd = self._fd(conflict_count=1)
        assert classify_file(fd, config) == RiskLevel.EXCLUDED


# ---------------------------------------------------------------------------
# _hoist_top_level_security_sensitive — file_classifier: null edge case (P0 fix)
# ---------------------------------------------------------------------------


def test_hoist_validator_hoists_when_no_file_classifier():
    import os

    os.environ.setdefault("ANTHROPIC_API_KEY", "test")
    os.environ.setdefault("OPENAI_API_KEY", "test")

    cfg = MergeConfig.model_validate(
        {
            "upstream_ref": "origin/main",
            "fork_ref": "origin/fork",
            "security_sensitive": {
                "patterns": ["models/auth/**", "routers/web/auth/**"]
            },
        }
    )
    assert cfg.file_classifier.security_sensitive.patterns == [
        "models/auth/**",
        "routers/web/auth/**",
    ]


def test_hoist_validator_hoists_when_file_classifier_is_null():
    import os

    os.environ.setdefault("ANTHROPIC_API_KEY", "test")
    os.environ.setdefault("OPENAI_API_KEY", "test")

    cfg = MergeConfig.model_validate(
        {
            "upstream_ref": "origin/main",
            "fork_ref": "origin/fork",
            "file_classifier": None,
            "security_sensitive": {"patterns": ["services/auth/**"]},
        }
    )
    assert "services/auth/**" in cfg.file_classifier.security_sensitive.patterns


def test_hoist_validator_fully_qualified_wins_over_top_level():
    import os

    os.environ.setdefault("ANTHROPIC_API_KEY", "test")
    os.environ.setdefault("OPENAI_API_KEY", "test")

    cfg = MergeConfig.model_validate(
        {
            "upstream_ref": "origin/main",
            "fork_ref": "origin/fork",
            "file_classifier": {
                "security_sensitive": {"patterns": ["explicit/path/**"]}
            },
            "security_sensitive": {"patterns": ["top_level/**"]},
        }
    )
    assert cfg.file_classifier.security_sensitive.patterns == ["explicit/path/**"]
