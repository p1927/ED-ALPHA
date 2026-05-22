from __future__ import annotations

from datetime import date

import pytest

import aggregate_gdelt_run_scores as aggregate
import calc_gdelt_run_metrics as metrics
import score_gdelt_news as scorer


class AggregateCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchall(self):
        return self.rows


def test_compute_time_bounds_includes_full_start_and_end_days():
    start_time, end_time = scorer.compute_time_bounds(
        date(2025, 9, 1),
        min_days_before=30,
        max_days_before=5,
    )

    assert start_time == "20250802000000"
    assert end_time == "20250827235959"


def test_aggregate_scores_sums_llm_scores_minus_one_for_all_labelled_ciks():
    cursor = AggregateCursor(rows=[(1001, 6), (1003, 2)])

    totals = aggregate.aggregate_scores(
        cursor,
        run_id=42,
        labelled_ciks=[(1001, 1), (1002, 0), (1003, 1)],
    )

    assert totals == [(1001, 1, 6), (1002, 0, 0), (1003, 1, 2)]
    assert "FROM gdelt_article_scores" in cursor.executed[0][0]


def test_compute_metrics_orders_top_k_and_calculates_recall_precision():
    ranked_scores = [
        (1001, 0, 9),
        (1002, 1, 8),
        (1003, 1, 5),
        (1004, 0, 1),
    ]

    result = metrics.compute_metrics(ranked_scores, [1, 3])

    assert result == [
        (1, [1001], [9], 0, 2, 0.0, 0.0),
        (3, [1001, 1002, 1003], [9, 8, 5], 2, 2, 1.0, pytest.approx(2 / 3)),
    ]


def test_sanitize_k_values_deduplicates_sorts_and_rejects_empty():
    assert metrics.sanitize_k_values([10, 5, 10, 0, -1]) == [5, 10]

    with pytest.raises(ValueError, match="At least one positive"):
        metrics.sanitize_k_values([0, -3])
