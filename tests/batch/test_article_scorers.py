from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from article_scorers import BaseArticleScorer, ScoreResult, load_article_scorer, validate_score_result
from llm_methods import BaseLLMMethod, OpenRouterChatMethod, create_llm_method
from score_gdelt_news import create_article_scorer, score_article, validate_scorer_args


def write_module(tmp_path: Path, module_name: str, source: str) -> None:
    module_path = tmp_path / f"{module_name}.py"
    module_path.write_text(source, encoding="utf-8")


@pytest.fixture
def importable_tmp_path(tmp_path):
    sys.path.insert(0, str(tmp_path))
    try:
        yield tmp_path
    finally:
        sys.path.remove(str(tmp_path))
        for name in list(sys.modules):
            if name.startswith("custom_"):
                sys.modules.pop(name, None)


def args(**overrides):
    defaults = {
        "model": None,
        "scorer_class": None,
        "reasoning_mode": "none",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_load_article_scorer_accepts_base_class_subclass(importable_tmp_path):
    write_module(
        importable_tmp_path,
        "custom_subclass",
        """
from article_scorers import BaseArticleScorer, ScoreResult

class KeywordScorer(BaseArticleScorer):
    def score(self, title, snippet):
        return ScoreResult(score=4, reason="keyword match")
""",
    )

    scorer = load_article_scorer("custom_subclass:KeywordScorer")

    assert isinstance(scorer, BaseArticleScorer)
    assert score_article(scorer, "deal", "merger talks") == (4, "keyword match")


def test_load_article_scorer_accepts_duck_typed_class(importable_tmp_path):
    write_module(
        importable_tmp_path,
        "custom_duck",
        """
from article_scorers import ScoreResult

class DuckScorer:
    def score(self, title, snippet):
        return ScoreResult(score=3, reason="duck typed scorer")
""",
    )

    scorer = load_article_scorer("custom_duck:DuckScorer")

    assert score_article(scorer, None, None) == (3, "duck typed scorer")


def test_load_article_scorer_rejects_bad_import_paths(importable_tmp_path):
    with pytest.raises(ValueError, match="module.path:ClassName"):
        load_article_scorer("missing-colon")

    with pytest.raises(ImportError, match="Could not import scorer module"):
        load_article_scorer("custom_missing:Scorer")

    write_module(importable_tmp_path, "custom_empty", "class Other: pass\n")
    with pytest.raises(ImportError, match="was not found"):
        load_article_scorer("custom_empty:Scorer")


def test_load_article_scorer_rejects_missing_score_and_constructor_failure(importable_tmp_path):
    write_module(importable_tmp_path, "custom_no_score", "class NoScore: pass\n")
    with pytest.raises(TypeError, match="score"):
        load_article_scorer("custom_no_score:NoScore")

    write_module(
        importable_tmp_path,
        "custom_bad_constructor",
        "class BadConstructor:\n    def __init__(self, required):\n        pass\n",
    )
    with pytest.raises(TypeError):
        load_article_scorer("custom_bad_constructor:BadConstructor")


def test_validate_score_result_rejects_invalid_results():
    assert validate_score_result(ScoreResult(score=5, reason="clear event")) == ScoreResult(
        score=5,
        reason="clear event",
    )

    with pytest.raises(ValueError, match="ScoreResult"):
        validate_score_result({"score": 5, "reason": "not a dataclass"})

    with pytest.raises(ValueError, match="between 1 and 5"):
        validate_score_result(ScoreResult(score=6, reason="too high"))

    with pytest.raises(ValueError, match="non-empty"):
        validate_score_result(ScoreResult(score=3, reason=" "))


def test_validate_scorer_args_requires_exactly_one_scorer_kind():
    validate_scorer_args(args(model="openai/gpt-5"))
    validate_scorer_args(args(scorer_class="custom_duck:DuckScorer"))

    with pytest.raises(ValueError, match="exactly one"):
        validate_scorer_args(args())

    with pytest.raises(ValueError, match="exactly one"):
        validate_scorer_args(args(model="openai/gpt-5", scorer_class="custom_duck:DuckScorer"))


def test_create_article_scorer_uses_custom_class_without_openrouter_key(importable_tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    write_module(
        importable_tmp_path,
        "custom_selection",
        """
from article_scorers import ScoreResult

class SelectionScorer:
    def score(self, title, snippet):
        return ScoreResult(score=2, reason="selected custom scorer")
""",
    )

    scorer, model_name, description = create_article_scorer(
        args(scorer_class="custom_selection:SelectionScorer"),
        requests.Session(),
    )

    assert score_article(scorer, "headline", "snippet") == (2, "selected custom scorer")
    assert model_name == "python:custom_selection:SelectionScorer"
    assert description == "python scorer=custom_selection:SelectionScorer"


def test_create_article_scorer_requires_key_for_openrouter(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        create_article_scorer(args(model="openai/gpt-5"), requests.Session())

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    scorer, model_name, description = create_article_scorer(
        args(model="openai/gpt-5", reasoning_mode="thinking"),
        requests.Session(),
    )

    assert type(scorer).__name__ == "OpenRouterArticleScorer"
    assert model_name == "openai/gpt-5"
    assert description == "openrouter model=openai/gpt-5 reasoning_mode=thinking"


def test_llm_methods_compatibility_aliases_keep_existing_imports_working():
    session = requests.Session()

    base = BaseLLMMethod(session)
    scorer = create_llm_method(
        session,
        api_key="test-key",
        model="openai/gpt-5",
        reasoning_mode="none",
    )

    assert base.session is session
    assert isinstance(scorer, OpenRouterChatMethod)
