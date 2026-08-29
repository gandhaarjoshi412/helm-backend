from __future__ import annotations
from typing import Any, Dict
from packages.agent.tools.base import Tool, ToolContext, ToolResult


class RequestApprovalTool(Tool):
    name = "request_approval"
    description = "Explicitly request human approval for a high-impact or sensitive action."
    parameters = {
        "type": "object",
        "properties": {
            "action_type": {"type": "string", "description": "Type of action (e.g. git_push, create_pr, deploy)"},
            "description": {"type": "string", "description": "Human-readable description of what will be performed"},
            "payload": {"type": "object", "description": "Arbitrary metadata associated with the action", "default": {}},
        },
        "required": ["action_type", "description"],
    }

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        action_type = arguments.get("action_type", "")
        description = arguments.get("description", "")
        payload = arguments.get("payload", {})
        return ToolResult(
            success=True,
            output=f"Approval request recorded for '{action_type}': {description}",
            metadata={"action_type": action_type, "description": description, "payload": payload, "status": "pending"},
        )
