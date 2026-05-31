import os

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pydantic import BaseModel

from src.llm.client import (
    AnthropicClient,
    LLMClientFactory,
    ModelOutputError,
    OpenAIClient,
    ParseError,
    _build_httpx_timeout,
    uses_openai_wire,
)
from src.llm.prompt_caching import CacheStrategy
from src.models.config import AgentLLMConfig


class SimpleSchema(BaseModel):
    name: str
    value: int


def _make_anthropic_client(
    cache_strategy: CacheStrategy = CacheStrategy.SYSTEM_AND_RECENT,
) -> AnthropicClient:
    with patch("anthropic.AsyncAnthropic"):
        return AnthropicClient(
            model="claude-opus-4-6",
            api_key="test-key",
            temperature=0.2,
            max_tokens=1024,
            max_retries=3,
            cache_strategy=cache_strategy,
        )


def _make_openai_client() -> OpenAIClient:
    with patch("openai.AsyncOpenAI"):
        return OpenAIClient(
            model="gpt-4o",
            api_key="test-key",
            temperature=0.2,
            max_tokens=1024,
            max_retries=3,
        )


class TestBuildHttpxTimeout:
    """Fix 6: ``request_timeout_seconds`` must drive the httpx ``read``
    timeout end-to-end. The previous ``min(total, 90)`` cap silently
    truncated user config — a 300s setting only ever produced a 90s
    read timeout, which is why planner_judge on Opus timed out for the
    forgejo run even though config said 300s."""

    def test_read_timeout_honors_full_request_timeout(self):
        timeout = _build_httpx_timeout(300.0)
        assert timeout.read == 300.0, (
            "read timeout must equal the full request_timeout_seconds, "
            "not the legacy 90s cap"
        )

    def test_short_timeout_passes_through(self):
        # Users who want to fail fast (CI, behind Cloudflare with 120s
        # proxy timeout) can still set a small value and have it honored.
        timeout = _build_httpx_timeout(45.0)
        assert timeout.read == 45.0

    def test_connect_write_pool_unchanged(self):
        # Only ``read`` was capped — the other phases stay at the values
        # tuned for "dead pool entry shouldn't hang the request".
        timeout = _build_httpx_timeout(300.0)
        assert timeout.connect == 10.0
        assert timeout.write == 30.0
        assert timeout.pool == 10.0


class TestAnthropicClientInit:
    def test_stores_model(self):
        client = _make_anthropic_client()
        assert client.model == "claude-opus-4-6"

    def test_stores_temperature(self):
        client = _make_anthropic_client()
        assert client.temperature == 0.2

    def test_stores_max_tokens(self):
        client = _make_anthropic_client()
        assert client.max_tokens == 1024

    def test_stores_max_retries(self):
        client = _make_anthropic_client()
        assert client.max_retries == 3


class TestAnthropicClientComplete:
    async def test_returns_text_content(self):
        client = _make_anthropic_client()
        mock_content = MagicMock()
        mock_content.text = "Hello world"
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        client._client.messages.create = AsyncMock(return_value=mock_response)

        result = await client.complete([{"role": "user", "content": "Hi"}])
        assert result == "Hello world"

    async def test_passes_messages_to_api(self):
        client = _make_anthropic_client(cache_strategy=CacheStrategy.NONE)
        mock_content = MagicMock()
        mock_content.text = "response"
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        client._client.messages.create = AsyncMock(return_value=mock_response)

        messages = [{"role": "user", "content": "test"}]
        await client.complete(messages)

        call_kwargs = client._client.messages.create.call_args.kwargs
        assert call_kwargs["messages"] == messages

    async def test_passes_model_to_api(self):
        client = _make_anthropic_client()
        mock_content = MagicMock()
        mock_content.text = "response"
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        client._client.messages.create = AsyncMock(return_value=mock_response)

        await client.complete([{"role": "user", "content": "test"}])

        call_kwargs = client._client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "claude-opus-4-6"

    async def test_includes_system_when_provided(self):
        client = _make_anthropic_client(cache_strategy=CacheStrategy.NONE)
        mock_content = MagicMock()
        mock_content.text = "response"
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        client._client.messages.create = AsyncMock(return_value=mock_response)

        await client.complete(
            [{"role": "user", "content": "test"}], system="Be helpful"
        )

        call_kwargs = client._client.messages.create.call_args.kwargs
        assert call_kwargs["system"] == "Be helpful"

    async def test_system_cached_when_strategy_enabled(self):
        client = _make_anthropic_client(cache_strategy=CacheStrategy.SYSTEM_AND_RECENT)
        mock_content = MagicMock()
        mock_content.text = "response"
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        client._client.messages.create = AsyncMock(return_value=mock_response)

        await client.complete(
            [{"role": "user", "content": "test"}], system="Be helpful"
        )

        call_kwargs = client._client.messages.create.call_args.kwargs
        system_val = call_kwargs["system"]
        assert isinstance(system_val, list)
        assert system_val[0]["text"] == "Be helpful"
        assert system_val[0]["cache_control"] == {"type": "ephemeral"}

    async def test_excludes_system_when_none(self):
        client = _make_anthropic_client()
        mock_content = MagicMock()
        mock_content.text = "response"
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        client._client.messages.create = AsyncMock(return_value=mock_response)

        await client.complete([{"role": "user", "content": "test"}])

        call_kwargs = client._client.messages.create.call_args.kwargs
        assert "system" not in call_kwargs

    async def test_passes_extra_kwargs(self):
        client = _make_anthropic_client()
        mock_content = MagicMock()
        mock_content.text = "response"
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        client._client.messages.create = AsyncMock(return_value=mock_response)

        await client.complete([{"role": "user", "content": "test"}], top_p=0.9)

        call_kwargs = client._client.messages.create.call_args.kwargs
        assert call_kwargs["top_p"] == 0.9


