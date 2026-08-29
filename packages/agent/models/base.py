from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator, Dict, List, Optional
from pydantic import BaseModel, Field


class ToolCallRequest(BaseModel):
    id: str
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


class ModelResponse(BaseModel):
    content: Optional[str] = None
    tool_calls: List[ToolCallRequest] = Field(default_factory=list)
    reasoning_summary: Optional[str] = None
    finish_reason: str = "stop"
    usage: Dict[str, int] = Field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


class ModelProvider(ABC):
    """Abstract interface for LLM model providers."""

    @abstractmethod
    async def generate(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2,
        max_tokens: Optional[int] = 4096,
    ) -> ModelResponse:
        """Generate response with optional tool calls."""
        pass

    @abstractmethod
    async def stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2,
    ) -> AsyncGenerator[str, None]:
        """Stream model text output."""
        pass
