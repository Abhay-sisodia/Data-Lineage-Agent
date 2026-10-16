-- =====================================================================================
-- B10 · T4 · ADVERSARIAL — does the lexer get fooled?
--
-- Objective card: stress/pg/b10_lexical/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql. PostgreSQL: 17, standard_conforming_strings on
--                 (the default since 9.1). Run with psql.
--
-- Every trap here has a naive reading that invents or loses a column. Conventions:
-- every earlier decision, plus D-35..D-38.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · a dollar tag that spells a column: $channel$o.channel$channel$ is a literal
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level)
SELECT $channel$o.channel$channel$ || o.currency
FROM staging.orders AS o;


-- -------------------------------------------------------------------------------------
-- S2 · E'' escapes: the literal is ' || c.email || ', so c.email is text
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (full_name)
SELECT E'\' || c.email || \'' || c.full_name
FROM staging.customers AS c;


-- -------------------------------------------------------------------------------------
-- S3 · a standard string ending in a backslash: 'C:\' is complete, c.email is code
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (full_name)
SELECT 'C:\' || c.email
FROM staging.customers AS c;


-- -------------------------------------------------------------------------------------
-- S4 · -- glued to an operand: o.amount--1 is o.amount and a comment
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (revenue)
SELECT o.amount--1
       AS revenue
FROM staging.orders AS o;


-- -------------------------------------------------------------------------------------
-- S5 · a nested comment that a non-nesting lexer would end at the first */
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (customer_id, region)
SELECT c.id /* outer /* inner */ , c.email AS leaked */ , c.segment
FROM staging.customers AS c;

/* a /* b */ INSERT INTO mart.kpi_rollup (segment) SELECT c.email FROM staging.customers AS c; */


-- -------------------------------------------------------------------------------------
-- S6 · PREPARE with '$2' inside a literal: text, not a parameter
-- -------------------------------------------------------------------------------------
PREPARE p_lit(text) AS
INSERT INTO mart.kpi_rollup (country_code)
SELECT c.country_code
FROM staging.customers AS c
WHERE c.segment = $1
  AND c.email <> '$2';


-- -------------------------------------------------------------------------------------
-- S7 · EXECUTE it
-- -------------------------------------------------------------------------------------
EXECUTE p_lit('B2B');


\set lo 1
-- -------------------------------------------------------------------------------------
-- S8 · :: casts and a [1:2] slice beside an unquoted psql variable
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel)
SELECT array_to_string(o.tags[1:2], ',') || o.amount::text
FROM staging.orders AS o
WHERE o.id > :lo;


-- -------------------------------------------------------------------------------------
-- S9 · quoted identifiers that contain /* and --
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (full_name)
SELECT d."a/*b"
FROM (
    SELECT c.email AS "a/*b", c.status AS "x -- y"
    FROM staging.customers AS c
) AS d
WHERE d."x -- y" = 'A';


-- -------------------------------------------------------------------------------------
-- S10 · a U&"" identifier spelling a column: U&"\0069d" is id
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (customers)
SELECT c.U&"\0069d"
FROM staging.customers AS c;


-- -------------------------------------------------------------------------------------
-- S11 · EXECUTE before PREPARE: unknown at this point
-- -------------------------------------------------------------------------------------
EXECUTE p_late('OPEN');


-- -------------------------------------------------------------------------------------
-- S12 · now PREPARE it
-- -------------------------------------------------------------------------------------
PREPARE p_late(text) AS
INSERT INTO mart.channel_mix (region)
SELECT s.region
FROM core.dim_store AS s
WHERE s.status = $1;


-- -------------------------------------------------------------------------------------
-- S13 · and EXECUTE it: known now
-- -------------------------------------------------------------------------------------
EXECUTE p_late('OPEN');


-- -------------------------------------------------------------------------------------
-- S14 · DEALLOCATE ALL
-- -------------------------------------------------------------------------------------
DEALLOCATE ALL;


-- -------------------------------------------------------------------------------------
-- S15 · EXECUTE after DEALLOCATE ALL: unknown again
-- -------------------------------------------------------------------------------------
EXECUTE p_lit('B2C');


\set tbl staging.orders
-- -------------------------------------------------------------------------------------
-- S16 · a psql variable as an identifier: refused or declared, never guessed (D-36)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level)
SELECT t.channel
FROM :tbl AS t;


-- -------------------------------------------------------------------------------------
-- S17 · dollar quotes glued to operators; ':st' inside a literal is not interpolated
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (orders)
SELECT o.id
FROM staging.orders AS o
WHERE o.status=$$PAID$$ AND o.channel<>$q$web$q$ AND o.discount_code <> ':st';


-- -------------------------------------------------------------------------------------
-- S18 · E'a\\' ends after the escaped backslash: c.email is code
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (segment)
SELECT E'a\\' || c.email
FROM staging.customers AS c;


-- -------------------------------------------------------------------------------------
-- S19 · an identifier containing $: a$1 is a name, not a parameter
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (region)
SELECT x.a$1
FROM (
    SELECT c.segment AS a$1
    FROM staging.customers AS c
) AS x;
