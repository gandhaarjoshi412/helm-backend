import pytest
import os
from packages.agent.models.mock import MockModelProvider
from packages.agent.policies.engine import PolicyEngine
from packages.agent.tools.registry import ToolRegistry
from packages.context_engine.parser.ast_parser import ASTParser
from packages.context_engine.graph.builder import CodeGraph
from packages.context_engine.retrieval.hybrid import HybridRetrievalEngine
from packages.sandbox.local_process import LocalProcessExecutor


@pytest.mark.asyncio
async def test_mock_model_provider():
    mock = MockModelProvider()
    mock.enqueue_text("Hello from HELM")
    res = await mock.generate([{"role": "user", "content": "Hi"}])
    assert res.content == "Hello from HELM"
    assert len(mock.call_history) == 1


@pytest.mark.asyncio
async def test_sandbox_local_process(temp_repo):
    sandbox = LocalProcessExecutor()
    env_id = await sandbox.create_environment(temp_repo)
    assert env_id

    # Test file reading
    content = await sandbox.read_file(env_id, "src/calculator.py")
    assert "def add" in content

    # Test file writing
    await sandbox.write_file(env_id, "src/calculator.py", "def add(a, b): return a + b\n")
    content_after = await sandbox.read_file(env_id, "src/calculator.py")
    assert content_after.strip() == "def add(a, b): return a + b"

    # Test command execution
    exec_res = await sandbox.execute(env_id, "python3 -c 'print(1+1)'")
    assert exec_res.success
    assert exec_res.stdout.strip() == "2"

    # Test diff
    diff = await sandbox.get_git_diff(env_id)
    assert len(diff) > 0

    await sandbox.destroy_environment(env_id)


def test_ast_parser_python():
    parser = ASTParser()
    py_code = """
import os
from math import sqrt

class Calculator:
    def compute(self, x):
        return sqrt(x)

def standalone_func(val):
    c = Calculator()
    return c.compute(val)
"""
    result = parser.parse_file("test.py", py_code)
    assert result.language == "python"
    assert "os" in result.imports
    assert any(s.name == "Calculator" for s in result.symbols)
    assert any(s.name == "standalone_func" for s in result.symbols)


def test_ast_parser_typescript():
    parser = ASTParser()
    ts_code = """
import { PaymentClient } from './payment';

export interface UserConfig {
    id: string;
}

export function processOrder(orderId: string) {
    return orderId;
}

export class CheckoutService {
    checkout() {
        return true;
    }
}
"""
    result = parser.parse_file("checkout.ts", ts_code)
    assert result.language == "typescript"
    assert "./payment" in result.imports
    assert any(s.name == "processOrder" for s in result.symbols)
    assert any(s.name == "CheckoutService" for s in result.symbols)
    assert any(s.name == "UserConfig" for s in result.symbols)


def test_code_graph_and_retrieval(temp_repo):
    cg = CodeGraph(temp_repo)
    cg.build_graph()
    assert len(cg.nodes) > 0

    refs = cg.find_references("add")
    assert len(refs) > 0

    retrieval = HybridRetrievalEngine(temp_repo, code_graph=cg)
    context = retrieval.retrieve("multiply")
    assert any("calculator.py" in f for f in context.relevant_files)


def test_policy_engine_rules():
    policy = PolicyEngine()

    # Read operation allowed
    read_rule = policy.evaluate_tool("read_file", {"path": "src/app.py"})
    assert read_rule.allowed
    assert not read_rule.requires_approval

    # Destructive command blocked
    block_rule = policy.evaluate_tool("run_command", {"command": "rm -rf /"})
    assert not block_rule.allowed

    # Git push requires approval
    push_rule = policy.evaluate_tool("push_branch", {"branch_name": "feature/fix"})
    assert push_rule.allowed
    assert push_rule.requires_approval
