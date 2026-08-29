from __future__ import annotations
import os
import shutil
import tempfile
from pathlib import Path
import git
import pytest
from httpx import ASGITransport, AsyncClient

# Set testing environment variables
os.environ["APP_ENV"] = "testing"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["MODEL_PROVIDER"] = "mock"
os.environ["SANDBOX_PROVIDER"] = "local_process"

from apps.api.app.database import Base, engine, init_db
from apps.api.app.main import app
from packages.sandbox.local_process import LocalProcessExecutor


@pytest.fixture
def temp_repo():
    """Creates a temporary sample Git repository with Python code and tests."""
    temp_dir = tempfile.mkdtemp(prefix="helm_test_repo_")
    repo_path = Path(temp_dir)

    # Initialize git repo
    repo = git.Repo.init(str(repo_path))

    # Create sample files
    src_dir = repo_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    tests_dir = repo_path / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)

    # Math module with a bug
    (src_dir / "calculator.py").write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n\ndef multiply(a: int, b: int) -> int:\n    return a + b  # Intentional bug\n"
    )

    # Test file
    (tests_dir / "test_calculator.py").write_text(
        "from src.calculator import add, multiply\n\ndef test_add():\n    assert add(2, 3) == 5\n\ndef test_multiply():\n    assert multiply(2, 3) == 6\n"
    )

    repo.git.add(A=True)
    repo.git.config("user.name", "Test User")
    repo.git.config("user.email", "test@helm.ai")
    repo.index.commit("Initial commit")

    yield str(repo_path)

    # Cleanup
    shutil.rmtree(temp_dir, ignore_errors=True)


from apps.api.app.config import settings

@pytest.fixture
async def api_client():
    """Provides async httpx client for FastAPI test endpoints."""
    await init_db()
    transport = ASGITransport(app=app)
    headers = {"X-API-Key": settings.HELM_API_KEY} if settings.HELM_API_KEY else {}
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
        yield client
