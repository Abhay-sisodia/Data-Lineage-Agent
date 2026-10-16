-- =====================================================================================
-- B1 · T4 · ADVERSARIAL — does it get fooled?
--
-- Objective card: stress/pg/b1_core_select/OBJECTIVES.md, section T4.
-- Schema:         stress/pg/schema.sql.   PostgreSQL: 17 (all constructs 9.6+).
--
-- Every statement is built so that a resolver which GUESSES produces a confident,
-- plausible and wrong edge. The key's forbidden list carries as much of this test as
-- its edge list: a wrong edge here is worse than a missing one, because it reads as a
-- finding about the data.
--
-- All of it is valid PostgreSQL. Where a trap leans on a specific resolution rule, the
-- rule is named in the statement's header.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · unqualified columns with two relations in scope
--   Each unqualified name exists in exactly ONE of the two relations, so PostgreSQL
--   accepts it. `country_code` and `opened_on` belong to the SECOND relation: a resolver
--   that binds an unqualified name to the first table in FROM gets them wrong.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (customer_id, revenue, region, full_name)
SELECT
    o.id,
    amount,
    country_code,
    discount_code
FROM staging.orders AS o
JOIN core.dim_store AS s
  ON s.id = o.store_id
WHERE channel = 'WEB'
  AND opened_on < DATE '2020-01-01';


-- -------------------------------------------------------------------------------------
-- S2 · a table alias spelled like another table
--   `customers` here is staging.ORDERS. Nothing in this statement reads
--   staging.customers.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (customer_id, full_name, status)
SELECT
    customers.id,
    customers.discount_code,
    customers.status
FROM staging.orders AS customers
WHERE customers.channel = 'STORE';


-- -------------------------------------------------------------------------------------
-- S3 · one alias, two scopes, two tables
--   `x` is fct_shipment in the first derived table and dim_carrier in the second. Only
--   dim_carrier is filtered on status.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.carrier_scorecard (carrier_id, carrier_name, cost_per_kg)
SELECT a.cid, b.cname, a.cost_kg
FROM (
    SELECT x.carrier_id AS cid,
           x.cost / NULLIF(x.weight_kg, 0) AS cost_kg
    FROM core.fct_shipment AS x
) AS a
JOIN (
    SELECT x.id AS cid, x.name AS cname
    FROM core.dim_carrier AS x
    WHERE x.status = 'ACTIVE'
) AS b
  ON b.cid = a.cid;


-- -------------------------------------------------------------------------------------
-- S4 · an output alias that is also an input column name
--   The output column `cost` is sh.weight_kg. PostgreSQL resolves the two clauses
--   DIFFERENTLY:
--     WHERE cost > 0      -> the INPUT column sh.cost (WHERE cannot see output names)
--     ORDER BY cost DESC  -> the OUTPUT column, i.e. weight_kg (output names win in a
--                            bare ORDER BY name)
--   With a LIMIT, both decide which rows land (D-9), so both are filters - on two
--   different columns.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (customer_id, revenue)
SELECT
    sh.order_id,
    sh.weight_kg AS cost
FROM staging.shipments AS sh
WHERE cost > 0
ORDER BY cost DESC
LIMIT 20;


-- -------------------------------------------------------------------------------------
-- S5 · ordinals: GROUP BY 1, 2 and ORDER BY 4
--   `4` is the fourth output column, SUM(o.amount). Read as a literal, the ordering
--   would decide nothing; read as an ordinal it is a post-aggregation filter (D-9, D-6).
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel, region, orders, revenue)
SELECT
    o.channel,
    o.currency,
    COUNT(o.id),
    SUM(o.amount)
FROM staging.orders AS o
GROUP BY 1, 2
ORDER BY 4 DESC
LIMIT 5;


-- -------------------------------------------------------------------------------------
-- S6 · keyword-like identifiers, quoted and unquoted
--   year, month and name are non-reserved in PostgreSQL and legal unquoted; "level" is
--   quoted in the target list, "month" quoted on read. The same column spelled two ways
--   is one column.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup ("level", region, segment, orders)
SELECT
    d.year::text,
    d."month"::text,
    w.name,
    d.quarter
FROM core.dim_date AS d
CROSS JOIN core.dim_warehouse AS w
WHERE d."year" = 2025
  AND w.name <> 'CLOSED';


