"""merge init — generate a CLAUDE.md for the target repository.

Scans the repo structure and representative metadata files, then calls
an LLM to produce a CLAUDE.md draft describing the project's merge
conventions. Subsequent `merge` runs read this file automatically.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from rich.console import Console

from src.cli.commands.setup import _confirm

console = Console()

_README_NAMES = ("CLAUDE.md", "README.md", "README.rst", "README.txt")
_META_GLOBS = (
    "manifest.yaml",
    "pyproject.toml",
    "setup.py",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
)
_MAX_FILE_CHARS = 3_000
_MAX_TREE_LINES = 80


def init_command_impl(repo_path: str) -> None:
    root = Path(repo_path).resolve()
    if not (root / ".git").exists():
        console.print(f"[red]{root} is not a git repository.[/red]")
        return

    out_path = root / "CLAUDE.md"
    if out_path.exists():
        console.print(f"[yellow]CLAUDE.md already exists in {root}.[/yellow]")
        if not _confirm("Overwrite?", default=False):
            return

    console.print(f"[cyan]Scanning repository: {root}[/cyan]")
    repo_summary = _build_repo_summary(root)

    api_key = _detect_api_key()
    if not api_key:
        console.print(
            "[yellow]No LLM API key found (ANTHROPIC_API_KEY or OPENAI_API_KEY). "
            "Generating template instead.[/yellow]"
        )
        draft = _generate_template(root, repo_summary)
    else:
        console.print("[cyan]Calling LLM to generate CLAUDE.md draft…[/cyan]")
        draft = _call_llm(api_key, repo_summary)

    out_path.write_text(draft, encoding="utf-8")
    console.print(f"[green]✓ CLAUDE.md written to {out_path}[/green]")
    console.print(
        "Review and adjust the file, then run [bold]merge <branch>[/bold] as usual."
    )


def _build_repo_summary(root: Path) -> str:
    sections: list[str] = []

    git_remote = _run_git(root, ["remote", "get-url", "origin"]) or ""
    git_desc = _run_git(root, ["log", "--oneline", "-5"]) or ""
    sections.append(f"Repository: {git_remote.strip()}\n\nRecent commits:\n{git_desc}")

    tree = _dir_tree(root, max_lines=_MAX_TREE_LINES)
    sections.append(f"Directory structure (top 2 levels):\n{tree}")

    for name in _README_NAMES:
        p = root / name
        if p.exists() and name != "CLAUDE.md":
            text = _read_truncated(p)
            sections.append(f"Contents of {name}:\n{text}")
            break

    meta_files: list[str] = []
    for glob in _META_GLOBS:
        for p in sorted(root.rglob(glob))[:3]:
            meta_files.append(f"--- {p.relative_to(root)} ---\n{_read_truncated(p)}")
    if meta_files:
        sections.append("Representative metadata files:\n" + "\n\n".join(meta_files))

    return "\n\n===\n\n".join(sections)


def _dir_tree(root: Path, max_lines: int) -> str:
    lines: list[str] = []
    for p in sorted(root.iterdir()):
        if p.name.startswith("."):
            continue
        if p.is_dir():
            lines.append(f"{p.name}/")
            for child in sorted(p.iterdir())[:10]:
                if not child.name.startswith("."):
                    suffix = "/" if child.is_dir() else ""
                    lines.append(f"  {child.name}{suffix}")
        else:
            lines.append(p.name)
        if len(lines) >= max_lines:
            lines.append("  …")
            break
    return "\n".join(lines)


def _read_truncated(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[:_MAX_FILE_CHARS] + ("…" if len(text) > _MAX_FILE_CHARS else "")
    except OSError:
        return "(unreadable)"


def _run_git(root: Path, args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _detect_api_key() -> tuple[str, str] | None:
    """Return (provider, api_key) for the first available LLM key."""
    if key := os.environ.get("ANTHROPIC_API_KEY"):
        return ("anthropic", key)
    if key := os.environ.get("OPENAI_API_KEY"):
        return ("openai", key)
    return None


def _call_llm(api_key_info: tuple[str, str], repo_summary: str) -> str:
    provider, key = api_key_info
    prompt = _build_prompt(repo_summary)
    if provider == "anthropic":
        return _call_anthropic(key, prompt)
    return _call_openai(key, prompt)


def _build_prompt(repo_summary: str) -> str:
    return (
        "You are helping configure an automated Git merge system for a repository.\n"
        "Based on the repository information below, write a CLAUDE.md file that "
        "describes the project's merge conventions.\n\n"
        "The CLAUDE.md should cover:\n"
        "1. What kind of project this is and its purpose\n"
        "2. Which files or directories are customized/forked and should preserve "
        "local changes during upstream merges\n"
        "3. Which files should always take upstream changes directly\n"
        "4. Any special merge rules (e.g. based on author fields in manifest files, "
        "config ownership, etc.)\n"
        "5. Files that are security-sensitive or require human review\n\n"
        "Be concise and use clear natural language. Do not include code blocks or "
        "headings with '# Build Commands' — focus only on merge conventions.\n\n"
        "Repository information:\n\n"
        f"{repo_summary}\n\n"
        "Write the CLAUDE.md content now:"
    )


def _call_anthropic(api_key: str, prompt: str) -> str:
    try:
        import anthropic
    except ImportError:
        console.print(
            "[yellow]anthropic package not installed. Generating template.[/yellow]"
        )
        return _generate_template_from_prompt(prompt)

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    block = message.content[0]
    if not isinstance(block, anthropic.types.TextBlock):
        return _generate_template_from_prompt(prompt)
    return block.text


def _call_openai(api_key: str, prompt: str) -> str:
    try:
        import openai
    except ImportError:
        console.print(
            "[yellow]openai package not installed. Generating template.[/yellow]"
        )
        return _generate_template_from_prompt(prompt)

    client = openai.OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content or ""


def _generate_template(root: Path, repo_summary: str) -> str:
    name = root.name
    return (
        f"# {name} — Merge Conventions\n\n"
        "## Project Overview\n\n"
        "<!-- Describe the project and its purpose -->\n\n"
        "## Merge Strategy\n\n"
        "<!-- Describe which files are customized locally and should preserve "
        "local changes, and which files should always take upstream changes. -->\n\n"
        "## Special Rules\n\n"
        "<!-- List any metadata-based rules, e.g.:\n"
        "  - Files matching custom/**: preserve local customizations\n"
        "  - Files matching vendor/**: always take upstream -->\n\n"
        "## Security-Sensitive Files\n\n"
        "<!-- List files that require human review during merges -->\n"
    )


def _generate_template_from_prompt(prompt: str) -> str:
    return (
        "# Merge Conventions\n\n"
        "<!-- Auto-generation failed. Please fill in your project's merge "
        "conventions manually. -->\n\n"
        "## Project Overview\n\n## Merge Strategy\n\n## Special Rules\n\n"
        "## Security-Sensitive Files\n"
    )
