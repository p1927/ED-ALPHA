"""The real CLI parser and scorer loop reuse work and report every incomplete run."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import score_gdelt_news as scorer


@pytest.fixture
def run(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "score_gdelt_news",
            "--experiment-id",
            "1",
            "--min-days-before",
            "10",
            "--max-days-before",
            "1",
            "--model",
            "fixture",
        ],
    )
    monkeypatch.setattr(
        scorer,
        "load_configuration",
        lambda: {"user_email": "fixture@example.com", "database_config": {}},
    )
    connection = MagicMock()
    monkeypatch.setattr(scorer.psycopg2, "connect", lambda **kw: connection)
    monkeypatch.setattr(scorer, "fetch_predict_date", lambda *a: date(2026, 10, 1))
    monkeypatch.setattr(scorer, "assert_labels_exist", lambda *a: None)
    monkeypatch.setattr(
        scorer, "create_article_scorer", lambda *a: (object(), "fixture", "fixture")
    )
    monkeypatch.setattr(scorer, "create_scoring_run", lambda *a: 7)
    rows = [
        ("20260901000000", f"gkg-{i}", "https://fixture.example/article", i, 0)
        for i in range(10)
    ]
    monkeypatch.setattr(scorer, "iter_target_records", lambda *a: iter(rows))
    fetched, scored, written, partial = [], [], [], []
    monkeypatch.setattr(
        scorer,
        "get_article_content",
        lambda *a: fetched.append(a) or ("title", "snippet"),
    )
    monkeypatch.setattr(
        scorer, "score_article", lambda *a: scored.append(a) or (4, "fixture score")
    )
    monkeypatch.setattr(scorer, "upsert_score", lambda *a: written.append(a))
    monkeypatch.setattr(scorer, "mark_run_partial", lambda *a: partial.append(a[-1]))
    return SimpleNamespace(
        rows=rows,
        fetched=fetched,
        scored=scored,
        written=written,
        partial=partial,
        connection=connection,
    )


def test_actual_parser_and_loop_read_and_score_a_repeated_url_once(run):
    scorer.main()  # parse_args supplies no reasoning_mode: the former unconditional access crashed.
    assert len(run.fetched) == len(run.scored) == 1
    assert len(run.written) == len(run.rows) == 10
    assert run.partial == [False]
    run.connection.close.assert_called_once()


def test_failed_scorer_is_not_retried_for_each_company_row(run, monkeypatch):
    def fail(*args):
        run.scored.append(args)
        raise RuntimeError("controlled scorer outage")

    monkeypatch.setattr(scorer, "score_article", fail)
    with pytest.raises(SystemExit, match="10 article/company rows"):
        scorer.main()
    assert len(run.scored) == len(run.fetched) == 1
    assert run.written == [] and run.partial == [True]


@pytest.mark.parametrize(
    "failure", [scorer.ArticleFetchError, scorer.ArticleContentUnavailable]
)
def test_fetch_or_content_skips_keep_the_run_partial(run, monkeypatch, failure):
    def fail(*args):
        raise failure("controlled article unavailable")

    monkeypatch.setattr(scorer, "get_article_content", fail)
    with pytest.raises(SystemExit, match="partial"):
        scorer.main()
    assert run.scored == run.written == [] and run.partial == [True]
