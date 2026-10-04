-- =====================================================================================
-- B7 · T4 · ADVERSARIAL — does DDL lineage get fooled by the script's own history?
--
-- Objective card: stress/pg/b7_ddl_lineage/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql. PostgreSQL: 17.
--
-- Traps: a view replaced mid-script (each read sees the definition in force at that
-- line); a view dropped and replaced by a TABLE of the same name; a temp table that
-- shadows a real table's name for unqualified references; a view over `*`; CREATE TABLE
-- IF NOT EXISTS ... AS on a table that already exists; a generated CASE over two columns.
-- ORDER MATTERS. Every node is schema-qualified (D-25).
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · a view, first definition
-- -------------------------------------------------------------------------------------
CREATE VIEW mart.v_x AS
SELECT o.channel AS c
FROM staging.orders AS o;

-- S2 · read under the first definition
INSERT INTO mart.kpi_rollup (level)
SELECT v.c FROM mart.v_x AS v;


-- -------------------------------------------------------------------------------------
-- S3 · CREATE OR REPLACE: a different column behind the same name
-- -------------------------------------------------------------------------------------
CREATE OR REPLACE VIEW mart.v_x AS
SELECT o.currency::text AS c
FROM staging.orders AS o;

-- S4 · read under the second definition
INSERT INTO mart.kpi_rollup (segment)
SELECT v.c FROM mart.v_x AS v;


-- -------------------------------------------------------------------------------------
-- S5a · the view dropped, and a TABLE of the same name created
-- -------------------------------------------------------------------------------------
DROP VIEW mart.v_x;

-- S5b · the replacing table
CREATE TABLE mart.v_x (c text);

-- S5 · read now: a table, so a node - not either view's query
INSERT INTO mart.kpi_rollup (region)
SELECT v.c FROM mart.v_x AS v;


-- -------------------------------------------------------------------------------------
-- S6a · a temp table named `orders`
-- -------------------------------------------------------------------------------------
CREATE TEMP TABLE orders (id bigint, note text);

-- S6b · filled from payments, through the unqualified name
INSERT INTO orders (id, note)
SELECT p.id, p.method
FROM staging.payments AS p;

-- S6 · unqualified `orders` is the temp table; staging.orders is the real one
INSERT INTO mart.channel_mix (channel, region)
SELECT x.note, y.channel
FROM orders AS x
JOIN staging.orders AS y ON y.id = x.id;


-- -------------------------------------------------------------------------------------
-- S7a · a view over `*`
-- -------------------------------------------------------------------------------------
CREATE VIEW mart.v_star AS
SELECT * FROM core.dim_carrier;

-- S7 · read through the starred view
INSERT INTO mart.carrier_scorecard (carrier_id, carrier_name)
SELECT v.id, v.name
FROM mart.v_star AS v;


-- -------------------------------------------------------------------------------------
-- S8 · CREATE TABLE IF NOT EXISTS ... AS on a table that exists: nothing happens
-- -------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mart.channel_mix AS
SELECT o.channel FROM staging.orders AS o;


-- -------------------------------------------------------------------------------------
-- S9 · a generated CASE over two columns: conditional (D-19, D-33)
-- -------------------------------------------------------------------------------------
CREATE TABLE mart.tiered (
    qty     int,
    price   numeric,
    tier    text GENERATED ALWAYS AS (
        CASE WHEN qty > 10 AND price > 5 THEN 'BULK' ELSE 'STD' END
    ) STORED
);
