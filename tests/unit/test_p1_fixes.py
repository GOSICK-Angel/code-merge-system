"""Unit tests for P1 fixes from the upstream-50-commits-v2 test report:

- O-B3: ``is_binary_asset`` routes binary files out of LLM batch review.
- O-B4: binary-safe upstream copy via ``apply_bytes_with_snapshot``.
"""

from __future__ import annotations

import base64
import subprocess
import tempfile
from pathlib import Path

import pytest

from src.models.config import MergeConfig
from src.models.decision import MergeDecision
from src.models.state import MergeState
from src.tools.binary_assets import BINARY_ASSET_EXTENSIONS, is_binary_asset
from src.tools.git_tool import GitTool
from src.tools.patch_applier import apply_bytes_with_snapshot


def test_is_binary_asset_common_extensions():
    assert is_binary_asset("tools/vanna/_assets/vanna_configure.png") is True
    assert is_binary_asset("models/icon.jpg") is True
    assert is_binary_asset("static/fonts/a.woff2") is True
    assert is_binary_asset("public/audio/bg.mp3") is True
    assert is_binary_asset("dist/plugin.zip") is True
    assert is_binary_asset("libs/mylib.so") is True
    assert is_binary_asset("vendor/tool.exe") is True


def test_is_binary_asset_case_insensitive():
    assert is_binary_asset("ICON.PNG") is True
    assert is_binary_asset("Track.Mp3") is True


def test_is_binary_asset_rejects_text_files():
    assert is_binary_asset("") is False
    assert is_binary_asset("src/app.py") is False
    assert is_binary_asset("README.md") is False
    assert is_binary_asset("config.yaml") is False
    assert is_binary_asset("style.css") is False
    assert is_binary_asset("index.html") is False
    assert is_binary_asset("data.json") is False
    assert is_binary_asset("locale.txt") is False


def test_is_binary_asset_svg_is_text():
    """SVG is XML/text — must stay in the LLM pipeline, not be filtered out."""
    assert is_binary_asset("assets/icon.svg") is False


def test_is_binary_asset_no_extension():
    assert is_binary_asset("Makefile") is False
    assert is_binary_asset("src/cli") is False


def test_binary_asset_extensions_set_is_not_empty():
    assert len(BINARY_ASSET_EXTENSIONS) > 20
    # All entries are lowercase and start with a dot.
    for ext in BINARY_ASSET_EXTENSIONS:
        assert ext.startswith(".")
        assert ext == ext.lower()


# --------------------------------------------------------------------------
# O-B4: binary-safe upstream copy path
# --------------------------------------------------------------------------


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"


def _init_repo_with_binary(tmp: Path, filename: str, content: bytes) -> GitTool:
    subprocess.run(["git", "init", "-q", str(tmp)], check=True)
    subprocess.run(["git", "-C", str(tmp), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(tmp), "config", "user.name", "t"], check=True)
    (tmp / filename).write_bytes(content)
    subprocess.run(["git", "-C", str(tmp), "add", filename], check=True)
    subprocess.run(["git", "-C", str(tmp), "commit", "-q", "-m", "seed"], check=True)
    return GitTool(str(tmp))


def test_get_file_bytes_returns_raw_bytes_for_binary():
    with tempfile.TemporaryDirectory() as tmp:
        gt = _init_repo_with_binary(Path(tmp), "icon.png", _PNG_MAGIC)
        data = gt.get_file_bytes("HEAD", "icon.png")
        assert data == _PNG_MAGIC
        assert gt.get_file_bytes("HEAD", "does/not/exist") is None


def test_get_file_bytes_round_trips_utf8():
    with tempfile.TemporaryDirectory() as tmp:
        gt = _init_repo_with_binary(
            Path(tmp), "note.txt", "hello 世界\n".encode("utf-8")
        )
        data = gt.get_file_bytes("HEAD", "note.txt")
        assert data is not None
        # `git show` may strip the trailing newline; just assert payload
        # round-trips through UTF-8.
        assert data.decode("utf-8").rstrip("\n") == "hello 世界"


@pytest.mark.asyncio
async def test_apply_bytes_with_snapshot_writes_png_intact():
    """O-B4: writing a PNG via the bytes path must preserve raw bytes
    (no UTF-8 decode/encode round trip that corrupts non-UTF-8 payloads)."""
    with tempfile.TemporaryDirectory() as tmp:
        gt = _init_repo_with_binary(Path(tmp), "seed.txt", b"seed")
        cfg = MergeConfig(upstream_ref="upstream", fork_ref="fork")
        state = MergeState(config=cfg)

        rec = await apply_bytes_with_snapshot(
            "assets/icon.png",
            _PNG_MAGIC,
            gt,
            state,
            phase="auto_merge",
            agent="binary_asset_router",
            decision=MergeDecision.TAKE_TARGET,
        )

        written = (Path(tmp) / "assets" / "icon.png").read_bytes()
        assert written == _PNG_MAGIC
        assert rec.decision == MergeDecision.TAKE_TARGET
        assert rec.is_rolled_back is False
        # original_snapshot is None (file did not pre-exist).
        assert rec.original_snapshot is None


@pytest.mark.asyncio
async def test_apply_bytes_with_snapshot_rollback_preserves_original(monkeypatch):
    """If the write itself fails, the original bytes must be restored and
    the record marks rollback."""
    with tempfile.TemporaryDirectory() as tmp:
        gt = _init_repo_with_binary(Path(tmp), "icon.png", b"OLD_BYTES")
        cfg = MergeConfig(upstream_ref="upstream", fork_ref="fork")
        state = MergeState(config=cfg)

        abs_icon = Path(tmp) / "icon.png"
        assert abs_icon.read_bytes() == b"OLD_BYTES"

        orig_write_bytes = Path.write_bytes
        call_count = {"n": 0}

        def fake_write_bytes(self, data):
            call_count["n"] += 1
            # First call = the real write attempt → raise to trigger the
            # rollback path. Subsequent calls (= rollback restore) go
            # through the real implementation.
            if call_count["n"] == 1:
                raise OSError("simulated write failure")
            return orig_write_bytes(self, data)

        monkeypatch.setattr(Path, "write_bytes", fake_write_bytes)

        rec = await apply_bytes_with_snapshot(
            "icon.png",
            _PNG_MAGIC,
            gt,
            state,
        )

        assert rec.decision == MergeDecision.ESCALATE_HUMAN
        assert rec.is_rolled_back is True
        assert rec.rationale.startswith("Binary apply failed")
        # Rollback restored the original bytes.
        assert abs_icon.read_bytes() == b"OLD_BYTES"
        assert rec.original_snapshot is not None
        assert base64.b64decode(rec.original_snapshot) == b"OLD_BYTES"


@pytest.mark.asyncio
async def test_apply_bytes_with_snapshot_base64_snapshot_of_existing_file():
    with tempfile.TemporaryDirectory() as tmp:
        gt = _init_repo_with_binary(Path(tmp), "icon.png", b"OLD")
        cfg = MergeConfig(upstream_ref="upstream", fork_ref="fork")
        state = MergeState(config=cfg)

        rec = await apply_bytes_with_snapshot(
            "icon.png",
            _PNG_MAGIC,
            gt,
            state,
        )

        assert rec.original_snapshot is not None
        assert base64.b64decode(rec.original_snapshot) == b"OLD"
        assert (Path(tmp) / "icon.png").read_bytes() == _PNG_MAGIC
