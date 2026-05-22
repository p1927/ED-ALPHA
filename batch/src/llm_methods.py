from __future__ import annotations

from article_scorers import (
    BaseArticleScorer,
    OpenRouterArticleScorer,
    ScoreResult,
    create_openrouter_scorer,
)


class LLMMethodError(RuntimeError):
    """Backward-compatible error name for older imports."""


class LLMResponseFormatError(ValueError):
    """Backward-compatible error name for older imports."""


BaseLLMMethod = BaseArticleScorer
OpenRouterChatMethod = OpenRouterArticleScorer


def create_llm_method(
    session,
    *,
    api_key: str,
    model: str,
    reasoning_mode: str = "none",
    supports_json_format: bool = True,
) -> BaseArticleScorer:
    return create_openrouter_scorer(
        session,
        api_key=api_key,
        model=model,
        reasoning_mode=reasoning_mode,
        supports_json_format=supports_json_format,
    )


__all__ = [
    "BaseLLMMethod",
    "LLMMethodError",
    "LLMResponseFormatError",
    "OpenRouterChatMethod",
    "ScoreResult",
    "create_llm_method",
]
