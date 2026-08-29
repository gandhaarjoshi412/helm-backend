from __future__ import annotations
from typing import Any, Dict, Optional


class HELMError(Exception):
    """Base error class for all HELM platform exceptions."""

    def __init__(
        self,
        message: str,
        code: str = "HELM_INTERNAL_ERROR",
        status_code: int = 500,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            }
        }


class ProjectNotFoundError(HELMError):
    def __init__(self, project_id: str):
        super().__init__(
            message=f"Project with ID '{project_id}' not found.",
            code="PROJECT_NOT_FOUND",
            status_code=404,
            details={"project_id": project_id},
        )


class TaskNotFoundError(HELMError):
    def __init__(self, task_id: str):
        super().__init__(
            message=f"Task with ID '{task_id}' not found.",
            code="TASK_NOT_FOUND",
            status_code=404,
            details={"task_id": task_id},
        )


class ApprovalNotFoundError(HELMError):
    def __init__(self, approval_id: str):
        super().__init__(
            message=f"Approval request '{approval_id}' not found.",
            code="APPROVAL_NOT_FOUND",
            status_code=404,
            details={"approval_id": approval_id},
        )


class SandboxError(HELMError):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=f"Sandbox Execution Error: {message}",
            code="SANDBOX_ERROR",
            status_code=500,
            details=details,
        )


class ToolExecutionError(HELMError):
    def __init__(self, tool_name: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=f"Tool '{tool_name}' failed: {message}",
            code="TOOL_EXECUTION_ERROR",
            status_code=400,
            details={"tool_name": tool_name, **(details or {})},
        )


class PolicyViolationError(HELMError):
    def __init__(self, action: str, reason: str):
        super().__init__(
            message=f"Action '{action}' violated security policy: {reason}",
            code="POLICY_VIOLATION",
            status_code=403,
            details={"action": action, "reason": reason},
        )


class ModelProviderError(HELMError):
    def __init__(self, provider: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=f"Model Provider '{provider}' Error: {message}",
            code="MODEL_PROVIDER_ERROR",
            status_code=502,
            details={"provider": provider, **(details or {})},
        )
