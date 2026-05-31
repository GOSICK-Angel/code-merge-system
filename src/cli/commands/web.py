"""CLI command: merge — launch the Web UI for setup or an in-flight run.

``web_command_impl`` is the single entry the top-level ``merge`` command
uses. It detects whether ``.merge/config.yaml`` already exists and
either:

  - Boots the orchestrator immediately and renders the dashboard /
    review gates as today, or
  - Boots the bridge in ``setup`` mode (no MergeState yet), serves the
    browser the Setup view, waits for ``setup.submit``, then constructs
    a MergeState and transitions the bridge into run mode without
    closing any sockets.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
import webbrowser
from importlib.resources import files
from pathlib import Path

import yaml
from rich.console import Console

from src.cli.exit_codes import EXIT_UNKNOWN_ERROR
from src.cli.paths import get_config_path, get_project_merge_dir
from src.core.orchestrator import Orchestrator
from src.core.phases.base import ActivityEvent
from src.models.config import MergeConfig
from src.models.setup import SetupPayload
from src.models.state import MergeState
from src.web.static_server import StaticHTTPServer
from src.web.ws_bridge import MergeWSBridge

logger = logging.getLogger(__name__)
console = Console()


def _resolve_web_dist() -> Path:
    """Locate the ``web/dist`` directory.

    Installed wheels carry the bundle under ``src/web/dist`` via package_data;
    in source checkouts the bundle lives at the repo root in ``web/dist``.
    """
    try:
        packaged = Path(str(files("src.web") / "dist"))
        if (packaged / "index.html").exists():
            return packaged
    except (ModuleNotFoundError, FileNotFoundError, TypeError):
        pass
    return Path(__file__).resolve().parents[3] / "web" / "dist"


def web_command_impl(
    repo_path: str = ".",
    ws_port: int = 8765,
    web_port: int = 5173,
    open_browser: bool = True,
) -> None:
    """Single entry for ``merge`` (non-``--ci``).

    Reads ``<repo_path>/.merge/config.yaml`` and either runs the
    orchestrator immediately or first walks the user through the
    browser-side Setup form to create one. The ``--ci`` path bypasses
    this entirely and lives in ``src/cli/main.py``.
    """
    asyncio.run(
        _serve(
            repo_path=repo_path,
            ws_port=ws_port,
            web_port=web_port,
            open_browser=open_browser,
        )
    )


def web_resume_impl(
    state: MergeState,
    ws_port: int,
    web_port: int,
    open_browser: bool = True,
) -> None:
    """Resume from an already-loaded checkpoint state — no setup wizard."""
    asyncio.run(
        _serve_with_state(
            state=state,
            config=state.config,
            ws_port=ws_port,
            web_port=web_port,
            open_browser=open_browser,
        )
    )


async def _serve(
    repo_path: str,
    ws_port: int,
    web_port: int,
    open_browser: bool,
) -> None:
    web_dist = _resolve_web_dist()
    if not (web_dist / "index.html").exists():
        console.print(
            "[red]Web UI assets not found.[/red]\n"
            f"Expected: {web_dist}\n"
            "If you installed from source (`pip install -e .`), build the "
            "frontend first:\n"
            "  [bold]cd web && npm ci && npm run build[/bold]"
        )
        sys.exit(EXIT_UNKNOWN_ERROR)

    config_path = get_config_path(repo_path)
    if config_path.exists():
        raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        merge_config = MergeConfig.model_validate(raw_config)
        state = MergeState(config=merge_config, dry_run=False)
        await _serve_with_state(
            state=state,
            config=merge_config,
            ws_port=ws_port,
            web_port=web_port,
            open_browser=open_browser,
        )
        return

    # First-run setup path — open the browser to a setup-mode bridge,
    # wait for the user to submit the form, then keep the same sockets
    # open while we promote the bridge into run mode and start the
    # orchestrator. Reusing sockets is critical: tearing the WS down
    # between modes would drop the just-rendered "Saving config…"
    # screen and force a manual refresh.
    bridge = MergeWSBridge(state=None, mode="setup", repo_path=repo_path)
    await bridge.start("localhost", ws_port)

    runs_root = get_project_merge_dir(repo_path) / "runs"
    static_server = StaticHTTPServer(web_dist, runs_root=runs_root)
    await static_server.start("localhost", web_port)

    url = f"http://localhost:{web_port}/?ws={ws_port}"
    _open_browser_or_print(url, open_browser)
    console.print(f"[bold green]Setup wizard:[/bold green] {url}")

    try:
        merge_config = await bridge.wait_for_setup()
        runtime = bridge.last_setup_payload
        _maybe_draft_forks_profile(runtime, merge_config, repo_path)
        dry_run = bool(runtime and runtime.dry_run)
        merge_config = _apply_workflow_from_setup(merge_config, runtime)

        state = MergeState(config=merge_config, dry_run=dry_run)
        await bridge.transition_to_run(state)

        await _run_orchestrator(
            bridge=bridge,
            state=state,
            config=merge_config,
        )
        console.print(
            f"[bold green]Run complete — report at "
            f"http://localhost:{web_port} (Ctrl+C to exit)[/bold green]"
        )
        await asyncio.sleep(float("inf"))
    except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
        console.print("[yellow]Interrupted by user.[/yellow]")
    finally:
        await _shutdown_servers(bridge, static_server)


async def _serve_with_state(
    state: MergeState,
    config: MergeConfig,
    ws_port: int,
    web_port: int,
    open_browser: bool,
) -> None:
    """Existing-config fast path: bridge starts in run mode immediately."""
    web_dist = _resolve_web_dist()
    bridge = MergeWSBridge(state)
    await bridge.start("localhost", ws_port)

    runs_root = get_project_merge_dir(config.repo_path) / "runs"
    static_server = StaticHTTPServer(web_dist, runs_root=runs_root)
    await static_server.start("localhost", web_port)

    url = f"http://localhost:{web_port}/?ws={ws_port}"
    _open_browser_or_print(url, open_browser)
    console.print(f"[bold green]Web UI:[/bold green] {url}")

    try:
        await _run_orchestrator(bridge=bridge, state=state, config=config)
        console.print(
            f"[bold green]Run complete — report at "
            f"http://localhost:{web_port} (Ctrl+C to exit)[/bold green]"
        )
        await asyncio.sleep(float("inf"))
    except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
        console.print("[yellow]Interrupted by user.[/yellow]")
    finally:
        await _shutdown_servers(bridge, static_server)


async def _shutdown_servers(
    bridge: MergeWSBridge,
    static_server: StaticHTTPServer,
) -> None:
    """Best-effort teardown: a failure tearing down one server must not
    block (or mask) teardown of the other. The signal handler in
    ``Checkpoint.register_signal_handler`` already restored SIG_DFL, so a
    second ^C here is a clean SIGINT, but we still suppress so a wedged
    socket doesn't leak an exception out of the asyncio runner's
    ``_cancel_all_tasks`` cleanup pass.
    """
    with contextlib.suppress(Exception):
        await bridge.stop()
    with contextlib.suppress(Exception):
        await static_server.stop()


async def _run_orchestrator(
    bridge: MergeWSBridge,
    state: MergeState,
    config: MergeConfig,
) -> None:
    """Main orchestrator loop — shared by the setup and existing-config paths.

    Identical to the pre-PR-3 ``_run_web`` body. Lifted into its own
    function so the setup-mode launcher (which has to assemble the
    state itself after submit) can reuse it without duplicating the
    AWAITING_HUMAN dispatch table.
    """
    orchestrator = Orchestrator(config)

    def _on_transition(_s: MergeState, _target: object, reason: str) -> None:
        bridge.notify_state_change(reason)

    orchestrator.state_machine.add_observer(_on_transition)

    def _on_activity(event: ActivityEvent) -> None:
        bridge.notify_agent_activity(event)
        bridge.notify_state_change(f"{event.agent}: {event.action}")

    orchestrator.set_activity_callback(_on_activity)

    connected = await bridge.wait_for_client(timeout=60.0)
    if not connected:
        console.print(
            "[yellow]No browser client connected within 60s; "
            "continuing in background.[/yellow]"
        )

    while True:
        state = await orchestrator.run(state)
        await bridge.broadcast_state_patch()

        if state.status.value != "awaiting_human":
            break

        if _bridge_cancelled(bridge):
            console.print("[yellow]Cancelled by user.[/yellow]")
            break

        # Three-way dispatch matching the three AWAITING_HUMAN gates:
        #   1. Judge gate — judge_verdict produced, awaiting accept/abort/rerun
        #   2. Conflict gate — at least one HumanDecisionRequest still pending
        #   3. Plan-review gate — fallthrough (pending_user_decisions / overall plan)
        awaiting_judge = (
            state.judge_verdict is not None and state.judge_resolution is None
        )
        has_pending_conflicts = any(
            req.human_decision is None for req in state.human_decision_requests.values()
        )
        if awaiting_judge:
            await bridge.wait_for_judge_resolution()
        elif has_pending_conflicts:
            await bridge.wait_for_human_decisions()
        else:
            await bridge.wait_for_plan_review()

        if _bridge_cancelled(bridge):
            console.print("[yellow]Cancelled by user.[/yellow]")
            break

        await bridge.broadcast_state_patch()


def _apply_workflow_from_setup(
    config: MergeConfig, runtime: SetupPayload | None
) -> MergeConfig:
    """Overlay a named workflow preset onto the freshly-saved config.

    The workflow choice is a session hint (lives only on the Setup form
    submission), so we apply it after ``apply_setup_payload`` has
    already written the base config.yaml. Failures here are surfaced
    but non-fatal — the run continues with the saved config minus the
    preset, matching the pre-PR-3 ``--workflow`` flag behaviour.
    """
    if runtime is None or not runtime.workflow:
        return config
    from src.core.workflow_loader import apply_workflow_by_name, load_workflows

    try:
        catalog = load_workflows()
        config = apply_workflow_by_name(config, runtime.workflow, catalog)
        wf_def = catalog.workflows[runtime.workflow]
        console.print(
            f"[cyan]Workflow applied:[/cyan] [bold]{runtime.workflow}[/bold] "
            f"(review_mode={wf_def.review_mode}, dry_run={wf_def.dry_run})"
        )
    except (FileNotFoundError, KeyError, ValueError) as e:
        console.print(
            f"[yellow]Workflow '{runtime.workflow}' not applied: {e}[/yellow]"
        )
    return config


def _maybe_draft_forks_profile(
    runtime: SetupPayload | None, config: MergeConfig, repo_path: str
) -> None:
    """Non-interactive drafter — only fires when the form opted in.

    Mirrors the post-wizard prompt the old terminal flow used, minus
    the ``$EDITOR`` launch (the browser is the editor now). Any draft
    failure is logged and skipped so a transient git issue cannot
    block the orchestrator launch.
    """
    if runtime is None or not runtime.init_forks_profile:
        return
    from src.cli.commands.setup import draft_forks_profile_file

    try:
        out_path = draft_forks_profile_file(
            target_branch=config.upstream_ref,
            fork_ref=config.fork_ref,
            repo_path=repo_path,
        )
        if out_path is not None:
            console.print(f"  [green]Drafted forks-profile:[/green] {out_path}")
    except Exception as e:
        console.print(f"  [yellow]forks-profile draft failed (skip): {e}[/yellow]")


def _open_browser_or_print(url: str, open_browser: bool) -> None:
    if not open_browser:
        return
    try:
        webbrowser.open(url)
    except (
        webbrowser.Error,
        OSError,
    ) as exc:  # pragma: no cover - browser env quirks
        logger.warning("Failed to open browser: %s", exc)


def _bridge_cancelled(bridge: MergeWSBridge) -> bool:
    """Phase 0 stub — real cancel_event lands in Phase 1.

    Reading via getattr keeps the wiring forward-compatible: once Phase 1
    adds ``MergeWSBridge.is_cancelled``, this helper picks it up without
    touching the orchestrator loop.
    """
    is_cancelled = getattr(bridge, "is_cancelled", None)
    if callable(is_cancelled):
        result = is_cancelled()
        return bool(result)
    return False
