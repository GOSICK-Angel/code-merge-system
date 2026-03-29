import asyncio
from typing import Callable, TypeVar, Awaitable

T = TypeVar("T")


class PhaseRunner:
    def __init__(self, batch_size: int = 10, max_concurrency: int = 5):
        self.batch_size = batch_size
        self.max_concurrency = max_concurrency

    async def run_sequential(
        self,
        items: list[T],
        handler: Callable[[T], Awaitable],
    ) -> list:
        results = []
        for item in items:
            result = await handler(item)
            results.append(result)
        return results

    async def run_parallel(
        self,
        items: list[T],
        handler: Callable[[T], Awaitable],
    ) -> list:
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def bounded_handler(item: T):
            async with semaphore:
                return await handler(item)

        tasks = [bounded_handler(item) for item in items]
        return await asyncio.gather(*tasks, return_exceptions=True)

    async def run_batched(
        self,
        items: list[T],
        handler: Callable[[T], Awaitable],
        on_batch_complete: Callable[[list], Awaitable] | None = None,
        parallel: bool = True,
    ) -> list:
        all_results = []

        for i in range(0, len(items), self.batch_size):
            batch = items[i:i + self.batch_size]

            if parallel:
                batch_results = await self.run_parallel(batch, handler)
            else:
                batch_results = await self.run_sequential(batch, handler)

            all_results.extend(batch_results)

            if on_batch_complete is not None:
                await on_batch_complete(batch_results)

        return all_results
