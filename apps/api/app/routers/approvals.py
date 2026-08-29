from __future__ import annotations
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.database import get_db
from apps.api.app.models import ApprovalModel, TaskModel
from apps.api.app.redis_client import redis_manager
from packages.shared.events import AgentEvent, EventType
from packages.shared.schemas import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalStatus,
    TaskStatus,
    utc_now,
)

router = APIRouter(prefix="/api/approvals", tags=["Approvals"])


@router.get("", response_model=List[ApprovalRequest])
async def list_approvals(
    status_filter: Optional[str] = None, db: AsyncSession = Depends(get_db)
):
    query = select(ApprovalModel).order_by(ApprovalModel.requested_at.desc())
    if status_filter:
        query = query.where(ApprovalModel.status == status_filter)
    res = await db.execute(query)
    items = res.scalars().all()
    return [
        ApprovalRequest(
            id=item.id,
            task_id=item.task_id,
            run_id=item.run_id,
            action_type=item.action_type,
            description=item.description,
            payload=item.payload or {},
            status=ApprovalStatus(item.status),
            requested_at=item.requested_at,
            resolved_at=item.resolved_at,
            resolved_by=item.resolved_by,
            rejection_reason=item.rejection_reason,
        )
        for item in items
    ]


@router.get("/{approval_id}", response_model=ApprovalRequest)
async def get_approval(approval_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(ApprovalModel).where(ApprovalModel.id == approval_id))
    item = res.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' not found.")
    return ApprovalRequest(
        id=item.id,
        task_id=item.task_id,
        run_id=item.run_id,
        action_type=item.action_type,
        description=item.description,
        payload=item.payload or {},
        status=ApprovalStatus(item.status),
        requested_at=item.requested_at,
        resolved_at=item.resolved_at,
        resolved_by=item.resolved_by,
        rejection_reason=item.rejection_reason,
    )


@router.post("/{approval_id}/approve", response_model=ApprovalRequest)
async def approve_request(
    approval_id: str,
    decision: Optional[ApprovalDecision] = None,
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(ApprovalModel).where(
            (ApprovalModel.id == approval_id) | (ApprovalModel.task_id == approval_id)
        )
    )
    item = res.scalar_one_or_none()

    if not item:
        # Check if task exists and create approval on the fly
        task_res = await db.execute(select(TaskModel).where(TaskModel.id == approval_id))
        task = task_res.scalar_one_or_none()
        if not task:
            raise HTTPException(status_code=404, detail=f"Approval or Task '{approval_id}' not found.")
        item = ApprovalModel(
            id=f"appr_{approval_id[-8:]}",
            task_id=task.id,
            run_id=task.run_id,
            action_type="git_push",
            description="User action approved.",
            status=ApprovalStatus.APPROVED.value,
            requested_at=utc_now(),
            resolved_at=utc_now(),
            resolved_by=decision.user_id if decision else "user",
        )
        db.add(item)
    else:
        item.status = ApprovalStatus.APPROVED.value
        item.resolved_at = utc_now()
        item.resolved_by = decision.user_id if decision else "user"

    # Update associated task status to completed
    task_res = await db.execute(select(TaskModel).where(TaskModel.id == item.task_id))
    task = task_res.scalar_one_or_none()
    if task:
        task.status = TaskStatus.COMPLETED.value
        task.phase = "completed"
        task.completed_at = utc_now()

    await db.commit()
    await db.refresh(item)

    # Publish resolution event
    await redis_manager.publish_event(
        item.task_id,
        AgentEvent(
            run_id=item.run_id,
            task_id=item.task_id,
            type=EventType.APPROVAL_RESOLVED,
            phase="completed",
            title="Action Approved",
            summary=f"Action '{item.action_type}' was approved. Process completed.",
            status="success",
        ),
    )

    return ApprovalRequest(
        id=item.id,
        task_id=item.task_id,
        run_id=item.run_id,
        action_type=item.action_type,
        description=item.description,
        payload=item.payload or {},
        status=ApprovalStatus(item.status),
        requested_at=item.requested_at,
        resolved_at=item.resolved_at,
        resolved_by=item.resolved_by,
    )


@router.post("/{approval_id}/reject", response_model=ApprovalRequest)
async def reject_request(
    approval_id: str,
    decision: Optional[ApprovalDecision] = None,
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(ApprovalModel).where(
            (ApprovalModel.id == approval_id) | (ApprovalModel.task_id == approval_id)
        )
    )
    item = res.scalar_one_or_none()

    if not item:
        task_res = await db.execute(select(TaskModel).where(TaskModel.id == approval_id))
        task = task_res.scalar_one_or_none()
        if not task:
            raise HTTPException(status_code=404, detail=f"Approval or Task '{approval_id}' not found.")
        item = ApprovalModel(
            id=f"appr_{approval_id[-8:]}",
            task_id=task.id,
            run_id=task.run_id,
            action_type="git_push",
            description="User action rejected.",
            status=ApprovalStatus.REJECTED.value,
            requested_at=utc_now(),
            resolved_at=utc_now(),
            resolved_by=decision.user_id if decision else "user",
            rejection_reason=decision.comment if decision else "Rejected by user",
        )
        db.add(item)
    else:
        item.status = ApprovalStatus.REJECTED.value
        item.resolved_at = utc_now()
        item.resolved_by = decision.user_id if decision else "user"
        item.rejection_reason = decision.comment if decision else "Rejected by user"

    task_res = await db.execute(select(TaskModel).where(TaskModel.id == item.task_id))
    task = task_res.scalar_one_or_none()
    if task:
        task.status = TaskStatus.CANCELLED.value
        task.phase = "rejected"
        task.completed_at = utc_now()

    await db.commit()
    await db.refresh(item)

    await redis_manager.publish_event(
        item.task_id,
        AgentEvent(
            run_id=item.run_id,
            task_id=item.task_id,
            type=EventType.APPROVAL_RESOLVED,
            phase="rejected",
            title="Action Rejected",
            summary=f"Action '{item.action_type}' was rejected by user.",
            status="warning",
        ),
    )

    return ApprovalRequest(
        id=item.id,
        task_id=item.task_id,
        run_id=item.run_id,
        action_type=item.action_type,
        description=item.description,
        payload=item.payload or {},
        status=ApprovalStatus(item.status),
        requested_at=item.requested_at,
        resolved_at=item.resolved_at,
        resolved_by=item.resolved_by,
        rejection_reason=item.rejection_reason,
    )
