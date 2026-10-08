"""WebSocket routes.

Clients authenticate by passing `?token=<access_jwt>` in the query string —
WebSocket protocols don't support `Authorization` headers from browsers cleanly.
"""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import jwt
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from app.core.security import decode_token
from app.db.session import SessionLocal
from app.realtime.manager import manager
from app.repositories.project import ProjectRepository

router = APIRouter(prefix="/ws", tags=["ws"])


async def _authenticate(token: str | None) -> UUID | None:
    if not token:
        return None
    try:
        payload = decode_token(token, expected_type="access")
        return UUID(payload.sub)
    except (jwt.PyJWTError, ValueError):
        return None


async def _owns_project(user_id: UUID, project_id: str) -> bool:
    try:
        parsed = UUID(str(project_id))
    except ValueError:
        return False
    async with SessionLocal() as db:
        return await ProjectRepository(db).get_for_user(parsed, user_id) is not None


async def _common_loop(ws: WebSocket, channels: list[str], *, user_id: UUID) -> None:
    for ch in channels:
        await manager.subscribe(ch, ws)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                data: dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError:
                continue
            msg_type = data.get("type")
            if msg_type == "ping":
                await ws.send_json({"type": "pong"})
            elif msg_type == "subscribe_project":
                pid = data.get("project_id")
                if pid and await _owns_project(user_id, str(pid)):
                    await manager.subscribe(f"project:{pid}:chat", ws)
    except WebSocketDisconnect:
        pass
    finally:
        await manager.unsubscribe_all(ws)


@router.websocket("/projects")
async def projects_ws(ws: WebSocket, token: str = Query(default="")):
    user_id = await _authenticate(token)
    if not user_id:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await ws.accept()
    await _common_loop(ws, [f"user:{user_id}:projects"], user_id=user_id)


@router.websocket("/chat")
async def chat_ws(ws: WebSocket, token: str = Query(default="")):
    user_id = await _authenticate(token)
    if not user_id:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await ws.accept()
    await _common_loop(ws, [f"user:{user_id}:chat"], user_id=user_id)
