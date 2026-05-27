"""Tests for the flexible-provider setup payload model + helpers.

Covers:
- ``SetupPayload`` validators: at least one provider enabled,
  default_provider auto-pick / explicit-when-ambiguous, agent_choices
  references a disabled provider.
- ``apply_setup_payload``: writes only enabled providers' env vars,
  honours per-agent overrides, threshold overrides on top of factory
  defaults.
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
    ModelParams,
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
        "ANTHROPIC_MODELS",
        "OPENAI_MODELS",
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
        # Models list is also persisted to .env so the next setup run
        # can prefill the textarea from the global resolution chain.
        assert "ANTHROPIC_MODELS" in env_text
        assert ",".join(_DEFAULT_ANTHROPIC_MODELS) in env_text
        # No openai key supplied → no env entry, no github block.
        assert "OPENAI_API_KEY" not in env_text
        assert "OPENAI_MODELS" not in env_text
        assert "github" not in raw

    def test_cross_provider_fallback_protects_every_agent(self, tmp_path: Path) -> None:
        # With both providers enabled, every agent gets a *cross-provider*
        # fallback so a single-provider outage (429 storm, downed gateway,
        # deprecated model id) can't stall the run. Agents on the default
        # provider fall over to the other provider's first model; agents on
        # the other provider take the reverse direction back to the
        # default. No agent is left without a lifeline — that's the gap the
        # old "fall back to default provider only" behaviour left open.
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

        # planner_judge runs on openai (non-default) → reverse fallback to
        # the default provider's first model.
        pj_fb = raw["agents"]["planner_judge"].get("fallback")
        assert pj_fb is not None
        assert pj_fb["provider"] == "anthropic"
        assert pj_fb["model"] == "claude-opus-4-7"
        assert pj_fb["api_key_env"] == "ANTHROPIC_API_KEY"

        # human_interface runs on the default provider (anthropic) →
        # cross-provider fallback to the other provider's first model.
        hi_fb = raw["agents"]["human_interface"].get("fallback")
        assert hi_fb is not None
        assert hi_fb["provider"] == "openai"
        assert hi_fb["model"] == "gpt-5.4"
        assert hi_fb["api_key_env"] == "OPENAI_API_KEY"

        # planner runs on the default provider too — previously left with
        # no fallback at all; now it falls over to openai.
        planner_fb = raw["agents"]["planner"].get("fallback")
        assert planner_fb is not None
        assert planner_fb["provider"] == "openai"
        assert planner_fb["model"] == "gpt-5.4"

    def test_explicit_fallback_choice_overrides_default_direction(
        self, tmp_path: Path
    ) -> None:
        # The UI can pin a specific fallback model for the default-provider
        # agents; agents on the other provider still take the reverse.
        payload = _payload(
            anthropic=ProviderConfig(
                enabled=True, api_key="ak", models=list(_DEFAULT_ANTHROPIC_MODELS)
            ),
            openai=ProviderConfig(
                enabled=True, api_key="ok", models=list(_DEFAULT_OPENAI_MODELS)
            ),
            default_provider="anthropic",
            fallback=AgentChoice(provider="openai", model="gpt-5.4-mini"),
        )
        apply_setup_payload(payload, str(tmp_path))
        raw = yaml.safe_load(
            (tmp_path / ".merge" / "config.yaml").read_text(encoding="utf-8")
        )
        # planner on default anthropic → user-pinned openai gpt-5.4-mini.
        planner_fb = raw["agents"]["planner"].get("fallback")
        assert planner_fb is not None
        assert planner_fb["provider"] == "openai"
        assert planner_fb["model"] == "gpt-5.4-mini"

    def test_single_provider_keeps_same_provider_model_fallback(
        self, tmp_path: Path
    ) -> None:
        # Only one provider enabled → nothing to cross-fall to. An agent on
        # a non-default model still gets a same-provider fallback to
        # models[0]; default-model agents stay fallback-free.
        payload = _payload(
            anthropic=ProviderConfig(
                enabled=True,
                api_key="ak",
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
        hi_fb = raw["agents"]["human_interface"].get("fallback")
        assert hi_fb is not None
        assert hi_fb["provider"] == "anthropic"
        assert hi_fb["model"] == "claude-opus-4-7"
        assert "fallback" not in raw["agents"]["planner"]

    def test_fallback_provider_must_be_enabled(self) -> None:
        with pytest.raises(ValueError, match="fallback.provider.*not enabled"):
            _payload(
                anthropic=ProviderConfig(
                    enabled=True, api_key="ak", models=list(_DEFAULT_ANTHROPIC_MODELS)
                ),
                default_provider="anthropic",
                fallback=AgentChoice(provider="openai", model="gpt-5.4"),
            )

    def test_agents_get_per_model_params_from_payload(self, tmp_path: Path) -> None:
        # Each agent inherits the params of the model it runs, sourced from
        # payload.model_params. Two agents on the same model share them.
        payload = _payload(
            anthropic=ProviderConfig(
                enabled=True,
                api_key="ak",
                models=["claude-opus-4-7", "claude-haiku-4-5-20251001"],
            ),
            agent_choices={
                "human_interface": AgentChoice(
                    provider="anthropic", model="claude-haiku-4-5-20251001"
                ),
            },
            model_params={
                "claude-opus-4-7": ModelParams(
                    max_tokens=9001, temperature=0.15, max_retries=4
                ),
                "claude-haiku-4-5-20251001": ModelParams(
                    max_tokens=2048, temperature=0.3, max_retries=2
                ),
            },
        )
        apply_setup_payload(payload, str(tmp_path))
        raw = yaml.safe_load(
            (tmp_path / ".merge" / "config.yaml").read_text(encoding="utf-8")
        )
        planner = raw["agents"]["planner"]  # opus (default)
        assert planner["max_tokens"] == 9001
        assert planner["temperature"] == 0.15
        assert planner["max_retries"] == 4
        hi = raw["agents"]["human_interface"]  # haiku
        assert hi["max_tokens"] == 2048
        assert hi["temperature"] == 0.3
        assert hi["max_retries"] == 2

    def test_model_params_fall_back_to_recommended_when_absent(
        self, tmp_path: Path
    ) -> None:
        # No model_params supplied → resolver fills recommended defaults by
        # model family (gpt-5* reasoning → 32768, opus → 8192).
        payload = _payload(
            anthropic=ProviderConfig(
                enabled=True, api_key="ak", models=list(_DEFAULT_ANTHROPIC_MODELS)
            ),
            openai=ProviderConfig(
                enabled=True, api_key="ok", models=list(_DEFAULT_OPENAI_MODELS)
            ),
            default_provider="anthropic",
            agent_choices={
                "executor": AgentChoice(provider="openai", model="gpt-5.4"),
            },
        )
        apply_setup_payload(payload, str(tmp_path))
        raw = yaml.safe_load(
            (tmp_path / ".merge" / "config.yaml").read_text(encoding="utf-8")
        )
        assert raw["agents"]["planner"]["max_tokens"] == 8192  # opus default
        assert raw["agents"]["executor"]["max_tokens"] == 32768  # gpt-5.4 reasoning
        # The cross-provider fallback block carries the fallback model's params.
        exec_fb = raw["agents"]["executor"]["fallback"]
        assert exec_fb["model"] == _DEFAULT_ANTHROPIC_MODELS[0]
        assert exec_fb["max_tokens"] == 8192

    def test_fallback_model_must_be_in_provider_list(self) -> None:
        with pytest.raises(ValueError, match="fallback.model.*not in"):
            _payload(
                anthropic=ProviderConfig(
                    enabled=True, api_key="ak", models=list(_DEFAULT_ANTHROPIC_MODELS)
                ),
                openai=ProviderConfig(
                    enabled=True, api_key="ok", models=list(_DEFAULT_OPENAI_MODELS)
                ),
                default_provider="anthropic",
                fallback=AgentChoice(provider="openai", model="gpt-nonexistent"),
            )

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

    def test_blank_api_key_still_persists_models(self, tmp_path: Path) -> None:
        # ``api_key=""`` means "keep what's on disk", but the models
        # list is its own state managed by the wizard — when the user
        # supplies models without retyping the key we still want them
        # persisted so the next setup can prefill the textarea from
        # the env resolution chain.
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
        env_path = tmp_path / ".merge" / ".env"
        assert env_path.exists()
        env_text = env_path.read_text(encoding="utf-8")
        assert "ANTHROPIC_MODELS" in env_text
        assert "claude-opus-4-7" in env_text
        # The empty key was NOT written — the on-disk key (if any) is
        # preserved by ``write_env_file``'s merge semantics.
        assert "ANTHROPIC_API_KEY" not in env_text


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

    def test_provider_models_env_overrides_hardcoded_recommendation(
        self, tmp_path: Path
    ) -> None:
        """``ANTHROPIC_MODELS`` / ``OPENAI_MODELS`` from the env chain
        seed the UI textarea instead of the hardcoded list."""
        with (
            patch.dict(
                os.environ,
                {
                    "ANTHROPIC_MODELS": "claude-custom-a, claude-custom-b",
                    "OPENAI_MODELS": "gpt-custom-x\ngpt-custom-y",
                },
                clear=False,
            ),
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

        assert ctx.provider_recommended_models["anthropic"] == [
            "claude-custom-a",
            "claude-custom-b",
        ]
        assert ctx.provider_recommended_models["openai"] == [
            "gpt-custom-x",
            "gpt-custom-y",
        ]

    def test_provider_models_falls_back_to_hardcoded_when_env_unset(
        self, tmp_path: Path
    ) -> None:
        """No ``*_MODELS`` env var → keeps the built-in recommendation
        so existing first-run UX is unchanged."""
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

        from src.cli.commands.setup import PROVIDER_RECOMMENDED_MODELS

        assert ctx.provider_recommended_models["anthropic"] == list(
            PROVIDER_RECOMMENDED_MODELS["anthropic"]
        )
        assert ctx.provider_recommended_models["openai"] == list(
            PROVIDER_RECOMMENDED_MODELS["openai"]
        )


class TestConfigOverrides:
    """``config_overrides`` deep-merges into the generated config.yaml and is
    validated before the file is written (Web config UI Phase 1)."""

    def test_overrides_deep_merged_and_persisted(self, tmp_path: Path) -> None:
        payload = _payload(
            config_overrides={
                "max_files_per_run": 123,
                "dependency_graph": {"god_node_min_dependents": 12},
                "thresholds": {"human_escalation": 0.42},
            }
        )
        config = apply_setup_payload(payload, str(tmp_path))

        raw = yaml.safe_load(
            (tmp_path / ".merge" / "config.yaml").read_text(encoding="utf-8")
        )
        assert raw["max_files_per_run"] == 123
        assert raw["dependency_graph"]["god_node_min_dependents"] == 12
        # Curated threshold defaults survive a nested override that only
        # touches a sibling key.
        assert raw["thresholds"]["auto_merge_confidence"] == 0.85
        assert raw["thresholds"]["human_escalation"] == 0.42

        assert config.max_files_per_run == 123
        assert config.dependency_graph.god_node_min_dependents == 12
        assert config.thresholds.human_escalation == 0.42

    def test_invalid_override_rejected_before_write(self, tmp_path: Path) -> None:
        # max_files_per_run has ge=1 — 0 must fail validation, and because
        # validation runs before the write, no config.yaml is left behind.
        payload = _payload(config_overrides={"max_files_per_run": 0})
        with pytest.raises(Exception):
            apply_setup_payload(payload, str(tmp_path))
        assert not (tmp_path / ".merge" / "config.yaml").exists()
