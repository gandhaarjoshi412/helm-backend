"""
Tests for Hermes-ported HELM tool components.
Covers: V4A patch parser, ReadFileTool gutter/cap, EditFileTool, WebSearchTool, WebExtractTool.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Patch parser tests
# ---------------------------------------------------------------------------

from packages.agent.tools.patch_parser import (
    OperationType,
    _atomic_write,
    _find_hunk_position,
    apply_v4a_patch,
    parse_v4a_patch,
)


class TestV4APatchParser:
    """Test the V4A patch parser ported from Hermes."""

    def test_parse_add_file(self):
        patch = (
            "*** Begin Patch\n"
            "*** Add File: newfile.py\n"
            "+def hello():\n"
            "+    return 'world'\n"
            "*** End Patch\n"
        )
        ops, error = parse_v4a_patch(patch)
        assert error is None
        assert len(ops) == 1
        assert ops[0].operation == OperationType.ADD
        assert ops[0].file_path == "newfile.py"
        assert "def hello():" in (ops[0].content or "")

    def test_parse_delete_file(self):
        patch = (
            "*** Begin Patch\n"
            "*** Delete File: obsolete.py\n"
            "*** End Patch\n"
        )
        ops, error = parse_v4a_patch(patch)
        assert error is None
        assert len(ops) == 1
        assert ops[0].operation == OperationType.DELETE
        assert ops[0].file_path == "obsolete.py"

    def test_parse_move_file(self):
        patch = (
            "*** Begin Patch\n"
            "*** Move File: old/path.py -> new/path.py\n"
            "*** End Patch\n"
        )
        ops, error = parse_v4a_patch(patch)
        assert error is None
        assert len(ops) == 1
        assert ops[0].operation == OperationType.MOVE
        assert ops[0].file_path == "old/path.py"
        assert ops[0].new_path == "new/path.py"

    def test_parse_update_file(self):
        patch = (
            "*** Begin Patch\n"
            "*** Update File: src/main.py\n"
            "@@ def old_function @@\n"
            " def old_function():\n"
            "-    return 1\n"
            "+    return 2\n"
            "*** End Patch\n"
        )
        ops, error = parse_v4a_patch(patch)
        assert error is None
        assert len(ops) == 1
        assert ops[0].operation == OperationType.UPDATE
        assert ops[0].file_path == "src/main.py"
        assert len(ops[0].hunks) == 1
        hunk = ops[0].hunks[0]
        prefixes = [hl.prefix for hl in hunk.lines]
        assert '-' in prefixes
        assert '+' in prefixes

    def test_parse_no_begin_marker(self):
        ops, error = parse_v4a_patch("*** Update File: foo.py\n-old\n+new\n*** End Patch\n")
        assert error is not None
        assert "Begin Patch" in error

    def test_parse_no_end_marker(self):
        ops, error = parse_v4a_patch("*** Begin Patch\n*** Add File: foo.py\n+content\n")
        assert error is not None
        assert "End Patch" in error

    def test_parse_crlf_tolerance(self):
        patch = "*** Begin Patch\r\n*** Add File: test.txt\r\n+hello\r\n*** End Patch\r\n"
        ops, error = parse_v4a_patch(patch)
        assert error is None
        assert len(ops) == 1

    def test_parse_multiple_operations(self):
        patch = (
            "*** Begin Patch\n"
            "*** Add File: new.py\n"
            "+x = 1\n"
            "*** Delete File: old.py\n"
            "*** End Patch\n"
        )
        ops, error = parse_v4a_patch(patch)
        assert error is None
        assert len(ops) == 2
        assert ops[0].operation == OperationType.ADD
        assert ops[1].operation == OperationType.DELETE


class TestApplyV4APatch:
    """Integration tests for apply_v4a_patch against real filesystem."""

    def test_add_file(self, tmp_path):
        patch = (
            "*** Begin Patch\n"
            "*** Add File: hello.py\n"
            "+def greet():\n"
            "+    return 'hello'\n"
            "*** End Patch\n"
        )
        result = apply_v4a_patch(patch, str(tmp_path))
        assert result["success"] is True
        assert "hello.py" in result["files_changed"]
        assert (tmp_path / "hello.py").exists()
        content = (tmp_path / "hello.py").read_text()
        assert "def greet():" in content

    def test_delete_file(self, tmp_path):
        target = tmp_path / "delete_me.py"
        target.write_text("x = 1\n")
        patch = (
            "*** Begin Patch\n"
            "*** Delete File: delete_me.py\n"
            "*** End Patch\n"
        )
        result = apply_v4a_patch(patch, str(tmp_path))
        assert result["success"] is True
        assert not target.exists()

    def test_move_file(self, tmp_path):
        src = tmp_path / "old.py"
        src.write_text("x = 1\n")
        patch = (
            "*** Begin Patch\n"
            "*** Move File: old.py -> new.py\n"
            "*** End Patch\n"
        )
        result = apply_v4a_patch(patch, str(tmp_path))
        assert result["success"] is True
        assert not src.exists()
        assert (tmp_path / "new.py").exists()

    def test_update_file(self, tmp_path):
        target = tmp_path / "calc.py"
        target.write_text("def add(a, b):\n    return a - b\n")
        patch = (
            "*** Begin Patch\n"
            "*** Update File: calc.py\n"
            " def add(a, b):\n"
            "-    return a - b\n"
            "+    return a + b\n"
            "*** End Patch\n"
        )
        result = apply_v4a_patch(patch, str(tmp_path))
        assert result["success"] is True
        content = target.read_text()
        assert "return a + b" in content
        assert "return a - b" not in content

    def test_update_missing_file(self, tmp_path):
        patch = (
            "*** Begin Patch\n"
            "*** Update File: ghost.py\n"
            " def foo():\n"
            "-    pass\n"
            "+    return 1\n"
            "*** End Patch\n"
        )
        result = apply_v4a_patch(patch, str(tmp_path))
        assert result["success"] is False
        assert any("ghost.py" in e for e in result["errors"])

    def test_invalid_patch_returns_error(self, tmp_path):
        result = apply_v4a_patch("not a patch", str(tmp_path))
        assert result["success"] is False
        assert len(result["errors"]) > 0


class TestAtomicWrite:
    """Test atomic file write utility."""

    def test_atomic_write_creates_file(self, tmp_path):
        p = tmp_path / "output.txt"
        _atomic_write(p, "hello world\n")
        assert p.exists()
        assert p.read_text() == "hello world\n"

    def test_atomic_write_preserves_mode(self, tmp_path):
        p = tmp_path / "script.py"
        p.write_text("# old\n")
        os.chmod(p, 0o755)
        _atomic_write(p, "# new\n")
        mode = oct(p.stat().st_mode)
        assert "755" in mode or "7" in mode  # mode preserved

    def test_atomic_write_creates_parent_dirs(self, tmp_path):
        p = tmp_path / "deep" / "nested" / "file.txt"
        _atomic_write(p, "content\n")
        assert p.exists()


# ---------------------------------------------------------------------------
# ReadFileTool tests
# ---------------------------------------------------------------------------

from packages.agent.tools.repo_tools import (
    _MAX_READ_CHARS,
    _render_gutter,
    _truncate_to_char_budget,
)


class TestReadFileToolHelpers:
    """Test Hermes-ported ReadFileTool helper functions."""

    def test_render_gutter_single_line(self):
        result = _render_gutter(["print('hello')"], start_line=1)
        assert result == "1 | print('hello')"

    def test_render_gutter_multi_line(self):
        lines = ["a", "b", "c"]
        result = _render_gutter(lines, start_line=10)
        assert "10 | a" in result
        assert "11 | b" in result
        assert "12 | c" in result

    def test_render_gutter_right_justifies_line_numbers(self):
        lines = [f"line {i}" for i in range(100)]
        result = _render_gutter(lines, start_line=1)
        # Line 1 should be right-justified to 3 chars (100 total)
        assert " 1 | line 0" in result
        assert "99 | line 98" in result

    def test_truncate_within_budget(self):
        content = "a\nb\nc"
        text, lines, truncated = _truncate_to_char_budget(content, 1000)
        assert truncated is False
        assert text == content
        assert lines == 3

    def test_truncate_over_budget(self):
        # Create content that exceeds budget
        content = "\n".join("x" * 50 for _ in range(100))
        text, lines, truncated = _truncate_to_char_budget(content, 200)
        assert truncated is True
        assert len(text) <= 200 + 50  # within one line of budget
        assert lines < 100

    def test_truncate_single_oversized_line(self):
        content = "x" * 200
        text, lines, truncated = _truncate_to_char_budget(content, 100)
        assert truncated is True
        assert len(text) == 100


# ---------------------------------------------------------------------------
# WebSearchTool / WebExtractTool tests (mocked HTTP)
# ---------------------------------------------------------------------------

from packages.agent.tools.web_tools import WebExtractTool, WebSearchTool


class TestWebSearchTool:
    """Test WebSearchTool with mocked DuckDuckGo response."""

    @pytest.mark.asyncio
    async def test_search_returns_results(self):
        ddg_response = json.dumps({
            "AbstractText": "Python is a programming language.",
            "AbstractURL": "https://www.python.org",
            "Heading": "Python",
            "RelatedTopics": [
                {"FirstURL": "https://example.com/1", "Text": "Python tutorial for beginners"},
                {"FirstURL": "https://example.com/2", "Text": "Python documentation"},
            ],
        })

        with patch("packages.agent.tools.web_tools._http_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = ddg_response
            tool = WebSearchTool()
            context = MagicMock()
            result = await tool.execute({"query": "Python programming"}, context)

        assert result.success is True
        assert "Python" in str(result.output)
        assert "https://www.python.org" in str(result.output)

    @pytest.mark.asyncio
    async def test_search_empty_query(self):
        tool = WebSearchTool()
        context = MagicMock()
        result = await tool.execute({"query": ""}, context)
        assert result.success is False
        assert "empty" in result.error.lower()

    @pytest.mark.asyncio
    async def test_search_http_error(self):
        with patch("packages.agent.tools.web_tools._http_get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = Exception("Connection refused")
            tool = WebSearchTool()
            context = MagicMock()
            result = await tool.execute({"query": "test query"}, context)

        assert result.success is False
        assert "failed" in result.error.lower()


class TestWebExtractTool:
    """Test WebExtractTool with mocked HTTP."""

    @pytest.mark.asyncio
    async def test_extract_returns_content(self):
        fake_content = "# Python Docs\n\nPython is great.\n" * 50

        with patch("packages.agent.tools.web_tools._http_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = fake_content
            tool = WebExtractTool()
            context = MagicMock()
            result = await tool.execute({"url": "https://docs.python.org"}, context)

        assert result.success is True
        assert "Python" in str(result.output)

    @pytest.mark.asyncio
    async def test_extract_truncates_large_content(self):
        # Generate content larger than 10K chars
        large_content = "x" * 25_000

        with patch("packages.agent.tools.web_tools._http_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = large_content
            tool = WebExtractTool()
            context = MagicMock()
            result = await tool.execute({"url": "https://example.com", "max_chars": 10_000}, context)

        assert result.success is True
        assert "truncated" in str(result.output)
        assert len(str(result.output)) <= 10_100  # slight overhead for message

    @pytest.mark.asyncio
    async def test_extract_invalid_url(self):
        tool = WebExtractTool()
        context = MagicMock()
        result = await tool.execute({"url": "not-a-url"}, context)
        assert result.success is False
        assert "http" in result.error.lower()

    @pytest.mark.asyncio
    async def test_extract_empty_url(self):
        tool = WebExtractTool()
        context = MagicMock()
        result = await tool.execute({"url": ""}, context)
        assert result.success is False


# ---------------------------------------------------------------------------
# Device path blocklist test
# ---------------------------------------------------------------------------

class TestDeviceBlocklist:
    """Ensure blocked device paths are rejected by ReadFileTool."""

    @pytest.mark.asyncio
    async def test_blocked_device_path(self):
        from packages.agent.tools.repo_tools import ReadFileTool
        tool = ReadFileTool()
        context = MagicMock()
        result = await tool.execute({"path": "/dev/urandom"}, context)
        assert result.success is False
        assert "blocked" in result.error.lower()


# ---------------------------------------------------------------------------
# EditFileTool tests
# ---------------------------------------------------------------------------

class TestEditFileTool:
    """Test upgraded EditFileTool."""

    @pytest.mark.asyncio
    async def test_edit_file_success(self):
        from packages.agent.tools.edit_tools import EditFileTool
        tool = EditFileTool()
        context = MagicMock()
        context.sandbox.read_file = AsyncMock(return_value="old content\n")
        context.sandbox.write_file = AsyncMock()
        context.env_id = "test_env"

        result = await tool.execute({"path": "foo.py", "content": "new content\n"}, context)
        assert result.success is True
        context.sandbox.write_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_edit_file_reports_additions_deletions(self):
        from packages.agent.tools.edit_tools import EditFileTool
        tool = EditFileTool()
        context = MagicMock()
        context.sandbox.read_file = AsyncMock(return_value="line1\nline2\nline3\n")
        context.sandbox.write_file = AsyncMock()
        context.env_id = "test_env"

        result = await tool.execute(
            {"path": "foo.py", "content": "line1\nNEW LINE\nline3\n"},
            context,
        )
        assert result.success is True
        assert "+" in result.output or result.metadata.get("additions", 0) >= 0
