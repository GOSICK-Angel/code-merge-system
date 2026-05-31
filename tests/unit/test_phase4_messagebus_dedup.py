"""Phase 4.2 — MessageBus / Hook 去重守护测试.

去重不变量：HookManager 是唯一的事件机制，冗余且零流量的 MessageBus
已从生产路径移除。AgentMessage / MessageType / AgentType 仍是各 agent
``run()`` 的返回类型，属活代码，不在本次移除范围。
"""

from __future__ import annotations

import importlib

import pytest

from src.core.hooks import HookManager
from src.core.phases.base import PhaseContext


class TestMessageBusRemoved:
    def test_message_bus_module_is_gone(self) -> None:
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("src.core.message_bus")

    def test_core_package_no_longer_exports_message_bus(self) -> None:
        core = importlib.import_module("src.core")
        assert "MessageBus" not in getattr(core, "__all__", [])
        assert not hasattr(core, "MessageBus")


class TestPhaseContextEventMechanism:
    def test_phase_context_has_no_message_bus_field(self) -> None:
        assert "message_bus" not in PhaseContext.__dataclass_fields__

    def test_phase_context_uses_hookmanager_as_sole_event_bus(self) -> None:
        assert "hooks" in PhaseContext.__dataclass_fields__
        field = PhaseContext.__dataclass_fields__["hooks"]
        assert field.type is HookManager or field.type == "HookManager"


class TestMessageModelStillLive:
    """去重不得殃及消息模型——它是各 agent run() 的返回契约。"""

    def test_agent_message_types_still_importable(self) -> None:
        from src.models.message import AgentMessage, AgentType, MessageType

        assert AgentMessage is not None
        assert AgentType is not None
        assert MessageType is not None
