import os
import sys
import yaml
import click
from pathlib import Path
from rich.console import Console

try:
    import readline  # noqa: F401
except ImportError:
    # Windows lacks stdlib readline; arrow-key editing degrades but CLI still runs.
    pass
from src.cli.commands.forks_profile import forks_profile as _forks_profile_group
from src.cli.env import load_env
from src.cli.paths import get_project_merge_dir
from src.models.config import MergeConfig


def _load_repo_env(repo_path: str) -> None:
    """Pull ``<repo>/.merge/.env`` into ``os.environ`` if present.

    ``cli()`` already ran ``load_env()`` once at startup, but that helper
    points at the code-merge-system install tree's own ``.env`` — not the
    target repository the user is merging. Forks that ship a project-
    scoped ``.merge/.env`` (custom OpenAI/Anthropic gateway URL,
    per-project API keys, etc.) need it loaded *before* any LLM client is
    constructed; otherwise clients fall back to the public default
    endpoints and confusing model-name errors surface.

    ``override=True`` is intentional: the project ``.merge/.env`` is the
    authoritative configuration for that repo, and must beat both the
    install-tree ``.env`` and the global ``~/.config/code-merge-system/.env``
    fallback (loaded earlier by ``load_env()``). Without override, a stale
    ``OPENAI_BASE_URL`` from those fallbacks silently routes LLM calls to
    the wrong gateway even when the project ``.env`` explicitly points
    elsewhere.
    """
    env_path = get_project_merge_dir(repo_path) / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover — python-dotenv is a hard dep
        return
    load_dotenv(env_path, override=True)


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
@click.option(
    "--no-tui",
    "no_tui",
    is_flag=True,
    hidden=True,
    help="(deprecated) alias of --no-web",
)
@click.option("--no-web", is_flag=True, help="Disable Web UI (plain-text output)")
@click.option(
    "--no-browser",
    is_flag=True,
    help="Skip opening browser, print URL only",
)
@click.option(
    "--web-port",
    default=5173,
    type=int,
    help="HTTP static port for Web UI",
)
@click.option("--dry-run", is_flag=True, help="Analyze only, do not merge")
@click.option(
    "--ws-port", default=8765, type=int, help="WebSocket port for the Web UI bridge"
)
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
@click.option(
    "--auto-decisions",
    default=None,
    type=click.Path(exists=True),
    help=(
        "V2 decisions YAML pre-populated with rounds for every AWAITING_HUMAN "
        "cycle (plan_review / conflict_marker / conflict_resolution / "
        "judge_review). Drives the run end-to-end without operator "
        "intervention; intended for CI."
    ),
)
def merge_command(
    target_branch: str,
    ci: bool,
    no_tui: bool,
    no_web: bool,
    no_browser: bool,
    web_port: int,
    dry_run: bool,
    ws_port: int,
    reconfigure: bool,
    workflow: str | None,
    auto_decisions: str | None,
) -> None:
    """Merge TARGET_BRANCH into the current branch (one-stop flow)."""
    import warnings

    if no_tui:
        warnings.warn(
            "`--no-tui` is deprecated; use `--no-web` instead. "
            "This alias will be removed in a future release.",
            DeprecationWarning,
            stacklevel=2,
        )
        click.echo(
            "[deprecation] --no-tui is deprecated; use --no-web instead.",
            err=True,
        )
        no_web = no_web or no_tui

    _load_repo_env(".")

    from src.cli.commands.setup import detect_or_setup

    config = detect_or_setup(
        target_branch,
        repo_path=".",
        reconfigure=reconfigure,
        non_interactive=ci,
    )

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

    if not ci and not no_web:
        from src.cli.commands.web import web_command_impl

        web_command_impl(
            config,
            ws_port=ws_port,
            web_port=web_port,
            dry_run=dry_run,
            open_browser=not no_browser,
        )
        return

    from src.cli.commands.run import run_command_impl

    run_command_impl(config, dry_run, ci=ci, auto_decisions=auto_decisions)


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
@click.option(
    "--reload-config",
    is_flag=True,
    default=False,
    help="Re-read .merge/config.yaml and overlay runtime-safe fields "
    "(timeouts, retry budgets, cache_strategy, commit_round_* caps) onto "
    "the checkpoint config. Plan-shaping fields (provider/model, "
    "thresholds, max_files_per_run, project_context, refs) are NOT "
    "overridden to keep resume consistent with the frozen plan.",
)
@click.option(
    "--tui",
    "tui",
    is_flag=True,
    default=False,
    hidden=True,
    help="(deprecated) alias of --web",
)
@click.option(
    "--web",
    is_flag=True,
    default=False,
    help="Resume inside the interactive Web UI (browser) instead of "
    "plain-text output. Initial frame reflects the checkpoint's "
    "current_phase / status.",
)
@click.option(
    "--no-browser",
    is_flag=True,
    help="With --web, skip opening browser and print URL only.",
)
@click.option(
    "--web-port",
    default=5173,
    type=int,
    help="HTTP static port for the Web UI (only used with --web).",
)
@click.option(
    "--ws-port",
    default=8765,
    type=int,
    help="WebSocket port for the Web UI bridge (only used with --web).",
)
def resume_command(
    run_id: str | None,
    checkpoint: str | None,
    decisions: str | None,
    reload_config: bool,
    tui: bool,
    web: bool,
    no_browser: bool,
    web_port: int,
    ws_port: int,
) -> None:
    """Resume execution from a checkpoint"""
    import warnings

    if tui:
        warnings.warn(
            "`--tui` is deprecated; use `--web` instead. "
            "This alias will be removed in a future release.",
            DeprecationWarning,
            stacklevel=2,
        )
        click.echo(
            "[deprecation] --tui is deprecated; use --web instead.",
            err=True,
        )
        web = web or tui

    _load_repo_env(".")
    from src.cli.commands.resume import resume_command_impl

    resume_command_impl(
        run_id,
        checkpoint,
        decisions,
        reload_config=reload_config,
        web=web,
        ws_port=ws_port,
        web_port=web_port,
        open_browser=not no_browser,
    )


