# Data Model and Pipeline Flow

This document describes how ED-Alpha stores pipeline outputs and how data moves through the batch jobs.

- **Entity Relationship Diagram** shows how ED-Alpha stores company filings, GDELT news, experiment labels, LLM scores, rankings, and evaluation metrics.
- **Batch Pipeline Flow** connects the database relationships to the batch script order, so the full data path is visible from ingestion to metrics.

## Entity Relationship Diagram

This diagram summarizes the database shape and how records connect.

```mermaid
erDiagram
    company_profiles ||--o{ company_tickers : has
    company_profiles ||--o{ company_recent_filings : files
    company_profiles ||--o{ gdelt_gkg_company_links : linked_to

    filing_experiments ||--o{ filing_experiment_labels : assigns
    filing_experiments ||--o{ filing_experiment_label_evidence : evidence
    filing_experiments ||--o{ gdelt_scoring_runs : config_for

    gdelt_scoring_runs ||--o{ gdelt_article_scores : produces
    filing_experiments ||--o{ gdelt_article_scores : scored_for
    gdelt_articles ||--o{ gdelt_article_scores : scored_item

    gdelt_scoring_runs ||--o{ gdelt_run_cik_scores : aggregates
    filing_experiments ||--o{ gdelt_run_cik_scores : aggregates

    gdelt_scoring_runs ||--o{ gdelt_run_metrics : measures

    gdelt_gkg_records ||--o{ gdelt_gkg_company_links : joins
    gdelt_master_times ||--o{ gdelt_gkg_records : schedules
```

## Batch Pipeline Flow

This diagram shows the batch execution order from ingestion to evaluation.

```mermaid
flowchart TD
    A[fetch_company_tickers] -->|companies| CP[company_profiles & company_tickers]
    B[fetch_recent_filings] -->|filings| RF[company_recent_filings]
    C[fetch_gdelt_master_times] --> MT[gdelt_master_times]
    D[fetch_gdelt_gkg] --> GR[gdelt_gkg_records]
    MT --> GR
    E[link_gdelt_gkg_companies] --> CL[gdelt_gkg_company_links]
    GR --> CL
    CP --> CL
    F[generate_labels] --> FE[filing_experiments & filing_experiment_labels]
    RF --> FE
    G[scrape_filing_items] --> FS[filing_item_sections]
    RF --> FS
    H[score_gdelt_news] -->|articles & scores| GA[gdelt_articles] & AS[gdelt_article_scores]
    FE --> H
    CL --> H
    I[aggregate_gdelt_run_scores] --> RCS[gdelt_run_cik_scores]
    AS --> RCS
    FE --> RCS
    J[calc_gdelt_run_metrics] --> RM[gdelt_run_metrics]
    RCS --> RM
```
