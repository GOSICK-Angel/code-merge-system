import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock
from src.models.config import MergeConfig
from src.models.decision import MergeDecision
from src.models.state import MergeState
from src.tools.patch_applier import apply_with_snapshot, _git_blob_sha


def _make_state() -> MergeState:
    config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
    return MergeState(config=config)


def _mock_git_tool_with_correct_blob(tmp_path: Path) -> MagicMock:
    """Mock that replicates `git hash-object` for whatever bytes the file
    actually has on disk — lets the post-write self-check pass."""
    git_tool = MagicMock()
    git_tool.repo_path = tmp_path

    def fake_blob_sha(rel: str) -> str:
        return _git_blob_sha((tmp_path / rel).read_bytes())

    git_tool.get_worktree_blob_sha.side_effect = fake_blob_sha
    return git_tool


@pytest.mark.asyncio
async def test_snapshot_saved_before_write():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        test_file = tmp_path / "src" / "module.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        original_content = "def old_function():\n    return 'old'\n"
        test_file.write_text(original_content, encoding="utf-8")

        git_tool = _mock_git_tool_with_correct_blob(tmp_path)

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
async def test_rollback_on_blob_self_check_mismatch():
    """Step 2: when the post-write blob hash doesn't match what we intended
    to write (encoding corruption, concurrent modification, FS gremlins),
    apply_with_snapshot must rollback and return ESCALATE_HUMAN."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        test_file = tmp_path / "src" / "critical.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        original_content = "def critical():\n    return True\n"
        test_file.write_text(original_content, encoding="utf-8")

        git_tool = MagicMock()
        git_tool.repo_path = tmp_path
        git_tool.get_worktree_blob_sha.return_value = "deadbeef" * 5

        state = _make_state()

        record = await apply_with_snapshot(
            "src/critical.py",
            "intended new content\n",
            git_tool,
            state,
        )

        assert record.decision == MergeDecision.ESCALATE_HUMAN
        assert record.is_rolled_back is True
        assert "blob_sha_mismatch" in (record.rollback_reason or "")
        assert test_file.read_text() == original_content, (
            "Self-check failure must restore original content"
        )


@pytest.mark.asyncio
async def test_self_check_passes_on_normal_write():
    """Self-check must NOT fire on the happy path — verifies the helper
    computes the same sha as `git hash-object` over freshly-written bytes."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        test_file = tmp_path / "f.txt"
        test_file.write_text("seed\n", encoding="utf-8")

        git_tool = _mock_git_tool_with_correct_blob(tmp_path)
        state = _make_state()

        record = await apply_with_snapshot(
            "f.txt",
            "alpha\nbeta\n",
            git_tool,
            state,
            decision=MergeDecision.TAKE_TARGET,
        )

        assert record.decision == MergeDecision.TAKE_TARGET
        assert record.is_rolled_back is False
        assert test_file.read_text() == "alpha\nbeta\n"
