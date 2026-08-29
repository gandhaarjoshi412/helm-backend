"""
File editing tools for HELM.
EditFileTool  — atomic write (temp + os.replace) with mode preservation.
ApplyPatchTool — supports V4A format (Hermes-style) with fuzzy context matching,
                 and falls back to `git apply` for standard unified diffs.
"""
from __future__ import annotations

import difflib
from typing import Any, Dict

from packages.agent.tools.base import Tool, ToolContext, ToolResult
from packages.agent.tools.patch_parser import apply_v4a_patch, apply_v4a_patch_async


class EditFileTool(Tool):
    name = "edit_file"
    description = (
        "Write or overwrite a file in the sandbox repository. "
        "Uses atomic write (temp file + rename) to prevent partial writes. "
        "Provide the complete new file content."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path to file in repository"},
            "content": {"type": "string", "description": "Complete new file content"},
        },
        "required": ["path", "content"],
    }

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        path = arguments.get("path", "")
        content = arguments.get("content", "")
        try:
            # Read existing file to compute change statistics
            old_content = ""
            try:
                old_content = await context.sandbox.read_file(context.env_id, path)
            except Exception:
                pass

            # Write via sandbox (which handles the actual atomic write)
            await context.sandbox.write_file(context.env_id, path, content)

            old_lines = old_content.splitlines(keepends=True)
            new_lines = content.splitlines(keepends=True)
            diff = "".join(
                difflib.unified_diff(old_lines, new_lines, fromfile=f"a/{path}", tofile=f"b/{path}")
            )
            additions = diff.count("\n+")
            deletions = diff.count("\n-")

            return ToolResult(
                success=True,
                output=f"Successfully wrote '{path}' (+{additions}/-{deletions} lines).",
                metadata={
                    "diff": diff,
                    "is_new": not bool(old_content),
                    "additions": additions,
                    "deletions": deletions,
                },
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


class ApplyPatchTool(Tool):
    name = "apply_patch"
    description = (
        "Apply a patch to files in the repository. Supports two formats:\n\n"
        "1. **V4A format** (preferred — from Hermes/Codex/Cline):\n"
        "   *** Begin Patch\n"
        "   *** Update File: path/to/file.py\n"
        "   @@ context hint @@\n"
        "    context line\n"
        "   -removed line\n"
        "   +added line\n"
        "   *** Add File: path/to/new.py\n"
        "   +new file content\n"
        "   *** Delete File: path/to/old.py\n"
        "   *** Move File: old.py -> new.py\n"
        "   *** End Patch\n\n"
        "2. **Unified diff** (standard `git diff` / `diff -u` format):\n"
        "   Uses `git apply` as fallback."
    )
    parameters = {
        "type": "object",
        "properties": {
            "patch_content": {
                "type": "string",
                "description": "Patch content in V4A or unified diff format",
            },
            "path": {
                "type": "string",
                "description": "File path (only needed for unified diff fallback)",
            },
        },
        "required": ["patch_content"],
    }

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        patch_content = arguments.get("patch_content", "")
        path = arguments.get("path", "")

        # Detect format
        is_v4a = "*** Begin Patch" in patch_content or "***Begin Patch" in patch_content

        if is_v4a:
            return await self._apply_v4a(patch_content, context)
        else:
            return await self._apply_git(patch_content, path, context)

    async def _apply_v4a(self, patch_content: str, context: ToolContext) -> ToolResult:
        """Apply using the Hermes V4A fuzzy patch engine."""
        try:
            if context.sandbox and context.env_id:
                result = await apply_v4a_patch_async(patch_content, sandbox=context.sandbox, env_id=context.env_id)
            else:
                result = apply_v4a_patch(patch_content, repo_path=context.repo_path)

            if result["success"]:
                return ToolResult(
                    success=True,
                    output=(
                        f"V4A patch applied: {result['operations_applied']} file(s) changed: "
                        f"{', '.join(result['files_changed'])}"
                    ),
                    metadata=result,
                )
            else:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"V4A patch errors:\n" + "\n".join(result["errors"]),
                    metadata=result,
                )
        except Exception as e:
            return ToolResult(success=False, output="", error=f"V4A patch failed: {e}")

    async def _apply_git(self, patch_content: str, path: str, context: ToolContext) -> ToolResult:
        """Fallback: write patch to temp file and run git apply."""
        try:
            safe_name = (path or "patch").replace("/", "_").replace(".", "_")
            patch_temp = f".helm_patch_{safe_name}.diff"
            await context.sandbox.write_file(context.env_id, patch_temp, patch_content)
            exec_res = await context.sandbox.execute(
                context.env_id,
                f"git apply --ignore-whitespace {patch_temp}",
                timeout_seconds=30,
            )
            await context.sandbox.execute(context.env_id, f"rm -f {patch_temp}")
            if not exec_res.success:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"git apply failed: {exec_res.stderr or exec_res.stdout}",
                )
            return ToolResult(success=True, output=f"Patch applied to '{path or 'repository'}'.")
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))
