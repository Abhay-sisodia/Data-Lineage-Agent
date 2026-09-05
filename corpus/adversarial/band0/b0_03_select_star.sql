-- Band 0 / SELECT *
-- Why it's hard: the column list only exists once the schema is bound. Without DDL
-- expansion this produces table-level lineage, which is the useless level.
--
-- Silent-failure risk: if the DDL snapshot is stale, expansion produces the WRONG
-- column list and every downstream edge inherits the error - with no symptom. The
-- schema snapshot must be versioned alongside the code snapshot.
--
-- Expected: expanded to the four real columns of tmp_recent's source projection.

CREATE OR REPLACE VIEW v_recent_raw AS
SELECT cust_id, last_login
  FROM stg_customer
 WHERE last_login IS NOT NULL;
/

CREATE OR REPLACE PROCEDURE b0_select_star IS
BEGIN
    INSERT INTO tmp_recent
    SELECT * FROM v_recent_raw;

    COMMIT;
END b0_select_star;
/