class TestAnthropicClientCompleteStructured:
    async def test_returns_parsed_schema(self):
        client = _make_anthropic_client()
        mock_content = MagicMock()
        mock_content.text = '{"name": "Alice", "value": 42}'
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        client._client.messages.create = AsyncMock(return_value=mock_response)

        result = await client.complete_structured(
            [{"role": "user", "content": "test"}], SimpleSchema
        )
        assert isinstance(result, SimpleSchema)
        assert result.name == "Alice"
        assert result.value == 42

    async def test_strips_markdown_code_fences(self):
        client = _make_anthropic_client()
        mock_content = MagicMock()
        mock_content.text = '```json\n{"name": "Bob", "value": 10}\n```'
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        client._client.messages.create = AsyncMock(return_value=mock_response)

        result = await client.complete_structured(
            [{"role": "user", "content": "test"}], SimpleSchema
        )
        assert result.name == "Bob"
        assert result.value == 10

    async def test_strips_code_fences_without_closing(self):
        client = _make_anthropic_client()
        mock_content = MagicMock()
        mock_content.text = '```\n{"name": "Carol", "value": 5}'
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        client._client.messages.create = AsyncMock(return_value=mock_response)

        result = await client.complete_structured(
            [{"role": "user", "content": "test"}], SimpleSchema
        )
        assert result.name == "Carol"

    async def test_raises_parse_error_on_invalid_json(self):
        client = _make_anthropic_client()
        mock_content = MagicMock()
        mock_content.text = "not valid json at all"
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        client._client.messages.create = AsyncMock(return_value=mock_response)

        with pytest.raises(ParseError):
            await client.complete_structured(
                [{"role": "user", "content": "test"}], SimpleSchema
            )

    async def test_raises_model_output_error_on_schema_mismatch(self):
        client = _make_anthropic_client()
        mock_content = MagicMock()
        mock_content.text = '{"wrong_field": "oops"}'
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        client._client.messages.create = AsyncMock(return_value=mock_response)

        with pytest.raises(ModelOutputError) as exc_info:
            await client.complete_structured(
                [{"role": "user", "content": "test"}], SimpleSchema
            )
        assert exc_info.value.schema_name == "SimpleSchema"

    async def test_appends_instruction_to_last_user_message(self):
        client = _make_anthropic_client(cache_strategy=CacheStrategy.NONE)
        mock_content = MagicMock()
        mock_content.text = '{"name": "test", "value": 1}'
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        client._client.messages.create = AsyncMock(return_value=mock_response)

        messages = [{"role": "user", "content": "original"}]
        await client.complete_structured(messages, SimpleSchema)

        call_kwargs = client._client.messages.create.call_args.kwargs
        last_msg = call_kwargs["messages"][-1]
        assert last_msg["role"] == "user"
        assert "original" in last_msg["content"]
        assert "JSON" in last_msg["content"]

    async def test_adds_user_message_when_no_user_last(self):
        client = _make_anthropic_client(cache_strategy=CacheStrategy.NONE)
        mock_content = MagicMock()
        mock_content.text = '{"name": "test", "value": 1}'
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        client._client.messages.create = AsyncMock(return_value=mock_response)

        messages = [{"role": "assistant", "content": "I can help"}]
        await client.complete_structured(messages, SimpleSchema)

        call_kwargs = client._client.messages.create.call_args.kwargs
        last_msg = call_kwargs["messages"][-1]
        assert last_msg["role"] == "user"


