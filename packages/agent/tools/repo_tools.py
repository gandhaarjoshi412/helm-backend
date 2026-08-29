from __future__ import annotations
from typing import Any, Dict, List, Optional
from packages.agent.tools.base import Tool, ToolContext, ToolResult
from packages.shared.logging import logger


class RepoSearchTool(Tool):
    name = "repo_search"
    description = "Search repository source code and files for keyword, token, or pattern matches."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search term or regex pattern"},
            "max_results": {"type": "integer", "description": "Maximum number of results to return", "default": 20},
        },
        "required": ["query"],
    }

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        query = arguments.get("query", "")
        max_results = arguments.get("max_results", 20)
        if not context.retrieval_engine:
            return ToolResult(success=False, output=[], error="Retrieval engine is not initialized.")
        results = context.retrieval_engine.search_keyword(query, max_results=max_results)
        return ToolResult(
            success=True,
            output=[{"file": r.file_path, "line": r.line_number, "text": r.matched_text} for r in results],
        )



# Hermes-style file read constants
_MAX_READ_CHARS = 100_000
_LARGE_FILE_HINT_BYTES = 512_000
_BLOCKED_DEVICE_PATHS = frozenset({
    "/dev/zero", "/dev/random", "/dev/urandom", "/dev/full",
    "/dev/stdin", "/dev/tty", "/dev/console",
    "/dev/stdout", "/dev/stderr",
})


def _render_gutter(lines: List[str], start_line: int = 1) -> str:
    """Render lines with numbered gutter like Hermes: '   1 | content'."""
    width = len(str(start_line + len(lines) - 1))
    return "\n".join(
        f"{str(start_line + i).rjust(width)} | {line}"
        for i, line in enumerate(lines)
    )


def _truncate_to_char_budget(content: str, max_chars: int):
    """Trim gutter-rendered content to fit char budget. Returns (text, lines_kept, truncated)."""
    if len(content) <= max_chars:
        return content, content.count("\n") + 1, False
    lines = content.split("\n")
    kept, running = [], 0
    for line in lines:
        addition = len(line) + (1 if kept else 0)
        if running + addition > max_chars:
            break
        kept.append(line)
        running += addition
    if not kept:
        kept.append(lines[0][:max_chars])
    return "\n".join(kept), len(kept), True


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read file contents from the repository with optional line range (1-indexed). "
        "Returns line-numbered output. Capped at 100,000 characters — use start_line/end_line "
        "for large files. Supports next_offset continuation for paginated reads."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path to file in repository"},
            "start_line": {"type": "integer", "description": "Start line (1-indexed, inclusive)"},
            "end_line": {"type": "integer", "description": "End line (1-indexed, inclusive)"},
        },
        "required": ["path"],
    }

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        path = arguments.get("path", "")
        start_line = arguments.get("start_line")
        end_line = arguments.get("end_line")

        # Device-path blocklist (ported from Hermes)
        if path in _BLOCKED_DEVICE_PATHS:
            return ToolResult(success=False, output="", error=f"Reading '{path}' is blocked (device file).")

        try:
            content = await context.sandbox.read_file(context.env_id, path)
            all_lines = content.splitlines()
            total_lines = len(all_lines)

            # Apply line range
            s = max(1, (start_line or 1)) - 1
            e = min(total_lines, end_line or total_lines)
            selected = all_lines[s:e]

            # Render with gutter
            rendered = _render_gutter(selected, start_line=s + 1)

            # Large-file hint
            hint = ""
            if len(content) > _LARGE_FILE_HINT_BYTES and not (start_line or end_line):
                hint = (
                    f"\n\n[Large file: {len(content):,} bytes / {total_lines:,} lines. "
                    f"Use start_line/end_line for targeted reads.]"
                )

            # Char budget cap
            rendered, lines_kept, truncated = _truncate_to_char_budget(rendered, _MAX_READ_CHARS)
            if truncated:
                next_line = s + 1 + lines_kept
                rendered += (
                    f"\n\n[Output capped at {_MAX_READ_CHARS:,} chars. "
                    f"Read continues from line {next_line}. "
                    f"Use start_line={next_line} to continue.]"
                )

            return ToolResult(
                success=True,
                output=rendered + hint,
                metadata={
                    "total_lines": total_lines,
                    "lines_shown": lines_kept,
                    "truncated": truncated,
                },
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))




class FindSymbolTool(Tool):
    name = "find_symbol"
    description = "Find symbols (classes, functions, methods, interfaces) across the codebase."
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Name or partial name of the symbol to find"},
        },
        "required": ["name"],
    }

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        name = arguments.get("name", "")
        if not context.retrieval_engine:
            return ToolResult(success=False, output=[], error="Retrieval engine is not initialized.")
        symbols = context.retrieval_engine.search_symbols(name)
        return ToolResult(success=True, output=symbols)


class FindReferencesTool(Tool):
    name = "find_references"
    description = "Find all definitions and references of a symbol across the code graph."
    parameters = {
        "type": "object",
        "properties": {
            "symbol_name": {"type": "string", "description": "Name of the symbol"},
        },
        "required": ["symbol_name"],
    }

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        symbol_name = arguments.get("symbol_name", "")
        if not context.code_graph:
            return ToolResult(success=False, output=[], error="Code graph is not initialized.")
        refs = context.code_graph.find_references(symbol_name)
        return ToolResult(success=True, output=refs)


class FindCallersTool(Tool):
    name = "find_callers"
    description = "Find all functions, methods, or classes that call the given symbol."
    parameters = {
        "type": "object",
        "properties": {
            "symbol_name": {"type": "string", "description": "Name of the symbol"},
        },
        "required": ["symbol_name"],
    }

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        symbol_name = arguments.get("symbol_name", "")
        if not context.code_graph:
            return ToolResult(success=False, output=[], error="Code graph is not initialized.")
        callers = context.code_graph.find_callers(symbol_name)
        return ToolResult(
            success=True,
            output=[
                {
                    "name": c.name,
                    "kind": c.node_type.value,
                    "file": c.file_path,
                    "lines": f"{c.line_start}-{c.line_end}",
                    "signature": c.signature,
                }
                for c in callers
            ],
        )


class FindDependentsTool(Tool):
    name = "find_dependents"
    description = "Find all files/modules that import or depend on a given file."
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Relative path to module/file"},
        },
        "required": ["file_path"],
    }

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        file_path = arguments.get("file_path", "")
        if not context.code_graph:
            return ToolResult(success=False, output=[], error="Code graph is not initialized.")
        deps = context.code_graph.find_dependents(file_path)
        return ToolResult(success=True, output=deps)


class GetRepositoryStructureTool(Tool):
    name = "get_repository_structure"
    description = "List files and directory tree in the sandbox repository."
    parameters = {
        "type": "object",
        "properties": {
            "dir_path": {"type": "string", "description": "Subdirectory path or empty for root", "default": ""},
        },
    }

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        dir_path = arguments.get("dir_path", "")
        try:
            files = await context.sandbox.list_files(context.env_id, dir_path)
            return ToolResult(success=True, output=files)
        except Exception as e:
            return ToolResult(success=False, output=[], error=str(e))
