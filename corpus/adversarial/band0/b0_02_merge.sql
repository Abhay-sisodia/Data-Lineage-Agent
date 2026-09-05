-- Band 0 / MERGE
-- Why it's hard: multiple targets and conditional branches inside ONE statement.
-- The matched and not-matched arms write the same columns from different sources.
-- Approach: split into insert/update arms and derive lineage for each separately.
--
-- Expected: the UPDATE arm and the INSERT arm produce distinct edge sets. Collapsing
-- them into one would lose which source populates a column in which case.

CREATE OR REPLACE PROCEDURE b0_merge IS
BEGIN
    MERGE INTO dim_customer d
    USING (SELECT cust_id,
                  region,
                  email,
                  last_login
             FROM stg_customer) s
       ON (d.cust_id = s.cust_id)
    WHEN MATCHED THEN
        UPDATE SET d.email  = s.email,
                   d.region = s.region
    WHEN NOT MATCHED THEN
        INSERT (cust_id, region, is_active, email, lifetime_value)
        VALUES (s.cust_id, s.region, 0, s.email, 0);

    COMMIT;
END b0_merge;
/
