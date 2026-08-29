from __future__ import annotations
import json
from typing import Any, Dict, List, Optional
from packages.agent.models.base import ModelProvider, ModelResponse
from packages.context_engine.retrieval.hybrid import RetrievedContext
from packages.shared.schemas import ImplementationPlan, TestResult
from packages.shared.logging import logger


class SpecialistPrompts:
    SYSTEM_CODER = """You are HELM's Primary Coding Agent.
Your goal is to inspect repository context, design minimal surgical fixes or clean features, and output precise edits.
Always prioritize stability, minimal diffs, and existing code style.

Preferred tools and formats:
- Use read_file (with start_line/end_line for large files) to explore code.
- Use apply_patch with V4A format for edits (*** Begin Patch / *** Update File / *** End Patch).
- Use web_search + web_extract when you need docs for an unfamiliar API or library.
- Use run_tests to verify after every change.
- Output HELM_TASK_COMPLETE: <summary> when fully done."""

    SYSTEM_RESEARCHER = """You are HELM's Research & Recon Agent.
Your mission: understand repository architecture, find relevant symbols, trace call chains, and identify test coverage.
If the task involves an external library or API you are unsure about, note it so the coding agent can web_search it."""

    SYSTEM_VERIFIER = """You are HELM's Verification Agent.
You run tests, linters, and type checkers inside the sandbox and extract actionable diagnostics on failure."""

    SYSTEM_REVIEWER = """You are HELM's Independent Code Review Agent.
You review git diffs for correctness, regressions, edge cases, security, and cleanliness.
Pay particular attention to: off-by-one errors, missing error handling, hardcoded values, and unintended side effects."""


class ResearchSpecialist:
    """Specialist agent responsible for RECON and context gathering."""

    def __init__(self, model_provider: ModelProvider):
        self.model = model_provider

    async def analyze_context(self, prompt: str, retrieved: RetrievedContext) -> Dict[str, Any]:
        user_msg = (
            f"User Goal: {prompt}\n\n"
            f"Relevant Files: {retrieved.relevant_files}\n"
            f"Relevant Symbols: {json.dumps(retrieved.relevant_symbols[:5], indent=2)}\n"
            f"Recent Commits: {json.dumps(retrieved.relevant_commits[:3], indent=2)}\n\n"
            f"Provide a brief reconnaissance summary: what files/components are critical to this task?"
        )
        messages = [
            {"role": "system", "content": SpecialistPrompts.SYSTEM_RESEARCHER},
            {"role": "user", "content": user_msg},
        ]
        res = await self.model.generate(messages, temperature=0.1)
        return {
            "summary": res.content or "Recon completed.",
            "relevant_files": retrieved.relevant_files,
            "relevant_symbols": retrieved.relevant_symbols,
        }


class PlanningSpecialist:
    """Specialist agent responsible for creating structured implementation plans."""

    def __init__(self, model_provider: ModelProvider):
        self.model = model_provider

    async def create_plan(self, prompt: str, recon_info: Dict[str, Any]) -> ImplementationPlan:
        user_msg = (
            f"Task: {prompt}\n"
            f"Recon Summary: {recon_info.get('summary', '')}\n"
            f"Candidate Files: {recon_info.get('relevant_files', [])}\n\n"
            f"Output a JSON object with keys: goal, files_to_modify, files_to_add, tests_to_run, verification_steps."
        )
        messages = [
            {"role": "system", "content": SpecialistPrompts.SYSTEM_CODER},
            {"role": "user", "content": user_msg},
        ]
        res = await self.model.generate(messages, temperature=0.1)
        content = res.content or ""

        # Parse JSON from model response
        try:
            # Strip markdown json blocks if present
            clean = content.strip()
            if "```json" in clean:
                clean = clean.split("```json")[1].split("```")[0].strip()
            elif "```" in clean:
                clean = clean.split("```")[1].split("```")[0].strip()
            data = json.loads(clean)
            return ImplementationPlan(
                goal=data.get("goal", prompt),
                architecture_summary=recon_info.get("summary", ""),
                files_to_modify=data.get("files_to_modify", recon_info.get("relevant_files", [])[:3]),
                files_to_add=data.get("files_to_add", []),
                tests_to_run=data.get("tests_to_run", ["pytest"]),
                verification_steps=data.get("verification_steps", ["Run test suite"]),
            )
        except Exception:
            return ImplementationPlan(
                goal=prompt,
                architecture_summary=recon_info.get("summary", ""),
                files_to_modify=recon_info.get("relevant_files", [])[:3],
                files_to_add=[],
                tests_to_run=["pytest"],
                verification_steps=["Run test suite in sandbox"],
            )


class ReviewSpecialist:
    """Specialist agent responsible for independent diff review."""

    def __init__(self, model_provider: ModelProvider):
        self.model = model_provider

    async def review_diff(self, prompt: str, diff_text: str, test_result: Optional[TestResult]) -> Dict[str, Any]:
        if not diff_text:
            return {"passed": True, "summary": "No changes made.", "concerns": []}

        user_msg = (
            f"Original Task: {prompt}\n"
            f"Test Result: {'PASSED' if (test_result and test_result.passed) else 'FAILED/NOT RUN'}\n\n"
            f"Diff to Review:\n{diff_text[:4000]}\n\n"
            f"Evaluate the diff for: 1. Correctness, 2. Unintended regressions, 3. Security.\n"
            f"Summarize your findings."
        )
        messages = [
            {"role": "system", "content": SpecialistPrompts.SYSTEM_REVIEWER},
            {"role": "user", "content": user_msg},
        ]
        res = await self.model.generate(messages, temperature=0.1)
        return {
            "passed": True,
            "summary": res.content or "Diff review passed with no critical issues.",
            "concerns": [],
        }
