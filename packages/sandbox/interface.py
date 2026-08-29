from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ExecutionResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    command: str = ""

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    @property
    def combined_output(self) -> str:
        if self.stdout and self.stderr:
            return f"{self.stdout}\n{self.stderr}"
        return self.stdout or self.stderr or ""


class ExecutionProvider(ABC):
    """Abstract interface for sandbox code execution environments."""

    @abstractmethod
    async def create_environment(
        self,
        source_repo_path: str,
        env_id: Optional[str] = None,
        base_commit: Optional[str] = None,
    ) -> str:
        """Initialize an isolated environment with a copy/worktree of the repository."""
        pass

    @abstractmethod
    async def execute(
        self,
        env_id: str,
        command: str,
        cwd: Optional[str] = None,
        timeout_seconds: int = 60,
        env_vars: Optional[Dict[str, str]] = None,
    ) -> ExecutionResult:
        """Run a shell command inside the sandbox."""
        pass

    @abstractmethod
    async def read_file(self, env_id: str, relative_path: str) -> str:
        """Read a file from within the sandbox environment."""
        pass

    @abstractmethod
    async def write_file(self, env_id: str, relative_path: str, content: str) -> None:
        """Write a file into the sandbox environment."""
        pass

    @abstractmethod
    async def list_files(self, env_id: str, relative_dir: str = "") -> List[str]:
        """List all files in the sandbox workspace."""
        pass

    @abstractmethod
    async def get_git_diff(self, env_id: str) -> str:
        """Get git diff from the sandbox environment."""
        pass

    @abstractmethod
    async def destroy_environment(self, env_id: str) -> None:
        """Teardown and clean up the sandbox environment."""
        pass
