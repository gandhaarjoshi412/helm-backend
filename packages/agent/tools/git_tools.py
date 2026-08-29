from __future__ import annotations
from typing import Any, Dict
from packages.agent.tools.base import Tool, ToolContext, ToolResult


class GetGitStatusTool(Tool):
    name = "get_git_status"
    description = "Inspect repository working tree status, dirty files, and staged modifications."
    parameters = {"type": "object", "properties": {}}

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        res = await context.sandbox.execute(context.env_id, "git status --short", timeout_seconds=15)
        return ToolResult(
            success=res.success,
            output=res.combined_output,
            error=res.stderr if not res.success else None,
            metadata={"is_dirty": bool(res.stdout.strip())},
        )


class GetGitDiffTool(Tool):
    name = "get_git_diff"
    description = "Get full unified git diff of uncommitted or committed changes."
    parameters = {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Git target (e.g. HEAD, HEAD~1, or specific file path)", "default": "HEAD"},
        },
    }

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        target = arguments.get("target", "HEAD")
        res = await context.sandbox.execute(context.env_id, f"git diff {target}", timeout_seconds=20)
        return ToolResult(success=res.success, output=res.stdout, error=res.stderr if not res.success else None)


class GetGitHistoryTool(Tool):
    name = "get_git_history"
    description = "Get recent Git commit logs."
    parameters = {
        "type": "object",
        "properties": {
            "max_commits": {"type": "integer", "description": "Number of recent commits to show (default: 10)", "default": 10},
        },
    }

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        max_commits = arguments.get("max_commits", 10)
        res = await context.sandbox.execute(
            context.env_id,
            f"git log -n {max_commits} --pretty=format:'%h - %an, %ar : %s'",
            timeout_seconds=15,
        )
        return ToolResult(success=res.success, output=res.stdout, error=res.stderr if not res.success else None)


class GetGitBlameTool(Tool):
    name = "get_git_blame"
    description = "Show line-by-line commit blame for a file."
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Relative file path"},
            "line_start": {"type": "integer", "description": "Start line number"},
            "line_end": {"type": "integer", "description": "End line number"},
        },
        "required": ["file_path"],
    }

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        file_path = arguments.get("file_path", "")
        line_start = arguments.get("line_start")
        line_end = arguments.get("line_end")
        cmd = f"git blame {file_path}"
        if line_start is not None and line_end is not None:
            cmd = f"git blame -L {line_start},{line_end} {file_path}"
        res = await context.sandbox.execute(context.env_id, cmd, timeout_seconds=15)
        return ToolResult(success=res.success, output=res.stdout, error=res.stderr if not res.success else None)


class CreateBranchTool(Tool):
    name = "create_branch"
    description = "Create and switch to a new Git branch in the sandbox."
    parameters = {
        "type": "object",
        "properties": {
            "branch_name": {"type": "string", "description": "Name of the branch to create"},
        },
        "required": ["branch_name"],
    }

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        branch_name = arguments.get("branch_name", "")
        res = await context.sandbox.execute(context.env_id, f"git checkout -b {branch_name}", timeout_seconds=15)
        return ToolResult(
            success=res.success,
            output=res.combined_output or f"Switched to a new branch '{branch_name}'",
            error=res.stderr if not res.success else None,
        )


class CommitChangesTool(Tool):
    name = "commit_changes"
    description = "Stage all modified files and commit them to Git."
    parameters = {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "Commit message"},
        },
        "required": ["message"],
    }

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        msg = arguments.get("message", "HELM auto commit")
        # Stage all files
        stage_res = await context.sandbox.execute(context.env_id, "git add -A", timeout_seconds=15)
        if not stage_res.success:
            return ToolResult(success=False, output="", error=f"Git add failed: {stage_res.stderr}")

        commit_res = await context.sandbox.execute(
            context.env_id,
            f'git -c user.name="HELM Agent" -c user.email="helm@agent.ai" commit -m "{msg}"',
            timeout_seconds=15,
        )
        return ToolResult(
            success=commit_res.success,
            output=commit_res.combined_output,
            error=commit_res.stderr if not commit_res.success else None,
        )


class PushBranchTool(Tool):
    name = "push_branch"
    description = "Push a branch to a remote Git repository (requires approval)."
    parameters = {
        "type": "object",
        "properties": {
            "branch_name": {"type": "string", "description": "Name of branch to push"},
            "remote": {"type": "string", "description": "Remote name (default: origin)", "default": "origin"},
        },
        "required": ["branch_name"],
    }

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        branch_name = arguments.get("branch_name", "")
        remote = arguments.get("remote", "origin")
        res = await context.sandbox.execute(context.env_id, f"git push {remote} {branch_name}", timeout_seconds=30)
        return ToolResult(success=res.success, output=res.combined_output, error=res.stderr if not res.success else None)


class CreatePullRequestTool(Tool):
    name = "create_pull_request"
    description = "Create a pull request on GitHub (requires approval)."
    parameters = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Pull request title"},
            "body": {"type": "string", "description": "Pull request description / body"},
            "head_branch": {"type": "string", "description": "Branch containing changes"},
            "base_branch": {"type": "string", "description": "Target branch (e.g. main)", "default": "main"},
        },
        "required": ["title", "head_branch"],
    }

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        title = arguments.get("title", "")
        head = arguments.get("head_branch", "")
        base = arguments.get("base_branch", "main")
        return ToolResult(
            success=True,
            output=f"Pull request '{title}' prepared for merge from '{head}' into '{base}'.",
            metadata={"title": title, "head": head, "base": base},
        )
