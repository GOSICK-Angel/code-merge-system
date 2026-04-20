from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any
import anthropic
import openai
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel
from src.models.config import AgentLLMConfig
from src.llm.prompt_caching import CacheStrategy, apply_cache_markers


class ParseError(Exception):
    pass


class ModelOutputError(Exception):
    """LLM returned valid JSON but it doesn't match the expected schema."""

    def __init__(self, raw: str, schema_name: str, detail: str) -> None:
        super().__init__(f"Model output doesn't match {schema_name}: {detail}")
        self.raw = raw
        self.schema_name = schema_name


class LLMClient(ABC):
    model: str

    @abstractmethod
    async def complete(
        self, messages: list[dict[str, Any]], system: str | None = None, **kwargs: Any
    ) -> str:
        pass

    @abstractmethod
    async def complete_structured(
        self,
        messages: list[dict[str, Any]],
        schema: type[BaseModel],
        system: str | None = None,
    ) -> BaseModel:
        pass

    def update_api_key(self, new_key: str) -> None:
        """Replace the API key used by this client (C2 credential rotation)."""

    def with_model(self, model: str) -> _ModelOverrideContext:
        """Context manager to temporarily override the model (D1 smart routing)."""
        return _ModelOverrideContext(self, model)


class _ModelOverrideContext:
    """Temporarily swaps ``client.model`` and restores it on exit."""

    def __init__(self, client: LLMClient, model: str) -> None:
        self._client = client
        self._new_model = model
        self._old_model = ""

    def __enter__(self) -> LLMClient:
        self._old_model = self._client.model
        self._client.model = self._new_model
        return self._client

    def __exit__(self, *exc: Any) -> None:
        self._client.model = self._old_model


class AnthropicClient(LLMClient):
    def __init__(
        self,
        model: str,
        api_key: str,
        temperature: float,
        max_tokens: int,
        max_retries: int,
        base_url: str | None = None,
        cache_strategy: CacheStrategy = CacheStrategy.SYSTEM_AND_RECENT,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.cache_strategy = cache_strategy
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = anthropic.AsyncAnthropic(**kwargs)

    def update_api_key(self, new_key: str) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=new_key)

    async def complete(
        self, messages: list[dict[str, Any]], system: str | None = None, **kwargs: Any
    ) -> str:
        cached_messages, cached_system = apply_cache_markers(
            messages, system=system, strategy=self.cache_strategy
        )
        kwargs_merged: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": cached_messages,
        }
        if cached_system:
            kwargs_merged["system"] = cached_system
        kwargs_merged.update(kwargs)

        response = await self._client.messages.create(**kwargs_merged)
        return str(response.content[0].text)

    async def complete_structured(
        self,
        messages: list[dict[str, Any]],
        schema: type[BaseModel],
        system: str | None = None,
    ) -> BaseModel:
        import json

        schema_json = schema.model_json_schema()
        instruction = f"\n\nRespond with valid JSON matching this schema:\n{json.dumps(schema_json, indent=2)}"

        augmented = list(messages)
        if augmented and augmented[-1]["role"] == "user":
            augmented[-1] = {
                "role": "user",
                "content": augmented[-1]["content"] + instruction,
            }
        else:
            augmented.append({"role": "user", "content": instruction})

        raw = await self.complete(augmented, system=system)

        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            cleaned = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
        try:
            data = json.loads(cleaned)
        except Exception as e:
            raise ParseError(
                f"Failed to parse structured response: {e}\nRaw: {raw[:500]}"
            ) from e
        try:
            return schema.model_validate(data)
        except Exception as ve:
            raise ModelOutputError(raw, schema.__name__, str(ve)) from ve


class OpenAIClient(LLMClient):
    def __init__(
        self,
        model: str,
        api_key: str,
        temperature: float,
        max_tokens: int,
        max_retries: int,
        base_url: str | None = None,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = openai.AsyncOpenAI(**kwargs)

    def update_api_key(self, new_key: str) -> None:
        self._client = openai.AsyncOpenAI(api_key=new_key)

    async def complete(
        self, messages: list[dict[str, Any]], system: str | None = None, **kwargs: Any
    ) -> str:
        all_messages: list[ChatCompletionMessageParam] = []
        if system:
            all_messages.append({"role": "system", "content": system})
        for msg in messages:
            all_messages.append({"role": msg["role"], "content": msg["content"]})

        response = await self._client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            messages=all_messages,
            **kwargs,
        )
        content: str | None = response.choices[0].message.content
        if not content:
            finish_reason = response.choices[0].finish_reason
            raise RuntimeError(
                f"OpenAI returned empty content (finish_reason={finish_reason!r}, model={self.model!r})"
            )
        return content

    async def complete_structured(
        self,
        messages: list[dict[str, Any]],
        schema: type[BaseModel],
        system: str | None = None,
    ) -> BaseModel:
        import json

        schema_json = schema.model_json_schema()
        instruction = f"\n\nRespond with valid JSON matching this schema:\n{json.dumps(schema_json, indent=2)}"

        augmented = list(messages)
        if augmented and augmented[-1]["role"] == "user":
            augmented[-1] = {
                "role": "user",
                "content": augmented[-1]["content"] + instruction,
            }
        else:
            augmented.append({"role": "user", "content": instruction})

        raw = await self.complete(augmented, system=system)

        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            cleaned = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
        try:
            data = json.loads(cleaned)
        except Exception as e:
            raise ParseError(
                f"Failed to parse structured response: {e}\nRaw: {raw[:500]}"
            ) from e
        try:
            return schema.model_validate(data)
        except Exception as ve:
            raise ModelOutputError(raw, schema.__name__, str(ve)) from ve


class LLMClientFactory:
    @staticmethod
    def create(config: AgentLLMConfig) -> LLMClient:
        primary_env = config.api_key_env_list[0]
        api_key = os.environ.get(primary_env)
        if not api_key:
            raise EnvironmentError(
                f"Required env var '{primary_env}' is not set. "
                f"Needed for agent using {config.provider}/{config.model}."
            )
        base_url: str | None = None
        if config.api_base_url_env:
            base_url = os.environ.get(config.api_base_url_env) or None
        if not base_url:
            default_env = (
                "ANTHROPIC_BASE_URL"
                if config.provider == "anthropic"
                else "OPENAI_BASE_URL"
            )
            base_url = os.environ.get(default_env) or None
        if config.provider == "anthropic":
            return AnthropicClient(
                model=config.model,
                api_key=api_key,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
                max_retries=config.max_retries,
                base_url=base_url,
                cache_strategy=CacheStrategy(config.cache_strategy),
            )
        elif config.provider == "openai":
            if base_url and not base_url.rstrip("/").endswith("/v1"):
                base_url = base_url.rstrip("/") + "/v1"
            return OpenAIClient(
                model=config.model,
                api_key=api_key,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
                max_retries=config.max_retries,
                base_url=base_url,
            )
        raise ValueError(f"Unknown provider: {config.provider}")
