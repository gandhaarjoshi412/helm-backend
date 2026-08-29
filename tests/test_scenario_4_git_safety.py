import pytest
from pathlib import Path
from packages.agent.models.mock import MockModelProvider
from packages.agent.orchestrator.runner import HELMRunner
from packages.sandbox.local_process import LocalProcessExecutor
from packages.shared.schemas import TaskMode, TaskStatus


@pytest.mark.asyncio
async def test_scenario_4_git_safety_dirty_worktree(temp_repo):
    """
    Scenario 4: Git safety.
    Start with uncommitted user changes in the repository -> agent detects dirty state -> preserves user changes.
    """
    # Create uncommitted user work in the repo
    user_file = Path(temp_repo) / "src" / "user_draft.txt"
    user_file.write_text("User WIP notes not yet committed.")

    mock_model = MockModelProvider()
    mock_model.enqueue_text("Inspecting repo.")
    mock_model.enqueue_text('{"goal": "Check code", "files_to_modify": [], "tests_to_run": ["pytest"]}')
    mock_model.enqueue_tool_call("run_tests", {"test_command": "pytest tests/test_calculator.py"})
    mock_model.enqueue_text("Done.")
    mock_model.enqueue_text("Review.")

    runner = HELMRunner(
        sandbox=LocalProcessExecutor(),
        model_provider=mock_model,
    )

    state = await runner.run(
        task_id="task_sc4",
        run_id="run_sc4",
        project_id="proj_test",
        prompt="Inspect code safety",
        repo_path=temp_repo,
        mode=TaskMode.AUTONOMOUS,
    )

    # Verify that dirty files were detected and logged
    assert len(state.dirty_files_detected) > 0
    # Verify original user file was not destroyed
    assert user_file.exists()
    assert "User WIP notes" in user_file.read_text()
