-- =====================================================================================
-- B7 · T1 · BREADTH — is each lineage-creating DDL form supported at all?
--
-- Objective card: stress/pg/b7_ddl_lineage/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql. PostgreSQL: 17 (generated columns 12+, identity
--                 10+, declarative partitions 10+).
--
-- ORDER MATTERS: objects created here are read further down (D-31). Conventions: every
-- earlier decision, plus D-31 (in-script objects registered; views inlined, tables are
-- nodes), D-32 (REFRESH re-derives), D-33 (plain CREATE TABLE counted; generated columns
-- derive) and D-34 (partition / child flows to parent). Every node is schema-qualified;
-- a temp table is pg_temp.<name>.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · CREATE TABLE AS, grouped and filtered
-- -------------------------------------------------------------------------------------
CREATE TABLE mart.ch_rev AS
SELECT o.channel, SUM(o.amount) AS rev
FROM staging.orders AS o
WHERE o.status = 'PAID'
GROUP BY o.channel;


-- -------------------------------------------------------------------------------------
-- S2 · CREATE TABLE AS with a column list naming the columns
-- -------------------------------------------------------------------------------------
CREATE TABLE mart.ch_pair (ch, amt) AS
SELECT o.channel, o.amount
FROM staging.orders AS o;


-- -------------------------------------------------------------------------------------
-- S3 · CREATE TABLE AS ... WITH NO DATA: the shape, no rows, no edges
-- -------------------------------------------------------------------------------------
CREATE TABLE mart.ch_shape AS
SELECT o.channel
FROM staging.orders AS o
WITH NO DATA;


-- -------------------------------------------------------------------------------------
-- S4 · SELECT ... INTO TEMP: a temp table, pg_temp.tmp_cust
-- -------------------------------------------------------------------------------------
SELECT c.id, c.email
INTO TEMP TABLE tmp_cust
FROM staging.customers AS c
WHERE c.status = 'A';


-- -------------------------------------------------------------------------------------
-- S5 · reading the temp table: a node (D-31), bound with certainty (pg_temp is first)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (customer_id, full_name)
SELECT t.id, t.email
FROM tmp_cust AS t;


-- -------------------------------------------------------------------------------------
-- S6 · CREATE VIEW with a WHERE
-- -------------------------------------------------------------------------------------
CREATE VIEW mart.v_paid AS
SELECT o.id, o.channel, o.amount
FROM staging.orders AS o
WHERE o.status = 'PAID';


-- -------------------------------------------------------------------------------------
-- S7 · reading the view: inlined to its base table, its WHERE carried (D-31)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel, revenue)
SELECT v.channel, SUM(v.amount)
FROM mart.v_paid AS v
GROUP BY v.channel;


-- -------------------------------------------------------------------------------------
-- S8 · a view with a column list
-- -------------------------------------------------------------------------------------
CREATE VIEW mart.v_cust (cid, nm) AS
SELECT c.id, c.full_name
FROM staging.customers AS c;


-- -------------------------------------------------------------------------------------
-- S9 · a view on a view: its lineage reaches the base table through the first view
-- -------------------------------------------------------------------------------------
CREATE VIEW mart.v_cust2 AS
SELECT v.cid, upper(v.nm) AS nm
FROM mart.v_cust AS v;


-- -------------------------------------------------------------------------------------
-- S10 · a materialized view: holds data, so it is a node
-- -------------------------------------------------------------------------------------
CREATE MATERIALIZED VIEW mart.mv_store AS
SELECT s.region, COUNT(s.id) AS n
FROM core.dim_store AS s
GROUP BY s.region;


-- -------------------------------------------------------------------------------------
-- S11 · REFRESH: the definition is known, so its lineage is emitted again (D-32)
-- -------------------------------------------------------------------------------------
REFRESH MATERIALIZED VIEW mart.mv_store;


-- -------------------------------------------------------------------------------------
-- S12 · reading the materialized view: its own columns are the sources
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (region, customers)
SELECT m.region, m.n
FROM mart.mv_store AS m;


-- -------------------------------------------------------------------------------------
-- S13 · CREATE TABLE with generated columns (D-33)
-- -------------------------------------------------------------------------------------
CREATE TABLE mart.price_band (
    product_id  bigint,
    list_price  numeric(12,2),
    cost        numeric(12,2),
    margin      numeric(12,2) GENERATED ALWAYS AS (list_price - cost) STORED,
    band        text GENERATED ALWAYS AS (CASE WHEN list_price > 100 THEN 'HI' ELSE 'LO' END) STORED
);


-- -------------------------------------------------------------------------------------
-- S14 · inserting into the table created above: its columns come from S13
-- -------------------------------------------------------------------------------------
INSERT INTO mart.price_band (product_id, list_price, cost)
SELECT p.id, p.list_price, p.cost
FROM staging.products AS p;


-- -------------------------------------------------------------------------------------
-- S15 · LIKE copies structure only
-- -------------------------------------------------------------------------------------
CREATE TABLE mart.orders_shape (LIKE staging.orders INCLUDING ALL);


-- -------------------------------------------------------------------------------------
-- S16 · PARTITION OF: the partition's rows are the parent's (D-34)
-- -------------------------------------------------------------------------------------
CREATE TABLE staging.web_events_2024 PARTITION OF staging.web_events
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');


-- -------------------------------------------------------------------------------------
-- S17a · a table shaped like the parent
-- -------------------------------------------------------------------------------------
CREATE TABLE staging.web_events_2023 (LIKE staging.web_events);

-- S17 · ATTACH it as a partition (D-34)
ALTER TABLE staging.web_events
    ATTACH PARTITION staging.web_events_2023
    FOR VALUES FROM ('2023-01-01') TO ('2024-01-01');


-- -------------------------------------------------------------------------------------
-- S18 · INHERITS: the child's inherited columns flow to the parent; its own do not
-- -------------------------------------------------------------------------------------
CREATE TABLE staging.orders_archive (
    archived_at timestamptz
) INHERITS (staging.orders);


-- -------------------------------------------------------------------------------------
-- S19 · DETACH moves nothing
-- -------------------------------------------------------------------------------------
ALTER TABLE staging.web_events DETACH PARTITION staging.web_events_2023;


-- -------------------------------------------------------------------------------------
-- S20a · an identity column: no lineage of its own
-- -------------------------------------------------------------------------------------
CREATE TABLE mart.seq_demo (
    id      bigint GENERATED ALWAYS AS IDENTITY,
    label   text
);

-- S20 · OVERRIDING SYSTEM VALUE lets the INSERT supply the identity value
INSERT INTO mart.seq_demo (id, label) OVERRIDING SYSTEM VALUE
SELECT c.id, c.full_name
FROM staging.customers AS c;
