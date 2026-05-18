from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator


CollaborationPattern = Literal["compute", "review_only", "propose_then_confirm"]

ForbiddenRule = Literal[
    "writes_state",
    "direct_llm_call",
    "fills_missing_fields_with_defaults",
]


class AgentContract(BaseModel):
    """Single source of truth for an agent's behavioral contract.

    Loaded from ``src/agents/contracts/<name>.yaml``.  Defines the
    agent's input whitelist, output schema, reusable prompt gate IDs,
    forbidden operations, and collaboration pattern.
    """

    name: str = Field(
        ..., description="Must match the file name and AgentRegistry key."
    )
    version: int = Field(
        default=0,
        ge=0,
        description=(
            "Contract schema version. Bump when prompt/aggregation rules/IO "
            "schema changes (see _schema.md Versioning). Default 0 allows "
            "future yaml omissions to load without crash; the 7 shipped yaml "
            "all declare version=1, so default is never consumed in practice."
        ),
    )
    inputs: list[str] = Field(
        ...,
        description=(
            "Whitelist of MergeState attribute names this agent is allowed to read. "
            "Access to fields not listed here raises FieldNotInContract."
        ),
    )
    output_schema: str = Field(
        ...,
        description="Name of the Pydantic model this agent's run() returns (or wraps).",
    )
    gates: list[str] = Field(
        default_factory=list,
        description="Prompt gate IDs this agent may invoke (from gate_registry).",
    )
    forbidden: list[ForbiddenRule] = Field(
        default_factory=list,
        description="Behaviors the agent must never exhibit. Enforced by tests + runtime.",
    )
    collaboration: CollaborationPattern = Field(
        default="compute",
        description=(
            "How the agent interacts with the user/orchestrator. "
            "'compute' = pure function; 'review_only' = read-only reviewer; "
            "'propose_then_confirm' = must present >=2 options before any commit."
        ),
    )
    requires_human_options: bool = Field(
        default=False,
        description=(
            "When true, the agent must render user choices as >=2 labeled options "
            "with a recommended pick. Enforced at runtime by HumanInterface."
        ),
    )

    @field_validator("inputs")
    @classmethod
    def _unique_inputs(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("inputs must be unique")
        return v

    @field_validator("gates")
    @classmethod
    def _unique_gates(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("gates must be unique")
        return v


class FieldNotInContract(AttributeError):
    """Raised when an agent accesses a MergeState field not in its contract.inputs."""


def contracts_dir() -> Path:
    return Path(__file__).parent / "contracts"


def contract_path(name: str) -> Path:
    return contracts_dir() / f"{name}.yaml"


def load_contract(name: str) -> AgentContract:
    """Load and validate a contract yaml by agent name.

    Raises FileNotFoundError if the yaml is missing, and ValidationError
    if the schema is violated.  Callers are expected to handle missing
    files explicitly (agents without contracts simply don't call this).
    """
    path = contract_path(name)
    if not path.exists():
        raise FileNotFoundError(f"Contract file not found for agent '{name}': {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Contract yaml for '{name}' must be a mapping at top level")
    data.setdefault("name", name)
    if data["name"] != name:
        raise ValueError(
            f"Contract name mismatch: file={name!r} yaml.name={data['name']!r}"
        )
    return AgentContract.model_validate(data)


def list_contract_names() -> list[str]:
    """Return agent names for every contract yaml present on disk."""
    return sorted(p.stem for p in contracts_dir().glob("*.yaml"))
