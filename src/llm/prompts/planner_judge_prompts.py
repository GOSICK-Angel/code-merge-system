from __future__ import annotations

import hashlib
from pathlib import Path

from src.models.plan import MergePlan
from src.models.diff import FileDiff, RiskLevel
from src.models.plan_judge import PlanIssue
from src.models.plan_review import PlannerIssueResponse, IssueResponseAction
from src.tools.file_classifier import matches_any_pattern

REVIEW_SEGMENT_SIZE = 80


# Risk levels for which a classifier-vs-batch divergence is meaningful.
# Sentinel levels (BINARY, DELETED_ONLY, EXCLUDED) skip MISMATCH /
# NOT-BATCHED detection because they do not participate in batching at
# all. Defined up here so the safelist helpers can reference the symbol
# without relying on Python's late name binding inside function bodies.
_MISMATCH_TRACKED_LEVELS = frozenset(
    {RiskLevel.AUTO_SAFE, RiskLevel.AUTO_RISKY, RiskLevel.HUMAN_REQUIRED}
)


# Subset of SAFELIST_PATTERNS that represents lockfile-style manifests.
# Lockfiles can balloon to thousands of lines on routine dependency
# bumps; matching one of these triggers a per-file change-line ceiling
# (``safelist_lockfile_max_lines``) so a malicious supply-chain
# rewrite cannot tunnel through the safelist invisibly.
LOCKFILE_PATTERNS: list[str] = [
    "**/uv.lock",
    "**/poetry.lock",
    "**/pnpm-lock.yaml",
    "**/yarn.lock",
    "**/package-lock.json",
    "**/Pipfile.lock",
]


# Files matching one of these patterns AND with conflict_count=0,
# is_security_sensitive=False, batch_risk == classifier_risk are
# considered "obviously safe" — a segment composed entirely of such
# files can skip the LLM Judge call entirely. Only ecosystem-universal
# entries belong here; per-repo metadata (plugin position files, asset
# directories, ignore-rule files specific to one tool) must be added via
# `MergeConfig.plan_review.segment_safelist_patterns` rather than this
# list, so the generic agent stays decoupled from any single fork.
SAFELIST_PATTERNS: list[str] = [
    # Lock / dependency manifests
    *LOCKFILE_PATTERNS,
    "**/pyproject.toml",
    "**/requirements.txt",
    "**/requirements-*.txt",
    # Universal VCS / ignore metadata
    "**/.gitignore",
    "**/.gitkeep",
    "**/.gitattributes",
    # Routine docs
    "**/CHANGELOG.md",
    "**/LICENSE",
    "**/LICENSE.txt",
    "**/LICENSE.md",
]


def is_segment_obviously_safe(
    segment: list[FileDiff],
    batch_risk_map: dict[str, str],
    extra_safelist_patterns: list[str] | None = None,
    lockfile_max_lines: int = 1000,
) -> bool:
    """Return True iff EVERY file in the segment is trivially safe.

    A file is trivially safe when:
      - conflict_count == 0
      - is_security_sensitive == False
      - batch_risk == classifier risk_level (no MISMATCH)
      - file path is in the plan (not NOT-BATCHED)
      - one of:
          * path matches a LOCKFILE_PATTERNS glob AND
            lines_added + lines_deleted < ``lockfile_max_lines``, OR
          * path matches a non-lockfile SAFELIST_PATTERNS glob, OR
          * path matches an entry in ``extra_safelist_patterns``
            (per-repo extension supplied by caller), OR
          * lines_added + lines_deleted < 50 AND extension in
            {.yaml, .toml, .json, .md, .txt, .lock} AND file_path
            does NOT contain any risk-keyword substring (auth, otp,
            verify, secret, credential, password, token, login,
            permission, signature)
    """
    extra = extra_safelist_patterns or []
    risk_keywords = (
        "auth",
        "otp",
        "verify",
        "secret",
        "credential",
        "password",
        "token",
        "login",
        "permission",
        "signature",
        "oauth",
        "signin",
        "signup",
    )
    safe_exts = {".yaml", ".yml", ".toml", ".json", ".md", ".txt", ".lock"}

    for fd in segment:
        if fd.conflict_count > 0:
            return False
        if fd.is_security_sensitive:
            return False

        batch_rl = batch_risk_map.get(fd.file_path)
        if batch_rl is None:
            return False
        if (
            fd.risk_level in _MISMATCH_TRACKED_LEVELS
            and batch_rl != fd.risk_level.value
        ):
            return False

        if matches_any_pattern(fd.file_path, LOCKFILE_PATTERNS):
            if fd.lines_added + fd.lines_deleted >= lockfile_max_lines:
                return False
            continue
        if matches_any_pattern(fd.file_path, SAFELIST_PATTERNS):
            continue
        if extra and matches_any_pattern(fd.file_path, extra):
            continue

        ext = Path(fd.file_path).suffix.lower()
        if ext not in safe_exts:
            return False

        if fd.lines_added + fd.lines_deleted >= 50:
            return False

        path_lower = fd.file_path.lower()
        if any(kw in path_lower for kw in risk_keywords):
            return False

    return True


