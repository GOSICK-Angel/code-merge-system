from __future__ import annotations

import fnmatch
import functools
import json as json_lib
import logging
import re
from pathlib import Path
from typing import Any, TYPE_CHECKING

from src.models.diff import (
    FileDiff,
    FileChangeCategory,
    ForkDivergence,
    RiskLevel,
    FileStatus,
)
from src.models.config import FileClassifierConfig

if TYPE_CHECKING:
    from src.tools.git_tool import GitTool

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=512)
def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a gitignore-style glob into an anchored regex.

    Semantics (matches `git`/`pathspec` conventions, NOT fnmatch):
      - ``**``        — zero or more path segments (only when standalone)
      - ``*``         — zero or more chars within a single segment (no /)
      - ``?``         — exactly one char within a segment
      - segment       — literal match against one path segment
      - leading ``**/``  — match at any depth
      - trailing ``/**`` — match the directory and everything beneath
      - bare ``foo``  — anchored full-path match (NOT contains)

    Why this matters: the previous fnmatch + lstrip-fallback implementation
    treated ``.github/workflows/**`` as a contains-match, which incorrectly
    matched nested paths like
    ``cmd/templates/.github/workflows/plugin-publish.yml``.
    """
    if not pattern:
        return re.compile(r"^$")

    parts = pattern.split("/")
    placeholder = "\x00DOUBLESTAR\x00"
    rendered_parts: list[str] = []
    for part in parts:
        if part == "**":
            rendered_parts.append(placeholder)
            continue
        seg_chars: list[str] = []
        for ch in part:
            if ch == "*":
                seg_chars.append("[^/]*")
            elif ch == "?":
                seg_chars.append("[^/]")
            else:
                seg_chars.append(re.escape(ch))
        rendered_parts.append("".join(seg_chars))

    joined = "/".join(rendered_parts)
    joined = joined.replace(f"{placeholder}/", "(?:.+/)?")
    joined = joined.replace(f"/{placeholder}", "(?:/.+)?")
    joined = joined.replace(placeholder, ".*")

    return re.compile(f"^{joined}$")


def matches_any_pattern(file_path: str, patterns: list[str]) -> bool:
    """Anchored glob match. See ``_glob_to_regex`` for semantics.

    For backward compatibility with bare basename globs (no ``/``, e.g.
    ``*_key*``) the basename is also tested for those patterns only.
    """
    if not patterns:
        return False
    basename = Path(file_path).name
    for pattern in patterns:
        regex = _glob_to_regex(pattern)
        if regex.fullmatch(file_path):
            return True
        if "/" not in pattern and fnmatch.fnmatchcase(basename, pattern):
            return True
    return False


def matches_any_pattern_ci(file_path: str, patterns: list[str]) -> bool:
    """Case-insensitive variant of :func:`matches_any_pattern`.

    Use this only when path casing should not affect the verdict —
    e.g. weak risk-hint heuristics where ``Login.py`` should be treated
    the same as ``login.py``. Strict patterns (env files, lockfiles,
    forks-profile globs, excluded patterns) must keep the
    case-sensitive matcher because their semantics depend on the
    exact filename agreed by the project.
    """
    if not patterns:
        return False
    return matches_any_pattern(
        file_path.lower(), [pattern.lower() for pattern in patterns]
    )


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

    # Upstream-new files (D_MISSING) by definition have lines_changed ==
    # estimate_total_lines, so ``change_ratio`` always pegs at the cap
    # and contributes a full 0.20 to the score — pushing harmless brand-
    # new files into AUTO_RISKY for no reason. Drop that dimension and
    # redistribute its weight so the remaining four still sum to 1.0,
    # mirroring the conflict_density==0 reality of these files.
    is_upstream_new = file_diff.change_category == FileChangeCategory.D_MISSING
    if is_upstream_new:
        dropped = weights.pop("change_ratio")
        scale = 1.0 / (1.0 - dropped)
        for k in weights:
            weights[k] *= scale

    size_score = min(1.0, (file_diff.lines_changed / 500) ** 0.5)

    total_lines = max(1, file_diff.lines_added + file_diff.lines_deleted)
    conflict_lines = sum(
        h.end_line_current - h.start_line_current
        for h in file_diff.hunks
        if h.has_conflict
    )
    conflict_density_score = min(1.0, conflict_lines / total_lines)

    if is_upstream_new:
        change_ratio_score = 0.0
    else:
        change_ratio = file_diff.lines_changed / estimate_total_lines(file_diff)
        change_ratio_score = min(1.0, change_ratio * 2)

    type_score_map = {
        ".py": 0.7,
        ".ts": 0.7,
        ".js": 0.6,
        ".java": 0.7,
        ".go": 0.7,
        ".rs": 0.8,
        ".yaml": 0.5,
        ".json": 0.4,
        ".toml": 0.4,
        ".md": 0.1,
        ".txt": 0.1,
        ".sql": 0.8,
        ".sh": 0.7,
    }
    ext = Path(file_diff.file_path).suffix.lower()
    type_score = type_score_map.get(ext, 0.5)

    security_score = 1.0 if file_diff.is_security_sensitive else 0.0

    raw_score = (
        weights["size"] * size_score
        + weights["conflict_density"] * conflict_density_score
        + weights.get("change_ratio", 0.0) * change_ratio_score
        + weights["file_type"] * type_score
        + weights["security"] * security_score
    )

    if matches_any_pattern(file_diff.file_path, config.always_take_target_patterns):
        return 0.1

    no_content_change = file_diff.lines_added == 0 and file_diff.lines_deleted == 0
    # Env-template files (.env.example / .env.sample / ...) match the
    # strict ``patterns`` glob (``**/.env.*``) but only hold placeholders.
    # Demote them to the weak risk_hint floor before the strong-signal
    # check below, otherwise plugin-style fork merges (one env template
    # per plugin) needlessly route every template through human review.
    is_env_template = matches_any_pattern(
        file_diff.file_path, config.security_sensitive.env_template_patterns
    )

    # Strong security signal — file path matches the strict patterns
    # (cert/key files, .env, exact-named credentials.<ext>, etc.). Floor
    # the score so ``always_require_human`` reliably picks it up.
    if (
        not no_content_change
        and not is_env_template
        and matches_any_pattern(file_diff.file_path, config.security_sensitive.patterns)
    ):
        return float(max(raw_score, 0.8))

    # Weak security signal — filename merely *mentions* secret/credential/
    # auth/etc., or matches an env-template glob. Add a hint bump so the
    # file usually clears AUTO_SAFE and gets a ConflictAnalyst look, but
    # don't escalate it all the way to HUMAN_REQUIRED on path heuristics
    # alone (the test_validate_credentials false-positive class). LLM
    # blending downstream has final word.
    # risk_hint matching is intentionally case-insensitive — these are
    # weak heuristics meant to catch ``Login.py`` / ``OAuth.ts`` /
    # ``OTP_handler.go`` style naming as well as their lowercase forms.
    # Strict ``patterns`` above stay case-sensitive on purpose: a real
    # ``.env`` file is conventionally lowercase and treating
    # ``MyConfig.txt`` as a credential file would over-flag.
    if not no_content_change and (
        is_env_template
        or matches_any_pattern_ci(
            file_diff.file_path, config.security_sensitive.risk_hint_patterns
        )
    ):
        bumped = raw_score + config.security_sensitive.risk_hint_bump
        return float(
            round(min(1.0, _apply_c_class_floor(bumped, file_diff, config)), 3)
        )

    return float(round(_apply_c_class_floor(raw_score, file_diff, config), 3))


