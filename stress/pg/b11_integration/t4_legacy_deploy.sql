-- =====================================================================================
-- B11 · T4 · LEGACY DEPLOY — a messy psql deploy script, end to end
--
-- Objective card: stress/pg/b11_integration/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql. PostgreSQL: 17. Run with psql.
--
-- psql meta-commands (D-37) and variables (D-36), a session search_path (D-23), quoted
-- mixed-case and keyword names (B9), PREPARE / EXECUTE (D-35), nested comments, E'' and
-- dollar quotes (B10), constant dynamic SQL in a DO block (D-42), \i and \copy. Every
-- earlier decision applies.
-- =====================================================================================

\set ON_ERROR_STOP on
\echo 'deploying the legacy feed, step 1 of 3'
\set run_day '2026-10-03'

SET search_path TO staging, public;

-- -------------------------------------------------------------------------------------
-- S1 · the legacy feed rolled up: quoted names, bound through the session path
-- -------------------------------------------------------------------------------------
INSERT INTO mart."FeedSummary" ("CustomerID", "Total", "Status Code")
SELECT f."CustomerID", SUM(f."Amount"), f."Status Code"
FROM "LegacyFeed" AS f
WHERE f.created_at::date = :'run_day'::date
GROUP BY f."CustomerID", f."Status Code";


-- -------------------------------------------------------------------------------------
-- S2 · the lower-case amount is a different column from "Amount"
-- -------------------------------------------------------------------------------------
INSERT INTO mart."FeedSummary" (total)
SELECT SUM(f.amount)
FROM "LegacyFeed" AS f;


-- -------------------------------------------------------------------------------------
-- S3 · keyword-named columns, quoted
-- -------------------------------------------------------------------------------------
INSERT INTO mart."FeedSummary" ("order")
SELECT f."order"
FROM staging."LegacyFeed" AS f
WHERE f."user" = 'legacy';


-- -------------------------------------------------------------------------------------
-- S4 · PREPARE the ledger copy: `ledger` binds through the path; comments and E'' inside
-- -------------------------------------------------------------------------------------
PREPARE p_led(text) AS
INSERT INTO core.ledger (id, account, balance)
SELECT l.id, l.account /* nested /* ; */ comment */, l.amount
FROM ledger AS l
WHERE l.currency = $1 AND l.account <> E'n/a\'s';


-- -------------------------------------------------------------------------------------
-- S5 · EXECUTE it (D-35)
-- -------------------------------------------------------------------------------------
EXECUTE p_led('EUR');


-- -------------------------------------------------------------------------------------
-- S6 · a dollar-quoted literal holding a semicolon, in an UPDATE
-- -------------------------------------------------------------------------------------
UPDATE core.dim_product AS p
SET name = $$Legacy; $$ || p.sku
WHERE p.status = 'LEGACY';


RESET search_path;
\echo 'step 2 of 3: grants'
\i deploy/grants.sql

-- R7 — constant dynamic SQL in a DO block
DO $$
DECLARE
    v_t text := 'channel_mix';
BEGIN
-- S7 · the folded statement (D-42)
    EXECUTE format('INSERT INTO mart.%I (channel, orders) SELECT o.channel, COUNT(o.id) FROM staging.orders AS o GROUP BY o.channel', v_t);
END;
$$;

\echo 'step 3 of 3: returns'
\copy staging.returns FROM 'returns.csv' WITH (FORMAT csv)
-- -------------------------------------------------------------------------------------
-- S8 · after the \copy: the returns roll-up still runs
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level, revenue)
SELECT r.reason_code, SUM(r.amount)
FROM staging.returns AS r
GROUP BY r.reason_code;
