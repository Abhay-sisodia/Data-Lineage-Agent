-- =====================================================================================
-- B7 · T3 · INTERACTION — does DDL lineage compose with B1-B6 rules?
--
-- Objective card: stress/pg/b7_ddl_lineage/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql. PostgreSQL: 17.
--
-- Created objects read by aggregates and phases (B2-06), UPDATE and MERGE (B3, D-11),
-- set operations, documents (D-29) and partitions (D-34). ORDER MATTERS.
-- Every node is schema-qualified (D-25).
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · a grouped view
-- -------------------------------------------------------------------------------------
CREATE VIEW mart.v_store_rev AS
SELECT f.store_id, SUM(f.net_amount) AS rev
FROM core.fct_order_line AS f
GROUP BY f.store_id;


-- -------------------------------------------------------------------------------------
-- S2 · the grouped view read and cut on its aggregate: post-aggregation (B2-06)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (store_id, net_amount)
SELECT v.store_id, v.rev
FROM mart.v_store_rev AS v
WHERE v.rev > 1000;


-- -------------------------------------------------------------------------------------
-- S3 · UPDATE ... FROM the view: the join is structural (D-11); the aggregate cut filters
-- -------------------------------------------------------------------------------------
UPDATE core.dim_store AS d
SET status = 'TOP'
FROM mart.v_store_rev AS v
WHERE v.store_id = d.id
  AND v.rev > 5000;


-- -------------------------------------------------------------------------------------
-- S4 · MERGE USING the view
-- -------------------------------------------------------------------------------------
MERGE INTO mart.sales_daily AS t
USING mart.v_store_rev AS v
ON t.store_id = v.store_id
WHEN MATCHED THEN
    UPDATE SET net_amount = v.rev;


-- -------------------------------------------------------------------------------------
-- S5 · a UNION ALL view, read and grouped by its literal column
-- -------------------------------------------------------------------------------------
CREATE VIEW mart.v_all_amounts AS
SELECT o.amount AS amt, 'order' AS src FROM staging.orders AS o
UNION ALL
SELECT p.amount, 'payment' FROM staging.payments AS p;

-- S5b · the union view read: both arms reach the target; the grouping key is a literal
INSERT INTO mart.kpi_rollup (revenue, level)
SELECT SUM(a.amt), a.src
FROM mart.v_all_amounts AS a
GROUP BY a.src;


-- -------------------------------------------------------------------------------------
-- S6 · a generated column over a jsonb document (D-33, D-29)
-- -------------------------------------------------------------------------------------
CREATE TABLE mart.order_docs (
    id          bigint,
    doc         jsonb,
    channel     text GENERATED ALWAYS AS (doc ->> 'channel') STORED
);

-- S6b · filled from staging
INSERT INTO mart.order_docs (id, doc)
SELECT o.id, o.payload
FROM staging.orders AS o;


-- -------------------------------------------------------------------------------------
-- S7 · a partition created, then written directly (D-34)
-- -------------------------------------------------------------------------------------
CREATE TABLE staging.web_events_2025 PARTITION OF staging.web_events
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');

-- S7b · rows written into the partition are the parent's rows too
INSERT INTO staging.web_events_2025 (id, event_type, event_ts)
SELECT o.id, 'order', o.order_ts
FROM staging.orders AS o
WHERE o.order_ts >= TIMESTAMPTZ '2025-01-01 00:00:00+00';
