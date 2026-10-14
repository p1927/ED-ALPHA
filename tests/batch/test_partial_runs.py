"""A scoring run that skipped articles is marked partial in the DB and never served as a prediction."""

from __future__ import annotations

import pytest

import score_gdelt_news as scorer
from test_score_gdelt_news import FakeConnection, FakeCursor


def test_new_run_starts_partial_after_the_additive_column_is_ensured():
    cursor = FakeCursor(fetchone_result=(7,))
    run_id = scorer.create_scoring_run(FakeConnection(cursor), 1, 1, 30, 10, "m", None)
    statements = [q for q, _ in cursor.executed]
    assert run_id == 7
    assert statements[0] == scorer.ADD_PARTIAL_COLUMN_SQL
    assert "VALUES (%s, %s, %s, %s, %s, %s, TRUE)" in statements[1]  # a crashed run stays partial


@pytest.mark.parametrize("partial", [True, False])
def test_mark_run_partial_writes_the_flag(partial):
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    scorer.mark_run_partial(connection, 7, partial)
    assert cursor.executed == [("UPDATE gdelt_scoring_runs SET partial = %s WHERE id = %s", (partial, 7))]
    assert connection.commits == 1


def test_sql_migration_is_additive_and_nullable():
    from pathlib import Path

    sql = (Path(__file__).resolve().parents[2] / "db" / "10_alter_gdelt_scoring_runs_partial.sql").read_text()
    statements = [line for line in sql.splitlines() if line and not line.startswith("--")]
    assert statements == ["ALTER TABLE gdelt_scoring_runs ADD COLUMN IF NOT EXISTS partial BOOLEAN;"]
    assert scorer.ADD_PARTIAL_COLUMN_SQL in statements[0]
