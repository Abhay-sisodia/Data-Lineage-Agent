-- Band 1 / explicit cursors
-- Why it's hard: OPEN / FETCH INTO splits the producer from the consumer across
-- many lines, often with the FETCH inside a loop and the target variables declared
-- far from either.
-- Approach: bind fetch targets positionally back to the cursor's select list.
--
-- Positional binding is the trap. The FETCH target order is what matters, NOT the
-- variable names - v_email and v_region below are fetched in the cursor's column
-- order, and matching on name would wire them to the wrong sources.

CREATE OR REPLACE PROCEDURE b1_explicit_cursor IS
    CURSOR c_cust IS
        SELECT cust_id,
               email,
               region
          FROM stg_customer
         WHERE status_code = 1;

    v_id     NUMBER;
    v_email  VARCHAR2(200);
    v_region VARCHAR2(10);
BEGIN
    OPEN c_cust;
    LOOP
        FETCH c_cust INTO v_id, v_email, v_region;
        EXIT WHEN c_cust%NOTFOUND;

        UPDATE dim_customer
           SET email  = v_email,
               region = v_region
         WHERE cust_id = v_id;
    END LOOP;
    CLOSE c_cust;

    COMMIT;
END b1_explicit_cursor;
/
