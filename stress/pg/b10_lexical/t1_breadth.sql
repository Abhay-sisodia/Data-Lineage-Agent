-- =====================================================================================
-- B10 · T1 · BREADTH — is each lexical form supported at all?
--
-- Objective card: stress/pg/b10_lexical/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql, plus pg_trgm and an operator mart.=== (text, text)
--                 assumed installed. PostgreSQL: 17. Run with psql (meta-commands).
--
-- Conventions: every earlier decision, plus D-35 (a prepared statement's lineage is
-- emitted at EXECUTE), D-36 (parameters and psql variables are external values),
-- D-37 (meta-commands skipped; \i declared, \copy refused) and D-38 (a user-defined
-- operator reads like an unknown function). Every node is schema-qualified.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · a plain dollar-quoted literal in WHERE
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel)
SELECT o.channel
FROM staging.orders AS o
WHERE o.status = $$PAID$$;


-- -------------------------------------------------------------------------------------
-- S2 · a tagged dollar quote holding $$, a quote and a semicolon
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (segment)
SELECT $tag$it's $$ok$$; really$tag$ || c.segment
FROM staging.customers AS c;


-- -------------------------------------------------------------------------------------
-- S3 · nested dollar quotes: the inner tag is text inside the outer literal
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (region)
SELECT $outer$ a $inner$ b; $inner$ c $outer$ || s.region
FROM core.dim_store AS s;


-- -------------------------------------------------------------------------------------
-- S4 · an E'' string with escaped quote and backslash
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (customer_id, full_name)
SELECT c.id, E'it\'s \\ ' || c.full_name
FROM staging.customers AS c;


-- -------------------------------------------------------------------------------------
-- S5 · U&'' strings, with the default escape and with UESCAPE
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (country_code, level)
SELECT c.country_code, U&'d\0061t\+000061' || c.segment
FROM staging.customers AS c
WHERE c.status <> U&'d!0061t!+000061' UESCAPE '!';


-- -------------------------------------------------------------------------------------
-- S6 · B'' and X'' bit strings in WHERE
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (store_id, gross_amount)
SELECT o.store_id, o.amount
FROM staging.orders AS o
WHERE (o.store_id::bit(8) & B'00001111') = X'0F';


-- -------------------------------------------------------------------------------------
-- S7 · comments inside a statement: a line comment and a nested block comment
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (customer_id, full_name)
SELECT c.id,             -- FROM staging.payments AS p (not code)
       /* outer /* inner; SELECT p.method */ still a comment; */ c.full_name
FROM staging.customers AS c;


/* A whole statement commented out, between statements; it must give nothing:
   INSERT INTO mart.kpi_rollup (level) SELECT p.method FROM staging.payments AS p; (commented out)
*/


-- -------------------------------------------------------------------------------------
-- S8 · PREPARE an INSERT with two parameters: counted, no edges (D-35)
-- -------------------------------------------------------------------------------------
PREPARE p_rev(text, numeric) AS
INSERT INTO mart.kpi_rollup (level, revenue)
SELECT o.channel, o.amount * $2
FROM staging.orders AS o
WHERE o.status = $1;


-- -------------------------------------------------------------------------------------
-- S9 · EXECUTE it: the edges, at this line
-- -------------------------------------------------------------------------------------
EXECUTE p_rev('PAID', 1.1);


-- -------------------------------------------------------------------------------------
-- S10 · EXECUTE it again: the same edges, again
-- -------------------------------------------------------------------------------------
EXECUTE p_rev('SHIPPED', 1.0);


-- -------------------------------------------------------------------------------------
-- S11 · PREPARE a SELECT
-- -------------------------------------------------------------------------------------
PREPARE p_cust(bigint) AS
SELECT c.full_name, c.email
FROM staging.customers AS c
WHERE c.id = $1;


-- -------------------------------------------------------------------------------------
-- S12 · EXECUTE the SELECT: lands in <result>
-- -------------------------------------------------------------------------------------
EXECUTE p_cust(42);


-- -------------------------------------------------------------------------------------
-- S13 · CREATE TABLE AS EXECUTE: a write into the new table (D-31, D-35)
-- -------------------------------------------------------------------------------------
CREATE TABLE mart.cust_pick AS EXECUTE p_cust(7);


-- -------------------------------------------------------------------------------------
-- S14 · DEALLOCATE: counted, no edges
-- -------------------------------------------------------------------------------------
DEALLOCATE p_cust;


-- -------------------------------------------------------------------------------------
-- S15 · EXECUTE after DEALLOCATE: unknown, declared source_unavailable
-- -------------------------------------------------------------------------------------
EXECUTE p_cust(1);


-- -------------------------------------------------------------------------------------
-- S16 · OPERATOR(pg_catalog.||) is ||
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (segment)
SELECT c.full_name OPERATOR(pg_catalog.||) c.email
FROM staging.customers AS c;


-- -------------------------------------------------------------------------------------
-- S17 · OPERATOR(pg_catalog.*) is *
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (revenue)
SELECT p.amount OPERATOR(pg_catalog.*) 2
FROM staging.payments AS p;


-- -------------------------------------------------------------------------------------
-- S18 · an extension operator, pg_trgm's <-> (D-38)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (avg_basket)
SELECT c.full_name <-> c.email
FROM staging.customers AS c;


-- -------------------------------------------------------------------------------------
-- S19 · text search @@ in WHERE
-- -------------------------------------------------------------------------------------
INSERT INTO mart.product_performance (product_id)
SELECT p.id
FROM staging.products AS p
WHERE to_tsvector(p.name) @@ to_tsquery('shoe');


-- -------------------------------------------------------------------------------------
-- S20 · a user-defined operator in WHERE (D-38)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (segment)
SELECT p.brand
FROM staging.products AS p
WHERE p.sku OPERATOR(mart.===) p.name;


\set st 'PAID'
\timing on
\pset null '(null)'
-- -------------------------------------------------------------------------------------
-- S21 · a psql variable as a value (D-36); the meta-commands above are skipped
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (region)
SELECT o.channel
FROM staging.orders AS o
WHERE o.status = :'st';


\echo 'INSERT INTO mart.kpi_rollup (level) SELECT o.status FROM staging.orders o; done'
\i deploy/extra_views.sql
\copy staging.orders FROM 'orders.csv' WITH (FORMAT csv)
-- -------------------------------------------------------------------------------------
-- S22 · after \echo, \i and \copy: \i declared, \copy refused, \echo nothing (D-37)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (region)
SELECT s.region
FROM core.dim_store AS s;


-- -------------------------------------------------------------------------------------
-- S23 · a query sent with \gset writes psql variables, nothing else (D-37)
-- -------------------------------------------------------------------------------------
SELECT max(o.amount) AS top_amount FROM staging.orders AS o \gset
\echo :top_amount
SELECT :'top_amount' AS echoed;


-- -------------------------------------------------------------------------------------
-- S24 · two statements on one line
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (customers) SELECT c.id FROM staging.customers AS c; INSERT INTO mart.kpi_rollup (orders) SELECT o.id FROM staging.orders AS o;


;
;;
-- -------------------------------------------------------------------------------------
-- S25 · after the empty statements above, a statement
-- -------------------------------------------------------------------------------------
INSERT INTO mart.events_log (kind)
SELECT e.kind
FROM staging.events_log AS e;
