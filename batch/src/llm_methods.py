from __future__ import annotations

from article_scorers import BaseArticleScorer, ScoreResult


class LLMMethodError(RuntimeError):
    """Backward-compatible error name for older imports."""


class LLMResponseFormatError(ValueError):
    """Backward-compatible error name for older imports."""


BaseLLMMethod = BaseArticleScorer


def create_llm_method(*, adapter_id: str | None = None) -> BaseArticleScorer:
    """The one built-in scorer: Trade's model adapter (OpenRouter is never used in Trade)."""
    from trade_adapter_article_scorer import create_trade_adapter_scorer

    return create_trade_adapter_scorer(adapter_id=adapter_id)


__all__ = [
    "BaseLLMMethod",
    "LLMMethodError",
    "LLMResponseFormatError",
    "ScoreResult",
    "create_llm_method",
]