-- -------------------------------------------------------------------------------------
-- S7 · construct names where they are not constructs
--   Refusal-register keywords inside string literals and comments. The statement is a
--   plain projection with a WHERE; nothing here is refusable.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level, region, segment)
SELECT
    'CONNECT BY PRIOR',              -- ROLLUP ( is in this comment, not in the query
    c.name,
    /* JSON_TABLE( and MATCH_RECOGNIZE live in this comment only */
    'see JSON_TABLE( in the docs'
FROM core.dim_category AS c
WHERE c.department <> 'START WITH x';


-- -------------------------------------------------------------------------------------
-- S8 · a self-join: two aliases of one table, each feeding different columns
--   child supplies the key and the name, parent supplies segment and region. At node
--   level both are dim_customer, so the trap is CROSSING: a name landing in segment.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (customer_id, full_name, segment, region)
SELECT
    child.customer_key,
    child.full_name,
    parent.segment,
    parent.region
FROM core.dim_customer AS child
JOIN core.dim_customer AS parent
  ON parent.customer_id = child.customer_id
 AND parent.is_current
WHERE NOT child.is_current;


-- -------------------------------------------------------------------------------------
-- S9 · COUNT(*), COUNT(col) and COUNT(1) side by side
--   Only COUNT(c.email) has a value source. All three are aggregated columns, so the
--   grouping influences all three.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (segment, orders, customers, revenue)
SELECT
    c.segment,
    COUNT(*),
    COUNT(c.email),
    COUNT(1)
FROM staging.customers AS c
GROUP BY c.segment;


-- -------------------------------------------------------------------------------------
-- S10 · values from nowhere
--   now(), a typed NULL and a string constant. One real column, so exactly one edge.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (sale_date, store_id, net_amount, region)
SELECT
    now()::date,
    w.id,
    NULL::numeric,
    'n/a'
FROM core.dim_warehouse AS w;


-- -------------------------------------------------------------------------------------
-- S11 · columns that appear ONLY inside an ON clause
--   currency on both sides is part of the join condition and nowhere else (D-5).
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel, revenue)
SELECT
    o.status,
    p.amount
FROM staging.orders AS o
JOIN staging.payments AS p
  ON p.order_id = o.id
 AND p.currency = o.currency;


-- -------------------------------------------------------------------------------------
-- S12 · one column, three targets, three transforms
--   net_amount lands as itself, through LAG (derived) and through SUM OVER
--   (aggregated). A transform leaking from one target to the next is the failure.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.product_performance (product_id, revenue, prev_month_revenue, running_revenue)
SELECT
    f.product_key,
    f.net_amount,
    LAG(f.net_amount) OVER w,
    SUM(f.net_amount) OVER w
FROM core.fct_order_line AS f
WINDOW w AS (PARTITION BY f.product_key ORDER BY f.order_date);


-- -------------------------------------------------------------------------------------
-- S13 · a quoted mixed-case alias beside its unquoted lower-case twin
--   "Region" and region are DIFFERENT identifiers: the quoted one keeps its case. Here
--   "Region" is dim_customer.segment and region is dim_customer.region.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (region, segment)
SELECT
    x."Region",
    x.region
FROM (
    SELECT d.segment AS "Region",
           d.region  AS region
    FROM core.dim_customer AS d
) AS x;


-- -------------------------------------------------------------------------------------
-- S14 · a cross-relation comparison in WHERE after a LEFT JOIN (D-11's exception)
--   `s.status = c.status` touches the NULL-SUPPLYING side. It removes every carrier
--   with no shipment, which the join had kept - a real filter, not a restated join.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level, region, orders)
SELECT
    c.name,
    c.mode,
    s.shipment_id
FROM core.dim_carrier AS c
LEFT JOIN core.fct_shipment AS s
       ON s.carrier_id = c.id
WHERE s.status = c.status;


-- -------------------------------------------------------------------------------------
-- S15 · a comma join whose join condition is in WHERE (D-11)
--   The cross-relation EQUALITY is the join, so it is structural. The >= is not covered
--   by D-11 (equality only - see B1-Q2) and stays a filter, as does `s.status = 'OPEN'`.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (sale_date, store_id, gross_amount)
SELECT
    f.order_date,
    s.id,
    f.gross_amount
FROM core.fct_order_line AS f,
     core.dim_store AS s
WHERE f.store_id = s.id
  AND f.order_date >= s.opened_on
  AND s.status = 'OPEN';
