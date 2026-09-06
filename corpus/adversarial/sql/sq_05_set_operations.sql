-- COMPLEX SQL 5 / UNION ALL and MINUS across differently-shaped arms
--
-- Why it's hard: set operations bind BY POSITION (silent failure s5), and here the arms
-- read DIFFERENT tables. Column 3 of the result is fed by three different source columns
-- from three different relations, so the target has three legitimate sources and missing
-- any one of them is a silent under-report.
--
-- Note the second arm selects refund_amount where the first selects line_amount: the
-- names differ, the positions match, and position is what SQL uses.

CREATE OR REPLACE PROCEDURE sq_set_operations IS
BEGIN
    INSERT INTO fct_product_sales (period_month, product_id, gross_sales)
    SELECT period_month, product_id, amount
      FROM (
            SELECT TRUNC(o.order_date, 'MM') AS period_month,
                   l.product_id              AS product_id,
                   l.line_amount             AS amount
              FROM stg_order_lines l
              JOIN stg_orders o ON o.order_id = l.order_id

            UNION ALL

            SELECT TRUNC(o.order_date, 'MM'),
                   r.product_id,
                   -r.refund_amount
              FROM stg_returns r
              JOIN stg_orders o ON o.order_id = r.order_id

            UNION ALL

            SELECT TRUNC(SYSDATE, 'MM'),
                   p.product_id,
                   p.list_price
              FROM stg_products p
             WHERE p.status_code = 0
           );

    COMMIT;
END sq_set_operations;
/