def _apply_c_class_floor(
    score: float, file_diff: FileDiff, config: FileClassifierConfig
) -> float:
    """Lift C-class (both_changed) files above the auto_safe band when
    no stronger path-based signal already escalated. Pre-merge there are
    no conflict markers yet, so the conflict_density dimension is always
    0 for C-class — a structural three-way conflict can otherwise score
    as low as ~0.3 and slip into the auto_safe batch. Files with no
    content change are excluded so renames don't get spuriously bumped.
    """
    if file_diff.change_category != FileChangeCategory.C:
        return score
    if file_diff.lines_added == 0 and file_diff.lines_deleted == 0:
        return score
    return max(score, config.c_class_risk_floor)


async def compute_llm_risk_score(
    file_diff: FileDiff,
    llm_client: Any,
    rule_score: float,
    rule_weight: float = 0.6,
) -> float:
    from src.llm.prompts.risk_scoring_prompts import (
        build_risk_scoring_prompt,
        RISK_SCORING_SYSTEM,
    )

    prompt = build_risk_scoring_prompt(file_diff, rule_score)
    messages = [{"role": "user", "content": prompt}]

    try:
        raw = await llm_client.complete(messages, system=RISK_SCORING_SYSTEM)
        raw_str = str(raw).strip()
        if raw_str.startswith("```"):
            lines = raw_str.splitlines()
            raw_str = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
        data = json_lib.loads(raw_str)
        llm_score = float(data.get("llm_risk_score", rule_score))
        llm_score = max(0.0, min(1.0, llm_score))
    except Exception as e:
        logger.warning(
            "LLM risk scoring failed for %s: %s, falling back to rule score",
            file_diff.file_path,
            e,
        )
        return rule_score

    blended = rule_weight * rule_score + (1.0 - rule_weight) * llm_score
    return float(round(max(0.0, min(1.0, blended)), 3))


