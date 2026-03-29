import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from src.models.config import MergeConfig
from src.models.state import MergeState
from src.tools.patch_applier import apply_with_snapshot


def _make_state() -> MergeState:
    config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
    return MergeState(config=config)


@pytest.mark.asyncio
async def test_snapshot_saved_before_write():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        test_file = tmp_path / "src" / "module.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        original_content = "def old_function():\n    return 'old'\n"
        test_file.write_text(original_content, encoding="utf-8")

        git_tool = MagicMock()
        git_tool.repo_path = tmp_path

        state = _make_state()
        new_content = "def new_function():\n    return 'new'\n"

        record = await apply_with_snapshot(
            "src/module.py",
            new_content,
            git_tool,
            state,
        )

        assert record.original_snapshot == original_content, (
            "Original snapshot must be saved before writing"
        )
        assert test_file.read_text() == new_content, "New content must be written"


@pytest.mark.asyncio
async def test_rollback_on_failure():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        test_file = tmp_path / "src" / "critical.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        original_content = "def critical():\n    return True\n"
        test_file.write_text(original_content, encoding="utf-8")

        git_tool = MagicMock()
        git_tool.repo_path = tmp_path

        state = _make_state()

        def write_raise(*args, **kwargs):
            raise PermissionError("Simulated write failure")

        with patch("builtins.open", side_effect=write_raise):
            pass

        record = await apply_with_snapshot(
            "src/critical.py",
            "INVALID CONTENT THAT FAILS",
            git_tool,
            state,
        )

        assert record is not None
        assert record.original_snapshot == original_content
