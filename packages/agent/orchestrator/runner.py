from __future__ import annotations
import asyncio
import difflib
import json
import time
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional
import git

from packages.agent.models.base import ModelProvider, ModelResponse, ToolCallRequest
from packages.agent.policies.engine import PolicyEngine
from packages.agent.tools.base import ToolContext, ToolResult
from packages.agent.tools.registry import ToolRegistry
from packages.agent.workflows.specialists import PlanningSpecialist, ResearchSpecialist, ReviewSpecialist
from packages.agent.workflows.state import AgentRunState, WorkflowPhase
from packages.context_engine.graph.builder import CodeGraph
from packages.context_engine.retrieval.hybrid import HybridRetrievalEngine
from packages.sandbox.interface import ExecutionProvider
from packages.shared.errors import HELMError
from packages.shared.events import AgentEvent, EventType
from packages.shared.logging import logger
from packages.shared.schemas import ApprovalRequest, ApprovalStatus, ChangeSet, FileDiff, TaskMode, TaskStatus, TestResult

EventCallback = Callable[[AgentEvent], Coroutine[Any, Any, None]]


class HELMRunner:
    """
    HELM Autonomous Software Engineering Orchestrator.
    Manages the complete lifecycle: ASK -> RECON -> PLAN -> EXECUTE -> VERIFY -> SELF-CORRECT -> REVIEW -> SHIP.
    """

    def __init__(
        self,
        sandbox: ExecutionProvider,
        model_provider: ModelProvider,
        policy_engine: Optional[PolicyEngine] = None,
        tool_registry: Optional[ToolRegistry] = None,
        event_callback: Optional[EventCallback] = None,
    ):
        self.sandbox = sandbox
        self.model = model_provider
        self.policy = policy_engine or PolicyEngine()
        self.tools = tool_registry or ToolRegistry(self.policy)
        self.event_callback = event_callback
        self.researcher = ResearchSpecialist(self.model)
        self.planner = PlanningSpecialist(self.model)
        self.reviewer = ReviewSpecialist(self.model)

    async def emit_event(self, event: AgentEvent) -> None:
        if self.event_callback:
            try:
                await self.event_callback(event)
            except Exception as e:
                logger.warning(f"Error in event callback: {e}")

    async def run(
        self,
        task_id: str,
        run_id: str,
        project_id: str,
        prompt: str,
        repo_path: str,
        mode: TaskMode = TaskMode.AUTONOMOUS,
        base_commit: Optional[str] = None,
        max_iterations: int = 30,
    ) -> AgentRunState:
        state = AgentRunState(
            task_id=task_id,
            run_id=run_id,
            project_id=project_id,
            prompt=prompt,
            mode=mode,
            repo_path=repo_path,
            base_commit=base_commit,
            max_iterations=max_iterations,
        )

        try:
            # 1. INITIALIZING & ASK
            await self._phase_ask(state)

            # 2. RECONNAISSANCE
            await self._phase_recon(state)

            # 3. PLANNING
            await self._phase_plan(state)

            # 4. EXECUTION & VERIFICATION LOOP (with SELF-CORRECTION)
            await self._phase_execute_and_verify_loop(state)

            # 5. CODE REVIEW
            await self._phase_review(state)

            # 6. SHIP & DIFF FINALIZATION
            await self._phase_ship(state)

            return state

        except Exception as e:
            logger.error(f"Agent run failed: {e}", exc_info=True)
            state.phase = WorkflowPhase.FAILED
            state.status = TaskStatus.FAILED
            state.error_message = str(e)
            await self.emit_event(
                AgentEvent(
                    run_id=state.run_id,
                    task_id=state.task_id,
                    type=EventType.RUN_FAILED,
                    phase="failed",
                    title="Task Failed",
                    summary=str(e),
                    status="error",
                )
            )
            return state

    async def _phase_ask(self, state: AgentRunState) -> None:
        state.phase = WorkflowPhase.ASK
        state.status = TaskStatus.INITIALIZING

        await self.emit_event(
            AgentEvent(
                run_id=state.run_id,
                task_id=state.task_id,
                type=EventType.RUN_STARTED,
                phase="ask",
                title="Task Initialized",
                summary=f"Starting task: '{state.prompt}' (Mode: {state.mode.value})",
            )
        )

        # Create isolated sandbox environment
        env_id = await self.sandbox.create_environment(
            source_repo_path=state.repo_path,
            env_id=f"env_{state.run_id}",
            base_commit=state.base_commit,
        )
        state.env_id = env_id

        # Git Safety Check: inspect working tree
        status_res = await self.sandbox.execute(env_id, "git status --short", timeout_seconds=15)
        if status_res.stdout.strip():
            dirty = [line.strip() for line in status_res.stdout.splitlines() if line.strip()]
            state.dirty_files_detected = dirty
            logger.info(f"Preserved {len(dirty)} pre-existing uncommitted files in repository.")
            await self.emit_event(
                AgentEvent(
                    run_id=state.run_id,
                    task_id=state.task_id,
                    type=EventType.AGENT_MESSAGE,
                    phase="ask",
                    title="Git Safety Checked",
                    summary=f"Detected and preserved {len(dirty)} existing modifications.",
                    status="info",
                )
            )

    async def _phase_recon(self, state: AgentRunState) -> None:
        state.phase = WorkflowPhase.RECON
        state.status = TaskStatus.RECON

        await self.emit_event(
            AgentEvent(
                run_id=state.run_id,
                task_id=state.task_id,
                type=EventType.PHASE_STARTED,
                phase="recon",
                title="Reconnaissance Started",
                summary="Analyzing repository architecture, symbols, and dependencies.",
            )
        )

        # Build context graph & hybrid retrieval on host repo
        code_graph = CodeGraph(state.repo_path)
        code_graph.build_graph()
        retrieval = HybridRetrievalEngine(state.repo_path, code_graph=code_graph)

        retrieved = retrieval.retrieve(state.prompt, max_files=10)
        recon_result = await self.researcher.analyze_context(state.prompt, retrieved)

        await self.emit_event(
            AgentEvent(
                run_id=state.run_id,
                task_id=state.task_id,
                type=EventType.CONTEXT_SEARCH,
                phase="recon",
                title="Context Retrieved",
                summary=f"Identified {len(retrieved.relevant_files)} relevant files: {', '.join(retrieved.relevant_files[:5])}",
                metadata={"relevant_files": retrieved.relevant_files},
            )
        )

        await self.emit_event(
            AgentEvent(
                run_id=state.run_id,
                task_id=state.task_id,
                type=EventType.PHASE_COMPLETED,
                phase="recon",
                title="Reconnaissance Completed",
                summary=recon_result.get("summary", ""),
            )
        )

    async def _phase_plan(self, state: AgentRunState) -> None:
        state.phase = WorkflowPhase.PLAN
        state.status = TaskStatus.PLANNING

        await self.emit_event(
            AgentEvent(
                run_id=state.run_id,
                task_id=state.task_id,
                type=EventType.PHASE_STARTED,
                phase="plan",
                title="Planning Implementation",
                summary="Generating structured plan and test strategy.",
            )
        )

        # Retrieve recon context
        code_graph = CodeGraph(state.repo_path)
        code_graph.build_graph()
        retrieval = HybridRetrievalEngine(state.repo_path, code_graph=code_graph)
        retrieved = retrieval.retrieve(state.prompt, max_files=10)
        recon_result = {"summary": retrieved.summary, "relevant_files": retrieved.relevant_files}

        plan = await self.planner.create_plan(state.prompt, recon_result)
        state.plan = plan

        await self.emit_event(
            AgentEvent(
                run_id=state.run_id,
                task_id=state.task_id,
                type=EventType.PHASE_COMPLETED,
                phase="plan",
                title="Plan Formulated",
                summary=f"Plan: Modify {len(plan.files_to_modify)} files, run verification '{', '.join(plan.tests_to_run)}'",
                metadata=plan.model_dump(),
            )
        )

    async def _phase_execute_and_verify_loop(self, state: AgentRunState) -> None:
        state.phase = WorkflowPhase.EXECUTE
        state.status = TaskStatus.EXECUTING

        code_graph = CodeGraph(state.repo_path)
        code_graph.build_graph()
        retrieval = HybridRetrievalEngine(state.repo_path, code_graph=code_graph)

        tool_context = ToolContext(
            env_id=state.env_id or "",
            sandbox=self.sandbox,
            repo_path=state.repo_path,
            code_graph=code_graph,
            retrieval_engine=retrieval,
            task_id=state.task_id,
            run_id=state.run_id,
        )

        openai_tools = self.tools.to_openai_tools()

        messages: List[Dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "You are HELM, an autonomous software engineering agent.\n"
                    f"Repository: {state.repo_path}\n"
                    f"Task: {state.prompt}\n\n"
                    "## Plan\n"
                    f"{json.dumps(state.plan.model_dump() if state.plan else {}, indent=2)}\n\n"
                    "## Instructions\n"
                    "- Use tools to explore, edit, and verify code.\n"
                    "- For edits, prefer apply_patch with V4A format (*** Begin Patch / *** End Patch).\n"
                    "- Use web_search + web_extract if you need docs for an unfamiliar library or API.\n"
                    "- Run run_tests after every change to verify correctness.\n"
                    "- Self-correct if tests fail: diagnose, fix, re-verify.\n"
                    "- When the task is fully complete and tests pass, output exactly:\n"
                    "    HELM_TASK_COMPLETE: <one-line summary of what was done>\n"
                    "  This is your only termination signal — do not stop before outputting it."
                ),
            },
            {
                "role": "user",
                "content": f"Begin: {state.prompt}",
            },
        ]

        iteration = 0
        while iteration < state.max_iterations:
            iteration += 1
            state.iteration = iteration

            await self.emit_event(
                AgentEvent(
                    run_id=state.run_id,
                    task_id=state.task_id,
                    type=EventType.PHASE_STARTED if iteration == 1 else EventType.SELF_CORRECTION,
                    phase="execute" if iteration == 1 else "self_correct",
                    title=f"Execution Cycle {iteration}/{state.max_iterations}",
                    summary=f"Running autonomous step {iteration}...",
                )
            )

            # Generate next agent action
            response: ModelResponse = await self.model.generate(
                messages=messages,
                tools=openai_tools,
                temperature=0.1,
            )

            # Emit Thinking Process if reasoning model returned chain of thought
            if response.reasoning_summary and response.reasoning_summary.strip():
                await self.emit_event(
                    AgentEvent(
                        run_id=state.run_id,
                        task_id=state.task_id,
                        type=EventType.AGENT_MESSAGE,
                        phase="execute",
                        title="Thinking Process",
                        summary=f"💭 Thinking:\n{response.reasoning_summary}",
                        metadata={"content": f"💭 **Thinking Process:**\n\n{response.reasoning_summary}"},
                    )
                )

            if response.content and response.content.strip():
                await self.emit_event(
                    AgentEvent(
                        run_id=state.run_id,
                        task_id=state.task_id,
                        type=EventType.AGENT_MESSAGE,
                        phase="execute",
                        title="Agent Response",
                        summary=response.content,
                        metadata={"content": response.content},
                    )
                )

            # Check HELM_TASK_COMPLETE in the assistant's response text (with or without tool calls)
            if response.content and "HELM_TASK_COMPLETE" in response.content:
                logger.info("HELM_TASK_COMPLETE signal in response content — stopping iteration loop.")
                messages.append({"role": "assistant", "content": response.content})
                break

            # If no tool calls, check completion signal or run verification
            if not response.has_tool_calls:
                content = response.content or ""
                messages.append({"role": "assistant", "content": content})

                # Trigger verification check
                verify_res = await self._run_verification(state, tool_context)
                if verify_res.passed:
                    break
                else:
                    # Feed test failure back for self-correction
                    messages.append(
                        {
                            "role": "user",
                            "content": f"Verification failed (exit code {verify_res.exit_code}):\n{verify_res.output}\nDiagnose and fix.",
                        }
                    )
                    continue

            # Process tool calls
            assistant_msg: Dict[str, Any] = {
                "role": "assistant",
                "content": response.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in response.tool_calls
                ],
            }
            messages.append(assistant_msg)

            # Flag for immediate exit from both the tool loop and iteration loop
            task_complete = False

            for tc in response.tool_calls:
                await self.emit_event(
                    AgentEvent(
                        run_id=state.run_id,
                        task_id=state.task_id,
                        type=EventType.TOOL_STARTED,
                        phase="execute",
                        title=f"Tool: {tc.name}",
                        summary=f"Executing {tc.name}",
                        tool_name=tc.name,
                        tool_input=tc.arguments,
                    )
                )

                start_tool = time.monotonic()
                tool_res: ToolResult = await self.tools.execute_tool(tc.name, tc.arguments, tool_context)
                tool_duration_ms = int((time.monotonic() - start_tool) * 1000)

                # Record file changes
                if tool_res.success:
                    new_modified = []
                    if tc.name == "edit_file":
                        file_p = tc.arguments.get("path", "")
                        if file_p:
                            new_modified.append(file_p)
                    elif tc.name == "apply_patch":
                        new_modified.extend(tool_res.metadata.get("files_changed", []))
                        if not new_modified and tc.arguments.get("path"):
                            new_modified.append(tc.arguments["path"])

                    for f in new_modified:
                        if f and f not in state.files_modified:
                            state.files_modified.append(f)
                            await self.emit_event(
                                AgentEvent(
                                    run_id=state.run_id,
                                    task_id=state.task_id,
                                    type=EventType.FILE_MODIFIED,
                                    phase="execute",
                                    title=f"Modified {f}",
                                    summary=f"Applied changes to {f}",
                                )
                            )

                # Record test results — and exit immediately if tests passed
                if tc.name == "run_tests":
                    state.last_test_result = TestResult(
                        command=tc.arguments.get("test_command", "pytest"),
                        passed=tool_res.success,
                        exit_code=tool_res.metadata.get("exit_code", 0 if tool_res.success else 1),
                        output=str(tool_res.output),
                        duration_ms=tool_res.metadata.get("duration_ms", tool_duration_ms),
                        tests_passed=tool_res.metadata.get("tests_passed", 1 if tool_res.success else 0),
                        tests_failed=tool_res.metadata.get("tests_failed", 0 if tool_res.success else 1),
                    )
                    await self.emit_event(
                        AgentEvent(
                            run_id=state.run_id,
                            task_id=state.task_id,
                            type=EventType.TEST_COMPLETED,
                            phase="verify",
                            title="Test Run Finished",
                            summary=f"Tests: {'PASSED' if tool_res.success else 'FAILED'}",
                            status="success" if tool_res.success else "error",
                            metadata=tool_res.metadata,
                        )
                    )
                    if tool_res.success:
                        task_complete = True

                await self.emit_event(
                    AgentEvent(
                        run_id=state.run_id,
                        task_id=state.task_id,
                        type=EventType.TOOL_COMPLETED,
                        phase="execute",
                        title=f"Completed {tc.name}",
                        summary=str(tool_res.output)[:200] if tool_res.success else f"Error: {tool_res.error}",
                        tool_name=tc.name,
                        tool_output={"success": tool_res.success, "output": str(tool_res.output)[:500]},
                        duration_ms=tool_duration_ms,
                        status="success" if tool_res.success else "error",
                    )
                )

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(
                            {"success": tool_res.success, "output": tool_res.output, "error": tool_res.error}
                        ),
                    }
                )

                # Also catch HELM_TASK_COMPLETE in tool output text
                if "HELM_TASK_COMPLETE" in str(tool_res.output):
                    logger.info("HELM_TASK_COMPLETE detected in tool output — stopping immediately.")
                    task_complete = True

                # Exit the tool loop as soon as task is done
                if task_complete:
                    break

            # Exit the iteration loop as soon as task is done
            if task_complete:
                logger.info(f"Task completed at cycle {iteration}/{state.max_iterations} — stopping early.")
                break

    async def _run_verification(self, state: AgentRunState, context: ToolContext) -> TestResult:
        state.phase = WorkflowPhase.VERIFY
        state.status = TaskStatus.VERIFYING

        test_cmd = (state.plan.tests_to_run[0] if state.plan and state.plan.tests_to_run else "pytest")
        await self.emit_event(
            AgentEvent(
                run_id=state.run_id,
                task_id=state.task_id,
                type=EventType.VERIFICATION_STARTED,
                phase="verify",
                title="Running Verification",
                summary=f"Executing test command: '{test_cmd}'",
            )
        )

        res = await self.tools.execute_tool("run_tests", {"test_command": test_cmd}, context)
        test_res = TestResult(
            command=test_cmd,
            passed=res.success,
            exit_code=res.metadata.get("exit_code", 0 if res.success else 1),
            output=str(res.output),
            duration_ms=res.metadata.get("duration_ms", 100),
            tests_passed=res.metadata.get("tests_passed", 1 if res.success else 0),
            tests_failed=res.metadata.get("tests_failed", 0 if res.success else 1),
        )
        state.last_test_result = test_res

        await self.emit_event(
            AgentEvent(
                run_id=state.run_id,
                task_id=state.task_id,
                type=EventType.VERIFICATION_COMPLETED,
                phase="verify",
                title="Verification Complete",
                summary=f"Verification status: {'PASSED' if test_res.passed else 'FAILED'}",
                status="success" if test_res.passed else "error",
                metadata=test_res.model_dump(),
            )
        )
        return test_res

    async def _phase_review(self, state: AgentRunState) -> None:
        state.phase = WorkflowPhase.REVIEW
        state.status = TaskStatus.REVIEWING

        raw_diff = await self.sandbox.get_git_diff(state.env_id or "")

        await self.emit_event(
            AgentEvent(
                run_id=state.run_id,
                task_id=state.task_id,
                type=EventType.REVIEW_STARTED,
                phase="review",
                title="Code Review",
                summary="Performing independent diff and quality review.",
            )
        )

        review_res = await self.reviewer.review_diff(state.prompt, raw_diff, state.last_test_result)
        state.review_comments = [review_res.get("summary", "")]

        await self.emit_event(
            AgentEvent(
                run_id=state.run_id,
                task_id=state.task_id,
                type=EventType.REVIEW_COMPLETED,
                phase="review",
                title="Review Completed",
                summary=review_res.get("summary", "Diff review passed."),
            )
        )

    async def _phase_ship(self, state: AgentRunState) -> None:
        state.phase = WorkflowPhase.SHIP

        # Generate structured ChangeSet
        raw_diff = await self.sandbox.get_git_diff(state.env_id or "")

        # If files_modified is empty but diff exists, extract file paths from git diff
        if not state.files_modified and raw_diff:
            import re
            found_paths = re.findall(r'^\+\+\+ b/(.+)$', raw_diff, re.MULTILINE)
            untracked_section = raw_diff.split('Untracked files:')[-1] if 'Untracked files:' in raw_diff else ''
            untracked_paths = [ln.replace('+ ', '').strip() for ln in untracked_section.splitlines() if ln.startswith('+ ')]
            state.files_modified = list(dict.fromkeys(found_paths + untracked_paths))

        diff_items: List[FileDiff] = []
        for file_p in state.files_modified:
            diff_items.append(
                FileDiff(
                    path=file_p,
                    status="modified",
                    additions=raw_diff.count("\n+"),
                    deletions=raw_diff.count("\n-"),
                    diff_content=raw_diff,
                )
            )

        state.changeset = ChangeSet(
            task_id=state.task_id,
            run_id=state.run_id,
            files_changed=state.files_modified,
            files_added=state.files_created,
            total_additions=raw_diff.count("\n+"),
            total_deletions=raw_diff.count("\n-"),
            diffs=diff_items,
            raw_diff=raw_diff,
        )

        # Check if task requested approval for push / PR
        if "push" in state.prompt.lower() or "pr" in state.prompt.lower() or "pull request" in state.prompt.lower():
            rule = self.policy.evaluate_tool("push_branch", {"branch_name": "feature/helm-update"})
            if rule.requires_approval:
                state.phase = WorkflowPhase.WAITING_FOR_APPROVAL
                state.status = TaskStatus.WAITING_FOR_APPROVAL
                state.approval_id = f"appr_{state.task_id}"

                await self.emit_event(
                    AgentEvent(
                        run_id=state.run_id,
                        task_id=state.task_id,
                        type=EventType.APPROVAL_REQUIRED,
                        phase="waiting_for_approval",
                        title="Approval Required",
                        summary="Agent completed task and requested approval to push branch to GitHub.",
                        metadata={"action_type": "git_push", "approval_id": state.approval_id},
                    )
                )
                return

        state.phase = WorkflowPhase.COMPLETED
        state.status = TaskStatus.COMPLETED

        # If informational inquiry or review comments exist, synthesize a clear final answer
        final_answer = ""
        if len(state.files_modified) == 0 or any(w in state.prompt.lower() for w in ["what", "how", "explain", "summarize", "tell me", "why", "describe"]):
            try:
                # Ask model for concise explanation of findings
                ans_resp = await self.model.generate(
                    messages=[
                        {"role": "system", "content": "You are HELM, an autonomous software engineering agent. Provide a clear, structured, and informative answer/summary to the user prompt based on your codebase analysis."},
                        {"role": "user", "content": f"User Prompt: {state.prompt}\n\nRepository: {state.repo_path}\nPlan Summary: {state.plan.architecture_summary if state.plan else ''}\nReview Comments: {', '.join(state.review_comments)}"}
                    ],
                    temperature=0.2,
                )
                if ans_resp.content:
                    final_answer = ans_resp.content.strip()
            except Exception:
                pass

        if not final_answer and state.review_comments:
            final_answer = "\n".join(state.review_comments)

        if final_answer:
            await self.emit_event(
                AgentEvent(
                    run_id=state.run_id,
                    task_id=state.task_id,
                    type=EventType.AGENT_MESSAGE,
                    phase="completed",
                    title="Agent Answer & Summary",
                    summary=final_answer,
                    metadata={"content": final_answer, "is_final_answer": True},
                )
            )

        await self.emit_event(
            AgentEvent(
                run_id=state.run_id,
                task_id=state.task_id,
                type=EventType.RUN_COMPLETED,
                phase="completed",
                title="Task Completed",
                summary=f"Successfully resolved task: {len(state.files_modified)} files changed, tests passed.",
                status="success",
                metadata=state.changeset.model_dump() if state.changeset else {},
            )
        )