@cli.command("init")
@click.option(
    "--repo-path",
    default=".",
    show_default=True,
    help="Path to the target repository",
)
def init_command(repo_path: str) -> None:
    """Generate a CLAUDE.md for the target repository to guide merge decisions."""
    from src.cli.commands.init_context import init_command_impl

    init_command_impl(repo_path)


@cli.command("plan-suggest")
@click.option(
    "--target",
    default="upstream/main",
    show_default=True,
    help="Upstream ref to enumerate baselines from.",
)
@click.option(
    "--repo-path",
    default=".",
    show_default=True,
    type=click.Path(exists=True, file_okay=False),
    help="Repository root.",
)
@click.option(
    "--patterns",
    default="*",
    show_default=True,
    help=(
        "Comma-separated substrings to count against changed file paths. "
        "Default '*' disables filtering and reports total file counts only. "
        "Pass fork-specific substrings (e.g. owner or vendor prefix) to "
        "weight baselines by fork-customised coverage."
    ),
)
@click.option(
    "--candidates",
    default="5,10,30,50",
    show_default=True,
    help="Comma-separated commit-window sizes to evaluate.",
)
def plan_suggest_command(
    target: str, repo_path: str, patterns: str, candidates: str
) -> None:
    """Suggest a baseline commit window for the next merge run.

    For each ``~N`` window relative to TARGET, prints commit count, total
    changed-file count, and how many of those files match the substring
    PATTERNS. Default ``*`` reports raw totals; pass project-specific
    substrings to weight baselines by fork-customised coverage so the
    chosen window exercises SEMANTIC_MERGE without blowing up the budget.
    """
    from src.tools.git_tool import GitTool

    try:
        gt = GitTool(repo_path)
    except Exception as e:
        console.print(f"[red]Cannot open repo at '{repo_path}': {e}[/red]")
        sys.exit(1)

    try:
        head_sha = gt.repo.git.rev_parse(target).strip()
    except Exception as e:
        console.print(f"[red]Ref '{target}' not found: {e}[/red]")
        sys.exit(1)

    needles = [s for s in (p.strip() for p in patterns.split(",")) if s]
    use_filter = needles != ["*"] and bool(needles)

    sizes: list[int] = []
    for token in candidates.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            sizes.append(int(token))
        except ValueError:
            console.print(f"[yellow]Skipping invalid candidate '{token}'[/yellow]")
    if not sizes:
        console.print("[red]No valid --candidates values[/red]")
        sys.exit(1)

    console.print(
        f"[bold]Baseline suggestions vs {target}[/bold] (pattern filter: {patterns})"
    )
    console.print(f"{'baseline':<22} {'commits':>8} {'files':>8} {'matches':>9}")
    for n in sorted(set(sizes)):
        baseline = f"{target}~{n}"
        try:
            base_sha = gt.repo.git.rev_parse(baseline).strip()
        except Exception:
            console.print(f"{baseline:<22} (resolve failed)")
            continue
        try:
            files_raw = gt.repo.git.diff("--name-only", f"{base_sha}..{head_sha}")
        except Exception as exc:
            console.print(f"{baseline:<22} (diff failed: {exc})")
            continue
        files = [line for line in files_raw.splitlines() if line]
        if use_filter:
            matches = sum(1 for f in files if any(needle in f for needle in needles))
        else:
            matches = len(files)
        console.print(f"{baseline:<22} {n:>8} {len(files):>8} {matches:>9}")


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

    repo_root = Path(config.repo_path).expanduser()
    if not repo_root.exists():
        errors.append(
            f"repo_path '{config.repo_path}' does not exist on disk "
            "(check that the path is correct relative to the current "
            "directory, or use an absolute path)"
        )
        return errors
    if not repo_root.is_dir():
        errors.append(f"repo_path '{config.repo_path}' is not a directory")
        return errors

    try:
        from src.tools.git_tool import GitTool

        gt = GitTool(str(repo_root))
    except Exception as e:
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


cli.add_command(_forks_profile_group)


if __name__ == "__main__":
    cli()
