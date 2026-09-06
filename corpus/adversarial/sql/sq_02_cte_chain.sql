-- COMPLEX SQL 2 / chained common table expressions
--
-- Why it's hard: a CTE is a named subquery that later CTEs and the main query refer to
-- by name. Resolving `base.amount` requires knowing what `base` projected, which
-- requires having analysed a different part of the same statement first. Three CTEs
-- chained, with the third reading the first two.
--
-- The corpus has no CTEs at all, and real ETL is full of them.

CREATE OR REPLACE PROCEDURE sq_cte_chain IS
BEGIN
    INSERT INTO fct_product_sales (period_month, product_id, gross_sales, net_sales, units_sold)
    WITH base AS (
        SELECT l.product_id      AS product_id,
               o.order_date      AS order_date,
               l.line_amount     AS amount,
               l.quantity        AS qty
          FROM stg_order_lines l
          JOIN stg_orders o ON o.order_id = l.order_id
    ),
    refunds AS (
        SELECT r.product_id AS product_id,
               SUM(r.refund_amount) AS refunded
          FROM stg_returns r
         GROUP BY r.product_id
    ),
    combined AS (
        SELECT b.product_id,
               TRUNC(b.order_date, 'MM') AS period_month,
               SUM(b.amount)             AS gross,
               SUM(b.qty)                AS units,
               MAX(NVL(f.refunded, 0))   AS refunded
          FROM base b
          LEFT JOIN refunds f ON f.product_id = b.product_id
         GROUP BY b.product_id, TRUNC(b.order_date, 'MM')
    )
    SELECT period_month,
           product_id,
           gross,
           gross - refunded,
           units
      FROM combined;

    COMMIT;
END sq_cte_chain;
/
