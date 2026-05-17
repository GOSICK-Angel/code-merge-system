"""Setup wizard payloads.

Two pydantic v2 models bridge the Web UI Setup view and the pure
``apply_setup_payload`` writer in ``src/cli/commands/setup.py``:

- ``SetupPayload`` — what the browser submits (or what
  ``build_default_payload`` synthesises for ``merge --ci`` first-run
  when no ``.merge/config.yaml`` exists).
- ``SetupContext`` — what the browser fetches first to pre-fill the
  form (auto-detected branch, masked API keys already on disk,
  fork-divergence hint, existing config summary if re-configuring).

Kept under ``src/models/`` rather than ``src/web/serializers.py``
because ``serializers.py`` is dict-shaped output for snapshots,
whereas these models flow in both directions and are validated on
ingest.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


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
    api_keys: dict[str, str] = Field(default_factory=dict)
    thresholds: ThresholdsPayload | None = None
    dry_run: bool = False
    workflow: str | None = None
    init_forks_profile: bool = False


class ApiKeyHint(BaseModel):
    """Masked hint for one env var — ``masked`` is empty when not set anywhere.

    ``source`` records where the existing value came from so the UI can
    explain "this came from your shell env, edits here override it for
    this project only":

    - ``"shell"`` — currently in ``os.environ`` (highest priority)
    - ``"project_env"`` — read from ``<repo>/.merge/.env``
    - ``"global_env"`` — read from ``~/.config/code-merge-system/.env``
    - ``""`` — not set
    """

    name: str
    masked: str = ""
    source: str = ""


class SetupContext(BaseModel):
    """Pre-fill data the Web UI fetches via ``setup.detect``."""

    current_branch: str
    suggested_target: str
    api_key_hints: list[ApiKeyHint] = Field(default_factory=list)
    fork_divergence_count: int = 0
    has_existing_config: bool = False
    existing_config_summary: dict[str, Any] | None = None
    forks_profile_threshold: int = 30
