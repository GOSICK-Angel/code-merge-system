"""CLI command: merge tui — launch interactive terminal UI."""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import sys
from pathlib import Path

import yaml
from rich.console import Console

from src.core.orchestrator import Orchestrator
from src.models.config import MergeConfig
from src.models.state import MergeState
from src.web.ws_bridge import MergeWSBridge

logger = logging.getLogger(__name__)
console = Console()


def tui_command_impl(config_path: str, ws_port: int, dry_run: bool) -> None:
    """Launch the React Ink TUI alongside the merge orchestrator."""
    config_file = Path(config_path)
    raw_config = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    merge_config = MergeConfig.model_validate(raw_config)
    state = MergeState(config=merge_config)

    asyncio.run(_run_tui(state, merge_config, ws_port, dry_run))


async def _run_tui(
    state: MergeState,
    config: MergeConfig,
    ws_port: int,
    dry_run: bool,
) -> None:
    bridge = MergeWSBridge(state)
    await bridge.start("localhost", ws_port)

    orchestrator = Orchestrator(config)

    def _on_transition(s: MergeState, target: object, reason: str) -> None:
        bridge.notify_state_change(reason)

    orchestrator.state_machine.add_observer(_on_transition)

    tui_proc = _spawn_tui_process(ws_port)

    try:
        if not dry_run:
            state = await orchestrator.run(state)
            await bridge.broadcast_state_patch()
        else:
            logger.info("Dry-run mode: waiting for TUI to exit")

        if tui_proc and tui_proc.poll() is None:
            console.print("[green]Merge complete. Press q in the TUI to exit.[/green]")
            tui_proc.wait()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted[/yellow]")
    finally:
        if tui_proc and tui_proc.poll() is None:
            tui_proc.terminate()
            tui_proc.wait(timeout=5)
        await bridge.stop()


def _spawn_tui_process(ws_port: int) -> subprocess.Popen[bytes] | None:
    tui_dir = Path(__file__).resolve().parents[3] / "tui"
    entry_point = tui_dir / "src" / "index.tsx"

    if not entry_point.exists():
        console.print(
            f"[yellow]TUI entry point not found at {entry_point}. "
            f"Run 'cd tui && npm install' first.[/yellow]"
        )
        return None

    npx = shutil.which("npx")
    if npx is None:
        console.print("[yellow]npx not found; TUI requires Node.js.[/yellow]")
        return None

    try:
        proc = subprocess.Popen(
            [npx, "tsx", str(entry_point), "--ws-port", str(ws_port)],
            cwd=str(tui_dir),
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
        return proc
    except FileNotFoundError:
        console.print("[yellow]Failed to launch TUI process.[/yellow]")
        return None
