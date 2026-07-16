from __future__ import annotations

import os
import re
import ssl
from datetime import date, timedelta
from typing import List, Optional, Dict
from pathlib import Path

import asyncpg
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

# Import Pydantic models from the separate file
from models import (
    Experiment,
    RunMetric,
    Evidence,
    EventInfo,
    ResultRow,
    ResultsResponse,
    TickerPrediction,
)

# ============================================================
# FastAPI app setup
# ============================================================

app = FastAPI(title="ED-ALPHA App Backend", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ALLOW_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# Database settings
# ============================================================
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None
else:
    try:
        project_root = Path(__file__).resolve().parents[2]
    except IndexError:
        project_root = Path(__file__).resolve().parent
    load_dotenv(project_root / ".env")

def _build_dsn_from_pg_env() -> str:
    """
    Build a PostgreSQL DSN from standard PG* environment variables.
    Priority: DATABASE_URL > (PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD)
    """
    host = os.getenv("PGHOST", "localhost")
    port = os.getenv("PGPORT", "5432")
    db = os.getenv("PGDATABASE", "postgres")
    user = os.getenv("PGUSER", "postgres")
    pw = os.getenv("PGPASSWORD", "")
    return f"postgresql://{user}:{pw}@{host}:{port}/{db}"

DATABASE_URL = os.getenv("DATABASE_URL") or _build_dsn_from_pg_env()

_pool: Optional[asyncpg.Pool] = None


def _ssl_ctx_from_env() -> Optional[ssl.SSLContext]:
    """
    Enable SSL by default (PGSSL=require). To disable, set PGSSL=disable.
    For production on RDS you should load the RDS CA bundle and enable
    certificate verification (verify-full).
    """
    mode = (
        os.getenv("PGSSL")
        or os.getenv("PGSSLMODE")
        or "require"
    ).lower()

    if mode in ("disable", "off", "false", "0"):
        return None

    if mode in ("allow-self-signed", "self-signed", "trust"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    ctx = ssl.create_default_context()

    root_cert = os.getenv("PGSSLROOTCERT") or os.getenv("PGSSL_ROOT_CERT")
    if root_cert:
        ctx.load_verify_locations(cafile=root_cert)

    # For verify-ca the server hostname is not validated.
    if mode == "verify-ca":
        ctx.check_hostname = False

    # Default (require / verify-full) keeps hostname verification on.
    return ctx


@app.on_event("startup")
async def on_startup():
    """Initialize the async connection pool."""
    global _pool
    _pool = await asyncpg.create_pool(
        dsn=DATABASE_URL,
        min_size=1,
        max_size=int(os.getenv("DB_POOL_MAX", "10")),
        ssl=_ssl_ctx_from_env(),
        command_timeout=60,
        statement_cache_size=2048,
    )


@app.on_event("shutdown")
async def on_shutdown():
    """Close the pool on shutdown."""
    global _pool
    if _pool:
        await _pool.close()


# ============================================================
# Helpers
# ============================================================

def _sec_doc_url(cik: int, accession_number: str, primary_document: str) -> str:
    """
    Build a standard SEC EDGAR Archives URL:
    https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no_nodash}/{primary_document}
    """
    acc_nodash = re.sub(r"-", "", accession_number)
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{primary_document}"


def _require_pool():
    if _pool is None:
        raise HTTPException(status_code=500, detail="DB pool not initialized")


# ============================================================
# 1) GET: list experiments with brief configs (+ run_ids)
#    Endpoint: GET /experiments
#    Query params: limit, offset
# ============================================================

@app.get("/experiments", response_model=List[Experiment])
async def list_experiments(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """
    Returns experiments and their key fields, including a list of run_ids (if any).
    Ordered by created_at DESC.
    """
    _require_pool()
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT fe.id,
                   fe.predict_date,
                   fe.horizon_days,
                   fe.item_codes,
                   fe.neg_multiplier,
                   fe.seed,
                   fe.created_at,
                   COALESCE(array_agg(gr.id ORDER BY gr.id)
                            FILTER (WHERE gr.id IS NOT NULL), '{}') AS run_ids
            FROM filing_experiments fe
            LEFT JOIN gdelt_scoring_runs gr ON gr.experiment_id = fe.id
            GROUP BY fe.id
            ORDER BY fe.created_at DESC
            LIMIT $1 OFFSET $2
            """,
            limit, offset,
        )

    return [
        Experiment(
            id=r["id"],
            predict_date=r["predict_date"],
            horizon_days=r["horizon_days"],
            item_codes=r["item_codes"],
            neg_multiplier=r["neg_multiplier"],
            seed=r["seed"],
            created_at=r["created_at"],
            run_ids=list(r["run_ids"]),
        )
        for r in rows
    ]


# ============================================================
# 2) GET: @K metrics for a run
#    Endpoint: GET /runs/{run_id}/metrics
# ============================================================

@app.get("/runs/{run_id}/metrics", response_model=List[RunMetric])
async def get_run_metrics(run_id: int):
    """
    Returns the @K metrics (precision/recall, top_ciks/top_scores arrays, etc.) for a given run_id.
    """
    _require_pool()
    async with _pool.acquire() as conn:
        # basic existence check
        run_row = await conn.fetchrow("SELECT id FROM gdelt_scoring_runs WHERE id = $1", run_id)
        if not run_row:
            raise HTTPException(status_code=404, detail=f"run_id={run_id} not found")

        rows = await conn.fetch(
            """
            SELECT k, top_ciks, top_scores, positives_in_top, total_positives, recall, precision, computed_at
            FROM gdelt_run_metrics
            WHERE run_id = $1
            ORDER BY k
            """,
            run_id,
        )

    return [
        RunMetric(
            k=r["k"],
            top_ciks=list(r["top_ciks"]),
            top_scores=list(r["top_scores"]),
            positives_in_top=r["positives_in_top"],
            total_positives=r["total_positives"],
            recall=float(r["recall"]),
            precision=float(r["precision"]),
            computed_at=r["computed_at"],
        )
        for r in rows
    ]


# ============================================================
# 3) GET: results for an experiment (top-K from a run), with evidence and event info
#    Endpoint: GET /experiments/{experiment_id}/results?run_id=&k=&evidence_per_company=
# ============================================================

@app.get("/experiments/{experiment_id}/results", response_model=ResultsResponse)
async def get_experiment_results(
    experiment_id: int,
    run_id: int = Query(..., description="gdelt_scoring_runs.id associated with the experiment"),
    k: int = Query(50, ge=1, le=1000, description="Top-K companies to return"),
    evidence_per_company: int = Query(3, ge=1, le=10, description="Max evidence items per company"),
):
    """
    Returns:
      - For each of the top-K companies (by total_score in a run):
        - company id (CIK), name, total_score
        - evidence list: LLM reason (summary), URL, title, llm_score, evaluated_at
        - event (8-K info): accession_number, form, filing_date, primary_document, items, URL
          (If not confirmed, event is None.)
    Event window is [predict_date, predict_date + horizon_days].
    """
    _require_pool()
    async with _pool.acquire() as conn:
        # Validate experiment
        exp = await conn.fetchrow(
            """
            SELECT id, predict_date, horizon_days
            FROM filing_experiments
            WHERE id = $1
            """,
            experiment_id,
        )
        if not exp:
            raise HTTPException(status_code=404, detail=f"experiment_id={experiment_id} not found")

        # Validate run and ownership
        run = await conn.fetchrow(
            "SELECT id, experiment_id FROM gdelt_scoring_runs WHERE id = $1",
            run_id,
        )
        if not run:
            raise HTTPException(status_code=404, detail=f"run_id={run_id} not found")
        if run["experiment_id"] != experiment_id:
            raise HTTPException(
                status_code=400,
                detail=f"run_id={run_id} does not belong to experiment_id={experiment_id}",
            )

        predict_date: date = exp["predict_date"]
        horizon_days: int = exp["horizon_days"]
        end_date: date = predict_date + timedelta(days=horizon_days)

        # Fetch top-K companies by total_score for the run
        top_rows = await conn.fetch(
            """
            SELECT r.cik, r.total_score, cp.title AS company_name
            FROM gdelt_run_cik_scores r
            LEFT JOIN company_profiles cp ON cp.cik = r.cik
            WHERE r.run_id = $1
            ORDER BY r.total_score DESC, r.cik
            LIMIT $2
            """,
            run_id, k,
        )

        if not top_rows:
            return ResultsResponse(experiment_id=experiment_id, run_id=run_id, k=k, results=[])

        ciks = [r["cik"] for r in top_rows]

        # Evidence (top N per company) from article scores
        evidence_rows = await conn.fetch(
            """
            SELECT s.cik,
                   s.llm_score,
                   s.llm_reason,
                   s.evaluated_at,
                   a.article_url,
                   a.title
            FROM gdelt_article_scores s
            JOIN gdelt_articles a ON a.article_url = s.article_url
            WHERE s.run_id = $1
              AND s.cik = ANY($2::bigint[])
            ORDER BY s.cik, s.llm_score DESC, s.evaluated_at DESC
            """,
            run_id, ciks,
        )

        # Group and clamp to evidence_per_company
        grouped_evidence: Dict[int, List[Evidence]] = {}
        for row in evidence_rows:
            arr = grouped_evidence.setdefault(row["cik"], [])
            if len(arr) < evidence_per_company:
                arr.append(
                    Evidence(
                        llm_score=row["llm_score"],
                        summary=row["llm_reason"],
                        url=row["article_url"],
                        title=row["title"],
                        evaluated_at=row["evaluated_at"],
                    )
                )

        # 8-K events within [predict_date, end_date] if any (first hit per CIK)
        event_rows = await conn.fetch(
            """
            SELECT f.cik,
                   f.accession_number,
                   f.form,
                   f.filing_date,
                   f.primary_document,
                   f.items
            FROM company_recent_filings f
            WHERE f.cik = ANY($1::bigint[])
              AND f.form ILIKE '8-K%%'
              AND (f.filing_date IS NULL OR (f.filing_date >= $2 AND f.filing_date <= $3))
            ORDER BY f.cik, f.filing_date NULLS LAST
            """,
            ciks, predict_date, end_date,
        )

        first_event_by_cik: Dict[int, EventInfo] = {}
        for r in event_rows:
            cik = r["cik"]
            if cik in first_event_by_cik:
                continue
            url = None
            if r["primary_document"] and r["accession_number"]:
                url = _sec_doc_url(cik, r["accession_number"], r["primary_document"])
            first_event_by_cik[cik] = EventInfo(
                accession_number=r["accession_number"],
                form=r["form"],
                filing_date=r["filing_date"],
                primary_document=r["primary_document"],
                items=r["items"],
                url=url,
            )

        # Assemble final response rows
        results: List[ResultRow] = []
        for r in top_rows:
            cik = r["cik"]
            results.append(
                ResultRow(
                    cik=cik,
                    company_name=r["company_name"],
                    total_score=r["total_score"],
                    evidence=grouped_evidence.get(cik, []),
                    event=first_event_by_cik.get(cik),
                )
            )

        return ResultsResponse(experiment_id=experiment_id, run_id=run_id, k=k, results=results)


@app.get("/predictions/{ticker}", response_model=TickerPrediction)
async def get_prediction_by_ticker(
    ticker: str,
    evidence_per_company: int = Query(5, ge=1, le=10),
):
    """
    Return the latest ED-ALPHA score for a US ticker symbol.

    Used by the Trade company-research corp_events stage. Requires batch ingest
    (company tickers + GDELT scoring run) to return ranked predictions.
    """
    _require_pool()
    symbol = ticker.strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="ticker is required")

    async with _pool.acquire() as conn:
        ticker_row = await conn.fetchrow(
            """
            SELECT ct.cik, cp.title AS company_name
            FROM company_tickers ct
            JOIN company_profiles cp ON cp.cik = ct.cik
            WHERE UPPER(ct.ticker) = $1
            ORDER BY ct.cik
            LIMIT 1
            """,
            symbol,
        )
        if not ticker_row:
            return TickerPrediction(
                ticker=symbol,
                status="not_found",
                detail=f"Ticker {symbol} not in company_tickers; run fetch_company_tickers.py",
            )

        cik = int(ticker_row["cik"])
        company_name = ticker_row["company_name"]

        exp = await conn.fetchrow(
            """
            SELECT fe.id,
                   fe.predict_date,
                   fe.horizon_days,
                   COALESCE(array_agg(gr.id ORDER BY gr.id DESC)
                            FILTER (WHERE gr.id IS NOT NULL), '{}') AS run_ids
            FROM filing_experiments fe
            LEFT JOIN gdelt_scoring_runs gr ON gr.experiment_id = fe.id
            GROUP BY fe.id
            HAVING COUNT(gr.id) > 0
            ORDER BY fe.created_at DESC
            LIMIT 1
            """
        )
        if not exp or not exp["run_ids"]:
            return TickerPrediction(
                ticker=symbol,
                status="no_data",
                cik=cik,
                company_name=company_name,
                detail=(
                    "No scoring runs yet. Start batch ingest inside ed-alpha "
                    "(fetch_company_tickers, GDELT, generate_labels, score_gdelt_news)."
                ),
            )

        experiment_id = int(exp["id"])
        run_id = int(exp["run_ids"][0])
        predict_date: date = exp["predict_date"]
        horizon_days: int = exp["horizon_days"]
        end_date: date = predict_date + timedelta(days=horizon_days)

        score_row = await conn.fetchrow(
            """
            SELECT total_score,
                   (SELECT COUNT(*) + 1
                    FROM gdelt_run_cik_scores r2
                    WHERE r2.run_id = r.run_id AND r2.total_score > r.total_score) AS rank
            FROM gdelt_run_cik_scores r
            WHERE r.run_id = $1 AND r.cik = $2
            """,
            run_id,
            cik,
        )

        evidence_rows = await conn.fetch(
            """
            SELECT s.llm_score,
                   s.llm_reason,
                   s.evaluated_at,
                   a.article_url,
                   a.title
            FROM gdelt_article_scores s
            JOIN gdelt_articles a ON a.article_url = s.article_url
            WHERE s.run_id = $1 AND s.cik = $2
            ORDER BY s.llm_score DESC, s.evaluated_at DESC
            LIMIT $3
            """,
            run_id,
            cik,
            evidence_per_company,
        )

        event_row = await conn.fetchrow(
            """
            SELECT f.accession_number,
                   f.form,
                   f.filing_date,
                   f.primary_document,
                   f.items
            FROM company_recent_filings f
            WHERE f.cik = $1
              AND f.form ILIKE '8-K%%'
              AND (f.filing_date IS NULL OR (f.filing_date >= $2 AND f.filing_date <= $3))
            ORDER BY f.filing_date DESC NULLS LAST
            LIMIT 1
            """,
            cik,
            predict_date,
            end_date,
        )

    evidence = [
        Evidence(
            llm_score=row["llm_score"],
            summary=row["llm_reason"],
            url=row["article_url"],
            title=row["title"],
            evaluated_at=row["evaluated_at"],
        )
        for row in evidence_rows
    ]

    event = None
    if event_row and event_row["primary_document"] and event_row["accession_number"]:
        event = EventInfo(
            accession_number=event_row["accession_number"],
            form=event_row["form"],
            filing_date=event_row["filing_date"],
            primary_document=event_row["primary_document"],
            items=event_row["items"],
            url=_sec_doc_url(cik, event_row["accession_number"], event_row["primary_document"]),
        )

    if not score_row:
        return TickerPrediction(
            ticker=symbol,
            status="no_score",
            cik=cik,
            company_name=company_name,
            experiment_id=experiment_id,
            run_id=run_id,
            predict_date=predict_date,
            horizon_days=horizon_days,
            evidence=evidence,
            event=event,
            detail=f"{symbol} not ranked in run_id={run_id}; company may be outside top-K.",
        )

    return TickerPrediction(
        ticker=symbol,
        status="ok",
        cik=cik,
        company_name=company_name,
        total_score=int(score_row["total_score"]),
        rank=int(score_row["rank"]) if score_row["rank"] is not None else None,
        experiment_id=experiment_id,
        run_id=run_id,
        predict_date=predict_date,
        horizon_days=horizon_days,
        evidence=evidence,
        event=event,
    )


@app.get("/health")
async def health():
    """Liveness probe."""
    return {"status": "ok"}
