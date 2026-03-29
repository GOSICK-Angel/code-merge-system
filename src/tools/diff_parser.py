import re
from uuid import uuid4
from src.models.diff import FileDiff, DiffHunk, FileStatus, RiskLevel


def parse_unified_diff(raw_diff: str, file_path: str) -> list[DiffHunk]:
    hunks: list[DiffHunk] = []
    hunk_pattern = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)
    matches = list(hunk_pattern.finditer(raw_diff))

    for i, match in enumerate(matches):
        start_target = int(match.group(1))
        len_target = int(match.group(2) or 1)
        start_current = int(match.group(3))
        len_current = int(match.group(4) or 1)

        if i + 1 < len(matches):
            hunk_content = raw_diff[match.start():matches[i + 1].start()]
        else:
            hunk_content = raw_diff[match.start():]

        lines = hunk_content.splitlines()
        content_current_lines: list[str] = []
        content_target_lines: list[str] = []
        conflict_marker_lines: list[int] = []
        has_conflict = False

        for line_num, line in enumerate(lines[1:], start=1):
            if line.startswith("+"):
                content_current_lines.append(line[1:])
            elif line.startswith("-"):
                content_target_lines.append(line[1:])
            elif line.startswith(" "):
                content_current_lines.append(line[1:])
                content_target_lines.append(line[1:])
            if line.startswith("<<<<<<<") or line.startswith("=======") or line.startswith(">>>>>>>"):
                has_conflict = True
                conflict_marker_lines.append(line_num)

        hunk = DiffHunk(
            hunk_id=str(uuid4()),
            start_line_current=start_current,
            end_line_current=start_current + len_current,
            start_line_target=start_target,
            end_line_target=start_target + len_target,
            content_current="\n".join(content_current_lines),
            content_target="\n".join(content_target_lines),
            content_base=None,
            has_conflict=has_conflict,
            conflict_marker_lines=conflict_marker_lines,
        )
        hunks.append(hunk)

    return hunks


def parse_conflict_markers(content: str) -> list[DiffHunk]:
    hunks: list[DiffHunk] = []
    lines = content.splitlines()
    i = 0
    line_num = 0

    while i < len(lines):
        line = lines[i]
        if line.startswith("<<<<<<<"):
            current_lines: list[str] = []
            base_lines: list[str] = []
            target_lines: list[str] = []
            start_line = line_num
            conflict_marker_positions: list[int] = [line_num]
            i += 1
            line_num += 1

            section = "current"
            while i < len(lines):
                inner = lines[i]
                if inner.startswith("======="):
                    conflict_marker_positions.append(line_num)
                    if section == "current":
                        section = "target"
                    i += 1
                    line_num += 1
                elif inner.startswith(">>>>>>>"):
                    conflict_marker_positions.append(line_num)
                    i += 1
                    line_num += 1
                    break
                elif inner.startswith("|||||||"):
                    conflict_marker_positions.append(line_num)
                    section = "base"
                    i += 1
                    line_num += 1
                else:
                    if section == "current":
                        current_lines.append(inner)
                    elif section == "base":
                        base_lines.append(inner)
                    elif section == "target":
                        target_lines.append(inner)
                    i += 1
                    line_num += 1

            hunk = DiffHunk(
                hunk_id=str(uuid4()),
                start_line_current=start_line,
                end_line_current=start_line + len(current_lines),
                start_line_target=start_line,
                end_line_target=start_line + len(target_lines),
                content_current="\n".join(current_lines),
                content_target="\n".join(target_lines),
                content_base="\n".join(base_lines) if base_lines else None,
                has_conflict=True,
                conflict_marker_lines=conflict_marker_positions,
            )
            hunks.append(hunk)
        else:
            i += 1
            line_num += 1

    return hunks


def build_file_diff(
    file_path: str,
    raw_diff: str,
    file_status: FileStatus,
    base_content: str | None = None,
    current_content: str | None = None,
    target_content: str | None = None,
) -> FileDiff:
    hunks = parse_unified_diff(raw_diff, file_path)

    lines_added = sum(1 for line in raw_diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
    lines_deleted = sum(1 for line in raw_diff.splitlines() if line.startswith("-") and not line.startswith("---"))
    lines_changed = max(lines_added, lines_deleted)
    conflict_count = sum(1 for h in hunks if h.has_conflict)

    if current_content and "<<<<<<" in current_content:
        conflict_hunks = parse_conflict_markers(current_content)
        for ch in conflict_hunks:
            if ch.content_base is None and base_content is not None:
                pass
        hunks = conflict_hunks if conflict_hunks else hunks
        conflict_count = len(conflict_hunks)

    for hunk in hunks:
        if hunk.content_base is None and base_content is not None:
            object.__setattr__(hunk, "content_base", base_content)

    return FileDiff(
        file_path=file_path,
        file_status=file_status,
        risk_level=RiskLevel.AUTO_SAFE,
        risk_score=0.0,
        lines_added=lines_added,
        lines_deleted=lines_deleted,
        lines_changed=lines_changed,
        conflict_count=conflict_count,
        hunks=hunks,
        raw_diff=raw_diff,
    )


def detect_language(file_path: str) -> str | None:
    language_map = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".java": "java",
        ".go": "go",
        ".rs": "rust",
        ".rb": "ruby",
        ".php": "php",
        ".cs": "csharp",
        ".cpp": "cpp",
        ".c": "c",
        ".sh": "bash",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".json": "json",
        ".toml": "toml",
        ".sql": "sql",
        ".md": "markdown",
    }
    from pathlib import Path
    ext = Path(file_path).suffix.lower()
    return language_map.get(ext)
