-- Additive, nullable (shared-DB rule: a lagging reader still works). A scoring run is created
-- partial = TRUE and flipped to FALSE only when every article scored; a crashed or partial run stays TRUE.
-- NULL = a run from before this column existed. Readers that serve predictions skip partial = TRUE.
-- The same statement is run by batch/src/score_gdelt_news.py and app/backend/main.py on existing databases.
ALTER TABLE gdelt_scoring_runs ADD COLUMN IF NOT EXISTS partial BOOLEAN;