class TestOpenAIClientInit:
    def test_stores_model(self):
        client = _make_openai_client()
        assert client.model == "gpt-4o"

    def test_stores_temperature(self):
        client = _make_openai_client()
        assert client.temperature == 0.2

    def test_stores_max_tokens(self):
        client = _make_openai_client()
        assert client.max_tokens == 1024

    def test_disables_sdk_internal_retry(self):
        with patch("openai.AsyncOpenAI") as mock:
            OpenAIClient(
                model="gpt-4o",
                api_key="test-key",
                temperature=0.2,
                max_tokens=1024,
                max_retries=3,
            )
            kwargs = mock.call_args.kwargs
            assert kwargs.get("max_retries") == 0, (
                "SDK retry must be disabled — BaseAgent owns retry policy"
            )


class TestOpenAIClientComplete:
    async def test_returns_message_content(self):
        client = _make_openai_client()
        mock_message = MagicMock()
        mock_message.content = "Hello from OpenAI"
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        client._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await client.complete([{"role": "user", "content": "Hi"}])
        assert result == "Hello from OpenAI"

    async def test_raises_on_empty_content(self):
        client = _make_openai_client()
        mock_message = MagicMock()
        mock_message.content = None
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_choice.finish_reason = "content_filter"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        client._client.chat.completions.create = AsyncMock(return_value=mock_response)

        with pytest.raises(RuntimeError, match="empty content"):
            await client.complete([{"role": "user", "content": "Hi"}])

    async def test_prepends_system_message(self):
        client = _make_openai_client()
        mock_message = MagicMock()
        mock_message.content = "response"
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        client._client.chat.completions.create = AsyncMock(return_value=mock_response)

        await client.complete(
            [{"role": "user", "content": "test"}], system="You are helpful"
        )

        call_kwargs = client._client.chat.completions.create.call_args.kwargs
        first_msg = call_kwargs["messages"][0]
        assert first_msg["role"] == "system"
        assert first_msg["content"] == "You are helpful"

    async def test_no_system_message_when_not_provided(self):
        client = _make_openai_client()
        mock_message = MagicMock()
        mock_message.content = "response"
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        client._client.chat.completions.create = AsyncMock(return_value=mock_response)

        await client.complete([{"role": "user", "content": "test"}])

        call_kwargs = client._client.chat.completions.create.call_args.kwargs
        roles = [m["role"] for m in call_kwargs["messages"]]
        assert "system" not in roles

    async def test_passes_user_messages(self):
        client = _make_openai_client()
        mock_message = MagicMock()
        mock_message.content = "response"
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        client._client.chat.completions.create = AsyncMock(return_value=mock_response)

        messages = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "second"},
            {"role": "user", "content": "third"},
        ]
        await client.complete(messages)

        call_kwargs = client._client.chat.completions.create.call_args.kwargs
        sent = call_kwargs["messages"]
        assert any(m["content"] == "first" for m in sent)
        assert any(m["content"] == "third" for m in sent)

    async def test_passes_extra_kwargs(self):
        client = _make_openai_client()
        mock_message = MagicMock()
        mock_message.content = "response"
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        client._client.chat.completions.create = AsyncMock(return_value=mock_response)

        await client.complete([{"role": "user", "content": "test"}], top_p=0.8)

        call_kwargs = client._client.chat.completions.create.call_args.kwargs
        assert call_kwargs["top_p"] == 0.8

    async def test_reasoning_model_uses_max_completion_tokens(self):
        with patch("openai.AsyncOpenAI"):
            client = OpenAIClient(
                model="gpt-5.4",
                api_key="test-key",
                temperature=0.2,
                max_tokens=32768,
                max_retries=3,
            )
        mock_message = MagicMock()
        mock_message.content = "ok"
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        client._client.chat.completions.create = AsyncMock(return_value=mock_response)

        await client.complete([{"role": "user", "content": "Hi"}])

        call_kwargs = client._client.chat.completions.create.call_args.kwargs
        assert call_kwargs["max_completion_tokens"] == 32768
        assert "reasoning_effort" not in call_kwargs, (
            "reasoning_effort must not be sent by default — proxies may not support it"
        )
        assert "max_tokens" not in call_kwargs
        assert "temperature" not in call_kwargs

    async def test_reasoning_model_with_explicit_reasoning_effort(self):
        with patch("openai.AsyncOpenAI"):
            client = OpenAIClient(
                model="gpt-5.4",
                api_key="test-key",
                temperature=0.2,
                max_tokens=32768,
                max_retries=3,
                reasoning_effort="low",
            )
        mock_message = MagicMock()
        mock_message.content = "ok"
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        client._client.chat.completions.create = AsyncMock(return_value=mock_response)

        await client.complete([{"role": "user", "content": "Hi"}])

        call_kwargs = client._client.chat.completions.create.call_args.kwargs
        assert call_kwargs["reasoning_effort"] == "low"
        assert call_kwargs["max_completion_tokens"] == 32768

    async def test_non_reasoning_model_keeps_legacy_params(self):
        client = _make_openai_client()  # gpt-4o
        mock_message = MagicMock()
        mock_message.content = "ok"
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        client._client.chat.completions.create = AsyncMock(return_value=mock_response)

        await client.complete([{"role": "user", "content": "Hi"}])

        call_kwargs = client._client.chat.completions.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 1024
        assert call_kwargs["temperature"] == 0.2