def compute_segment_signature(
    segment: list[FileDiff],
    batch_risk_map: dict[str, str],
) -> str:
    """Stable hash of (file_path, batch_risk) tuples for cache lookup."""
    pairs = sorted(
        (fd.file_path, batch_risk_map.get(fd.file_path, "")) for fd in segment
    )
    blob = "\n".join(f"{p}\t{r}" for p, r in pairs).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def precheck_plan_integrity(
    plan: MergePlan,
    file_diffs: list[FileDiff],
) -> list[PlanIssue]:
    """Deterministically detect MISMATCH (classifier vs batch risk
    divergence) and NOT-BATCHED (classifier produced verdict, plan
    dropped the file) issues. These do not need an LLM round-trip.

    Returns one PlanIssue per offending file; callers merge with LLM
    issues by file_path.
    """
    batch_risk_map: dict[str, RiskLevel] = {}
    for batch in plan.phases:
        for fp in batch.file_paths:
            batch_risk_map[fp] = batch.risk_level

    issues: list[PlanIssue] = []
    for fd in file_diffs:
        if fd.risk_level not in _MISMATCH_TRACKED_LEVELS:
            continue
        batch_rl = batch_risk_map.get(fd.file_path)
        if batch_rl is None:
            issues.append(
                PlanIssue(
                    file_path=fd.file_path,
                    # File is absent from the plan entirely — no
                    # current classification to cite. ``None`` is
                    # honest about that vs. the prior AUTO_SAFE
                    # placeholder.
                    current_classification=None,
                    suggested_classification=fd.risk_level,
                    reason=(
                        "NOT-BATCHED: classifier produced a verdict "
                        f"({fd.risk_level.value}) but the plan dropped "
                        "this file from every batch (data loss)."
                    ),
                    issue_type="wrong_batch",
                )
            )
            continue
        if batch_rl != fd.risk_level:
            issues.append(
                PlanIssue(
                    file_path=fd.file_path,
                    current_classification=batch_rl,
                    suggested_classification=fd.risk_level,
                    reason=(
                        f"MISMATCH: classifier risk_level={fd.risk_level.value} "
                        f"but plan placed file in {batch_rl.value} batch — "
                        "trust the classifier."
                    ),
                    issue_type="risk_underestimated",
                )
            )
    return issues


