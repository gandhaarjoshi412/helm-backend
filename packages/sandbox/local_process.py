from __future__ import annotations
import asyncio
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional
import git

from packages.sandbox.interface import ExecutionProvider, ExecutionResult
from packages.shared.errors import SandboxError
from packages.shared.logging import logger


class LocalProcessExecutor(ExecutionProvider):
    """
    Executes code in isolated local temporary directories.
    Provides strict path containment, environment scrubbing, and Git safety.
    """

    def __init__(self, base_temp_dir: Optional[str] = None):
        self.base_temp_dir = Path(base_temp_dir or tempfile.gettempdir()) / "helm_sandboxes"
        self.base_temp_dir.mkdir(parents=True, exist_ok=True)
        self._active_envs: Dict[str, Path] = {}

    def _resolve_safe_path(self, env_id: str, relative_path: str) -> Path:
        if env_id not in self._active_envs:
            raise SandboxError(f"Environment '{env_id}' does not exist or has been destroyed.")
        env_dir = self._active_envs[env_id].resolve()
        target = (env_dir / relative_path).resolve()
        try:
            target.relative_to(env_dir)
        except ValueError:
            raise SandboxError(f"Path traversal detected: '{relative_path}' is outside sandbox root.")
        return target

    async def create_environment(
        self,
        source_repo_path: str,
        env_id: Optional[str] = None,
        base_commit: Optional[str] = None,
    ) -> str:
        env_id = env_id or f"env_{os.urandom(6).hex()}"
        env_dir = self.base_temp_dir / env_id
        if env_dir.exists():
            shutil.rmtree(str(env_dir), ignore_errors=True)
        env_dir.mkdir(parents=True, exist_ok=True)

        source_path = Path(source_repo_path).resolve()
        if not source_path.exists():
            raise SandboxError(f"Source repository path '{source_repo_path}' does not exist.")

        try:
            if (source_path / ".git").exists():
                repo = git.Repo.clone_from(str(source_path), str(env_dir))
                if base_commit:
                    repo.git.checkout(base_commit)

                # Copy working tree modifications (uncommitted / untracked files) to preserve user state
                for root, dirs, files in os.walk(source_path):
                    dirs[:] = [d for d in dirs if d not in [".git", ".venv", "__pycache__", ".pytest_cache", "node_modules", "dist", "build"]]
                    for f in files:
                        src_file = Path(root) / f
                        rel = src_file.relative_to(source_path)
                        dst_file = env_dir / rel
                        dst_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(src_file), str(dst_file))
            else:
                for root, dirs, files in os.walk(source_path):
                    dirs[:] = [d for d in dirs if d not in [".git", ".venv", "__pycache__", ".pytest_cache", "node_modules", "dist", "build"]]
                    for f in files:
                        src_file = Path(root) / f
                        rel = src_file.relative_to(source_path)
                        dst_file = env_dir / rel
                        dst_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(src_file), str(dst_file))
                repo = git.Repo.init(str(env_dir))
                repo.git.add(A=True)
                if repo.is_dirty(untracked_files=True):
                    repo.index.commit("Initial sandbox state")

        except Exception as e:
            shutil.rmtree(str(env_dir), ignore_errors=True)
            raise SandboxError(f"Failed to initialize sandbox environment: {e}")

        self._active_envs[env_id] = env_dir
        logger.info(f"Created LocalProcess sandbox environment: {env_id} at {env_dir}")
        return env_id

    async def execute(
        self,
        env_id: str,
        command: str,
        cwd: Optional[str] = None,
        timeout_seconds: int = 60,
        env_vars: Optional[Dict[str, str]] = None,
    ) -> ExecutionResult:
        if env_id not in self._active_envs:
            raise SandboxError(f"Environment '{env_id}' not found.")

        env_dir = self._active_envs[env_id]
        exec_cwd = self._resolve_safe_path(env_id, cwd) if cwd else env_dir

        existing_pythonpath = os.environ.get("PYTHONPATH", "")
        sandbox_pythonpath = f"{str(env_dir)}:{existing_pythonpath}" if existing_pythonpath else str(env_dir)

        current_py_bin = os.path.dirname(sys.executable)
        current_path = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
        full_path = f"{current_py_bin}:{current_path}" if current_py_bin not in current_path else current_path

        safe_env = {
            "PATH": full_path,
            "HOME": str(env_dir),
            "PYTHONPATH": sandbox_pythonpath,
            "LANG": "C.UTF-8",
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            **(env_vars or {}),
        }

        start_time = time.monotonic()
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=str(exec_cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=safe_env,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(), timeout=timeout_seconds
                )
                duration_ms = int((time.monotonic() - start_time) * 1000)
                return ExecutionResult(
                    exit_code=process.returncode or 0,
                    stdout=stdout_bytes.decode("utf-8", errors="replace"),
                    stderr=stderr_bytes.decode("utf-8", errors="replace"),
                    duration_ms=duration_ms,
                    timed_out=False,
                    command=command,
                )
            except asyncio.TimeoutError:
                try:
                    process.kill()
                    await process.wait()
                except Exception:
                    pass
                duration_ms = int((time.monotonic() - start_time) * 1000)
                return ExecutionResult(
                    exit_code=-1,
                    stdout="",
                    stderr=f"Command timed out after {timeout_seconds} seconds.",
                    duration_ms=duration_ms,
                    timed_out=True,
                    command=command,
                )
        except Exception as e:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            return ExecutionResult(
                exit_code=-1,
                stdout="",
                stderr=f"Execution error: {str(e)}",
                duration_ms=duration_ms,
                timed_out=False,
                command=command,
            )

    async def read_file(self, env_id: str, relative_path: str) -> str:
        target_path = self._resolve_safe_path(env_id, relative_path)
        if not target_path.exists():
            raise SandboxError(f"File '{relative_path}' not found in sandbox.")
        if not target_path.is_file():
            raise SandboxError(f"Path '{relative_path}' is not a regular file.")
        try:
            return target_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            raise SandboxError(f"Failed to read '{relative_path}': {e}")

    async def write_file(self, env_id: str, relative_path: str, content: str) -> None:
        target_path = self._resolve_safe_path(env_id, relative_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            target_path.write_text(content, encoding="utf-8")
        except Exception as e:
            raise SandboxError(f"Failed to write '{relative_path}': {e}")

    async def list_files(self, env_id: str, relative_dir: str = "") -> List[str]:
        target_dir = self._resolve_safe_path(env_id, relative_dir) if relative_dir else self._active_envs[env_id]
        if not target_dir.exists():
            return []
        files: List[str] = []
        env_root = self._active_envs[env_id]
        for root, dirs, filenames in os.walk(target_dir):
            dirs[:] = [d for d in dirs if d not in [".git", "node_modules", "__pycache__", ".venv", ".pytest_cache"]]
            for f in filenames:
                full_p = Path(root) / f
                rel = full_p.relative_to(env_root)
                files.append(str(rel))
        return sorted(files)

    async def get_git_diff(self, env_id: str) -> str:
        if env_id not in self._active_envs:
            raise SandboxError(f"Environment '{env_id}' not found.")
        env_dir = self._active_envs[env_id]
        try:
            repo = git.Repo(str(env_dir))
            diff_text = repo.git.diff("HEAD")
            untracked = repo.untracked_files
            if untracked:
                diff_text += f"\n\nUntracked files:\n" + "\n".join(f"+ {u}" for u in untracked)
            return diff_text
        except Exception:
            return ""

    async def destroy_environment(self, env_id: str) -> None:
        if env_id in self._active_envs:
            env_dir = self._active_envs.pop(env_id)
            shutil.rmtree(str(env_dir), ignore_errors=True)
            logger.info(f"Destroyed LocalProcess sandbox environment: {env_id}")
