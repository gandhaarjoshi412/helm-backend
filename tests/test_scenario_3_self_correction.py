import pytest
from packages.agent.models.mock import MockModelProvider
from packages.agent.orchestrator.runner import HELMRunner
from packages.sandbox.local_process import LocalProcessExecutor
from packages.shared.schemas import TaskMode, TaskStatus


@pytest.mark.asyncio
async def test_scenario_3_self_correction(temp_repo):
    """
    Scenario 3: Self-correction.
    First edit produces syntax error or wrong logic -> test fails -> agent diagnoses error -> fixes code -> tests pass.
    """
    mock_model = MockModelProvider()

    # Step 1: Recon & Plan
    mock_model.enqueue_text("Inspecting calculator.")
    mock_model.enqueue_text('{"goal": "Fix multiply", "files_to_modify": ["src/calculator.py"], "tests_to_run": ["pytest"]}')

    # Step 2: First attempt (makes an erroneous implementation)
    mock_model.enqueue_tool_call(
        "edit_file",
        {
            "path": "src/calculator.py",
            "content": "def add(a, b): return a + b\ndef multiply(a, b): return a - b  # Wrong fix\n",
        },
    )

    # Step 3: Run tests -> this fails (assertion error)
    mock_model.enqueue_tool_call("run_tests", {"test_command": "pytest tests/test_calculator.py"})

    # Step 4: Self-correction (agent inspects failure and makes correct fix)
    mock_model.enqueue_tool_call(
        "edit_file",
        {
            "path": "src/calculator.py",
            "content": "def add(a: int, b: int) -> int:\n    return a + b\n\ndef multiply(a: int, b: int) -> int:\n    return a * b\n",
        },
    )

    # Step 5: Re-run tests -> passes
    mock_model.enqueue_tool_call("run_tests", {"test_command": "pytest tests/test_calculator.py"})

    # Step 6: Conclusion
    mock_model.enqueue_text("Diagnosed previous assertion failure. Corrected multiplication logic. Verified.")
    mock_model.enqueue_text("Review passed.")

    runner = HELMRunner(
        sandbox=LocalProcessExecutor(),
        model_provider=mock_model,
    )

    state = await runner.run(
        task_id="task_sc3",
        run_id="run_sc3",
        project_id="proj_test",
        prompt="Fix the calculation bug",
        repo_path=temp_repo,
        mode=TaskMode.AUTONOMOUS,
    )

    assert state.status == TaskStatus.COMPLETED
    assert state.last_test_result is not None
    assert state.last_test_result.passed
