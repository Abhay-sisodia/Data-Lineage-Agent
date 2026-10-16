-- =====================================================================================
-- B9 · T2 · DEPTH — does name resolution survive nesting?
--
-- Objective card: stress/pg/b9_names_resolution/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql (with B9's additive objects). PostgreSQL: 17.
--
-- Names carried through 3-5 levels. Every node is schema-qualified (D-25). S5 sets a
-- path and S6 resets it; S1-S4 name every relation explicitly or through a CTE.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · the alias `o` reused at four levels, each time for a different relation
--   Innermost-first resolution (B2-08) keeps every `o.` on its own level.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel, revenue, orders)
SELECT o.channel, o.revenue, o.n
FROM (
    SELECT o.channel, SUM(o.amount) AS revenue, COUNT(o.amount) AS n
    FROM (
        SELECT o.channel, o.amount
        FROM (
            SELECT o.channel, o.amount, o.status
            FROM staging.orders AS o
        ) AS o
        WHERE o.status <> 'VOID'
    ) AS o
    GROUP BY o.channel
) AS o
WHERE o.n > 1;


-- -------------------------------------------------------------------------------------
-- S2 · a CTE named `ledger` hides the tables named ledger for an unqualified reference;
--      the schema-qualified core.ledger in the same statement is still the table
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (segment, revenue, level)
WITH ledger AS (
    SELECT l.id, l.account, l.amount
    FROM staging.ledger AS l
    WHERE l.currency = 'USD'
)
SELECT ledger.account, ledger.amount, c.status
FROM ledger
JOIN core.ledger AS c ON c.id = ledger.id;


-- -------------------------------------------------------------------------------------
-- S3 · SELECT * through three derived tables, quoted names all the way
-- -------------------------------------------------------------------------------------
INSERT INTO mart."FeedSummary" ("CustomerID", "Total")
SELECT *
FROM (
    SELECT *
    FROM (
        SELECT f."CustomerID", f."Amount"
        FROM staging."LegacyFeed" AS f
        WHERE f."Status Code" = 'OK'
    ) AS a
) AS b;


-- -------------------------------------------------------------------------------------
-- S4 · a NATURAL join two levels down; its merged `account` (the left input's) is
--      grouped and carried up
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (segment, revenue)
SELECT t.account, t.total
FROM (
    SELECT n.account, SUM(n.amount) AS total
    FROM (
        SELECT account, s.amount
        FROM staging.ledger AS s
        NATURAL JOIN core.ledger AS c
        WHERE c.status = 'ACTIVE'
    ) AS n
    GROUP BY n.account
) AS t;


-- -------------------------------------------------------------------------------------
-- S5a · a path with core FIRST
-- -------------------------------------------------------------------------------------
SET search_path TO core, staging;

-- S5 · unqualified names three CTEs deep: `ledger` is core's (first on the path), and
--      `events_log` falls through to staging (core has none)
INSERT INTO mart.kpi_rollup (segment, revenue)
WITH a AS (
    SELECT l.account, l.balance
    FROM ledger AS l
),
b AS (
    SELECT a.account, a.balance * 2 AS b2
    FROM a
),
c AS (
    SELECT b.account, b.b2
    FROM b
    WHERE b.account IN (SELECT e.kind FROM events_log AS e)
)
SELECT c.account, c.b2
FROM c;


-- -------------------------------------------------------------------------------------
-- S6a · RESET
-- -------------------------------------------------------------------------------------
RESET search_path;

-- S6 · no path known again: an unqualified dim_carrier three levels down goes to the
--      default schema, and the assumption is declared (D-24)
INSERT INTO mart.carrier_scorecard (carrier_id, carrier_name)
SELECT x.id, x.nm
FROM (
    SELECT y.id, upper(y.name) AS nm
    FROM (
        SELECT cr.id, cr.name
        FROM dim_carrier AS cr
        WHERE cr.status = 'ACTIVE'
    ) AS y
) AS x;


-- -------------------------------------------------------------------------------------
-- S7 · t.* of t.* of a derived table
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (customer_id, full_name, region)
SELECT z.*
FROM (
    SELECT q.*
    FROM (
        SELECT c.id, c.full_name, c.country_code
        FROM staging.customers AS c
    ) AS q
) AS z;
