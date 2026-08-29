"""
V4A Patch Format Parser — ported from Hermes Agent (NousResearch/hermes-agent)
MIT License. All Hermes-internal imports replaced with stdlib equivalents.

Supports the V4A patch format used by Codex, Cline, and other coding agents:

    *** Begin Patch
    *** Update File: path/to/file.py
    @@ optional context hint @@
     context line (space prefix)
    -removed line (minus prefix)
    +added line (plus prefix)
    *** Add File: path/to/new.py
    +new file content
    *** Delete File: path/to/old.py
    *** Move File: old/path.py -> new/path.py
    *** End Patch

Usage:
    from packages.agent.tools.patch_parser import parse_v4a_patch, apply_v4a_patch

    result = apply_v4a_patch(patch_content, repo_path)
    # result: dict with {success, files_changed, errors}
"""
from __future__ import annotations

import difflib
import os
import re
import stat
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class OperationType(Enum):
    ADD = "add"
    UPDATE = "update"
    DELETE = "delete"
    MOVE = "move"


@dataclass
class HunkLine:
    """A single line in a patch hunk."""
    prefix: str  # ' ', '-', or '+'
    content: str


@dataclass
class Hunk:
    """A group of changes within a file."""
    context_hint: Optional[str] = None
    lines: List[HunkLine] = field(default_factory=list)


@dataclass
class PatchOperation:
    """A single operation in a V4A patch."""
    operation: OperationType
    file_path: str
    new_path: Optional[str] = None  # for MOVE
    hunks: List[Hunk] = field(default_factory=list)
    content: Optional[str] = None  # for ADD


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse_v4a_patch(patch_content: str) -> Tuple[List[PatchOperation], Optional[str]]:
    """
    Parse a V4A format patch string.

    Returns:
        (operations, error_message) — error_message is None on success.
    """
    # Normalise CRLF → LF
    lines = [ln.rstrip('\r') for ln in patch_content.split('\n')]
    operations: List[PatchOperation] = []

    begin_re = re.compile(r'^\*\*\*\s*Begin\s+Patch\s*$')
    end_re   = re.compile(r'^\*\*\*\s*End\s+Patch\s*$')

    start_idx = end_idx = None
    for i, line in enumerate(lines):
        if begin_re.match(line):
            start_idx = i
        elif end_re.match(line):
            end_idx = i

    if start_idx is None:
        return [], "No '*** Begin Patch' marker found."
    if end_idx is None:
        return [], "No '*** End Patch' marker found."
    if end_idx <= start_idx:
        return [], "'*** End Patch' appears before '*** Begin Patch'."

    body = lines[start_idx + 1:end_idx]

    update_re = re.compile(r'^\*\*\*\s*Update\s+File:\s*(.+)$')
    add_re    = re.compile(r'^\*\*\*\s*Add\s+File:\s*(.+)$')
    delete_re = re.compile(r'^\*\*\*\s*Delete\s+File:\s*(.+)$')
    move_re   = re.compile(r'^\*\*\*\s*Move\s+File:\s*(.+?)\s*->\s*(.+)$')
    hunk_re   = re.compile(r'^@@.*@@')

    current_op: Optional[PatchOperation] = None
    current_hunk: Optional[Hunk] = None

    def _flush_op():
        nonlocal current_op, current_hunk
        if current_hunk and current_op and current_op.operation == OperationType.UPDATE:
            current_op.hunks.append(current_hunk)
        if current_op:
            operations.append(current_op)
        current_op = None
        current_hunk = None

    for line in body:
        m = update_re.match(line)
        if m:
            _flush_op()
            current_op = PatchOperation(operation=OperationType.UPDATE, file_path=m.group(1).strip())
            continue

        m = add_re.match(line)
        if m:
            _flush_op()
            current_op = PatchOperation(operation=OperationType.ADD, file_path=m.group(1).strip(), content="")
            continue

        m = delete_re.match(line)
        if m:
            _flush_op()
            current_op = PatchOperation(operation=OperationType.DELETE, file_path=m.group(1).strip())
            operations.append(current_op)
            current_op = None
            continue

        m = move_re.match(line)
        if m:
            _flush_op()
            current_op = PatchOperation(
                operation=OperationType.MOVE,
                file_path=m.group(1).strip(),
                new_path=m.group(2).strip(),
            )
            operations.append(current_op)
            current_op = None
            continue

        if current_op is None:
            continue

        if current_op.operation == OperationType.ADD:
            # Lines starting with '+' are content; strip the prefix
            if line.startswith('+'):
                current_op.content = (current_op.content or "") + line[1:] + "\n"
            continue

        if current_op.operation == OperationType.UPDATE:
            if hunk_re.match(line):
                if current_hunk:
                    current_op.hunks.append(current_hunk)
                hint = line.strip('@').strip()
                current_hunk = Hunk(context_hint=hint if hint else None)
                continue

            if current_hunk is None:
                current_hunk = Hunk()

            if line.startswith(' ') or line == '':
                current_hunk.lines.append(HunkLine(prefix=' ', content=line[1:] if line else ''))
            elif line.startswith('-'):
                current_hunk.lines.append(HunkLine(prefix='-', content=line[1:]))
            elif line.startswith('+'):
                current_hunk.lines.append(HunkLine(prefix='+', content=line[1:]))

    _flush_op()
    return operations, None


