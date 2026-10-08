"""WebSocket connection manager with Redis pub/sub fan-out.

Each connected socket subscribes to one or more channels (e.g. `user:{id}:projects`,
`user:{id}:chat`, `project:{id}:chat`). Server-side code calls `publish(channel, event)`
to broadcast an event to every connected socket subscribed to that channel — even
across multiple uvicorn workers, since the message goes through Redis.
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any

import redis.asyncio as redis
from fastapi import WebSocket

from app.core.config import settings
from app.core.logging import logger


class RealtimeManager:
    def __init__(self) -> None:
        self._redis: redis.Redis | None = None
        self._pubsub: redis.client.PubSub | None = None
        self._sockets: dict[str, set[WebSocket]] = defaultdict(set)
        self._listener: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        client = redis.from_url(settings.redis_url, decode_responses=True)
        pubsub = client.pubsub(ignore_subscribe_messages=True)
        try:
            await pubsub.psubscribe("rt.*")
        except (redis.RedisError, OSError) as exc:
            # In-process fan-out still works; cross-worker fan-out does not.
            logger.warning("realtime.redis.unavailable", error=str(exc))
            await pubsub.close()
            await client.close()
            return
        self._redis = client
        self._pubsub = pubsub
        self._listener = asyncio.create_task(self._listen())

    async def stop(self) -> None:
        if self._listener:
            self._listener.cancel()
        if self._pubsub:
            await self._pubsub.close()
        if self._redis:
            await self._redis.close()

    async def subscribe(self, channel: str, ws: WebSocket) -> None:
        async with self._lock:
            self._sockets[channel].add(ws)

    async def unsubscribe_all(self, ws: WebSocket) -> None:
        async with self._lock:
            for subscribers in self._sockets.values():
                subscribers.discard(ws)

    async def publish(self, channel: str, event: dict[str, Any]) -> None:
        if self._redis is None:
            await self._fanout(channel, event)
            return
        await self._redis.publish(f"rt.{channel}", json.dumps(event))

    async def _listen(self) -> None:
        assert self._pubsub is not None
        try:
            async for message in self._pubsub.listen():
                if message.get("type") not in ("message", "pmessage"):
                    continue
                channel = message["channel"].removeprefix("rt.")
                try:
                    payload = json.loads(message["data"])
                except (ValueError, TypeError):
                    continue
                await self._fanout(channel, payload)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.exception("realtime.listener.crashed", error=str(exc))

    async def _fanout(self, channel: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            subscribers = list(self._sockets.get(channel, ()))
        for ws in subscribers:
            try:
                await ws.send_json(payload)
            except Exception:
                # The disconnect handler will clean up. Just don't crash the listener.
                continue


manager = RealtimeManager()
