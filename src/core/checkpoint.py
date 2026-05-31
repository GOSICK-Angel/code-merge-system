import json
import logging
import signal
from pathlib import Path
from typing import Any
from src.models.state import MergeState

logger = logging.getLogger(__name__)


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    """Write JSON atomically via temp file + rename (POSIX-safe)."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.rename(path)


class Checkpoint:
    """Single rolling checkpoint per run directory.

    Default mode: one ``checkpoint.json`` file, overwritten on each save.
    Debug mode (``debug_checkpoints=True``): additionally writes tagged copies
    into ``<run_dir>/checkpoints_debug/<tag>.json`` for phase-by-phase tracing.
    """

    def __init__(self, run_dir: Path, *, debug_checkpoints: bool = False) -> None:
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._debug = debug_checkpoints
        if self._debug:
            self._debug_dir = self.run_dir / "checkpoints_debug"
            self._debug_dir.mkdir(exist_ok=True)

    def save(self, state: MergeState, tag: str = "") -> Path:
        """Overwrite the single checkpoint.json and optionally save a tagged debug copy."""
        main_path = self.run_dir / "checkpoint.json"
        data = state.model_dump(mode="json")
        _atomic_write(main_path, data)
        state.checkpoint_path = str(main_path)

        if self._debug and tag:
            debug_path = self._debug_dir / f"{tag}.json"
            debug_path.write_text(
                json.dumps(data, indent=2, default=str), encoding="utf-8"
            )

        return main_path

    def load(self, checkpoint_path: Path | None = None) -> MergeState:
        path = checkpoint_path or (self.run_dir / "checkpoint.json")
        if not path.exists():
            raise FileNotFoundError(f"Checkpoint not found: {path}")

        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Checkpoint corrupted (invalid JSON): {path}") from e

        try:
            return MergeState.model_validate(data)
        except Exception as e:
            logger.error("Checkpoint schema validation failed: %s", e)
            raise RuntimeError(f"Checkpoint schema mismatch: {path}") from e

    def get_latest(self) -> Path | None:
        """Return the single checkpoint path if it exists, else None."""
        path = self.run_dir / "checkpoint.json"
        return path if path.exists() else None

    def register_signal_handler(self, state: MergeState) -> None:
        def handler(signum: int, frame: object) -> None:
            # Restore default disposition first so a second ^C during
            # cleanup (e.g. while ``static_server.stop()`` is blocked on
            # ``threading.Event.wait``) is a clean kernel SIGINT instead
            # of re-entering this handler and re-raising ``SystemExit``
            # on top of a partially-torn-down asyncio loop.
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    signal.signal(sig, signal.SIG_DFL)
                except (OSError, ValueError):
                    pass
            try:
                self.save(state, "interrupt")
            except Exception as exc:
                logger.error("Checkpoint save failed during interrupt: %s", exc)
            raise SystemExit(0)

        try:
            signal.signal(signal.SIGINT, handler)
            signal.signal(signal.SIGTERM, handler)
        except (OSError, ValueError):
            pass