def _make_openai_responses_client(
    model: str = "gpt-5.4",
    reasoning_effort: str | None = None,
) -> OpenAIClient:
    with patch("openai.AsyncOpenAI"):
        return OpenAIClient(
            model=model,
            api_key="test-key",
            temperature=0.2,
            max_tokens=32768,
            max_retries=3,
            reasoning_effort=reasoning_effort,
            api_style="responses",
        )


def _stub_responses_create(client: OpenAIClient, output_text: str) -> AsyncMock:
    mock_block = MagicMock()
    mock_block.type = "output_text"
    mock_block.text = output_text
    mock_item = MagicMock()
    mock_item.content = [mock_block]
    mock_response = MagicMock()
    mock_response.output = [mock_item]
    mock_response.output_text = None
    mock_response.status = "completed"
    create_mock = AsyncMock(return_value=mock_response)
    client._client.responses.create = create_mock
    return create_mock


class TestOpenAIClientResponsesAPI:
    async def test_responses_style_routes_to_responses_create(self):
        client = _make_openai_responses_client()
        create_mock = _stub_responses_create(client, "hello")

        out = await client.complete([{"role": "user", "content": "hi"}])

        assert out == "hello"
        create_mock.assert_awaited_once()
        call_kwargs = create_mock.call_args.kwargs
        assert call_kwargs["model"] == "gpt-5.4"
        assert call_kwargs["input"] == "hi"
        assert call_kwargs["max_output_tokens"] == 32768
        assert "messages" not in call_kwargs
        assert "max_completion_tokens" not in call_kwargs

    async def test_responses_passes_system_as_instructions(self):
        client = _make_openai_responses_client()
        create_mock = _stub_responses_create(client, "ok")

        await client.complete([{"role": "user", "content": "hi"}], system="be terse")

        call_kwargs = create_mock.call_args.kwargs
        assert call_kwargs["instructions"] == "be terse"

    async def test_responses_translates_response_format_to_text_format(self):
        client = _make_openai_responses_client()
        create_mock = _stub_responses_create(client, '{"ok":true}')

        await client.complete(
            [{"role": "user", "content": "hi"}],
            response_format={"type": "json_object"},
        )

        call_kwargs = create_mock.call_args.kwargs
        assert call_kwargs["text"] == {"format": {"type": "json_object"}}
        assert "response_format" not in call_kwargs

    async def test_responses_passes_reasoning_effort_via_reasoning_dict(self):
        client = _make_openai_responses_client(reasoning_effort="medium")
        create_mock = _stub_responses_create(client, "ok")

        await client.complete([{"role": "user", "content": "hi"}])

        call_kwargs = create_mock.call_args.kwargs
        assert call_kwargs["reasoning"] == {"effort": "medium"}

    async def test_responses_extracts_text_from_output_blocks(self):
        client = _make_openai_responses_client()
        # output_text is null (proxy doesn't populate); must fall back to blocks
        _stub_responses_create(client, "fallback-text")

        out = await client.complete([{"role": "user", "content": "hi"}])

        assert out == "fallback-text"

    async def test_responses_raises_on_empty_output(self):
        client = _make_openai_responses_client()
        mock_response = MagicMock()
        mock_response.output = []
        mock_response.output_text = None
        mock_response.status = "incomplete"
        client._client.responses.create = AsyncMock(return_value=mock_response)

        with pytest.raises(RuntimeError, match="empty content"):
            await client.complete([{"role": "user", "content": "hi"}])

    async def test_responses_uses_list_input_for_multi_message(self):
        client = _make_openai_responses_client()
        create_mock = _stub_responses_create(client, "ok")

        await client.complete(
            [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "ack"},
                {"role": "user", "content": "second"},
            ]
        )

        call_kwargs = create_mock.call_args.kwargs
        assert isinstance(call_kwargs["input"], list)
        assert len(call_kwargs["input"]) == 3
        assert call_kwargs["input"][0] == {"role": "user", "content": "first"}
        assert "max_completion_tokens" not in call_kwargs
        assert "reasoning_effort" not in call_kwargs


