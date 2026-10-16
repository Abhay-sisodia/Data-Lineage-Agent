-- =====================================================================================
-- B9 · T5 · METAMORPHIC — do equivalent spellings of a name give identical lineage?
--
-- Objective card: stress/pg/b9_names_resolution/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql (with B9's additive objects). PostgreSQL: 17.
--
-- Pairs tagged `-- @pair Pn A|B`, checked by stress/pg/triage.py. P1-P10 MUST BE EQUAL.
-- P1 B runs under a SET, so it is certain and declares nothing - the pair compares edges.
--
-- Then one realistic multi-schema load: a quoted legacy feed and a ledger, both found
-- through the search_path, joined to a core dimension that the path falls through to.
-- Every node is schema-qualified (D-25).
-- =====================================================================================


-- P1 · schema-qualified vs SET search_path + unqualified
-- @pair P1 A
INSERT INTO mart.kpi_rollup (segment, revenue)
SELECT l.account, l.amount
FROM staging.ledger AS l;

SET search_path TO staging;

-- @pair P1 B
INSERT INTO mart.kpi_rollup (segment, revenue)
SELECT l.account, l.amount
FROM ledger AS l;

RESET search_path;


-- P2 · "amount" (quoted, lower case) vs amount
-- @pair P2 A
INSERT INTO mart.channel_mix (revenue)
SELECT o."amount"
FROM staging.orders AS o;

-- @pair P2 B
INSERT INTO mart.channel_mix (revenue)
SELECT o.amount
FROM staging.orders AS o;


-- P3 · upper case vs lower case, unquoted
-- @pair P3 A
INSERT INTO MART.CHANNEL_MIX (CHANNEL)
SELECT O.CHANNEL
FROM STAGING.ORDERS AS O;

-- @pair P3 B
INSERT INTO mart.channel_mix (channel)
SELECT o.channel
FROM staging.orders AS o;


-- P4 · SELECT * vs the explicit column list
-- @pair P4 A
INSERT INTO core.ledger
SELECT *
FROM core.ledger;

-- @pair P4 B
INSERT INTO core.ledger (id, account, balance, status)
SELECT id, account, balance, status
FROM core.ledger;


-- P5 · t.* vs the explicit list
-- @pair P5 A
INSERT INTO mart.carrier_scorecard (carrier_id, carrier_name)
SELECT x.*
FROM (SELECT c.id, c.name FROM core.dim_carrier AS c) AS x;

-- @pair P5 B
INSERT INTO mart.carrier_scorecard (carrier_id, carrier_name)
SELECT x.id, x.name
FROM (SELECT c.id, c.name FROM core.dim_carrier AS c) AS x;


-- P6 · JOIN USING vs JOIN ON with the left column named
-- @pair P6 A
INSERT INTO mart.sales_daily (store_id, net_amount)
SELECT store_id, b.net_amount
FROM staging.orders AS a
JOIN core.fct_order_line AS b USING (store_id);

-- @pair P6 B
INSERT INTO mart.sales_daily (store_id, net_amount)
SELECT a.store_id, b.net_amount
FROM staging.orders AS a
JOIN core.fct_order_line AS b ON b.store_id = a.store_id;


-- P7 · NATURAL JOIN vs USING over the shared columns
-- @pair P7 A
INSERT INTO mart.kpi_rollup (segment, revenue)
SELECT account, c.balance
FROM staging.ledger AS s
NATURAL JOIN core.ledger AS c;

-- @pair P7 B
INSERT INTO mart.kpi_rollup (segment, revenue)
SELECT account, c.balance
FROM staging.ledger AS s
JOIN core.ledger AS c USING (id, account);


-- P8 · three-part names vs an alias
-- @pair P8 A
INSERT INTO mart.channel_mix (channel, revenue)
SELECT staging.payments.method, staging.payments.amount
FROM staging.payments;

-- @pair P8 B
INSERT INTO mart.channel_mix (channel, revenue)
SELECT p.method, p.amount
FROM staging.payments AS p;


-- P9 · a CTE named like a real table vs a derived table of the same name
-- @pair P9 A
INSERT INTO mart.kpi_rollup (segment, revenue)
WITH ledger AS (
    SELECT c.account, c.balance FROM core.ledger AS c WHERE c.status = 'OPEN'
)
SELECT ledger.account, ledger.balance
FROM ledger;

-- @pair P9 B
INSERT INTO mart.kpi_rollup (segment, revenue)
SELECT ledger.account, ledger.balance
FROM (SELECT c.account, c.balance FROM core.ledger AS c WHERE c.status = 'OPEN') AS ledger;


-- P10 · FULL JOIN USING vs COALESCE of both keys
-- @pair P10 A
INSERT INTO mart.sales_daily (store_id, gross_amount)
SELECT store_id, a.amount
FROM staging.orders AS a
FULL JOIN core.fct_order_line AS b USING (store_id);

-- @pair P10 B
INSERT INTO mart.sales_daily (store_id, gross_amount)
SELECT COALESCE(a.store_id, b.store_id), a.amount
FROM staging.orders AS a
FULL JOIN core.fct_order_line AS b ON b.store_id = a.store_id;


-- -------------------------------------------------------------------------------------
-- R1a · the path for the load: staging first, core behind it
-- -------------------------------------------------------------------------------------
SET search_path TO staging, core;

-- R1 · a realistic multi-schema load
--   feed:  the quoted legacy table, unqualified (staging via the path)
--   book:  ledger totals - staging.ledger, first on the path, though core has one too
--   join:  dim_customer is only in core, so the path falls through to it
--   value: "Total" adds the feed's "Amount" to a guarded ledger total (one-column
--          COALESCE: derived; the SUM dominates its own path); the ledger is on a LEFT
--          JOIN's null side, so its account filters nothing (D-14)
INSERT INTO mart."FeedSummary" ("CustomerID", "Total", total, "Status Code")
WITH feed AS (
    SELECT f."CustomerID", f."Amount", f.amount, upper(f."Status Code") AS sc
    FROM "LegacyFeed" AS f
    WHERE f.created_at >= DATE '2024-01-01'
),
book AS (
    SELECT l.account, SUM(l.amount) AS posted
    FROM ledger AS l
    GROUP BY l.account
)
SELECT
    fd."CustomerID",
    fd."Amount" + COALESCE(b.posted, 0),
    fd.amount,
    fd.sc
FROM feed AS fd
LEFT JOIN book AS b ON b.account = fd."CustomerID"::text
JOIN dim_customer AS dc ON dc.customer_id = fd."CustomerID"
WHERE dc.is_current;
