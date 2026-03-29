import asyncio
import os
import sys
import yaml
import click
from pathlib import Path
from rich.console import Console
from src.models.config import MergeConfig
from src.models.state import MergeState, SystemStatus
from src.core.checkpoint import Checkpoint
from src.core.orchestrator import Orchestrator
from src.tools.report_writer import write_markdown_report, write_json_report


console = Console()


@click.group()
def cli():
    pass


@cli.command("run")
@click.option("--config", "-c", required=True, type=click.Path(exists=True))
@click.option("--dry-run", is_flag=True, help="Analyze only, do not merge")
def run_command(config: str, dry_run: bool):
    """Execute complete merge workflow"""
    from src.cli.commands.run import run_command_impl
    run_command_impl(config, dry_run)


@cli.command("resume")
@click.option("--run-id", required=False, default=None)
@click.option("--checkpoint", required=False, type=click.Path(exists=True), default=None)
def resume_command(run_id: str | None, checkpoint: str | None):
    """Resume execution from a checkpoint"""
    from src.cli.commands.resume import resume_command_impl
    resume_command_impl(run_id, checkpoint)


@cli.command("report")
@click.option("--run-id", required=True)
@click.option("--output", "-o", default="./outputs")
def report_command(run_id: str, output: str):
    """Generate reports only (without executing merge)"""
    checkpoint_manager = Checkpoint(output)
    latest = checkpoint_manager.get_latest(run_id)
    if latest is None:
        console.print(f"[red]No checkpoint found for run_id: {run_id}[/red]")
        sys.exit(1)

    state = checkpoint_manager.load(latest)

    try:
        json_path = write_json_report(state, output)
        console.print(f"JSON report: {json_path}")
    except Exception as e:
        console.print(f"[red]JSON report failed: {e}[/red]")

    try:
        md_path = write_markdown_report(state, output)
        console.print(f"Markdown report: {md_path}")
    except Exception as e:
        console.print(f"[red]Markdown report failed: {e}[/red]")


@cli.command("validate")
@click.option("--config", "-c", required=True, type=click.Path(exists=True))
def validate_command(config: str):
    """Validate config file and check required environment variables"""
    config_file = Path(config)
    try:
        raw_config = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        merge_config = MergeConfig.model_validate(raw_config)
    except Exception as e:
        console.print(f"[red]Config validation failed: {e}[/red]")
        sys.exit(1)

    errors = validate_config_and_env(merge_config)

    if errors:
        console.print("[red]Validation errors:[/red]")
        for err in errors:
            console.print(f"  - {err}")
        sys.exit(1)
    else:
        console.print("[green]Config is valid. All required environment variables are set.[/green]")


def validate_config_and_env(config: MergeConfig) -> list[str]:
    errors: list[str] = []

    for agent_name, agent_config in config.agents.model_dump().items():
        env_var = agent_config.get("api_key_env", "")
        if env_var and not os.environ.get(env_var):
            errors.append(f"Agent '{agent_name}' requires env var '{env_var}' (not set)")

    try:
        from src.tools.git_tool import GitTool
        gt = GitTool(config.repo_path)
    except ValueError as e:
        errors.append(f"repo_path '{config.repo_path}' is not a valid git repository: {e}")
        return errors

    for ref in (config.upstream_ref, config.fork_ref):
        try:
            gt.repo.git.rev_parse(ref)
        except Exception:
            errors.append(f"Git ref '{ref}' does not exist in repository")

    return errors


if __name__ == "__main__":
    cli()