_PLANNER_JUDGE_SYSTEM_BASE = """You are an independent reviewer of code merge plans. Your task is to verify that \
high-risk files are correctly classified — NOT to find as many issues as possible.

## When to raise an issue

Only raise an issue when you have CONCRETE evidence from the diff data:
- `conflict_count > 0` → file may need `human_required`
- `is_security_sensitive = true` → file must be `human_required` or `auto_risky` at minimum
- A file is obviously security-related (auth, crypto, secrets, permissions) but classified `auto_safe`
- Batch grouping creates a dangerous ordering dependency (specific files must be named)

## When NOT to raise an issue

- File has `conflict_count = 0` AND `is_security_sensitive = false` → its `auto_safe` classification is almost certainly correct; do NOT suggest upgrading it
- File has a large diff but no conflicts → `auto_risky` is acceptable; do NOT escalate to `human_required`
- You are uncertain or the concern is hypothetical → stay silent; do not flag speculatively

## Calibration

A well-formed plan for a typical merge will have most files as `auto_safe`. If you find yourself flagging \
more than 20% of files, reconsider — you are likely being too aggressive.

IMPORTANT: When prior review rounds are shown, focus ONLY on:
1. Issues that were NOT resolved by the Planner (still open).
2. NEW issues you discover that were not raised before.
Do NOT re-raise issues that have already been resolved. If all prior issues are resolved and no new issues are found, approve the plan.

IMPORTANT: You MUST respond with ONLY a single JSON object. No markdown, no explanations, no text before or after the JSON.
Your entire response must be valid JSON that can be parsed by json.loads()."""

_PLANNER_JUDGE_SYSTEM_ZH_SUFFIX = """

语言要求（最高优先级）：
- "summary" 字段必须使用中文撰写。
- 每个 issue 的 "reason" 字段必须使用中文撰写。
- 禁止在这两个字段中使用英文句子，技术术语（如文件路径、枚举值）除外。"""


def get_planner_judge_system(lang: str = "en") -> str:
    if lang == "zh":
        return _PLANNER_JUDGE_SYSTEM_BASE + _PLANNER_JUDGE_SYSTEM_ZH_SUFFIX
    return _PLANNER_JUDGE_SYSTEM_BASE


def _build_file_manifest(
    file_diffs: list[FileDiff],
    batch_risk_map: dict[str, str] | None = None,
) -> str:
    """Compact one-line-per-file manifest: path + classifier classification +
    flags.  Files whose batch risk_level diverges from the classifier verdict
    (or whose path was dropped from the plan entirely) get an explicit MISMATCH
    or NOT-BATCHED flag so the reviewer can catch silent demotion or data loss
    without having to scan every line.

    Only AUTO_SAFE / AUTO_RISKY / HUMAN_REQUIRED participate in mismatch
    detection; sentinel levels like EXCLUDED / BINARY / DELETED_ONLY mean the
    classifier intentionally did not produce a merge verdict, so divergence
    from the batch risk is expected and not flagged."""
    lines: list[str] = []
    for fd in file_diffs:
        flags: list[str] = []
        if fd.is_security_sensitive:
            flags.append("SEC")
        if fd.conflict_count > 0:
            flags.append(f"conflicts={fd.conflict_count}")
        if fd.lines_added + fd.lines_deleted > 100:
            flags.append(f"+{fd.lines_added}/-{fd.lines_deleted}")
        if batch_risk_map is not None and fd.risk_level in _MISMATCH_TRACKED_LEVELS:
            batch_rl = batch_risk_map.get(fd.file_path)
            if batch_rl is None:
                flags.append("NOT-BATCHED")
            elif batch_rl != fd.risk_level.value:
                flags.append(f"batch={batch_rl} MISMATCH")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        lines.append(f"  {fd.file_path}: {fd.risk_level.value}{flag_str}")
    return "\n".join(lines)


def classify_prior_issues(
    prior_issues: list[PlanIssue],
    current_classifications: dict[str, RiskLevel],
) -> tuple[list[PlanIssue], list[PlanIssue]]:
    resolved: list[PlanIssue] = []
    still_open: list[PlanIssue] = []
    for issue in prior_issues:
        current_rl = current_classifications.get(issue.file_path)
        if current_rl == issue.suggested_classification:
            resolved.append(issue)
        else:
            still_open.append(issue)
    return resolved, still_open


