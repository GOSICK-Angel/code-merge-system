from pathlib import Path

import pytest
import yaml

from src.cli.commands import resume as resume_mod
from src.cli.commands.resume import _reload_runtime_safe_fields
from src.models.config import MergeConfig
from src.models.state import MergeState, SystemStatus


def _write_config(repo: Path, body: dict) -> None:
    merge_dir = repo / ".merge"
    merge_dir.mkdir(parents=True, exist_ok=True)
    (merge_dir / "config.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")


def _make_state(repo: Path) -> MergeState:
    base_cfg = {
        "upstream_ref": "upstream/main",
        "fork_ref": "feat/merge",
        "repo_path": str(repo),
        "commit_round_size": 5,
        "commit_round_max_files": 60,
        "agents": {
            "planner_judge": {
                "provider": "openai",
                "model": "gpt-5.4",
                "cache_strategy": "system_and_recent",
                "request_timeout_seconds": 300,
                "max_retries": 3,
                "api_key_env": "OPENAI_API_KEY",
            },
            "conflict_analyst": {
                "provider": "anthropic",
                "model": "claude-opus-4-6",
                "api_key_env": "ANTHROPIC_API_KEY",
            },
        },
    }
    return MergeState(config=MergeConfig.model_validate(base_cfg))


def test_reload_overlays_whitelisted_fields(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state = _make_state(tmp_path)
    _write_config(
        tmp_path,
        {
            "upstream_ref": "upstream/main",
            "fork_ref": "feat/merge",
            "commit_round_max_files": 30,
            "commit_round_size": 3,
            "agents": {
                "planner_judge": {
                    "provider": "openai",
                    "model": "gpt-5.4",
                    "cache_strategy": "none",
                    "request_timeout_seconds": 180,
                    "max_retries": 5,
                    "api_key_env": "OPENAI_API_KEY",
                },
                "conflict_analyst": {
                    "provider": "anthropic",
                    "model": "claude-opus-4-6",
                    "api_key_env": "ANTHROPIC_API_KEY",
                },
            },
        },
    )
    changes = _reload_runtime_safe_fields(state, str(tmp_path))
    joined = "\n".join(changes)
    assert "commit_round_size: 5 → 3" in joined
    assert "commit_round_max_files: 60 → 30" in joined
    assert state.config.commit_round_max_files == 30
    assert state.config.commit_round_size == 3
    assert state.config.agents.planner_judge.cache_strategy == "none"
    assert state.config.agents.planner_judge.request_timeout_seconds == 180
    assert state.config.agents.planner_judge.max_retries == 5


def test_reload_ignores_non_whitelisted_fields(tmp_path):
    state = _make_state(tmp_path)
    # try to change a plan-shaping field — provider/model swap MUST be ignored
    _write_config(
        tmp_path,
        {
            "upstream_ref": "upstream/main",
            "fork_ref": "feat/merge",
            "max_files_per_run": 1234,  # not in whitelist
            "agents": {
                "planner_judge": {
                    "provider": "anthropic",  # attempt to swap provider
                    "model": "claude-haiku-4-5-20251001",  # attempt model swap
                    "cache_strategy": "none",
                    "api_key_env": "ANTHROPIC_API_KEY",
                },
            },
        },
    )
    _reload_runtime_safe_fields(state, str(tmp_path))
    # plan-shaping fields untouched
    assert state.config.max_files_per_run != 1234
    assert state.config.agents.planner_judge.provider == "openai"
    assert state.config.agents.planner_judge.model == "gpt-5.4"
    # whitelisted field applied
    assert state.config.agents.planner_judge.cache_strategy == "none"


def test_reload_no_changes_when_already_aligned(tmp_path):
    state = _make_state(tmp_path)
    _write_config(
        tmp_path,
        {
            "upstream_ref": "upstream/main",
            "fork_ref": "feat/merge",
            "commit_round_size": 5,
            "commit_round_max_files": 60,
            "agents": {
                "planner_judge": {
                    "provider": "openai",
                    "model": "gpt-5.4",
                    "cache_strategy": "system_and_recent",
                    "request_timeout_seconds": 300,
                    "max_retries": 3,
                    "api_key_env": "OPENAI_API_KEY",
                },
            },
        },
    )
    changes = _reload_runtime_safe_fields(state, str(tmp_path))
    assert changes == []


def test_reload_missing_config_raises(tmp_path):
    state = _make_state(tmp_path)
    with pytest.raises(FileNotFoundError):
        _reload_runtime_safe_fields(state, str(tmp_path))


def test_resume_command_impl_invokes_reload(tmp_path, monkeypatch):
    """End-to-end smoke: passing reload_config=True calls the reload path
    before Orchestrator.run."""
    from src.core.checkpoint import Checkpoint

    state = _make_state(tmp_path)
    _write_config(
        tmp_path,
        {
            "upstream_ref": "upstream/main",
            "fork_ref": "feat/merge",
            "commit_round_max_files": 25,
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
    state.status = SystemStatus.ANALYZING_CONFLICTS
    ckpt = Checkpoint(tmp_path)
    saved_path = ckpt.save(state, tag="init")

    captured = {"cfg": None}

    class _StubOrch:
        def __init__(self, cfg):
            captured["cfg"] = cfg

        async def run(self, s):
            return s

    monkeypatch.setattr(resume_mod, "Orchestrator", _StubOrch)
    monkeypatch.chdir(tmp_path)

    resume_mod.resume_command_impl(
        run_id=None,
        checkpoint_path=str(saved_path),
        decisions=None,
        reload_config=True,
    )

    assert captured["cfg"] is not None
    assert captured["cfg"].commit_round_max_files == 25
    assert captured["cfg"].agents.planner_judge.cache_strategy == "none"