class TestOpenAIClientCompleteStructured:
    async def test_returns_parsed_schema(self):
        client = _make_openai_client()
        mock_message = MagicMock()
        mock_message.content = '{"name": "Dave", "value": 99}'
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        client._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await client.complete_structured(
            [{"role": "user", "content": "test"}], SimpleSchema
        )
        assert isinstance(result, SimpleSchema)
        assert result.name == "Dave"
        assert result.value == 99

    async def test_strips_markdown_code_fences(self):
        client = _make_openai_client()
        mock_message = MagicMock()
        mock_message.content = '```json\n{"name": "Eve", "value": 7}\n```'
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        client._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await client.complete_structured(
            [{"role": "user", "content": "test"}], SimpleSchema
        )
        assert result.name == "Eve"

    async def test_raises_parse_error_on_invalid_json(self):
        client = _make_openai_client()
        mock_message = MagicMock()
        mock_message.content = "not json"
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        client._client.chat.completions.create = AsyncMock(return_value=mock_response)

        with pytest.raises(ParseError):
            await client.complete_structured(
                [{"role": "user", "content": "test"}], SimpleSchema
            )

    async def test_appends_instruction_to_last_user_message(self):
        client = _make_openai_client()
        mock_message = MagicMock()
        mock_message.content = '{"name": "test", "value": 1}'
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        client._client.chat.completions.create = AsyncMock(return_value=mock_response)

        messages = [{"role": "user", "content": "original prompt"}]
        await client.complete_structured(messages, SimpleSchema)

        call_kwargs = client._client.chat.completions.create.call_args.kwargs
        last_msg = call_kwargs["messages"][-1]
        assert "original prompt" in last_msg["content"]
        assert "JSON" in last_msg["content"]


class TestLLMClientFactory:
    def test_creates_anthropic_client(self):
        config = AgentLLMConfig(
            provider="anthropic",
            model="claude-opus-4-6",
            api_key_env="ANTHROPIC_API_KEY",
        )
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("anthropic.AsyncAnthropic"):
                client = LLMClientFactory.create(config)
        assert isinstance(client, AnthropicClient)

    def test_creates_openai_client(self):
        config = AgentLLMConfig(
            provider="openai",
            model="gpt-4o",
            api_key_env="OPENAI_API_KEY",
        )
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
            with patch("openai.AsyncOpenAI"):
                client = LLMClientFactory.create(config)
        assert isinstance(client, OpenAIClient)

    def test_raises_when_env_var_missing(self):
        config = AgentLLMConfig(
            provider="anthropic",
            model="claude-opus-4-6",
            api_key_env="MISSING_KEY_XYZ",
        )
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("MISSING_KEY_XYZ", None)
            with pytest.raises(EnvironmentError, match="MISSING_KEY_XYZ"):
                LLMClientFactory.create(config)

    def test_raises_on_unknown_provider(self):
        config = AgentLLMConfig(
            provider="anthropic",
            model="claude-opus-4-6",
            api_key_env="SOME_KEY",
        )
        config_dict = config.model_dump()
        config_dict["provider"] = "anthropic"

        with patch.dict("os.environ", {"SOME_KEY": "test-key"}):
            with patch("anthropic.AsyncAnthropic"):
                client = LLMClientFactory.create(config)
        assert client is not None

    def test_factory_passes_temperature_to_anthropic(self):
        config = AgentLLMConfig(
            provider="anthropic",
            model="claude-opus-4-6",
            temperature=0.7,
            api_key_env="ANTHROPIC_API_KEY",
        )
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "key"}):
            with patch("anthropic.AsyncAnthropic"):
                client = LLMClientFactory.create(config)
        assert client.temperature == 0.7

    def test_factory_passes_max_tokens_to_openai(self):
        config = AgentLLMConfig(
            provider="openai",
            model="gpt-4o",
            max_tokens=2048,
            api_key_env="OPENAI_API_KEY",
        )
        with patch.dict("os.environ", {"OPENAI_API_KEY": "key"}):
            with patch("openai.AsyncOpenAI"):
                client = LLMClientFactory.create(config)
        assert client.max_tokens == 2048

    def test_factory_auto_bumps_reasoning_model_max_tokens(self):
        config = AgentLLMConfig(
            provider="openai",
            model="gpt-5.4",
            max_tokens=8192,
            reasoning_effort=None,
            api_key_env="OPENAI_API_KEY",
        )
        with patch.dict("os.environ", {"OPENAI_API_KEY": "key"}):
            with patch("openai.AsyncOpenAI"):
                client = LLMClientFactory.create(config)
        assert isinstance(client, OpenAIClient)
        assert client.max_tokens == 32768
        assert client.reasoning_effort == "medium"

    def test_factory_respects_explicit_reasoning_overrides(self):
        config = AgentLLMConfig(
            provider="openai",
            model="gpt-5.4",
            max_tokens=65536,
            reasoning_effort="low",
            api_key_env="OPENAI_API_KEY",
        )
        with patch.dict("os.environ", {"OPENAI_API_KEY": "key"}):
            with patch("openai.AsyncOpenAI"):
                client = LLMClientFactory.create(config)
        assert client.max_tokens == 65536
        assert client.reasoning_effort == "low"

    def test_factory_does_not_bump_non_reasoning_openai_model(self):
        config = AgentLLMConfig(
            provider="openai",
            model="gpt-4o",
            max_tokens=4096,
            api_key_env="OPENAI_API_KEY",
        )
        with patch.dict("os.environ", {"OPENAI_API_KEY": "key"}):
            with patch("openai.AsyncOpenAI"):
                client = LLMClientFactory.create(config)
        assert client.max_tokens == 4096
        assert client.reasoning_effort is None

    def test_raises_value_error_for_unsupported_provider(self):
        config = AgentLLMConfig(
            provider="anthropic",
            model="claude-opus-4-6",
            api_key_env="MY_KEY",
        )
        with patch.dict("os.environ", {"MY_KEY": "test-key"}):
            with patch("anthropic.AsyncAnthropic"):
                with patch.object(config, "provider", "unknown_provider"):
                    with pytest.raises((ValueError, AttributeError)):
                        LLMClientFactory.create(config)


