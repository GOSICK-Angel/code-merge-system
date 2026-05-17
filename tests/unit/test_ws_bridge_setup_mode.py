"""Tests for the PR-1 setup-mode branch of ``MergeWSBridge``.

Exercises the WS protocol contract without spinning up a real server:
- ``setup.detect`` returns a ``setup_snapshot`` frame with ``SetupContext``
- ``setup.submit`` validates → writes via ``apply_setup_payload`` →
  emits ``setup_ready`` and unblocks ``wait_for_setup``
- ``submit_decision`` (a run-mode command) gets rejected with
  ``command_error`` while the bridge is still in setup mode
- ``transition_to_run`` flips the mode and pushes a state snapshot
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

import pytest

from src.models.config import MergeConfig
from src.models.state import MergeState, SystemStatus
from src.web.ws_bridge import MergeWSBridge


@dataclass(eq=False)
class _StubWS:
    sent: list[str] = field(default_factory=list)

    async def send(self, data: str) -> None:
        self.sent.append(data)


@pytest.fixture(autouse=True)
def _clean_api_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def setup_bridge(tmp_path: Path) -> MergeWSBridge:
    return MergeWSBridge(state=None, mode="setup", repo_path=str(tmp_path))


class TestSetupConstructor:
    def test_run_mode_without_state_rejected(self) -> None:
        with pytest.raises(ValueError):
            MergeWSBridge(state=None, mode="run")

    def test_setup_mode_constructs_without_state(self) -> None:
        bridge = MergeWSBridge(state=None, mode="setup")
        assert bridge.mode == "setup"


class TestSetupDetect:
    @pytest.mark.asyncio
    async def test_returns_setup_snapshot(
        self, setup_bridge: MergeWSBridge, tmp_path: Path
    ) -> None:
        ws = _StubWS()
        with (
            patch(
                "src.cli.commands.setup._auto_detect_fork_ref",
                return_value="feat/x",
            ),
            patch(
                "src.cli.commands.setup._detect_upstream_default",
                return_value="origin/main",
            ),
            patch(
                "src.cli.commands.setup._count_fork_deleted_files",
                return_value=12,
            ),
        ):
            await setup_bridge._handle_command(
                ws,  # type: ignore[arg-type]
                {"type": "setup.detect", "payload": {}},
            )

        assert len(ws.sent) == 1
        frame = json.loads(ws.sent[0])
        assert frame["type"] == "setup_snapshot"
        ctx = frame["payload"]
        assert ctx["current_branch"] == "feat/x"
        assert ctx["suggested_target"] == "origin/main"
        assert ctx["fork_divergence_count"] == 12
        assert ctx["has_existing_config"] is False

    @pytest.mark.asyncio
    async def test_rejected_in_run_mode(self, tmp_path: Path) -> None:
        cfg = MergeConfig(upstream_ref="upstream/main", fork_ref="feat/x")
        bridge = MergeWSBridge(
            MergeState(config=cfg), mode="run", repo_path=str(tmp_path)
        )
        ws = _StubWS()
        await bridge._handle_command(
            ws,  # type: ignore[arg-type]
            {"type": "setup.detect", "payload": {}},
        )
        frame = json.loads(ws.sent[0])
        assert frame["type"] == "command_error"
        assert frame["payload"]["reason"] == "not_in_setup_mode"


class TestSetupSubmit:
    @pytest.mark.asyncio
    async def test_valid_payload_persists_and_signals(
        self, setup_bridge: MergeWSBridge, tmp_path: Path
    ) -> None:
        ws = _StubWS()
        valid = {
            "target_branch": "upstream/main",
            "fork_ref": "feat/x",
            "project_context": "demo",
            "anthropic": {"enabled": True, "api_key": "sk-test"},
            "thresholds": None,
            "dry_run": False,
            "workflow": None,
            "init_forks_profile": False,
        }
        await setup_bridge._handle_command(
            ws,  # type: ignore[arg-type]
            {"type": "setup.submit", "payload": valid},
        )

        frames = [json.loads(s) for s in ws.sent]
        assert frames[-1]["type"] == "setup_ready"
        assert frames[-1]["payload"]["config_path"].endswith(".merge/config.yaml")

        config = await asyncio.wait_for(setup_bridge.wait_for_setup(), timeout=1)
        assert config.upstream_ref == "upstream/main"
        assert (tmp_path / ".merge" / "config.yaml").exists()
        assert (tmp_path / ".merge" / ".env").exists()

    @pytest.mark.asyncio
    async def test_invalid_payload_replies_with_error(
        self, setup_bridge: MergeWSBridge
    ) -> None:
        ws = _StubWS()
        await setup_bridge._handle_command(
            ws,  # type: ignore[arg-type]
            {"type": "setup.submit", "payload": {"target_branch": ""}},
        )
        frame = json.loads(ws.sent[0])
        assert frame["type"] == "setup_error"
        assert frame["payload"]["reason"] == "invalid_payload"
        # wait_for_setup must still block — submission failed.
        assert not setup_bridge._setup_complete.is_set()

    @pytest.mark.asyncio
    async def test_second_submit_rejected(
        self, setup_bridge: MergeWSBridge, tmp_path: Path
    ) -> None:
        ws = _StubWS()
        valid = {
            "target_branch": "upstream/main",
            "fork_ref": "feat/x",
            "anthropic": {"enabled": True, "api_key": "sk-test"},
        }
        await setup_bridge._handle_command(
            ws,  # type: ignore[arg-type]
            {"type": "setup.submit", "payload": valid},
        )
        ws.sent.clear()
        await setup_bridge._handle_command(
            ws,  # type: ignore[arg-type]
            {"type": "setup.submit", "payload": valid},
        )
        frame = json.loads(ws.sent[0])
        assert frame["type"] == "setup_error"
        assert frame["payload"]["reason"] == "already_submitted"


class TestRunCommandsBlockedDuringSetup:
    @pytest.mark.asyncio
    async def test_submit_decision_rejected(self, setup_bridge: MergeWSBridge) -> None:
        ws = _StubWS()
        await setup_bridge._handle_command(
            ws,  # type: ignore[arg-type]
            {
                "type": "submit_decision",
                "payload": {"file_path": "a.py", "decision": "USE_UPSTREAM"},
            },
        )
        frame = json.loads(ws.sent[0])
        assert frame["type"] == "command_error"
        assert frame["payload"]["reason"] == "setup_required"
        assert frame["payload"]["command"] == "submit_decision"


class TestTransitionToRun:
    @pytest.mark.asyncio
    async def test_promotes_mode_and_pushes_state(
        self, setup_bridge: MergeWSBridge, tmp_path: Path
    ) -> None:
        cfg = MergeConfig(upstream_ref="upstream/main", fork_ref="feat/x")
        state = MergeState(config=cfg, status=SystemStatus.INITIALIZED)
        # Register a client so broadcast_state_patch actually sends.
        ws = _StubWS()
        setup_bridge._clients.add(ws)  # type: ignore[arg-type]

        await setup_bridge.transition_to_run(state)

        assert setup_bridge.mode == "run"
        # broadcast_state_patch emits state_snapshot
        assert any(json.loads(s)["type"] == "state_snapshot" for s in ws.sent)

    @pytest.mark.asyncio
    async def test_transition_from_run_mode_rejected(self, tmp_path: Path) -> None:
        cfg = MergeConfig(upstream_ref="upstream/main", fork_ref="feat/x")
        state = MergeState(config=cfg)
        bridge = MergeWSBridge(state, mode="run")
        with pytest.raises(RuntimeError):
            await bridge.transition_to_run(state)
