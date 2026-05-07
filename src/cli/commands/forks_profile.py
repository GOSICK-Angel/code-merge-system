"""Standalone CLI for `.merge/forks-profile.yaml` (§9.7 P1, §9.10 P3).

Subcommands:

  merge forks-profile validate [-p PATH]
      Load and schema-validate the YAML. Exits 0 on success, 1 on
      schema / YAML errors, 2 when the file does not exist.

  merge forks-profile schema [-o PATH]
      Emit the JSON Schema (Draft 2020-12) for the ForksProfile model so
      IDEs, pre-commit hooks, and CI lints can validate the YAML without
      pulling in code-merge-system as a dependency.

  merge forks-profile init [OPTIONS]
      Auto-draft a forks-profile.yaml from git history (FORK_ONLY,
      FORK_DELETED, FORK_MODIFIED divergence + migration globs).
      Output is always TODO-marked and policies default to
      escalate_human — review before committing.

  merge forks-profile diff [OPTIONS]
      Compare an existing yaml against a fresh heuristic draft and
      surface the three drift categories (over-declared, missing,
      classification mismatch).

Designed for fork maintainers' PR pipelines — no API keys, no
MergeState involved. ``init`` / ``diff`` need a working git tree; the
other subcommands work on the yaml alone.
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path

import click
from rich.console import Console

from src.cli.paths import get_forks_profile_path
from src.models.forks_profile import ForksProfile
from src.tools.forks_profile_differ import (
    diff_profile_vs_heuristic,
    format_profile_diff,
)
from src.tools.forks_profile_drafter import (
    DEFAULT_MIGRATION_GLOBS,
    draft_profile,
    render_profile_yaml,
)
from src.tools.forks_profile_loader import (
    ForksProfileError,
    load_forks_profile,
    summarize_for_log,
)
from src.tools.git_tool import GitTool


_console = Console()


@click.group("forks-profile")
def forks_profile() -> None:
    """Validate and export the .merge/forks-profile.yaml schema."""


@forks_profile.command("validate")
@click.option(
    "--path",
    "-p",
    type=click.Path(),
    default=None,
    help=(
        "Profile YAML to validate. Defaults to "
        "<repo>/.merge/forks-profile.yaml in the current directory."
    ),
)
@click.option(
    "--repo",
    type=click.Path(file_okay=False, exists=True),
    default=".",
    help="Repository root used to resolve the default profile path.",
)
def validate_command(path: str | None, repo: str) -> None:
    """Validate `.merge/forks-profile.yaml` against the ForksProfile schema."""
    profile_path = Path(path) if path else get_forks_profile_path(repo)

    if not profile_path.exists():
        _console.print(
            f"[yellow]No forks-profile.yaml found at {profile_path}.[/yellow]\n"
            "[yellow]This is fine — the file is optional. "
            "Create one only if your fork needs to declare removed_domains, "
            "rewritten_modules, or migration_policy.[/yellow]"
        )
        sys.exit(2)

    # ``load_forks_profile`` resolves the path via ``get_forks_profile_path``,
    # so feed it the parent of ``.merge`` to honour a user-supplied --path.
    try:
        if path:
            profile = _load_explicit(profile_path)
        else:
            profile = load_forks_profile(repo)
    except ForksProfileError as e:
        _console.print(f"[red]Validation failed:[/red] {e}")
        sys.exit(1)

    if profile is None:
        _console.print(
            f"[yellow]{profile_path} is empty — nothing to validate.[/yellow]"
        )
        sys.exit(0)

    _console.print(f"[green]✓ {profile_path} is a valid forks-profile.[/green]")
    _console.print(f"  {summarize_for_log(profile)}")


def _load_explicit(profile_path: Path) -> ForksProfile | None:
    """Load a forks-profile from an explicit (non-default) path.

    Mirrors ``load_forks_profile`` failure modes but bypasses the
    ``<repo>/.merge/forks-profile.yaml`` convention so the CLI can
    point at any YAML the user names with ``--path``.
    """
    import yaml
    from pydantic import ValidationError

    raw = profile_path.read_text(encoding="utf-8")
    if not raw.strip():
        return None
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise ForksProfileError(
            f"forks-profile YAML parse failed at {profile_path}: {e}"
        ) from e
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ForksProfileError(
            f"forks-profile root must be a mapping, got {type(data).__name__} "
            f"at {profile_path}"
        )
    try:
        return ForksProfile.model_validate(data)
    except ValidationError as e:
        raise ForksProfileError(
            f"forks-profile schema validation failed at {profile_path}: {e}"
        ) from e


@forks_profile.command("schema")
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=None,
    help="Write schema to this file instead of stdout.",
)
@click.option(
    "--indent",
    type=int,
    default=2,
    show_default=True,
    help="JSON pretty-print indent.",
)
def schema_command(output: str | None, indent: int) -> None:
    """Emit the JSON Schema for forks-profile.yaml."""
    schema = ForksProfile.model_json_schema()
    schema["title"] = "ForksProfile"
    schema["$comment"] = (
        "Generated from src/models/forks_profile.py — do not edit by hand."
    )
    text = json.dumps(schema, indent=indent, sort_keys=False, ensure_ascii=False)

    if output:
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text + "\n", encoding="utf-8")
        _console.print(f"[green]Wrote schema to {out_path}.[/green]")
    else:
        click.echo(text)


def _resolve_merge_base(
    git_tool: GitTool, upstream: str, fork: str, explicit: str | None
) -> str:
    if explicit:
        return explicit
    return git_tool.get_merge_base(upstream, fork)


@forks_profile.command("init")
@click.option(
    "--upstream",
    default="upstream/main",
    show_default=True,
    help="Upstream ref to compare against.",
)
@click.option(
    "--fork",
    default="HEAD",
    show_default=True,
    help="Fork ref to draft from.",
)
@click.option(
    "--merge-base",
    default=None,
    help="Merge-base SHA (default: git merge-base of upstream and fork).",
)
@click.option(
    "--repo",
    type=click.Path(file_okay=False, exists=True),
    default=".",
    show_default=True,
    help="Repository root.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=None,
    help=(
        "Write draft to this file. If the file already exists the command "
        "exits 2 without overwriting; remove it manually to re-draft. "
        "Without this flag the draft is printed to stdout."
    ),
)
@click.option(
    "--rewrite-retention-threshold",
    type=float,
    default=0.30,
    show_default=True,
    help="A FORK_MODIFIED file is candidate for rewritten_modules when its "
    "lines_retained / lines_at_base falls below this fraction.",
)
@click.option(
    "--rewrite-min-lines",
    type=int,
    default=50,
    show_default=True,
    help="Minimum lines_changed for a file to be a rewrite candidate.",
)
@click.option(
    "--rewrite-min-fork-commits",
    type=int,
    default=5,
    show_default=True,
    help="Alternative trigger: fork-only commits touching the file.",
)
@click.option(
    "--migration-glob",
    "migration_globs",
    multiple=True,
    help="Path glob(s) identifying DB migration files (repeatable). "
    "Defaults cover sql/py/rb migrations under common conventions.",
)
@click.option(
    "--cluster-min-files",
    type=int,
    default=None,
    help="Override adaptive clustering threshold (default: max(3, n/20)).",
)
def init_command(
    upstream: str,
    fork: str,
    merge_base: str | None,
    repo: str,
    output: str | None,
    rewrite_retention_threshold: float,
    rewrite_min_lines: int,
    rewrite_min_fork_commits: int,
    migration_globs: tuple[str, ...],
    cluster_min_files: int | None,
) -> None:
    """Auto-draft a forks-profile.yaml from observable git divergence."""
    if output:
        out_path = Path(output)
        if out_path.exists():
            _console.print(
                f"[red]Refusing to overwrite existing file:[/red] {out_path}\n"
                "Remove or rename it before re-running `init` "
                "(no `--force` by design — see doc/forks-profile-init.md §4.1)."
            )
            sys.exit(2)

    try:
        git_tool = GitTool(repo)
    except ValueError as e:
        _console.print(f"[red]Not a git repository:[/red] {e}")
        sys.exit(1)

    try:
        base = _resolve_merge_base(git_tool, upstream, fork, merge_base)
    except Exception as e:  # noqa: BLE001 — git failures are user-facing
        _console.print(f"[red]Failed to resolve merge-base:[/red] {e}")
        sys.exit(1)

    try:
        drafted = draft_profile(
            git_tool,
            upstream_ref=upstream,
            fork_ref=fork,
            merge_base=base,
            rewrite_retention_threshold=rewrite_retention_threshold,
            rewrite_min_lines=rewrite_min_lines,
            rewrite_min_fork_commits=rewrite_min_fork_commits,
            migration_globs=list(migration_globs) or list(DEFAULT_MIGRATION_GLOBS),
            cluster_min_files=cluster_min_files,
        )
    except Exception as e:  # noqa: BLE001
        _console.print(f"[red]Drafting failed:[/red] {e}")
        sys.exit(1)

    today = _dt.date.today().isoformat()
    text = render_profile_yaml(drafted, today=today)

    if output:
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        _console.print(f"[green]Wrote draft to {out_path}.[/green]")
        _console.print(
            "  "
            + ", ".join(f"{k}={v}" for k, v in drafted.stats.items())
            + ". Review every TODO before committing."
        )
    else:
        click.echo(text, nl=False)


@forks_profile.command("diff")
@click.option(
    "--upstream",
    default="upstream/main",
    show_default=True,
    help="Upstream ref to compare against.",
)
@click.option(
    "--fork",
    default="HEAD",
    show_default=True,
    help="Fork ref to draft from.",
)
@click.option(
    "--merge-base",
    default=None,
    help="Merge-base SHA (default: git merge-base of upstream and fork).",
)
@click.option(
    "--repo",
    type=click.Path(file_okay=False, exists=True),
    default=".",
    show_default=True,
    help="Repository root.",
)
@click.option(
    "--profile",
    "-p",
    type=click.Path(),
    default=None,
    help="Profile YAML to diff. Defaults to <repo>/.merge/forks-profile.yaml.",
)
@click.option(
    "--rewrite-retention-threshold",
    type=float,
    default=0.30,
    show_default=True,
)
@click.option(
    "--rewrite-min-lines",
    type=int,
    default=50,
    show_default=True,
)
@click.option(
    "--rewrite-min-fork-commits",
    type=int,
    default=5,
    show_default=True,
)
@click.option(
    "--migration-glob",
    "migration_globs",
    multiple=True,
)
@click.option(
    "--cluster-min-files",
    type=int,
    default=None,
)
@click.option(
    "--exit-non-zero-on-diff",
    is_flag=True,
    default=False,
    help="Exit 1 when drift is detected (CI gate). Without the flag, "
    "drift is reported but exit is always 0.",
)
def diff_command(
    upstream: str,
    fork: str,
    merge_base: str | None,
    repo: str,
    profile: str | None,
    rewrite_retention_threshold: float,
    rewrite_min_lines: int,
    rewrite_min_fork_commits: int,
    migration_globs: tuple[str, ...],
    cluster_min_files: int | None,
    exit_non_zero_on_diff: bool,
) -> None:
    """Diff a checked-in forks-profile.yaml against a fresh heuristic draft."""
    profile_path = Path(profile) if profile else get_forks_profile_path(repo)
    if not profile_path.exists():
        _console.print(
            f"[red]No forks-profile.yaml at {profile_path}.[/red] "
            "Run `merge forks-profile init -o <path>` first."
        )
        sys.exit(2)

    try:
        if profile:
            loaded = _load_explicit(profile_path)
        else:
            loaded = load_forks_profile(repo)
    except ForksProfileError as e:
        _console.print(f"[red]Profile load failed:[/red] {e}")
        sys.exit(1)

    try:
        git_tool = GitTool(repo)
    except ValueError as e:
        _console.print(f"[red]Not a git repository:[/red] {e}")
        sys.exit(1)

    try:
        base = _resolve_merge_base(git_tool, upstream, fork, merge_base)
        drafted = draft_profile(
            git_tool,
            upstream_ref=upstream,
            fork_ref=fork,
            merge_base=base,
            rewrite_retention_threshold=rewrite_retention_threshold,
            rewrite_min_lines=rewrite_min_lines,
            rewrite_min_fork_commits=rewrite_min_fork_commits,
            migration_globs=list(migration_globs) or list(DEFAULT_MIGRATION_GLOBS),
            cluster_min_files=cluster_min_files,
        )
    except Exception as e:  # noqa: BLE001
        _console.print(f"[red]Drafting failed:[/red] {e}")
        sys.exit(1)

    diff = diff_profile_vs_heuristic(loaded, drafted)
    click.echo(format_profile_diff(diff), nl=False)

    if exit_non_zero_on_diff and not diff.is_empty():
        sys.exit(1)


__all__ = ["forks_profile"]