class TestProviderRegistry:
    """Gateway: ``openai_compatible`` is an OpenAI-wire provider for
    self-hosted / proxied gateways, distinguished only by base_url + model.
    The factory is registry-driven so adding such a provider stays neutral
    (no vendor names baked into source)."""

    def test_uses_openai_wire_helper(self):
        assert uses_openai_wire("openai") is True
        assert uses_openai_wire("openai_compatible") is True
        assert uses_openai_wire("anthropic") is False
        assert uses_openai_wire("nonsense") is False

    def test_config_accepts_openai_compatible_provider(self):
        cfg = AgentLLMConfig(
            provider="openai_compatible",
            model="internal-model",
            api_key_env="GW_KEY",
            api_base_url_env="GW_URL",
        )
        assert cfg.provider == "openai_compatible"

    def test_creates_openai_compatible_client_with_explicit_base_url(self):
        config = AgentLLMConfig(
            provider="openai_compatible",
            model="internal-model",
            api_key_env="GW_KEY",
            api_base_url_env="GW_URL",
        )
        with patch.dict(
            "os.environ",
            {"GW_KEY": "k", "GW_URL": "https://gw.internal/v1"},
        ):
            with patch("openai.AsyncOpenAI") as mock:
                client = LLMClientFactory.create(config)
        assert isinstance(client, OpenAIClient)
        assert mock.call_args.kwargs["base_url"] == "https://gw.internal/v1"

    def test_openai_compatible_does_not_auto_append_v1(self):
        # Internal gateways vary — the user supplies the complete URL; the
        # factory must not silently rewrite it the way it does for "openai".
        config = AgentLLMConfig(
            provider="openai_compatible",
            model="internal-model",
            api_key_env="GW_KEY",
            api_base_url_env="GW_URL",
        )
        with patch.dict(
            "os.environ",
            {"GW_KEY": "k", "GW_URL": "https://gw.internal/api"},
        ):
            with patch("openai.AsyncOpenAI") as mock:
                LLMClientFactory.create(config)
        assert mock.call_args.kwargs["base_url"] == "https://gw.internal/api"

    def test_openai_compatible_requires_base_url(self):
        config = AgentLLMConfig(
            provider="openai_compatible",
            model="internal-model",
            api_key_env="GW_KEY",
        )
        with patch.dict("os.environ", {"GW_KEY": "k"}, clear=False):
            os.environ.pop("OPENAI_BASE_URL", None)
            with patch("openai.AsyncOpenAI"):
                with pytest.raises(
                    EnvironmentError, match="requires an explicit base URL"
                ):
                    LLMClientFactory.create(config)

    def test_openai_compatible_bumps_reasoning_model_max_tokens(self):
        # The reasoning-model guard applies regardless of which OpenAI-wire
        # provider routes the request.
        config = AgentLLMConfig(
            provider="openai_compatible",
            model="gpt-5.4",
            max_tokens=8192,
            api_key_env="GW_KEY",
            api_base_url_env="GW_URL",
        )
        with patch.dict(
            "os.environ",
            {"GW_KEY": "k", "GW_URL": "https://gw.internal/v1"},
        ):
            with patch("openai.AsyncOpenAI"):
                client = LLMClientFactory.create(config)
        assert client.max_tokens == 32768
        assert client.reasoning_effort == "medium"

    def test_openai_appends_v1_to_custom_base_url(self):
        config = AgentLLMConfig(
            provider="openai",
            model="gpt-4o",
            api_key_env="OPENAI_API_KEY",
            api_base_url_env="MY_PROXY_URL",
        )
        with patch.dict(
            "os.environ",
            {"OPENAI_API_KEY": "k", "MY_PROXY_URL": "https://proxy.example"},
        ):
            with patch("openai.AsyncOpenAI") as mock:
                LLMClientFactory.create(config)
        assert mock.call_args.kwargs["base_url"] == "https://proxy.example/v1"


