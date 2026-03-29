import logging
from abc import ABC, abstractmethod
import asyncio
from pydantic import BaseModel
from src.models.config import AgentLLMConfig
from src.models.message import AgentType, AgentMessage
from src.models.state import MergeState
from src.llm.client import LLMClient, LLMClientFactory, ParseError


class BaseAgent(ABC):
    agent_type: AgentType

    def __init__(self, llm_config: AgentLLMConfig):
        self.llm_config = llm_config
        self.llm: LLMClient = LLMClientFactory.create(llm_config)
        self.logger = logging.getLogger(f"agent.{self.agent_type.value}")

    @abstractmethod
    async def run(self, state) -> AgentMessage:
        pass

    @abstractmethod
    def can_handle(self, state: MergeState) -> bool:
        pass

    async def _call_llm_with_retry(
        self,
        messages: list[dict],
        system: str | None = None,
        schema: type[BaseModel] | None = None,
        max_retries: int | None = None,
    ) -> str | BaseModel:
        retries = (
            max_retries if max_retries is not None else self.llm_config.max_retries
        )
        last_error: Exception | None = None

        for attempt in range(retries):
            try:
                if schema is not None:
                    return await self.llm.complete_structured(
                        messages, schema, system=system
                    )
                else:
                    return await self.llm.complete(messages, system=system)
            except ParseError as e:
                last_error = e
                self.logger.warning(
                    f"Parse error on attempt {attempt + 1}/{retries}: {e}"
                )
                if attempt + 1 < retries:
                    await asyncio.sleep(2**attempt)
            except Exception as e:
                last_error = e
                self.logger.warning(
                    f"LLM error on attempt {attempt + 1}/{retries}: {e}"
                )
                if attempt + 1 < retries:
                    await asyncio.sleep(2**attempt)

        raise RuntimeError(
            f"LLM call failed after {retries} attempts: {last_error}"
        ) from last_error
