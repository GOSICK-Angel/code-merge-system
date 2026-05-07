from enum import Enum
from pydantic import BaseModel, Field


class FileChangeCategory(str, Enum):
    A = "unchanged"
    B = "upstream_only"
    C = "both_changed"
    D_MISSING = "upstream_new"
    D_EXTRA = "current_only"
    E = "current_only_change"


class ForkDivergence(str, Enum):
    """P2-3 (§6.2 item 3): per-file fork-vs-upstream divergence kind,
    frozen at plan_review time and read by judge to downgrade
    deterministic checks when the divergence is intentional fork
    behavior rather than a merge bug."""

    UNCHANGED = "unchanged"
    UPSTREAM_ADDED = "upstream_added"
    UPSTREAM_ONLY_CHANGE = "upstream_only_change"
    FORK_MODIFIED = "fork_modified"
    FORK_DELETED = "fork_deleted"
    FORK_ONLY = "fork_only"


class FileStatus(str, Enum):
    ADDED = "added"
    DELETED = "deleted"
    MODIFIED = "modified"
    RENAMED = "renamed"
    BINARY = "binary"


class RiskLevel(str, Enum):
    AUTO_SAFE = "auto_safe"
    AUTO_RISKY = "auto_risky"
    HUMAN_REQUIRED = "human_required"
    DELETED_ONLY = "deleted_only"
    BINARY = "binary"
    EXCLUDED = "excluded"


class DiffHunk(BaseModel):
    hunk_id: str
    start_line_current: int
    end_line_current: int
    start_line_target: int
    end_line_target: int
    content_current: str
    content_target: str
    content_base: str | None
    has_conflict: bool
    conflict_marker_lines: list[int] = Field(default_factory=list)


class FileDiff(BaseModel):
    file_path: str
    file_status: FileStatus
    risk_level: RiskLevel
    risk_score: float = Field(ge=0.0, le=1.0)
    risk_factors: list[str] = Field(default_factory=list)
    lines_added: int = 0
    lines_deleted: int = 0
    lines_changed: int = 0
    upstream_lines_added: int = 0
    upstream_lines_deleted: int = 0
    conflict_count: int = 0
    hunks: list[DiffHunk] = Field(default_factory=list)
    change_category: FileChangeCategory | None = None
    is_security_sensitive: bool = False
    language: str | None = None
    raw_diff: str | None = None
