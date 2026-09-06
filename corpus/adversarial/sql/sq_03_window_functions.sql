-- COMPLEX SQL 3 / window functions
--
-- Why it's hard: the PARTITION BY and ORDER BY columns influence the result without
-- being the value's source, and LAG reads a DIFFERENT ROW of the same column. A naive
-- walk treats every column inside the OVER clause as a value source, which is the same
-- category error as treating a correlated predicate as a value source.
--
-- Expected: rank_in_month is DERIVED from the ordering column, not a copy of it;
-- prior_month traces back to line_amount even though the value comes from another row.

CREATE OR REPLACE PROCEDURE sq_window_functions IS
BEGIN
    INSERT INTO fct_product_sales (period_month, product_id, gross_sales,
                                   rank_in_month, prior_month, units_sold)
    SELECT period_month,
           product_id,
           total,
           ROW_NUMBER() OVER (PARTITION BY period_month ORDER BY total DESC),
           LAG(total) OVER (PARTITION BY product_id ORDER BY period_month),
           units
      FROM (
            SELECT TRUNC(o.order_date, 'MM') AS period_month,
                   l.product_id              AS product_id,
                   SUM(l.line_amount)        AS total,
                   SUM(l.quantity)           AS units
              FROM stg_order_lines l
              JOIN stg_orders o ON o.order_id = l.order_id
             GROUP BY TRUNC(o.order_date, 'MM'), l.product_id
           );

    COMMIT;
END sq_window_functions;
/
