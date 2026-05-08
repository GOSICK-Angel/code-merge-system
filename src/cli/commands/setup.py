"""Interactive setup wizard for the one-stop `merge <branch>` flow.

Entry point: detect_or_setup(target_branch, repo_path, reconfigure) -> MergeConfig

First run:  guides the user through API keys + thresholds, writes
            <repo>/.merge/config.yaml and <repo>/.merge/.env.
Repeat run: loads existing config, shows a one-line summary, and asks
            for confirmation (or 'c' to reconfigure).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from src.cli.env import read_env_file, write_env_file
from src.cli.paths import (
    ensure_merge_dir,
    get_config_path,
    get_forks_profile_path,
    get_global_env_path,
    get_project_merge_dir,
)
from src.models.config import MergeConfig

console = Console()

_ENV_KEYS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "GITHUB_TOKEN",
)


def detect_or_setup(
    target_branch: str,
    repo_path: str = ".",
    reconfigure: bool = False,
    non_interactive: bool = False,
) -> MergeConfig:
    """Load existing config or run interactive wizard.

    Returns a validated MergeConfig with upstream_ref = target_branch.
    On first run, also migrates any existing MERGE_RECORD/ directory.

    When ``non_interactive`` is true (set by ``merge --ci``), the function
    refuses to enter the interactive wizard and the per-run "Press Enter"
    prompt — config must already exist on disk.
    """
    config_path = get_config_path(repo_path)

    if not reconfigure and config_path.exists():
        return _repeat_run_flow(
            target_branch, repo_path, config_path, non_interactive=non_interactive
        )

    if non_interactive:
        raise RuntimeError(
            f"--ci requires existing config at {config_path}; "
            "run `merge <branch>` once interactively, or pass --reconfigure "
            "with valid pre-set env vars."
        )

    migrate_merge_record(repo_path)
    return _interactive_setup(target_branch, repo_path)


def _auto_detect_fork_ref(repo_path: str) -> str:
    """Return the current git branch name, falling back to 'origin/main'."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        branch = result.stdout.strip()
        if branch and branch != "HEAD":
            return branch
    except Exception:
        pass
    return "origin/main"


def _resolve_api_keys(repo_path: str) -> dict[str, str]:
    """Merge API keys from all sources (lowest to highest priority):

    1. ~/.config/code-merge-system/.env   (global fallback)
    2. <repo>/.merge/.env                 (project-level)
    3. Shell environment variables        (highest priority)
    """
    resolved: dict[str, str] = {}

    global_env = get_global_env_path()
    if global_env.exists():
        resolved.update(read_env_file(global_env))

    project_env = get_project_merge_dir(repo_path) / ".env"
    if project_env.exists():
        resolved.update(read_env_file(project_env))

    for key in _ENV_KEYS:
        val = os.environ.get(key)
        if val:
            resolved[key] = val

    return resolved


def _repeat_run_flow(
    target_branch: str,
    repo_path: str,
    config_path: Path,
    non_interactive: bool = False,
) -> MergeConfig:
    """Show config summary and confirm before starting."""
    try:
        raw: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        raw["upstream_ref"] = target_branch
        config = MergeConfig.model_validate(raw)
    except Exception as e:
        if non_interactive:
            raise RuntimeError(
                f"--ci cannot recover from config load error: {e}"
            ) from e
        console.print(f"[yellow]Config load error: {e}. Re-running setup.[/yellow]")
        return _interactive_setup(target_branch, repo_path)

    console.print(
        Panel(
            f"[bold]Code Merge System[/bold]\n\n"
            f"  Target:  [cyan]{target_branch}[/cyan] → [cyan]{config.fork_ref}[/cyan]\n"
            f"  Repo:    {Path(repo_path).resolve()}\n"
            f"  Config:  {config_path}",
            title="merge",
            border_style="cyan",
        )
    )
    if non_interactive:
        return config

    console.print("\nPress Enter to start, or [bold]c[/bold] to reconfigure...")
    choice = Prompt.ask("", default="", show_default=False)
    if choice.lower() == "c":
        return _interactive_setup(target_branch, repo_path)

    return config


