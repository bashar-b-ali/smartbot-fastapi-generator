"""In-process rate limits for expensive LLM operations."""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from uuid import UUID

from app.core.config import settings
from app.core.exceptions import RateLimitError

_lock = asyncio.Lock()
_hits: dict[str, deque[float]] = defaultdict(deque)


async def enforce_llm_rate_limit(user_id: UUID | str) -> None:
    limit = max(1, int(settings.rate_limit_llm_per_minute))
    now = time.monotonic()
    window_start = now - 60.0
    key = str(user_id)
    async with _lock:
        bucket = _hits[key]
        while bucket and bucket[0] < window_start:
            bucket.popleft()
        if len(bucket) >= limit:
            retry_after = max(1, int(60 - (now - bucket[0])))
            raise RateLimitError(
                "Too many AI requests. Please wait a moment and try again.",
                details={"retry_after_seconds": retry_after, "limit_per_minute": limit},
            )
        bucket.append(now)
