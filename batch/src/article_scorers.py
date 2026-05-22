from __future__ import annotations

import importlib
import json
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

import requests


@dataclass(frozen=True)
class ScoreResult:
    score: int
    reason: str


class BaseArticleScorer:
    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session

    def score(self, title: Optional[str], snippet: Optional[str]) -> ScoreResult:  # pragma: no cover
        raise NotImplementedError


def validate_score_result(result: object) -> ScoreResult:
    if not isinstance(result, ScoreResult):
        raise ValueError("Article scorer must return a ScoreResult.")

    try:
        score = int(result.score)
    except (TypeError, ValueError) as exc:
        raise ValueError("ScoreResult.score must be an integer from 1 to 5.") from exc

    if score < 1 or score > 5:
        raise ValueError("ScoreResult.score must be between 1 and 5.")

    reason = str(result.reason).strip()
    if not reason:
        raise ValueError("ScoreResult.reason must be a non-empty string.")

    return ScoreResult(score=score, reason=reason)


def _clean_prompt_field(value: Optional[str], fallback: str) -> str:
    text = (value or "").strip()
    return text if text else fallback


def _build_prompt_messages(title: Optional[str], snippet: Optional[str]) -> List[Dict[str, str]]:
    cleaned_title = _clean_prompt_field(title, "(No headline provided)")
    cleaned_snippet = _clean_prompt_field(snippet, "(No snippet provided)")

    system = (
        "You assess whether news articles describe material SEC-style corporate actions, including agreements, "
        "financings, governance changes, listings/delistings, restructurings, and other significant events. "
        "Respond with JSON containing integer field 'score' (1-5) and string field 'reason'."
    )
    user = (
        "Evaluate the following article headline and body snippet. "
        "Rate how strongly it signals a material corporate trigger that is likely or imminent using this 1-5 scoring guide: "
        "1 = no indication; 2 = weak hint/background; 3 = possible or emerging trigger; "
        "4 = high confidence of a trigger; 5 = clear, confirmed trigger.\n\n"
        "Consider all categories comprehensively when determining if any material corporate action appears likely or imminent. "
        "Category definitions: 1.01 = Entry into a material definitive agreement (M&A, joint venture, major contract); "
        "1.02 = Termination of a material definitive agreement; 1.03 = Bankruptcy or receivership; "
        "2.01 = Completion of acquisition or disposition of assets; 2.03 = Creation of or increase in a direct financial obligation; "
        "2.04 = Triggering events accelerating or increasing a financial obligation; "
        "3.01 = Notice of delisting or failure to satisfy a continued listing rule; "
        "3.02 = Unregistered sales of equity securities; 3.03 = Material modification to rights of security holders; "
        "4.02 = Non-reliance on previously issued financial statements; 5.01 = Changes in control of registrant; "
        "5.03 = Amendments to articles/bylaws or change in fiscal year; 8.01 = Other material events "
        "(recalls, investigations, regulatory actions, etc.).\n\n"
        "Guidelines:\n"
        "- Treat the task as predictive: if any material event appears plausible or imminent based on the article, "
        "set the score to 3 or higher even if not yet confirmed.\n"
        "- Reserve score 4-5 for high-confidence or announced events; use score 2 for vague background mentions.\n"
        "- In the reason, mention key evidence supporting your assessment and specify which category types (e.g., 1.01, 2.01) "
        "are most relevant.\n\n"
        "Return only 'score' as an integer (1-5) and 'reason' as a string explaining your assessment.\n\n"
        f"Headline: {cleaned_title}\n"
        f"Snippet: {cleaned_snippet}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _strip_code_fence(content: str) -> str:
    marker = "```"
    if not content.lstrip().startswith(marker):
        return content
    pattern = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
    match = pattern.search(content)
    if match:
        return match.group(1).strip()
    return content


def _parse_json_payload(content: str) -> ScoreResult:
    content = content.strip()
    if content.startswith("```"):
        content = _strip_code_fence(content)
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Response content is not valid JSON: {exc}") from exc

    try:
        score = int(parsed["score"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("JSON response is missing integer field 'score'.") from exc

    reason = str(parsed.get("reason", "")).strip()
    return validate_score_result(ScoreResult(score=score, reason=reason))


def _extract_content_from_body(body: Dict[str, object]) -> str:
    # Chat Completions format
    if "choices" in body:
        choices = body["choices"]
        if not isinstance(choices, list) or not choices:
            raise ValueError("Chat Completions response missing choices.")
        first = choices[0]
        if not isinstance(first, dict):
            raise ValueError("Chat Completions choice must be an object.")
        message = first.get("message")
        if not isinstance(message, dict):
            raise ValueError("Chat Completions message missing.")
        content = message.get("content")
        if not isinstance(content, str):
            raise ValueError("Chat Completions message content missing.")
        return content

    # Responses API format
    if "output" in body:
        output = body["output"]
        if not isinstance(output, list):
            raise ValueError("Responses output must be a list.")
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") not in {"message", "output_text"}:
                continue
            content = item.get("content")
            if isinstance(content, list):
                for chunk in content:
                    if isinstance(chunk, dict) and chunk.get("type") in {"output_text", "text"}:
                        text = chunk.get("text")
                        if isinstance(text, str) and text.strip():
                            return text
            text_value = item.get("text")
            if isinstance(text_value, str) and text_value.strip():
                return text_value
        raise ValueError("Responses output missing text content.")

    raise ValueError("Unsupported response payload structure.")


class OpenRouterArticleScorer(BaseArticleScorer):
    def __init__(
        self,
        session: requests.Session,
        api_key: str,
        model: str,
        *,
        reasoning_mode: str = "none",
        request_timeout: int = 30,
        supports_json_format: bool = True,
        supports_reasoning: bool = True,
    ):
        self.session = session
        self.api_key = api_key
        self.model = model
        self.reasoning_mode = reasoning_mode
        self.request_timeout = request_timeout
        self.supports_json_format = supports_json_format
        self.supports_reasoning = supports_reasoning

    def score(self, title: Optional[str], snippet: Optional[str]) -> ScoreResult:
        messages = _build_prompt_messages(title, snippet)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload: Dict[str, object] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
        }
        if self.supports_json_format:
            payload["response_format"] = {"type": "json_object"}
        if self.reasoning_mode == "thinking" and self.supports_reasoning:
            payload["reasoning"] = {"effort": "medium"}

        response = self.session.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            data=json.dumps(payload),
            timeout=self.request_timeout,
        )
        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            detail = ""
            try:
                body_text = response.text
                if body_text:
                    detail = f" Response body: {body_text}"
            except Exception:
                pass
            raise requests.HTTPError(f"OpenRouter request failed: {exc}.{detail}") from exc

        content = _extract_content_from_body(response.json())
        return _parse_json_payload(content)


def load_article_scorer(path: str) -> BaseArticleScorer:
    if ":" not in path:
        raise ValueError("Scorer class must be specified as 'module.path:ClassName'.")

    module_name, class_name = path.split(":", 1)
    if not module_name or not class_name:
        raise ValueError("Scorer class must be specified as 'module.path:ClassName'.")

    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ImportError(f"Could not import scorer module '{module_name}'.") from exc

    try:
        scorer_class = getattr(module, class_name)
    except AttributeError as exc:
        raise ImportError(f"Scorer class '{class_name}' was not found in module '{module_name}'.") from exc

    scorer = scorer_class()
    score_method = getattr(scorer, "score", None)
    if not callable(score_method):
        raise TypeError("Scorer class must define a callable score(title, snippet) method.")

    return scorer


def create_openrouter_scorer(
    session: requests.Session,
    *,
    api_key: str,
    model: str,
    reasoning_mode: str = "none",
    supports_json_format: bool = True,
) -> OpenRouterArticleScorer:
    return OpenRouterArticleScorer(
        session,
        api_key=api_key,
        model=model,
        reasoning_mode=reasoning_mode,
        supports_json_format=supports_json_format,
    )


__all__ = [
    "BaseArticleScorer",
    "OpenRouterArticleScorer",
    "ScoreResult",
    "create_openrouter_scorer",
    "load_article_scorer",
    "validate_score_result",
]