class TestFactoryOverrides:
    """The Setup connectivity probe builds clients with explicit creds via
    ``api_key_override`` / ``base_url_override`` so it never has to mutate
    ``os.environ`` just to test an un-saved key."""

    def test_api_key_override_skips_env_lookup(self):
        config = AgentLLMConfig(
            provider="anthropic",
            model="claude-opus-4-6",
            api_key_env="UNSET_KEY_SHOULD_NOT_BE_READ",
        )
        with patch("anthropic.AsyncAnthropic") as mock:
            client = LLMClientFactory.create(config, api_key_override="explicit")
        assert isinstance(client, AnthropicClient)
        assert mock.call_args.kwargs["api_key"] == "explicit"

    def test_base_url_override_still_appends_v1_for_openai(self):
        config = AgentLLMConfig(
            provider="openai",
            model="gpt-4o",
            api_key_env="OPENAI_API_KEY",
        )
        with patch("openai.AsyncOpenAI") as mock:
            LLMClientFactory.create(
                config, api_key_override="k", base_url_override="https://gw"
            )
        assert mock.call_args.kwargs["base_url"] == "https://gw/v1"

    def test_base_url_override_not_appended_for_openai_compatible(self):
        config = AgentLLMConfig(
            provider="openai_compatible",
            model="internal-model",
            api_key_env="GW_KEY",
        )
        with patch("openai.AsyncOpenAI") as mock:
            LLMClientFactory.create(
                config, api_key_override="k", base_url_override="https://gw/api"
            )
        assert mock.call_args.kwargs["base_url"] == "https://gw/api"


class TestAnthropicThinkingBlockParsing:
    """O-B1: Anthropic responses may start with a ThinkingBlock that has no
    ``.text`` attribute. The client must skip it and extract the first text
    block instead of raising AttributeError on retries."""

    async def test_skips_thinking_block_and_returns_text(self):
        client = _make_anthropic_client(cache_strategy=CacheStrategy.NONE)
        thinking_block = MagicMock(spec=["type", "thinking"])
        thinking_block.type = "thinking"
        thinking_block.thinking = "let me consider..."
        text_block = MagicMock(spec=["type", "text"])
        text_block.type = "text"
        text_block.text = "final answer"
        mock_response = MagicMock()
        mock_response.content = [thinking_block, text_block]
        client._client.messages.create = AsyncMock(return_value=mock_response)

        result = await client.complete([{"role": "user", "content": "x"}])
        assert result == "final answer"

    async def test_concatenates_multiple_text_blocks(self):
        client = _make_anthropic_client(cache_strategy=CacheStrategy.NONE)
        first = MagicMock(spec=["type", "text"])
        first.type = "text"
        first.text = "hello "
        second = MagicMock(spec=["type", "text"])
        second.type = "text"
        second.text = "world"
        mock_response = MagicMock()
        mock_response.content = [first, second]
        client._client.messages.create = AsyncMock(return_value=mock_response)

        result = await client.complete([{"role": "user", "content": "x"}])
        assert result == "hello world"

    async def test_raises_when_no_text_blocks(self):
        client = _make_anthropic_client(cache_strategy=CacheStrategy.NONE)
        thinking_only = MagicMock(spec=["type", "thinking"])
        thinking_only.type = "thinking"
        thinking_only.thinking = "only thought"
        mock_response = MagicMock()
        mock_response.content = [thinking_only]
        mock_response.stop_reason = "end_turn"
        client._client.messages.create = AsyncMock(return_value=mock_response)

        with pytest.raises(RuntimeError, match="no text blocks"):
            await client.complete([{"role": "user", "content": "x"}])


