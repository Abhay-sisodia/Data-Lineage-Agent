-- COMPLEX SQL 4 / five levels of nesting with renaming at every level
--
-- Why it's hard: the alias chain in b0_05 was two levels. This is five, with a JOIN
-- introduced part-way and the column renamed at each level. Every level is a chance to
-- lose the trail or bind to the wrong side of the join.
--
-- amount -> amt -> val -> v2 -> final_value -> fct_product_sales.net_sales

CREATE OR REPLACE PROCEDURE sq_deep_nesting IS
BEGIN
    INSERT INTO fct_product_sales (period_month, product_id, net_sales, units_sold)
    SELECT month_start, prod, final_value, unit_total
      FROM (
        SELECT m           AS month_start,
               pid         AS prod,
               SUM(v2)     AS final_value,
               SUM(q2)     AS unit_total
          FROM (
            SELECT mth AS m,
                   pd  AS pid,
                   val AS v2,
                   qty AS q2
              FROM (
                SELECT TRUNC(dt, 'MM') AS mth,
                       p_id            AS pd,
                       amt * 1.0       AS val,
                       qn              AS qty
                  FROM (
                    SELECT o.order_date  AS dt,
                           l.product_id  AS p_id,
                           l.line_amount AS amt,
                           l.quantity    AS qn
                      FROM stg_order_lines l
                      JOIN stg_orders o ON o.order_id = l.order_id
                  )
              )
          )
         GROUP BY m, pid
      );

    COMMIT;
END sq_deep_nesting;
/
