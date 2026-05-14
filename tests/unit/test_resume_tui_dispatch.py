"""resume --tui dispatch tests: ensure resume_command_impl routes to TUI
when tui=True, and continues using the plain Orchestrator path otherwise.
The TUI implementation itself (websocket bridge, ink subprocess) is covered
by its own integration tests — here we only verify the wiring."""

from pathlib import Path

import pytest
import yaml

from src.cli.commands import resume as resume_mod
from src.core.checkpoint import Checkpoint
from src.models.config import MergeConfig
from src.models.state import MergeState, SystemStatus


def _write_config(repo: Path, body: dict) -> None:
    merge_dir = repo / ".merge"
    merge_dir.mkdir(parents=True, exist_ok=True)
    (merge_dir / "config.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")


def _make_state(repo: Path) -> MergeState:
    cfg = MergeConfig.model_validate(
        {
            "upstream_ref": "upstream/main",
            "fork_ref": "feat/merge",
            "repo_path": str(repo),
            "agents": {
                "planner_judge": {
                    "provider": "openai",
                    "model": "gpt-5.4",
                    "api_key_env": "OPENAI_API_KEY",
                },
            },
        }
    )
    s = MergeState(config=cfg)
    s.status = SystemStatus.ANALYZING_CONFLICTS
    return s


def _save(repo: Path, state: MergeState) -> Path:
    ckpt = Checkpoint(repo)
    return ckpt.save(state, tag="init")


@pytest.fixture
def stub_orch(monkeypatch):
    captured = {"calls": 0, "cfg": None}

    class _StubOrch:
        def __init__(self, cfg):
            captured["calls"] += 1
            captured["cfg"] = cfg

        async def run(self, s):
            return s

    monkeypatch.setattr(resume_mod, "Orchestrator", _StubOrch)
    return captured


def test_tui_flag_dispatches_to_tui_resume(tmp_path, monkeypatch, stub_orch):
    state = _make_state(tmp_path)
    saved = _save(tmp_path, state)
    monkeypatch.chdir(tmp_path)

    captured_tui = {"called": False, "ws_port": None, "state": None}

    def _stub_tui_resume(s, ws_port):
        captured_tui["called"] = True
        captured_tui["state"] = s
        captured_tui["ws_port"] = ws_port

    # Patch where it's imported (inside resume_command_impl)
    import src.cli.commands.tui as tui_mod

    monkeypatch.setattr(tui_mod, "tui_resume_impl", _stub_tui_resume)

    resume_mod.resume_command_impl(
        run_id=None,
        checkpoint_path=str(saved),
        decisions=None,
        reload_config=False,
        tui=True,
        ws_port=9000,
    )

    assert captured_tui["called"] is True
    assert captured_tui["ws_port"] == 9000
    assert captured_tui["state"].run_id == state.run_id
    # Orchestrator path must NOT run when tui=True
    assert stub_orch["calls"] == 0


def test_no_tui_flag_uses_orchestrator_path(tmp_path, monkeypatch, stub_orch):
    state = _make_state(tmp_path)
    saved = _save(tmp_path, state)
    monkeypatch.chdir(tmp_path)

    resume_mod.resume_command_impl(
        run_id=None,
        checkpoint_path=str(saved),
        decisions=None,
        reload_config=False,
        tui=False,
    )
    assert stub_orch["calls"] == 1


def test_tui_with_reload_config_applies_overlay_then_launches(
    tmp_path, monkeypatch, stub_orch
):
    state = _make_state(tmp_path)
    saved = _save(tmp_path, state)
    monkeypatch.chdir(tmp_path)
    _write_config(
        tmp_path,
        {
            "upstream_ref": "upstream/main",
            "fork_ref": "feat/merge",
            "agents": {
                "planner_judge": {
                    "provider": "openai",
                    "model": "gpt-5.4",
                    "cache_strategy": "none",
                    "api_key_env": "OPENAI_API_KEY",
                },
            },
        },
    )

    captured_tui = {"state": None}
    import src.cli.commands.tui as tui_mod

    def _stub_tui_resume(s, ws_port):
        captured_tui["state"] = s

    monkeypatch.setattr(tui_mod, "tui_resume_impl", _stub_tui_resume)

    resume_mod.resume_command_impl(
        run_id=None,
        checkpoint_path=str(saved),
        decisions=None,
        reload_config=True,
        tui=True,
        ws_port=8765,
    )

    assert captured_tui["state"] is not None
    # reload_config overlay must apply BEFORE tui dispatch
    assert captured_tui["state"].config.agents.planner_judge.cache_strategy == "none"
    assert stub_orch["calls"] == 0