def _build_prior_issues_section(
    resolved: list[PlanIssue],
    still_open: list[PlanIssue],
    revision_round: int,
    lang: str,
) -> str:
    if not resolved and not still_open:
        return ""

    if lang == "zh":
        header = f"\n## 前轮审查历史（当前为第 {revision_round} 轮）\n"
        resolved_hdr = "### ✅ 已解决（无需重复提出）\n"
        open_hdr = "### ❌ 仍未解决（仍需关注）\n"
        none_str = "无\n"
        focus = (
            "\n⚠️ 重点提示：已解决的问题请勿重复提出。"
            "仅报告上方「仍未解决」列表中的问题和你新发现的问题。\n"
        )
    else:
        header = f"\n## Prior Review History (this is round {revision_round})\n"
        resolved_hdr = "### ✅ Resolved (do NOT re-raise)\n"
        open_hdr = "### ❌ Still Open (still need attention)\n"
        none_str = "None\n"
        focus = (
            "\n⚠️ FOCUS: Do NOT re-raise resolved issues. "
            "Only report issues from the 'Still Open' list above and any NEW issues you discover.\n"
        )

    lines = [header]

    lines.append(resolved_hdr)
    if resolved:
        for iss in resolved:
            curr = (
                iss.current_classification.value
                if iss.current_classification is not None
                else "(not in plan)"
            )
            lines.append(
                f"  - `{iss.file_path}`: {curr} → "
                f"{iss.suggested_classification.value} ✔\n"
            )
    else:
        lines.append(none_str)

    lines.append(open_hdr)
    if still_open:
        for iss in still_open:
            curr = (
                iss.current_classification.value
                if iss.current_classification is not None
                else "(not in plan)"
            )
            lines.append(
                f"  - `{iss.file_path}`: requested {curr} → "
                f"{iss.suggested_classification.value}, still at "
                f"{curr}\n"
            )
    else:
        lines.append(none_str)

    lines.append(focus)
    return "".join(lines)


def _build_planner_responses_section(
    planner_responses: list[PlannerIssueResponse],
    lang: str,
) -> str:
    if not planner_responses:
        return ""

    rejected = [r for r in planner_responses if r.action == IssueResponseAction.REJECT]
    discussed = [
        r for r in planner_responses if r.action == IssueResponseAction.DISCUSS
    ]
    accepted = [r for r in planner_responses if r.action == IssueResponseAction.ACCEPT]

    if lang == "zh":
        header = "\n## Planner 对你上轮建议的回应\n"
        acc_hdr = f"### ✅ 已接受 ({len(accepted)} 条)\n"
        rej_hdr = (
            f"### ❌ 已拒绝 ({len(rejected)} 条) — 请评估 Planner 的理由是否成立\n"
        )
        disc_hdr = f"### 💬 需讨论 ({len(discussed)} 条) — Planner 提出了替代方案\n"
        focus = (
            "\n⚠️ 重点：对于 Planner 已接受的建议，无需再次提出。"
            "对于被拒绝的建议，如果 Planner 的理由成立则放弃该建议；"
            "如果你仍然认为存在风险，请给出更具体的证据。"
            "对于讨论中的建议，评估 Planner 的替代方案是否可接受。\n"
        )
    else:
        header = "\n## Planner's Responses to Your Prior Suggestions\n"
        acc_hdr = f"### ✅ Accepted ({len(accepted)} items)\n"
        rej_hdr = f"### ❌ Rejected ({len(rejected)} items) — evaluate if Planner's reasoning holds\n"
        disc_hdr = f"### 💬 Under Discussion ({len(discussed)} items) — Planner proposed alternatives\n"
        focus = (
            "\n⚠️ FOCUS: Do NOT re-raise accepted items. "
            "For rejected items, if the Planner's reasoning is sound, drop the issue; "
            "if you still see risk, provide more specific evidence. "
            "For discussed items, evaluate whether the counter-proposal is acceptable.\n"
        )

    lines = [header]

    lines.append(acc_hdr)
    for r in accepted:
        lines.append(f"  - `{r.file_path}`: {r.reason}\n")
    if not accepted:
        lines.append("None\n")

    lines.append(rej_hdr)
    for r in rejected:
        cp = f" | Counter: {r.counter_proposal}" if r.counter_proposal else ""
        lines.append(f"  - `{r.file_path}`: {r.reason}{cp}\n")
    if not rejected:
        lines.append("None\n")

    lines.append(disc_hdr)
    for r in discussed:
        cp = f" | Proposal: {r.counter_proposal}" if r.counter_proposal else ""
        lines.append(f"  - `{r.file_path}`: {r.reason}{cp}\n")
    if not discussed:
        lines.append("None\n")

    lines.append(focus)
    return "".join(lines)


