-- STRESS PG 1 / wide construct coverage
--
-- The PostgreSQL counterpart of stress_01_wide_coverage.sql, and deliberately a CLOSE port
-- rather than a fresh design. Statements 1 and 2 are the Oracle package's stress_cte_window
-- and stress_set_ops rewritten in PostgreSQL syntax, with the same shape, the same column
-- names and the same traps. Their Oracle originals score 100%, so a difference here is a
-- DIALECT difference and nothing else - which is the only way to tell a port defect apart
-- from an analysis defect.
--
-- EVERY CONSTRUCT BELOW IS DRAWN FROM THE 34 "EDGES" AND 2 "NO-SOURCE" ROWS OF
-- docs/postgres_scope.md. Nothing here is expected to be refused, and the key declares no
-- boundaries at all. That is the contract the user asked for: inside the scope, clean.
-- Out-of-scope constructs live in stress_pg_03_boundary.sql, where every one of them is
-- expected to be refused by name.
--
-- Bands. Everything is band 0. A PL/pgSQL body is refused (ADR-0002 section 5), so there is
-- no band 1 and no band 2 for PostgreSQL - no variables, no cursors, no trigger inheritance.
-- Writing a band-1 edge into a PostgreSQL key would be labelling a capability the scope
-- says does not exist.
--
-- Origin. A script has no named units, so every edge's origin is `<script>`. The line
-- numbers in the key are the statement's line in THIS file, hand-read. Origin is not part
-- of the match key (ADR-0001 amendment 1b), so they cost nothing if they are off by one.
--
-- Ground truth was written from this source BEFORE the analyser was run over it.

-- ====================================================================================
-- 1. CTE chain -> window functions -> aggregate insert.
--    WITH x2, JOIN, LEFT JOIN, GROUP BY, HAVING, SUM, ROW_NUMBER, LAG, date_trunc, COALESCE.
--    The Oracle original is stress_cte_window; TRUNC(x,'MM') becomes date_trunc('month',x)
--    and NVL becomes COALESCE. Nothing else moves.
-- ====================================================================================
INSERT INTO fct_product_sales (
    period_month, product_id, category_name, gross_sales,
    net_sales, refund_total, units_sold, rank_in_month, prior_month
)
WITH monthly AS (
    SELECT date_trunc('month', o.order_date) AS period_month,
           l.product_id                      AS product_id,
           SUM(l.line_amount)                AS gross,
           SUM(l.quantity)                   AS units
      FROM stg_orders o
      JOIN stg_order_lines l
        ON l.order_id = o.order_id
     WHERE o.currency = 'GBP'
     GROUP BY date_trunc('month', o.order_date), l.product_id
    HAVING SUM(l.line_amount) > 0
),
refunded AS (
    SELECT r.product_id          AS product_id,
           SUM(r.refund_amount)  AS refunds
      FROM stg_returns r
     WHERE r.status_code = 'DONE'
     GROUP BY r.product_id
)
SELECT m.period_month,
       m.product_id,
       c.category_name,
       m.gross,
       m.gross - COALESCE(f.refunds, 0),
       COALESCE(f.refunds, 0),
       m.units,
       ROW_NUMBER() OVER (PARTITION BY m.period_month ORDER BY m.gross DESC),
       LAG(m.gross) OVER (PARTITION BY m.product_id ORDER BY m.period_month)
  FROM monthly m
  LEFT JOIN refunded f
    ON f.product_id = m.product_id
  JOIN stg_products p
    ON p.product_id = m.product_id
  JOIN stg_categories c
    ON c.category_id = p.category_id;

-- ====================================================================================
-- 2. UNION ALL. Set operations bind BY POSITION, not by name: the second arm's aliases
--    disagree with the target columns on purpose, so an analyser reading names instead of
--    positions lands refund_amount in the wrong column and the key catches it.
-- ====================================================================================
INSERT INTO fct_revenue (cust_id, period_month, net_amount, order_count)
SELECT o.cust_id, date_trunc('month', o.order_date), SUM(o.gross_amount), COUNT(*)
  FROM stg_orders o
 WHERE o.currency = 'GBP'
 GROUP BY o.cust_id, date_trunc('month', o.order_date)
