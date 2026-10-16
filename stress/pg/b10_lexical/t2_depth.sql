-- =====================================================================================
-- B10 · T2 · DEPTH — does lexical support survive nesting?
--
-- Objective card: stress/pg/b10_lexical/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql, plus pg_trgm. PostgreSQL: 17. Run with psql.
--
-- Each lexical form from T1, three to five levels down: in CTEs, derived tables and
-- scalar subqueries. Conventions: every earlier decision, plus D-35..D-38.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · a tagged literal in the innermost of three CTEs, filtering there
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel, revenue)
WITH a AS (
    SELECT o.channel, o.amount
    FROM staging.orders AS o
    WHERE o.status = $st$PAID$st$
),
b AS (
    SELECT a.channel, a.amount || $x$; -- not a comment $x$ AS amt_text, a.amount
    FROM a
),
c AS (
    SELECT b.channel, b.amount
    FROM b
)
SELECT c.channel, c.amount
FROM c;


-- -------------------------------------------------------------------------------------
-- S2 · comments nested five deep, inside a derived table inside a derived table
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (customer_id, full_name)
SELECT d2.id, d2.nm
FROM (
    SELECT d1.id, d1.nm
    FROM (
        SELECT c.id, /* 1 /* 2 /* 3 /* 4 /* 5 c.email, */ 4 */ 3 */ 2 */ 1 */ c.full_name AS nm
        FROM staging.customers AS c
        -- WHERE c.status = 'X'
    ) AS d1
) AS d2;


-- -------------------------------------------------------------------------------------
-- S3 · PREPARE a four-deep CTE chain, $1 at depth 4 and $2 at depth 1
-- -------------------------------------------------------------------------------------
PREPARE p_chain(text, numeric) AS
INSERT INTO mart.sales_daily (store_id, net_amount)
WITH l4 AS (
    SELECT o.store_id, o.amount
    FROM staging.orders AS o
    WHERE o.currency = $1
),
l3 AS (SELECT l4.store_id, l4.amount FROM l4),
l2 AS (SELECT l3.store_id, SUM(l3.amount) AS amt FROM l3 GROUP BY l3.store_id),
l1 AS (SELECT l2.store_id, l2.amt * $2 AS amt FROM l2)
SELECT l1.store_id, l1.amt
FROM l1;


-- -------------------------------------------------------------------------------------
-- S4 · EXECUTE the chain
-- -------------------------------------------------------------------------------------
EXECUTE p_chain('EUR', 1.08);


-- -------------------------------------------------------------------------------------
-- S5 · a nested dollar quote holding a whole statement, three levels down
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (segment)
SELECT x.seg
FROM (
    SELECT y.seg
    FROM (
        SELECT $outer$ $inner$ DELETE FROM staging.customers; $inner$ $outer$ || c.segment AS seg
        FROM staging.customers AS c
    ) AS y
) AS x;


\set st_open 'OPEN'
-- -------------------------------------------------------------------------------------
-- S6 · a psql variable three derived tables down
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (region, country_code)
SELECT x.region, x.cc
FROM (
    SELECT y.region, y.cc
    FROM (
        SELECT s.region, s.country_code AS cc
        FROM core.dim_store AS s
        WHERE s.status = :'st_open'
    ) AS y
) AS x;


-- -------------------------------------------------------------------------------------
-- S7 · a custom operator under an aggregate, two CTEs deep
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (segment, avg_basket)
WITH d AS (
    SELECT c.segment, c.full_name <-> c.email AS dist
    FROM staging.customers AS c
),
g AS (
    SELECT d.segment, AVG(d.dist) AS avg_dist
    FROM d
    GROUP BY d.segment
)
SELECT g.segment, g.avg_dist
FROM g;


-- -------------------------------------------------------------------------------------
-- S8 · PREPARE a SELECT over a CTE chain with E'' and U&'' literals
-- -------------------------------------------------------------------------------------
PREPARE p_ship(text) AS
WITH s AS (
    SELECT sh.carrier_id, sh.cost, sh.status
    FROM staging.shipments AS sh
    WHERE sh.status <> E'LOST\'S'
),
t AS (
    SELECT s.carrier_id, s.cost
    FROM s
    WHERE s.status = U&'DELIVERED' OR s.status = $1
)
SELECT t.carrier_id, t.cost
FROM t;


-- -------------------------------------------------------------------------------------
-- S9 · CREATE TABLE AS EXECUTE over the chain
-- -------------------------------------------------------------------------------------
CREATE TABLE mart.ship_pick AS EXECUTE p_ship('RETURNED');


-- -------------------------------------------------------------------------------------
-- S10 · reading the created table (D-31)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.carrier_scorecard (carrier_id, cost_per_kg)
SELECT sp.carrier_id, sp.cost
FROM mart.ship_pick AS sp;


-- -------------------------------------------------------------------------------------
-- S11 · OPERATOR(pg_catalog.…) spellings nested in a CASE in a scalar subquery
-- -------------------------------------------------------------------------------------
INSERT INTO mart.product_performance (product_id, margin)
SELECT p.id,
       (SELECT CASE WHEN p2.list_price OPERATOR(pg_catalog.>) 0
                    THEN p2.list_price OPERATOR(pg_catalog.-) p2.cost
               END
        FROM staging.products AS p2
        WHERE p2.id = p.id)
FROM staging.products AS p;


-- -------------------------------------------------------------------------------------
-- S12 · a run of short statements, some sharing a line
-- -------------------------------------------------------------------------------------
INSERT INTO mart.events_log (id) SELECT e.id FROM staging.events_log AS e;
INSERT INTO mart.events_log (kind) SELECT e.kind FROM staging.events_log AS e;
INSERT INTO core.ledger (account) SELECT l.account FROM staging.ledger AS l;
INSERT INTO core.ledger (balance) SELECT l.amount FROM staging.ledger AS l; INSERT INTO core.ledger (status) SELECT l.currency FROM staging.ledger AS l;
INSERT INTO core.dim_carrier (name) SELECT sh.status FROM staging.shipments AS sh;