def build_plan_review_prompt(
    plan: MergePlan,
    file_diffs: list[FileDiff],
    lang: str = "en",
    *,
    revision_round: int = 0,
    prior_resolved: list[PlanIssue] | None = None,
    prior_still_open: list[PlanIssue] | None = None,
    planner_responses: list[PlannerIssueResponse] | None = None,
) -> str:
    phases_summary = "\n".join(
        f"  Phase {batch.phase.value}: {len(batch.file_paths)} files ({batch.risk_level.value})"
        for batch in plan.phases
    )

    batch_risk_map: dict[str, str] = {}
    for batch in plan.phases:
        for fp in batch.file_paths:
            batch_risk_map[fp] = batch.risk_level.value

    manifest = _build_file_manifest(file_diffs, batch_risk_map=batch_risk_map)

    prior_section = ""
    if revision_round > 0:
        prior_section = _build_prior_issues_section(
            prior_resolved or [],
            prior_still_open or [],
            revision_round,
            lang,
        )

    planner_response_section = ""
    if revision_round > 0 and planner_responses:
        planner_response_section = _build_planner_responses_section(
            planner_responses, lang
        )

    return f"""Review the following merge plan for quality and correctness.

## Merge Plan Summary
- Upstream: {plan.upstream_ref}
- Fork: {plan.fork_ref}
- Total files: {plan.risk_summary.total_files}
- Auto-safe: {plan.risk_summary.auto_safe_count}
- Auto-risky: {plan.risk_summary.auto_risky_count}
- Human required: {plan.risk_summary.human_required_count}

## Phase Breakdown
{phases_summary}

## All Files (path: classification [flags])
{manifest}
{prior_section}{planner_response_section}
## Your Review Tasks (raise issues ONLY with concrete evidence)
1. Files where `is_security_sensitive=true` but classified below `auto_risky` → flag
2. Files where `conflict_count > 0` but NOT classified `human_required` → flag
3. Files that are obviously security-critical by name/path but classified `auto_safe` → flag
4. Dangerous batch ordering that would break a dependency → flag (name both files)
5. Files with `conflict_count=0` and `is_security_sensitive=false` → do NOT flag regardless of diff size
{"6. Do NOT re-raise issues already marked as resolved above" if revision_round > 0 else ""}

Note: MISMATCH / NOT-BATCHED divergence between the classifier and the
batch is verified deterministically before this prompt is built; you
do not need to re-detect it. Focus on semantic / path-based judgment.

Return JSON with:
{{
  "result": "approved" | "revision_needed" | "critical_replan",
  "issues": [
    {{
      "file_path": "path/to/file",
      "current_classification": "<MUST be exactly one of: auto_safe, auto_risky, human_required, deleted_only, binary, excluded>",
      "suggested_classification": "<MUST be exactly one of: auto_safe, auto_risky, human_required, deleted_only, binary, excluded>",
      "reason": "Specific reason why classification is wrong",
      "issue_type": "risk_underestimated | wrong_batch | missing_dependency | security_missed"
    }}
  ],
  "approved_files_count": 0,
  "flagged_files_count": 0,
  "summary": "Overall assessment"
}}

CRITICAL: Each issue MUST reference a SINGLE file_path. The "current_classification" and "suggested_classification" fields MUST be exactly one of the enum values listed above — do NOT combine multiple values or add free text.
{"⚠️ 语言要求：'summary' 和每个 issue 的 'reason' 字段必须使用中文撰写，禁止使用英文句子（技术术语如文件路径、枚举值除外）。" if lang == "zh" else ""}
Respond with ONLY the JSON object. No other text."""


