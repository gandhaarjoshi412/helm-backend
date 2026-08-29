from __future__ import annotations
import re
from typing import Any, Dict
from packages.agent.tools.base import Tool, ToolContext, ToolResult


class RunCommandTool(Tool):
    name = "run_command"
    description = "Execute a shell command inside the isolated sandbox."
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to run"},
            "cwd": {"type": "string", "description": "Working directory relative to repository root"},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default: 60)", "default": 60},
        },
        "required": ["command"],
    }

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        command = arguments.get("command", "")
        cwd = arguments.get("cwd")
        timeout = arguments.get("timeout", 60)
        res = await context.sandbox.execute(context.env_id, command, cwd=cwd, timeout_seconds=timeout)
        return ToolResult(
            success=res.success,
            output=res.combined_output,
            error=res.stderr if not res.success else None,
            metadata={
                "exit_code": res.exit_code,
                "duration_ms": res.duration_ms,
                "timed_out": res.timed_out,
            },
        )


class RunTestsTool(Tool):
    name = "run_tests"
    description = "Execute test suites (e.g. pytest, npm test, go test) in the sandbox and parse results."
    parameters = {
        "type": "object",
        "properties": {
            "test_command": {"type": "string", "description": "Test command (e.g. 'pytest -v' or 'npm test')"},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default: 120)", "default": 120},
        },
        "required": ["test_command"],
    }

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        test_command = arguments.get("test_command", "pytest")
        timeout = arguments.get("timeout", 120)
        res = await context.sandbox.execute(context.env_id, test_command, timeout_seconds=timeout)

        # Parse test counts
        passed_count = 0
        failed_count = 0
        output = res.combined_output

        # Pytest pattern: "5 passed, 1 failed"
        pytest_match = re.search(r"(\d+)\s+passed(?:,\s+(\d+)\s+failed)?", output)
        if pytest_match:
            passed_count = int(pytest_match.group(1))
            failed_count = int(pytest_match.group(2)) if pytest_match.group(2) else 0
        elif res.success:
            passed_count = 1

        return ToolResult(
            success=res.success,
            output=output,
            error=res.stderr if not res.success else None,
            metadata={
                "exit_code": res.exit_code,
                "duration_ms": res.duration_ms,
                "tests_passed": passed_count,
                "tests_failed": failed_count,
                "timed_out": res.timed_out,
            },
        )


class RunLinterTool(Tool):
    name = "run_linter"
    description = "Execute linters (ruff, eslint, flake8, golangci-lint) in the sandbox."
    parameters = {
        "type": "object",
        "properties": {
            "lint_command": {"type": "string", "description": "Linter command to execute"},
        },
        "required": ["lint_command"],
    }

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        lint_command = arguments.get("lint_command", "ruff check .")
        res = await context.sandbox.execute(context.env_id, lint_command, timeout_seconds=60)
        return ToolResult(
            success=res.success,
            output=res.combined_output,
            error=res.stderr if not res.success else None,
            metadata={"exit_code": res.exit_code, "duration_ms": res.duration_ms},
        )


class RunTypecheckTool(Tool):
    name = "run_typecheck"
    description = "Execute static type checker (mypy, tsc) in the sandbox."
    parameters = {
        "type": "object",
        "properties": {
            "typecheck_command": {"type": "string", "description": "Typecheck command (e.g. 'mypy .' or 'npx tsc --noEmit')"},
        },
        "required": ["typecheck_command"],
    }

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        cmd = arguments.get("typecheck_command", "mypy .")
        res = await context.sandbox.execute(context.env_id, cmd, timeout_seconds=60)
        return ToolResult(
            success=res.success,
            output=res.combined_output,
            error=res.stderr if not res.success else None,
            metadata={"exit_code": res.exit_code, "duration_ms": res.duration_ms},
        )


class RunBuildTool(Tool):
    name = "run_build"
    description = "Execute project build command (e.g. npm run build, go build) in sandbox."
    parameters = {
        "type": "object",
        "properties": {
            "build_command": {"type": "string", "description": "Build command to execute"},
        },
        "required": ["build_command"],
    }

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        cmd = arguments.get("build_command", "npm run build")
        res = await context.sandbox.execute(context.env_id, cmd, timeout_seconds=120)
        return ToolResult(
            success=res.success,
            output=res.combined_output,
            error=res.stderr if not res.success else None,
            metadata={"exit_code": res.exit_code, "duration_ms": res.duration_ms},
        )