def is_security_sensitive(file_path: str, config: FileClassifierConfig) -> bool:
    # Env-template files match the strict ``**/.env.*`` glob but contain
    # only placeholders — exclude them from the strong-signal flag so
    # they fall back to the weak risk_hint path (auto_risky, not
    # human_required). See SecuritySensitiveConfig.env_template_patterns.
    if matches_any_pattern(file_path, config.security_sensitive.env_template_patterns):
        return False
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

    if file_diff.file_status == FileStatus.DELETED and file_diff.lines_added == 0:
        return RiskLevel.DELETED_ONLY

    # Committed conflict markers (<<<<<<< in the diff) are always a human concern:
    # the executor cannot resolve them automatically and any auto-apply would embed
    # the raw markers into the working tree.
    if file_diff.conflict_count > 0:
        return RiskLevel.HUMAN_REQUIRED

    force_safe_patterns = getattr(config, "force_auto_safe_patterns", [])
    if force_safe_patterns and not file_diff.is_security_sensitive:
        if matches_any_pattern(file_diff.file_path, force_safe_patterns):
            return RiskLevel.AUTO_SAFE

    risk_score = file_diff.risk_score

    if risk_score < 0.3:
        base_level = RiskLevel.AUTO_SAFE
    elif risk_score < 0.6:
        base_level = RiskLevel.AUTO_RISKY
    else:
        base_level = RiskLevel.HUMAN_REQUIRED

    # Category-C files (both fork and upstream modified) are never truly safe:
    # a clean patch apply may succeed but still drop semantic intent from one
    # side. Enforce a floor of AUTO_RISKY so the executor runs gates, and
    # escalate to HUMAN_REQUIRED when the diff is non-trivial.
    if file_diff.change_category == FileChangeCategory.C:
        if base_level == RiskLevel.AUTO_SAFE:
            if file_diff.lines_changed >= 20 or file_diff.is_security_sensitive:
                return RiskLevel.HUMAN_REQUIRED
            return RiskLevel.AUTO_RISKY
    return base_level


def _fork_deleted_skip_record(file_path: str) -> Any:
    """Build a SKIP FileDecisionRecord preserving fork's explicit delete.

    Local import to avoid models/agents → file_classifier circular dep.
    """
    from datetime import datetime
    from src.models.decision import (
        DecisionSource,
        FileDecisionRecord,
        MergeDecision,
    )
    from src.models.diff import FileStatus

    return FileDecisionRecord(
        file_path=file_path,
        file_status=FileStatus.DELETED,
        decision=MergeDecision.SKIP,
        decision_source=DecisionSource.AUTO_EXECUTOR,
        confidence=1.0,
        rationale=(
            "Fork explicitly deleted this file (FORK_DELETED divergence); "
            "preserving deletion rather than restoring from upstream "
            "(P-γ-1.5-B regression for R2 helper_test.go)."
        ),
        phase="auto_merge",
        agent="fork_delete_preserver",
        timestamp=datetime.now(),
    )


def is_fork_deleted(state: Any, file_path: str) -> bool:
    """Return True if ``file_path`` was explicitly deleted by the fork.

    Reads ``state.fork_divergence_map`` (populated in InitializePhase by
    :func:`compute_fork_divergence_map`). Distinguishes the FORK_DELETED
    case (base has file + fork removed it + upstream still has it) from
    the genuine D_MISSING case (file new in upstream after fork branched
    off).

    The classifier returns ``D_MISSING`` for both cases because both
    look "missing from fork". Downstream D_MISSING action sites must
    consult this helper before restoring from upstream — restoring a
    fork-deleted file silently re-introduces code the fork meant to
    drop (calibrated via R2 helper_test.go regression, P-γ-1.5-B).
    """
    div_map = getattr(state, "fork_divergence_map", None)
    if not div_map:
        return False
    value = div_map.get(file_path)
    if value is None:
        return False
    return bool(value == ForkDivergence.FORK_DELETED.value)


