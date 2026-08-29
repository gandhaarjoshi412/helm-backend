from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
import uuid


class EventType(str, Enum):
    RUN_STARTED = "run_started"
    PHASE_STARTED = "phase_started"
    PHASE_COMPLETED = "phase_completed"
    CONTEXT_SEARCH = "context_search"
    FILE_READ = "file_read"
    FILE_MODIFIED = "file_modified"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    COMMAND_STARTED = "command_started"
    COMMAND_COMPLETED = "command_completed"
    TEST_STARTED = "test_started"
    TEST_COMPLETED = "test_completed"
    AGENT_MESSAGE = "agent_message"
    VERIFICATION_STARTED = "verification_started"
    VERIFICATION_COMPLETED = "verification_completed"
    SELF_CORRECTION = "self_correction"
    REVIEW_STARTED = "review_started"
    REVIEW_COMPLETED = "review_completed"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_RESOLVED = "approval_resolved"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"


class AgentEvent(BaseModel):
    id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")
    run_id: str
    task_id: str
    type: EventType
    phase: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    title: str = ""
    summary: str = ""
    tool_name: Optional[str] = None
    tool_input: Optional[Dict[str, Any]] = None
    tool_output: Optional[Dict[str, Any]] = None
    duration_ms: Optional[int] = None
    status: str = "info"  # info, success, warning, error
    metadata: Dict[str, Any] = Field(default_factory=dict)
