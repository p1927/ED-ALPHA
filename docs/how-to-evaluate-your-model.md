# How to evaluate your model

Use this guide to connect your own article-level event scorer to ED-Alpha and evaluate it with the existing aggregation and recall/precision workflow.

ED-Alpha v1 standardizes custom models as article scorers:

```python
title: str | None
snippet: str | None
```

Each scorer must return:

```python
ScoreResult(score=<1_to_5>, reason=<non_empty_text>)
```

Use this score scale:

1. `1`: not relevant.
2. `2`: weak signal.
3. `3`: possible event signal.
4. `4`: strong event signal.
5. `5`: clear or explicit event signal.

After article scoring, ED-Alpha reuses the existing company-level aggregation and Top-K evaluation scripts.

## 1. Create a scorer class

Create a Python module outside or inside the repository, for example `my_model/scorer.py`.
Subclassing `BaseArticleScorer` is recommended for readability, but ED-Alpha only requires an object with a callable `score(title, snippet)` method.

```python
from article_scorers import BaseArticleScorer, ScoreResult


class MyArticleScorer(BaseArticleScorer):
    def score(self, title: str | None, snippet: str | None) -> ScoreResult:
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

If your model needs weights, credentials, GPU settings, or a local configuration file, load them inside your scorer class using your own code or environment variables.
The scorer constructor should not require arguments.

## 2. Make your scorer importable

When running inside the batch container, make sure both ED-Alpha's `src` directory and your scorer module are on `PYTHONPATH`.

For example, if your code is available at `/app/my_model/scorer.py`:

```bash
PYTHONPATH=/app/src:/app python src/score_gdelt_news.py \
  --experiment-id <experiment_id> \
  --min-days-before 30 \
  --max-days-before 5 \
  --batch-size 200 \
  --run-label "my-model" \
  --scorer-class my_model.scorer:MyArticleScorer
```

The value of `--scorer-class` must use the format:

```text
module.path:ClassName
```

For OpenRouter models, keep using the built-in scorer:

```bash
python src/score_gdelt_news.py \
  --experiment-id <experiment_id> \
  --min-days-before 30 \
  --max-days-before 5 \
  --batch-size 200 \
  --run-label "openrouter-baseline" \
  --model openai/gpt-5 \
  --reasoning-mode thinking
```

`OPENROUTER_API_KEY` is required only when using `--model`.

## 3. Run aggregation and metrics

The scoring command prints a `run_id`.
Use that `run_id` to aggregate per-company scores and evaluate Top-K performance.

```bash
python src/aggregate_gdelt_run_scores.py --run-id <run_id>
python src/calc_gdelt_run_metrics.py --run-id <run_id> --k-values 10 25 50 100
```

Refresh the dashboard and select the new experiment/run to inspect ranked companies, evidence articles, matched filings, and metrics.

## Current scope

Custom scorers operate at the article level.
They must emit a 1-5 event-signal score plus a non-empty reason.
Changing the company-level aggregation protocol, using arbitrary continuous scores, or plugging in a company-level ranker directly requires extending the current pipeline.
