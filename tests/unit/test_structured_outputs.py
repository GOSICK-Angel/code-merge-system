"""P2-1 Structured Outputs reliability layer.

Native Structured Outputs (OpenAI ``response_format=json_schema`` /
Anthropic forced tool-use) produce a well-formed JSON string that still
flows through the existing response parsers. The path is opt-in via
``AgentLLMConfig.use_structured_outputs`` (default False) and degrades
automatically to prompt-injection JSON when the gateway rejects it.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pydantic import BaseModel

from src.agents.conflict_analyst_agent import ConflictAnalystAgent
from src.llm.client import (
    AnthropicClient,
    LLMClient,
    OpenAIClient,
    _append_schema_instruction,
    _to_openai_strict_schema,
)
from src.llm.structured_schemas import (
    BATCH_FILE_REVIEW,
    COMMIT_ROUND,
    CONFLICT_ANALYSIS,
    DECISION_PROPOSALS,
    FILE_REVIEW,
    JUDGE_RE_EVALUATE,
    JUDGE_VERDICT,
    PLAN_CLASSIFICATION,
    PLAN_JUDGE_VERDICT,
    _WIRE_MODELS,
    wire_schema,
)
from src.models.config import AgentLLMConfig
from src.models.diff import FileDiff, FileStatus, RiskLevel

_SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}}}


def _anthropic(**kw: Any) -> AnthropicClient:
    with patch("anthropic.AsyncAnthropic"):
        return AnthropicClient(
            model="claude-haiku-4-5-20251001",
            api_key="k",
            temperature=0.2,
            max_tokens=4096,
            max_retries=1,
            **kw,
        )


def _openai() -> OpenAIClient:
    with patch("openai.AsyncOpenAI"):
        return OpenAIClient(
            model="gpt-4o", api_key="k", temperature=0.2, max_tokens=1024, max_retries=1
        )


def _anthropic_text_block(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    resp.stop_reason = "end_turn"
    return resp


def _openai_resp(content: str) -> MagicMock:
    choice = MagicMock()
    choice.message.content = content
    choice.finish_reason = "stop"
    resp = MagicMock()
    resp.choices = [choice]
    return resp


class TestStrictSchema:
    def test_objects_get_additional_properties_false_and_required(self) -> None:
        # Every registered wire schema must satisfy OpenAI strict-mode
        # constraints, including nested $defs / arrays of objects.
        assert len(_WIRE_MODELS) == 9
        for name in _WIRE_MODELS:
            strict = _to_openai_strict_schema(wire_schema(name))

            def _check(node: Any) -> None:
                if isinstance(node, dict):
                    if node.get("type") == "object" and "properties" in node:
                        assert node["additionalProperties"] is False
                        assert set(node["required"]) == set(node["properties"])
                    for v in node.values():
                        _check(v)
                elif isinstance(node, list):
                    for v in node:
                        _check(v)

            _check(strict)


class TestAbcFallback:
    async def test_default_appends_schema_instruction(self) -> None:
        captured: dict[str, Any] = {}

        class _Stub(LLMClient):
            model = "stub"

            async def complete(
                self,
                messages: list[dict[str, Any]],
                system: str | None = None,
                **kw: Any,
            ) -> str:
                captured["messages"] = messages
                return "STUBOUT"

            async def complete_structured(
                self,
                messages: list[dict[str, Any]],
                schema: type[BaseModel],
                system: str | None = None,
            ) -> BaseModel:
                raise NotImplementedError

        out = await _Stub().structured_json(
            [{"role": "user", "content": "task"}],
            json_schema=_SCHEMA,
            schema_name="thing",
        )
        assert out == "STUBOUT"
        assert (
            "JSON object conforming to this schema"
            in captured["messages"][-1]["content"]
        )

    def test_append_helper_creates_user_turn_when_absent(self) -> None:
        out = _append_schema_instruction([], _SCHEMA)
        assert out[-1]["role"] == "user"
        assert "schema" in out[-1]["content"].lower()


class TestAnthropicStructured:
    async def test_forced_tool_use_returns_input_json(self) -> None:
        client = _anthropic()
        block = MagicMock()
        block.type = "tool_use"
        block.input = {"recommended_strategy": "semantic_merge"}
        resp = MagicMock()
        resp.content = [block]
        client._client.messages.create = AsyncMock(return_value=resp)

        out = await client.structured_json(
            [{"role": "user", "content": "analyze"}],
            json_schema=_SCHEMA,
            schema_name="conflict_analysis",
        )
        assert json.loads(out) == {"recommended_strategy": "semantic_merge"}
        kwargs = client._client.messages.create.call_args.kwargs
        assert kwargs["tool_choice"] == {"type": "tool", "name": "conflict_analysis"}
        assert kwargs["tools"][0]["input_schema"] == _SCHEMA

    async def test_no_tool_use_block_falls_back_to_prompt(self) -> None:
        client = _anthropic()
        # First create() (forced tool-use) returns only text → unsupported;
        # the fallback complete() create() returns the JSON text block.
        client._client.messages.create = AsyncMock(
            side_effect=[
                _anthropic_text_block("ignored"),
                _anthropic_text_block('{"x": "fallback"}'),
            ]
        )
        out = await client.structured_json(
            [{"role": "user", "content": "go"}],
            json_schema=_SCHEMA,
            schema_name="thing",
        )
        assert json.loads(out) == {"x": "fallback"}
        assert client._client.messages.create.await_count == 2

    async def test_thinking_proxy_503_falls_back_to_prompt(self) -> None:
        # Some proxies force interleaved thinking on, which rejects forced
        # tool_choice with a 503 "Thinking mode does not support this
        # tool_choice". That must degrade to prompt injection, not propagate.
        import anthropic
        import httpx

        client = _anthropic()
        req = httpx.Request("POST", "http://x")
        resp = httpx.Response(503, request=req)
        err = anthropic.InternalServerError(
            "Thinking mode does not support this tool_choice",
            response=resp,
            body=None,
        )
        client._client.messages.create = AsyncMock(
            side_effect=[err, _anthropic_text_block('{"x": "degraded"}')]
        )
        out = await client.structured_json(
            [{"role": "user", "content": "go"}],
            json_schema=_SCHEMA,
            schema_name="thing",
        )
        assert json.loads(out) == {"x": "degraded"}

    async def test_unrelated_5xx_propagates(self) -> None:
        import anthropic
        import httpx

        client = _anthropic()
        req = httpx.Request("POST", "http://x")
        resp = httpx.Response(503, request=req)
        err = anthropic.InternalServerError(
            "upstream connect error", response=resp, body=None
        )
        client._client.messages.create = AsyncMock(side_effect=err)
        with pytest.raises(anthropic.InternalServerError):
            await client.structured_json(
                [{"role": "user", "content": "go"}],
                json_schema=_SCHEMA,
                schema_name="thing",
            )

    async def test_thinking_budget_skips_native_tool_use(self) -> None:
        client = _anthropic(thinking_budget_tokens=2048)
        client._client.messages.create = AsyncMock(
            return_value=_anthropic_text_block('{"x": "viathinking"}')
        )
        out = await client.structured_json(
            [{"role": "user", "content": "go"}],
            json_schema=_SCHEMA,
            schema_name="thing",
        )
        assert json.loads(out) == {"x": "viathinking"}
        # Forced tool-use is incompatible with thinking → no tools sent.
        kwargs = client._client.messages.create.call_args.kwargs
        assert "tools" not in kwargs


class TestOpenAIStructured:
    async def test_json_schema_response_format_passed(self) -> None:
        client = _openai()
        client._client.chat.completions.create = AsyncMock(
            return_value=_openai_resp('{"x": "ok"}')
        )
        out = await client.structured_json(
            [{"role": "user", "content": "review"}],
            json_schema=_SCHEMA,
            schema_name="file_review",
        )
        assert json.loads(out) == {"x": "ok"}
        kwargs = client._client.chat.completions.create.call_args.kwargs
        rf = kwargs["response_format"]
        assert rf["type"] == "json_schema"
        assert rf["json_schema"]["name"] == "file_review"
        assert rf["json_schema"]["strict"] is True

    async def test_falls_back_to_json_object_then_succeeds(self) -> None:
        client = _openai()
        # First call (json_schema) raises an unsupported-kwarg TypeError;
        # second call (json_object) succeeds.
        client._client.chat.completions.create = AsyncMock(
            side_effect=[
                TypeError("unsupported response_format"),
                _openai_resp('{"x":"jo"}'),
            ]
        )
        out = await client.structured_json(
            [{"role": "user", "content": "review"}],
            json_schema=_SCHEMA,
            schema_name="file_review",
        )
        assert json.loads(out) == {"x": "jo"}
        assert client._client.chat.completions.create.await_count == 2
        second = client._client.chat.completions.create.call_args_list[1].kwargs
        assert second["response_format"] == {"type": "json_object"}


class TestConfigFlag:
    def test_defaults_false(self) -> None:
        assert AgentLLMConfig().use_structured_outputs is False

    def test_opt_in(self) -> None:
        assert (
            AgentLLMConfig(use_structured_outputs=True).use_structured_outputs is True
        )


class TestAgentWiring:
    def _agent(self, structured: bool) -> ConflictAnalystAgent:
        cfg = AgentLLMConfig(
            provider="anthropic",
            model="m",
            api_key_env="ANTHROPIC_API_KEY",
            max_retries=1,
            use_structured_outputs=structured,
        )
        return ConflictAnalystAgent(cfg)

    def test_structured_kwargs_empty_when_off(self) -> None:
        assert self._agent(False)._structured_kwargs(CONFLICT_ANALYSIS) == {}

    def test_structured_kwargs_populated_when_on(self) -> None:
        kw = self._agent(True)._structured_kwargs(CONFLICT_ANALYSIS)
        assert kw["schema_name"] == CONFLICT_ANALYSIS
        assert kw["json_schema"] == wire_schema(CONFLICT_ANALYSIS)

    def test_structured_kwargs_resolve_for_every_piloted_schema(self) -> None:
        agent = self._agent(True)
        for name in (
            CONFLICT_ANALYSIS,
            FILE_REVIEW,
            PLAN_JUDGE_VERDICT,
            COMMIT_ROUND,
            DECISION_PROPOSALS,
            BATCH_FILE_REVIEW,
            JUDGE_VERDICT,
            JUDGE_RE_EVALUATE,
            PLAN_CLASSIFICATION,
        ):
            kw = agent._structured_kwargs(name)
            assert kw["schema_name"] == name
            assert kw["json_schema"] == wire_schema(name)

    async def test_analyze_file_passes_schema_when_enabled(self) -> None:
        agent = self._agent(True)
        fd = FileDiff(
            file_path="a.py",
            file_status=FileStatus.MODIFIED,
            risk_level=RiskLevel.AUTO_RISKY,
            risk_score=0.4,
            lines_added=3,
            lines_deleted=1,
        )
        spy = AsyncMock(return_value="{}")
        with patch.object(agent, "_call_llm_with_retry", new=spy):
            await agent.analyze_file(fd, None, "fork", "upstream")
        assert spy.await_count >= 1
        kwargs = spy.call_args.kwargs
        assert kwargs.get("schema_name") == CONFLICT_ANALYSIS
        assert kwargs.get("json_schema") == wire_schema(CONFLICT_ANALYSIS)

    async def test_analyze_file_no_schema_when_disabled(self) -> None:
        agent = self._agent(False)
        fd = FileDiff(
            file_path="a.py",
            file_status=FileStatus.MODIFIED,
            risk_level=RiskLevel.AUTO_RISKY,
            risk_score=0.4,
            lines_added=3,
            lines_deleted=1,
        )
        spy = AsyncMock(return_value="{}")
        with patch.object(agent, "_call_llm_with_retry", new=spy):
            await agent.analyze_file(fd, None, "fork", "upstream")
        assert "json_schema" not in spy.call_args.kwargs


class TestPlannerWiring:
    def _planner(self, structured: bool) -> Any:
        from src.agents.planner_agent import PlannerAgent

        cfg = AgentLLMConfig(
            provider="anthropic",
            model="m",
            api_key_env="ANTHROPIC_API_KEY",
            max_retries=1,
            use_structured_outputs=structured,
        )
        return PlannerAgent(cfg)

    async def test_classify_passes_plan_schema_when_enabled(self) -> None:
        agent = self._planner(True)
        fd = FileDiff(
            file_path="a.py",
            file_status=FileStatus.MODIFIED,
            risk_level=RiskLevel.AUTO_SAFE,
            risk_score=0.2,
            lines_added=2,
            lines_deleted=0,
        )
        spy = AsyncMock(return_value="{}")
        with patch.object(agent, "_call_llm_with_retry", new=spy):
            await agent._run_single_classify([fd], "ctx", "sys", 0, 1)
        kwargs = spy.call_args.kwargs
        assert kwargs.get("schema_name") == PLAN_CLASSIFICATION
        assert kwargs.get("json_schema") == wire_schema(PLAN_CLASSIFICATION)

    async def test_classify_no_schema_when_disabled(self) -> None:
        agent = self._planner(False)
        fd = FileDiff(
            file_path="a.py",
            file_status=FileStatus.MODIFIED,
            risk_level=RiskLevel.AUTO_SAFE,
            risk_score=0.2,
            lines_added=2,
            lines_deleted=0,
        )
        spy = AsyncMock(return_value="{}")
        with patch.object(agent, "_call_llm_with_retry", new=spy):
            await agent._run_single_classify([fd], "ctx", "sys", 0, 1)
        assert "json_schema" not in spy.call_args.kwargs
