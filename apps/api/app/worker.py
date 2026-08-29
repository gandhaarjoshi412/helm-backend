from __future__ import annotations
import asyncio
from typing import Any, Dict, Optional
from sqlalchemy import select

from apps.api.app.config import settings
from apps.api.app.database import AsyncSessionLocal
from apps.api.app.models import ApprovalModel, ChangeSetModel, EventModel, TaskModel
from apps.api.app.redis_client import redis_manager
from packages.agent.models.factory import get_model_provider
from packages.agent.orchestrator.runner import HELMRunner
from packages.agent.policies.engine import PolicyEngine
from packages.agent.tools.registry import ToolRegistry
from packages.agent.workflows.state import WorkflowPhase
from packages.sandbox.factory import get_execution_provider
from packages.shared.events import AgentEvent, EventType
from packages.shared.logging import logger
from packages.shared.schemas import ApprovalStatus, TaskMode, TaskStatus, gen_id, utc_now


class HELMWorker:
    """
    Background worker that executes queued HELM agent tasks asynchronously.
    Persists events, status updates, diffs, and approval requests to the database.
    """

    def __init__(self):
        self.running = False
        self.sandbox = get_execution_provider(settings.SANDBOX_PROVIDER)
        self.model_provider = get_model_provider()
        self.policy_engine = PolicyEngine()
        self.tool_registry = ToolRegistry(self.policy_engine)

    async def start(self) -> None:
        self.running = True
        logger.info("HELM background worker started. Waiting for tasks...")
        while self.running:
            try:
                task_data = await redis_manager.dequeue_task(timeout=1)
                if task_data:
                    await self.process_task(task_data)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in worker loop: {e}", exc_info=True)
                await asyncio.sleep(1)

    def stop(self) -> None:
        self.running = False

    async def process_task(self, task_data: Dict[str, Any]) -> None:
        task_id = task_data["task_id"]
        run_id = task_data["run_id"]
        project_id = task_data["project_id"]
        prompt = task_data["prompt"]
        repo_path = task_data["repo_path"]
        mode = TaskMode(task_data.get("mode", "autonomous"))
        base_commit = task_data.get("base_commit")

        logger.info(f"Worker processing task {task_id} (run {run_id}): '{prompt}'")

        # Update task status in DB
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(TaskModel).where(TaskModel.id == task_id))
            task = res.scalar_one_or_none()
            if task:
                task.status = TaskStatus.INITIALIZING.value
                task.started_at = utc_now()
                await session.commit()

        # Define event persistence & publishing callback
        async def on_event(event: AgentEvent) -> None:
            # 1. Publish to Redis / memory bus for SSE
            await redis_manager.publish_event(task_id, event)

            # 2. Persist event to DB
            async with AsyncSessionLocal() as session:
                ev_db = EventModel(
                    id=event.id,
                    task_id=task_id,
                    run_id=run_id,
                    type=event.type.value,
                    phase=event.phase,
                    timestamp=event.timestamp,
                    title=event.title,
                    summary=event.summary,
                    tool_name=event.tool_name,
                    tool_input=event.tool_input,
                    tool_output=event.tool_output,
                    duration_ms=event.duration_ms,
                    status=event.status,
                    metadata_json=event.metadata,
                )
                session.add(ev_db)

                # Update task status/phase if applicable
                res = await session.execute(select(TaskModel).where(TaskModel.id == task_id))
                t = res.scalar_one_or_none()
                if t:
                    t.phase = event.phase
                    t.updated_at = utc_now()
                await session.commit()

        runner = HELMRunner(
            sandbox=self.sandbox,
            model_provider=self.model_provider,
            policy_engine=self.policy_engine,
            tool_registry=self.tool_registry,
            event_callback=on_event,
        )

        final_state = await runner.run(
            task_id=task_id,
            run_id=run_id,
            project_id=project_id,
            prompt=prompt,
            repo_path=repo_path,
            mode=mode,
            base_commit=base_commit,
        )

        # Persist final state and changes
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(TaskModel).where(TaskModel.id == task_id))
            task = res.scalar_one_or_none()
            if task:
                task.status = final_state.status.value
                task.phase = final_state.phase.value
                task.completed_at = utc_now()
                task.files_changed = len(final_state.files_modified)
                if final_state.last_test_result:
                    task.tests_passed = final_state.last_test_result.tests_passed
                    task.tests_failed = final_state.last_test_result.tests_failed
                if final_state.error_message:
                    task.error_message = final_state.error_message

            # Save ChangeSet
            if final_state.changeset:
                cs_db = ChangeSetModel(
                    id=gen_id("cs"),
                    task_id=task_id,
                    run_id=run_id,
                    raw_diff=final_state.changeset.raw_diff,
                    files_json=final_state.changeset.files_changed,
                    stats_json={
                        "additions": final_state.changeset.total_additions,
                        "deletions": final_state.changeset.total_deletions,
                    },
                    created_at=utc_now(),
                )
                session.add(cs_db)

            # Save Approval if waiting
            if final_state.phase == WorkflowPhase.WAITING_FOR_APPROVAL and final_state.approval_id:
                appr_db = ApprovalModel(
                    id=final_state.approval_id,
                    task_id=task_id,
                    run_id=run_id,
                    action_type="git_push",
                    description=f"Push changes ({len(final_state.files_modified)} files) to GitHub branch.",
                    status=ApprovalStatus.PENDING.value,
                    payload={"files_changed": final_state.files_modified},
                    requested_at=utc_now(),
                )
                session.add(appr_db)

            await session.commit()

        logger.info(f"Task {task_id} execution finished with status: {final_state.status.value}")


worker = HELMWorker()
