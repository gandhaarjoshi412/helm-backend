from __future__ import annotations
import asyncio
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import git

from packages.sandbox.interface import ExecutionProvider, ExecutionResult
from packages.sandbox.local_process import LocalProcessExecutor
from packages.shared.errors import SandboxError
from packages.shared.logging import logger

try:
    import docker
    DOCKER_AVAILABLE = True
except ImportError:
    DOCKER_AVAILABLE = False


class LocalDockerExecutor(ExecutionProvider):
    """
    Manages isolated Docker containers for repository execution.
    Features resource caps, network isolation controls, and timeout enforcement.
    Falls back gracefully to LocalProcessExecutor if Docker daemon is not running.
    """

    def __init__(
        self,
        image_name: str = "helm-sandbox:latest",
        memory_limit: str = "2g",
        cpu_limit: float = 2.0,
        base_temp_dir: Optional[str] = None,
    ):
        self.image_name = image_name
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.base_temp_dir = Path(base_temp_dir or tempfile.gettempdir()) / "helm_sandboxes"
        self.base_temp_dir.mkdir(parents=True, exist_ok=True)
        self._active_containers: Dict[str, Any] = {}
        self._active_paths: Dict[str, Path] = {}
        self._fallback_executor: Optional[LocalProcessExecutor] = None
        self._docker_client = None

        if DOCKER_AVAILABLE:
            try:
                self._docker_client = docker.from_env()
                self._docker_client.ping()
            except Exception as e:
                logger.warning(f"Docker daemon unavailable ({e}). LocalDockerExecutor will use LocalProcess fallback.")
                self._docker_client = None

        if self._docker_client is None:
            self._fallback_executor = LocalProcessExecutor(base_temp_dir=str(self.base_temp_dir))

    async def create_environment(
        self,
        source_repo_path: str,
        env_id: Optional[str] = None,
        base_commit: Optional[str] = None,
    ) -> str:
        if self._docker_client is None or self._fallback_executor is not None:
            return await self._fallback_executor.create_environment(source_repo_path, env_id, base_commit)

        env_id = env_id or f"env_{os.urandom(6).hex()}"
        host_dir = self.base_temp_dir / env_id
        host_dir.mkdir(parents=True, exist_ok=True)

        source_path = Path(source_repo_path).resolve()
        try:
            if (source_path / ".git").exists():
                repo = git.Repo.clone_from(str(source_path), str(host_dir))
                if base_commit:
                    repo.git.checkout(base_commit)
            else:
                shutil.copytree(str(source_path), str(host_dir), dirs_exist_ok=True)
                repo = git.Repo.init(str(host_dir))
                repo.git.add(A=True)
                if repo.is_dirty(untracked_files=True):
                    repo.index.commit("Initial sandbox state")

            container = self._docker_client.containers.run(
                self.image_name,
                command="tail -f /dev/null",
                detach=True,
                name=f"helm_sandbox_{env_id}",
                volumes={str(host_dir): {"bind": "/workspace", "mode": "rw"}},
                working_dir="/workspace",
                mem_limit=self.memory_limit,
                nano_cpus=int(self.cpu_limit * 1e9),
                network_mode="bridge",
                remove=False,
            )
            self._active_containers[env_id] = container
            self._active_paths[env_id] = host_dir
            logger.info(f"Spawned Docker sandbox container {container.id[:12]} for {env_id}")
            return env_id
        except Exception as e:
            logger.warning(f"Docker container launch failed: {e}. Falling back to LocalProcessExecutor.")
            self._fallback_executor = LocalProcessExecutor(base_temp_dir=str(self.base_temp_dir))
            return await self._fallback_executor.create_environment(source_repo_path, env_id, base_commit)

    async def execute(
        self,
        env_id: str,
        command: str,
        cwd: Optional[str] = None,
        timeout_seconds: int = 60,
        env_vars: Optional[Dict[str, str]] = None,
    ) -> ExecutionResult:
        if self._fallback_executor is not None or env_id not in self._active_containers:
            return await (self._fallback_executor or LocalProcessExecutor(str(self.base_temp_dir))).execute(
                env_id, command, cwd, timeout_seconds, env_vars
            )

        container = self._active_containers[env_id]
        work_dir = f"/workspace/{cwd}" if cwd else "/workspace"
        start_time = time.monotonic()

        try:
            loop = asyncio.get_event_loop()
            exec_res = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: container.exec_run(
                        cmd=["/bin/sh", "-c", command],
                        workdir=work_dir,
                        environment=env_vars or {},
                        demux=True,
                    ),
                ),
                timeout=timeout_seconds,
            )
            duration_ms = int((time.monotonic() - start_time) * 1000)
            exit_code, (stdout_b, stderr_b) = exec_res
            return ExecutionResult(
                exit_code=exit_code or 0,
                stdout=(stdout_b or b"").decode("utf-8", errors="replace"),
                stderr=(stderr_b or b"").decode("utf-8", errors="replace"),
                duration_ms=duration_ms,
                timed_out=False,
                command=command,
            )
        except asyncio.TimeoutError:
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
                stderr=f"Docker exec error: {e}",
                duration_ms=duration_ms,
                timed_out=False,
                command=command,
            )

    async def read_file(self, env_id: str, relative_path: str) -> str:
        if self._fallback_executor is not None or env_id not in self._active_paths:
            return await (self._fallback_executor or LocalProcessExecutor(str(self.base_temp_dir))).read_file(
                env_id, relative_path
            )
        host_path = (self._active_paths[env_id] / relative_path).resolve()
        if not host_path.exists():
            raise SandboxError(f"File '{relative_path}' not found in sandbox.")
        return host_path.read_text(encoding="utf-8", errors="replace")

    async def write_file(self, env_id: str, relative_path: str, content: str) -> None:
        if self._fallback_executor is not None or env_id not in self._active_paths:
            return await (self._fallback_executor or LocalProcessExecutor(str(self.base_temp_dir))).write_file(
                env_id, relative_path, content
            )
        host_path = (self._active_paths[env_id] / relative_path).resolve()
        host_path.parent.mkdir(parents=True, exist_ok=True)
        host_path.write_text(content, encoding="utf-8")

    async def list_files(self, env_id: str, relative_dir: str = "") -> List[str]:
        if self._fallback_executor is not None or env_id not in self._active_paths:
            return await (self._fallback_executor or LocalProcessExecutor(str(self.base_temp_dir))).list_files(
                env_id, relative_dir
            )
        host_dir = self._active_paths[env_id] / relative_dir if relative_dir else self._active_paths[env_id]
        if not host_dir.exists():
            return []
        files = []
        for root, dirs, filenames in os.walk(host_dir):
            dirs[:] = [d for d in dirs if d not in [".git", "node_modules", "__pycache__", ".venv"]]
            for f in filenames:
                full_p = Path(root) / f
                rel = full_p.relative_to(self._active_paths[env_id])
                files.append(str(rel))
        return sorted(files)

    async def get_git_diff(self, env_id: str) -> str:
        if self._fallback_executor is not None or env_id not in self._active_paths:
            return await (self._fallback_executor or LocalProcessExecutor(str(self.base_temp_dir))).get_git_diff(env_id)
        try:
            repo = git.Repo(str(self._active_paths[env_id]))
            diff_text = repo.git.diff("HEAD")
            untracked = repo.untracked_files
            if untracked:
                diff_text += f"\n\nUntracked files:\n" + "\n".join(f"+ {u}" for u in untracked)
            return diff_text
        except Exception:
            return ""

    async def destroy_environment(self, env_id: str) -> None:
        if self._fallback_executor is not None:
            await self._fallback_executor.destroy_environment(env_id)
        if env_id in self._active_containers:
            container = self._active_containers.pop(env_id)
            try:
                container.stop(timeout=2)
                container.remove(force=True)
            except Exception as e:
                logger.warning(f"Error removing sandbox container: {e}")
        if env_id in self._active_paths:
            path = self._active_paths.pop(env_id)
            shutil.rmtree(str(path), ignore_errors=True)
