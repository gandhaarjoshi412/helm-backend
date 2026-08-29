from __future__ import annotations
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional
import uuid

from packages.agent.models.base import ModelProvider, ModelResponse, ToolCallRequest


class MockModelProvider(ModelProvider):
    """
    Deterministic mock provider for unit testing, integration tests,
    and offline scenario verification.
    """

    def __init__(self, response_queue: Optional[List[ModelResponse]] = None):
        self.response_queue: List[ModelResponse] = response_queue or []
        self.call_history: List[Dict[str, Any]] = []
        self.dynamic_handler: Optional[Callable[[List[Dict[str, Any]], Optional[List[Dict[str, Any]]]], ModelResponse]] = None

    def enqueue_response(self, response: ModelResponse) -> None:
        self.response_queue.append(response)

    def enqueue_tool_call(self, tool_name: str, arguments: Dict[str, Any], content: Optional[str] = None) -> None:
        self.response_queue.append(
            ModelResponse(
                content=content,
                tool_calls=[
                    ToolCallRequest(
                        id=f"call_{uuid.uuid4().hex[:8]}",
                        name=tool_name,
                        arguments=arguments,
                    )
                ],
                finish_reason="tool_calls",
                usage={"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
            )
        )

    def enqueue_text(self, text: str) -> None:
        self.response_queue.append(
            ModelResponse(
                content=text,
                tool_calls=[],
                finish_reason="stop",
                usage={"prompt_tokens": 40, "completion_tokens": 30, "total_tokens": 70},
            )
        )

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2,
        max_tokens: Optional[int] = 4096,
    ) -> ModelResponse:
        self.call_history.append({"messages": messages, "tools": tools})

        if self.dynamic_handler:
            return self.dynamic_handler(messages, tools)

        if self.response_queue:
            return self.response_queue.pop(0)

        # Default fallback response
        return ModelResponse(
            content="Task analysis complete. Ready for verification.",
            tool_calls=[],
            finish_reason="stop",
            usage={"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        )

    async def stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2,
    ) -> AsyncGenerator[str, None]:
        res = await self.generate(messages, tools, temperature)
        if res.content:
            yield res.content
