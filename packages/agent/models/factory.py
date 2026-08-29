from __future__ import annotations
import os
from typing import Optional
from packages.agent.models.base import ModelProvider, ModelResponse, ToolCallRequest
from packages.agent.models.deepseek import DeepSeekProvider
from packages.agent.models.mock import MockModelProvider


def get_model_provider(provider_type: Optional[str] = None) -> ModelProvider:
    provider = provider_type or os.getenv("MODEL_PROVIDER", "deepseek")
    if provider == "mock":
        return MockModelProvider()
    elif provider == "deepseek":
        return DeepSeekProvider()
    else:
        return DeepSeekProvider()
