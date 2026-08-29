from __future__ import annotations
import asyncio
import json
from typing import AsyncGenerator
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.database import get_db
from apps.api.app.models import EventModel, TaskModel
from apps.api.app.redis_client import redis_manager
from packages.shared.events import AgentEvent, EventType

router = APIRouter(prefix="/api/tasks", tags=["Events"])


@router.get("/{task_id}/events")
async def stream_task_events(task_id: str, db: AsyncSession = Depends(get_db)):
    # Verify task exists
    res = await db.execute(select(TaskModel).where(TaskModel.id == task_id))
    task = res.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")

    async def event_generator() -> AsyncGenerator[str, None]:
        # 1. First yield all historical events from database
        hist_res = await db.execute(
            select(EventModel).where(EventModel.task_id == task_id).order_by(EventModel.timestamp.asc())
        )
        hist_events = hist_res.scalars().all()
        for ev in hist_events:
            data = {
                "id": ev.id,
                "task_id": ev.task_id,
                "run_id": ev.run_id,
                "type": ev.type,
                "phase": ev.phase,
                "timestamp": ev.timestamp.isoformat(),
                "title": ev.title,
                "summary": ev.summary,
                "status": ev.status,
                "tool_name": ev.tool_name,
                "tool_input": ev.tool_input,
                "tool_output": ev.tool_output,
                "duration_ms": ev.duration_ms,
            }
            yield f"event: {ev.type}\ndata: {json.dumps(data)}\n\n"

        # If task is already completed or failed, close stream
        if task.status in ("completed", "failed", "cancelled"):
            return

        # 2. Stream live events
        try:
            async for live_ev in redis_manager.subscribe_events(task_id):
                payload = live_ev.model_dump(mode="json")
                yield f"event: {live_ev.type.value}\ndata: {json.dumps(payload)}\n\n"
                if live_ev.type in (EventType.RUN_COMPLETED, EventType.RUN_FAILED):
                    break
        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
