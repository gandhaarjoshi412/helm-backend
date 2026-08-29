from __future__ import annotations
from typing import Any, Dict, List, Optional

from packages.agent.policies.engine import PolicyEngine
from packages.agent.tools.approval_tools import RequestApprovalTool
from packages.agent.tools.base import Tool, ToolContext, ToolResult
from packages.agent.tools.edit_tools import ApplyPatchTool, EditFileTool
from packages.agent.tools.execution_tools import (
    RunBuildTool,
    RunCommandTool,
    RunLinterTool,
    RunTestsTool,
    RunTypecheckTool,
)
from packages.agent.tools.git_tools import (
    CommitChangesTool,
    CreateBranchTool,
    CreatePullRequestTool,
    GetGitBlameTool,
    GetGitDiffTool,
    GetGitHistoryTool,
    GetGitStatusTool,
    PushBranchTool,
)
from packages.agent.tools.repo_tools import (
    FindCallersTool,
    FindDependentsTool,
    FindReferencesTool,
    FindSymbolTool,
    GetRepositoryStructureTool,
    ReadFileTool,
    RepoSearchTool,
)
from packages.agent.tools.web_tools import WebExtractTool, WebSearchTool
from packages.shared.errors import PolicyViolationError, ToolExecutionError
from packages.shared.logging import logger


class ToolRegistry:
    """Central registry of all controlled tools available to HELM agents."""

    def __init__(self, policy_engine: Optional[PolicyEngine] = None):
        self.policy_engine = policy_engine or PolicyEngine()
        self._tools: Dict[str, Tool] = {}
        self._register_default_tools()

    def _register_default_tools(self) -> None:
        default_tool_instances: List[Tool] = [
            RepoSearchTool(),
            ReadFileTool(),
            FindSymbolTool(),
            FindReferencesTool(),
            FindCallersTool(),
            FindDependentsTool(),
            GetRepositoryStructureTool(),
            EditFileTool(),
            ApplyPatchTool(),
            RunCommandTool(),
            RunTestsTool(),
            RunLinterTool(),
            RunTypecheckTool(),
            RunBuildTool(),
            GetGitStatusTool(),
            GetGitDiffTool(),
            GetGitHistoryTool(),
            GetGitBlameTool(),
            CreateBranchTool(),
            CommitChangesTool(),
            PushBranchTool(),
            CreatePullRequestTool(),
            RequestApprovalTool(),
            # Hermes-ported web tools (no API key required)
            WebSearchTool(),
            WebExtractTool(),
        ]
        for tool in default_tool_instances:
            self._tools[tool.name] = tool

    def get_tool(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def list_tools(self) -> List[Tool]:
        return list(self._tools.values())

    def to_openai_tools(self) -> List[Dict[str, Any]]:
        return [tool.to_openai_schema() for tool in self._tools.values()]

    async def execute_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        tool = self.get_tool(tool_name)
        if not tool:
            return ToolResult(
                success=False,
                output="",
                error=f"Tool '{tool_name}' is not recognized or available.",
            )

        # Policy validation
        rule = self.policy_engine.evaluate_tool(tool_name, arguments)
        if not rule.allowed:
            logger.warning(f"Policy blocked tool '{tool_name}': {rule.reason}")
            return ToolResult(
                success=False,
                output="",
                error=f"Action blocked by policy: {rule.reason}",
                metadata={"policy_blocked": True},
            )

        if rule.requires_approval:
            logger.info(f"Tool '{tool_name}' requires human approval before proceeding.")
            return ToolResult(
                success=True,
                output=f"Action '{tool_name}' is pending human approval.",
                metadata={"requires_approval": True, "reason": rule.reason, "arguments": arguments},
            )

        try:
            return await tool.execute(arguments, context)
        except Exception as e:
            logger.error(f"Error executing tool '{tool_name}': {e}")
            return ToolResult(success=False, output="", error=str(e))
