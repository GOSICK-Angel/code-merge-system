from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

import anthropic
import httpx
import openai
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel

from src.llm.prompt_caching import CacheStrategy, apply_cache_markers
from src.models.config import AgentLLMConfig


@dataclass(frozen=True)
class LLMResponse:
    """LLM completion response carrying provider-side metadata.

    ``stop_reason`` is normalised across providers to one of:

    - ``"stop"``       — normal end-of-message (Anthropic ``end_turn``,
      OpenAI ``stop``)
    - ``"max_tokens"`` — output hit the ``max_tokens`` ceiling and was
      truncated mid-stream (Anthropic ``max_tokens``, OpenAI ``length``,
      OpenAI Responses ``incomplete``)
    - ``"tool_use"``   — provider asked to call a tool instead of
      finishing (rare for our merge-output path; surfaced for
      completeness so downstream can decide)
    - ``"content_filter"`` — provider refused / filtered
    - ``None``         — provider gave no signal (test mocks, legacy
      paths that didn't carry metadata)

    Callers MUST treat ``stop_reason == "max_tokens"`` as "this text is
    truncated; do not write it to disk" — see ``parse_merge_result``.
    """

    text: str
    stop_reason: str | None = None


def _build_httpx_timeout(total_seconds: float) -> httpx.Timeout:
    """Explicit per-phase timeout. ``read`` honors the full
    ``request_timeout_seconds`` from config — operators behind a slow
    proxy / on a slow Opus path need to be able to wait it out, and
    upstreams (Cloudflare 524 at 120s, etc.) will still surface their
    own error before our cap if applicable. Users who want to fail
    fast just set a small ``request_timeout_seconds``.
    Previously this was ``min(total, 90)``, which silently truncated
    user config — a 300s setting produced only a 90s read timeout.
    ``connect``/``write``/``pool`` stay short on purpose so a dead pool
    entry doesn't hang the request.
    """
    return httpx.Timeout(connect=10.0, read=float(total_seconds), write=30.0, pool=10.0)


class ParseError(Exception):
    pass


class ModelOutputError(Exception):
    """LLM returned valid JSON but it doesn't match the expected schema."""

    def __init__(self, raw: str, schema_name: str, detail: str) -> None:
        super().__init__(f"Model output doesn't match {schema_name}: {detail}")
        self.raw = raw
        self.schema_name = schema_name


def _append_schema_instruction(
    messages: list[dict[str, Any]], json_schema: dict[str, Any]
) -> list[dict[str, Any]]:
    """Append a JSON-Schema instruction to the last user turn (fallback path)."""
    import json

    instruction = (
        "\n\nRespond with ONLY a JSON object conforming to this schema "
        "(no markdown fences, no preamble — the first character must be `{`):\n"
        + json.dumps(json_schema, indent=2)
    )
    augmented = list(messages)
    if augmented and augmented[-1].get("role") == "user":
        last = augmented[-1]
        augmented[-1] = {"role": "user", "content": str(last["content"]) + instruction}
    else:
        augmented.append({"role": "user", "content": instruction})
    return augmented


