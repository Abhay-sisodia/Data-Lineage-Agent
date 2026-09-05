-- Band 2 / DB links and cross-schema references
-- Why it's hard: the other end may not be in scope at all. You can see that data
-- arrives; you cannot see where it came from.
-- Approach: emit a boundary node immediately, marked out-of-coverage and COUNTED.
-- If credentials arrive later the boundary resolves and coverage rises visibly on
-- the same report.
--
-- PARSE-ONLY: this file is deliberately NOT compiled against the local database.
-- The link target does not exist, so compilation would fail - which is precisely the
-- situation in a real estate where the remote side is out of scope. The analyser must
-- still produce a boundary node from the source text alone.
--
-- The correct output here is NOT an error. It is:
--     boundary_node(remote_customer@crm_link) - out of coverage, 1 dangling reference
-- and that count belongs in the coverage statement as a known unknown.

CREATE OR REPLACE PROCEDURE b2_db_links IS
BEGIN
    -- Remote source: everything upstream of this is invisible.
    INSERT INTO dim_customer (cust_id, region, is_active, email, lifetime_value)
    SELECT r.cust_id,
           r.region,
           0,
           r.email,
           0
      FROM remote_customer@crm_link r
     WHERE r.region IS NOT NULL;

    -- Cross-schema reference to a schema we may never be granted.
    INSERT INTO tmp_recent (cust_id, last_login)
    SELECT cust_id, last_login
      FROM finance_dw.customer_activity
     WHERE last_login > SYSDATE - 30;

    COMMIT;
END b2_db_links;
/
