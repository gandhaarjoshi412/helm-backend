from __future__ import annotations
import os
from typing import Optional
from packages.sandbox.interface import ExecutionProvider
from packages.sandbox.local_docker import LocalDockerExecutor
from packages.sandbox.local_process import LocalProcessExecutor


def get_execution_provider(provider_type: Optional[str] = None) -> ExecutionProvider:
    provider = provider_type or os.getenv("SANDBOX_PROVIDER", "local_process")
    if provider == "local_process":
        return LocalProcessExecutor()
    elif provider == "local_docker":
        return LocalDockerExecutor()
    else:
        return LocalProcessExecutor()
