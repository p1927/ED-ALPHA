from __future__ import annotations

from pathlib import Path

import pytest

import score_gdelt_news as scorer


class FakeCursor:
    def __init__(self, fetchone_result=None):
        self.fetchone_result = fetchone_result
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchone(self):
        return self.fetchone_result


class FakeConnection:
    def __init__(self, cursor: FakeCursor):
        self.cursor_obj = cursor
        self.commits = 0

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1


class NoHttpSession:
    def get(self, *args, **kwargs):  # pragma: no cover - should never be called
        raise AssertionError("HTTP should not be called for cached article content")


def test_repo_has_no_legacy_gdelt_article_table_references():
    repo_root = Path(__file__).resolve().parents[2]
    forbidden = "gdelt_" + "articles2"
    offenders = []

    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        if ".git" in path.parts or "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if forbidden in text:
            offenders.append(path.relative_to(repo_root).as_posix())

    assert offenders == []


def test_upsert_article_uses_gdelt_articles_table():
    cursor = FakeCursor()
    connection = FakeConnection(cursor)

    scorer.upsert_article(
        connection,
        "https://example.com/article",
        "Headline",
        "Snippet",
        None,
    )

    assert connection.commits == 1
    query = cursor.executed[0][0]
    assert "INSERT INTO gdelt_articles " in query
    assert ("gdelt_" + "articles2") not in query


def test_get_article_content_returns_cached_content_without_http():
    cursor = FakeCursor(fetchone_result=(" Cached title ", " Cached snippet ", None))
    connection = FakeConnection(cursor)

    title, snippet = scorer.get_article_content(
        connection,
        NoHttpSession(),
        "https://example.com/cached",
        "Ed-Alpha/0.1 (test@example.com)",
    )

    assert title == "Cached title"
    assert snippet == "Cached snippet"
    assert "FROM gdelt_articles WHERE article_url = %s" in cursor.executed[0][0]


def test_get_article_content_raises_for_cached_fetch_error():
    cursor = FakeCursor(fetchone_result=(None, None, "previous fetch failed"))
    connection = FakeConnection(cursor)

    with pytest.raises(scorer.ArticleFetchError, match="previous fetch failed"):
        scorer.get_article_content(
            connection,
            NoHttpSession(),
            "https://example.com/broken",
            "Ed-Alpha/0.1 (test@example.com)",
        )


def test_extract_title_and_snippet_prefers_article_text_and_normalizes():
    html_text = """
    <html>
      <head><title>  Example &amp; News  </title></head>
      <body><article> First line.\n\n Second line. </article></body>
    </html>
    """

    title, snippet = scorer.extract_title_and_snippet(html_text)

    assert title == "Example & News"
    assert snippet == "First line. Second line."


def test_validate_day_window_rejects_inverted_window():
    with pytest.raises(ValueError, match="greater than or equal"):
        scorer.validate_day_window(5, 30)
