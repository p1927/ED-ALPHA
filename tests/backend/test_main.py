from __future__ import annotations

from datetime import date, datetime, timezone

import httpx
import pytest

import main as backend


class FakeAcquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return FakeAcquire(self.connection)


class FakeConnection:
    def __init__(self, *, fetch_results=None, fetchrow_results=None):
        self.fetch_results = list(fetch_results or [])
        self.fetchrow_results = list(fetchrow_results or [])
        self.fetch_calls = []
        self.fetchrow_calls = []

    async def fetch(self, query, *args):
        self.fetch_calls.append((query, args))
        return self.fetch_results.pop(0)

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query, args))
        return self.fetchrow_results.pop(0)


@pytest.fixture(autouse=True)
def restore_pool():
    original_pool = backend._pool
    yield
    backend._pool = original_pool


async def request(path: str, connection: FakeConnection):
    backend._pool = FakePool(connection)
    transport = httpx.ASGITransport(app=backend.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


@pytest.mark.asyncio
async def test_health_returns_ok():
    transport = httpx.ASGITransport(app=backend.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_list_experiments_returns_run_ids():
    now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    connection = FakeConnection(
        fetch_results=[
            [
                {
                    "id": 1,
                    "predict_date": date(2025, 9, 1),
                    "horizon_days": 5,
                    "item_codes": ["1.01", "8.01"],
                    "neg_multiplier": 3,
                    "seed": 11,
                    "created_at": now,
                    "run_ids": [7, 8],
                }
            ]
        ]
    )

    response = await request("/experiments", connection)

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["id"] == 1
    assert payload[0]["run_ids"] == [7, 8]
    assert payload[0]["predict_date"] == "2025-09-01"


@pytest.mark.asyncio
async def test_get_run_metrics_returns_404_for_missing_run():
    connection = FakeConnection(fetchrow_results=[None])

    response = await request("/runs/999/metrics", connection)

    assert response.status_code == 404
    assert "run_id=999 not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_run_metrics_returns_metrics():
    now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    connection = FakeConnection(
        fetchrow_results=[{"id": 42}],
        fetch_results=[
            [
                {
                    "k": 10,
                    "top_ciks": [1001, 1002],
                    "top_scores": [9, 7],
                    "positives_in_top": 1,
                    "total_positives": 3,
                    "recall": 1 / 3,
                    "precision": 0.1,
                    "computed_at": now,
                }
            ]
        ],
    )

    response = await request("/runs/42/metrics", connection)

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["k"] == 10
    assert payload[0]["top_ciks"] == [1001, 1002]
    assert payload[0]["recall"] == pytest.approx(1 / 3)


@pytest.mark.asyncio
async def test_get_experiment_results_returns_top_rows_evidence_and_event_url():
    now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    connection = FakeConnection(
        fetchrow_results=[
            {"id": 3, "predict_date": date(2025, 9, 1), "horizon_days": 5},
            {"id": 44, "experiment_id": 3},
        ],
        fetch_results=[
            [{"cik": 123456, "total_score": 9, "company_name": "Acme Corp"}],
            [
                {
                    "cik": 123456,
                    "llm_score": 5,
                    "llm_reason": "Announced material agreement.",
                    "evaluated_at": now,
                    "article_url": "https://example.com/news",
                    "title": "Acme signs deal",
                }
            ],
            [
                {
                    "cik": 123456,
                    "accession_number": "0000123456-25-000001",
                    "form": "8-K",
                    "filing_date": date(2025, 9, 2),
                    "primary_document": "form8k.htm",
                    "items": "1.01",
                }
            ],
        ],
    )

    response = await request("/experiments/3/results?run_id=44&k=1", connection)

    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["company_name"] == "Acme Corp"
    assert result["evidence"][0]["llm_score"] == 5
    assert result["event"]["url"] == (
        "https://www.sec.gov/Archives/edgar/data/"
        "123456/000012345625000001/form8k.htm"
    )


@pytest.mark.asyncio
async def test_get_experiment_results_rejects_run_from_other_experiment():
    connection = FakeConnection(
        fetchrow_results=[
            {"id": 3, "predict_date": date(2025, 9, 1), "horizon_days": 5},
            {"id": 44, "experiment_id": 99},
        ]
    )

    response = await request("/experiments/3/results?run_id=44&k=1", connection)

    assert response.status_code == 400
    assert "does not belong to experiment_id=3" in response.json()["detail"]