# ---------------------------------------------------------------------------
# Fuzzy applicator
# ---------------------------------------------------------------------------

def _find_hunk_position(file_lines: List[str], hunk: Hunk, start: int = 0) -> int:
    """
    Find the best position in file_lines to apply a hunk using fuzzy context matching.
    Returns the line index, or -1 if not found.
    """
    context_lines = [hl.content for hl in hunk.lines if hl.prefix in (' ', '-')]
    if not context_lines:
        return start

    # Try exact match first, then fuzzy with increasing tolerance
    for tolerance in range(0, len(context_lines) + 1):
        for i in range(start, len(file_lines)):
            match_count = 0
            fi = i
            for cl in context_lines:
                if fi >= len(file_lines):
                    break
                if cl.rstrip() == file_lines[fi].rstrip():
                    match_count += 1
                    fi += 1
                elif tolerance > 0:
                    fi += 1  # skip one mismatch
            if match_count >= max(1, len(context_lines) - tolerance):
                return i
    return -1


def _apply_hunk(file_lines: List[str], hunk: Hunk, start_pos: int) -> Tuple[List[str], int]:
    """Apply a single hunk at start_pos. Returns (new_lines, new_cursor_pos)."""
    result = list(file_lines[:start_pos])
    cursor = start_pos

    for hl in hunk.lines:
        if hl.prefix == ' ':
            # context — advance cursor
            if cursor < len(file_lines):
                result.append(file_lines[cursor])
                cursor += 1
        elif hl.prefix == '-':
            # deletion — skip the line
            cursor += 1
        elif hl.prefix == '+':
            # insertion — add new line
            result.append(hl.content + "\n")

    # Append remaining
    result.extend(file_lines[cursor:])
    return result, len(result) - len(file_lines[cursor:])


# ---------------------------------------------------------------------------
# Atomic file write (from Hermes ShellFileOperations)
# ---------------------------------------------------------------------------

def _atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically using a temp file + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    old_mode: Optional[int] = None
    if path.exists():
        try:
            old_mode = stat.S_IMODE(path.stat().st_mode)
        except OSError:
            pass

    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".helm_write_")
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
        os.replace(tmp_path, path)
        if old_mode is not None:
            try:
                os.chmod(path, old_mode)
            except OSError:
                pass
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# High-level entry point
# ---------------------------------------------------------------------------

