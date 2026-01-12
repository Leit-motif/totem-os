"""LLM client implementations for Totem OS."""

from .client import FakeLLMClient, LLMClient, RealLLMClient, get_llm_client
from .router import (
    LLM_ROUTER_VERSION,
    ROUTE_PROMPT_VERSION,
    BaseLLMRouter,
    FakeLLMRouter,
    LLMRouterTrace,
    RealLLMRouter,
    get_llm_router,
    has_llm_api_key,
)

__all__ = [
    # Distill clients
    "LLMClient",
    "FakeLLMClient",
    "RealLLMClient",
    "get_llm_client",
    # Routing clients
    "BaseLLMRouter",
    "FakeLLMRouter",
    "RealLLMRouter",
    "LLMRouterTrace",
    "get_llm_router",
    "has_llm_api_key",
    # Version constants
    "LLM_ROUTER_VERSION",
    "ROUTE_PROMPT_VERSION",
]
