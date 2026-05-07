from __future__ import annotations

from typing import Any

from src.models.diff import FileDiff

_ROUND_PER_VERSION_CHARS = 1000


def _fmt_version(content: str | None, language: str) -> str:
    if not content:
        return "*(not available)*"
    trimmed = content[:_ROUND_PER_VERSION_CHARS]
    if len(content) > _ROUND_PER_VERSION_CHARS:
        trimmed += "\n... [truncated]"
    return f"```{language}\n{trimmed}\n```"


def build_commit_round_prompt(
    round_commits: list[dict[str, Any]],
    file_three_way: dict[str, tuple[str | None, str | None, str | None]],
    file_languages: dict[str, str],
    project_context: str = "",
) -> str:
    commit_summary = "\n".join(
        f"  - {c['sha'][:8]}: {c.get('message', '')}  ({len(c.get('files', []))} files)"
        for c in round_commits
    )

    file_sections: list[str] = []
    for fp, (base_c, current_c, target_c) in file_three_way.items():
        lang = file_languages.get(fp, "")
        file_sections.append(
            f"## {fp}  (language: {lang})\n"
            f"### Base (merge-base)\n{_fmt_version(base_c, lang)}\n"
            f"### Fork (current branch)\n{_fmt_version(current_c, lang)}\n"
            f"### Upstream (commit change)\n{_fmt_version(target_c, lang)}"
        )

    return (
        f"Analyze the following {len(file_three_way)} files from "
        f"{len(round_commits)} upstream commits being merged into a fork.\n\n"
        f"# Project Context\n{project_context or 'No project context provided.'}\n\n"
        f"# Commits in this round\n{commit_summary}\n\n"
        f"# File Contents\n"
        + "\n\n".join(file_sections)
        + """

For every file above provide a conflict analysis. Return JSON:
{
  "files": [
    {
      "file_path": "<exact path>",
      "conflict_type": "concurrent_modification | logic_contradiction | semantic_equivalent | dependency_update | interface_change | deletion_vs_modification | refactor_vs_feature | configuration | unknown",
      "recommended_strategy": "take_target | take_current | semantic_merge | escalate_human",
      "confidence": 0.85,
      "can_coexist": true,
      "is_security_sensitive": false,
      "rationale": "concise explanation",
      "upstream_intent": {"description": "...", "intent_type": "bugfix | refactor | feature | upgrade | config", "confidence": 0.9},
      "fork_intent": {"description": "...", "intent_type": "bugfix | refactor | feature | upgrade | config", "confidence": 0.8}
    }
  ]
}"""
    )


ANALYST_SYSTEM = """You are a professional code merge expert specializing in semantic analysis of Git conflicts.
Your task is to deeply analyze each conflict point, understand the intent of both sides,
and provide merge recommendations with confidence scores.
Always provide specific, actionable recommendations based on code semantics, not just syntax."""


def build_conflict_analysis_prompt(
    file_diff: FileDiff,
    base_content: str | None,
    current_content: str | None,
    target_content: str | None,
    project_context: str,
) -> str:
    language = file_diff.language or "unknown"
    base_section = (
        f"```{language}\n{base_content}\n```" if base_content else "Not available"
    )
    current_section = (
        f"```{language}\n{current_content}\n```" if current_content else "Not available"
    )
    target_section = (
        f"```{language}\n{target_content}\n```" if target_content else "Not available"
    )

    fork_added = file_diff.lines_added
    fork_deleted = file_diff.lines_deleted
    upstream_added = file_diff.upstream_lines_added
    upstream_deleted = file_diff.upstream_lines_deleted
    fork_total = fork_added + fork_deleted
    upstream_total = upstream_added + upstream_deleted

    if upstream_total == 0 and fork_total == 0:
        size_signal = "Both sides made no line changes (suspect rename / mode-only)."
    elif upstream_total == 0:
        size_signal = (
            "FORK changed lines only — upstream did not modify this file. "
            "Strongly prefer take_current."
        )
    elif fork_total == 0:
        size_signal = (
            "UPSTREAM changed lines only — fork did not modify this file. "
            "Strongly prefer take_target."
        )
    else:
        ratio = fork_total / upstream_total
        if ratio >= 5.0:
            size_signal = (
                f"FORK changes ({fork_total} lines) dominate upstream "
                f"({upstream_total} lines) by {ratio:.1f}x. Prefer take_current "
                f"or semantic_merge — take_target would discard substantial "
                f"fork customization."
            )
        elif ratio <= 0.2:
            size_signal = (
                f"UPSTREAM changes ({upstream_total} lines) dominate fork "
                f"({fork_total} lines). take_target is usually safe, but "
                f"verify that fork's small change isn't load-bearing."
            )
        else:
            size_signal = (
                f"Both sides made comparable changes "
                f"(fork={fork_total} vs upstream={upstream_total} lines). "
                f"semantic_merge is the default; only choose take_target / "
                f"take_current if one side's change clearly subsumes the other."
            )

    return f"""Analyze this Git merge conflict and provide a structured analysis.

# Project Context
{project_context or "No project context provided."}

# File Information
Path: {file_diff.file_path}
Language: {language}
Fork-side change (base→fork): +{fork_added} / -{fork_deleted}
Upstream-side change (base→upstream): +{upstream_added} / -{upstream_deleted}
Conflict count: {file_diff.conflict_count}

# Change-volume signal
{size_signal}

# Three-way Diff

## Common ancestor version (merge-base)
{base_section}

## Current version (fork's modifications)
{current_section}

## Target version (upstream's modifications)
{target_section}

# Analysis Task
Analyze this conflict and output:
1. conflict_type: one of concurrent_modification, logic_contradiction, semantic_equivalent,
   dependency_update, interface_change, deletion_vs_modification, refactor_vs_feature, configuration, unknown
2. upstream_intent: upstream modification intent (type, description, confidence)
3. fork_intent: fork modification intent (type, description, confidence)
4. can_coexist: whether both modifications can coexist
5. recommended_strategy: take_current, take_target, semantic_merge, escalate_human
6. confidence: overall confidence (0.0 to 1.0)
7. rationale: reasoning explanation

Return JSON:
{{
  "conflict_type": "concurrent_modification",
  "upstream_intent": {{
    "description": "What upstream changed and why",
    "intent_type": "bugfix | refactor | feature | upgrade | config",
    "confidence": 0.8
  }},
  "fork_intent": {{
    "description": "What fork changed and why",
    "intent_type": "bugfix | refactor | feature | upgrade | config",
    "confidence": 0.8
  }},
  "can_coexist": true,
  "recommended_strategy": "semantic_merge",
  "confidence": 0.75,
  "rationale": "Detailed explanation of the analysis and recommendation",
  "is_security_sensitive": false
}}"""
