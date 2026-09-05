"""Trade-adapter-backed alternative to OpenRouterArticleScorer.

Fork-only sidecar module (see ../../../docs/FORK_CONVENTIONS.md — "New,
self-contained behavior" shape). `article_scorers.py` is left untouched so
future upstream merges of ED-Alpha stay low-conflict; this file adds a new,
sibling `BaseArticleScorer` implementation instead of rewriting
`OpenRouterArticleScorer` in place.

Why this exists: Trade policy (2026-09-05) is that OpenRouter is never used
as an LLM provider anywhere in this monorepo — see
.claude/backlog/items/2026-09-05-openrouter-ban-llm-adapter-audit.md. Unlike
vibetrading/tradingagents (where OpenRouter was one of ~20 mostly-unused
generic providers and a hard sidecar *gate* was enough), ed-alpha's only
concrete scorer was OpenRouter-backed with nothing to fall back to — gating
it would just break scoring. This class is the real replacement: it routes
through Trade's own LLM adapter registry
(`integrations/trade_integrations/dataflows/model_adapters/` — `registry.py`
+ `catalog.yaml`, a priority-ordered failover chain across independently
enable/disable-able providers such as MiniMax and NVIDIA) instead of calling
OpenRouter's API directly.

Interface parity with `OpenRouterArticleScorer` (see article_scorers.py):
  - Same base class (`BaseArticleScorer`) and the same `score(title, snippet)
    -> ScoreResult` contract.
  - Reuses the *exact* same prompt construction (`_build_prompt_messages`)
    and the *exact* same response parsing/validation
    (`_parse_json_payload` -> `validate_score_result`) that
    `OpenRouterArticleScorer` already uses — scoring semantics (the 1-5
    scale, the SEC 8-K category guidance, JSON shape) are identical; only the
    transport (Trade adapter vs. a direct OpenRouter HTTP call) differs.
  - Deliberately does NOT accept `api_key`/`model` constructor args the way
    `OpenRouterArticleScorer` does — `load_article_scorer()` (ed-alpha's
    existing scorer-selection extension point, see score_gdelt_news.py's
    `--scorer-class` flag) instantiates a custom scorer class with zero
    arguments, and provider/model selection for this class is entirely
    config-driven via catalog.yaml, not passed in per-call.

Selection: use ed-alpha's existing `--scorer-class` mechanism, no new
selection mechanism needed —

    python src/score_gdelt_news.py ... \\
        --scorer-class trade_adapter_article_scorer:TradeAdapterArticleScorer

This only works when ed-alpha is checked out as a submodule inside the Trade
monorepo (so `<trade-root>/integrations/trade_integrations` exists) and run
with a Python environment that has Trade's own dependencies installed
(openai, httpx, pyyaml) — see `_ensure_trade_stack_path()` below. Running
ed-alpha's batch job in the stock, isolated Docker container defined by
`batch/Dockerfile`/`docker-compose.yaml` does *not* satisfy this today (that
container only installs `requirements.txt`'s bare fetch/parse dependencies
and never mounts the Trade repo) — wiring that container up (mounting
`integrations/`, adding its deps, passing through NVIDIA_API_KEY/
MINIMAX_API_KEY, TRADE_STACK_HUB_DIR) is tracked as follow-up scope, not done
here; see this class's originating backlog item's Attempts log.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from article_scorers import (
    BaseArticleScorer,
    ScoreResult,
    _build_prompt_messages,
    _parse_json_payload,
)


def _trade_repo_root() -> Optional[Path]:
    """Walk up from this file looking for the Trade monorepo root.

    ed-alpha lives at ``<trade-root>/ed-alpha`` as a git submodule, so this
    file's own path already sits inside the monorepo whenever it's actually
    checked out that way (mirrors vibetrading's
    ``agent/src/trade/hub_bridge.trade_repo_root()``, adapted for ed-alpha's
    own location one level up rather than two).
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "integrations" / "trade_integrations").is_dir():
            return parent
    return None


def _ensure_trade_stack_path() -> Path:
    root = _trade_repo_root()
    if root is None:
        raise RuntimeError(
            "TradeAdapterArticleScorer requires ed-alpha to be checked out as a submodule "
            "inside the Trade monorepo (expected <trade-root>/integrations/trade_integrations "
            "to exist, walking up from this file). Run ed-alpha's batch job from within the "
            "Trade repo checkout, not the standalone Docker container, to use this scorer."
        )
    integrations = root / "integrations"
    if str(integrations) not in sys.path:
        sys.path.insert(0, str(integrations))
    return root


class TradeAdapterArticleScorer(BaseArticleScorer):
    """Scores articles via Trade's LLM adapter registry instead of OpenRouter."""

    def __init__(
        self,
        *,
        max_tokens: int = 512,
        temperature: float = 0.0,
        adapter_id: Optional[str] = None,
    ):
        super().__init__(session=None)
        _ensure_trade_stack_path()
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.adapter_id = adapter_id

    def score(self, title: Optional[str], snippet: Optional[str]) -> ScoreResult:
        # Imported lazily (after _ensure_trade_stack_path ran in __init__) so
        # this module still imports cleanly outside the Trade monorepo (e.g.
        # under plain `pip install -r requirements.txt`) as long as nothing
        # actually instantiates this class.
        from trade_integrations.dataflows.model_adapters import generate_json_text

        messages = _build_prompt_messages(title, snippet)
        # generate_json_text takes one flattened prompt string, not a
        # role-tagged messages list (its own provider calls — _call_minimax /
        # _call_nvidia — already pack everything into a single user message),
        # so join system+user the same way rather than dropping the system
        # instructions.
        prompt = "\n\n".join(message["content"] for message in messages)
        content = generate_json_text(
            prompt,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            adapter_id=self.adapter_id,
        )
        return _parse_json_payload(content)


def create_trade_adapter_scorer(
    *,
    max_tokens: int = 512,
    temperature: float = 0.0,
    adapter_id: Optional[str] = None,
) -> TradeAdapterArticleScorer:
    """Factory mirroring article_scorers.create_openrouter_scorer's shape."""
    return TradeAdapterArticleScorer(
        max_tokens=max_tokens,
        temperature=temperature,
        adapter_id=adapter_id,
    )


__all__ = ["TradeAdapterArticleScorer", "create_trade_adapter_scorer"]