def _to_openai_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Make a Pydantic ``model_json_schema()`` satisfy OpenAI strict mode.

    Strict ``json_schema`` requires every object to set
    ``additionalProperties: false`` and list ALL of its properties in
    ``required``. Pydantic with ``extra="forbid"`` already emits the former
    and (with no field defaults) the latter, but ``$defs`` sub-schemas and
    any future loosening are normalised here defensively. Recurses through
    ``$defs`` / ``properties`` / array ``items``.
    """

    def _walk(node: Any) -> Any:
        if isinstance(node, dict):
            out = {k: _walk(v) for k, v in node.items()}
            if out.get("type") == "object" and "properties" in out:
                out["additionalProperties"] = False
                out["required"] = list(out["properties"].keys())
            return out
        if isinstance(node, list):
            return [_walk(v) for v in node]
        return node

    return _walk(schema)  # type: ignore[no-any-return]


class _StructuredUnsupported(Exception):
    """Provider accepted the request but did not return a structured payload."""


class LLMClient(ABC):
    model: str

    @abstractmethod
    async def complete(
        self, messages: list[dict[str, Any]], system: str | None = None, **kwargs: Any
    ) -> str:
        pass

    async def complete_meta(
        self, messages: list[dict[str, Any]], system: str | None = None, **kwargs: Any
    ) -> LLMResponse:
        """Return text + provider metadata (stop_reason, ...).

        Concrete subclasses override this with the real implementation
        that captures ``stop_reason`` / ``finish_reason`` from the
        provider response. The default here calls ``complete()`` so
        existing test mocks that only implement ``complete`` keep
        working — they just won't carry a stop_reason, and callers that
        depend on truncation detection will see ``None`` (the most
        cautious default: "no signal").
        """
        text = await self.complete(messages, system=system, **kwargs)
        return LLMResponse(text=text, stop_reason=None)

    @abstractmethod
    async def complete_structured(
        self,
        messages: list[dict[str, Any]],
        schema: type[BaseModel],
        system: str | None = None,
    ) -> BaseModel:
        pass

    async def structured_json(
        self,
        messages: list[dict[str, Any]],
        *,
        json_schema: dict[str, Any],
        schema_name: str,
        system: str | None = None,
    ) -> str:
        """Return a well-formed JSON *string* conforming to ``json_schema``.

        P2-1 reliability layer: concrete providers override this with
        native Structured Outputs (OpenAI ``response_format=json_schema`` /
        Anthropic forced tool-use) so the model cannot wrap its answer in
        markdown or a prose preamble. The returned string flows into the
        existing ``response_parser`` functions unchanged — those keep all
        grounding / sanitisation / deterministic-verdict logic.

        This default implementation is the graceful-degradation path:
        append the schema as a plain instruction and call ``complete``.
        Providers override and fall back to ``super().structured_json``
        when the gateway rejects native Structured Outputs (a self-hosted
        or proxied OpenAI-compatible endpoint may not support it).
        """
        augmented = _append_schema_instruction(messages, json_schema)
        return await self.complete(augmented, system=system)

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


def _extract_responses_text(response: Any) -> str:
    """Extract visible text from an OpenAI Responses API response.

    Prefers the SDK's derived ``output_text`` attribute; falls back to
    iterating ``output[].content[]`` for blocks with type ``output_text``
    when proxies don't populate the convenience field.
    """
    text = getattr(response, "output_text", None)
    if isinstance(text, str) and text:
        return text
    parts: list[str] = []
    for item in getattr(response, "output", []) or []:
        for block in getattr(item, "content", []) or []:
            if getattr(block, "type", None) == "output_text":
                t = getattr(block, "text", None)
                if isinstance(t, str) and t:
                    parts.append(t)
    return "".join(parts)


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
        thinking_budget_tokens: int | None = None,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.cache_strategy = cache_strategy
        self.thinking_budget_tokens = thinking_budget_tokens
        self._timeout = _build_httpx_timeout(timeout)
        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "timeout": self._timeout,
        }
        if base_url:
            kwargs["base_url"] = base_url
        self._client = anthropic.AsyncAnthropic(**kwargs)

    def update_api_key(self, new_key: str) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=new_key, timeout=self._timeout)

    async def complete(
        self, messages: list[dict[str, Any]], system: str | None = None, **kwargs: Any
    ) -> str:
        result = await self.complete_meta(messages, system=system, **kwargs)
        return result.text

    async def complete_meta(
        self, messages: list[dict[str, Any]], system: str | None = None, **kwargs: Any
    ) -> LLMResponse:
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
        if self.thinking_budget_tokens is not None:
            # Extended thinking requires temperature=1.0 (Anthropic API
            # constraint); the configured temperature is overridden here rather
            # than at config time so a single agent can be toggled freely.
            kwargs_merged["thinking"] = {
                "type": "enabled",
                "budget_tokens": self.thinking_budget_tokens,
            }
            kwargs_merged["temperature"] = 1.0
        kwargs_merged.update(kwargs)

        response = await self._client.messages.create(**kwargs_merged)
        text = _extract_anthropic_text(response)
        raw_stop = getattr(response, "stop_reason", None)
        if not text:
            raise RuntimeError(
                f"Anthropic returned no text blocks (stop_reason={raw_stop!r}, model={self.model!r})"
            )
        # Anthropic emits ``end_turn`` for normal completion and
        # ``max_tokens`` when truncated. Normalise the former to ``stop``
        # to align with the OpenAI vocabulary used by ``LLMResponse``.
        stop_reason = "stop" if raw_stop == "end_turn" else raw_stop
        return LLMResponse(text=text, stop_reason=stop_reason)

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

    async def structured_json(
        self,
        messages: list[dict[str, Any]],
        *,
        json_schema: dict[str, Any],
        schema_name: str,
        system: str | None = None,
    ) -> str:
        # Forced tool-use is incompatible with extended thinking; if a
        # thinking budget is configured, take the prompt-injection fallback
        # rather than dropping the thinking the operator opted into.
        if self.thinking_budget_tokens is not None:
            return await super().structured_json(
                messages,
                json_schema=json_schema,
                schema_name=schema_name,
                system=system,
            )
        try:
            return await self._structured_tool_use(
                messages, json_schema, schema_name, system
            )
        except _StructuredUnsupported:
            pass
        except anthropic.APIStatusError as e:
            # Some Anthropic-compatible proxies force interleaved thinking on,
            # which rejects forced tool_choice (observed as a 400 BadRequest or
            # a 503 "Thinking mode does not support this tool_choice"). Degrade
            # to prompt-injection in that case. Genuine transient/auth errors
            # (rate limit, 401, 5xx without this signal) re-raise so the outer
            # _call_llm_with_retry loop owns the retry policy.
            msg = str(getattr(e, "message", "") or e).lower()
            recoverable = (
                isinstance(e, anthropic.BadRequestError)
                or "tool_choice" in msg
                or "thinking" in msg
            )
            if not recoverable:
                raise
        return await super().structured_json(
            messages,
            json_schema=json_schema,
            schema_name=schema_name,
            system=system,
        )

    async def _structured_tool_use(
        self,
        messages: list[dict[str, Any]],
        json_schema: dict[str, Any],
        schema_name: str,
        system: str | None,
    ) -> str:
        import json

        cached_messages, cached_system = apply_cache_markers(
            messages, system=system, strategy=self.cache_strategy
        )
        cached_messages = _sanitize_surrogates(cached_messages)
        cached_system = _sanitize_surrogates(cached_system)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": cached_messages,
            "tools": [
                {
                    "name": schema_name,
                    "description": (
                        f"Emit the {schema_name} result as structured data "
                        "matching the input schema."
                    ),
                    "input_schema": json_schema,
                }
            ],
            "tool_choice": {"type": "tool", "name": schema_name},
        }
        if cached_system:
            kwargs["system"] = cached_system

        response = await self._client.messages.create(**kwargs)
        for block in response.content:
            if getattr(block, "type", None) == "tool_use":
                return json.dumps(getattr(block, "input", {}))
        raise _StructuredUnsupported(
            f"Anthropic returned no tool_use block (model={self.model!r})"
        )


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
        api_style: str = "chat",
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.reasoning_effort = reasoning_effort
        self.api_style = api_style
        # SDK-level retry is disabled: BaseAgent._call_llm_with_retry owns retry
        # policy with category-aware cooldowns. Default SDK max_retries=2 used
        # to compound (3×3 attempts × ~120s) into ~18min cascades on Cloudflare
        # 524 origin-timeouts.
        self._timeout = _build_httpx_timeout(timeout)
        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "timeout": self._timeout,
            "max_retries": 0,
        }
        if base_url:
            kwargs["base_url"] = base_url
        self._client = openai.AsyncOpenAI(**kwargs)

    def update_api_key(self, new_key: str) -> None:
        self._client = openai.AsyncOpenAI(
            api_key=new_key, max_retries=0, timeout=self._timeout
        )

    async def complete(
        self, messages: list[dict[str, Any]], system: str | None = None, **kwargs: Any
    ) -> str:
        result = await self.complete_meta(messages, system=system, **kwargs)
        return result.text

    async def complete_meta(
        self, messages: list[dict[str, Any]], system: str | None = None, **kwargs: Any
    ) -> LLMResponse:
        sanitized_messages = _sanitize_surrogates(messages)
        sanitized_system = _sanitize_surrogates(system)

        if self.api_style == "responses":
            return await self._complete_responses(
                sanitized_messages, sanitized_system, **kwargs
            )

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
        choice = response.choices[0]
        content: str | None = choice.message.content
        finish_reason: str | None = choice.finish_reason
        if not content:
            raise RuntimeError(
                f"OpenAI returned empty content (finish_reason={finish_reason!r}, model={self.model!r})"
            )
        # Normalise: OpenAI ``length`` → our ``max_tokens``.
        stop_reason = "max_tokens" if finish_reason == "length" else finish_reason
        return LLMResponse(text=content, stop_reason=stop_reason)

    async def _complete_responses(
        self,
        sanitized_messages: list[dict[str, Any]],
        sanitized_system: str | None,
        **kwargs: Any,
    ) -> LLMResponse:
        if (
            len(sanitized_messages) == 1
            and sanitized_messages[0].get("role") == "user"
            and isinstance(sanitized_messages[0].get("content"), str)
        ):
            input_payload: Any = sanitized_messages[0]["content"]
        else:
            input_payload = [
                {"role": msg["role"], "content": msg["content"]}
                for msg in sanitized_messages
            ]

        extra: dict[str, Any] = {}
        if self.reasoning_effort is not None:
            extra["reasoning"] = {"effort": self.reasoning_effort}

        response_format = kwargs.pop("response_format", None)
        if isinstance(response_format, dict):
            rf_type = response_format.get("type")
            if rf_type == "json_object":
                extra["text"] = {"format": {"type": "json_object"}}
            elif rf_type == "json_schema":
                js = response_format.get("json_schema", {})
                extra["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": js.get("name", "response"),
                        "schema": js.get("schema", {}),
                        "strict": bool(js.get("strict", True)),
                    }
                }

        if sanitized_system:
            extra["instructions"] = sanitized_system

        response = await self._client.responses.create(
            model=self.model,
            input=input_payload,
            max_output_tokens=self.max_tokens,
            **extra,
            **kwargs,
        )
        text = _extract_responses_text(response)
        status = getattr(response, "status", None)
        if not text:
            raise RuntimeError(
                f"OpenAI Responses API returned empty content "
                f"(status={status!r}, model={self.model!r})"
            )
        # The Responses API surfaces truncation via ``status="incomplete"``
        # plus ``incomplete_details.reason="max_output_tokens"`` (the
        # exact strings vary by SDK version — guard with getattr).
        stop_reason: str | None
        if status == "incomplete":
            details = getattr(response, "incomplete_details", None)
            reason = getattr(details, "reason", None) if details else None
            stop_reason = (
                "max_tokens"
                if reason in {"max_output_tokens", "max_tokens"}
                else (reason or "incomplete")
            )
        elif status == "completed":
            stop_reason = "stop"
        else:
            stop_reason = status
        return LLMResponse(text=text, stop_reason=stop_reason)

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

    async def structured_json(
        self,
        messages: list[dict[str, Any]],
        *,
        json_schema: dict[str, Any],
        schema_name: str,
        system: str | None = None,
    ) -> str:
        # Reasoning models on the chat wire reject ``response_format`` for
        # some gateways; the tiered fallback below absorbs that.
        strict_schema = _to_openai_strict_schema(json_schema)
        rf = {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "schema": strict_schema,
                "strict": True,
            },
        }
        try:
            return await self.complete(messages, system=system, response_format=rf)
        except (
            openai.BadRequestError,
            openai.UnprocessableEntityError,
            openai.NotFoundError,
            TypeError,
        ):
            # json_schema unsupported on this gateway — try plain JSON mode,
            # which most OpenAI-compatible endpoints still honor.
            try:
                return await self.complete(
                    _append_schema_instruction(messages, json_schema),
                    system=system,
                    response_format={"type": "json_object"},
                )
            except (
                openai.BadRequestError,
                openai.UnprocessableEntityError,
                openai.NotFoundError,
                TypeError,
            ):
                return await super().structured_json(
                    messages,
                    json_schema=json_schema,
                    schema_name=schema_name,
                    system=system,
                )


@dataclass(frozen=True)
class ProviderSpec:
    """Registry entry describing how to build a client for a provider.

    ``wire`` selects the request/response format (and thus the concrete
    ``LLMClient`` subclass). ``openai_compatible`` reuses the OpenAI wire
    but targets self-hosted / proxied OpenAI-API-compatible gateways that
    are distinguished only by ``base_url`` + ``model`` — so it requires an
    explicit base URL and does not assume the public OpenAI ``/v1`` suffix
    or the ``OPENAI_BASE_URL`` default env. This keeps gateway endpoints in
    config (per the target-repo-agnostic rule) rather than baked into source.
    """

    wire: Literal["anthropic", "openai"]
    default_base_url_env: str | None
    base_url_required: bool
    append_v1: bool
    supports_prompt_cache: bool


_PROVIDER_REGISTRY: dict[str, ProviderSpec] = {
    "anthropic": ProviderSpec(
        wire="anthropic",
        default_base_url_env="ANTHROPIC_BASE_URL",
        base_url_required=False,
        append_v1=False,
        supports_prompt_cache=True,
    ),
    "openai": ProviderSpec(
        wire="openai",
        default_base_url_env="OPENAI_BASE_URL",
        base_url_required=False,
        append_v1=True,
        supports_prompt_cache=False,
    ),
    "openai_compatible": ProviderSpec(
        wire="openai",
        default_base_url_env=None,
        base_url_required=True,
        append_v1=False,
        supports_prompt_cache=False,
    ),
}


def uses_openai_wire(provider: str) -> bool:
    """True when the provider speaks the OpenAI chat/responses wire format.

    Callers that special-case OpenAI request shaping (e.g. ``response_format``
    JSON mode) must treat every OpenAI-wire provider alike, not just the
    literal ``"openai"`` — otherwise ``openai_compatible`` gateways silently
    lose JSON mode.
    """
    spec = _PROVIDER_REGISTRY.get(provider)
    return spec is not None and spec.wire == "openai"


class LLMClientFactory:
    _WARNED_NO_CACHE: bool = False

    @staticmethod
    def create(
        config: AgentLLMConfig,
        *,
        api_key_override: str | None = None,
        base_url_override: str | None = None,
    ) -> LLMClient:
        """Build a client for ``config``.

        ``api_key_override`` / ``base_url_override`` let callers supply
        credentials directly instead of resolving them from the process
        environment — used by the Setup connectivity probe so testing an
        un-saved key never has to mutate ``os.environ``. When omitted the
        normal env-resolution chain applies.
        """
        spec = _PROVIDER_REGISTRY.get(config.provider)
        if spec is None:
            raise ValueError(f"Unknown provider: {config.provider}")

        # O-C3: prompt caching is an Anthropic-only feature. If the user
        # leaves ``cache_strategy`` at its default but routes to a provider
        # that can't cache they silently get zero cache hits — warn once per
        # process so ops can either switch providers or set
        # ``cache_strategy='none'``.
        if (
            not spec.supports_prompt_cache
            and config.cache_strategy != "none"
            and not LLMClientFactory._WARNED_NO_CACHE
        ):
            import logging as _logging

            _logging.getLogger("llm.factory").warning(
                "cache_strategy=%r has no effect on provider %r (%s); "
                "prompt caching is Anthropic-only. Set cache_strategy='none' "
                "to silence this warning.",
                config.cache_strategy,
                config.provider,
                config.model,
            )
            LLMClientFactory._WARNED_NO_CACHE = True

        api_key: str
        if api_key_override is not None:
            api_key = api_key_override
        else:
            primary_env = config.api_key_env_list[0]
            resolved = os.environ.get(primary_env)
            if not resolved:
                raise EnvironmentError(
                    f"Required env var '{primary_env}' is not set. "
                    f"Needed for agent using {config.provider}/{config.model}."
                )
            api_key = resolved

        base_url = LLMClientFactory._resolve_base_url(
            config, spec, explicit=base_url_override
        )

        if spec.wire == "anthropic":
            return AnthropicClient(
                model=config.model,
                api_key=api_key,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
                max_retries=config.max_retries,
                base_url=base_url,
                cache_strategy=CacheStrategy(config.cache_strategy),
                timeout=float(config.request_timeout_seconds),
                thinking_budget_tokens=config.thinking_budget_tokens,
            )
        return LLMClientFactory._build_openai(config, api_key, base_url)

    @staticmethod
    def _resolve_base_url(
        config: AgentLLMConfig, spec: ProviderSpec, explicit: str | None = None
    ) -> str | None:
        base_url: str | None = explicit or None
        if not base_url and config.api_base_url_env:
            base_url = os.environ.get(config.api_base_url_env) or None
        if not base_url and spec.default_base_url_env:
            base_url = os.environ.get(spec.default_base_url_env) or None
        if not base_url and spec.base_url_required:
            raise EnvironmentError(
                f"Provider '{config.provider}' requires an explicit base URL. "
                f"Point 'api_base_url_env' at an env var holding the gateway "
                f"URL (e.g. api_base_url_env: MERGE_GATEWAY_URL) and set it."
            )
        if base_url and spec.append_v1 and not base_url.rstrip("/").endswith("/v1"):
            base_url = base_url.rstrip("/") + "/v1"
        return base_url

    @staticmethod
    def _build_openai(
        config: AgentLLMConfig, api_key: str, base_url: str | None
    ) -> OpenAIClient:
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
            api_style=config.api_style,
        )
