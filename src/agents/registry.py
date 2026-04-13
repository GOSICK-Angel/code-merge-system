"""B3: Agent registry and factory.

Provides a central ``AgentRegistry`` that decouples agent creation from the
Orchestrator.  Each concrete agent self-registers at import time via
``AgentRegistry.register()``, and the Orchestrator (or tests) can create
agents via ``AgentRegistry.create()`` or ``AgentRegistry.create_all()``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from src.agents.base_agent import BaseAgent
from src.models.config import AgentLLMConfig, MergeConfig

logger = logging.getLogger(__name__)

AgentFactory = Callable[..., BaseAgent]


class AgentRegistry:
    """Global registry mapping agent names to their factory callables."""

    _factories: dict[str, AgentFactory] = {}
    _extra_kwargs_map: dict[str, list[str]] = {}

    @classmethod
    def register(
        cls,
        name: str,
        factory: AgentFactory,
        *,
        extra_kwargs: list[str] | None = None,
    ) -> None:
        """Register an agent factory under *name*.

        Parameters
        ----------
        name:
            Logical name matching the attribute on ``AgentsLLMConfig``
            (e.g. ``"planner"``, ``"executor"``).
        factory:
            A callable (typically the agent class itself) that accepts
            ``(AgentLLMConfig, **kwargs) -> BaseAgent``.
        extra_kwargs:
            Names of extra keyword arguments that must be supplied at
            creation time (e.g. ``["git_tool"]``).
        """
        cls._factories[name] = factory
        cls._extra_kwargs_map[name] = extra_kwargs or []

    @classmethod
    def create(cls, name: str, config: AgentLLMConfig, **kwargs: Any) -> BaseAgent:
        """Instantiate a single agent by name."""
        if name not in cls._factories:
            raise ValueError(
                f"Unknown agent '{name}'. Registered: {sorted(cls._factories)}"
            )
        return cls._factories[name](config, **kwargs)

    @classmethod
    def create_all(
        cls, config: MergeConfig, **shared_kwargs: Any
    ) -> dict[str, BaseAgent]:
        """Create all registered agents from *config*.

        Each agent reads its ``AgentLLMConfig`` from ``config.agents.<name>``.
        Extra kwargs (like ``git_tool``) are forwarded from *shared_kwargs*
        when the agent's registration declares them.
        """
        agents: dict[str, BaseAgent] = {}
        for name in cls._factories:
            agent_llm_config: AgentLLMConfig = getattr(config.agents, name)
            extra = {
                k: shared_kwargs[k]
                for k in cls._extra_kwargs_map.get(name, [])
                if k in shared_kwargs
            }
            agents[name] = cls._factories[name](agent_llm_config, **extra)
        return agents

    @classmethod
    def registered_names(cls) -> list[str]:
        return sorted(cls._factories)

    @classmethod
    def is_registered(cls, name: str) -> bool:
        return name in cls._factories

    @classmethod
    def clear(cls) -> None:
        """Remove all registrations (useful in tests)."""
        cls._factories.clear()
        cls._extra_kwargs_map.clear()
