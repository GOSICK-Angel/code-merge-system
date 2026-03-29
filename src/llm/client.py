import os
from abc import ABC, abstractmethod
import anthropic
import openai
from pydantic import BaseModel
from src.models.config import AgentLLMConfig


class ParseError(Exception):
    pass


class LLMClient(ABC):
    @abstractmethod
    async def complete(
        self, messages: list[dict], system: str | None = None, **kwargs
    ) -> str:
        pass

    @abstractmethod
    async def complete_structured(
        self, messages: list[dict], schema: type[BaseModel], system: str | None = None
    ) -> BaseModel:
        pass


class AnthropicClient(LLMClient):
    def __init__(
        self,
        model: str,
        api_key: str,
        temperature: float,
        max_tokens: int,
        max_retries: int,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def complete(
        self, messages: list[dict], system: str | None = None, **kwargs
    ) -> str:
        kwargs_merged = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": messages,
        }
        if system:
            kwargs_merged["system"] = system
        kwargs_merged.update(kwargs)

        response = await self._client.messages.create(**kwargs_merged)
        return response.content[0].text

    async def complete_structured(
        self, messages: list[dict], schema: type[BaseModel], system: str | None = None
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

        try:
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                lines = cleaned.splitlines()
                cleaned = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
            data = json.loads(cleaned)
            return schema.model_validate(data)
        except Exception as e:
            raise ParseError(
                f"Failed to parse structured response: {e}\nRaw: {raw[:500]}"
            ) from e


class OpenAIClient(LLMClient):
    def __init__(
        self,
        model: str,
        api_key: str,
        temperature: float,
        max_tokens: int,
        max_retries: int,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self._client = openai.AsyncOpenAI(api_key=api_key)

    async def complete(
        self, messages: list[dict], system: str | None = None, **kwargs
    ) -> str:
        all_messages: list[dict] = []
        if system:
            all_messages.append({"role": "system", "content": system})
        all_messages.extend(messages)

        response = await self._client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            messages=all_messages,
            **kwargs,
        )
        return response.choices[0].message.content or ""

    async def complete_structured(
        self, messages: list[dict], schema: type[BaseModel], system: str | None = None
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

        try:
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                lines = cleaned.splitlines()
                cleaned = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
            data = json.loads(cleaned)
            return schema.model_validate(data)
        except Exception as e:
            raise ParseError(
                f"Failed to parse structured response: {e}\nRaw: {raw[:500]}"
            ) from e


class LLMClientFactory:
    @staticmethod
    def create(config: AgentLLMConfig) -> LLMClient:
        api_key = os.environ.get(config.api_key_env)
        if not api_key:
            raise EnvironmentError(
                f"Required env var '{config.api_key_env}' is not set. "
                f"Needed for agent using {config.provider}/{config.model}."
            )
        if config.provider == "anthropic":
            return AnthropicClient(
                model=config.model,
                api_key=api_key,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
                max_retries=config.max_retries,
            )
        elif config.provider == "openai":
            return OpenAIClient(
                model=config.model,
                api_key=api_key,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
                max_retries=config.max_retries,
            )
        raise ValueError(f"Unknown provider: {config.provider}")
