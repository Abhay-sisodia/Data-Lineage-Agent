-- COMPLEX SQL 1 / five-table join with aggregation
--
-- Why it's hard: column resolution. `status_code` exists on stg_products AND
-- stg_returns; `cust_id` exists on stg_orders, stg_customer AND stg_returns. Every
-- unqualified reference is a chance to bind to the wrong table, and binding to the
-- wrong table is a SILENT failure - the statement still parses, still runs, and the
-- lineage is wrong.
--
-- Five tables, an outer join, a GROUP BY and an aggregate over a derived expression.
-- This is the shape real warehouse ETL actually has, and the band-0/1 corpus has
-- nothing like it.

CREATE OR REPLACE PROCEDURE sq_multi_join IS
BEGIN
    INSERT INTO fct_product_sales (period_month, product_id, category_name,
                                   gross_sales, net_sales, refund_total, units_sold)
    SELECT TRUNC(o.order_date, 'MM'),
           p.product_id,
           c.category_name,
           SUM(l.line_amount),
           SUM(l.line_amount) - NVL(SUM(r.refund_amount), 0),
           NVL(SUM(r.refund_amount), 0),
           SUM(l.quantity)
      FROM stg_orders o
      JOIN stg_order_lines l ON l.order_id = o.order_id
      JOIN stg_products p    ON p.product_id = l.product_id
      JOIN stg_categories c  ON c.category_id = p.category_id
      LEFT JOIN stg_returns r ON r.order_id = o.order_id
                             AND r.product_id = p.product_id
     WHERE p.status_code = 1
       AND o.gross_amount > 0
     GROUP BY TRUNC(o.order_date, 'MM'), p.product_id, c.category_name;

    COMMIT;
END sq_multi_join;
/
