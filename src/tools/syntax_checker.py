import ast
import json

import yaml as yaml_lib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SyntaxError_:
    line: int
    column: int
    message: str


@dataclass(frozen=True)
class SyntaxCheckResult:
    valid: bool
    errors: list[SyntaxError_] = field(default_factory=list)
    language: str = "unknown"


def check_syntax(file_path: str, content: str) -> SyntaxCheckResult:
    """Check syntax of file content based on file extension."""
    ext = Path(file_path).suffix.lower()

    checkers: dict[str, object] = {
        ".py": _check_python,
        ".json": _check_json,
        ".yaml": _check_yaml,
        ".yml": _check_yaml,
    }

    checker = checkers.get(ext)
    if checker is None:
        return SyntaxCheckResult(valid=True, errors=[], language=_ext_to_language(ext))

    if not callable(checker):
        return SyntaxCheckResult(valid=True, errors=[], language=_ext_to_language(ext))

    result: SyntaxCheckResult = checker(content)
    return result


def _check_python(content: str) -> SyntaxCheckResult:
    try:
        ast.parse(content)
        return SyntaxCheckResult(valid=True, errors=[], language="python")
    except SyntaxError as e:
        err = SyntaxError_(
            line=e.lineno or 0,
            column=e.offset or 0,
            message=str(e.msg) if hasattr(e, "msg") else str(e),
        )
        return SyntaxCheckResult(valid=False, errors=[err], language="python")


def _check_json(content: str) -> SyntaxCheckResult:
    # Empty / whitespace-only input is "no content to check", not a
    # syntax error. JSON's strict spec rejects empty strings, but in
    # this codebase Judge feeds in worktree files (including SKIP'd
    # ones that may be missing/empty) — treating them as critical
    # syntax errors leaks placeholder HumanDecisionRequest entries.
    if not content.strip():
        return SyntaxCheckResult(valid=True, errors=[], language="json")
    try:
        json.loads(content)
        return SyntaxCheckResult(valid=True, errors=[], language="json")
    except json.JSONDecodeError as e:
        err = SyntaxError_(line=e.lineno, column=e.colno, message=e.msg)
        return SyntaxCheckResult(valid=False, errors=[err], language="json")


def _check_yaml(content: str) -> SyntaxCheckResult:
    if not content.strip():
        return SyntaxCheckResult(valid=True, errors=[], language="yaml")
    try:
        yaml_lib.safe_load(content)
        return SyntaxCheckResult(valid=True, errors=[], language="yaml")
    except yaml_lib.YAMLError as e:
        line = 0
        col = 0
        msg = str(e)
        if hasattr(e, "problem_mark") and e.problem_mark is not None:
            line = e.problem_mark.line + 1
            col = e.problem_mark.column + 1
        err = SyntaxError_(line=line, column=col, message=msg)
        return SyntaxCheckResult(valid=False, errors=[err], language="yaml")


_LANG_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",
    ".tsx": "typescript",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
}


def _ext_to_language(ext: str) -> str:
    return _LANG_MAP.get(ext, "unknown")
