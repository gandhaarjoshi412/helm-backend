from __future__ import annotations
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.database import get_db
from apps.api.app.models import ProjectModel, TaskModel
from apps.api.app.redis_client import redis_manager
from apps.api.app.security.auth import UserContext, verify_api_key
from packages.shared.events import AgentEvent, EventType
from packages.shared.schemas import TaskCreate, TaskMode, TaskResponse, TaskStatus, gen_id, utc_now

router = APIRouter(prefix="/api/tasks", tags=["Tasks"])


@router.post("", response_model=TaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_task(
    task_in: TaskCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(verify_api_key),
):
    # Verify project exists and belongs to user
    result = await db.execute(select(ProjectModel).where(ProjectModel.id == task_in.project_id))
    proj = result.scalar_one_or_none()
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project '{task_in.project_id}' not found.")
    if not current_user.is_admin and proj.user_id and proj.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access to this project is forbidden.")

    task_id = gen_id("task")
    run_id = gen_id("run")

    db_task = TaskModel(
        id=task_id,
        user_id=current_user.user_id,
        project_id=task_in.project_id,
        run_id=run_id,
        prompt=task_in.prompt,
        mode=task_in.mode.value,
        status=TaskStatus.QUEUED.value,
        phase="initializing",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(db_task)
    await db.commit()
    await db.refresh(db_task)

    # Enqueue task for background worker execution
    task_payload = {
        "task_id": task_id,
        "run_id": run_id,
        "project_id": proj.id,
        "prompt": task_in.prompt,
        "repo_path": proj.repo_path,
        "mode": task_in.mode.value,
        "base_commit": task_in.base_commit,
    }
    await redis_manager.enqueue_task(task_payload)

    return TaskResponse(
        id=db_task.id,
        project_id=db_task.project_id,
        run_id=db_task.run_id,
        prompt=db_task.prompt,
        mode=TaskMode(db_task.mode),
        status=TaskStatus(db_task.status),
        phase=db_task.phase,
        created_at=db_task.created_at,
        updated_at=db_task.updated_at,
    )


@router.get("", response_model=List[TaskResponse])
async def list_tasks(
    project_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(verify_api_key),
):
    query = select(TaskModel).order_by(TaskModel.created_at.desc())
    if project_id:
        query = query.where(TaskModel.project_id == project_id)
    if not current_user.is_admin:
        query = query.where((TaskModel.user_id == current_user.user_id) | (TaskModel.user_id.is_(None)))
    result = await db.execute(query)
    tasks = result.scalars().all()
    return [
        TaskResponse(
            id=t.id,
            project_id=t.project_id,
            run_id=t.run_id,
            prompt=t.prompt,
            mode=TaskMode(t.mode),
            status=TaskStatus(t.status),
            phase=t.phase,
            created_at=t.created_at,
            updated_at=t.updated_at,
            started_at=t.started_at,
            completed_at=t.completed_at,
            files_changed=t.files_changed,
            tests_passed=t.tests_passed,
            tests_failed=t.tests_failed,
            error_message=t.error_message,
        )
        for t in tasks
    ]


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(verify_api_key),
):
    result = await db.execute(select(TaskModel).where(TaskModel.id == task_id))
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    if not current_user.is_admin and t.user_id and t.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access to this task is forbidden.")
    return TaskResponse(
        id=t.id,
        project_id=t.project_id,
        run_id=t.run_id,
        prompt=t.prompt,
        mode=TaskMode(t.mode),
        status=TaskStatus(t.status),
        phase=t.phase,
        created_at=t.created_at,
        updated_at=t.updated_at,
        started_at=t.started_at,
        completed_at=t.completed_at,
        files_changed=t.files_changed,
        tests_passed=t.tests_passed,
        tests_failed=t.tests_failed,
        error_message=t.error_message,
    )


@router.post("/{task_id}/cancel", response_model=TaskResponse)
async def cancel_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(verify_api_key),
):
    result = await db.execute(select(TaskModel).where(TaskModel.id == task_id))
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    if not current_user.is_admin and t.user_id and t.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access to this task is forbidden.")
    t.status = TaskStatus.CANCELLED.value
    t.phase = "cancelled"
    t.completed_at = utc_now()
    t.updated_at = utc_now()
    await db.commit()
    await db.refresh(t)

    # Publish abort event to SSE stream
    try:
        await redis_manager.publish_event(
            task_id,
            AgentEvent(
                run_id=t.run_id,
                task_id=t.id,
                type=EventType.RUN_FAILED,
                phase="cancelled",
                title="Task Aborted",
                summary="Task execution was stopped by user.",
                status="warning",
            ),
        )
    except Exception:
        pass

    return TaskResponse(
        id=t.id,
        project_id=t.project_id,
        run_id=t.run_id,
        prompt=t.prompt,
        mode=TaskMode(t.mode),
        status=TaskStatus(t.status),
        phase=t.phase,
        created_at=t.created_at,
        updated_at=t.updated_at,
        completed_at=t.completed_at,
    )

