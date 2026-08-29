from __future__ import annotations
import asyncio
import json
from typing import Any, AsyncGenerator, Dict, List, Optional
from packages.shared.events import AgentEvent
from packages.shared.logging import logger

try:
    import redis.asyncio as aioredis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False


class InMemoryEventBus:
    def __init__(self):
        self._subscribers: Dict[str, List[asyncio.Queue]] = {}
        self._task_queue: asyncio.Queue = asyncio.Queue()

    async def publish_event(self, task_id: str, event: AgentEvent) -> None:
        queues = self._subscribers.get(task_id, [])
        for q in queues:
            await q.put(event)

    def subscribe(self, task_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(task_id, []).append(q)
        return q

    def unsubscribe(self, task_id: str, q: asyncio.Queue) -> None:
        if task_id in self._subscribers and q in self._subscribers[task_id]:
            self._subscribers[task_id].remove(q)

    async def enqueue_task(self, task_data: Dict[str, Any]) -> None:
        await self._task_queue.put(task_data)

    async def dequeue_task(self, timeout: int = 1) -> Optional[Dict[str, Any]]:
        try:
            return await asyncio.wait_for(self._task_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None


class RedisManager:
    """Manages Redis connection, event pub/sub, and async task queues."""

    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        self.redis_url = redis_url
        self.redis_client = None
        self.memory_bus = InMemoryEventBus()
        self._use_fallback = False

    async def connect(self) -> None:
        if not REDIS_AVAILABLE:
            self._use_fallback = True
            logger.info("Using in-memory event bus and task queue.")
            return

        try:
            self.redis_client = aioredis.from_url(self.redis_url, encoding="utf-8", decode_responses=True)
            await self.redis_client.ping()
            logger.info("Connected to Redis successfully.")
        except Exception as e:
            logger.warning(f"Could not connect to Redis ({e}). Using in-memory fallback bus.")
            self._use_fallback = True
            self.redis_client = None

    async def publish_event(self, task_id: str, event: AgentEvent) -> None:
        await self.memory_bus.publish_event(task_id, event)
        if self.redis_client and not self._use_fallback:
            try:
                channel = f"helm:events:{task_id}"
                await self.redis_client.publish(channel, event.model_dump_json())
            except Exception as e:
                logger.warning(f"Redis publish failed: {e}")

    async def subscribe_events(self, task_id: str) -> AsyncGenerator[AgentEvent, None]:
        q = self.memory_bus.subscribe(task_id)
        try:
            while True:
                event = await q.get()
                yield event
        finally:
            self.memory_bus.unsubscribe(task_id, q)

    async def enqueue_task(self, task_data: Dict[str, Any]) -> None:
        await self.memory_bus.enqueue_task(task_data)
        if self.redis_client and not self._use_fallback:
            try:
                await self.redis_client.rpush("helm:task_queue", json.dumps(task_data))
            except Exception as e:
                logger.warning(f"Redis enqueue failed: {e}")

    async def dequeue_task(self, timeout: int = 1) -> Optional[Dict[str, Any]]:
        # Check memory queue first
        task = await self.memory_bus.dequeue_task(timeout=timeout)
        if task:
            return task

        if self.redis_client and not self._use_fallback:
            try:
                res = await self.redis_client.blpop("helm:task_queue", timeout=timeout)
                if res:
                    return json.loads(res[1])
            except Exception:
                pass
        return None

    async def close(self) -> None:
        if self.redis_client:
            await self.redis_client.close()


redis_manager = RedisManager()
