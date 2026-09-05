-- SILENT FAILURE 3 / shared temp tables
--
-- Two completely unrelated procedures both use tmp_recent. Key the temp relation on
-- its NAME and you fuse two unrelated lineages, inventing edges that never existed:
-- stg_orders would appear to flow into dim_customer.is_active, which is false.
--
-- Described as "easy to get wrong, catastrophic when you do" - and the invented edge
-- is worse than a missing one, because it is a confident wrong answer inside a
-- filing.
--
-- Defence: scope temp relations by (procedure, session), never by name alone.
--
-- Expected: ZERO cross-procedure edges between these two. This is the assertion, and
-- it is the reason this case exists.

-- Procedure A: customers -> tmp_recent -> dim_customer
CREATE OR REPLACE PROCEDURE s3_temp_writer_a IS
BEGIN
    DELETE FROM tmp_recent;

    INSERT INTO tmp_recent (cust_id, last_login)
    SELECT cust_id, last_login
      FROM stg_customer
     WHERE status_code = 1;

    UPDATE dim_customer
       SET is_active = 1
     WHERE cust_id IN (SELECT cust_id FROM tmp_recent);

    COMMIT;
END s3_temp_writer_a;
/

-- Procedure B: orders -> tmp_recent -> fct_revenue
-- Same table name, entirely different meaning, no relationship to A whatsoever.
CREATE OR REPLACE PROCEDURE s3_temp_writer_b IS
BEGIN
    DELETE FROM tmp_recent;

    INSERT INTO tmp_recent (cust_id, last_login)
    SELECT cust_id, order_date
      FROM stg_orders
     WHERE gross_amount > 100;

    INSERT INTO fct_revenue (cust_id, period_month, net_amount, order_count)
    SELECT cust_id, TRUNC(last_login, 'MM'), 0, COUNT(*)
      FROM tmp_recent
     GROUP BY cust_id, TRUNC(last_login, 'MM');

    COMMIT;
END s3_temp_writer_b;
/