class TestSurrogateSanitization:
    """O-B2: lone UTF-16 surrogate code points (e.g. ``\\udc89``) must be
    stripped from outgoing payloads, otherwise the utf-8 HTTP encoder raises
    UnicodeEncodeError for the whole request and every retry."""

    async def test_anthropic_replaces_surrogates_in_messages(self):
        client = _make_anthropic_client(cache_strategy=CacheStrategy.NONE)
        text_block = MagicMock(spec=["type", "text"])
        text_block.type = "text"
        text_block.text = "ok"
        mock_response = MagicMock()
        mock_response.content = [text_block]
        client._client.messages.create = AsyncMock(return_value=mock_response)

        dirty = "binary payload \udc89 trailing"
        await client.complete([{"role": "user", "content": dirty}])

        call_kwargs = client._client.messages.create.call_args.kwargs
        sent = call_kwargs["messages"][0]["content"]
        assert "\udc89" not in sent
        assert "\ufffd" in sent

    async def test_openai_replaces_surrogates_in_system(self):
        client = _make_openai_client()
        mock_choice = MagicMock()
        mock_choice.message.content = "ok"
        mock_choice.finish_reason = "stop"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        client._client.chat.completions.create = AsyncMock(return_value=mock_response)

        await client.complete(
            [{"role": "user", "content": "hi"}],
            system="preamble \udc89 danger",
        )

        call_kwargs = client._client.chat.completions.create.call_args.kwargs
        system_msg = call_kwargs["messages"][0]
        assert system_msg["role"] == "system"
        assert "\udc89" not in system_msg["content"]
        assert "\ufffd" in system_msg["content"]

    async def test_clean_strings_pass_through_unchanged(self):
        client = _make_anthropic_client(cache_strategy=CacheStrategy.NONE)
        text_block = MagicMock(spec=["type", "text"])
        text_block.type = "text"
        text_block.text = "ok"
        mock_response = MagicMock()
        mock_response.content = [text_block]
        client._client.messages.create = AsyncMock(return_value=mock_response)

        clean = "hello 你好 🙂"
        await client.complete([{"role": "user", "content": clean}])
        call_kwargs = client._client.messages.create.call_args.kwargs
        assert call_kwargs["messages"][0]["content"] == clean


class TestCompleteMetaStopReason:
    """``complete_meta`` exposes provider stop_reason normalised across
    providers. The merge-output quality gate in ``parse_merge_result``
    depends on this signal — when it goes missing, truncated LLM output
    can reach ``apply_with_snapshot``.
    """

    async def test_anthropic_end_turn_normalises_to_stop(self):
        client = _make_anthropic_client(cache_strategy=CacheStrategy.NONE)
        mock_content = MagicMock()
        mock_content.text = "hello"
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_response.stop_reason = "end_turn"
        client._client.messages.create = AsyncMock(return_value=mock_response)

        result = await client.complete_meta([{"role": "user", "content": "hi"}])
        assert result.text == "hello"
        assert result.stop_reason == "stop"

    async def test_anthropic_max_tokens_surfaces_as_max_tokens(self):
        client = _make_anthropic_client(cache_strategy=CacheStrategy.NONE)
        mock_content = MagicMock()
        mock_content.text = "truncated..."
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_response.stop_reason = "max_tokens"
        client._client.messages.create = AsyncMock(return_value=mock_response)

        result = await client.complete_meta([{"role": "user", "content": "hi"}])
        assert result.text == "truncated..."
        assert result.stop_reason == "max_tokens"

    async def test_openai_length_normalises_to_max_tokens(self):
        client = _make_openai_client()
        mock_choice = MagicMock()
        mock_choice.message.content = "truncated..."
        mock_choice.finish_reason = "length"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        client._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await client.complete_meta([{"role": "user", "content": "hi"}])
        assert result.text == "truncated..."
        assert result.stop_reason == "max_tokens"

    async def test_openai_stop_passes_through(self):
        client = _make_openai_client()
        mock_choice = MagicMock()
        mock_choice.message.content = "ok"
        mock_choice.finish_reason = "stop"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        client._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await client.complete_meta([{"role": "user", "content": "hi"}])
        assert result.stop_reason == "stop"

    async def test_complete_returns_text_and_stays_backwards_compatible(self):
        # ``complete`` is the legacy ``-> str`` shape. It must keep
        # working after the ``complete_meta`` introduction so existing
        # callers (every non-merge agent) don't have to be touched.
        client = _make_anthropic_client(cache_strategy=CacheStrategy.NONE)
        mock_content = MagicMock()
        mock_content.text = "plain text"
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_response.stop_reason = "end_turn"
        client._client.messages.create = AsyncMock(return_value=mock_response)

        result = await client.complete([{"role": "user", "content": "hi"}])
        assert isinstance(result, str)
        assert result == "plain text"
