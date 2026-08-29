from __future__ import annotations
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from packages.shared.schemas import ChangeSet, ImplementationPlan, TaskMode, TaskStatus, TestResult


class WorkflowPhase(str, Enum):
    ASK = "ask"
    RECON = "recon"
    PLAN = "plan"
    EXECUTE = "execute"
    VERIFY = "verify"
    SELF_CORRECT = "self_correct"
    REVIEW = "review"
    SHIP = "ship"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentRunState(BaseModel):
    task_id: str
    run_id: str
    project_id: str
    prompt: str
    mode: TaskMode = TaskMode.AUTONOMOUS
    phase: WorkflowPhase = WorkflowPhase.ASK
    status: TaskStatus = TaskStatus.INITIALIZING
    env_id: Optional[str] = None
    repo_path: str = ""
    base_commit: Optional[str] = None
    dirty_files_detected: List[str] = Field(default_factory=list)
    plan: Optional[ImplementationPlan] = None
    iteration: int = 0
    max_iterations: int = 30
    files_modified: List[str] = Field(default_factory=list)
    files_created: List[str] = Field(default_factory=list)
    last_test_result: Optional[TestResult] = None
    review_comments: List[str] = Field(default_factory=list)
    changeset: Optional[ChangeSet] = None
    error_message: Optional[str] = None
    approval_id: Optional[str] = None
