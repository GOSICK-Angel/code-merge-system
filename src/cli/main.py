import os
import sys
import yaml
import click
from pathlib import Path
from rich.console import Console
from src.models.config import MergeConfig
from src.cli.env import load_env


console = Console()


class _DefaultGroup(click.Group):
    """Forwards unrecognised first arguments to the 'merge' subcommand.

    Lets users type `merge upstream/main` without the explicit 'merge'
    token while keeping all named subcommands (resume, validate, …) unchanged.
    """

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError:
            merge_cmd = self.commands.get("merge")
            if merge_cmd is not None:
                return "merge", merge_cmd, args
            raise


@click.group(cls=_DefaultGroup)
def cli() -> None:
    load_env()


@cli.command("merge")
@click.argument("target_branch")
@click.option(
    "--ci", is_flag=True, help="CI mode: no interaction, JSON summary to stdout"
)
@click.option("--no-tui", is_flag=True, help="Disable interactive TUI")
@click.option("--dry-run", is_flag=True, help="Analyze only, do not merge")
@click.option("--ws-port", default=8765, type=int, help="WebSocket port for TUI bridge")
@click.option("--reconfigure", "-r", is_flag=True, help="Force reconfiguration wizard")
@click.option(
    "--workflow",
    "-w",
    default=None,
    help=(
        "Named workflow preset from config/workflows.yaml "
        "(standard|careful|fast|analysis-only). Overrides legacy flags where they overlap."
    ),
)
def merge_command(
    target_branch: str,
    ci: bool,
    no_tui: bool,
    dry_run: bool,
    ws_port: int,
    reconfigure: bool,
    workflow: str | None,
) -> None:
    """Merge TARGET_BRANCH into the current branch (one-stop flow)."""
    from src.cli.commands.setup import detect_or_setup

    config = detect_or_setup(target_branch, repo_path=".", reconfigure=reconfigure)

    if workflow is not None:
        from src.core.workflow_loader import apply_workflow_by_name, load_workflows

        try:
            catalog = load_workflows()
            config = apply_workflow_by_name(config, workflow, catalog)
            wf_def = catalog.workflows[workflow]
            if wf_def.dry_run:
                dry_run = True
            console.print(
                f"[cyan]Workflow applied:[/cyan] [bold]{workflow}[/bold] "
                f"(review_mode={wf_def.review_mode}, dry_run={wf_def.dry_run})"
            )
        except (FileNotFoundError, KeyError, ValueError) as e:
            console.print(f"[red]Workflow error: {e}[/red]")
            sys.exit(2)

    if not ci and not no_tui:
        from src.cli.commands.tui import tui_command_impl

        tui_command_impl(config, ws_port, dry_run)
        return

    from src.cli.commands.run import run_command_impl

    run_command_impl(config, dry_run, ci=ci)


@cli.command("resume")
@click.option("--run-id", required=False, default=None)
@click.option(
    "--checkpoint", required=False, type=click.Path(exists=True), default=None
)
@click.option(
    "--decisions",
    default=None,
    type=click.Path(exists=True),
    help="YAML file with human decisions",
)
def resume_command(
    run_id: str | None, checkpoint: str | None, decisions: str | None
) -> None:
    """Resume execution from a checkpoint"""
    from src.cli.commands.resume import resume_command_impl

    resume_command_impl(run_id, checkpoint, decisions)


@cli.command("validate")
@click.option("--config", "-c", required=True, type=click.Path(exists=True))
def validate_command(config: str) -> None:
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
        console.print(
            "[green]Config is valid. All required environment variables are set.[/green]"
        )


def validate_config_and_env(config: MergeConfig) -> list[str]:
    errors: list[str] = []

    for agent_name, agent_config in config.agents.model_dump().items():
        env_var = agent_config.get("api_key_env", "")
        if env_var and not os.environ.get(env_var):
            errors.append(
                f"Agent '{agent_name}' requires env var '{env_var}' (not set)"
            )

    try:
        from src.tools.git_tool import GitTool

        gt = GitTool(config.repo_path)
    except ValueError as e:
        errors.append(
            f"repo_path '{config.repo_path}' is not a valid git repository: {e}"
        )
        return errors

    for ref in (config.upstream_ref, config.fork_ref):
        try:
            gt.repo.git.rev_parse(ref)
        except Exception:
            errors.append(f"Git ref '{ref}' does not exist in repository")

    return errors


if __name__ == "__main__":
    cli()
