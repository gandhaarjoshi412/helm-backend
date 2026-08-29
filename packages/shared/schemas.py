from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import uuid


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def gen_id(prefix: str = "") -> str:
    val = uuid.uuid4().hex[:12]
    return f"{prefix}_{val}" if prefix else val


class TaskMode(str, Enum):
    ASSIST = "assist"
    GUIDED = "guided"
    AUTONOMOUS = "autonomous"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    INITIALIZING = "initializing"
    RECON = "recon"
    PLANNING = "planning"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    REVIEWING = "reviewing"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProjectBase(BaseModel):
    name: str
    repo_url: Optional[str] = None
    repo_path: Optional[str] = None
    default_branch: str = "main"
    description: Optional[str] = None


class ProjectCreate(ProjectBase):
    pass


class ProjectResponse(ProjectBase):
    id: str
    status: str = "ready"
    created_at: datetime = Field(default_factory=utc_now)
    last_indexed_at: Optional[datetime] = None


class TaskCreate(BaseModel):
    project_id: str
    prompt: str
    mode: TaskMode = TaskMode.AUTONOMOUS
    base_commit: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TaskResponse(BaseModel):
    id: str
    project_id: str
    run_id: str
    prompt: str
    mode: TaskMode
    status: TaskStatus
    phase: str = "initializing"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    files_changed: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    error_message: Optional[str] = None


class FileDiff(BaseModel):
    path: str
    status: str  # added, modified, deleted
    additions: int = 0
    deletions: int = 0
    diff_content: str = ""


class ChangeSet(BaseModel):
    task_id: str
    run_id: str
    files_changed: List[str] = Field(default_factory=list)
    files_added: List[str] = Field(default_factory=list)
    files_deleted: List[str] = Field(default_factory=list)
    total_additions: int = 0
    total_deletions: int = 0
    diffs: List[FileDiff] = Field(default_factory=list)
    raw_diff: str = ""


class TestResult(BaseModel):
    command: str
    passed: bool
    exit_code: int
    output: str
    duration_ms: int
    tests_run: int = 0
    tests_passed: int = 0
    tests_failed: int = 0


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalRequest(BaseModel):
    id: str = Field(default_factory=lambda: gen_id("appr"))
    task_id: str
    run_id: str
    action_type: str  # e.g., "git_push", "create_pr", "destructive_command"
    description: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_at: datetime = Field(default_factory=utc_now)
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[str] = None
    rejection_reason: Optional[str] = None


class ApprovalDecision(BaseModel):
    approved: bool = True
    user_id: Optional[str] = "user"
    comment: Optional[str] = None


class ImplementationPlan(BaseModel):
    goal: str
    architecture_summary: str = ""
    files_to_modify: List[str] = Field(default_factory=list)
    files_to_add: List[str] = Field(default_factory=list)
    files_to_delete: List[str] = Field(default_factory=list)
    tests_to_run: List[str] = Field(default_factory=list)
    verification_steps: List[str] = Field(default_factory=list)
    gated_actions: List[str] = Field(default_factory=list)
