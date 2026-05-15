"""CLI command: merge web — launch interactive browser UI."""

from __future__ import annotations

import asyncio
import logging
import sys
import webbrowser
from importlib.resources import files
from pathlib import Path

import yaml
from rich.console import Console

from src.cli.exit_codes import EXIT_UNKNOWN_ERROR
from src.core.orchestrator import Orchestrator
from src.core.phases.base import ActivityEvent
from src.models.config import MergeConfig
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
    config_path_or_config: str | MergeConfig,
    ws_port: int,
    web_port: int,
    dry_run: bool = False,
    open_browser: bool = True,
) -> None:
    """Launch the Web UI alongside a fresh merge run."""
    if isinstance(config_path_or_config, MergeConfig):
        merge_config = config_path_or_config
    else:
        config_file = Path(config_path_or_config)
        raw_config = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        merge_config = MergeConfig.model_validate(raw_config)

    state = MergeState(config=merge_config, dry_run=dry_run)
    asyncio.run(_run_web(state, merge_config, ws_port, web_port, open_browser))


def web_resume_impl(
    state: MergeState,
    ws_port: int,
    web_port: int,
    open_browser: bool = True,
) -> None:
    """Launch the Web UI against an already-loaded checkpoint state."""
    asyncio.run(_run_web(state, state.config, ws_port, web_port, open_browser))


async def _run_web(
    state: MergeState,
    config: MergeConfig,
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
            "  [bold]cd web && npm ci && npm run build[/bold]\n"
            "Or re-run with [bold]--no-web[/bold] for plain-text mode."
        )
        sys.exit(EXIT_UNKNOWN_ERROR)

    bridge = MergeWSBridge(state)
    await bridge.start("localhost", ws_port)
    # L5 Report fetches markdown / checkpoint from this tree via the
    # ``/runs/<run_id>/<file>`` URL prefix (see StaticHTTPServer).
    from src.cli.paths import get_project_merge_dir

    runs_root = get_project_merge_dir(".") / "runs"
    static_server = StaticHTTPServer(
        web_dist, runs_root=runs_root if runs_root.exists() else None
    )
    await static_server.start("localhost", web_port)

    orchestrator = Orchestrator(config)

    def _on_transition(_s: MergeState, _target: object, reason: str) -> None:
        bridge.notify_state_change(reason)

    orchestrator.state_machine.add_observer(_on_transition)

    def _on_activity(event: ActivityEvent) -> None:
        bridge.notify_agent_activity(event)
        bridge.notify_state_change(f"{event.agent}: {event.action}")

    orchestrator.set_activity_callback(_on_activity)

    url = f"http://localhost:{web_port}/?ws={ws_port}"
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception as exc:  # pragma: no cover - browser env quirks
            logger.warning("Failed to open browser: %s", exc)
    console.print(f"[bold green]Web UI:[/bold green] {url}")

    try:
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

            has_pending_conflicts = any(
                req.human_decision is None
                for req in state.human_decision_requests.values()
            )
            if has_pending_conflicts:
                await bridge.wait_for_human_decisions()
            else:
                await bridge.wait_for_plan_review()

            if _bridge_cancelled(bridge):
                console.print("[yellow]Cancelled by user.[/yellow]")
                break

            await bridge.broadcast_state_patch()
    except KeyboardInterrupt:
        console.print("[yellow]Interrupted by user.[/yellow]")
    finally:
        await bridge.stop()
        await static_server.stop()


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
