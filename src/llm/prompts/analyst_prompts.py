from src.models.diff import FileDiff


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

    return f"""Analyze this Git merge conflict and provide a structured analysis.

# Project Context
{project_context or "No project context provided."}

# File Information
Path: {file_diff.file_path}
Language: {language}
Lines added: {file_diff.lines_added}
Lines deleted: {file_diff.lines_deleted}
Conflict count: {file_diff.conflict_count}

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


def build_merge_suggestion_prompt(
    file_path: str,
    current_content: str,
    target_content: str,
    rationale: str,
    language: str,
) -> str:
    return f"""Generate a merged version of the following code that incorporates changes from both sides.

File: {file_path}
Language: {language}
Merge rationale: {rationale}

## Current version (fork)
```{language}
{current_content}
```

## Target version (upstream)
```{language}
{target_content}
```

Produce the best semantic merge that:
1. Preserves fork's private logic
2. Incorporates upstream improvements
3. Has no conflict markers
4. Is syntactically valid {language}

Return only the merged file content, no explanations."""