UNION ALL
SELECT r.cust_id, date_trunc('month', CURRENT_DATE), -SUM(r.refund_amount), COUNT(*)
  FROM stg_returns r
 WHERE r.status_code = 'DONE'
 GROUP BY r.cust_id;

-- ====================================================================================
-- 3. UPDATE ... FROM - the PostgreSQL join-update, which has no Oracle equivalent at all.
--    The correlation `s.cust_id = d.cust_id` sits in the WHERE clause rather than in an ON
--    clause, and this package labels BOTH its operands as filter edges. See the key: that
--    is a convention question this file is the first to raise, and it is flagged there
--    rather than settled quietly.
-- ====================================================================================
UPDATE dim_customer d
   SET lifetime_value = d.lifetime_value + s.gross_amount
  FROM stg_orders s
 WHERE s.cust_id = d.cust_id
   AND s.currency = 'GBP';

-- ====================================================================================
-- 4. DELETE ... USING. A DELETE writes no value, but its predicate columns decide what the
--    table ends up containing - convention (c), settled as D-1.
-- ====================================================================================
DELETE FROM gtt_stage g
 USING stg_returns r
 WHERE r.cust_id = g.cust_id
   AND r.status_code = 'VOID';

-- ====================================================================================
-- 5. INSERT ... ON CONFLICT DO UPDATE - PostgreSQL's upsert, and the structural analogue of
--    Oracle's MERGE: two arms, one statement, and the same fact written by both must be
--    deduplicated rather than counted twice. EXCLUDED is the proposed row, so
--    `email = EXCLUDED.email` is the SAME fact as the insert arm's `s.email`.
--
--    The trap: the conflict target `(cust_id)` decides WHICH row is updated. It is not the
--    source of anything the statement writes, and the key forbids reading it as one.
-- ====================================================================================
INSERT INTO dim_customer (cust_id, region, email, is_active)
SELECT s.cust_id,
       s.region,
       s.email,
       CASE WHEN s.status_code = 'A' THEN true ELSE false END
  FROM stg_customer s
 WHERE s.signup_date >= DATE '2024-01-01'
ON CONFLICT (cust_id) DO UPDATE
   SET email  = EXCLUDED.email,
       region = EXCLUDED.region;

-- ====================================================================================
-- 6. CREATE TABLE AS SELECT, carrying a self-join, a scalar subquery in the select list and
--    a correlated EXISTS.
--
--    CTAS is in scope because it was SILENT until 2026-09-23 - skipped before it was
--    counted, which is the one outcome this project treats as never acceptable. It now
--    delegates to the INSERT path, so everything below is the ordinary INSERT analysis.
--
--    The self-join is a known limit, recorded before the analyser saw it: `child` and
--    `parent` are different rows of one relation and the IR carries no row identity, so the
--    two cust_id edges are indistinguishable in the schema.
-- ====================================================================================
CREATE TABLE dim_customer_hier AS
SELECT child.cust_id  AS cust_id,
       parent.cust_id AS parent_cust_id,
       1              AS depth,
       (SELECT MAX(p.product_name)
          FROM stg_products p
         WHERE p.category_id = 1) AS root_name
  FROM stg_customer child
  JOIN stg_customer parent
    ON parent.region = child.region
 WHERE child.signup_date > parent.signup_date
   AND EXISTS (SELECT 1
                 FROM stg_orders o
                WHERE o.cust_id = child.cust_id);

-- ====================================================================================
-- 7. All-literal INSERT. NO EDGES, AND THAT IS THE COMPLETE ANSWER - every value is a
--    literal, so there is nothing upstream to name.
--
--    It is here because this is the case that broke the probe's first run: two all-literal
--    writes were reported SILENT when they had in fact been analysed and correctly found
--    nothing. "Counted and empty" and "skipped entirely" look identical from the outside
--    and are completely different facts, so the distinction is pinned by a package rather
--    than only by a bucket name.
-- ====================================================================================
INSERT INTO audit_log (cust_id, changed_by, changed_at)
VALUES (0, 'loader', CURRENT_TIMESTAMP);
