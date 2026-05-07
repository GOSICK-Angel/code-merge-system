"""Standalone CLI for `.merge/forks-profile.yaml` (§9.7 P1).

Two subcommands:

  merge forks-profile validate [-p PATH]
      Load and schema-validate the YAML. Exits 0 on success, 1 on
      schema / YAML errors, 2 when the file does not exist.

  merge forks-profile schema [-o PATH]
      Emit the JSON Schema (Draft 2020-12) for the ForksProfile model so
      IDEs, pre-commit hooks, and CI lints can validate the YAML without
      pulling in code-merge-system as a dependency.

Designed for fork maintainers' PR pipelines — no API keys, no git_tool,
no MergeState involved.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from rich.console import Console

from src.cli.paths import get_forks_profile_path
from src.models.forks_profile import ForksProfile
from src.tools.forks_profile_loader import (
    ForksProfileError,
    load_forks_profile,
    summarize_for_log,
)


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


__all__ = ["forks_profile"]
