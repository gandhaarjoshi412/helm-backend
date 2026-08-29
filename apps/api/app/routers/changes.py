from __future__ import annotations
import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.database import get_db
from apps.api.app.models import ChangeSetModel, TaskModel
from packages.shared.schemas import ChangeSet, FileDiff

router = APIRouter(prefix="/api/tasks", tags=["Changes"])


@router.get("/{task_id}/changes", response_model=ChangeSet)
async def get_task_changes(task_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(ChangeSetModel).where(ChangeSetModel.task_id == task_id))
    cs = res.scalar_one_or_none()
    if not cs:
        # Check if task exists
        task_res = await db.execute(select(TaskModel).where(TaskModel.id == task_id))
        task = task_res.scalar_one_or_none()
        if not task:
            raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
        return ChangeSet(task_id=task_id, run_id=task.run_id)

    files_list = cs.files_json or []
    stats = cs.stats_json or {}
    raw_diff = cs.raw_diff or ""

    diffs = [
        FileDiff(
            path=f,
            status="modified",
            additions=stats.get(f, {}).get("additions", raw_diff.count("\n+")),
            deletions=stats.get(f, {}).get("deletions", raw_diff.count("\n-")),
            diff_content=raw_diff,
        )
        for f in files_list
    ]

    return ChangeSet(
        task_id=cs.task_id,
        run_id=cs.run_id,
        files_changed=files_list,
        total_additions=raw_diff.count("\n+"),
        total_deletions=raw_diff.count("\n-"),
        diffs=diffs,
        raw_diff=raw_diff,
    )


@router.get("/{task_id}/diff", response_class=PlainTextResponse)
async def get_raw_diff(task_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(ChangeSetModel).where(ChangeSetModel.task_id == task_id))
    cs = res.scalar_one_or_none()
    if not cs:
        return ""
    return cs.raw_diff or ""
