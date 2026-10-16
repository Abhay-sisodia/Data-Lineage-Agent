-- =====================================================================================
-- B9 · T3 · INTERACTION — do name rules compose with B1-B4 rules?
--
-- Objective card: stress/pg/b9_names_resolution/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql (with B9's additive objects). PostgreSQL: 17.
--
-- Every relation is schema-qualified, so no search_path assumption arises. Every node is
-- schema-qualified (D-25).
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · GROUP BY a bare name that is both an output alias and an input column
--   PostgreSQL takes the INPUT column: `status` here is o.status, not the alias over
--   o.channel. So status is a grouping key, and influences the count (D-2).
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel, orders)
SELECT o.channel AS status, COUNT(o.id)
FROM staging.orders AS o
GROUP BY o.channel, status;


-- -------------------------------------------------------------------------------------
-- S2 · ORDER BY a bare name that is both: PostgreSQL takes the OUTPUT column
--   `paid_at` is the alias over p.amount, so amount decides the LIMIT (D-9).
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (customer_id, revenue)
SELECT p.order_id, p.amount AS paid_at
FROM staging.payments AS p
ORDER BY paid_at DESC
LIMIT 5;


-- -------------------------------------------------------------------------------------
-- S3 · a window partitioned by a USING-merged column (the left input's, INNER)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (store_id, net_amount, rank_in_region)
SELECT
    store_id,
    b.net_amount,
    RANK() OVER (PARTITION BY store_id ORDER BY b.net_amount DESC)
FROM staging.orders AS a
JOIN core.fct_order_line AS b USING (store_id);


-- -------------------------------------------------------------------------------------
-- S4 · same-named `status` in two joined tables, qualified, in GROUP BY and HAVING
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (region, orders)
SELECT s.status, COUNT(o.id)
FROM staging.orders AS o
JOIN core.dim_store AS s ON s.id = o.store_id
GROUP BY s.status
HAVING MAX(o.status) <> s.status;


-- -------------------------------------------------------------------------------------
-- S5 · UPDATE ... FROM where the target and the FROM table share a name
--   `ledger.id` is the target (core.ledger, not aliased); `amount` is unqualified and
--   only staging.ledger has it. The target-FROM equality is a join (D-11).
-- -------------------------------------------------------------------------------------
UPDATE core.ledger
SET balance = amount
FROM staging.ledger AS s
WHERE s.id = ledger.id
  AND s.currency = 'EUR';


-- -------------------------------------------------------------------------------------
-- S6 · MERGE whose USING alias is the target's table name
-- -------------------------------------------------------------------------------------
MERGE INTO core.ledger AS t
USING (
    SELECT s.id, s.account, s.amount
    FROM staging.ledger AS s
    WHERE s.posted_at >= DATE '2024-01-01'
) AS ledger
ON t.id = ledger.id
WHEN MATCHED THEN
    UPDATE SET balance = t.balance + ledger.amount
WHEN NOT MATCHED THEN
    INSERT (id, account, balance) VALUES (ledger.id, ledger.account, ledger.amount);


-- -------------------------------------------------------------------------------------
-- S7 · a quoted target list and a quoted RETURNING (D-16)
-- -------------------------------------------------------------------------------------
INSERT INTO mart."FeedSummary" ("CustomerID", "Status Code")
SELECT f."CustomerID", f."Status Code"
FROM staging."LegacyFeed" AS f
RETURNING "CustomerID", "Status Code";


-- -------------------------------------------------------------------------------------
-- S8 · a set operation takes its column names from the FIRST arm
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (segment, revenue)
SELECT u.acct, u.amt
FROM (
    SELECT s.account AS acct, s.amount AS amt
    FROM staging.ledger AS s
    UNION ALL
    SELECT c.account AS whatever, c.balance
    FROM core.ledger AS c
) AS u;
