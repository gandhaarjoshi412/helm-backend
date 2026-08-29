import pytest
from packages.agent.models.mock import MockModelProvider
from packages.agent.orchestrator.runner import HELMRunner
from packages.agent.policies.engine import PolicyConfig, PolicyEngine
from packages.agent.workflows.state import WorkflowPhase
from packages.sandbox.local_process import LocalProcessExecutor
from packages.shared.schemas import TaskMode, TaskStatus


@pytest.mark.asyncio
async def test_scenario_5_approval_gating(temp_repo):
    """
    Scenario 5: Approval gating.
    Prompt asks to push branch -> Agent finishes changes and verification -> Gated by policy -> Waits for human approval.
    """
    mock_model = MockModelProvider()
    mock_model.enqueue_text("Inspecting calculator.")
    mock_model.enqueue_text('{"goal": "Fix bug and push branch", "files_to_modify": ["src/calculator.py"], "tests_to_run": ["pytest"]}')
    mock_model.enqueue_tool_call(
        "edit_file",
        {
            "path": "src/calculator.py",
            "content": "def add(a: int, b: int) -> int:\n    return a + b\n\ndef multiply(a: int, b: int) -> int:\n    return a * b\n",
        },
    )
    mock_model.enqueue_tool_call("run_tests", {"test_command": "pytest tests/test_calculator.py"})
    mock_model.enqueue_text("Tests pass. Ready to push.")
    mock_model.enqueue_text("Review passed.")

    policy = PolicyEngine(PolicyConfig(git_push_requires_approval=True))

    runner = HELMRunner(
        sandbox=LocalProcessExecutor(),
        model_provider=mock_model,
        policy_engine=policy,
    )

    state = await runner.run(
        task_id="task_sc5",
        run_id="run_sc5",
        project_id="proj_test",
        prompt="Fix calculation bug and push branch feature/fix-math",
        repo_path=temp_repo,
        mode=TaskMode.AUTONOMOUS,
    )

    # Verification: should be waiting for human approval
    assert state.status == TaskStatus.WAITING_FOR_APPROVAL
    assert state.phase == WorkflowPhase.WAITING_FOR_APPROVAL
    assert state.approval_id is not None
