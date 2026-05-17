"""Tests for the flexible-provider setup payload model + helpers.

Covers:
- ``SetupPayload`` validators: at least one provider enabled,
  default_provider auto-pick / explicit-when-ambiguous, agent_choices
  references a disabled provider.
- ``apply_setup_payload``: writes only enabled providers' env vars,
  honours per-agent overrides, github block only when token set,
  threshold overrides on top of factory defaults.
- ``build_default_payload``: env-only build picks the right
  default_provider and enables/disables the right providers.
- ``detect_setup_context``: per-provider key hint priority chain,
  reads existing config summary including agents block.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from src.cli.commands.setup import (
    apply_setup_payload,
    build_default_payload,
    detect_setup_context,
)
from src.models.setup import (
    AgentChoice,
    ProviderConfig,
    SetupPayload,
    ThresholdsPayload,
)


@pytest.fixture(autouse=True)
def _clean_api_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GITHUB_TOKEN",
        "ANTHROPIC_BASE_URL",
        "OPENAI_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)


_DEFAULT_ANTHROPIC_MODELS = ["claude-opus-4-7", "claude-haiku-4-5-20251001"]
_DEFAULT_OPENAI_MODELS = ["gpt-5.4", "gpt-5.4-mini"]


def _payload(**overrides: object) -> SetupPayload:
    defaults: dict[str, object] = {
        "target_branch": "upstream/main",
        "fork_ref": "feat/x",
        "project_context": "",
        "anthropic": ProviderConfig(
            enabled=True, api_key="sk-ant", models=list(_DEFAULT_ANTHROPIC_MODELS)
        ),
    }
    defaults.update(overrides)
    return SetupPayload.model_validate(defaults)


class TestSetupPayloadValidation:
    def test_missing_target_branch_rejected(self) -> None:
        with pytest.raises(Exception):
            SetupPayload.model_validate(
                {
                    "fork_ref": "feat/x",
                    "anthropic": {
                        "enabled": True,
                        "api_key": "k",
                        "models": ["claude-opus-4-7"],
                    },
                }
            )

    def test_no_provider_enabled_rejected(self) -> None:
        with pytest.raises(Exception):
            SetupPayload.model_validate({"target_branch": "u", "fork_ref": "f"})

    def test_both_enabled_requires_explicit_default(self) -> None:
        with pytest.raises(Exception):
            SetupPayload.model_validate(
                {
                    "target_branch": "u",
                    "fork_ref": "f",
                    "anthropic": {
                        "enabled": True,
                        "api_key": "k",
                        "models": ["claude-opus-4-7"],
                    },
                    "openai": {
                        "enabled": True,
                        "api_key": "k",
                        "models": ["gpt-5.4"],
                    },
                }
            )

    def test_enabled_provider_with_empty_models_rejected(self) -> None:
        with pytest.raises(Exception):
            SetupPayload.model_validate(
                {
                    "target_branch": "u",
                    "fork_ref": "f",
                    "anthropic": {"enabled": True, "api_key": "k", "models": []},
                }
            )

    def test_agent_choice_model_must_be_in_provider_list(self) -> None:
        with pytest.raises(Exception):
            SetupPayload.model_validate(
                {
                    "target_branch": "u",
                    "fork_ref": "f",
                    "anthropic": {
                        "enabled": True,
                        "api_key": "k",
                        "models": ["claude-opus-4-7"],
                    },
                    "agent_choices": {
                        "planner": {
                            "provider": "anthropic",
                            "model": "claude-opus-4-DOES-NOT-EXIST",
                        }
                    },
                }
            )

    def test_single_provider_auto_picks_default(self) -> None:
        p = _payload()
        assert p.default_provider == "anthropic"

    def test_agent_choice_must_reference_enabled_provider(self) -> None:
        with pytest.raises(Exception):
            SetupPayload.model_validate(
                {
                    "target_branch": "u",
                    "fork_ref": "f",
                    "anthropic": {
                        "enabled": True,
                        "api_key": "k",
                        "models": ["claude-opus-4-7"],
                    },
                    "agent_choices": {"planner": {"provider": "openai"}},
                }
            )

    def test_threshold_out_of_range_rejected(self) -> None:
        with pytest.raises(Exception):
            ThresholdsPayload.model_validate({"auto_merge_confidence": 1.5})


class TestApplySetupPayload:
    def test_writes_config_and_env(self, tmp_path: Path) -> None:
        payload = _payload(
            project_context="dify fork",
            anthropic=ProviderConfig(
                enabled=True,
                api_key="sk-ant-test",
                base_url="https://gw",
                models=list(_DEFAULT_ANTHROPIC_MODELS),
            ),
        )
        config = apply_setup_payload(payload, str(tmp_path))

        cfg_path = tmp_path / ".merge" / "config.yaml"
        env_path = tmp_path / ".merge" / ".env"
        assert cfg_path.exists()
        assert env_path.exists()

        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        assert raw["upstream_ref"] == "upstream/main"
        assert raw["project_context"] == "dify fork"
        # All agents on anthropic since only it is enabled.
        for spec in raw["agents"].values():
            assert spec["provider"] == "anthropic"
            assert spec["api_key_env"] == "ANTHROPIC_API_KEY"
        assert config.upstream_ref == "upstream/main"

        env_text = env_path.read_text(encoding="utf-8")
        assert "ANTHROPIC_API_KEY" in env_text
        assert "ANTHROPIC_BASE_URL" in env_text
        # No openai key supplied → no env entry, no github block.
        assert "OPENAI_API_KEY" not in env_text
        assert "github" not in raw

    def test_github_block_set_when_token_supplied(self, tmp_path: Path) -> None:
        payload = _payload(github_token="ghp_xxx")
        apply_setup_payload(payload, str(tmp_path))
        raw = yaml.safe_load(
            (tmp_path / ".merge" / "config.yaml").read_text(encoding="utf-8")
        )
        assert raw["github"] == {"enabled": True, "token_env": "GITHUB_TOKEN"}

    def test_non_default_agent_gets_fallback_pointing_at_default(
        self, tmp_path: Path
    ) -> None:
        # When an agent's primary (provider, model) differs from
        # (default_provider, default_provider.models[0]), the resolver
        # must attach a fallback block so BaseAgent's circuit breaker
        # can recover when the primary fails (e.g. model deprecated,
        # 429 storm, provider outage). Same-as-default agents skip
        # fallback to avoid a self-pointing retry.
        payload = _payload(
            anthropic=ProviderConfig(
                enabled=True,
                api_key="ak",
                models=["claude-opus-4-7", "claude-haiku-4-5-20251001"],
            ),
            openai=ProviderConfig(
                enabled=True, api_key="ok", models=["gpt-5.4", "gpt-5.4-mini"]
            ),
            default_provider="anthropic",
            agent_choices={
                "planner_judge": AgentChoice(provider="openai", model="gpt-5.4-mini"),
                "human_interface": AgentChoice(
                    provider="anthropic", model="claude-haiku-4-5-20251001"
                ),
            },
        )
        apply_setup_payload(payload, str(tmp_path))
        raw = yaml.safe_load(
            (tmp_path / ".merge" / "config.yaml").read_text(encoding="utf-8")
        )

        # planner_judge runs on openai → fallback to anthropic opus.
        pj_fb = raw["agents"]["planner_judge"].get("fallback")
        assert pj_fb is not None
        assert pj_fb["provider"] == "anthropic"
        assert pj_fb["model"] == "claude-opus-4-7"
        assert pj_fb["api_key_env"] == "ANTHROPIC_API_KEY"

        # human_interface runs on anthropic haiku (same provider, diff
        # model) → still needs fallback to opus.
        hi_fb = raw["agents"]["human_interface"].get("fallback")
        assert hi_fb is not None
        assert hi_fb["model"] == "claude-opus-4-7"

        # planner already matches default → fallback would be self-pointing
        # and is therefore omitted.
        assert "fallback" not in raw["agents"]["planner"]

    def test_per_agent_override_routes_to_other_provider(self, tmp_path: Path) -> None:
        payload = _payload(
            anthropic=ProviderConfig(
                enabled=True, api_key="ak", models=list(_DEFAULT_ANTHROPIC_MODELS)
            ),
            openai=ProviderConfig(
                enabled=True, api_key="ok", models=list(_DEFAULT_OPENAI_MODELS)
            ),
            default_provider="anthropic",
            agent_choices={
                "planner_judge": AgentChoice(provider="openai", model="gpt-5.4-mini"),
            },
        )
        apply_setup_payload(payload, str(tmp_path))
        raw = yaml.safe_load(
            (tmp_path / ".merge" / "config.yaml").read_text(encoding="utf-8")
        )
        assert raw["agents"]["planner_judge"]["provider"] == "openai"
        assert raw["agents"]["planner_judge"]["model"] == "gpt-5.4-mini"
        assert raw["agents"]["planner_judge"]["api_key_env"] == "OPENAI_API_KEY"
        # Other agents inherit default_provider + default_provider.models[0].
        assert raw["agents"]["planner"]["provider"] == "anthropic"
        assert raw["agents"]["planner"]["model"] == _DEFAULT_ANTHROPIC_MODELS[0]

    def test_agents_without_override_use_default_providers_first_model(
        self, tmp_path: Path
    ) -> None:
        # The user enumerates models; models[0] is the implicit default
        # for every unassigned agent. No more per-(provider, agent)
        # table — if you want haiku for human_interface, ask for it
        # explicitly via agent_choices.
        payload = _payload(
            anthropic=ProviderConfig(
                enabled=True,
                api_key="k",
                models=["claude-opus-4-7", "claude-haiku-4-5-20251001"],
            ),
        )
        apply_setup_payload(payload, str(tmp_path))
        raw = yaml.safe_load(
            (tmp_path / ".merge" / "config.yaml").read_text(encoding="utf-8")
        )
        for name, spec in raw["agents"].items():
            assert spec["provider"] == "anthropic"
            assert spec["model"] == "claude-opus-4-7", name

    def test_explicit_human_interface_override_picks_haiku(
        self, tmp_path: Path
    ) -> None:
        payload = _payload(
            anthropic=ProviderConfig(
                enabled=True,
                api_key="k",
                models=["claude-opus-4-7", "claude-haiku-4-5-20251001"],
            ),
            agent_choices={
                "human_interface": AgentChoice(
                    provider="anthropic", model="claude-haiku-4-5-20251001"
                ),
            },
        )
        apply_setup_payload(payload, str(tmp_path))
        raw = yaml.safe_load(
            (tmp_path / ".merge" / "config.yaml").read_text(encoding="utf-8")
        )
        assert raw["agents"]["human_interface"]["model"] == "claude-haiku-4-5-20251001"
        # Everyone else still uses models[0].
        assert raw["agents"]["planner"]["model"] == "claude-opus-4-7"

    def test_explicit_thresholds_beat_defaults(self, tmp_path: Path) -> None:
        payload = _payload(
            thresholds=ThresholdsPayload(
                auto_merge_confidence=0.95, risk_score_high=0.5
            ),
        )
        apply_setup_payload(payload, str(tmp_path))
        raw = yaml.safe_load(
            (tmp_path / ".merge" / "config.yaml").read_text(encoding="utf-8")
        )
        assert raw["thresholds"]["auto_merge_confidence"] == 0.95
        assert raw["thresholds"]["risk_score_high"] == 0.5
        assert raw["thresholds"]["risk_score_low"] == 0.30

    def test_no_api_keys_supplied_skips_env_file(self, tmp_path: Path) -> None:
        # enabled=True but api_key="" means "keep what's on disk"; with
        # no on-disk file either, no env writes happen.
        payload = SetupPayload.model_validate(
            {
                "target_branch": "u",
                "fork_ref": "f",
                "anthropic": {
                    "enabled": True,
                    "api_key": "",
                    "models": ["claude-opus-4-7"],
                },
            }
        )
        apply_setup_payload(payload, str(tmp_path))
        assert not (tmp_path / ".merge" / ".env").exists()


class TestBuildDefaultPayload:
    def test_anthropic_only_in_env(self, tmp_path: Path) -> None:
        with (
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-a"}, clear=False),
            patch(
                "src.cli.commands.setup._auto_detect_fork_ref",
                return_value="feat/ci",
            ),
            patch(
                "src.cli.commands.setup._detect_upstream_default",
                return_value="origin/main",
            ),
        ):
            payload = build_default_payload(str(tmp_path))

        assert payload.anthropic.enabled is True
        assert payload.openai.enabled is False
        assert payload.default_provider == "anthropic"

    def test_openai_only_in_env(self, tmp_path: Path) -> None:
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "sk-o"}, clear=False),
            patch(
                "src.cli.commands.setup._auto_detect_fork_ref",
                return_value="feat/ci",
            ),
            patch(
                "src.cli.commands.setup._detect_upstream_default",
                return_value="origin/main",
            ),
        ):
            payload = build_default_payload(str(tmp_path))

        assert payload.openai.enabled is True
        assert payload.anthropic.enabled is False
        assert payload.default_provider == "openai"

    def test_neither_in_env_falls_back_to_anthropic_skeleton(
        self, tmp_path: Path
    ) -> None:
        with (
            patch(
                "src.cli.commands.setup._auto_detect_fork_ref",
                return_value="feat/ci",
            ),
            patch(
                "src.cli.commands.setup._detect_upstream_default",
                return_value="origin/main",
            ),
        ):
            payload = build_default_payload(str(tmp_path))

        assert payload.anthropic.enabled is True
        assert payload.anthropic.api_key == ""
        assert payload.default_provider == "anthropic"


class TestDetectSetupContext:
    def test_no_config_yet(self, tmp_path: Path) -> None:
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
                return_value=42,
            ),
        ):
            ctx = detect_setup_context(str(tmp_path))

        assert ctx.has_existing_config is False
        assert ctx.existing_config_summary is None
        assert ctx.current_branch == "feat/x"
        assert ctx.suggested_target == "origin/main"
        assert ctx.fork_divergence_count == 42
        assert ctx.forks_profile_threshold == 30
        # recommended models per provider published for the UI dropdown.
        assert "anthropic" in ctx.provider_recommended_models
        assert "openai" in ctx.provider_recommended_models
        # agent inventory contains the 6 known agents.
        names = [e["name"] for e in ctx.agent_inventory]
        assert "planner" in names
        assert "human_interface" in names

    def test_summarises_existing_config_including_agents(self, tmp_path: Path) -> None:
        # Seed a config with one agent override so the reconfigure flow
        # can pre-fill the AGENT OVERRIDES table.
        apply_setup_payload(
            SetupPayload.model_validate(
                {
                    "target_branch": "upstream/main",
                    "fork_ref": "feat/x",
                    "anthropic": {
                        "enabled": True,
                        "api_key": "k",
                        "models": ["claude-opus-4-7"],
                    },
                }
            ),
            str(tmp_path),
        )

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
                return_value=0,
            ),
        ):
            ctx = detect_setup_context(str(tmp_path))

        assert ctx.has_existing_config is True
        assert ctx.existing_config_summary is not None
        assert "agents" in ctx.existing_config_summary
        agents = ctx.existing_config_summary["agents"]
        assert isinstance(agents, dict)
        assert agents["planner"]["provider"] == "anthropic"

    def test_api_key_hint_priority_shell_beats_project_env(
        self, tmp_path: Path
    ) -> None:
        apply_setup_payload(
            SetupPayload.model_validate(
                {
                    "target_branch": "u",
                    "fork_ref": "f",
                    "openai": {
                        "enabled": True,
                        "api_key": "sk-from-file",
                        "models": ["gpt-5.4"],
                    },
                }
            ),
            str(tmp_path),
        )

        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "sk-from-shell"}, clear=False),
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
                return_value=0,
            ),
        ):
            ctx = detect_setup_context(str(tmp_path))

        # shell wins over project_env, mask reflects the shell value.
        assert ctx.openai_key_hint.source == "shell"
        assert ctx.openai_key_hint.masked != "sk-from-shell"
        assert "sk-" in ctx.openai_key_hint.masked

        # An env var not set anywhere has empty masked + source
        assert ctx.github_token_hint.masked == ""
        assert ctx.github_token_hint.source == ""
