import pytest
from packages.agent.models.mock import MockModelProvider
from packages.agent.orchestrator.runner import HELMRunner
from packages.agent.workflows.state import WorkflowPhase
from packages.sandbox.local_process import LocalProcessExecutor
from packages.shared.schemas import TaskMode, TaskStatus


@pytest.mark.asyncio
async def test_scenario_1_simple_bug_fix(temp_repo):
    """
    Scenario 1: Simple bug fix.
    Input: "Fix the obvious bug in calculator.py"
    Expected: Recon -> Plan -> Edit -> Test -> Pass -> Diff
    """
    mock_model = MockModelProvider()

    # Step 1: Recon & Plan response
    mock_model.enqueue_text("Calculator module contains a multiply bug where addition is used instead of multiplication.")
    mock_model.enqueue_text('{"goal": "Fix multiply function in calculator.py", "files_to_modify": ["src/calculator.py"], "tests_to_run": ["pytest tests/test_calculator.py"]}')

    # Step 2: Agent edits file
    mock_model.enqueue_tool_call(
        "edit_file",
        {
            "path": "src/calculator.py",
            "content": "def add(a: int, b: int) -> int:\n    return a + b\n\ndef multiply(a: int, b: int) -> int:\n    return a * b\n",
        },
    )

    # Step 3: Agent runs tests
    mock_model.enqueue_tool_call("run_tests", {"test_command": "pytest tests/test_calculator.py"})

    # Step 4: Final conclusion
    mock_model.enqueue_text("Successfully fixed multiply() implementation. All tests pass.")
    mock_model.enqueue_text("Review passed: diff is minimal and correct.")

    events_received = []

    async def event_collector(event):
        events_received.append(event)

    runner = HELMRunner(
        sandbox=LocalProcessExecutor(),
        model_provider=mock_model,
        event_callback=event_collector,
    )

    state = await runner.run(
        task_id="task_sc1",
        run_id="run_sc1",
        project_id="proj_test",
        prompt="Fix the obvious bug in calculator.py",
        repo_path=temp_repo,
        mode=TaskMode.AUTONOMOUS,
    )

    assert state.status == TaskStatus.COMPLETED
    assert state.phase == WorkflowPhase.COMPLETED
    assert "src/calculator.py" in state.files_modified
    assert state.last_test_result is not None
    assert state.last_test_result.passed
    assert state.changeset is not None
    assert len(state.changeset.raw_diff) > 0
    assert any(e.type.value == "file_modified" for e in events_received)
    assert any(e.type.value == "test_completed" for e in events_received)