def classify_three_way(
    file_path: str,
    merge_base: str,
    head_ref: str,
    upstream_ref: str,
    git_tool: GitTool,
) -> FileChangeCategory:
    base_hash = git_tool.get_file_hash(merge_base, file_path)
    head_hash = git_tool.get_file_hash(head_ref, file_path)
    up_hash = git_tool.get_file_hash(upstream_ref, file_path)

    if head_hash is None and up_hash is not None:
        return FileChangeCategory.D_MISSING
    if head_hash is not None and up_hash is None:
        return FileChangeCategory.D_EXTRA
    if head_hash is None and up_hash is None:
        return FileChangeCategory.A
    if head_hash == up_hash:
        return FileChangeCategory.A
    if head_hash == base_hash:
        return FileChangeCategory.B
    if up_hash == base_hash:
        return FileChangeCategory.E
    return FileChangeCategory.C


def classify_all_files(
    merge_base: str,
    head_ref: str,
    upstream_ref: str,
    git_tool: GitTool,
) -> dict[str, FileChangeCategory]:
    base_hashes = git_tool.list_files_with_hashes(merge_base)
    head_hashes = git_tool.list_files_with_hashes(head_ref)
    up_hashes = git_tool.list_files_with_hashes(upstream_ref)
    all_paths = set(head_hashes) | set(up_hashes)

    result: dict[str, FileChangeCategory] = {}
    for file_path in sorted(all_paths):
        head_hash = head_hashes.get(file_path)
        up_hash = up_hashes.get(file_path)
        base_hash = base_hashes.get(file_path)

        if head_hash is None and up_hash is not None:
            cat = FileChangeCategory.D_MISSING
        elif head_hash is not None and up_hash is None:
            cat = FileChangeCategory.D_EXTRA
        elif head_hash is None and up_hash is None:
            cat = FileChangeCategory.A
        elif head_hash == up_hash:
            cat = FileChangeCategory.A
        elif head_hash == base_hash:
            cat = FileChangeCategory.B
        elif up_hash == base_hash:
            cat = FileChangeCategory.E
        else:
            cat = FileChangeCategory.C
        result[file_path] = cat
    return result


def compute_fork_divergence_map(
    merge_base: str,
    head_ref: str,
    upstream_ref: str,
    git_tool: GitTool,
) -> dict[str, ForkDivergence]:
    """P2-3 (§6.2 item 3): per-file fork-vs-upstream divergence
    classification used by judge to suppress false-positive critical
    issues when the divergence is intentional fork behavior.

    Returns one entry per file present in head, upstream, or base.
    Mapping rules:

    - head_hash != base_hash and base_hash is not None  →  FORK_MODIFIED
    - head_hash is None and base_hash is not None
      and up_hash is not None                           →  FORK_DELETED
    - head_hash is not None and up_hash is None         →  FORK_ONLY
    - head_hash == base_hash and up_hash != base_hash   →  UPSTREAM_ONLY_CHANGE
    - head_hash is None and base_hash is None
      and up_hash is not None                           →  UPSTREAM_ADDED
    - all equal / file absent everywhere                →  UNCHANGED
    """
    base_hashes = git_tool.list_files_with_hashes(merge_base) if merge_base else {}
    head_hashes = git_tool.list_files_with_hashes(head_ref)
    up_hashes = git_tool.list_files_with_hashes(upstream_ref)
    all_paths = set(head_hashes) | set(up_hashes) | set(base_hashes)

    result: dict[str, ForkDivergence] = {}
    for file_path in sorted(all_paths):
        head_hash = head_hashes.get(file_path)
        up_hash = up_hashes.get(file_path)
        base_hash = base_hashes.get(file_path)

        if head_hash is not None and base_hash is not None and head_hash != base_hash:
            result[file_path] = ForkDivergence.FORK_MODIFIED
        elif head_hash is None and base_hash is not None and up_hash is not None:
            result[file_path] = ForkDivergence.FORK_DELETED
        elif head_hash is not None and up_hash is None:
            result[file_path] = ForkDivergence.FORK_ONLY
        elif head_hash is None and base_hash is None and up_hash is not None:
            result[file_path] = ForkDivergence.UPSTREAM_ADDED
        elif (
            head_hash is not None
            and base_hash is not None
            and head_hash == base_hash
            and up_hash is not None
            and up_hash != base_hash
        ):
            result[file_path] = ForkDivergence.UPSTREAM_ONLY_CHANGE
        else:
            result[file_path] = ForkDivergence.UNCHANGED
    return result


def category_summary(
    categories: dict[str, FileChangeCategory],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for cat in FileChangeCategory:
        counts[cat.value] = 0
    for cat in categories.values():
        counts[cat.value] = counts.get(cat.value, 0) + 1
    return counts
