from src.models.diff import FileDiff
from src.models.conflict import ConflictAnalysis


EXECUTOR_SYSTEM = """You are a code merge executor. Your task is to apply merge decisions to files,
performing semantic merges when needed. You must be precise and preserve all important logic from both branches.
Never lose code that may be functionally important."""


def build_semantic_merge_prompt(
    file_diff: FileDiff,
    conflict_analysis: ConflictAnalysis,
    current_content: str,
    target_content: str,
    project_context: str,
) -> str:
    language = file_diff.language or "unknown"
    rec_val = conflict_analysis.recommended_strategy.value if hasattr(
        conflict_analysis.recommended_strategy, "value"
    ) else conflict_analysis.recommended_strategy

    return f"""Perform a semantic merge of the following two versions of a file.

# Project Context
{project_context or "No project context provided."}

# File: {file_diff.file_path}
Language: {language}

# Conflict Analysis
- Type: {conflict_analysis.conflict_type.value if hasattr(conflict_analysis.conflict_type, 'value') else conflict_analysis.conflict_type}
- Recommended strategy: {rec_val}
- Rationale: {conflict_analysis.rationale}
- Confidence: {conflict_analysis.confidence}

# Current version (fork)
```{language}
{current_content}
```

# Target version (upstream)
```{language}
{target_content}
```

Produce a merged file that:
1. Preserves fork's private/custom logic
2. Incorporates upstream bug fixes and improvements
3. Contains NO conflict markers (<<<<<<<, =======, >>>>>>>)
4. Is syntactically valid

Return ONLY the merged file content."""


def build_dispute_prompt(
    file_diff: FileDiff,
    current_classification: str,
    observed_issues: str,
) -> str:
    return f"""As an Executor agent, you have observed potential issues with the current file classification.

File: {file_diff.file_path}
Current classification: {current_classification}
Lines added: {file_diff.lines_added}
Lines deleted: {file_diff.lines_deleted}
Conflicts: {file_diff.conflict_count}
Security sensitive: {file_diff.is_security_sensitive}

Observed issues:
{observed_issues}

Provide a structured dispute request explaining:
1. Why the current classification may be incorrect
2. What classification you suggest
3. The potential impact of proceeding with the current classification

Return JSON:
{{
  "dispute_reason": "Specific reason why classification is wrong",
  "suggested_reclassification": {{"path/to/file": "human_required"}},
  "impact_assessment": "What could go wrong if we proceed with current classification",
  "evidence": "Relevant code snippet or diff excerpt"
}}"""
