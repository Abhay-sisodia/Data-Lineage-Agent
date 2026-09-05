-- Band 0 / INSERT ... SELECT
-- Why it's hard: nothing. This is the easy case and the baseline.
-- If this does not work, stop - that is the finding.
--
-- Expected: straightforward column-to-column edges, mechanism AST.

CREATE OR REPLACE PROCEDURE b0_insert_select IS
BEGIN
    INSERT INTO tmp_recent (cust_id, last_login)
    SELECT cust_id, last_login
      FROM stg_customer
     WHERE last_login IS NOT NULL;

    COMMIT;
END b0_insert_select;
/
