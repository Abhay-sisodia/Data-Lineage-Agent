-- Band 1 / cursor FOR loops
-- Why it's hard: row values become variables become inserts. The projection of the
-- cursor's SELECT is the real source, but it reaches the target one field at a time
-- through a record variable.
-- Approach: treat the cursor's SELECT as the source projection and bind record
-- fields back to it.
--
-- Expected: stg_customer.cust_id -> dim_customer.cust_id via rec.cust_id, and a
-- DERIVED edge onto is_active from the CASE over rec.last_login.

CREATE OR REPLACE PROCEDURE b1_cursor_for_loop(p_region VARCHAR2) IS
BEGIN
    FOR rec IN (SELECT cust_id,
                       region,
                       last_login,
                       email
                  FROM stg_customer
                 WHERE region = p_region)
    LOOP
        UPDATE dim_customer
           SET is_active = CASE
                               WHEN rec.last_login > SYSDATE - 30 THEN 1
                               ELSE 0
                           END,
               email     = rec.email
         WHERE cust_id = rec.cust_id;
    END LOOP;

    COMMIT;
END b1_cursor_for_loop;
/
