import pytest
from pathlib import Path
from packages.agent.models.mock import MockModelProvider
from packages.agent.orchestrator.runner import HELMRunner
from packages.agent.workflows.state import WorkflowPhase
from packages.sandbox.local_process import LocalProcessExecutor
from packages.shared.schemas import TaskMode, TaskStatus


@pytest.mark.asyncio
async def test_scenario_2_multi_file_feature(temp_repo):
    """
    Scenario 2: Multi-file feature addition.
    Input: "Add power function to math utilities and add tests"
    Expected: Architecture analysis -> Plan -> Multiple file edits -> Tests -> Pass
    """
    mock_model = MockModelProvider()

    # Step 1: Recon & Plan
    mock_model.enqueue_text("Examined repository. Need to add power() in math module and corresponding test in test suite.")
    mock_model.enqueue_text(
        '{"goal": "Add power function", "files_to_modify": ["src/calculator.py", "tests/test_calculator.py"], "tests_to_run": ["pytest"]}'
    )

    # Step 2: Edit calculator.py
    mock_model.enqueue_tool_call(
        "edit_file",
        {
            "path": "src/calculator.py",
            "content": (
                "def add(a: int, b: int) -> int:\n    return a + b\n\n"
                "def multiply(a: int, b: int) -> int:\n    return a * b\n\n"
                "def power(base: int, exp: int) -> int:\n    return base ** exp\n"
            ),
        },
    )

    # Step 3: Edit test_calculator.py
    mock_model.enqueue_tool_call(
        "edit_file",
        {
            "path": "tests/test_calculator.py",
            "content": (
                "from src.calculator import add, multiply, power\n\n"
                "def test_add():\n    assert add(2, 3) == 5\n\n"
                "def test_multiply():\n    assert multiply(2, 3) == 6\n\n"
                "def test_power():\n    assert power(2, 3) == 8\n"
            ),
        },
    )

    # Step 4: Run tests
    mock_model.enqueue_tool_call("run_tests", {"test_command": "pytest tests/test_calculator.py"})

    # Step 5: Completion & Review
    mock_model.enqueue_text("Power function implemented and verified with tests.")
    mock_model.enqueue_text("Review passed: Multi-file changes are clean and adhere to project standards.")

    runner = HELMRunner(
        sandbox=LocalProcessExecutor(),
        model_provider=mock_model,
    )

    state = await runner.run(
        task_id="task_sc2",
        run_id="run_sc2",
        project_id="proj_test",
        prompt="Add power function to math utilities and add tests",
        repo_path=temp_repo,
        mode=TaskMode.AUTONOMOUS,
    )

    assert state.status == TaskStatus.COMPLETED
    assert len(state.files_modified) >= 2
    assert "src/calculator.py" in state.files_modified
    assert "tests/test_calculator.py" in state.files_modified
    assert state.last_test_result.passed
