-- =====================================================================================
-- B9 · T4 · ADVERSARIAL — does name resolution get fooled?
--
-- Objective card: stress/pg/b9_names_resolution/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql (with B9's additive objects). PostgreSQL: 17.
--
-- Traps: a quoted alias beside its unquoted twin, an unqualified name that binds to the
-- INNER query, "Amount" beside amount, an unquoted `user` (a function), a name with a
-- space, aliases named like a schema, like a column and like a keyword, a quoted schema
-- in SET, and an ambiguous relation hidden inside EXISTS after a RESET.
-- Every node is schema-qualified (D-25).
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · "O" and o are different aliases: orders and payments
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (revenue, orders)
SELECT "O".amount, o.amount::bigint
FROM staging.orders AS "O"
JOIN staging.payments AS o ON o.order_id = "O".id;


-- -------------------------------------------------------------------------------------
-- S2 · unqualified names inside a scalar subquery bind INNERMOST first
--   `amount` and `id` are both payments' columns, so the predicate is p.order_id = p.id
--   - not a correlation with orders at all. D-12: the subquery's WHERE columns are values.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel, revenue)
SELECT
    o.channel,
    (SELECT SUM(amount) FROM staging.payments AS p WHERE p.order_id = id)
FROM staging.orders AS o;


-- -------------------------------------------------------------------------------------
-- S3 · "Amount" and amount, both in WHERE
-- -------------------------------------------------------------------------------------
INSERT INTO mart."FeedSummary" ("CustomerID")
SELECT f."CustomerID"
FROM staging."LegacyFeed" AS f
WHERE f."Amount" > 0
  AND f.amount IS NULL;


-- -------------------------------------------------------------------------------------
-- S4 · unquoted `user` is CURRENT_USER, not the column "user"
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level, customers)
SELECT user, f."CustomerID"
FROM staging."LegacyFeed" AS f;


-- -------------------------------------------------------------------------------------
-- S5 · a name with a space, in the projection and GROUP BY
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level, revenue)
SELECT f."Status Code", SUM(f."Amount")
FROM staging."LegacyFeed" AS f
GROUP BY f."Status Code";


-- -------------------------------------------------------------------------------------
-- S6 · an alias named like a schema: `staging.channel` is the alias's column
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel)
SELECT staging.channel
FROM staging.orders AS staging;


-- -------------------------------------------------------------------------------------
-- S7 · an alias named like a column: status.status
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level, segment)
SELECT status.status, status.segment
FROM staging.customers AS status;


-- -------------------------------------------------------------------------------------
-- S8 · c.* of one of two same-named tables
-- -------------------------------------------------------------------------------------
INSERT INTO core.ledger
SELECT c.*
FROM staging.ledger AS s
JOIN core.ledger AS c ON c.id = s.id
WHERE s.currency = 'USD';


-- -------------------------------------------------------------------------------------
-- S9a · a quoted schema name in SET (the same schema: "staging" is staging)
-- -------------------------------------------------------------------------------------
SET search_path TO "staging";

-- S9 · unqualified ledger under that path is staging's
INSERT INTO mart.kpi_rollup (segment)
SELECT l.account
FROM ledger AS l
WHERE l.amount > 0;


-- -------------------------------------------------------------------------------------
-- S10a · RESET
-- -------------------------------------------------------------------------------------
RESET search_path;

-- S10 · an ambiguous relation hidden inside EXISTS: declared, nothing from it (D-24);
--       the outer correlation operand still filters
INSERT INTO mart.kpi_rollup (level)
SELECT d.name
FROM core.dim_store AS d
WHERE EXISTS (SELECT 1 FROM events_log AS e WHERE e.id = d.id);


-- -------------------------------------------------------------------------------------
-- S11 · a keyword as a quoted alias
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (revenue)
SELECT "select".amount
FROM staging.orders AS "select";
