from __future__ import annotations

import json

import pytest

from article_scorers import BaseArticleScorer, ScoreResult, load_article_scorer
from trade_adapter_article_scorer import (
    TradeAdapterArticleScorer,
    create_trade_adapter_scorer,
)


def test_is_a_base_article_scorer_drop_in():
    scorer = TradeAdapterArticleScorer()
    assert isinstance(scorer, BaseArticleScorer)


def test_requires_no_constructor_args_like_load_article_scorer_expects():
    # score_gdelt_news.py's --scorer-class path (article_scorers.load_article_scorer)
    # instantiates a custom scorer class with zero arguments — confirm this class is
    # selectable that way without any code change to the existing selection mechanism.
    scorer = load_article_scorer("trade_adapter_article_scorer:TradeAdapterArticleScorer")
    assert isinstance(scorer, TradeAdapterArticleScorer)


def test_create_trade_adapter_scorer_factory_mirrors_create_openrouter_scorer_shape():
    scorer = create_trade_adapter_scorer(max_tokens=256, temperature=0.2, adapter_id="nvidia-generative")
    assert isinstance(scorer, TradeAdapterArticleScorer)
    assert scorer.max_tokens == 256
    assert scorer.temperature == 0.2
    assert scorer.adapter_id == "nvidia-generative"


def test_score_calls_trade_adapter_with_combined_prompt_and_parses_json(monkeypatch):
    import trade_integrations.dataflows.model_adapters as model_adapters

    captured = {}

    def fake_generate_json_text(prompt, *, max_tokens, temperature, adapter_id=None):
        captured["prompt"] = prompt
        captured["max_tokens"] = max_tokens
        captured["temperature"] = temperature
        captured["adapter_id"] = adapter_id
        return json.dumps({"score": 4, "reason": "material agreement referenced"})

    monkeypatch.setattr(model_adapters, "generate_json_text", fake_generate_json_text)

    scorer = TradeAdapterArticleScorer(max_tokens=300, temperature=0.0, adapter_id="minimax-generative")
    result = scorer.score("Acme signs merger agreement", "Acme Corp today announced a merger.")

    assert result == ScoreResult(score=4, reason="material agreement referenced")
    assert captured["max_tokens"] == 300
    assert captured["temperature"] == 0.0
    assert captured["adapter_id"] == "minimax-generative"
    # Same prompt construction as OpenRouterArticleScorer (_build_prompt_messages) —
    # system instructions and user headline/snippet both flow into the one prompt string
    # generate_json_text expects, since none of the adapter's own provider calls support a
    # separate system role.
    assert "material SEC-style corporate actions" in captured["prompt"]
    assert "Acme signs merger agreement" in captured["prompt"]
    assert "Acme Corp today announced a merger." in captured["prompt"]


def test_score_raises_on_malformed_json_response(monkeypatch):
    import trade_integrations.dataflows.model_adapters as model_adapters

    monkeypatch.setattr(model_adapters, "generate_json_text", lambda *a, **k: "not json")

    scorer = TradeAdapterArticleScorer()
    with pytest.raises(ValueError, match="not valid JSON"):
        scorer.score("headline", "snippet")


def test_missing_trade_stack_raises_loud_runtime_error(monkeypatch):
    import trade_adapter_article_scorer as module

    monkeypatch.setattr(module, "_trade_repo_root", lambda: None)

    with pytest.raises(RuntimeError, match="Trade monorepo"):
        TradeAdapterArticleScorer()
