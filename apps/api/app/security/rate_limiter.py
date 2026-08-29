from __future__ import annotations
import asyncio
import time
from collections import defaultdict
from typing import Dict, List, Optional
from fastapi import HTTPException, Request, status
from packages.shared.logging import logger


class SlidingWindowRateLimiter:
    """
    In-memory Sliding Window Rate Limiter.
    Tracks timestamps per client IP / key and enforces requests-per-minute (RPM) limits.
    """

    def __init__(self, rpm: int = 120, cleanup_interval_seconds: int = 300):
        self.rpm = rpm
        self.window_seconds = 60
        self._history: Dict[str, List[float]] = defaultdict(list)
        self._lock = asyncio.Lock()
        self._cleanup_interval = cleanup_interval_seconds
        self._last_cleanup = time.monotonic()

    def _get_client_id(self, request: Request) -> str:
        # Prefer X-Forwarded-For if behind a reverse proxy, fallback to client.host
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        client = getattr(request, "client", None)
        if client and hasattr(client, "host"):
            return client.host
        return "127.0.0.1"

    async def _cleanup_old_entries(self, now: float) -> None:
        cutoff = now - self.window_seconds
        to_delete = []
        for client_id, timestamps in self._history.items():
            self._history[client_id] = [t for t in timestamps if t > cutoff]
            if not self._history[client_id]:
                to_delete.append(client_id)
        for cid in to_delete:
            del self._history[cid]
        self._last_cleanup = now

    async def check(self, request: Request, custom_rpm: Optional[int] = None) -> None:
        limit = custom_rpm or self.rpm
        client_id = self._get_client_id(request)
        now = time.time()
        monotonic_now = time.monotonic()

        async with self._lock:
            # Periodic cleanup
            if monotonic_now - self._last_cleanup > self._cleanup_interval:
                await self._cleanup_old_entries(now)

            window_start = now - self.window_seconds
            timestamps = [t for t in self._history[client_id] if t > window_start]
            self._history[client_id] = timestamps

            if len(timestamps) >= limit:
                retry_after = int(self.window_seconds - (now - timestamps[0])) + 1
                logger.warning(
                    f"Rate limit exceeded for client {client_id} on {request.url.path} (Limit: {limit} RPM)"
                )
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Too many requests. Rate limit exceeded ({limit} requests/min). Please try again in {retry_after}s.",
                    headers={
                        "Retry-After": str(retry_after),
                        "X-RateLimit-Limit": str(limit),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(int(now + retry_after)),
                    },
                )

            self._history[client_id].append(now)


# Global rate limiter instances
general_limiter = SlidingWindowRateLimiter(rpm=120)
task_dispatch_limiter = SlidingWindowRateLimiter(rpm=15)
