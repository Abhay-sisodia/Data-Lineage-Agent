-- =====================================================================================
-- B9 · T1 · BREADTH — is each name-resolution rule supported at all?
--
-- Objective card: stress/pg/b9_names_resolution/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql (with B9's additive objects). PostgreSQL: 17.
--
-- ORDER MATTERS IN THIS FILE: search_path is session state. S1-S3 run with no path
-- known (D-24: the default schema, else a unique name, else ambiguous). S4 sets one, S8
-- replaces it, S9 resets it (D-23). Every node is schema-qualified (D-25). The
-- unqualified references before S4 assume a role-level search_path containing core and
-- staging; the analyser cannot see that, which is exactly what the declared boundary says.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · no path known; the name is in the default schema (core): bound, and declared
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (region, country_code)
SELECT s.region, s.country_code
FROM dim_store AS s;


-- -------------------------------------------------------------------------------------
-- S2 · no path known; the name exists only in staging: bound by uniqueness (D-24)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.carrier_scorecard (carrier_id, shipments)
SELECT sh.carrier_id, COUNT(sh.id)
FROM shipments AS sh
GROUP BY sh.carrier_id;


-- -------------------------------------------------------------------------------------
-- S3 · no path known; events_log is in staging AND mart, neither the default: ambiguous,
--      declared, no edge from either (D-24)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level)
SELECT e.kind
FROM events_log AS e;


-- -------------------------------------------------------------------------------------
-- S4 · SET search_path: from here unqualified names are certain (D-23)
-- -------------------------------------------------------------------------------------
SET search_path TO staging, core;


-- -------------------------------------------------------------------------------------
-- S5 · `ledger` is in staging and core; staging is first on the path, so it wins -
--      although core is the dictionary's default
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (segment, revenue)
SELECT l.account, l.amount
FROM ledger AS l
WHERE l.currency = 'EUR';


-- -------------------------------------------------------------------------------------
-- S6 · a name only in the path's second schema falls through to it
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (region)
SELECT w.region
FROM dim_warehouse AS w;


-- -------------------------------------------------------------------------------------
-- S7 · a schema qualifier overrides the path
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (segment, revenue)
SELECT l.account, l.balance
FROM core.ledger AS l;


-- -------------------------------------------------------------------------------------
-- S8a · a new path: mart only
-- -------------------------------------------------------------------------------------
SET search_path TO mart;

-- S8 · under the mart-only path; the TARGET is unqualified too
INSERT INTO kpi_rollup (level)
SELECT e.kind
FROM events_log AS e
WHERE e.rolled_up;


-- -------------------------------------------------------------------------------------
-- S9a · RESET: no path known again, so `ledger` goes to the default schema (D-23, D-24)
-- -------------------------------------------------------------------------------------
RESET search_path;

-- S9 · after RESET: the default schema, declared
INSERT INTO mart.kpi_rollup (segment)
SELECT l.status
FROM ledger AS l;


-- -------------------------------------------------------------------------------------
-- S10 · quoted identifiers: "Amount" and amount are two columns, in the source and in
--       the target; "order", "user" and "Status Code" are ordinary quoted names
-- -------------------------------------------------------------------------------------
INSERT INTO mart."FeedSummary" ("CustomerID", "Total", total, "order", "Status Code")
SELECT f."CustomerID", f."Amount", f.amount, f."order", f."Status Code"
FROM staging."LegacyFeed" AS f
WHERE f."user" IS NOT NULL;


-- -------------------------------------------------------------------------------------
-- S11 · unquoted upper case folds; a quoted lower-case name is the same name
-- -------------------------------------------------------------------------------------
INSERT INTO MART.KPI_ROLLUP (REGION, "segment")
SELECT C.COUNTRY_CODE, "c"."segment"
FROM STAGING.CUSTOMERS AS C;


-- -------------------------------------------------------------------------------------
-- S12 · three-part column names, no alias
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel, revenue)
SELECT staging.orders.channel, staging.orders.amount
FROM staging.orders
WHERE staging.orders.status = 'PAID';


-- -------------------------------------------------------------------------------------
-- S13 · SELECT * from a table into the same-shaped table, column list omitted
-- -------------------------------------------------------------------------------------
INSERT INTO core.ledger
SELECT *
FROM core.ledger
WHERE status = 'OPEN';


-- -------------------------------------------------------------------------------------
-- S14 · t.* over a derived table joined with USING
--   x.* is (id, amount) - x's own id, which is orders.customer_id. USING is structural.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (full_name, customer_id, revenue)
SELECT c.full_name, x.*
FROM staging.customers AS c
JOIN (
    SELECT o.customer_id AS id, o.amount
    FROM staging.orders AS o
) AS x USING (id);


-- -------------------------------------------------------------------------------------
-- S15a · FULL JOIN USING: the merged store_id is COALESCE(a, b) - two columns, so
--        conditional (D-19); neither side can lose a row, so nothing filters (D-14)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (store_id, gross_amount, net_amount)
SELECT store_id, a.amount, b.net_amount
FROM staging.orders AS a
FULL JOIN core.fct_order_line AS b USING (store_id);


-- -------------------------------------------------------------------------------------
-- S15b · RIGHT JOIN USING: the merged column is the RIGHT input's
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (store_id, net_amount)
SELECT store_id, b.net_amount
FROM staging.orders AS a
RIGHT JOIN core.fct_order_line AS b USING (store_id);


-- -------------------------------------------------------------------------------------
-- S15c · NATURAL JOIN: staging.ledger and core.ledger share id and account; the merged
--        account is the left input's
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (segment, revenue)
SELECT account, c.balance
FROM staging.ledger AS s
NATURAL JOIN core.ledger AS c;


-- -------------------------------------------------------------------------------------
-- S16 · COLLATE in the projection (derived) and in an ORDER BY under LIMIT (D-9)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (segment)
SELECT c.segment COLLATE "C"
FROM staging.customers AS c
ORDER BY c.full_name COLLATE "C"
LIMIT 10;
