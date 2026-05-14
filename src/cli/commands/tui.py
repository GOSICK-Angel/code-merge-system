"""CLI command: merge tui — launch interactive terminal UI."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml
from rich.console import Console

from src.core.orchestrator import Orchestrator
from src.core.phases.base import ActivityEvent
from src.models.config import MergeConfig
from src.models.state import MergeState
from src.web.ws_bridge import MergeWSBridge

logger = logging.getLogger(__name__)
console = Console()


def tui_command_impl(
    config_path_or_config: str | MergeConfig,
    ws_port: int,
    dry_run: bool = False,
) -> None:
    """Launch the React Ink TUI alongside the merge orchestrator.

    Accepts either a file path string or an already-constructed MergeConfig.
    """
    if isinstance(config_path_or_config, MergeConfig):
        merge_config = config_path_or_config
    else:
        config_file = Path(config_path_or_config)
        raw_config = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        merge_config = MergeConfig.model_validate(raw_config)

    state = MergeState(config=merge_config, dry_run=dry_run)
    asyncio.run(_run_tui(state, merge_config, ws_port))


def tui_resume_impl(state: MergeState, ws_port: int) -> None:
    """Launch the TUI against an already-loaded checkpoint state.

    The bridge takes a snapshot of ``state`` on first broadcast, so the TUI
    opens directly on the checkpoint's current_phase / status instead of
    the fresh-run initialize screen. AWAITING_HUMAN cycles inside the run
    loop are handled identically to ``tui_command_impl``.
    """
    asyncio.run(_run_tui(state, state.config, ws_port))


async def _run_tui(
    state: MergeState,
    config: MergeConfig,
    ws_port: int,
) -> None:
    bridge = MergeWSBridge(state)
    await bridge.start("localhost", ws_port)

    orchestrator = Orchestrator(config)

    def _on_transition(s: MergeState, target: object, reason: str) -> None:
        bridge.notify_state_change(reason)

    orchestrator.state_machine.add_observer(_on_transition)

    def _on_activity(event: ActivityEvent) -> None:
        bridge.notify_agent_activity(event)
        bridge.notify_state_change(f"{event.agent}: {event.action}")

    orchestrator.set_activity_callback(_on_activity)

    tui_stdout_fd = os.dup(sys.stdout.fileno())
    tui_proc = _spawn_tui_process(ws_port, tui_stdout_fd)

    if tui_proc is None:
        # _spawn_tui_process already explained why (missing entry point /
        # missing npx / npm install failure) on the live stdout. We must
        # NOT mute Python stdio in this branch — that would also hide
        # the bridge.wait_for_client timeout 30s later, which is exactly
        # the "merge hangs silently after `Press Enter`" symptom users
        # reported when ``tui/node_modules`` is absent.
        console.print(
            "[red]TUI failed to start.[/red] "
            "Re-run with `--no-tui` to fall back to plain-text output."
        )
        await bridge.stop()
        os.close(tui_stdout_fd)
        raise SystemExit(1)

    _mute_python_stdio()

    try:
        await bridge.wait_for_client(timeout=30.0)

        while True:
            state = await orchestrator.run(state)
            await bridge.broadcast_state_patch()

            if state.status.value != "awaiting_human":
                break

            has_pending_conflicts = any(
                req.human_decision is None
                for req in state.human_decision_requests.values()
            )
            if has_pending_conflicts:
                await bridge.wait_for_human_decisions()
            else:
                await bridge.wait_for_plan_review()
            await bridge.broadcast_state_patch()

        if tui_proc and tui_proc.poll() is None:
            await _wait_proc_async(tui_proc)
    except KeyboardInterrupt:
        pass
    finally:
        _restore_python_stdio()
        if tui_proc and tui_proc.poll() is None:
            tui_proc.terminate()
            try:
                await asyncio.wait_for(_wait_proc_async(tui_proc), timeout=5)
            except asyncio.TimeoutError:
                tui_proc.kill()
        await bridge.stop()
        os.close(tui_stdout_fd)


async def _wait_proc_async(proc: subprocess.Popen[bytes]) -> int:
    """Wait for subprocess without blocking the event loop."""
    while proc.poll() is None:
        await asyncio.sleep(0.1)
    return proc.returncode


def _mute_python_stdio() -> None:
    """Redirect Python stdout/stderr to devnull so prints don't corrupt Ink."""
    devnull = open(os.devnull, "w")
    sys.stdout = devnull
    sys.stderr = devnull


def _restore_python_stdio() -> None:
    """Restore original stdout/stderr after TUI exits."""
    sys.stdout = sys.__stdout__
    sys.stderr = sys.__stderr__


def _ensure_tui_deps(tui_dir: Path, stdout_fd: int) -> bool:
    """Make sure ``tui/node_modules`` is populated before launching tsx.

    First-run users frequently hit a silent hang because ``npx tsx`` will
    happily start, fail to resolve ``react`` / ``ink`` / ``ws`` from the
    bare ``tui/`` directory, and exit immediately — meanwhile the Python
    side has muted stdio and is waiting on the WebSocket handshake. This
    helper short-circuits that path: if ``node_modules`` is missing we
    run ``npm install`` *before* muting stdio, with output piped to the
    live terminal so progress is visible. Returns False (and prints a
    diagnostic) on any failure so the caller can bail out cleanly.
    """
    node_modules = tui_dir / "node_modules"
    if node_modules.exists():
        return True

    npm = shutil.which("npm")
    if npm is None:
        console.print(
            "[yellow]npm not found; cannot install TUI dependencies.[/yellow] "
            "Install Node.js or re-run merge with `--no-tui`."
        )
        return False

    console.print(
        f"[bold]Installing TUI dependencies at {tui_dir} "
        "(first run only, ~1 min)...[/bold]"
    )
    try:
        result = subprocess.run(
            [npm, "install"],
            cwd=str(tui_dir),
            stdout=stdout_fd,
            stderr=stdout_fd,
            check=False,
        )
    except OSError as exc:
        console.print(f"[red]npm install failed to launch:[/red] {exc}")
        return False

    if result.returncode != 0:
        console.print(
            f"[red]npm install exited with code {result.returncode}.[/red] "
            f"Run `cd {tui_dir} && npm install` manually, or re-run merge "
            "with `--no-tui`."
        )
        return False

    console.print("[green]TUI dependencies installed.[/green]")
    return True


def _spawn_tui_process(ws_port: int, stdout_fd: int) -> subprocess.Popen[bytes] | None:
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

    if not _ensure_tui_deps(tui_dir, stdout_fd):
        return None

    try:
        proc = subprocess.Popen(
            [npx, "tsx", str(entry_point), "--ws-port", str(ws_port)],
            cwd=str(tui_dir),
            stdin=sys.stdin,
            stdout=stdout_fd,
            stderr=subprocess.DEVNULL,
        )
        return proc
    except FileNotFoundError:
        console.print("[yellow]Failed to launch TUI process.[/yellow]")
        return None
