import asyncio
from collections import defaultdict
from src.models.message import AgentMessage, AgentType


class MessageBus:
    def __init__(self):
        self._messages: list[AgentMessage] = []
        self._subscribers: dict[AgentType, list] = defaultdict(list)
        self._queue: asyncio.Queue[AgentMessage] = asyncio.Queue()

    def publish(self, message: AgentMessage) -> None:
        self._messages.append(message)
        self._queue.put_nowait(message)

        for callback in self._subscribers.get(message.receiver, []):
            try:
                callback(message)
            except Exception:
                pass

        if message.receiver == AgentType.BROADCAST:
            for agent_type, callbacks in self._subscribers.items():
                if agent_type != message.sender:
                    for callback in callbacks:
                        try:
                            callback(message)
                        except Exception:
                            pass

    def subscribe(self, agent_type: AgentType, callback) -> None:
        self._subscribers[agent_type].append(callback)

    def get_messages(
        self,
        receiver: AgentType | None = None,
        unprocessed_only: bool = False,
    ) -> list[AgentMessage]:
        results = self._messages
        if receiver is not None:
            results = [
                m
                for m in results
                if m.receiver == receiver or m.receiver == AgentType.BROADCAST
            ]
        if unprocessed_only:
            results = [m for m in results if not m.is_processed]
        return results

    def mark_processed(self, message_id: str) -> None:
        for msg in self._messages:
            if msg.message_id == message_id:
                object.__setattr__(msg, "is_processed", True)
                break

    def clear(self) -> None:
        self._messages.clear()
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def wait_for_message(
        self, timeout: float | None = None
    ) -> AgentMessage | None:
        try:
            if timeout is not None:
                return await asyncio.wait_for(self._queue.get(), timeout=timeout)
            return await self._queue.get()
        except asyncio.TimeoutError:
            return None
