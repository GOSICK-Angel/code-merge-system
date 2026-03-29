import fnmatch
from pathlib import Path
from src.models.diff import FileDiff, RiskLevel, FileStatus
from src.models.config import FileClassifierConfig


def matches_any_pattern(file_path: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if fnmatch.fnmatch(file_path, pattern):
            return True
        path_obj = Path(file_path)
        try:
            if path_obj.match(pattern):
                return True
        except Exception:
            pass
        normalized_pattern = pattern.lstrip("**/").lstrip("*")
        if normalized_pattern:
            if fnmatch.fnmatch(file_path, f"*{normalized_pattern}"):
                return True
            if fnmatch.fnmatch(path_obj.name, normalized_pattern):
                return True
    return False


def estimate_total_lines(file_diff: FileDiff) -> int:
    if not file_diff.hunks:
        return max(1, file_diff.lines_added + file_diff.lines_deleted)
    total = 0
    for hunk in file_diff.hunks:
        total += max(
            hunk.end_line_current - hunk.start_line_current,
            hunk.end_line_target - hunk.start_line_target,
        )
    return max(1, total + file_diff.lines_changed)


def compute_risk_score(file_diff: FileDiff, config: FileClassifierConfig) -> float:
    weights = {
        "size": 0.15,
        "conflict_density": 0.35,
        "change_ratio": 0.20,
        "file_type": 0.20,
        "security": 0.10,
    }

    size_score = min(1.0, (file_diff.lines_changed / 500) ** 0.5)

    total_lines = max(1, file_diff.lines_added + file_diff.lines_deleted)
    conflict_lines = sum(
        h.end_line_current - h.start_line_current
        for h in file_diff.hunks
        if h.has_conflict
    )
    conflict_density_score = min(1.0, conflict_lines / total_lines)

    change_ratio = file_diff.lines_changed / estimate_total_lines(file_diff)
    change_ratio_score = min(1.0, change_ratio * 2)

    type_score_map = {
        ".py": 0.7, ".ts": 0.7, ".js": 0.6,
        ".java": 0.7, ".go": 0.7, ".rs": 0.8,
        ".yaml": 0.5, ".json": 0.4, ".toml": 0.4,
        ".md": 0.1, ".txt": 0.1,
        ".sql": 0.8, ".sh": 0.7,
    }
    ext = Path(file_diff.file_path).suffix.lower()
    type_score = type_score_map.get(ext, 0.5)

    security_score = 1.0 if file_diff.is_security_sensitive else 0.0

    raw_score = (
        weights["size"] * size_score
        + weights["conflict_density"] * conflict_density_score
        + weights["change_ratio"] * change_ratio_score
        + weights["file_type"] * type_score
        + weights["security"] * security_score
    )

    if matches_any_pattern(file_diff.file_path, config.always_take_target_patterns):
        return 0.1

    if matches_any_pattern(file_diff.file_path, config.security_sensitive.patterns):
        return max(raw_score, 0.8)

    return round(raw_score, 3)


def is_security_sensitive(file_path: str, config: FileClassifierConfig) -> bool:
    return matches_any_pattern(file_path, config.security_sensitive.patterns)


def classify_file(
    file_diff: FileDiff,
    config: FileClassifierConfig,
) -> RiskLevel:
    if matches_any_pattern(file_diff.file_path, config.excluded_patterns):
        return RiskLevel.EXCLUDED

    ext = Path(file_diff.file_path).suffix.lower()
    if ext in config.binary_extensions:
        return RiskLevel.BINARY

    if file_diff.file_status == FileStatus.BINARY:
        return RiskLevel.BINARY

    if (
        file_diff.file_status == FileStatus.DELETED
        and file_diff.lines_added == 0
    ):
        return RiskLevel.DELETED_ONLY

    risk_score = file_diff.risk_score

    if risk_score < 0.3:
        return RiskLevel.AUTO_SAFE
    elif risk_score < 0.6:
        return RiskLevel.AUTO_RISKY
    else:
        return RiskLevel.HUMAN_REQUIRED