def build_segment_plan_review_prompt(
    plan: MergePlan,
    file_segment: list[FileDiff],
    segment_idx: int,
    total_segments: int,
    total_files: int,
    lang: str = "en",
    *,
    revision_round: int = 0,
    prior_resolved: list[PlanIssue] | None = None,
    prior_still_open: list[PlanIssue] | None = None,
    planner_responses: list[PlannerIssueResponse] | None = None,
) -> str:
    """Build a review prompt for one segment of the file list.

    Includes the overall plan context (phases + risk counts) for ordering
    decisions, then the manifest for only the files in this segment.
    """
    phases_summary = "\n".join(
        f"  Phase {batch.phase.value}: {len(batch.file_paths)} files ({batch.risk_level.value})"
        for batch in plan.phases
    )

    batch_risk_map: dict[str, str] = {}
    for batch in plan.phases:
        for fp in batch.file_paths:
            batch_risk_map[fp] = batch.risk_level.value

    manifest = _build_file_manifest(file_segment, batch_risk_map=batch_risk_map)

    prior_section = ""
    if revision_round > 0:
        prior_section = _build_prior_issues_section(
            prior_resolved or [],
            prior_still_open or [],
            revision_round,
            lang,
        )

    planner_response_section = ""
    if revision_round > 0 and planner_responses:
        planner_response_section = _build_planner_responses_section(
            planner_responses, lang
        )

    segment_label = (
        f"Segment {segment_idx + 1} of {total_segments} "
        f"({len(file_segment)} files; total plan: {total_files} files)"
    )

    return f"""Review segment {segment_idx + 1} of {total_segments} of a merge plan.
{segment_label}

## Overall Plan Context
- Upstream: {plan.upstream_ref}
- Fork: {plan.fork_ref}
- Total files in plan: {total_files}
- Auto-safe: {plan.risk_summary.auto_safe_count}
- Auto-risky: {plan.risk_summary.auto_risky_count}
- Human required: {plan.risk_summary.human_required_count}

## Phase Breakdown (full plan — for ordering decisions)
{phases_summary}

## Files in This Segment (path: classification [flags])
{manifest}
{prior_section}{planner_response_section}
## Your Review Tasks (raise issues ONLY with concrete evidence)
1. Files where `is_security_sensitive=true` but classified below `auto_risky` → flag
2. Files where `conflict_count > 0` but NOT classified `human_required` → flag
3. Files that are obviously security-critical by name/path but classified `auto_safe` → flag
4. Dangerous batch ordering that would break a dependency → flag (name both files)
5. Files with `conflict_count=0` and `is_security_sensitive=false` → do NOT flag regardless of diff size
{"6. Do NOT re-raise issues already marked as resolved above" if revision_round > 0 else ""}

Note: MISMATCH / NOT-BATCHED divergence between the classifier and the
batch is verified deterministically before this prompt is built; you
do not need to re-detect it. Focus on semantic / path-based judgment.

Only report issues for files listed in this segment. The other segments will cover the remaining files.

Return JSON with:
{{
  "result": "approved" | "revision_needed" | "critical_replan",
  "issues": [
    {{
      "file_path": "path/to/file",
      "current_classification": "<MUST be exactly one of: auto_safe, auto_risky, human_required, deleted_only, binary, excluded>",
      "suggested_classification": "<MUST be exactly one of: auto_safe, auto_risky, human_required, deleted_only, binary, excluded>",
      "reason": "Specific reason why classification is wrong",
      "issue_type": "risk_underestimated | wrong_batch | missing_dependency | security_missed"
    }}
  ],
  "approved_files_count": 0,
  "flagged_files_count": 0,
  "summary": "Assessment of this segment"
}}

CRITICAL: Each issue MUST reference a SINGLE file_path from this segment. The "current_classification" and "suggested_classification" fields MUST be exactly one of the enum values listed above.
{"⚠️ 语言要求：'summary' 和每个 issue 的 'reason' 字段必须使用中文撰写，禁止使用英文句子（技术术语如文件路径、枚举值除外）。" if lang == "zh" else ""}
Respond with ONLY the JSON object. No other text."""
