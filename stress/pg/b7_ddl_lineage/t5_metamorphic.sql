-- =====================================================================================
-- B7 · T5 · METAMORPHIC — do equivalent DDL spellings give identical lineage?
--
-- Objective card: stress/pg/b7_ddl_lineage/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql. PostgreSQL: 17.
--
-- Pairs tagged `-- @pair Pn A|B`, checked by stress/pg/triage.py. P1-P5 MUST BE EQUAL.
-- Each pair writes the SAME object, dropped between the spellings, so the edges compare.
--
-- Then one realistic staging-to-mart build: a temp staging table, a view over it, a
-- materialized view over the view joined to a dimension, a REFRESH, and the mart load.
-- ORDER MATTERS. Every node is schema-qualified (D-25); a temp table is pg_temp.<name>.
-- =====================================================================================


-- P1 · CREATE TABLE AS vs CREATE TABLE + INSERT ... SELECT
-- @pair P1 A
CREATE TABLE mart.p1 AS
SELECT c.id, c.segment FROM staging.customers AS c;

DROP TABLE mart.p1;
CREATE TABLE mart.p1 (id bigint, segment text);

-- @pair P1 B
INSERT INTO mart.p1 (id, segment)
SELECT c.id, c.segment FROM staging.customers AS c;


-- P2 · SELECT INTO vs CREATE TABLE AS
-- @pair P2 A
SELECT o.id, o.channel INTO mart.p2 FROM staging.orders AS o;

DROP TABLE mart.p2;

-- @pair P2 B
CREATE TABLE mart.p2 AS
SELECT o.id, o.channel FROM staging.orders AS o;


-- P3 · reading a view vs the same query as a derived table
CREATE VIEW mart.v_seg AS
SELECT c.segment FROM staging.customers AS c WHERE c.status = 'A';

-- @pair P3 A
INSERT INTO mart.kpi_rollup (segment)
SELECT v.segment FROM mart.v_seg AS v;

-- @pair P3 B
INSERT INTO mart.kpi_rollup (segment)
SELECT v.segment
FROM (SELECT c.segment FROM staging.customers AS c WHERE c.status = 'A') AS v;


-- P4 · PARTITION OF vs CREATE TABLE (LIKE) + ATTACH PARTITION
-- @pair P4 A
CREATE TABLE staging.web_events_p4 PARTITION OF staging.web_events
    FOR VALUES FROM ('2020-01-01') TO ('2021-01-01');

DROP TABLE staging.web_events_p4;
CREATE TABLE staging.web_events_p4 (LIKE staging.web_events);

-- @pair P4 B
ALTER TABLE staging.web_events
    ATTACH PARTITION staging.web_events_p4 FOR VALUES FROM ('2020-01-01') TO ('2021-01-01');


-- P5 · a view's column list vs projection aliases
-- @pair P5 A
CREATE VIEW mart.v_p5 (k, v) AS
SELECT o.id, o.amount FROM staging.orders AS o;

DROP VIEW mart.v_p5;

-- @pair P5 B
CREATE VIEW mart.v_p5 AS
SELECT o.id AS k, o.amount AS v FROM staging.orders AS o;


-- -------------------------------------------------------------------------------------
-- R1a · staging: paid orders into a temp table
-- -------------------------------------------------------------------------------------
CREATE TEMP TABLE stg_paid AS
SELECT o.id, o.customer_id, o.amount, o.order_ts
FROM staging.orders AS o
WHERE o.status = 'PAID';

-- R1b · a view of monthly revenue per customer, over the temp table
CREATE VIEW mart.v_cust_month AS
SELECT s.customer_id, date_trunc('month', s.order_ts)::date AS m, SUM(s.amount) AS rev
FROM stg_paid AS s
GROUP BY s.customer_id, date_trunc('month', s.order_ts);

-- R1c · a materialized view joining the view to the customer dimension
CREATE MATERIALIZED VIEW mart.mv_cust_month AS
SELECT v.customer_id, v.m, v.rev, c.segment
FROM mart.v_cust_month AS v
JOIN staging.customers AS c ON c.id = v.customer_id;

-- R1d · the nightly refresh
REFRESH MATERIALIZED VIEW mart.mv_cust_month;

-- R1e · the mart load from the materialized view
INSERT INTO mart.customer_summary (customer_id, segment, lifetime_value)
SELECT mv.customer_id, mv.segment, SUM(mv.rev)
FROM mart.mv_cust_month AS mv
GROUP BY mv.customer_id, mv.segment;
