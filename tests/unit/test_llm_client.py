import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pydantic import BaseModel

from src.llm.client import (
    AnthropicClient,
    LLMClientFactory,
    ModelOutputError,
    OpenAIClient,
    ParseError,
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
