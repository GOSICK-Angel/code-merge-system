import json
import os
import signal
from pathlib import Path
from src.models.state import MergeState


class Checkpoint:
    def __init__(self, output_dir: str):
        self.checkpoint_dir = Path(output_dir) / "checkpoints"
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def save(self, state: MergeState, tag: str) -> Path:
        run_id = state.run_id
        filename = f"run_{run_id}_{tag}.json"
        checkpoint_path = self.checkpoint_dir / filename

        data = state.model_dump(mode="json")
        checkpoint_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

        latest_link = self.checkpoint_dir / f"run_{run_id}_latest.json"
        if latest_link.exists() or latest_link.is_symlink():
            latest_link.unlink()
        try:
            latest_link.symlink_to(checkpoint_path.name)
        except (OSError, NotImplementedError):
            latest_link.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

        state_copy = state.model_copy(update={"checkpoint_path": str(checkpoint_path)})
        state.checkpoint_path = str(checkpoint_path)

        return checkpoint_path

    def load(self, checkpoint_path: Path) -> MergeState:
        if not checkpoint_path.exists():
            raise FileNotFoundError(f"Checkpoint not found: {checkpoint_path}")

        raw = checkpoint_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        return MergeState.model_validate(data)

    def list_checkpoints(self, run_id: str) -> list[Path]:
        pattern = f"run_{run_id}_*.json"
        checkpoints = [
            p for p in self.checkpoint_dir.glob(pattern)
            if not p.name.endswith("_latest.json") and not p.is_symlink()
        ]
        return sorted(checkpoints, key=lambda p: p.stat().st_mtime)

    def get_latest(self, run_id: str) -> Path | None:
        latest_link = self.checkpoint_dir / f"run_{run_id}_latest.json"
        if latest_link.exists():
            if latest_link.is_symlink():
                target = latest_link.resolve()
                if target.exists():
                    return target
            else:
                return latest_link

        checkpoints = self.list_checkpoints(run_id)
        return checkpoints[-1] if checkpoints else None

    def register_signal_handler(self, state: MergeState) -> None:
        def handler(signum, frame):
            self.save(state, "interrupt")
            raise SystemExit(0)

        try:
            signal.signal(signal.SIGINT, handler)
            signal.signal(signal.SIGTERM, handler)
        except (OSError, ValueError):
            pass