def apply_v4a_patch(patch_content: str, repo_path: str) -> Dict:
    """
    Parse and apply a V4A patch to a repository on disk.

    Args:
        patch_content: V4A format patch string
        repo_path: Absolute path to repository root

    Returns:
        {
            "success": bool,
            "files_changed": list[str],
            "errors": list[str],
            "operations_applied": int,
        }
    """
    operations, error = parse_v4a_patch(patch_content)
    if error:
        return {"success": False, "files_changed": [], "errors": [error], "operations_applied": 0}

    root = Path(repo_path)
    files_changed: List[str] = []
    errors: List[str] = []

    for op in operations:
        file_path = root / op.file_path
        try:
            if op.operation == OperationType.DELETE:
                if file_path.exists():
                    file_path.unlink()
                    files_changed.append(op.file_path)
                else:
                    errors.append(f"DELETE: file not found: {op.file_path}")

            elif op.operation == OperationType.ADD:
                _atomic_write(file_path, op.content or "")
                files_changed.append(op.file_path)

            elif op.operation == OperationType.MOVE:
                new_path = root / op.new_path
                new_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.rename(new_path)
                files_changed.append(op.new_path)

            elif op.operation == OperationType.UPDATE:
                if not file_path.exists():
                    errors.append(f"UPDATE: file not found: {op.file_path}")
                    continue

                content = file_path.read_text(encoding='utf-8', errors='replace')
                file_lines = content.splitlines(keepends=True)
                # Normalise to LF
                file_lines = [ln.replace('\r\n', '\n').replace('\r', '\n') for ln in file_lines]

                cursor = 0
                for hunk in op.hunks:
                    pos = _find_hunk_position(file_lines, hunk, start=cursor)
                    if pos == -1:
                        errors.append(
                            f"UPDATE {op.file_path}: could not find context for hunk "
                            f"'{hunk.context_hint or '(no hint)'}'"
                        )
                        break
                    file_lines, cursor = _apply_hunk(file_lines, hunk, pos)

                _atomic_write(file_path, "".join(file_lines))
                files_changed.append(op.file_path)

        except Exception as e:
            errors.append(f"{op.operation.value.upper()} {op.file_path}: {e}")

    return {
        "success": len(errors) == 0,
        "files_changed": files_changed,
        "errors": errors,
        "operations_applied": len(files_changed),
    }


async def apply_v4a_patch_async(patch_content: str, sandbox: Any, env_id: str) -> Dict:
    """
    Parse and apply a V4A patch to an isolated sandbox environment.

    Args:
        patch_content: V4A format patch string
        sandbox: ExecutionProvider instance
        env_id: Target environment ID

    Returns:
        {
            "success": bool,
            "files_changed": list[str],
            "errors": list[str],
            "operations_applied": int,
        }
    """
    operations, error = parse_v4a_patch(patch_content)
    if error:
        return {"success": False, "files_changed": [], "errors": [error], "operations_applied": 0}

    files_changed: List[str] = []
    errors: List[str] = []

    for op in operations:
        try:
            if op.operation == OperationType.DELETE:
                res = await sandbox.execute(env_id, f"rm -f {op.file_path}")
                if res.success:
                    files_changed.append(op.file_path)
                else:
                    errors.append(f"DELETE {op.file_path}: {res.stderr}")

            elif op.operation == OperationType.ADD:
                await sandbox.write_file(env_id, op.file_path, op.content or "")
                files_changed.append(op.file_path)

            elif op.operation == OperationType.MOVE:
                res = await sandbox.execute(env_id, f"mv {op.file_path} {op.new_path}")
                if res.success:
                    files_changed.append(op.new_path)
                else:
                    errors.append(f"MOVE {op.file_path} -> {op.new_path}: {res.stderr}")

            elif op.operation == OperationType.UPDATE:
                try:
                    content = await sandbox.read_file(env_id, op.file_path)
                except Exception as e:
                    errors.append(f"UPDATE: file not found in sandbox: {op.file_path} ({e})")
                    continue

                file_lines = content.splitlines(keepends=True)
                file_lines = [ln.replace('\r\n', '\n').replace('\r', '\n') for ln in file_lines]

                cursor = 0
                hunk_error = False
                for hunk in op.hunks:
                    pos = _find_hunk_position(file_lines, hunk, start=cursor)
                    if pos == -1:
                        errors.append(
                            f"UPDATE {op.file_path}: could not find context for hunk "
                            f"'{hunk.context_hint or '(no hint)'}'"
                        )
                        hunk_error = True
                        break
                    file_lines, cursor = _apply_hunk(file_lines, hunk, pos)

                if not hunk_error:
                    await sandbox.write_file(env_id, op.file_path, "".join(file_lines))
                    files_changed.append(op.file_path)

        except Exception as e:
            errors.append(f"{op.operation.value.upper()} {op.file_path}: {e}")

    return {
        "success": len(errors) == 0,
        "files_changed": files_changed,
        "errors": errors,
        "operations_applied": len(files_changed),
    }