def _interactive_setup(target_branch: str, repo_path: str) -> MergeConfig:
    """Full interactive first-time wizard."""
    resolved_keys = _resolve_api_keys(repo_path)
    fork_ref = _auto_detect_fork_ref(repo_path)

    console.print(
        Panel(
            f"[bold cyan]Code Merge System[/bold cyan]\n\n"
            f"  Target: [cyan]{target_branch}[/cyan] → [cyan]{fork_ref}[/cyan]\n"
            f"  Repo:   {Path(repo_path).resolve()}",
            title="[1/3] Configuration",
            border_style="cyan",
        )
    )

    project_context = Prompt.ask(
        "\nProject description (helps AI understand context)",
        default="",
    )

    console.print("\n[bold yellow]API Keys[/bold yellow]")
    collected_keys: dict[str, str] = {}

    for name, required in [
        ("ANTHROPIC_API_KEY", True),
        ("OPENAI_API_KEY", True),
        ("GITHUB_TOKEN", False),
    ]:
        val = _prompt_api_key(name, resolved_keys.get(name, ""), required=required)
        if val:
            collected_keys[name] = val

    console.print("\n[bold yellow]Thresholds[/bold yellow]")
    use_defaults = Confirm.ask(
        "Use defaults? (auto_merge=0.85, risk_low=0.3, risk_high=0.6)",
        default=True,
    )
    if use_defaults:
        auto_merge, risk_low, risk_high = 0.85, 0.30, 0.60
    else:
        auto_merge = _prompt_float("auto_merge_confidence", 0.85)
        risk_low = _prompt_float("risk_score_low", 0.30)
        risk_high = _prompt_float("risk_score_high", 0.60)

    ensure_merge_dir(repo_path)

    if collected_keys:
        env_path = get_project_merge_dir(repo_path) / ".env"
        write_env_file(env_path, collected_keys)
        console.print(f"\n  [green]API keys saved to:[/green] {env_path}")
        for k, v in collected_keys.items():
            os.environ.setdefault(k, v)

    config_data: dict[str, Any] = {
        "upstream_ref": target_branch,
        "fork_ref": fork_ref,
        "working_branch": "merge/auto-{timestamp}",
        "enable_working_branch": False,
        "repo_path": repo_path,
        "project_context": project_context,
        "max_files_per_run": 500,
        "max_plan_revision_rounds": 2,
        "agents": {
            "planner": {
                "provider": "anthropic",
                "model": "claude-opus-4-6",
                "api_key_env": "ANTHROPIC_API_KEY",
            },
            "planner_judge": {
                "provider": "openai",
                "model": "gpt-4o",
                "api_key_env": "OPENAI_API_KEY",
            },
            "conflict_analyst": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "api_key_env": "ANTHROPIC_API_KEY",
            },
            "executor": {
                "provider": "openai",
                "model": "gpt-4o",
                "temperature": 0.1,
                "api_key_env": "OPENAI_API_KEY",
            },
            "judge": {
                "provider": "anthropic",
                "model": "claude-opus-4-6",
                "temperature": 0.1,
                "api_key_env": "ANTHROPIC_API_KEY",
            },
            "human_interface": {
                "provider": "anthropic",
                "model": "claude-haiku-4-5-20251001",
                "api_key_env": "ANTHROPIC_API_KEY",
            },
        },
        "thresholds": {
            "auto_merge_confidence": auto_merge,
            "human_escalation": 0.60,
            "risk_score_low": risk_low,
            "risk_score_high": risk_high,
        },
        "output": {
            "directory": "./outputs",
            "formats": ["json", "markdown"],
        },
    }

    if "GITHUB_TOKEN" in collected_keys:
        config_data["github"] = {"enabled": True, "token_env": "GITHUB_TOKEN"}

    config_path = get_config_path(repo_path)
    config_path.write_text(
        yaml.dump(config_data, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    console.print(f"  [green]Config saved to:[/green] {config_path}")

    merge_config = MergeConfig.model_validate(config_data)

    _offer_forks_profile_draft(target_branch, fork_ref, repo_path)

    console.print(
        Panel(
            f"  API keys ........ [green]OK[/green]\n"
            f"  Repository ...... {Path(repo_path).resolve()}",
            title="[2/3] Validation",
            border_style="green",
        )
    )
    console.print(
        Panel(
            f"  [cyan]{target_branch}[/cyan] → [cyan]{fork_ref}[/cyan]\n\n"
            "  Press Enter to start, or Ctrl+C to cancel...",
            title="[3/3] Ready to merge",
            border_style="green",
        )
    )
    Prompt.ask("", default="", show_default=False)

    return merge_config


def _prompt_api_key(name: str, existing: str, required: bool) -> str:
    source_hint = " (from env)" if os.environ.get(name) else ""
    masked = _mask_key(existing) if existing else ""
    hint = f" {masked}{source_hint}" if masked else ""
    label = f"  {name}:{hint}"
    if not required:
        label += " [optional, Enter to skip]"

    value = Prompt.ask(label, default="", show_default=False)
    if not value and existing:
        return existing
    if not value and required and not existing:
        console.print(
            f"    [yellow]Warning: {name} not set — some agents will fail.[/yellow]"
        )
    return value


def _mask_key(key: str) -> str:
    if len(key) <= 8:
        return "****"
    return key[:4] + "****" + key[-4:]


def _prompt_float(label: str, default: float) -> float:
    while True:
        raw = Prompt.ask(f"  {label}", default=str(default))
        try:
            val = float(raw)
            if 0.0 <= val <= 1.0:
                return val
            console.print("    [red]Must be between 0.0 and 1.0[/red]")
        except ValueError:
            console.print("    [red]Please enter a valid number[/red]")


FORKS_PROFILE_INIT_THRESHOLD = 30
"""Minimum count of fork-deleted files required before offering an init.

Calibrated against historical merge reports:
  - insforge v2.1.0: 30+ fork-deleted files (6 removed_domains in yaml) →
    a forks-profile.yaml materially reduced judge false positives.
  - dify-plugin-daemon 0.6.0: 251 D_EXTRA but mostly path reorganization
    (rename-class, not domain removal) — yaml still helps but not critical.

Below ~30 the auto overlay (PR-A) covers the routing on its own and a
yaml is just maintenance burden, so the wizard stays silent.
"""


def _offer_forks_profile_draft(
    target_branch: str, fork_ref: str, repo_path: str
) -> None:
    """Offer to draft `.merge/forks-profile.yaml` when the fork looks divergent.

    Called once during the first-time wizard, after config has been
    written but before "press Enter to start". The trigger is the
    cheapest possible signal — number of files the fork deleted relative
    to the upstream merge-base — so it never blocks setup on a slow
    full-divergence scan. When the user accepts, we run the full
    drafter and open the result in ``$EDITOR`` so they can review the
    TODO-marked entries before they ever flow into a real run.

    All git failures and IO errors silently skip; setup must never
    abort on this best-effort prompt.
    """
    profile_path = get_forks_profile_path(repo_path)
    if profile_path.exists():
        return

    try:
        merge_base = subprocess.run(
            ["git", "merge-base", target_branch, fork_ref],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if not merge_base:
            return
        deleted = subprocess.run(
            [
                "git",
                "diff",
                "--diff-filter=D",
                "--name-only",
                f"{merge_base}..{fork_ref}",
            ],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:
        return

    deleted_count = sum(1 for line in deleted.splitlines() if line.strip())
    if deleted_count < FORKS_PROFILE_INIT_THRESHOLD:
        return

    console.print(
        f"\n[bold yellow]Fork divergence detected:[/bold yellow] "
        f"{deleted_count} files deleted vs upstream merge-base."
    )
    console.print(
        "  Generating a forks-profile.yaml draft helps the merge system "
        "skip false-positive 'missing file' alerts and keep your "
        "deliberate removals/rewrites out of the AI flow."
    )
    if not Confirm.ask("  Draft forks-profile.yaml now?", default=True):
        return

    try:
        _draft_and_open_editor(profile_path, target_branch, fork_ref, repo_path)
    except Exception as e:
        console.print(
            f"  [yellow]forks-profile draft failed (run "
            f"`merge forks-profile init` later): {e}[/yellow]"
        )


def _draft_and_open_editor(
    profile_path: Path, target_branch: str, fork_ref: str, repo_path: str
) -> None:
    """Run the drafter, write the yaml, and open ``$EDITOR`` for review."""
    import datetime as _dt

    import click

    from src.tools.forks_profile_drafter import (
        draft_profile,
        render_profile_yaml,
    )
    from src.tools.git_tool import GitTool

    git_tool = GitTool(repo_path)
    merge_base = git_tool.get_merge_base(target_branch, fork_ref)
    drafted = draft_profile(
        git_tool,
        upstream_ref=target_branch,
        fork_ref=fork_ref,
        merge_base=merge_base,
    )
    text = render_profile_yaml(drafted, today=_dt.date.today().isoformat())

    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(text, encoding="utf-8")
    console.print(f"  [green]Draft written to:[/green] {profile_path}")
    stats = ", ".join(f"{k}={v}" for k, v in drafted.stats.items())
    console.print(f"  Stats: {stats}. Review every TODO before committing.")

    try:
        click.edit(filename=str(profile_path))
    except Exception:
        console.print(
            f"  [yellow]Could not open editor; review the draft manually "
            f"at {profile_path}.[/yellow]"
        )


def migrate_merge_record(repo_path: str = ".") -> None:
    """Move MERGE_RECORD/*.md into .merge/plans/ (one-time migration).

    Safe to call repeatedly — skips files that already exist in the
    destination and leaves the source directory untouched afterwards.
    """
    import shutil

    src_dir = Path(repo_path).resolve() / "MERGE_RECORD"
    if not src_dir.is_dir():
        return

    plans_dir = get_project_merge_dir(repo_path) / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    moved: list[str] = []
    for md_file in src_dir.glob("*.md"):
        dest = plans_dir / md_file.name
        if dest.exists():
            continue
        shutil.move(str(md_file), str(dest))
        moved.append(md_file.name)

    if moved:
        console.print(
            f"  [green]Migrated {len(moved)} plan file(s)[/green] "
            f"from MERGE_RECORD/ → .merge/plans/"
        )
