from __future__ import annotations

import os
import re
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


_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")

_OPENAI_REASONING_MODEL_PREFIXES = ("gpt-5", "o1", "o3", "o4")
_OPENAI_REASONING_MIN_MAX_TOKENS = 32768
_OPENAI_REASONING_DEFAULT_EFFORT = "medium"


def _is_openai_reasoning_model(model: str) -> bool:
    """Detect OpenAI reasoning models (gpt-5*, o1*, o3*, o4*).

    Reasoning models share `max_completion_tokens` between hidden reasoning
    and visible content; using the legacy `max_tokens` parameter caps total
    output and frequently leaves zero room for visible content, returning
    `finish_reason='stop'` with empty `message.content`.
    """
    return model.lower().startswith(_OPENAI_REASONING_MODEL_PREFIXES)


def _sanitize_surrogates(value: Any) -> Any:
    """Replace lone UTF-16 surrogate code points that break utf-8 encoding.

    The Anthropic/OpenAI HTTP clients serialize payloads as utf-8; strings that
    contain unpaired surrogates (often from binary files mis-decoded as text)
    raise UnicodeEncodeError for the whole request, defeating retries.
    """
    if isinstance(value, str):
        if not _SURROGATE_RE.search(value):
            return value
        return _SURROGATE_RE.sub("\ufffd", value)
    if isinstance(value, dict):
        return {k: _sanitize_surrogates(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_surrogates(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_surrogates(v) for v in value)
    return value


def _extract_anthropic_text(response: Any) -> str:
    """Extract visible text from an Anthropic Messages response.

    The ``content`` list may contain blocks of different types (``text``,
    ``thinking``, ``tool_use``, ...). Newer SDK / extended-thinking responses
    can place a ``ThinkingBlock`` first, which exposes ``.thinking`` instead of
    ``.text``. A naive ``response.content[0].text`` access raises
    AttributeError and aborts the whole retry chain, so iterate the list and
    collect text blocks while skipping thinking blocks.
    """
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        block_type = getattr(block, "type", None)
        if block_type == "thinking":
            continue
        text = getattr(block, "text", None)
        if isinstance(text, str) and text:
            parts.append(text)
    return "".join(parts)


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
        timeout: float = 60.0,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.cache_strategy = cache_strategy
        kwargs: dict[str, Any] = {"api_key": api_key, "timeout": timeout}
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
        cached_messages = _sanitize_surrogates(cached_messages)
        cached_system = _sanitize_surrogates(cached_system)
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
        text = _extract_anthropic_text(response)
        if not text:
            stop_reason = getattr(response, "stop_reason", None)
            raise RuntimeError(
                f"Anthropic returned no text blocks (stop_reason={stop_reason!r}, model={self.model!r})"
            )
        return text

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
        timeout: float = 60.0,
        reasoning_effort: str | None = None,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.reasoning_effort = reasoning_effort
        # SDK-level retry is disabled: BaseAgent._call_llm_with_retry owns retry
        # policy with category-aware cooldowns. Default SDK max_retries=2 used
        # to compound (3×3 attempts × ~120s) into ~18min cascades on Cloudflare
        # 524 origin-timeouts.
        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "timeout": timeout,
            "max_retries": 0,
        }
        if base_url:
            kwargs["base_url"] = base_url
        self._client = openai.AsyncOpenAI(**kwargs)

    def update_api_key(self, new_key: str) -> None:
        self._client = openai.AsyncOpenAI(api_key=new_key, max_retries=0)

    async def complete(
        self, messages: list[dict[str, Any]], system: str | None = None, **kwargs: Any
    ) -> str:
        sanitized_messages = _sanitize_surrogates(messages)
        sanitized_system = _sanitize_surrogates(system)
        all_messages: list[ChatCompletionMessageParam] = []
        if sanitized_system:
            all_messages.append({"role": "system", "content": sanitized_system})
        for msg in sanitized_messages:
            all_messages.append({"role": msg["role"], "content": msg["content"]})

        if _is_openai_reasoning_model(self.model):
            reasoning_kwargs: dict[str, Any] = {}
            if self.reasoning_effort is not None:
                reasoning_kwargs["reasoning_effort"] = self.reasoning_effort
            response = await self._client.chat.completions.create(
                model=self.model,
                max_completion_tokens=self.max_tokens,
                messages=all_messages,
                **reasoning_kwargs,
                **kwargs,
            )
        else:
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
    _WARNED_CACHE_OPENAI: bool = False

    @staticmethod
    def create(config: AgentLLMConfig) -> LLMClient:
        primary_env = config.api_key_env_list[0]
        # O-C3: prompt caching is an Anthropic-only feature. If the user
        # leaves ``cache_strategy`` at its default but routes to OpenAI they
        # silently get zero cache hits — warn once per process so ops can
        # either switch providers or migrate to a stable system-preamble
        # pattern for OpenAI.
        if (
            config.provider == "openai"
            and config.cache_strategy != "none"
            and not LLMClientFactory._WARNED_CACHE_OPENAI
        ):
            import logging as _logging

            _logging.getLogger("llm.factory").warning(
                "cache_strategy=%r has no effect on OpenAI (%s); Anthropic-only. "
                "Set cache_strategy='none' to silence this warning.",
                config.cache_strategy,
                config.model,
            )
            LLMClientFactory._WARNED_CACHE_OPENAI = True
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
                timeout=float(config.request_timeout_seconds),
            )
        elif config.provider == "openai":
            if base_url and not base_url.rstrip("/").endswith("/v1"):
                base_url = base_url.rstrip("/") + "/v1"
            effective_max_tokens = config.max_tokens
            effective_reasoning_effort = config.reasoning_effort
            if _is_openai_reasoning_model(config.model):
                if effective_reasoning_effort is None:
                    effective_reasoning_effort = _OPENAI_REASONING_DEFAULT_EFFORT
                if effective_max_tokens < _OPENAI_REASONING_MIN_MAX_TOKENS:
                    import logging as _logging

                    _logging.getLogger("llm.factory").warning(
                        "OpenAI reasoning model %r needs max_tokens >= %d "
                        "(shared between hidden reasoning and visible output); "
                        "auto-bumping from %d to %d. Set max_tokens explicitly "
                        "in config to silence this.",
                        config.model,
                        _OPENAI_REASONING_MIN_MAX_TOKENS,
                        effective_max_tokens,
                        _OPENAI_REASONING_MIN_MAX_TOKENS,
                    )
                    effective_max_tokens = _OPENAI_REASONING_MIN_MAX_TOKENS
            return OpenAIClient(
                model=config.model,
                api_key=api_key,
                temperature=config.temperature,
                max_tokens=effective_max_tokens,
                max_retries=config.max_retries,
                base_url=base_url,
                timeout=float(config.request_timeout_seconds),
                reasoning_effort=effective_reasoning_effort,
            )
        raise ValueError(f"Unknown provider: {config.provider}")
