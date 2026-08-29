from __future__ import annotations
import json
import os
from typing import Any, AsyncGenerator, Dict, List, Optional
from openai import AsyncOpenAI

from packages.agent.models.base import ModelProvider, ModelResponse, ToolCallRequest
from packages.shared.errors import ModelProviderError
from packages.shared.logging import logger

# Models that do NOT support native tool calling
_NO_TOOL_CALL_MODELS = {"deepseek-reasoner"}

# Models that do NOT accept a temperature parameter
_NO_TEMPERATURE_MODELS = {"deepseek-reasoner"}


class DeepSeekProvider(ModelProvider):
    """
    Model provider for DeepSeek V4 Flash / Chat with optional thinking mode.
    Supports deepseek-v4-flash (thinking: low/high/max) and deepseek-reasoner (R1).

    Thinking mode is enabled via extra_body per the DeepSeek V4 API:
        {"thinking": {"type": "enabled", "reasoning_effort": "low"}}
    reasoning_content is returned in message.reasoning_content.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        thinking: Optional[bool] = None,
        reasoning_effort: Optional[str] = None,
    ):
        try:
            from apps.api.app.config import settings
            default_key = settings.DEEPSEEK_API_KEY
            default_model = settings.DEEPSEEK_MODEL
            default_base_url = settings.DEEPSEEK_BASE_URL
        except Exception:
            default_key = ""
            default_model = "deepseek-v4-flash"
            default_base_url = "https://api.deepseek.com/v1"

        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY") or default_key
        self.model = model or os.getenv("DEEPSEEK_MODEL") or default_model
        self.base_url = base_url or os.getenv("DEEPSEEK_BASE_URL") or default_base_url

        # Thinking mode — reads from env or explicit param
        env_thinking = os.getenv("DEEPSEEK_THINKING", "false").lower() == "true"
        self.thinking_enabled = thinking if thinking is not None else env_thinking
        self.reasoning_effort = (
            reasoning_effort
            or os.getenv("DEEPSEEK_REASONING_EFFORT", "low")
        )

        if not self.api_key:
            logger.warning("DEEPSEEK_API_KEY is not set. API calls will fail unless mock provider is used.")

        self.client = AsyncOpenAI(
            api_key=self.api_key or "sk-dummy-key",
            base_url=self.base_url,
        )

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2,
        max_tokens: Optional[int] = 4096,
    ) -> ModelResponse:
        if not self.api_key or self.api_key == "your-deepseek-api-key-here":
            raise ModelProviderError(
                provider="deepseek",
                message="DEEPSEEK_API_KEY is missing or invalid. Please configure it in .env or environment.",
            )

        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }

        # Temperature: not supported by R1-family models
        if self.model not in _NO_TEMPERATURE_MODELS:
            kwargs["temperature"] = temperature

        # Tool calling: not supported by R1-family models
        if tools and self.model not in _NO_TOOL_CALL_MODELS:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        # Thinking mode via extra_body (deepseek-v4-flash supports reasoning_effort)
        if self.thinking_enabled and self.model not in _NO_TOOL_CALL_MODELS:
            kwargs["extra_body"] = {
                "thinking": {
                    "type": "enabled",
                    "reasoning_effort": self.reasoning_effort,  # "low" | "high" | "max"
                }
            }

        try:
            response = await self.client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            message = choice.message

            tool_calls: List[ToolCallRequest] = []
            if message.tool_calls:
                for tc in message.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    except json.JSONDecodeError:
                        args = {"raw_arguments": tc.function.arguments}
                    tool_calls.append(
                        ToolCallRequest(
                            id=tc.id,
                            name=tc.function.name,
                            arguments=args,
                        )
                    )

            usage_dict = {}
            if response.usage:
                usage_dict = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                }

            # Capture reasoning / chain-of-thought from thinking-capable models
            reasoning_summary = getattr(message, "reasoning_content", None)

            return ModelResponse(
                content=message.content,
                tool_calls=tool_calls,
                reasoning_summary=reasoning_summary,
                finish_reason=choice.finish_reason or "stop",
                usage=usage_dict,
            )
        except Exception as e:
            logger.error(f"DeepSeek API generation error: {e}")
            raise ModelProviderError(provider="deepseek", message=str(e))

    async def stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2,
    ) -> AsyncGenerator[str, None]:
        if not self.api_key:
            raise ModelProviderError(provider="deepseek", message="DEEPSEEK_API_KEY is not set.")

        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        if self.model not in _NO_TEMPERATURE_MODELS:
            kwargs["temperature"] = temperature
        if tools and self.model not in _NO_TOOL_CALL_MODELS:
            kwargs["tools"] = tools
        if self.thinking_enabled and self.model not in _NO_TOOL_CALL_MODELS:
            kwargs["extra_body"] = {
                "thinking": {
                    "type": "enabled",
                    "reasoning_effort": self.reasoning_effort,
                }
            }

        try:
            stream = await self.client.chat.completions.create(**kwargs)
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            raise ModelProviderError(provider="deepseek", message=str(e))
