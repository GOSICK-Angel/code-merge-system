"""resume --web dispatch tests: ensure resume_command_impl routes to the Web
UI when ``web=True``, and continues using the plain Orchestrator path
otherwise. The Web implementation itself (websocket bridge, static server)
is covered by its own integration tests — here we only verify the wiring."""

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


def test_web_flag_dispatches_to_web_resume(tmp_path, monkeypatch, stub_orch):
    state = _make_state(tmp_path)
    saved = _save(tmp_path, state)
    monkeypatch.chdir(tmp_path)

    captured_web = {
        "called": False,
        "ws_port": None,
        "web_port": None,
        "state": None,
        "open_browser": None,
    }

    def _stub_web_resume(s, ws_port, web_port, open_browser=True):
        captured_web["called"] = True
        captured_web["state"] = s
        captured_web["ws_port"] = ws_port
        captured_web["web_port"] = web_port
        captured_web["open_browser"] = open_browser

    import src.cli.commands.web as web_mod

    monkeypatch.setattr(web_mod, "web_resume_impl", _stub_web_resume)

    resume_mod.resume_command_impl(
        run_id=None,
        checkpoint_path=str(saved),
        decisions=None,
        reload_config=False,
        web=True,
        ws_port=9000,
        web_port=5173,
        open_browser=False,
    )

    assert captured_web["called"] is True
    assert captured_web["ws_port"] == 9000
    assert captured_web["web_port"] == 5173
    assert captured_web["open_browser"] is False
    assert captured_web["state"].run_id == state.run_id
    assert stub_orch["calls"] == 0


def test_no_web_flag_uses_orchestrator_path(tmp_path, monkeypatch, stub_orch):
    state = _make_state(tmp_path)
    saved = _save(tmp_path, state)
    monkeypatch.chdir(tmp_path)

    resume_mod.resume_command_impl(
        run_id=None,
        checkpoint_path=str(saved),
        decisions=None,
        reload_config=False,
        web=False,
    )
    assert stub_orch["calls"] == 1


def test_web_with_reload_config_applies_overlay_then_launches(
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

    captured_web = {"state": None}
    import src.cli.commands.web as web_mod

    def _stub_web_resume(s, ws_port, web_port, open_browser=True):
        captured_web["state"] = s

    monkeypatch.setattr(web_mod, "web_resume_impl", _stub_web_resume)

    resume_mod.resume_command_impl(
        run_id=None,
        checkpoint_path=str(saved),
        decisions=None,
        reload_config=True,
        web=True,
        ws_port=8765,
        web_port=5173,
    )

    assert captured_web["state"] is not None
    assert captured_web["state"].config.agents.planner_judge.cache_strategy == "none"
    assert stub_orch["calls"] == 0
