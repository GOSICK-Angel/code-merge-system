"""Setup wizard payloads.

Pydantic v2 models bridging the Web UI Setup view and the pure
``apply_setup_payload`` writer in ``src/cli/commands/setup.py``.

Provider model (post-flexible-keys revision):

- Each LLM provider — currently ``anthropic`` and ``openai`` — has its
  own ``ProviderConfig`` (enabled / api_key / base_url / default_model).
  At least one must be enabled with a key present somewhere (payload
  or already on disk). Both being enabled is also fine.
- ``default_provider`` is the provider used for any agent that
  doesn't opt out via ``agent_choices``. Validated to be one of the
  enabled providers.
- ``agent_choices`` is an optional per-agent override map. An agent
  without an entry inherits ``default_provider`` and that provider's
  built-in recommended model for the role.

``apply_setup_payload`` is the single writer; it derives the
``agents`` block in ``.merge/config.yaml`` from this payload and
persists ``.merge/.env`` keys for whichever providers are enabled.
``build_default_payload`` synthesises an instance from env vars + git
for the ``merge --ci`` first-run fallback.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

ProviderName = Literal["anthropic", "openai"]


class ProviderConfig(BaseModel):
    """Configuration for a single LLM provider.

    ``api_key`` is optional in the payload — empty means "keep whatever
    is on disk or in the environment". The launcher consults the
    resolved key chain (shell env > project ``.env`` > global ``.env``)
    at run time, so the form doesn't need to require the user to retype
    a key already saved by a previous setup.

    ``base_url`` is the optional gateway URL (e.g. for an enterprise
    proxy or a self-hosted compatible endpoint). Written to ``.env``
    as ``ANTHROPIC_BASE_URL`` / ``OPENAI_BASE_URL``.

    ``default_model`` is the user's preferred model for this provider
    when an agent override doesn't specify a model. Per-agent built-in
    defaults (e.g. ``human_interface`` → haiku) still apply when this
    field is left blank.
    """

    enabled: bool = False
    api_key: str = ""
    base_url: str | None = None
    default_model: str = ""


class AgentChoice(BaseModel):
    """Per-agent provider + model override.

    Listed in ``SetupPayload.agent_choices`` keyed by the agent name
    (e.g. ``planner`` / ``judge`` / ``human_interface``). Agents
    missing from the map inherit ``default_provider`` + the built-in
    (provider, agent) recommended model defined in
    ``src/cli/commands/setup.py:DEFAULT_AGENT_MODELS``.

    ``model`` may be left empty to fall back to the agent-role default
    for the chosen provider; the resolver inside
    ``apply_setup_payload`` substitutes the built-in.
    """

    provider: ProviderName
    model: str = ""


class ThresholdsPayload(BaseModel):
    """Optional threshold overrides — ``None`` means use the wizard defaults."""

    auto_merge_confidence: float | None = None
    risk_score_low: float | None = None
    risk_score_high: float | None = None

    @field_validator("auto_merge_confidence", "risk_score_low", "risk_score_high")
    @classmethod
    def _validate_unit_range(cls, v: float | None) -> float | None:
        if v is None:
            return None
        if not 0.0 <= v <= 1.0:
            raise ValueError("threshold must be between 0.0 and 1.0")
        return v


class SetupPayload(BaseModel):
    """Payload submitted by the Web UI Setup view.

    ``build_default_payload`` synthesises an instance from env vars +
    git state for ``merge --ci`` first-run; ``apply_setup_payload``
    writes ``.merge/config.yaml`` and ``.merge/.env`` from it without
    any further interaction.
    """

    target_branch: str = Field(..., min_length=1)
    fork_ref: str = Field(..., min_length=1)
    project_context: str = ""

    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    github_token: str = ""

    default_provider: ProviderName | None = None
    agent_choices: dict[str, AgentChoice] = Field(default_factory=dict)

    thresholds: ThresholdsPayload | None = None
    dry_run: bool = False
    workflow: str | None = None
    init_forks_profile: bool = False

    @model_validator(mode="after")
    def _validate_providers(self) -> SetupPayload:
        enabled = self._enabled_providers()
        if not enabled:
            raise ValueError(
                "at least one provider (anthropic or openai) must be enabled"
            )

        if self.default_provider is None:
            # Auto-pick when there's no ambiguity. Both-enabled case
            # rejects so the caller (UI or CI builder) is forced to be
            # explicit — silently picking would surprise users with
            # both keys configured.
            if len(enabled) == 1:
                # Mutate via model_construct-style attribute write is
                # safe in mode="after" since we own the instance.
                object.__setattr__(self, "default_provider", enabled[0])
            else:
                raise ValueError(
                    "default_provider is required when both providers are enabled"
                )
        elif self.default_provider not in enabled:
            raise ValueError(
                f"default_provider={self.default_provider!r} is not enabled"
            )

        for agent_name, choice in self.agent_choices.items():
            if choice.provider not in enabled:
                raise ValueError(
                    f"agent_choices[{agent_name!r}].provider={choice.provider!r} "
                    "is not enabled"
                )
        return self

    def _enabled_providers(self) -> list[ProviderName]:
        out: list[ProviderName] = []
        if self.anthropic.enabled:
            out.append("anthropic")
        if self.openai.enabled:
            out.append("openai")
        return out


class ApiKeyHintSource(BaseModel):
    """Source label for an existing API key value.

    ``shell`` / ``project_env`` / ``global_env`` mirrors the priority
    chain the run-time loader uses. Empty when no value is found
    anywhere.
    """

    name: str
    masked: str = ""
    source: str = ""


class SetupContext(BaseModel):
    """Pre-fill data the Web UI fetches via ``setup.detect``."""

    current_branch: str
    suggested_target: str
    fork_divergence_count: int = 0
    has_existing_config: bool = False
    existing_config_summary: dict[str, Any] | None = None
    forks_profile_threshold: int = 30

    # Per-provider hints — replaces the old flat ``api_key_hints`` list
    # so the form can render two independent provider sections without
    # having to filter a single list.
    anthropic_key_hint: ApiKeyHintSource = Field(
        default_factory=lambda: ApiKeyHintSource(name="ANTHROPIC_API_KEY")
    )
    openai_key_hint: ApiKeyHintSource = Field(
        default_factory=lambda: ApiKeyHintSource(name="OPENAI_API_KEY")
    )
    github_token_hint: ApiKeyHintSource = Field(
        default_factory=lambda: ApiKeyHintSource(name="GITHUB_TOKEN")
    )
    anthropic_base_url: str | None = None
    openai_base_url: str | None = None

    # UI dropdown source: recommended models per provider. The form
    # presents them as suggestions; users can still type a custom model
    # name. Maintained in ``src/cli/commands/setup.py`` so the model
    # list is owned alongside the agent-role defaults.
    provider_recommended_models: dict[str, list[str]] = Field(default_factory=dict)
    # Source for the AGENT OVERRIDES table — ordered list of agent names
    # with a short blurb describing what each does.
    agent_inventory: list[dict[str, str]] = Field(default_factory=list)
