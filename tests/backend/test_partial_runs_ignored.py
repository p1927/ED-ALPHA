"""/predictions/{ticker} only considers runs that were not marked partial."""

from __future__ import annotations

import pytest

from test_main import FakeConnection, request


@pytest.mark.asyncio
async def test_prediction_ignores_partial_runs():
    connection = FakeConnection(
        fetchrow_results=[
            {"cik": 1, "company_name": "Acme"},  # ticker lookup
            None,  # no experiment with a complete run
        ]
    )
    response = await request("/predictions/ACME", connection)
    assert response.status_code == 200
    assert response.json()["status"] == "no_data"
    experiment_query = connection.fetchrow_calls[1][0]
    assert "gr.partial IS NOT TRUE" in experiment_query
