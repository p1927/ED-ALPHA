# How to evaluate your model

Use this guide to connect your own event-scoring model to ED-Alpha and evaluate it with the existing recall/precision workflow.

## 1. Match the expected scorer format

ED-Alpha scores one news article at a time. Your wrapper must accept:

```python
title: str | None
snippet: str | None
```

Your wrapper must return:

```python
ScoreResult(score=<1_to_5>, reason=<non_empty_text>)
```

Use this score scale:

1. `1`: not relevant.
2. `2`: weak signal.
3. `3`: possible event signal.
4. `4`: strong event signal.
5. `5`: clear or explicit event signal.

## 2. Add a wrapper class

Add this class in `batch/src/llm_methods.py` below `OpenRouterChatMethod`.

```python
class CustomModelMethod(BaseLLMMethod):
    def __init__(self, session: requests.Session, request_timeout: int = 30):
        super().__init__(session)
        self.request_timeout = request_timeout

    def score(self, title: Optional[str], snippet: Optional[str]) -> ScoreResult:
        model_input = {
            "title": title or "",
            "snippet": snippet or "",
        }

        # Replace this dummy block with your model call.
        model_output = {
            "probability": 0.72,
            "explanation": "The article may indicate a material event.",
        }

        probability = float(model_output["probability"])
        if probability >= 0.8:
            score = 5
        elif probability >= 0.6:
            score = 4
        elif probability >= 0.4:
            score = 3
        elif probability >= 0.2:
            score = 2
        else:
            score = 1

        reason = str(model_output.get("explanation", "")).strip()
        if not reason:
            reason = f"Custom model probability={probability:.3f}"

        return ScoreResult(score=score, reason=reason)
```

## 3. Replace the dummy model call

If your model is an HTTP service, replace the dummy block with:

```python
response = self.session.post(
    "http://custom-model:8080/score",
    json=model_input,
    timeout=self.request_timeout,
)
response.raise_for_status()
model_output = response.json()
```

If your model is a local Python function, replace the dummy block with:

```python
model_output = call_your_model(
    title=model_input["title"],
    text=model_input["snippet"],
)
```

Then adjust the mapping code so your model output becomes:

```python
ScoreResult(score=<1_to_5>, reason=<non_empty_text>)
```

## 4. Register the wrapper

Update `create_llm_method` in `batch/src/llm_methods.py`.

```python
def create_llm_method(
    session: requests.Session,
    *,
    api_key: str,
    model: str,
    reasoning_mode: str = "none",
    supports_json_format: bool = True,
) -> BaseLLMMethod:
    # Add this branch for your model wrapper.
    if model == "custom-model":
        return CustomModelMethod(session)

    # Keep the existing OpenRouter path for normal LLM models.
    return OpenRouterChatMethod(
        session,
        api_key=api_key,
        model=model,
        reasoning_mode=reasoning_mode,
        supports_json_format=supports_json_format,
    )
```

## 5. Run the evaluation

Create an experiment and note the printed `experiment_id`.

```bash
python src/generate_labels.py --config config/custom_model_experiment.json
```

Score articles with your model.

```bash
python src/score_gdelt_news.py \
  --experiment-id <experiment_id> \
  --min-days-before 30 \
  --max-days-before 5 \
  --batch-size 200 \
  --run-label "custom-model" \
  --model custom-model \
  --reasoning-mode none
```

The scoring command prints a `run_id`. Use that `run_id` to aggregate and evaluate.

```bash
python src/aggregate_gdelt_run_scores.py --run-id <run_id>
python src/calc_gdelt_run_metrics.py --run-id <run_id> --k-values 10 25 50 100
```

## 6. If your model does not use OpenRouter

`score_gdelt_news.py` currently checks for `OPENROUTER_API_KEY` before creating a scorer. For a non-OpenRouter model, move that check so it only applies to OpenRouter models.
