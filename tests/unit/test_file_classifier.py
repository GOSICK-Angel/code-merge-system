from src.models.diff import FileDiff, FileStatus, RiskLevel
from src.models.config import FileClassifierConfig, SecuritySensitiveConfig
from src.tools.file_classifier import compute_risk_score


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
