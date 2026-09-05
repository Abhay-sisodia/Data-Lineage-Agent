-- Band 1 / temp and global temporary tables
-- Why it's hard: intermediate hops are invisible at table level. The chain
-- stg_orders -> gtt_stage -> fct_revenue looks like two unrelated pipelines unless
-- the temp table is modelled as a first-class relation.
-- Approach: model as relations with a SESSION scope.
--
-- See also silent/s3_shared_temp_table.sql, which is the dangerous version of this:
-- two unrelated procedures using the same temp table name. Scoping temp relations by
-- name alone fuses their lineages and invents edges that never existed.

CREATE GLOBAL TEMPORARY TABLE gtt_stage (
    cust_id      NUMBER(12),
    period_month DATE,
    amount       NUMBER(14,2)
) ON COMMIT PRESERVE ROWS;
/

CREATE OR REPLACE PROCEDURE b1_temp_tables IS
BEGIN
    DELETE FROM gtt_stage;

    INSERT INTO gtt_stage (cust_id, period_month, amount)
    SELECT cust_id,
           TRUNC(order_date, 'MM'),
           gross_amount - discount_amt
      FROM stg_orders;

    INSERT INTO fct_revenue (cust_id, period_month, net_amount, order_count)
    SELECT cust_id,
           period_month,
           SUM(amount),
           COUNT(*)
      FROM gtt_stage
     GROUP BY cust_id, period_month;

    COMMIT;
END b1_temp_tables;
/
