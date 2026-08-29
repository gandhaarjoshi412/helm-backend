from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from pydantic import BaseModel, ConfigDict, Field
from packages.sandbox.interface import ExecutionProvider
from packages.context_engine.graph.builder import CodeGraph
from packages.context_engine.retrieval.hybrid import HybridRetrievalEngine


class ToolContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    env_id: str
    sandbox: ExecutionProvider
    repo_path: str
    code_graph: Optional[CodeGraph] = None
    retrieval_engine: Optional[HybridRetrievalEngine] = None
    task_id: str = ""
    run_id: str = ""


class ToolResult(BaseModel):
    success: bool
    output: Any
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Tool(ABC):
    """Base class for all HELM coding and exploration tools."""

    name: str
    description: str
    parameters: Dict[str, Any]

    @abstractmethod
    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        pass

    def to_openai_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
