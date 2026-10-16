-- =====================================================================================
-- B7 · T2 · DEPTH — does DDL lineage survive chains of created objects?
--
-- Objective card: stress/pg/b7_ddl_lineage/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql. PostgreSQL: 17.
--
-- A view chain three deep (views inline, D-31), objects built over it, and temp tables
-- built from temp tables (each a node). ORDER MATTERS. Every node is schema-qualified.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · view 1: a CTE inside, filtered
-- -------------------------------------------------------------------------------------
CREATE VIEW mart.v_l1 AS
WITH paid AS (
    SELECT o.id, o.customer_id, o.amount
    FROM staging.orders AS o
    WHERE o.status = 'PAID'
)
SELECT paid.customer_id, paid.amount
FROM paid;


-- -------------------------------------------------------------------------------------
-- S2 · view 2 over view 1: grouped
-- -------------------------------------------------------------------------------------
CREATE VIEW mart.v_l2 AS
SELECT v.customer_id, SUM(v.amount) AS total
FROM mart.v_l1 AS v
GROUP BY v.customer_id;


-- -------------------------------------------------------------------------------------
-- S3 · view 3 over view 2: cut on the aggregate (post-aggregation, B2-06)
-- -------------------------------------------------------------------------------------
CREATE VIEW mart.v_l3 AS
SELECT v.customer_id, v.total
FROM mart.v_l2 AS v
WHERE v.total > 100;


-- -------------------------------------------------------------------------------------
-- S4 · the top view read into a table: three views inlined down to staging.orders
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (customer_id, lifetime_value)
SELECT v.customer_id, v.total
FROM mart.v_l3 AS v;


-- -------------------------------------------------------------------------------------
-- S5 · CREATE TABLE AS from a CTE chain over view 2, ranked and cut (D-9)
-- -------------------------------------------------------------------------------------
CREATE TABLE mart.top_spenders AS
WITH ranked AS (
    SELECT v.customer_id, v.total, rank() OVER (ORDER BY v.total DESC) AS rk
    FROM mart.v_l2 AS v
)
SELECT r.customer_id, r.total
FROM ranked AS r
WHERE r.rk <= 10;


-- -------------------------------------------------------------------------------------
-- S6 · a materialized view over view 2
-- -------------------------------------------------------------------------------------
CREATE MATERIALIZED VIEW mart.mv_l2 AS
SELECT v.customer_id, v.total
FROM mart.v_l2 AS v;


-- -------------------------------------------------------------------------------------
-- S7 · a temp table over view 1
-- -------------------------------------------------------------------------------------
CREATE TEMP TABLE t_a AS
SELECT v.customer_id AS cid, v.amount AS amt
FROM mart.v_l1 AS v;


-- -------------------------------------------------------------------------------------
-- S8 · a temp table over that temp table
-- -------------------------------------------------------------------------------------
CREATE TEMP TABLE t_b AS
SELECT a.cid, a.amt * 2 AS amt2
FROM t_a AS a;


-- -------------------------------------------------------------------------------------
-- S9 · the second temp table read into a mart table
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (customers, revenue)
SELECT b.cid, b.amt2
FROM t_b AS b;
