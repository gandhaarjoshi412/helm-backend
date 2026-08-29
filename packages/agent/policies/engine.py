from __future__ import annotations
import os
from typing import Any, Dict, List, Optional
import yaml
from pydantic import BaseModel, Field


class PolicyRule(BaseModel):
    allowed: bool = True
    requires_approval: bool = False
    reason: Optional[str] = None


class PolicyConfig(BaseModel):
    repository_read: bool = True
    repository_write: bool = True
    sandbox_execution: bool = True
    git_branch: bool = True
    git_commit: bool = True
    git_push_requires_approval: bool = True
    github_pr_requires_approval: bool = True
    destructive_commands_allowed: bool = False
    blocked_commands: List[str] = Field(
        default_factory=lambda: [
            "rm -rf /",
            "mkfs",
            ":(){ :|:& };:",
            "shutdown",
            "reboot",
            "dd if=",
        ]
    )


class PolicyEngine:
    """
    Evaluates requested agent actions against security, sandbox, and approval policies.
    """

    def __init__(self, config: Optional[PolicyConfig] = None):
        self.config = config or PolicyConfig()

    def evaluate_tool(self, tool_name: str, arguments: Dict[str, Any]) -> PolicyRule:
        """Evaluate whether a tool invocation is allowed, forbidden, or requires approval."""

        # 1. Read operations
        if tool_name in (
            "repo_search",
            "read_file",
            "read_symbol",
            "find_symbol",
            "find_references",
            "find_callers",
            "find_dependents",
            "get_repository_structure",
            "get_git_status",
            "get_git_diff",
            "get_git_history",
            "get_git_blame",
            "search_documentation",
        ):
            if not self.config.repository_read:
                return PolicyRule(allowed=False, reason="Repository read access is disabled by policy.")
            return PolicyRule(allowed=True, requires_approval=False)

        # 2. Write operations
        if tool_name in ("edit_file", "apply_patch"):
            if not self.config.repository_write:
                return PolicyRule(allowed=False, reason="Repository write access is disabled by policy.")
            return PolicyRule(allowed=True, requires_approval=False)

        # 3. Execution tools
        if tool_name in ("run_command", "run_tests", "run_linter", "run_typecheck", "run_build"):
            cmd = str(arguments.get("command", "") or arguments.get("test_command", ""))
            for blocked in self.config.blocked_commands:
                if blocked in cmd:
                    return PolicyRule(
                        allowed=False,
                        reason=f"Command '{cmd}' contains forbidden destructive pattern: '{blocked}'",
                    )
            return PolicyRule(allowed=True, requires_approval=False)

        # 4. Git local actions
        if tool_name in ("create_branch", "commit_changes"):
            return PolicyRule(allowed=True, requires_approval=False)

        # 5. External gated actions (push, PR)
        if tool_name == "push_branch":
            if self.config.git_push_requires_approval:
                return PolicyRule(
                    allowed=True,
                    requires_approval=True,
                    reason="Pushing branches to remote Git repository requires human approval.",
                )
            return PolicyRule(allowed=True, requires_approval=False)

        if tool_name == "create_pull_request":
            if self.config.github_pr_requires_approval:
                return PolicyRule(
                    allowed=True,
                    requires_approval=True,
                    reason="Creating pull requests on GitHub requires human approval.",
                )
            return PolicyRule(allowed=True, requires_approval=False)

        if tool_name == "request_approval":
            return PolicyRule(allowed=True, requires_approval=True)

        return PolicyRule(allowed=True, requires_approval=False)
